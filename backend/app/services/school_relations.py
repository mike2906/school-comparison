"""Kindergarten → school relationship, computed on request (UF42 step c).

Nothing is curated or stored. A kindergarten ``continues_to`` a school when all of
this holds:

* both websites are on the same site group (registrable domain, or the tenant site
  on a path-hosted platform; ``shared_site_check.site_group_key``);
* each side's current website has positive evidence: a shared-site check keep or
  replace decision recorded for that same site; and neither was withheld by URL
  validation;
* the registry names share a brand (``brand`` / ``shared_brand_key``, the logic of
  ``scripts/derive_school_groups.py``);
* one is kindergarten-level and the other school-level, in the same city;
* exactly one school qualifies (several candidates → no link);
* the school is itself publicly listed.

The link needs a site that passed the checks, not publishable extracted data: it is
built from the URL, the registry name/level/city and the target's public name, so
no website-extracted value crosses the boundary. The school's detail shows the same
link in reverse (``continued_from``).

``same_place`` is a second, looser relation for the map and the list: one brand's
private kindergarten and school whose pins are next door (see the section below).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Optional

from app.scrapers.shared_site_check import (
    KEEP,
    REPLACE,
    SHARED_SITE_CHECK_KEY,
    level_family,
    registrable_domain,
    site_group_key,
    site_id,
)

# ---------------------------------------------------------------------------
# Brand (shared with scripts/derive_school_groups.py)
# ---------------------------------------------------------------------------

LEGAL = r'(?i)\b(ЕООД|ООД|АД|ЕАД|СДРУЖЕНИЕ)\b'
TYPE_WORDS = (r'(?i)\b(ЧАСТНА|ЧАСТНО|ЧАСТНИ|ДЕТСКА ГРАДИНА|ДЕТСКА ЯСЛА|ЯСЛА|ГРАДИНА|'
              r'ОСНОВНО УЧИЛИЩЕ|СРЕДНО УЧИЛИЩЕ|НАЧАЛНО УЧИЛИЩЕ|УЧИЛИЩЕ|'
              r'ПРОФЕСИОНАЛНА ГИМНАЗИЯ|ПРОФИЛИРАНА ГИМНАЗИЯ|ГИМНАЗИЯ|'
              r'МЕЖДУНАРОДНО|С ЧУЖДОЕЗИКОВО ОБУЧЕНИЕ|ЕЗИКОВА|ЕЗИКОВО)\b')

# A shared brand shorter than this (e.g. a bare number) is too weak to link on.
MIN_BRAND_KEY_LENGTH = 4


def brand(name: Any) -> str:
    """The registry name without legal form and institution-type words."""
    text = re.sub(r'^[\s"„“]+|[\s"„“]+$', '', str(name or ''))
    text = re.sub(LEGAL, ' ', text)
    text = re.sub(TYPE_WORDS, ' ', text)
    text = re.sub(r'[\s"„“\-]+', ' ', text).strip(' -"')
    return text


def brand_key(value: Any) -> str:
    return re.sub(r'[^а-яa-z0-9]+', '', str(value or '').casefold())


def shared_brand_key(brand_keys: Iterable[str]) -> Optional[str]:
    """The shortest brand key contained in every key, if any."""
    keys = list(brand_keys)
    for candidate in sorted(set(keys), key=len):
        if all(candidate and candidate in k for k in keys):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

# The states URL validation and the shared-site check leave a withheld website in.
WITHHELD_WEBSITE_STATUSES = frozenset({"failed_validate", "no_official_website"})


@dataclass(frozen=True)
class Institution:
    id: int
    name: str
    education_level: str
    city: str
    website_url: Optional[str]
    scrape_status: Optional[str]
    attributes: Mapping[str, Any]
    name_i18n: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Any) -> "Institution":
        names = row.name_i18n if isinstance(row.name_i18n, Mapping) else {}
        return cls(
            id=int(row.id),
            name=str(names.get("bg") or names.get("en") or ""),
            education_level=str(row.education_level or ""),
            city=str(row.city or "").strip().casefold(),
            website_url=row.website_url,
            scrape_status=row.scrape_status,
            attributes=row.attributes if isinstance(row.attributes, Mapping) else {},
            name_i18n=names,
        )


def website_evidence_gap(inst: Institution) -> Optional[str]:
    """Why the current website is not verified for a link (None when it is).

    The shared-site check must have kept or chosen (replaced to) a URL on the same
    site as the current one: that decision verified the institution's address and
    level on the site itself. A URL imported, rediscovered or moved to another site
    after the decision has no evidence yet. URL-validation withholding still excludes.
    """
    if not inst.website_url or str(inst.scrape_status or "").strip().casefold() in WITHHELD_WEBSITE_STATUSES:
        return "withheld"
    check = inst.attributes.get(SHARED_SITE_CHECK_KEY)
    if not isinstance(check, Mapping) or check.get("action") not in (KEEP, REPLACE):
        return "no shared-site check decision"
    decided_url = check.get("new_url") or (check.get("previous_url") if check.get("action") == KEEP else None)
    if not decided_url or site_id(decided_url) != site_id(inst.website_url):
        return "shared-site check decision is for another site"
    return None


def website_passed_site_checks(inst: Institution) -> bool:
    return website_evidence_gap(inst) is None


def link_rejection(kindergarten: Institution, school: Institution) -> Optional[str]:
    """Why this pair is not a kindergarten → school link (None when the evidence holds)."""
    if level_family(kindergarten.education_level) != "kindergarten":
        return "source is not kindergarten-level"
    if level_family(school.education_level) != "school":
        return "target is not school-level"
    for side, inst in (("kindergarten", kindergarten), ("school", school)):
        gap = website_evidence_gap(inst)
        if gap:
            return f"{side} website: {gap}"
    group = site_group_key(kindergarten.website_url)
    if not group or group != site_group_key(school.website_url):
        return "different site"
    if not kindergarten.city or kindergarten.city != school.city:
        return "different city"
    shared = shared_brand_key([brand_key(brand(kindergarten.name)), brand_key(brand(school.name))])
    if not shared:
        return "no shared brand"
    if len(shared) < MIN_BRAND_KEY_LENGTH:
        return "shared brand too short"
    return None


def continues_to_target(kindergarten: Institution, others: Iterable[Institution]) -> Optional[int]:
    """The one school this kindergarten continues to, or None (none or ambiguous)."""
    matches = {
        other.id
        for other in others
        if other.id != kindergarten.id and link_rejection(kindergarten, other) is None
    }
    return next(iter(matches)) if len(matches) == 1 else None


# ---------------------------------------------------------------------------
# Database (detail endpoint only)
# ---------------------------------------------------------------------------

async def same_site_institutions(db, school) -> list[Institution]:
    """Institutions in the school's country and city whose URL may share its site group."""
    from sqlalchemy import func, select

    from app.models import School

    domain = registrable_domain(school.website_url)
    if not domain:
        return []
    rows = (
        await db.execute(
            select(
                School.id,
                School.name_i18n,
                School.education_level,
                School.city,
                School.website_url,
                School.scrape_status,
                School.attributes,
            ).where(
                School.country_code == school.country_code,
                func.lower(School.city) == str(school.city or "").strip().casefold(),
                School.website_url.isnot(None),
                # A cheap superset; the exact site-group comparison happens in Python.
                func.lower(School.website_url).contains(domain.casefold(), autoescape=True),
            )
        )
    ).all()
    return [Institution.from_row(row) for row in rows]


