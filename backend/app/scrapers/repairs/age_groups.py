"""Repair command: add missing age groups to a school's location from its website's own words."""

from __future__ import annotations

import builtins
import logging
import re
from collections import defaultdict
from typing import Optional
from urllib.parse import unquote

from rich.console import Console
from rich.table import Table

from app.scrapers.stages import common as stage_common
from app.scrapers.stages import navigate as navigate_stage

logger = logging.getLogger(__name__)
console = Console()


_AGE_GROUP_ORDER = {
    "nursery": 0,
    "first": 1,
    "second": 2,
    "third": 3,
    "preschool": 4,
    "grade_1_4": 5,
    "grade_5_7": 6,
    "grade_8_12": 7,
}


_AGE_GROUP_REPAIR_URL_HINTS = {
    "preschool": ("предучилищ", "preduchilisht", "podgotvit", "podgotov"),
    "grade_1_4": ("първи-клас", "parvi-klas", "1-klas", "i-klas"),
    "grade_5_7": ("пети-клас", "peti-klas", "5-klas"),
    "grade_8_12": ("след-седми-клас", "sled-sedmi-klas", "8-klas", "osmi-klas"),
}


_AGE_GROUP_REPAIR_TEXT_PATTERNS = {
    "preschool": (
        re.compile(r"\bпредучилищни\s+групи\b", flags=re.IGNORECASE),
        re.compile(r"\bподготвителна\s+група\b", flags=re.IGNORECASE),
        # Private schools' own names for the group: "ПУК 1" / "ПУК (5-6 г.)" (предучилищен
        # клас) and "ПК" in a grade range such as "ПК – 4 клас". Both need that context: a
        # bare "ПУК" is also an ordinary word in all-caps text, and "ПК" a PC or postal code.
        # "Подготвителен клас" is left out: it is also the 8th-grade intensive-language year.
        re.compile(r"\bПУК\s*(?:\d|\()"),
        re.compile(r"\bПК\s*[-–—‑]\s*(?:4|IV)\.?\s*(?i:клас)\b"),
    ),
    "grade_1_4": (
        re.compile(r"\bпърви\s+клас\b", flags=re.IGNORECASE),
        re.compile(r"\b1\.\s*клас\b", flags=re.IGNORECASE),
        re.compile(r"\bI[-\s]*ви?\s+клас\b", flags=re.IGNORECASE),
    ),
    "grade_5_7": (
        re.compile(r"\bпети\s+клас\b", flags=re.IGNORECASE),
        re.compile(r"\b5\.\s*клас\b", flags=re.IGNORECASE),
    ),
    "grade_8_12": (
        re.compile(r"\bслед\s+седми\s+клас\b", flags=re.IGNORECASE),
        re.compile(r"\bслед\s+7\s+клас\b", flags=re.IGNORECASE),
        re.compile(r"\bминимален\s+бал\b", flags=re.IGNORECASE),
        re.compile(r"\bбалообразуване\b", flags=re.IGNORECASE),
    ),
}


# Words right before a preschool mention that show the school is not offering the group:
# the certificate a first-grader brings ("удостоверение за завършена подготвителна група"),
# a negation, a partner kindergarten, or the state-funding rule quoted from the law.
_PRESCHOOL_NON_OFFER_PREFIX_RE = re.compile(
    r"(?:завършен\w*|завършил\w*|не\s+е\s+посещавал\w*|не\s+организира|партньори\s+за"
    r"|приеманите\s+деца(?:\s+и\s+ученици)?\s+в)\W*$",
    flags=re.IGNORECASE,
)


_CLASS_TEACHER_PAGE_RE = re.compile(r"(класни\s+ръководители|class\s+teachers?)", flags=re.IGNORECASE)


_AGE_GROUP_CLASS_PATTERNS = {
    "preschool": re.compile(r"(?im)^\s*(?:3|4)\.\s*група\b"),
    "grade_1_4": re.compile(r"(?im)^\s*(?:1|2|3|4)\s*[а-яa-z]\b"),
    "grade_5_7": re.compile(r"(?im)^\s*(?:5|6|7)\s*[а-яa-z]\b"),
    "grade_8_12": re.compile(r"(?im)^\s*(?:8|9|10|11|12)\s*[а-яa-z]\b"),
}


