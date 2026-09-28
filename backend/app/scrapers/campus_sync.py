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
    _distinctive_words,
    _skeleton,
    _tokens,
    address_key,
    address_matches,
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
    # "централа"/"централен офис" (HQ), not "Централна сграда" (a main campus).
    r"офис|office|headquarter|\bhq\b|централа\b|управлени|седалище|администрац|"
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
# Not after "4." in "до 4. клас": a number followed by a dot is an ordinal.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[^\d\s][.!?])\s+|\n+")
# Bare level words that make a heading/label name a level (for the label only;
# page-wide text mentions school readiness everywhere).
_LABEL_LEVEL_RE = {
    "school": re.compile(r"училищ|гимназ|(?<!pre)(?<!pre-)school|lyceum|лицей", re.IGNORECASE),
    "kindergarten": re.compile(r"градин|ясл|kindergarten|nursery", re.IGNORECASE),
}
CONTEXT_BEFORE = 2
CONTEXT_AFTER = 2
_FIRM_REJECTIONS = frozenset(
    {"office_or_partner_address", "describes_other_level", "entrance_of_existing_location"}
)
# "ул. Нишава 107 /вход от ул. Твърдишки проход/": the second street is a door, not a campus.
_ENTRANCE_RE = re.compile(
    r"(?:вход\s+от|entrance\s+(?:from|on))\s+(?:ул\.?|бул\.?|улица|street|st\.?)?\s*"
    r"[\"'„“”]*(?P<street>[^\"'„“”/()\n,]+)",
    re.IGNORECASE,
)


def _numbered_street(text: str) -> bool:
    key = address_key(text.rstrip(" /(-–,[:"))
    return key is not None and key.kind == "street_number"


# A geocoded campus this close to one of the school's other locations is the same building.
CAMPUS_SAME_BUILDING_RADIUS_M = 75.0
# A campus needs a full street address (street + number, or quarter + block).
PRECISE_ADDRESS_KINDS = frozenset({"street_number", "quarter_block"})


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
    matches = [g for g in defs if any(_label_in(label, lowered) for label in g.labels)]
    if len(matches) == 1:
        return matches[0].min_diff, matches[0].max_diff
    return None


def _label_in(label: str, lowered_text: str) -> bool:
    """A config label in text; a long one-word label also matches its inflections
    ("Подготвителна" ~ "подготвителен", "Preschool" ~ "Preschool")."""
    label = label.casefold().strip()
    if " " not in label and len(label) >= 7:
        return re.search(rf"(?<!\w){re.escape(label[:-2])}\w*", lowered_text) is not None
    return re.search(rf"(?<!\w){re.escape(label)}(?!\w)", lowered_text) is not None


