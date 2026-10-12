"""Repair command: re-navigate and re-extract schools whose location data looks wrong."""

from __future__ import annotations

import builtins
import logging
from collections import defaultdict
from typing import Optional

from rich.console import Console
from rich.table import Table

from app.scrapers.stages import common as stage_common
from app.scrapers.stages import extract as extract_stage
from app.scrapers.stages import navigate as navigate_stage

logger = logging.getLogger(__name__)
console = Console()


def _location_has_tag(location, tag: str) -> bool:
    return any(str(value) == tag for value in builtins.list(location.location_tags or []))


def _location_repair_reasons_for_school(school) -> list[str]:
    if not school.website_url:
        return []

    location = stage_common.primary_location_for_school(school)
    if location is None:
        return []

    from app.scrapers.extractor import _address_looks_like_registry_office

    reasons: list[str] = []
    address_bg = dict(location.address_i18n or {}).get("bg") or dict(location.address_i18n or {}).get("en") or ""
    if _address_looks_like_registry_office(address_bg):
        reasons.append("office-like-address")
    if location.lat is None or location.lng is None:
        reasons.append("missing-coords")
    if _location_has_tag(location, "coords_source=geojson") or _location_has_tag(location, "coords_source=geojson_website"):
        reasons.append("geojson-coords")
    return reasons


async def repair_locations_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    include_state: bool,
    dry_run: bool,
    run_extract: bool,
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.database import async_session_maker
    from app.models import School

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await stage_common.find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        query = (
            select(School)
            .options(selectinload(School.locations))
            .where(
                School.country_code == country,
                School.website_url.isnot(None),
            )
        )
        if city:
            query = query.where(School.city == city)
        if school_id:
            query = query.where(School.id == school_id)
        if not include_state:
            query = query.where(School.school_type.in_(["private", "international"]))
        if limit:
            query = query.limit(limit)

        schools = (await db.execute(query)).scalars().all()
        candidate_rows: list[tuple[int, str, str, str]] = []
        target_school_ids: list[int] = []
        reason_counts: dict[str, int] = defaultdict(int)

        for school in schools:
            reasons = _location_repair_reasons_for_school(school)
            if not reasons:
                continue
            location = stage_common.primary_location_for_school(school)
            address_bg = dict(location.address_i18n or {}).get("bg") or dict(location.address_i18n or {}).get("en") or ""
            candidate_rows.append((school.id, stage_common.school_label(school), ", ".join(reasons), address_bg))
            target_school_ids.append(school.id)
            for reason in reasons:
                reason_counts[reason] += 1

        if not candidate_rows:
            console.print("[yellow]No location-repair candidates found.[/yellow]")
            return

        console.print(f"[cyan]Found {len(candidate_rows)} location-repair candidates[/cyan]")
        console.print(f"  Office-like addresses: {reason_counts.get('office-like-address', 0)}")
        console.print(f"  Missing coordinates: {reason_counts.get('missing-coords', 0)}")
        console.print(f"  GeoJSON coordinates: {reason_counts.get('geojson-coords', 0)}")
        console.print(f"  Re-extract after navigate: {run_extract and not dry_run}")

        preview_table = Table(title="Location Repair Candidates")
        preview_table.add_column("ID", style="cyan", no_wrap=True)
        preview_table.add_column("School")
        preview_table.add_column("Reasons")
        preview_table.add_column("Primary address")
        for row in candidate_rows[:20]:
            preview_table.add_row(str(row[0]), row[1], row[2], row[3])
        console.print(preview_table)
        if len(candidate_rows) > 20:
            console.print(f"[dim]Showing first 20 of {len(candidate_rows)} repair candidates[/dim]")

        if dry_run:
            return

        console.print(f"[cyan]Refreshing navigation for {len(target_school_ids)} schools...[/cyan]")
        navigate_results = await navigate_stage.run_navigate_batch(
            db=db,
            country=country,
            city=city or "",
            limit=None,
            include_navigated=True,
            school_ids=target_school_ids,
            skip_timed_out_chunks=True,
        )
        if not run_extract:
            return

        extract_school_ids = [
            int(result["school_id"])
            for result in builtins.list(navigate_results or [])
            if result and result.get("success") and result.get("school_id") is not None
        ]
        if not extract_school_ids:
            console.print("[yellow]No schools finished navigation cleanly; skipping extract.[/yellow]")
            return

        console.print(f"[cyan]Refreshing extraction for {len(extract_school_ids)} schools...[/cyan]")
        await extract_stage.run_extract_batch(
            db=db,
            country=country,
            city=city or "",
            limit=None,
            include_extracted=True,
            school_ids=extract_school_ids,
        )