def _sorted_age_groups(age_groups: set[str] | list[str]) -> list[str]:
    return sorted(set(age_groups), key=lambda value: (_AGE_GROUP_ORDER.get(value, 99), value))


def _select_age_group_repair_location(school):
    locations = builtins.list(school.locations or [])
    if not locations:
        return None
    if len(locations) == 1:
        return locations[0]

    primary = stage_common.primary_location_for_school(school)
    if primary is None:
        return None

    non_primary_with_groups = [
        location
        for location in locations
        if location.id != primary.id and builtins.list(location.age_group_shifts or [])
    ]
    if not non_primary_with_groups:
        return primary
    return None


def _existing_age_groups_for_location(location) -> set[str]:
    return {
        item.age_group
        for item in builtins.list(location.age_group_shifts or [])
        if item and item.age_group
    }


def _school_name_bg(school) -> str:
    return str(dict(getattr(school, "name_i18n", {}) or {}).get("bg") or "").lower()


def _allowed_age_group_repairs_for_school(school) -> set[str]:
    education_level = str(getattr(school, "education_level", "") or "")
    name_bg = _school_name_bg(school)

    # A gymnasium (registry type 125 profiled / 126 vocational) has no preschool or
    # grades 1-4; pages that mention them describe a sibling school on a shared site,
    # a partner kindergarten or quoted legal text.
    if dict(getattr(school, "attributes", None) or {}).get("moe_detailed_type") in (125, 126):
        return {"grade_5_7", "grade_8_12"}

    if "обединено училище" in name_bg:
        return {"preschool", "grade_1_4", "grade_5_7", "grade_8_12"}

    if education_level == "kindergarten":
        return {"preschool"}

    if education_level == "primary":
        return {"preschool", "grade_1_4"}

    if education_level == "lower_secondary" or "основно училище" in name_bg:
        return {"preschool", "grade_1_4", "grade_5_7"}

    if education_level == "upper_secondary" or "средно училище" in name_bg:
        return {"preschool", "grade_1_4", "grade_5_7", "grade_8_12"}

    if "начално училище" in name_bg:
        return {"preschool", "grade_1_4"}

    return set()


def _missing_allowed_age_group_repairs(school, location) -> list[str]:
    allowed_age_groups = _allowed_age_group_repairs_for_school(school)
    if not allowed_age_groups:
        return []

    existing_age_groups = _existing_age_groups_for_location(location)
    return _sorted_age_groups(allowed_age_groups - existing_age_groups)


def _should_refresh_age_group_navigation(school) -> bool:
    repair_location = _select_age_group_repair_location(school)
    if repair_location is None:
        return False

    if _missing_allowed_age_group_repairs(school, repair_location):
        return True
    return False


def _infer_age_group_evidence_from_source_pages(source_pages) -> dict[str, list[str]]:
    evidence: dict[str, list[str]] = defaultdict(builtins.list)

    for page in builtins.list(source_pages or []):
        raw_markdown = getattr(page, "raw_markdown", None) or ""
        if not raw_markdown:
            continue

        source_url = getattr(page, "source_url", "") or ""
        decoded_url = unquote(source_url).lower()
        normalized_text = re.sub(r"\s+", " ", raw_markdown).strip()

        for age_group, url_hints in _AGE_GROUP_REPAIR_URL_HINTS.items():
            if any(hint in decoded_url for hint in url_hints):
                evidence[age_group].append(source_url)

        for age_group, patterns in _AGE_GROUP_REPAIR_TEXT_PATTERNS.items():
            matches = (match for pattern in patterns for match in pattern.finditer(normalized_text))
            if age_group == "preschool":
                matches = (
                    match
                    for match in matches
                    if not _PRESCHOOL_NON_OFFER_PREFIX_RE.search(
                        normalized_text[max(0, match.start() - 60) : match.start()]
                    )
                )
            if next(matches, None) is not None:
                evidence[age_group].append(source_url)

        if "класни-ръководители" in decoded_url or "klasni-rakovoditeli" in decoded_url or _CLASS_TEACHER_PAGE_RE.search(
            normalized_text
        ):
            for age_group, pattern in _AGE_GROUP_CLASS_PATTERNS.items():
                if pattern.search(raw_markdown):
                    evidence[age_group].append(source_url)

    return {
        age_group: builtins.list(dict.fromkeys(urls))
        for age_group, urls in evidence.items()
    }


