"""Campuses and age groups from a verified website (UF42 step b).

Extraction records *candidates* in the internal ``attributes["website_campuses"]``
(never on the wire). Locations and age groups are created only when deterministic
validation clears the website-data withholding marker, in the same transaction
(``validator.validate_school_data``), and only for a site that passed URL
validation and the shared-site check. Everything created here is tagged as
website-derived, and every path that withholds the website deletes it again;
registry locations and registry age groups are never removed.

Precision rules:

* A campus address comes from a contact page of the verified site, its nearby
  text names the registry city, and it is not an office/HQ/partner/franchise
  address, not described as the other education level, and not the registry
  address of another institution (a sibling kindergarten on a school's site).
* Age groups are added only where the site states a range explicitly and are
  mapped through the country's ``education_config`` (grade numbers are placed on
  the age scale using the config's own grade-range labels). With several campuses,
  a range counts only when the sentence names that campus.
"""

from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from app.scrapers.shared_site_check import (
    _skeleton,
    _tokens,
    address_key,
    address_matches,
    describes_level,
    level_family,
    site_id,
)
from app.utils.transliteration import transliterate_bulgarian

logger = logging.getLogger(__name__)

CAMPUSES_KEY = "website_campuses"
WEBSITE_CONTACT_ADDRESS_TAG = "address_source=website_contact"
CAMPUS_TAG = "location_source=website_campus"
AGE_GROUP_TAG_PREFIX = "age_group_source=website:"

