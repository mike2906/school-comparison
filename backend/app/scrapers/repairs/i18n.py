"""Repair command: clear transliterated (synthetic) English names and addresses, then re-extract."""

from __future__ import annotations

import logging
from typing import Optional

from rich.console import Console
from rich.table import Table

from app.scrapers.stages import common as stage_common
from app.scrapers.stages import extract as extract_stage

logger = logging.getLogger(__name__)
console = Console()


def _has_synthetic_school_name_en(school) -> bool:
    from app.utils.transliteration import transliterate_bulgarian

    name_i18n = dict(school.name_i18n or {})
    bg_name = name_i18n.get("bg")
    en_name = name_i18n.get("en")
    if not bg_name or not en_name:
        return False
    return en_name.strip() == transliterate_bulgarian(bg_name).strip()


def _has_synthetic_location_address_en(location) -> bool:
    from app.utils.transliteration import transliterate_address

    address_i18n = dict(location.address_i18n or {})
    bg_address = address_i18n.get("bg")
    en_address = address_i18n.get("en")
    if not bg_address or not en_address:
        return False
    return en_address.strip() == transliterate_address(bg_address).strip()


async def repair_i18n_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    dry_run: bool,
    reextract: bool,
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.database import async_session_maker
    from app.models import School, SourcePage
    from app.models.scrape_log import ScrapeType

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await stage_common.find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        query = (
            select(School)
            .options(selectinload(School.locations))
            .where(School.country_code == country)
        )
        if city:
            query = query.where(School.city == city)
        if school_id:
            query = query.where(School.id == school_id)
        if limit:
            query = query.limit(limit)

        schools = (await db.execute(query)).scalars().all()

        affected_schools = []
        affected_name_count = 0
        affected_location_count = 0
        preview_rows: list[tuple[int, str, bool, int]] = []

        for school in schools:
            has_name_issue = _has_synthetic_school_name_en(school)
            affected_locations = [
                location for location in (school.locations or []) if _has_synthetic_location_address_en(location)
            ]
            if not has_name_issue and not affected_locations:
                continue

            affected_schools.append(school)
            affected_name_count += int(has_name_issue)
            affected_location_count += len(affected_locations)
            preview_rows.append((school.id, stage_common.school_label(school), has_name_issue, len(affected_locations)))

        if not affected_schools:
            console.print("[yellow]No schools with synthetic EN i18n values found.[/yellow]")
            return

        console.print(f"[cyan]Found {len(affected_schools)} affected schools[/cyan]")
        console.print(f"  Synthetic school names: {affected_name_count}")
        console.print(f"  Synthetic location addresses: {affected_location_count}")
        console.print(f"  Mode: {'dry-run' if dry_run else 'commit'}")
        console.print(f"  Re-extract: {reextract}")

        preview_table = Table(title="Affected Schools")
        preview_table.add_column("ID", style="cyan", no_wrap=True)
        preview_table.add_column("School")
        preview_table.add_column("Name EN", justify="center")
        preview_table.add_column("Addr EN", justify="right")
        for row in preview_rows[:20]:
            preview_table.add_row(str(row[0]), row[1], "yes" if row[2] else "", str(row[3]))
        console.print(preview_table)
        if len(preview_rows) > 20:
            console.print(f"[dim]Showing first 20 of {len(preview_rows)} affected schools[/dim]")

        if dry_run:
            return

        repaired_school_ids: list[int] = []
        for school in affected_schools:
            changed = False
            if _has_synthetic_school_name_en(school):
                name_i18n = dict(school.name_i18n or {})
                name_i18n.pop("en", None)
                school.name_i18n = name_i18n
                changed = True

            for location in school.locations or []:
                if not _has_synthetic_location_address_en(location):
                    continue
                address_i18n = dict(location.address_i18n or {})
                address_i18n.pop("en", None)
                location.address_i18n = address_i18n
                changed = True

            if changed:
                repaired_school_ids.append(school.id)

        await db.commit()
        console.print(f"[green]✓ Cleared synthetic EN values for {len(repaired_school_ids)} schools[/green]")

        if not reextract:
            return

        page_query = (
            select(SourcePage.school_id)
            .where(
                SourcePage.school_id.in_(repaired_school_ids),
                SourcePage.scrape_type == ScrapeType.WEBSITE,
                SourcePage.is_valid.is_(True),
                SourcePage.raw_markdown.isnot(None),
            )
            .distinct()
        )
        eligible_ids = [row[0] for row in (await db.execute(page_query)).all()]
        ineligible_count = len(repaired_school_ids) - len(eligible_ids)
        if ineligible_count:
            console.print(
                f"[yellow]{ineligible_count} repaired schools have no navigated website content; skipped re-extraction[/yellow]"
            )
        if not eligible_ids:
            return

        console.print(f"[cyan]Re-extracting {len(eligible_ids)} repaired schools...[/cyan]")
        await extract_stage.run_extract_batch(
            db=db,
            country=country,
            city=city or "",
            limit=None,
            include_extracted=True,
            school_ids=eligible_ids,
        )