def stated_range(sentence: str, defs: Sequence[AgeGroupDef], offset: Optional[int]) -> Optional[tuple[int, int]]:
    """The age-difference range a sentence states explicitly, if exactly one."""
    found: set[tuple[int, int]] = set()
    # "до 4. клас" -> "до 4 клас": the ordinal dot is not the end of the range.
    sentence = re.sub(r"(\d)\.\s*(?=клас|grade)", r"\1 ", sentence, flags=re.IGNORECASE)
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
    # Only an entrance attached to another numbered street address on the same line
    # ("ул. Нишава 107 /вход от ул. X/") names a door of that building.
    entrance_streets = [
        set(_distinctive_words(match.group("street")))
        for page in pages
        if _is_contact_page(page)
        for line in (page.text or "").splitlines()
        for match in _ENTRANCE_RE.finditer(line)
        if _numbered_street(line[: match.start()])
    ]
    occurrences: list[dict] = []
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
            key = address_key(address)
            nearby = " ".join([*before[-1:], line, *after])
            if key is None or key.kind not in PRECISE_ADDRESS_KINDS:
                reason = "not_a_street_address"
            elif any(words and words <= set(key.words) for words in entrance_streets):
                reason = "entrance_of_existing_location"
            elif not _names_city(nearby, city):
                reason = "city_not_stated"
            elif _NOT_CAMPUS_RE.search(" ".join([*before, line, *after[:1]])):
                reason = "office_or_partner_address"
            elif _LABEL_LEVEL_RE[other_family].search(" ".join([*before, line])) and not _LABEL_LEVEL_RE[
                family
            ].search(" ".join([*before, line])):
                reason = "describes_other_level"
            else:
                reason = None
            occurrences.append({**candidate, "reason": reason})

    # Decide per address over all its occurrences: an office/partner/other-level
    # context anywhere rejects it; otherwise one fully qualified copy accepts it
    # (a header or footer copy without the city does not block it).
    groups: list[list[dict]] = []
    for occurrence in occurrences:
        group = next((g for g in groups if addresses_equivalent(g[0]["address"], occurrence["address"])), None)
        if group is None:
            groups.append([occurrence])
        else:
            group.append(occurrence)
    kept: list[dict] = []
    skipped: list[dict] = []
    for group in groups:
        firm = next((o for o in group if o["reason"] in _FIRM_REJECTIONS), None)
        accepted = next((o for o in group if o["reason"] is None), None)
        if firm is not None:
            skipped.append(firm)
        elif accepted is not None:
            kept.append({k: v for k, v in accepted.items() if k != "reason"})
        else:
            skipped.append(group[0])
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
) -> Optional[dict[str, Any]]:
    """Campus addresses and each campus's stated level range from the verified site.

    A level range counts only in a sentence that names the campus; a range stated
    for the school as a whole (history, holiday calendars, "from 1 to 8 grade" in
    an old article) is not tied to any building and is not used.
    """
    from app.scrapers.shared_site_check import is_directory_url

    site = site_id(website_url)
    site_pages = [p for p in pages if site_id(p.url) == site]
    if not site_pages or is_directory_url(website_url):
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

    if not campuses:
        return None
    return {
        "site": site,
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "campuses": campuses,
        "skipped": skipped,
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


def _same_street_different_number(first: str, second: str) -> bool:
    left, right = address_key(first), address_key(second)
    return bool(
        left and right
        and left.kind == right.kind == "street_number"
        and set(left.words) == set(right.words)
        and left.number != right.number
    )


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
        if any(_same_street_different_number(loc.address, address) for loc in existing):
            # A second building or a move: the site alone can't tell which.
            plan["skipped"].append({"address": address, "reason": "same_street_different_number"})
            continue
        if any(addresses_equivalent(done, address) for done in payload.get("same_building") or []):
            plan["skipped"].append({"address": address, "reason": "same_building_as_existing_location"})
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
            if AGE_GROUP_TAG_PREFIX + key not in tags:
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


async def record_candidates_at_extraction(db, school, pages: Sequence[Any], attrs: dict) -> None:
    """Store campus candidates for the new extraction and drop what the old one created.

    Extraction sets the withholding marker, so website-derived campus rows stop
    publishing now; validation recreates them when it clears the marker (as new rows:
    their pins are redone by the normal geocoder after the commit).
    """
    from app.models.country import Country

    country = await db.get(Country, school.country_code)
    payload = extract_campus_candidates(
        [SitePage(p.source_url, p.raw_markdown or "", p.page_category) for p in pages],
        website_url=school.website_url or "",
        city=school.city or "",
        education_level=school.education_level or "",
        education_config=country.education_config if country else None,
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


async def geocode_campus_locations(db, school_id: int, *, country_code: str = "bg") -> list[int]:
    """Pin this school's campus locations that have no pin yet (normal service and gates).

    Call only on a session with no open transaction: the geocoding service commits
    each pin. Campus rows are created inside validation, which may run nested in
    extraction's transaction, so this looks the rows up instead of taking the ids
    from that transaction; the next committed validation pins anything left.
    """
    from sqlalchemy import select

    from app.models.school import SchoolLocation
    from app.services.geocoding.service import geocode_failure_is_terminal

    candidates = (
        await db.execute(
            select(SchoolLocation).where(SchoolLocation.school_id == school_id, SchoolLocation.lat.is_(None))
        )
    ).scalars().all()
    locations = [
        loc for loc in candidates
        if CAMPUS_TAG in (loc.location_tags or []) and not geocode_failure_is_terminal(loc.geocode_meta)
    ]
    if not locations:
        return []
    from app.services.geocoding.service import GeocodingService

    geocoder = GeocodingService(db)
    for location in locations:
        try:
            await geocoder.geocode_location(location, country_code=country_code)
        except Exception as exc:  # a missing pin must not undo the validated data
            logger.warning("Campus location %s geocoding failed: %s", location.id, exc)
            continue
        await _drop_if_same_building(db, location)
    return [loc.id for loc in locations]


async def _drop_if_same_building(db, campus) -> bool:
    """A pinned campus next to another location of the school is that building (an
    entrance or a second spelling): delete it with its age groups."""
    from sqlalchemy import select

    from app.models.school import SchoolLocation
    from app.services.geocoding.service import _distance_m

    if campus.lat is None or campus.lng is None:
        return False
    others = (
        await db.execute(
            select(SchoolLocation).where(
                SchoolLocation.school_id == campus.school_id,
                SchoolLocation.id != campus.id,
                SchoolLocation.lat.isnot(None),
                SchoolLocation.lng.isnot(None),
            )
        )
    ).scalars().all()
    near = next(
        (o for o in others if _distance_m(campus.lat, campus.lng, o.lat, o.lng) <= CAMPUS_SAME_BUILDING_RADIUS_M),
        None,
    )
    if near is None:
        return False
    logger.info(
        "same_building_as_existing_location: campus %s (%s) is within %.0f m of location %s; deleted",
        campus.id, (campus.address_i18n or {}).get("bg"), CAMPUS_SAME_BUILDING_RADIUS_M, near.id,
    )
    # Remember it until the next extraction so validation doesn't recreate the row.
    from app.models import School

    school = await db.get(School, campus.school_id)
    attrs = dict(school.attributes or {})
    payload = dict(attrs.get(CAMPUSES_KEY) or {})
    address = (campus.address_i18n or {}).get("bg") or ""
    payload["same_building"] = [*(payload.get("same_building") or []), address]
    attrs[CAMPUSES_KEY] = payload
    school.attributes = attrs
    db.add(school)
    await db.delete(campus)
    await db.commit()
    return True