async def repair_age_groups_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    dry_run: bool,
    refresh_navigation: bool,
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.database import async_session_maker
    from app.models import School, SchoolLocation, SchoolLocationAgeGroupShift

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await stage_common.find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        id_query = select(School.id).where(
            School.country_code == country,
            School.website_url.isnot(None),
        )
        if city:
            id_query = id_query.where(School.city == city)
        if school_id:
            id_query = id_query.where(School.id == school_id)
        if limit:
            id_query = id_query.limit(limit)

        target_school_ids = [row[0] for row in (await db.execute(id_query)).all()]
        if not target_school_ids:
            console.print("[yellow]No website-backed schools matched the age-group repair filters.[/yellow]")
            return

        school_query = (
            select(School)
            .options(
                selectinload(School.locations).selectinload(SchoolLocation.age_group_shifts),
                selectinload(School.source_pages),
            )
            .where(School.id.in_(target_school_ids))
        )
        schools = (await db.execute(school_query)).scalars().all()

        if refresh_navigation and not dry_run:
            refresh_school_ids = [
                school.id
                for school in schools
                if _should_refresh_age_group_navigation(school)
            ]
            if refresh_school_ids:
                console.print(
                    f"[cyan]Refreshing navigation for {len(refresh_school_ids)} likely age-group candidates...[/cyan]"
                )
                await navigate_stage.run_navigate_batch(
                    db=db,
                    country=country,
                    city=city or "",
                    limit=None,
                    include_navigated=True,
                    school_ids=refresh_school_ids,
                    skip_timed_out_chunks=True,
                )
                schools = (await db.execute(school_query)).scalars().all()
            else:
                console.print("[yellow]No likely age-group candidates needed navigation refresh.[/yellow]")

        preview_rows: list[tuple[int, str, str, str]] = []
        repaired_school_ids: list[int] = []
        skipped_multi_location = 0

        for school in schools:
            repair_location = _select_age_group_repair_location(school)
            if repair_location is None:
                skipped_multi_location += 1
                continue

            evidence = _infer_age_group_evidence_from_source_pages(school.source_pages)
            if not evidence:
                continue

            existing_age_groups = _existing_age_groups_for_location(repair_location)
            allowed_age_groups = _allowed_age_group_repairs_for_school(school)
            missing_age_groups = _sorted_age_groups((set(evidence) & allowed_age_groups) - existing_age_groups)
            if not missing_age_groups:
                continue

            evidence_summary = "; ".join(
                f"{age_group}: {', '.join(evidence[age_group][:1])}"
                for age_group in missing_age_groups
            )
            preview_rows.append(
                (
                    school.id,
                    stage_common.school_label(school),
                    ", ".join(missing_age_groups),
                    evidence_summary,
                )
            )

            if dry_run:
                continue

            for age_group in missing_age_groups:
                db.add(
                    SchoolLocationAgeGroupShift(
                        location_id=repair_location.id,
                        age_group=age_group,
                        shift=None,
                        has_organised_groups=None,
                    )
                )
            repaired_school_ids.append(school.id)

        if not preview_rows:
            console.print("[yellow]No age-group repair candidates found.[/yellow]")
            return

        console.print(f"[cyan]Found {len(preview_rows)} age-group repair candidates[/cyan]")
        console.print(f"  Mode: {'dry-run' if dry_run else 'commit'}")
        console.print(f"  Refresh navigation first: {refresh_navigation and not dry_run}")
        console.print(f"  Skipped multi-location schools: {skipped_multi_location}")

        preview_table = Table(title="Age Group Repair")
        preview_table.add_column("ID", style="cyan", no_wrap=True)
        preview_table.add_column("School")
        preview_table.add_column("Missing age groups")
        preview_table.add_column("Evidence")
        for row in preview_rows[:20]:
            preview_table.add_row(str(row[0]), row[1], row[2], row[3])
        console.print(preview_table)
        if len(preview_rows) > 20:
            console.print(f"[dim]Showing first 20 of {len(preview_rows)} repair candidates[/dim]")

        if dry_run:
            return

        await db.commit()
        console.print(f"[green]✓ Repaired age groups for {len(repaired_school_ids)} schools[/green]")