_CONTACT_PATH_RE = re.compile(r"contact|kontakt|контакт", re.IGNORECASE)
# Addresses that belong to someone else or to administration, not a campus.
_NOT_CAMPUS_RE = re.compile(
    r"офис|office|headquarter|\bhq\b|централ|управлени|седалище|администрац|"
    r"partner|партньор|франчайз|franchise|счетоводств|accounting",
    re.IGNORECASE,
)
_RANGE_RE = re.compile(
    r"(?:from|от)\s+(?P<a>[^.;:]{1,40}?)\s+(?:to|through|until|до)\s+(?P<b>[^.;:]{1,30}?)"
    r"(?=$|[,.;:!?]|\s+(?:and|и|with)\b)",
    re.IGNORECASE,
)
_NUMERIC_RANGE_RE = re.compile(
    r"\b(?P<a>\d{1,2})\.?\s*[-–]\s*(?P<b>\d{1,2})\.?\s*(?:клас|grade)"
    r"|\bgrades?\s*(?P<c>\d{1,2})\s*[-–]\s*(?P<d>\d{1,2})\b",
    re.IGNORECASE,
)
_GRADE_RE = re.compile(
    r"\bgrade\s*(?P<a>\d{1,2})\b|\b(?P<b>\d{1,2})\s*(?:-?(?:ви|ри|ти|ми))?\.?\s*клас",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
CONTEXT_BEFORE = 2
CONTEXT_AFTER = 2


# ---------------------------------------------------------------------------
# Country age-group config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgeGroupDef:
    key: str
    categories: tuple[str, ...]
    min_diff: int
    max_diff: int
    labels: tuple[str, ...]


def age_group_defs(education_config: Mapping[str, Any] | None) -> list[AgeGroupDef]:
    defs = []
    for item in (education_config or {}).get("age_groups") or []:
        try:
            category = item.get("category")
            categories = tuple(category) if isinstance(category, list) else (str(category),)
            labels = tuple(str(v) for v in (item.get("label_i18n") or {}).values() if v)
            defs.append(AgeGroupDef(str(item["key"]), categories, int(item["min_diff"]), int(item["max_diff"]), labels))
        except (KeyError, TypeError, ValueError):
            continue
    return defs


def grade_offset(defs: Sequence[AgeGroupDef]) -> Optional[int]:
    """Age difference of grade 0, read from the config's own "Grades a-b" labels."""
    offsets = set()
    for group in defs:
        for label in group.labels:
            match = re.search(r"(\d{1,2})\s*[-–]\s*(\d{1,2})", label)
            if match:
                offsets.add(group.min_diff - int(match.group(1)))
    return offsets.pop() if len(offsets) == 1 else None


def _end_diffs(text: str, defs: Sequence[AgeGroupDef], offset: Optional[int]) -> Optional[tuple[int, int]]:
    grade = _GRADE_RE.search(text)
    if grade and offset is not None:
        value = offset + int(grade.group("a") or grade.group("b"))
        return value, value
    lowered = text.casefold()
    matches = [
        g for g in defs
        if any(re.search(rf"(?<![\w]){re.escape(label.casefold())}(?![\w])", lowered) for label in g.labels)
    ]
    if len(matches) == 1:
        return matches[0].min_diff, matches[0].max_diff
    return None


def stated_range(sentence: str, defs: Sequence[AgeGroupDef], offset: Optional[int]) -> Optional[tuple[int, int]]:
    """The age-difference range a sentence states explicitly, if exactly one."""
    found: set[tuple[int, int]] = set()
    for match in _RANGE_RE.finditer(sentence):
        start, end = match.group("a"), match.group("b")
        hi = _end_diffs(end, defs, offset)
        # "от 1 до 7 клас": the grade word after the range covers the bare start number.
        if re.fullmatch(r"\s*\d{1,2}\.?\s*", start) and _GRADE_RE.search(end):
            start = f"grade {start.strip(' .')}"
        lo = _end_diffs(start, defs, offset)
        if lo and hi and lo[0] <= hi[1]:
            found.add((lo[0], hi[1]))
    if offset is not None:
        for match in _NUMERIC_RANGE_RE.finditer(sentence):
            a, b = match.group("a") or match.group("c"), match.group("b") or match.group("d")
            if int(a) <= int(b):
                found.add((offset + int(a), offset + int(b)))
    return found.pop() if len(found) == 1 else None


def groups_for_range(diff_range: Sequence[int], family: str, defs: Sequence[AgeGroupDef]) -> list[str]:
    lo, hi = diff_range
    return [g.key for g in defs if family in g.categories and g.min_diff <= hi and g.max_diff >= lo]


# ---------------------------------------------------------------------------
# Extraction (candidates only)
# ---------------------------------------------------------------------------

@dataclass
class SitePage:
    url: str
    text: str
    category: Optional[str] = None


def addresses_equivalent(first: str, second: str) -> bool:
    for left, right in ((first, second), (second, first)):
        key = address_key(left)
        if key and address_matches(key, _tokens(right)):
            return True
    return False


def _names_city(text: str, city: str) -> bool:
    city_key = (city or "").casefold().strip()
    if not city_key:
        return False
    words = re.findall(r"[^\W\d_]+", text or "")
    return any(transliterate_bulgarian(word).casefold() == city_key for word in words)


def _is_contact_page(page: SitePage) -> bool:
    return (page.category or "") == "contact" or bool(_CONTACT_PATH_RE.search(unquote(urlparse(page.url).path)))


def _label(before: Sequence[str]) -> str:
    from app.scrapers import extractor_helpers as helpers

    for line in reversed(before):
        if re.search(r"[^\W\d_]{3}", line) and not helpers._looks_like_contact_address(
            helpers._normalize_contact_address_candidate(line)
        ):
            return line.strip(" :")
    return ""


def _campus_candidates(pages: Sequence[SitePage], *, city: str, family: str) -> tuple[list[dict], list[dict]]:
    from app.scrapers import extractor_helpers as helpers

    other_family = "school" if family == "kindergarten" else "kindergarten"
    kept: list[dict] = []
    skipped: list[dict] = []
    for page in pages:
        if not _is_contact_page(page):
            continue
        lines = [line.strip() for line in (page.text or "").splitlines() if line.strip()]
        for index, line in enumerate(lines):
            address = helpers._normalize_contact_address_candidate(line)
            if not helpers._looks_like_contact_address(address):
                continue
            before = lines[max(0, index - CONTEXT_BEFORE) : index]
            after = lines[index + 1 : index + 1 + CONTEXT_AFTER]
            context = " | ".join([*before, line, *after])
            candidate = {"address": address, "label": _label(before), "context": context, "source_url": page.url}
            if any(addresses_equivalent(address, existing["address"]) for existing in kept):
                continue
            if not _names_city(" ".join([line, *after]), city):
                skipped.append({**candidate, "reason": "city_not_stated"})
            elif _NOT_CAMPUS_RE.search(" ".join([*before, line])):
                skipped.append({**candidate, "reason": "office_or_partner_address"})
            elif describes_level(" ".join([*before, line]), other_family) and not describes_level(
                " ".join([*before, line]), family
            ):
                skipped.append({**candidate, "reason": "describes_other_level"})
            else:
                kept.append(candidate)
    return kept, skipped


def _sentences(pages: Iterable[SitePage]) -> Iterable[tuple[str, str]]:
    for page in pages:
        for sentence in _SENTENCE_SPLIT_RE.split(page.text or ""):
            sentence = sentence.strip()
            if sentence:
                yield page.url, sentence


def _distinctive(campuses: Sequence[dict]) -> list[set[str]]:
    label_words = [
        {_skeleton(w) for w in re.findall(r"[^\W\d_]{4,}", c["label"]) if len(_skeleton(w)) >= 2}
        for c in campuses
    ]
    return [
        words - set().union(*(other for j, other in enumerate(label_words) if j != i))
        for i, words in enumerate(label_words)
    ]


def extract_campus_candidates(
    pages: Sequence[SitePage],
    *,
    website_url: str,
    city: str,
    education_level: str,
    education_config: Mapping[str, Any] | None,
    shared_site: bool = False,
) -> Optional[dict[str, Any]]:
    """Campus addresses and stated level ranges from the verified site's pages."""
    site = site_id(website_url)
    site_pages = [p for p in pages if site_id(p.url) == site]
    if not site_pages:
        return None
    family = level_family(education_level)
    defs = age_group_defs(education_config)
    offset = grade_offset(defs)

    campuses, skipped = _campus_candidates(site_pages, city=city, family=family)
    sentences = list(_sentences(site_pages))
    if len(campuses) > 1:
        for campus, words in zip(campuses, _distinctive(campuses)):
            ranges: dict[tuple[int, int], tuple[str, str]] = {}
            for url, sentence in sentences:
                if words and words & set(_tokens(sentence)):
                    found = stated_range(sentence, defs, offset)
                    if found:
                        ranges.setdefault(found, (url, sentence))
            if len(ranges) == 1:
                (diff_range, (url, evidence)), = ranges.items()
                campus["diff_range"] = list(diff_range)
                campus["range_evidence"] = evidence[:300]
                campus["range_source_url"] = url

    school_level = None
    if len(campuses) <= 1 and not shared_site:
        ranges = {}
        for url, sentence in sentences:
            found = stated_range(sentence, defs, offset)
            if found:
                ranges.setdefault(found, (url, sentence))
        if len(ranges) == 1:
            (diff_range, (url, evidence)), = ranges.items()
            school_level = {"diff_range": list(diff_range), "evidence": evidence[:300], "source_url": url}

    if not campuses and not school_level:
        return None
    return {
        "site": site,
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "campuses": campuses,
        "skipped": skipped,
        "school_level": school_level,
    }


# ---------------------------------------------------------------------------
# Plan (pure) and apply (DB)
# ---------------------------------------------------------------------------

@dataclass
class ExistingLocation:
    location_id: int
    address: str
    age_groups: set[str]
    is_primary: bool


def plan_campus_sync(
    payload: Mapping[str, Any] | None,
    *,
    education_level: str,
    education_config: Mapping[str, Any] | None,
    existing: Sequence[ExistingLocation],
    other_institution_addresses: Sequence[tuple[int, str]],
) -> dict[str, Any]:
    plan: dict[str, Any] = {"new_locations": [], "age_groups": [], "skipped": []}
    if not payload:
        return plan
    family = level_family(education_level)
    defs = age_group_defs(education_config)

    def add_groups(location: ExistingLocation, diff_range, evidence) -> None:
        missing = [g for g in groups_for_range(diff_range, family, defs) if g not in location.age_groups]
        if missing:
            plan["age_groups"].append({"location_id": location.location_id, "address": location.address,
                                       "add": missing, "evidence": evidence})

    campuses = list(payload.get("campuses") or [])
    for campus in campuses:
        address = campus.get("address") or ""
        match = next((loc for loc in existing if addresses_equivalent(loc.address, address)), None)
        if match is not None:
            if campus.get("diff_range"):
                add_groups(match, campus["diff_range"], campus.get("range_evidence"))
            continue
        sibling = next((sid for sid, other in other_institution_addresses if addresses_equivalent(other, address)), None)
        if sibling is not None:
            plan["skipped"].append({"address": address, "reason": f"address_of_institution_{sibling}"})
            continue
        if len(campuses) < 2:
            # A single contact address is the primary sync's job, not a new campus.
            plan["skipped"].append({"address": address, "reason": "single_address_not_a_new_campus"})
            continue
        groups = groups_for_range(campus["diff_range"], family, defs) if campus.get("diff_range") else []
        plan["new_locations"].append({"address": address, "label": campus.get("label"), "age_groups": groups,
                                      "evidence": campus.get("context"), "range_evidence": campus.get("range_evidence"),
                                      "source_url": campus.get("source_url")})

    school_level = payload.get("school_level")
    if school_level and len(campuses) <= 1:
        primary = next((loc for loc in existing if loc.is_primary), existing[0] if existing else None)
        if primary is not None:
            add_groups(primary, school_level["diff_range"], school_level.get("evidence"))
    return plan


async def _load_plan_inputs(db, school) -> tuple[list[ExistingLocation], list[tuple[int, str]], Any, list]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import School
    from app.models.country import Country
    from app.models.school import SchoolLocation

    locations = (
        await db.execute(
            select(SchoolLocation)
            .options(selectinload(SchoolLocation.age_group_shifts))
            .where(SchoolLocation.school_id == school.id)
            .order_by(SchoolLocation.id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    existing = [
        ExistingLocation(
            loc.id,
            (loc.address_i18n or {}).get("bg") or (loc.address_i18n or {}).get("en") or "",
            {link.age_group for link in loc.age_group_shifts},
            bool(loc.is_primary),
        )
        for loc in locations
    ]
    others = (
        await db.execute(
            select(SchoolLocation.school_id, SchoolLocation.address_i18n)
            .join(School, School.id == SchoolLocation.school_id)
            .where(School.country_code == school.country_code, SchoolLocation.school_id != school.id)
        )
    ).all()
    other_addresses = [
        (sid, (addr or {}).get("bg") or (addr or {}).get("en") or "") for sid, addr in others if addr
    ]
    country = await db.get(Country, school.country_code)
    return existing, other_addresses, (country.education_config if country else None), locations


async def plan_for_school(db, school) -> dict[str, Any]:
    existing, other_addresses, config, _ = await _load_plan_inputs(db, school)
    return plan_campus_sync(
        (school.attributes or {}).get(CAMPUSES_KEY),
        education_level=school.education_level,
        education_config=config,
        existing=existing,
        other_institution_addresses=other_addresses,
    )


def site_is_verified(school) -> bool:
    """URL validation accepted the current site and nothing withholds its data."""
    from app.utils.website_data import website_data_is_publishable

    attrs = school.attributes if isinstance(school.attributes, Mapping) else {}
    validated = attrs.get("validated_website_url")
    payload = attrs.get(CAMPUSES_KEY) or {}
    return bool(
        school.website_url
        and validated
        and site_id(validated) == site_id(school.website_url)
        and payload.get("site") == site_id(school.website_url)
        and website_data_is_publishable(attrs, school.scrape_status)
    )


async def apply_campus_sync(db, school) -> dict[str, Any]:
    """Create campus locations and add stated age groups (called when the marker clears).

    Returns the plan plus the ids of new locations (they still need geocoding).
    """
    from app.models.school import SchoolLocation, SchoolLocationAgeGroupShift

    if not site_is_verified(school):
        return {"new_location_ids": [], "skipped_reason": "site_not_verified"}
    existing, other_addresses, config, locations = await _load_plan_inputs(db, school)
    plan = plan_campus_sync(
        (school.attributes or {}).get(CAMPUSES_KEY),
        education_level=school.education_level,
        education_config=config,
        existing=existing,
        other_institution_addresses=other_addresses,
    )
    by_id = {loc.id: loc for loc in locations}
    for change in plan["age_groups"]:
        location = by_id[change["location_id"]]
        tags = list(location.location_tags or [])
        for key in change["add"]:
            location.age_group_shifts.append(SchoolLocationAgeGroupShift(age_group=key))
            tags.append(AGE_GROUP_TAG_PREFIX + key)
        location.location_tags = tags
        db.add(location)
    new_locations = []
    for item in plan["new_locations"]:
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": item["address"]},
            is_primary=False,
            location_tags=[WEBSITE_CONTACT_ADDRESS_TAG, CAMPUS_TAG],
            geocode_meta={},
        )
        location.age_group_shifts = [SchoolLocationAgeGroupShift(age_group=key) for key in item["age_groups"]]
        db.add(location)
        new_locations.append(location)
    await db.flush()
    return {**plan, "new_location_ids": [loc.id for loc in new_locations]}


async def _site_is_shared(db, school) -> bool:
    from sqlalchemy import select

    from app.models import School
    from app.scrapers.shared_site_check import site_group_key

    key = site_group_key(school.website_url)
    if not key:
        return False
    rows = (
        await db.execute(
            select(School.website_url).where(
                School.country_code == school.country_code,
                School.website_url.isnot(None),
                School.id != school.id,
            )
        )
    ).scalars().all()
    return any(site_group_key(url) == key for url in rows)


async def record_candidates_at_extraction(db, school, pages: Sequence[Any], attrs: dict) -> None:
    """Store campus candidates for the new extraction and drop what the old one created.

    Extraction sets the withholding marker, so website-derived campus rows stop
    publishing now; validation recreates them when it clears the marker.
    """
    from app.models.country import Country

    country = await db.get(Country, school.country_code)
    payload = extract_campus_candidates(
        [SitePage(p.source_url, p.raw_markdown or "", p.page_category) for p in pages],
        website_url=school.website_url or "",
        city=school.city or "",
        education_level=school.education_level or "",
        education_config=country.education_config if country else None,
        shared_site=await _site_is_shared(db, school),
    )
    if payload:
        attrs[CAMPUSES_KEY] = payload
    else:
        attrs.pop(CAMPUSES_KEY, None)
    await remove_website_campus_data(db, school.id)


async def remove_website_campus_data(db, school_id: int, *, preview: bool = False) -> list[dict[str, Any]]:
    """Delete campus locations and website-added age groups (registry rows untouched)."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.school import SchoolLocation

    locations = (
        await db.execute(
            select(SchoolLocation)
            .options(selectinload(SchoolLocation.age_group_shifts))
            .where(SchoolLocation.school_id == school_id)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    changes: list[dict[str, Any]] = []
    for location in locations:
        tags = [str(t) for t in (location.location_tags or [])]
        if CAMPUS_TAG in tags:
            changes.append({"location_id": location.id, "action": "delete_campus_location",
                            "address": dict(location.address_i18n or {})})
            if not preview:
                await db.delete(location)  # its age-group rows cascade
            continue
        added = [t[len(AGE_GROUP_TAG_PREFIX):] for t in tags if t.startswith(AGE_GROUP_TAG_PREFIX)]
        if not added:
            continue
        changes.append({"location_id": location.id, "action": "remove_website_age_groups", "age_groups": added})
        if not preview:
            for link in list(location.age_group_shifts):
                if link.age_group in added:
                    await db.delete(link)
            location.location_tags = [t for t in tags if not t.startswith(AGE_GROUP_TAG_PREFIX)]
            db.add(location)
    if changes and not preview:
        await db.flush()
    return changes


async def geocode_campus_locations(db, location_ids: Sequence[int], *, country_code: str = "bg") -> None:
    """Pin new campus locations through the normal geocoding service and its gates."""
    if not location_ids:
        return
    from sqlalchemy import select

    from app.models.school import SchoolLocation
    from app.services.geocoding.service import GeocodingService

    geocoder = GeocodingService(db)
    locations = (
        await db.execute(select(SchoolLocation).where(SchoolLocation.id.in_(list(location_ids))))
    ).scalars().all()
    for location in locations:
        try:
            await geocoder.geocode_location(location, country_code=country_code)
        except Exception as exc:  # a missing pin must not undo the validated data
            logger.warning("Campus location %s geocoding failed: %s", location.id, exc)
