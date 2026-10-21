"""Site-grades stage: the grades a school's own website says it teaches.

A school's age groups come from the register's school type, which says nothing for some
types: an international-treaty school (type 181: the Anglo-American, French and German
schools) defaults to grades 8-12, a special school to its stage only, and a private
"средно училище" licensed to grade 12 may teach only to grade 7 (ЕСПА). The parent trial
(2026-10-19) found AAS shown as "Grades 8-12" (it is PreK-12) and ЕСПА as "1-12".

The school's own site, as we already publish it, says better: the grades its tuition rows
are for, the grades its admission facts name, and the highest grade its facts state. This
stage adds the bands those name (preschool, 1-4, 5-7, 8-12) and hides a band above the
highest grade the site states when nothing on the site names a grade in it. Both are
tagged on the location (``campus_sync.SITE_GRADES_ADDED_PREFIX`` / ``_HIDDEN_PREFIX``):
withholding the website undoes both (``remove_website_campus_data``) and the register
import keeps both (``base_adapter._sync_source_location``). Each run recomputes from the register's own
bands, so a band the site stops naming goes back. Schools only, one location with school
bands only (several are left as they are). No model calls; the default is a dry run.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocationAgeGroupShift
from app.scrapers.campus_sync import (
    AGE_GROUP_TAG_PREFIX,
    SITE_GRADES_ADDED_PREFIX,
    SITE_GRADES_HIDDEN_PREFIX,
    site_hidden_band,
    site_hidden_bands,
    site_hidden_tag,
)
from app.scrapers.price_evidence import PRESCHOOL_GRADE, label_grades
from app.scrapers.sources.bg.school_kinds import EVENING_SCHOOL_BANDS, is_evening_school
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

BANDS: dict[str, tuple[int, int]] = {
    "preschool": (0, 0),
    "grade_1_4": (1, 4),
    "grade_5_7": (5, 7),
    "grade_8_12": (8, 12),
}
# The class before first grade, said plainly. Not "подготвителен клас/година": that is
# also the intensive-language year before 8th grade (581's "Подготвителна година").
_PRESCHOOL_RE = re.compile(
    r"подготвителн[аи]\s+груп|предучилищ|\bпук\b|\bпг\b|pre-?k(?:indergarten)?\b|pre-?school|"
    r"preparatory\s+group|kindergarten|maternelle|petite\s+section|grande\s+section",
    re.IGNORECASE,
)
_RANGE_DASH_RE = re.compile(r"[-–—]|\bto\b|\bдо\b")


def fee_label_grades(label: Optional[str]) -> set[int]:
    """Grades a tuition label names, 0 for the class before first grade.

    "Kindergarten – Grade 5" (506) runs from that class to the 5th grade, and "Grade 6 –
    Grade 8" from the 6th to the 8th.
    """
    text = str(label or "")
    grades = {grade for grade in label_grades(text) if grade != PRESCHOOL_GRADE or _PRESCHOOL_RE.search(text)}
    # "Grade 9 – Grade 12": the grade word repeated hides the range from label_grades.
    school_grades = grades - {PRESCHOOL_GRADE}
    if len(school_grades) == 2 and _RANGE_DASH_RE.search(text):
        grades |= set(range(min(school_grades), max(school_grades) + 1))
    if _PRESCHOOL_RE.search(text):
        school_grades = grades - {PRESCHOOL_GRADE}
        if school_grades and _RANGE_DASH_RE.search(text):
            grades |= set(range(PRESCHOOL_GRADE, min(school_grades) + 1))
        grades.add(PRESCHOOL_GRADE)
    return grades


def site_grades(pricing: Iterable[Any], at_a_glance: Optional[dict[str, Any]]) -> tuple[set[int], Optional[int]]:
    """The grades the published site data names, and the highest grade its facts state."""
    named: set[int] = set()
    for row in pricing:
        if str(getattr(row.category, "value", row.category)) != "tuition":
            continue
        context = row.pricing_context
        english = context.get("age_group_en") if isinstance(context, dict) else getattr(context, "age_group_en", None)
        for label in (row.age_group, row.plan_name, english):
            named |= fee_label_grades(label)
    glance = at_a_glance or {}
    for admission in glance.get("admission") or []:
        grade = admission.get("grade")
        if isinstance(grade, int):
            named |= set(range(grade, max(grade, admission.get("grade_to") or grade) + 1))
    highest = glance.get("highest_grade")
    return named, highest if isinstance(highest, int) else None


def _band(grade: int) -> Optional[str]:
    return next((key for key, (low, high) in BANDS.items() if low <= grade <= high), None)


def planned_bands(register_bands: set[str], named: set[int], highest: Optional[int]) -> tuple[set[str], set[str]]:
    """(bands to add, bands to hide) given the register's school bands and the site's grades."""
    stated = named | ({highest} if highest is not None else set())
    add = {band for grade in stated if (band := _band(grade))} - register_bands
    hide = {
        band
        for band in register_bands
        if highest is not None
        and BANDS[band][0] > highest
        and not any(BANDS[band][0] <= grade <= BANDS[band][1] for grade in named)
    }
    if not register_bands - hide:
        return add, set()  # a misread highest grade must not take every register band away
    return add, hide


def _tagged(tags: Iterable[str], prefix: str) -> set[str]:
    return {tag[len(prefix):] for tag in tags if tag.startswith(prefix)}


async def run_site_grades(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = "sofia",
    school_ids: Sequence[int] = (),
    apply: bool = False,
) -> dict[str, Any]:
    """Plan (and with ``apply`` write) each school's bands; returns the report."""
    from app.schemas.school import SchoolDetailResponse
    from app.services.school_service import SchoolService

    query = select(School.id).where(
        School.country_code == country,
        School.education_level != "kindergarten",
        School.website_url.isnot(None),
        School.scrape_status.in_(SITE_ACCEPTED_STATUSES),
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    ids = list((await db.execute(query.order_by(School.id))).scalars().all())

    service = SchoolService(db)
    changes: list[dict[str, Any]] = []
    for school_id in ids:
        school = await service.get_school_with_details(school_id)
        locations = [
            location
            for location in school.locations
            if {link.age_group for link in location.age_group_shifts} & set(BANDS)
            or site_hidden_bands(location.location_tags)
        ]
        if len(locations) != 1:
            continue
        location = locations[0]
        tags = list(location.location_tags or [])
        added, hidden = _tagged(tags, SITE_GRADES_ADDED_PREFIX), set(site_hidden_bands(tags))
        current = {link.age_group for link in location.age_group_shifts}
        # Bands the website's campus data added are the site's claim, not the register's.
        website = _tagged(tags, AGE_GROUP_TAG_PREFIX)
        register_bands = ((current - added - website) | hidden) & set(BANDS)
        published = SchoolDetailResponse.model_validate(school)
        facts = published.school_facts
        glance = facts.at_a_glance.model_dump() if facts and facts.at_a_glance else None
        named, highest = site_grades(published.pricing, glance)
        add, hide = planned_bands(register_bands, named, highest)
        if is_evening_school((school.name_i18n or {}).get("bg")):
            add &= EVENING_SCHOOL_BANDS  # "1-12 клас" there includes classes for adults
        desired = (register_bands | add | website) - hide
        # Tags alone can match while a band came back another way (a campus sync, an import).
        if add == added and hide == hidden and desired == current & set(BANDS):
            continue
        change = {
            "school_id": school_id,
            "name_bg": (school.name_i18n or {}).get("bg"),
            "register": sorted(register_bands),
            "add": sorted(add),
            "hide": sorted(hide),
            "highest_grade": highest,
            "site_grades": sorted(named),
        }
        changes.append(change)
        if not apply:
            continue
        # A band hidden before keeps its mark; one hidden now records its shift and groups.
        hidden_tags = [tag for tag in tags if site_hidden_band(tag) in hide]
        hidden_tags += [site_hidden_tag(link) for link in location.age_group_shifts if link.age_group in hide - hidden]
        for link in list(location.age_group_shifts):
            if link.age_group in BANDS and link.age_group not in desired:
                await db.delete(link)
        was_hidden = site_hidden_bands(tags)
        for band in sorted(desired - current):
            db.add(SchoolLocationAgeGroupShift(location_id=location.id, age_group=band, **was_hidden.get(band, {})))
        kept = [tag for tag in tags if not tag.startswith((SITE_GRADES_ADDED_PREFIX, SITE_GRADES_HIDDEN_PREFIX))]
        location.location_tags = (
            kept
            + [SITE_GRADES_ADDED_PREFIX + band for band in sorted(add)]
            + sorted(hidden_tags)
        )
        await db.commit()
        change["applied"] = True
    return {"checked": len(ids), "changes": changes, "applied": apply}