async def continues_to(db, school) -> Optional[dict[str, Any]]:
    """The public projection of the school a kindergarten continues to, or None."""
    from sqlalchemy import select

    from app.models import School
    from app.services.school_service import SchoolService
    from app.utils.i18n_resolver import resolve_name_i18n
    from app.utils.website_data import attributes_for_publication

    source = Institution.from_row(school)
    if level_family(source.education_level) != "kindergarten" or not website_passed_site_checks(source):
        return None
    target_id = continues_to_target(source, await same_site_institutions(db, school))
    if target_id is None:
        return None
    target = (
        await db.execute(
            select(
                School.id,
                School.name_i18n,
                School.school_type,
                School.education_level,
                School.scrape_status,
                School.attributes,
            ).where(
                School.id == target_id,
                SchoolService._has_listable_location(source.city),
            )
        )
    ).first()
    if target is None:
        return None
    return {
        "id": int(target.id),
        "resolved_name_i18n": resolve_name_i18n(
            target.name_i18n, attributes_for_publication(target.attributes, target.scrape_status)
        ),
        "school_type": target.school_type,
        "education_level": target.education_level,
    }


async def continued_from(db, school) -> list[dict[str, Any]]:
    """The kindergartens that continue to this school (the inverse of ``continues_to``)."""
    from sqlalchemy import select

    from app.models import School
    from app.services.school_service import SchoolService
    from app.utils.i18n_resolver import resolve_name_i18n
    from app.utils.website_data import attributes_for_publication

    target = Institution.from_row(school)
    if level_family(target.education_level) != "school" or not website_passed_site_checks(target):
        return []
    others = await same_site_institutions(db, school)
    source_ids = [
        other.id
        for other in others
        if level_family(other.education_level) == "kindergarten"
        and continues_to_target(other, others) == target.id
    ]
    if not source_ids:
        return []
    rows = (
        await db.execute(
            select(
                School.id,
                School.name_i18n,
                School.school_type,
                School.education_level,
                School.scrape_status,
                School.attributes,
            ).where(
                School.id.in_(source_ids),
                SchoolService._has_listable_location(target.city),
            ).order_by(School.id)
        )
    ).all()
    return [
        {
            "id": int(row.id),
            "resolved_name_i18n": resolve_name_i18n(
                row.name_i18n, attributes_for_publication(row.attributes, row.scrape_status)
            ),
            "school_type": row.school_type,
            "education_level": row.education_level,
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Same place: one pin and one card for a kindergarten and its school
# ---------------------------------------------------------------------------
#
# A private kindergarten and a private school of one brand whose pins are this close
# are one place to a parent (Светлина, Д-р Петър Берон). Registry name, level, type,
# city and pin are all public, so no website evidence is needed. State institutions
# are left out: a municipal kindergarten and a state school are separate bodies even
# when they share a patron's name.

SAME_PLACE_MAX_METRES = 150


@dataclass(frozen=True)
class PlacePin:
    school_id: int
    location_id: int
    lat: float
    lng: float
    name: str
    education_level: str
    school_type: str
    city: str


def _same_place_pair(first: PlacePin, second: PlacePin) -> bool:
    from app.services.geocoding.service import _distance_m

    families = {level_family(first.education_level), level_family(second.education_level)}
    if families != {"kindergarten", "school"}:
        return False
    if "state" in (first.school_type, second.school_type):
        return False
    if not first.city or first.city != second.city:
        return False
    shared = shared_brand_key([brand_key(brand(first.name)), brand_key(brand(second.name))])
    if not shared or len(shared) < MIN_BRAND_KEY_LENGTH:
        return False
    return _distance_m(first.lat, first.lng, second.lat, second.lng) <= SAME_PLACE_MAX_METRES


def same_place_groups(pins: Iterable[PlacePin]) -> list[list[PlacePin]]:
    """Pins of two or more institutions that form one place.

    A kindergarten pin joins a school pin under :func:`_same_place_pair`; a place is
    everything joined that way, so a kindergarten next to a school and its gymnasium
    makes one place of three.
    """
    pins = list(pins)
    parent = list(range(len(pins)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for i, first in enumerate(pins):
        for j in range(i + 1, len(pins)):
            if pins[j].school_id != first.school_id and _same_place_pair(first, pins[j]):
                parent[root(i)] = root(j)

    groups: dict[int, list[PlacePin]] = {}
    for index, pin in enumerate(pins):
        groups.setdefault(root(index), []).append(pin)
    return [
        sorted(group, key=lambda pin: (pin.school_id, pin.location_id))
        for group in groups.values()
        if len({pin.school_id for pin in group}) > 1
    ]


async def same_place_by_school(db, *, country_code: str, city: Optional[str]) -> dict[int, list[dict[str, Any]]]:
    """For each listed school in a place with others: the others, as published.

    Each entry is the other institution's public projection plus ``location_id`` (this
    school's pin in the place) and ``other_location_id`` (the other's pin there).
    """
    from sqlalchemy import select

    from app.models import School, SchoolLocation
    from app.services.school_service import SchoolService
    from app.utils.i18n_resolver import resolve_name_i18n
    from app.utils.website_data import attributes_for_publication

    query = (
        select(
            School.id,
            School.name_i18n,
            School.education_level,
            School.school_type,
            School.city,
            SchoolLocation.id.label("location_id"),
            SchoolLocation.lat,
            SchoolLocation.lng,
        )
        .join(SchoolLocation, SchoolLocation.school_id == School.id)
        .where(
            School.country_code == country_code,
            School.school_type != "state",
            SchoolService._scoped_resolved_location_clause(SchoolLocation, city),
        )
    )
    city_clause = SchoolService._city_clause(city)
    if city_clause is not None:
        query = query.where(city_clause)
    rows = (await db.execute(query)).all()
    pins = [
        PlacePin(
            school_id=int(row.id),
            location_id=int(row.location_id),
            lat=float(row.lat),
            lng=float(row.lng),
            name=str((row.name_i18n or {}).get("bg") or (row.name_i18n or {}).get("en") or ""),
            education_level=str(row.education_level or ""),
            school_type=str(row.school_type or ""),
            city=str(row.city or "").strip().casefold(),
        )
        for row in rows
    ]
    groups = same_place_groups(pins)
    if not groups:
        return {}

    ids = {pin.school_id for group in groups for pin in group}
    published = {
        int(row.id): {
            "id": int(row.id),
            "resolved_name_i18n": resolve_name_i18n(
                row.name_i18n, attributes_for_publication(row.attributes, row.scrape_status)
            ),
            "school_type": row.school_type,
            "education_level": row.education_level,
        }
        for row in (
            await db.execute(
                select(
                    School.id,
                    School.name_i18n,
                    School.school_type,
                    School.education_level,
                    School.scrape_status,
                    School.attributes,
                ).where(School.id.in_(ids))
            )
        ).all()
    }

    result: dict[int, list[dict[str, Any]]] = {}
    for group in groups:
        for pin in group:
            seen: set[int] = set()
            for other in group:
                if other.school_id == pin.school_id or other.school_id in seen:
                    continue
                seen.add(other.school_id)
                result.setdefault(pin.school_id, []).append(
                    {**published[other.school_id], "location_id": pin.location_id, "other_location_id": other.location_id}
                )
    return result
