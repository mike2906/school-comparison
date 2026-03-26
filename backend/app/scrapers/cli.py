from __future__ import annotations

"""
CLI tool for running scraping pipeline stages.

This provides a convenient interface for testing and debugging the scraping
pipeline without needing Celery workers.

Usage:
    # Run by school name (fuzzy match)
    uv run python -m app.scrapers.cli run --school "British School" --stage discover

    # Run by school ID
    uv run python -m app.scrapers.cli run --school-id 42 --stage validate-urls

    # Discover/normalize website URLs
    uv run python -m app.scrapers.cli run --stage discover-websites --city sofia --limit 20

    # Retry failed URL discovery+validation (failed_validate only)
    uv run python -m app.scrapers.cli run --stage recover-failed-urls --city sofia --limit 20

    # Run batch with limit
    uv run python -m app.scrapers.cli run --stage discover --city sofia --limit 10

    # Synchronous mode (no Celery)
    uv run python -m app.scrapers.cli run --stage all --sync --limit 5

    # Dry run (show what would happen)
    uv run python -m app.scrapers.cli run --stage discover --dry-run

    # Reset school status
    uv run python -m app.scrapers.cli reset --school "134 СУ" --to pending

    # List schools
    uv run python -m app.scrapers.cli list --city sofia --limit 10

    # Show stats
    uv run python -m app.scrapers.cli stats --city sofia

    # Clear legacy transliterated EN names/addresses and rerun extraction
    uv run python -m app.scrapers.cli repair-i18n --city sofia

    # Refresh location data only for likely-bad website-backed schools
    uv run python -m app.scrapers.cli repair-locations --city sofia
"""
import asyncio
import builtins
from collections import defaultdict
from math import ceil
import sys
import logging
import random
import re
from urllib.parse import urlparse
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
# Keep CLI output focused on scraper progress; DEBUG=true still enables app-level behavior.
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

console = Console()

_BRAND_ALIAS_GENERIC_EXACT = {
    "school",
    "kindergarten",
    "preschool",
    "academy",
    "училище",
    "детска градина",
    "гимназия",
}
_BRAND_ALIAS_BANNED_SUBSTRINGS = {
    "google reference school",
    "елитно канадско образование",
    "school community",
    "our identity",
    "иновативно училище",
}
_BRAND_ALIAS_BANNED_MARKERS = {
    "парти център",
    "портал",
    "@school",
    "→",
}


@click.group()
def cli():
    """Sofia School Comparison - Scraping Pipeline CLI"""
    pass


@cli.command("repair-i18n")
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to inspect")
@click.option("--dry-run", is_flag=True, help="Show affected schools without changing data")
@click.option("--no-reextract", is_flag=True, help="Clear synthetic EN values without rerunning extraction")
def repair_i18n(school, school_id, city, country, limit, dry_run, no_reextract):
    """Repair legacy transliterated EN i18n fields and optionally rerun extraction."""
    asyncio.run(
        _repair_i18n_command(
            school_name=school,
            school_id=school_id,
            city=city,
            country=country,
            limit=limit,
            dry_run=dry_run,
            reextract=not no_reextract,
        )
    )


@cli.command("repair-websites")
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to inspect")
@click.option("--include-state", is_flag=True, help="Include state schools")
@click.option("--dry-run", is_flag=True, help="Preview alias/validation candidates without writing data")
@click.option("--no-recover-failed", is_flag=True, help="Skip recovery attempts for failed_validate schools")
@click.option("--no-extract", is_flag=True, help="Skip extraction after successful revalidation")
def repair_websites(school, school_id, city, country, limit, include_state, dry_run, no_recover_failed, no_extract):
    """Promote safe website aliases, revalidate ownership, and refresh extraction."""
    asyncio.run(
        _repair_websites_command(
            school_name=school,
            school_id=school_id,
            city=city,
            country=country,
            limit=limit,
            include_state=include_state,
            dry_run=dry_run,
            recover_failed=not no_recover_failed,
            run_extract=not no_extract,
        )
    )


@cli.command("cleanup-display-names")
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to inspect")
@click.option("--dry-run", is_flag=True, help="Preview affected schools without writing data")
def cleanup_display_names(school, school_id, city, country, limit, dry_run):
    """Clean obviously bad stored display names without changing legal names."""
    asyncio.run(
        _cleanup_display_names_command(
            school_name=school,
            school_id=school_id,
            city=city,
            country=country,
            limit=limit,
            dry_run=dry_run,
        )
    )


@cli.command("audit-display-names")
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to inspect")
@click.option("--top", type=int, default=20, show_default=True, help="Number of findings to show")
def audit_display_names(school, school_id, city, country, limit, top):
    """Audit likely display-name mismatches using scraped page evidence."""
    asyncio.run(
        _audit_display_names_command(
            school_name=school,
            school_id=school_id,
            city=city,
            country=country,
            limit=limit,
            top=top,
        )
    )


@cli.command("repair-locations")
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to inspect")
@click.option("--include-state", is_flag=True, help="Include state schools")
@click.option("--dry-run", is_flag=True, help="Preview affected schools without writing data")
@click.option("--no-extract", is_flag=True, help="Only rerun navigate, skip extract")
def repair_locations(school, school_id, city, country, limit, include_state, dry_run, no_extract):
    """Refresh likely-bad location rows via targeted navigate+extract reruns."""
    asyncio.run(
        _repair_locations_command(
            school_name=school,
            school_id=school_id,
            city=city,
            country=country,
            limit=limit,
            include_state=include_state,
            dry_run=dry_run,
            run_extract=not no_extract,
        )
    )


@cli.command()
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option(
    "--stage",
    type=click.Choice(
        [
            "discover",
            "discover-websites",
            "recover-failed-urls",
            "validate-urls",
            "navigate",
            "extract",
            "validate-data",
            "summarize",
            "nvo",
            "all",
        ],
        case_sensitive=False,
    ),
    required=True,
    help="Pipeline stage to run",
)
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to process")
@click.option("--year", type=int, help="Specific NVO exam year to import")
@click.option(
    "--history-years",
    type=int,
    default=5,
    show_default=True,
    help="Number of recent NVO years to import when --year is omitted",
)
@click.option(
    "--exam-type",
    "exam_types",
    multiple=True,
    help="NVO exam type to import (repeatable: nvo_4, nvo_7, nvo_10)",
)
@click.option("--sample-ratio", type=float, default=0.0, help="Sample ratio for unchanged schools (discover only)")
@click.option("--include-navigated", is_flag=True, help="For navigate stage, recrawl already navigated schools")
@click.option("--include-extracted", is_flag=True, help="For extract stage, re-extract already extracted schools")
@click.option(
    "--force-validate",
    is_flag=True,
    help="For validate-data stage, include schools that already have a current validation report",
)
@click.option("--sync", is_flag=True, help="Run synchronously (no Celery)")
@click.option("--dry-run", is_flag=True, help="Show what would happen without executing")
def run(
    school,
    school_id,
    stage,
    city,
    country,
    limit,
    year,
    history_years,
    exam_types,
    sample_ratio,
    include_navigated,
    include_extracted,
    force_validate,
    sync,
    dry_run,
):
    """Run a pipeline stage."""
    if dry_run:
        console.print(f"[yellow]DRY RUN - would execute:[/yellow]")
        console.print(f"  Stage: {stage}")
        console.print(f"  School: {school or school_id or 'batch'}")
        console.print(f"  City: {city}")
        console.print(f"  Country: {country}")
        console.print(f"  Limit: {limit}")
        console.print(f"  Year: {year}")
        console.print(f"  History years: {history_years}")
        console.print(f"  Exam types: {builtins.list(exam_types) or 'default'}")
        console.print(f"  Sample ratio: {sample_ratio}")
        console.print(f"  Include navigated: {include_navigated}")
        console.print(f"  Include extracted: {include_extracted}")
        console.print(f"  Force validate: {force_validate}")
        console.print(f"  Mode: {'sync' if sync else 'celery'}")
        return

    if sync:
        # Run synchronously
        asyncio.run(
            _run_sync(
                school,
                school_id,
                stage,
                city,
                country,
                limit,
                year,
                history_years,
                builtins.list(exam_types),
                sample_ratio,
                include_navigated,
                include_extracted,
                force_validate,
            )
        )
    else:
        # Run via Celery
        console.print("[yellow]Celery mode not yet implemented. Use --sync for now.[/yellow]")
        sys.exit(1)


async def _run_sync(
    school_name,
    school_id,
    stage,
    city,
    country,
    limit,
    year,
    history_years,
    exam_types: list[str],
    sample_ratio,
    include_navigated: bool,
    include_extracted: bool,
    force_validate: bool,
):
    """Run pipeline stage synchronously."""
    from app.database import async_session_maker

    async with async_session_maker() as db:
        if school_name or school_id:
            # Single school mode
            if school_name:
                school_id = await _find_school_by_name(db, school_name, country)
                if not school_id:
                    console.print(f"[red]School not found: {school_name}[/red]")
                    return

            console.print(f"[cyan]Running stage '{stage}' for school ID {school_id}[/cyan]")

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task(f"Processing school {school_id}...", total=None)

                try:
                    if stage == "discover":
                        console.print("[yellow]Discover stage runs in batch mode only[/yellow]")
                    elif stage == "discover-websites":
                        await _run_discover_website(db, school_id, country)
                    elif stage == "recover-failed-urls":
                        await _run_recover_failed_school(db, school_id, country)
                    elif stage == "validate-urls":
                        await _run_validate_url(db, school_id, country)
                    elif stage == "navigate":
                        await _run_navigate_school(db, school_id, country)
                    elif stage == "extract":
                        await _run_extract_school(db, school_id, country)
                    elif stage == "validate-data":
                        await _run_validate_data_school(db, school_id, country, run_spot_check=True)
                    elif stage == "summarize":
                        await _run_summarize_school(db, school_id, country)
                    elif stage == "nvo":
                        await _run_nvo_import(
                            db,
                            country=country,
                            city=city,
                            year=year,
                            history_years=history_years,
                            exam_types=exam_types,
                            school_ids=[school_id],
                        )
                    elif stage == "all":
                        await _run_all_stages(db, school_id, country)

                    progress.update(task, completed=True)
                    console.print(f"[green]✓ Completed stage '{stage}' for school {school_id}[/green]")

                except Exception as e:
                    console.print(f"[red]✗ Error: {str(e)}[/red]")
                    logger.exception("Stage execution failed")

        else:
            # Batch mode
            console.print(f"[cyan]Running stage '{stage}' in batch mode[/cyan]")
            console.print(f"  City: {city}")
            console.print(f"  Limit: {limit or 'all'}")

            if stage == "discover":
                await _run_discover_batch(db, country, city, limit, sample_ratio)
            elif stage == "discover-websites":
                await _run_discover_websites_batch(db, country, city, limit)
            elif stage == "recover-failed-urls":
                await _run_recover_failed_urls_batch(db, country, city, limit)
            elif stage == "validate-urls":
                await _run_validate_urls_batch(db, country, city, limit)
            elif stage == "navigate":
                await _run_navigate_batch(db, country, city, limit, include_navigated=include_navigated)
            elif stage == "extract":
                await _run_extract_batch(db, country, city, limit, include_extracted=include_extracted)
            elif stage == "validate-data":
                await _run_validate_data_batch(db, country, city, limit, force_validate=force_validate)
            elif stage == "summarize":
                await _run_summarize_batch(db, country, city, limit)
            elif stage == "nvo":
                await _run_nvo_import(
                    db,
                    country=country,
                    city=city,
                    year=year,
                    history_years=history_years,
                    exam_types=exam_types,
                    school_ids=None,
                )
            elif stage == "all":
                await _run_validate_urls_batch(db, country, city, limit)
                await _run_navigate_batch(db, country, city, limit, include_navigated=include_navigated)
                await _run_extract_batch(db, country, city, limit, include_extracted=include_extracted)
                await _run_validate_data_batch(db, country, city, limit, force_validate=force_validate)
                await _run_summarize_batch(db, country, city, limit)
            else:
                console.print(f"[yellow]Batch mode for '{stage}' not yet implemented[/yellow]")


async def _run_nvo_import(
    db,
    *,
    country: str,
    city: Optional[str],
    year: Optional[int],
    history_years: int,
    exam_types: Optional[list[str]],
    school_ids: Optional[list[int]],
):
    """Run official NVO import."""
    from app.scrapers.nvo_results import import_nvo_results

    console.print("[cyan]Importing official NVO results...[/cyan]")
    console.print(f"  Country: {country}")
    console.print(f"  City: {city or 'all'}")
    console.print(f"  Year: {year or 'latest available'}")
    console.print(f"  History years: {history_years}")
    console.print(f"  Exam types: {exam_types or 'all supported'}")
    if school_ids:
        console.print(f"  School IDs: {school_ids}")

    summary = await import_nvo_results(
        db=db,
        country_code=country,
        city=city,
        year=year,
        history_years=history_years,
        exam_types=exam_types or None,
        school_ids=school_ids,
    )

    console.print("[green]✓ NVO import complete:[/green]")
    console.print(f"  Years imported: {summary.get('years_imported') or []}")
    console.print(f"  Matched schools: {summary.get('matched_schools', 0)}")
    console.print(f"  Created rows: {summary.get('created_rows', 0)}")
    console.print(f"  Updated rows: {summary.get('updated_rows', 0)}")
    console.print(f"  Skipped rows: {summary.get('skipped_rows', 0)}")
    console.print(f"  Unmatched rows: {summary.get('unmatched_rows', 0)}")
    console.print(f"  Source URL: {summary.get('source_url')}")
    if summary.get("slice_failures"):
        console.print(f"[yellow]  Slice failures: {len(summary['slice_failures'])}[/yellow]")
    return summary


async def _find_school_by_name(db, name: str, country: str) -> Optional[int]:
    """Find school by fuzzy name match."""
    from app.models import School
    from sqlalchemy import select, cast, String, or_, func

    # Try exact match first
    result = await db.execute(
        select(School.id, School.name_i18n).where(
            School.country_code == country,
            or_(
                cast(School.name_i18n["bg"], String).ilike(f"%{name}%"),
                cast(School.name_i18n["en"], String).ilike(f"%{name}%"),
            ),
        )
    )

    schools = result.all()

    if not schools:
        return None

    if len(schools) == 1:
        school_id, name_i18n = schools[0]
        console.print(f"[green]Found school: {name_i18n.get('bg', name_i18n.get('en'))}[/green]")
        return school_id

    # Multiple matches - show options
    console.print(f"[yellow]Found {len(schools)} schools matching '{name}':[/yellow]")
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim")
    table.add_column("Name")

    for school_id, name_i18n in schools[:10]:  # Show max 10
        table.add_row(str(school_id), name_i18n.get("bg", name_i18n.get("en", "N/A")))

    console.print(table)
    console.print("[yellow]Use --school-id to specify which school[/yellow]")
    return None


def _school_label(school) -> str:
    name_i18n = school.name_i18n or {}
    return name_i18n.get("bg") or name_i18n.get("en") or f"School {school.id}"


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


def _primary_location_for_school(school):
    locations = builtins.list(school.locations or [])
    if not locations:
        return None
    return sorted(locations, key=lambda loc: (not bool(loc.is_primary), loc.id or 0))[0]


def _location_has_tag(location, tag: str) -> bool:
    return any(str(value) == tag for value in builtins.list(location.location_tags or []))


def _location_repair_reasons_for_school(school) -> list[str]:
    if not school.website_url:
        return []

    location = _primary_location_for_school(school)
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


async def _repair_locations_command(
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
            school_id = await _find_school_by_name(db, school_name, country)
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
            location = _primary_location_for_school(school)
            address_bg = dict(location.address_i18n or {}).get("bg") or dict(location.address_i18n or {}).get("en") or ""
            candidate_rows.append((school.id, _school_label(school), ", ".join(reasons), address_bg))
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
        navigate_results = await _run_navigate_batch(
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
        await _run_extract_batch(
            db=db,
            country=country,
            city=city or "",
            limit=None,
            include_extracted=True,
            school_ids=extract_school_ids,
        )


async def _repair_i18n_command(
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
            school_id = await _find_school_by_name(db, school_name, country)
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
            preview_rows.append((school.id, _school_label(school), has_name_issue, len(affected_locations)))

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
        await _run_extract_batch(
            db=db,
            country=country,
            city=city or "",
            limit=None,
            include_extracted=True,
            school_ids=eligible_ids,
        )


def _contains_cyrillic(text: str | None) -> bool:
    return bool(text and re.search(r"[А-Яа-я]", text))


async def _audit_display_names_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    top: int,
):
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School, SourcePage
    from app.scrapers.display_name_audit import audit_school_display_name

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await _find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        school_query = select(School).where(
            School.country_code == country,
            School.website_url.isnot(None),
        )
        if city:
            school_query = school_query.where(School.city == city)
        if school_id:
            school_query = school_query.where(School.id == school_id)
        if limit:
            school_query = school_query.limit(limit)

        schools = (await db.execute(school_query)).scalars().all()
        if not schools:
            console.print("[yellow]No schools matched the audit filters.[/yellow]")
            return

        school_ids = [school.id for school in schools]
        page_query = select(SourcePage).where(
            SourcePage.school_id.in_(school_ids),
            SourcePage.raw_markdown.isnot(None),
        )
        pages = (await db.execute(page_query)).scalars().all()
        pages_by_school: dict[int, list[dict[str, object]]] = defaultdict(list)
        for page in pages:
            pages_by_school[int(page.school_id)].append(
                {
                    "source_url": page.source_url,
                    "page_category": page.page_category,
                    "raw_markdown": page.raw_markdown,
                }
            )

        findings = []
        for school in schools:
            finding = audit_school_display_name(
                {
                    "id": school.id,
                    "name_i18n": school.name_i18n or {},
                    "attributes": school.attributes or {},
                    "website_url": school.website_url,
                },
                pages_by_school.get(school.id, []),
            )
            if finding is not None:
                findings.append(finding)

        findings.sort(key=lambda item: (-item.score, -item.repeated_pages, item.school_id))

        console.print(f"[cyan]Audited {len(schools)} schools with website content[/cyan]")
        console.print(f"  Findings: {len(findings)}")
        if not findings:
            console.print("[green]No likely display-name mismatches found with the current heuristics.[/green]")
            return

        table = Table(title="Likely Display Name Mismatches")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("School")
        table.add_column("Stored")
        table.add_column("Candidate")
        table.add_column("Score", justify="right")
        table.add_column("Pages", justify="right")
        for finding in findings[: max(1, top)]:
            table.add_row(
                str(finding.school_id),
                finding.legal_name or f"School {finding.school_id}",
                finding.stored_display_name or "-",
                finding.candidate_name,
                str(finding.score),
                str(finding.repeated_pages),
            )
        console.print(table)

        for finding in findings[: min(len(findings), max(1, top), 10)]:
            console.print(
                f"[dim]{finding.school_id}: candidate='{finding.candidate_name}' "
                f"urls={', '.join(finding.evidence_urls[:3])}[/dim]"
            )


def _clean_display_name_i18n_for_school(school) -> tuple[dict[str, str] | None, bool, str | None]:
    from app.scrapers.extractor_helpers import _is_display_name_en_transliteration, _is_low_quality_display_name
    from app.utils.i18n_resolver import resolve_name_i18n

    attrs = dict(school.attributes or {})
    raw_display = attrs.get("display_name_i18n")
    if not isinstance(raw_display, dict):
        return None, False, None

    display = {
        str(lang): " ".join(str(text or "").split()).strip()
        for lang, text in raw_display.items()
        if str(lang).strip() and str(text).strip()
    }
    if not display:
        return None, False, "clear-empty"

    if any(_is_low_quality_display_name(value) for value in display.values()):
        return None, True, "clear-junk"

    bg_value = display.get("bg")
    en_value = display.get("en")
    if _is_display_name_en_transliteration(bg_value, en_value):
        display.pop("en", None)
        return (display or None), True, "drop-transliterated-en"
    if en_value and bg_value and en_value == bg_value and _contains_cyrillic(en_value):
        display.pop("en", None)
        return (display or None), True, "drop-cyrillic-en"

    if en_value and _contains_cyrillic(en_value):
        resolved_en = resolve_name_i18n(school.name_i18n, {"display_name_i18n": display}).get("en")
        if resolved_en and resolved_en != en_value:
            display.pop("en", None)
            return (display or None), True, "drop-worse-en"

    return display, False, None


async def _cleanup_display_names_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    dry_run: bool,
):
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await _find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        query = select(School).where(School.country_code == country)
        if city:
            query = query.where(School.city == city)
        if school_id:
            query = query.where(School.id == school_id)
        if limit:
            query = query.limit(limit)

        schools = (await db.execute(query)).scalars().all()

        preview_rows: list[tuple[int, str, str]] = []
        cleaned_school_ids: list[int] = []
        reason_counts = {
            "clear-junk": 0,
            "drop-cyrillic-en": 0,
            "drop-transliterated-en": 0,
            "drop-worse-en": 0,
            "clear-empty": 0,
        }

        for school in schools:
            cleaned_display, changed, reason = _clean_display_name_i18n_for_school(school)
            if not changed:
                continue
            preview_rows.append((school.id, _school_label(school), reason or "updated"))
            cleaned_school_ids.append(school.id)
            if reason:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            if dry_run:
                continue

            attrs = dict(school.attributes or {})
            if cleaned_display:
                attrs["display_name_i18n"] = cleaned_display
            else:
                attrs.pop("display_name_i18n", None)
            school.attributes = attrs

        if not preview_rows:
            console.print("[yellow]No display-name cleanup candidates found.[/yellow]")
            return

        console.print(f"[cyan]Found {len(preview_rows)} display-name cleanup candidates[/cyan]")
        console.print(f"  Junk display names: {reason_counts.get('clear-junk', 0)}")
        console.print(f"  Dropped duplicated Cyrillic EN: {reason_counts.get('drop-cyrillic-en', 0)}")
        console.print(f"  Dropped transliterated EN: {reason_counts.get('drop-transliterated-en', 0)}")
        console.print(f"  Dropped worse EN variants: {reason_counts.get('drop-worse-en', 0)}")
        console.print(f"  Cleared empty display maps: {reason_counts.get('clear-empty', 0)}")
        console.print(f"  Mode: {'dry-run' if dry_run else 'commit'}")

        preview_table = Table(title="Display Name Cleanup")
        preview_table.add_column("ID", style="cyan", no_wrap=True)
        preview_table.add_column("School")
        preview_table.add_column("Action")
        for row in preview_rows[:20]:
            preview_table.add_row(str(row[0]), row[1], row[2])
        console.print(preview_table)
        if len(preview_rows) > 20:
            console.print(f"[dim]Showing first 20 of {len(preview_rows)} cleanup candidates[/dim]")

        if dry_run:
            return

        await db.commit()
        console.print(f"[green]✓ Cleaned stored display names for {len(cleaned_school_ids)} schools[/green]")


def _normalize_brand_alias_host_text(text: str) -> str:
    return re.sub(r"[^a-z0-9а-я]+", "", (text or "").lower())


def _brand_alias_token_variants(token: str) -> set[str]:
    from app.utils.transliteration import transliterate_bulgarian

    lowered = (token or "").lower()
    variants = {lowered, transliterate_bulgarian(lowered).lower()}
    if lowered.startswith("в"):
        variants.add("w" + transliterate_bulgarian(lowered[1:]).lower())
    if lowered.startswith("w"):
        variants.add("v" + lowered[1:])
    return {value for value in variants if value}


def _select_brand_aliases_for_school(candidate: dict[str, str] | None, website_url: str, school) -> list[str]:
    from app.scrapers.school_tokens import extract_school_name_tokens

    if not candidate:
        return []
    if school.school_type not in {"private", "international"}:
        return []

    host = urlparse(website_url or "").netloc.lower().replace("www.", "")
    host_compact = _normalize_brand_alias_host_text(host)
    aliases: list[str] = []
    seen: set[str] = set()

    for value in candidate.values():
        text = " ".join((value or "").split()).strip()
        lowered = text.lower()
        if not text or lowered in _BRAND_ALIAS_GENERIC_EXACT:
            continue
        if any(bad in lowered for bad in _BRAND_ALIAS_BANNED_SUBSTRINGS):
            continue
        if any(marker in lowered for marker in _BRAND_ALIAS_BANNED_MARKERS):
            continue
        if len(text.split()) > 7:
            continue
        if school.education_level == "kindergarten" and lowered.startswith(
            ("частно средно училище", "частно основно училище", "чоу ", "чсу ")
        ):
            continue

        tokens = [token for token in extract_school_name_tokens(text, limit=8) if len(token) >= 3]
        if not tokens:
            continue

        aligned = False
        for token in tokens:
            for variant in _brand_alias_token_variants(token):
                if len(variant) >= 3 and variant in host_compact:
                    aligned = True
                    break
            if aligned:
                break
        if not aligned:
            continue

        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        aliases.append(text)

    return aliases


async def _repair_websites_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    include_state: bool,
    dry_run: bool,
    recover_failed: bool,
    run_extract: bool,
):
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School, SourcePage
    from app.models.scrape_log import ScrapeType
    from app.scrapers.extractor_helpers import _extract_display_name_i18n_deterministic
    from app.scrapers.url_validator import extract_validation_aliases, validate_school_url

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await _find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        query = select(School).where(School.country_code == country)
        if city:
            query = query.where(School.city == city)
        if school_id:
            query = query.where(School.id == school_id)
        if not include_state:
            query = query.where(School.school_type.in_(["private", "international"]))
        if limit:
            query = query.limit(limit)

        schools = (await db.execute(query)).scalars().all()
        if not schools:
            console.print("[yellow]No schools matched the repair-websites filters.[/yellow]")
            return

        failed_validate_ids = [school.id for school in schools if school.scrape_status == "failed_validate"]
        if recover_failed and failed_validate_ids and not dry_run:
            console.print(f"[cyan]Recovering {len(failed_validate_ids)} failed website mappings...[/cyan]")
            for target_school_id in failed_validate_ids:
                try:
                    await _run_recover_failed_school(db, target_school_id, country)
                except Exception as exc:
                    logger.error("Failed website recovery for school %s: %s", target_school_id, exc)

            schools = (await db.execute(query)).scalars().all()

        schools_by_id = {school.id: school for school in schools}
        website_school_ids = [school.id for school in schools if school.website_url]
        if not website_school_ids:
            console.print("[yellow]No schools with website URLs matched the repair-websites filters.[/yellow]")
            return

        pages = (
            await db.execute(
                select(SourcePage)
                .where(
                    SourcePage.school_id.in_(website_school_ids),
                    SourcePage.scrape_type == ScrapeType.WEBSITE,
                    SourcePage.is_valid != False,
                    SourcePage.raw_markdown.isnot(None),
                )
                .order_by(SourcePage.school_id, SourcePage.id)
            )
        ).scalars().all()

        pages_by_school: dict[int, list] = defaultdict(builtins.list)
        for page in pages:
            pages_by_school[page.school_id].append(page)

        alias_updates: dict[int, list[str]] = {}
        preview_rows: list[tuple[int, str, list[str]]] = []
        for target_school_id in website_school_ids:
            school = schools_by_id[target_school_id]
            school_pages = pages_by_school.get(target_school_id)
            if not school_pages:
                continue

            candidate = _extract_display_name_i18n_deterministic(
                text="\n\n".join((page.raw_markdown or "") for page in school_pages[:4]),
                registry_name=(school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en"),
                country_code=school.country_code,
            )
            aliases = _select_brand_aliases_for_school(candidate, school.website_url, school)
            if not aliases:
                continue

            existing_aliases = extract_validation_aliases(school.attributes)
            merged_aliases: list[str] = []
            seen: set[str] = set()
            for value in existing_aliases + aliases:
                key = value.casefold()
                if key in seen:
                    continue
                seen.add(key)
                merged_aliases.append(value)
            if merged_aliases == existing_aliases:
                continue

            alias_updates[target_school_id] = merged_aliases
            preview_rows.append((target_school_id, _school_label(school), aliases))

        console.print(f"[cyan]Website repair candidates: {len(preview_rows)}[/cyan]")
        console.print(f"  Failed websites to recover: {len(failed_validate_ids)}")
        console.print(f"  Alias promotions: {len(alias_updates)}")
        console.print(f"  Re-extract after validate: {run_extract and not dry_run}")

        if preview_rows:
            preview_table = Table(title="Alias Promotions")
            preview_table.add_column("ID", style="cyan", no_wrap=True)
            preview_table.add_column("School")
            preview_table.add_column("New aliases")
            for row in preview_rows[:20]:
                preview_table.add_row(str(row[0]), row[1], ", ".join(row[2]))
            console.print(preview_table)
            if len(preview_rows) > 20:
                console.print(f"[dim]Showing first 20 of {len(preview_rows)} alias promotions[/dim]")

        if dry_run:
            return

        for target_school_id, aliases in alias_updates.items():
            school = schools_by_id[target_school_id]
            attrs = dict(school.attributes or {})
            attrs["name_aliases"] = aliases
            school.attributes = attrs
        await db.commit()

        validate_target_ids = sorted(
            {
                school.id
                for school in schools
                if school.website_url and (school.id in alias_updates or school.id in failed_validate_ids)
            }
        )
        if not validate_target_ids:
            console.print("[yellow]No schools needed revalidation after alias promotion.[/yellow]")
            return

        validated_ids: list[int] = []
        invalid_rows: list[tuple[int, str, str]] = []
        for target_school_id in validate_target_ids:
            school = schools_by_id[target_school_id]
            school_name_value = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
            aliases = dict(school.attributes or {}).get("name_aliases", [])
            result, _, reason = await validate_school_url(
                school_id=target_school_id,
                url=school.website_url,
                country_code=school.country_code,
                update_db=True,
                school_name=school_name_value,
                school_aliases=aliases,
            )
            if result.value == "valid":
                validated_ids.append(target_school_id)
            else:
                invalid_rows.append((target_school_id, _school_label(school), reason or result.value))

        console.print("[green]✓ Validation complete:[/green]")
        console.print(f"  Validated: {len(validated_ids)}")
        console.print(f"  Not validated: {len(invalid_rows)}")
        for row in invalid_rows[:10]:
            console.print(f"  [yellow]{row[0]} {row[1]}[/yellow]: {row[2]}")

        if run_extract and validated_ids:
            console.print(f"[cyan]Refreshing extraction for {len(validated_ids)} schools...[/cyan]")
            await _run_extract_batch(
                db=db,
                country=country,
                city=city or "",
                limit=None,
                include_extracted=True,
                school_ids=validated_ids,
            )


async def _run_discover_batch(db, country: str, city: str, limit: Optional[int], sample_ratio: float):
    """Run discovery stage in batch mode."""
    from app.scrapers.sources import get_adapters_for_country

    adapters = get_adapters_for_country(country, city)

    if not adapters:
        console.print(f"[red]No adapters found for country={country}, city={city}[/red]")
        return

    console.print(f"[cyan]Found {len(adapters)} adapter(s)[/cyan]")

    for adapter_class in adapters:
        adapter = adapter_class(db=db)
        console.print(f"\n[cyan]Running {adapter.ADAPTER_NAME}...[/cyan]")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task(f"Discovering schools...", total=None)

            try:
                result = await adapter.run(limit=limit, sample_ratio=sample_ratio)
                progress.update(task, completed=True)

                console.print(f"[green]✓ Discovery complete:[/green]")
                console.print(f"  Created: {result['created']}")
                console.print(f"  Updated: {result['updated']}")
                console.print(f"  Skipped: {result['skipped']}")

            except Exception as e:
                console.print(f"[red]✗ Error: {str(e)}[/red]")
                logger.exception("Discovery failed")


async def _run_validate_urls_batch(
    db,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    statuses: Optional[list[str]] = None,
    school_ids: Optional[list[int]] = None,
):
    """Run URL validation stage in batch mode."""
    from app.config import get_settings
    from app.models import School
    from sqlalchemy import select
    from app.scrapers.url_validator import extract_validation_aliases, validate_school_url

    statuses = statuses or ["pending", "failed_validate"]

    query = select(School.id, School.website_url, School.name_i18n, School.attributes).where(
        School.country_code == country,
        School.scrape_status.in_(statuses),
        School.website_url.isnot(None),
    )

    if city:
        query = query.where(School.city == city)

    if school_ids:
        query = query.where(School.id.in_(school_ids))

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.all()

    if not schools:
        console.print("[yellow]No schools to validate[/yellow]")
        return

    settings = get_settings()
    requested_concurrency = max(1, int(settings.url_validation_concurrency))
    max_concurrency = max(1, int(settings.url_validation_max_concurrency))
    concurrency = min(requested_concurrency, max_concurrency)

    console.print(f"[cyan]Validating {len(schools)} school URLs...[/cyan]")
    console.print(f"  Concurrency: {concurrency}")
    if requested_concurrency > max_concurrency:
        console.print(
            f"[yellow]  Requested concurrency {requested_concurrency} capped to {max_concurrency} "
            f"(DB session safety)[/yellow]"
        )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Validating URLs...", total=len(schools))

        valid_count = 0
        invalid_count = 0
        ambiguous_count = 0
        error_count = 0
        semaphore = asyncio.Semaphore(concurrency)

        async def _validate_single(
            school_id: int,
            website_url: str,
            school_name: Optional[str],
            school_aliases: list[str],
        ) -> str:
            async with semaphore:
                try:
                    result, _, _ = await validate_school_url(
                        school_id=school_id,
                        url=website_url,
                        country_code=country,
                        update_db=True,
                        school_name=school_name,
                        school_aliases=school_aliases,
                    )
                    return result.value
                except Exception as exc:
                    logger.error(f"Error validating school {school_id}: {exc}")
                    return "error"

        tasks = [
            asyncio.create_task(
                _validate_single(
                    school_id=school_id,
                    website_url=website_url,
                    school_name=(name_i18n or {}).get("bg") or (name_i18n or {}).get("en"),
                    school_aliases=extract_validation_aliases(attributes),
                )
            )
            for school_id, website_url, name_i18n, attributes in schools
            if website_url
        ]

        for completed in asyncio.as_completed(tasks):
            outcome = await completed
            if outcome == "valid":
                valid_count += 1
            elif outcome == "invalid":
                invalid_count += 1
            elif outcome == "ambiguous":
                ambiguous_count += 1
            else:
                error_count += 1
            progress.update(task, advance=1)

    console.print(f"[green]✓ URL validation complete:[/green]")
    console.print(f"  Valid: {valid_count}")
    console.print(f"  Invalid: {invalid_count}")
    console.print(f"  Ambiguous: {ambiguous_count}")
    console.print(f"  Errors: {error_count}")


async def _run_recover_failed_school(db, school_id: int, country: str) -> dict:
    """Rediscover + revalidate URL for one failed school, trying multiple candidates."""
    from app.config import get_settings
    from app.models import School
    from sqlalchemy import select
    from app.scrapers.website_discovery import WebsiteDiscoverer

    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()
    if not school:
        raise ValueError(f"School {school_id} not found")

    if school.scrape_status != "failed_validate":
        console.print(
            f"[yellow]School {school_id} is '{school.scrape_status}' (expected failed_validate); skipping[/yellow]"
        )
        return {"school_id": school_id, "skipped": True, "reason": "not_failed_validate"}

    settings = get_settings()
    max_attempts = max(1, int(settings.url_recovery_candidate_attempts))
    discoverer = WebsiteDiscoverer(country_code=country)
    return await discoverer.recover_failed_school(
        db=db,
        school=school,
        max_attempts=max_attempts,
    )


async def _recover_failed_school_with_new_session(school_id: int, country: str) -> dict:
    """Run recovery for one school in an isolated DB session."""
    from app.database import async_session_maker

    async with async_session_maker() as db:
        return await _run_recover_failed_school(db, school_id, country)


async def _run_recover_failed_urls_batch(db, country: str, city: str, limit: Optional[int]):
    """Retry website discovery for failed URLs with multi-candidate validation fallback."""
    from app.config import get_settings
    from app.models import School
    from sqlalchemy import select

    query = select(School.id).where(
        School.country_code == country,
        School.scrape_status == "failed_validate",
    )

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    school_ids = [row[0] for row in result.all()]

    if not school_ids:
        console.print("[yellow]No failed_validate schools to recover[/yellow]")
        return

    console.print(f"[cyan]Recovering {len(school_ids)} failed school URLs...[/cyan]")

    settings = get_settings()
    requested_concurrency = max(1, int(settings.url_recovery_concurrency))
    max_concurrency = max(1, int(settings.url_validation_max_concurrency))
    concurrency = min(requested_concurrency, max_concurrency)
    console.print(f"  Concurrency: {concurrency}")
    if requested_concurrency > max_concurrency:
        console.print(
            f"[yellow]  Requested concurrency {requested_concurrency} capped to {max_concurrency} "
            f"(DB session safety)[/yellow]"
        )

    validated_count = 0
    still_failed_count = 0
    terminal_count = 0
    skipped_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Recovering failed URLs...", total=len(school_ids))

        semaphore = asyncio.Semaphore(concurrency)

        async def _recover_single(target_school_id: int) -> dict:
            async with semaphore:
                return await _recover_failed_school_with_new_session(target_school_id, country)

        tasks = [asyncio.create_task(_recover_single(school_id)) for school_id in school_ids]
        for completed in asyncio.as_completed(tasks):
            try:
                out = await completed
                if out.get("terminal"):
                    terminal_count += 1
                elif out.get("skipped"):
                    skipped_count += 1
                elif out.get("status") == "validated":
                    validated_count += 1
                else:
                    still_failed_count += 1
            except Exception as exc:
                logger.error(f"Failed to recover school URL: {exc}")
            progress.update(task, advance=1)

    console.print("[green]✓ Failed URL recovery pass complete:[/green]")
    console.print(f"  Recovered + validated: {validated_count}")
    console.print(f"  Still failed: {still_failed_count}")
    console.print(f"  No official website: {terminal_count}")
    console.print(f"  Skipped: {skipped_count}")


async def _run_discover_website(db, school_id: int, country: str):
    """Run website discovery for a single school."""
    from app.scrapers.website_discovery import discover_school_website

    console.print(f"  Discovering website for school {school_id}...")
    result = await discover_school_website(db=db, school_id=school_id, country_code=country)

    if result.get("found"):
        console.print(
            f"  Result: {result.get('website_url')} "
            f"(method={result.get('method')}, updated={result.get('updated')})"
        )
    else:
        console.print(f"[yellow]  Not found: {result.get('reason', 'No reason')}[/yellow]")
    return result


async def _run_discover_websites_batch(db, country: str, city: str, limit: Optional[int]):
    """Run website discovery stage in batch mode."""
    from app.models import School
    from sqlalchemy import and_, or_, select

    query = select(School).where(
        School.country_code == country,
        or_(
            School.scrape_status.in_(["pending", "failed_validate"]),
            and_(School.scrape_status == "extraction_failed", School.website_url.is_(None)),
        ),
    )

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()

    if not schools:
        console.print("[yellow]No schools to discover websites for[/yellow]")
        return

    console.print(f"[cyan]Discovering websites for {len(schools)} schools...[/cyan]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Discovering websites...", total=len(schools))

        found_count = 0
        updated_count = 0

        for school in schools:
            try:
                result = await _run_discover_website(db, school.id, country)
                if result and result.get("found"):
                    found_count += 1
                if result and result.get("updated"):
                    updated_count += 1
            except Exception as e:
                logger.error(f"Error discovering website for school {school.id}: {e}")
            progress.update(task, advance=1)

    console.print(f"[green]✓ Website discovery complete:[/green]")
    console.print(f"  Found: {found_count}")
    console.print(f"  Updated: {updated_count}")
    console.print(f"  Unchanged: {len(schools) - updated_count}")


async def _run_validate_url(db, school_id: int, country: str):
    """Run URL validation for a single school."""
    from app.models import School
    from sqlalchemy import select
    from app.scrapers.url_validator import extract_validation_aliases, validate_school_url

    result = await db.execute(select(School).where(School.id == school_id))
    school = result.scalar_one_or_none()

    if not school:
        raise ValueError(f"School {school_id} not found")

    if not school.website_url:
        console.print(f"[yellow]School {school_id} has no website URL[/yellow]")
        return

    console.print(f"  Validating: {school.website_url}")

    validation_result, final_url, reason = await validate_school_url(
        school_id=school_id,
        url=school.website_url,
        country_code=country,
        update_db=True,
        school_name=(school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en"),
        school_aliases=extract_validation_aliases(school.attributes),
    )

    console.print(f"  Result: {validation_result.value} - {reason}")


async def _run_navigate_school(db, school_id: int, country: str):
    """Run navigation stage for a single school."""
    from app.scrapers.navigator import navigate_school

    console.print(f"  Navigating website for school {school_id}...")
    result = await navigate_school(db=db, school_id=school_id, country_code=country)

    if result.get("success"):
        console.print(
            f"  Result: pages={result.get('pages_found', 0)}, "
            f"created={result.get('created', 0)}, updated={result.get('updated', 0)}, "
            f"cache_hits={result.get('cache_hits', 0)}"
        )
    else:
        console.print(f"[yellow]  Skipped: {result.get('reason', 'Unknown reason')}[/yellow]")
    return result


async def _run_navigate_batch(
    db,
    country: str,
    city: str,
    limit: Optional[int],
    include_navigated: bool = False,
    school_ids: Optional[list[int]] = None,
    skip_timed_out_chunks: bool = False,
):
    """Run navigation stage in batch mode."""
    from app.models import School
    from app.scrapers.navigator import navigate_schools_batch
    from sqlalchemy import select

    query = select(School).where(
        School.country_code == country,
        School.website_url.isnot(None),
    )

    explicit_school_ids = builtins.list(school_ids or [])
    if explicit_school_ids:
        query = query.where(School.id.in_(explicit_school_ids))
    else:
        statuses = ["validated", "navigated"] if include_navigated else ["validated"]
        query = query.where(School.scrape_status.in_(statuses))

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()
    school_ids = [school.id for school in schools]

    if not school_ids:
        console.print("[yellow]No schools to navigate[/yellow]")
        return

    if explicit_school_ids:
        status_label = "explicit repair selection"
    else:
        status_label = "validated+navigated" if include_navigated else "validated"
    console.print(f"[cyan]Navigating websites for {len(school_ids)} schools...[/cyan]")
    console.print(f"  Status filter: {status_label}")

    results: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Navigating websites...", total=len(school_ids))

        success_count = 0
        fail_count = 0

        from app.config import get_settings as _get_settings

        settings = _get_settings()
        _nav_timeout = settings.nav_school_timeout_seconds
        nav_batch_concurrency = max(1, int(getattr(settings, "nav_batch_concurrency", 3)))
        console.print(f"  Batch crawl concurrency: {nav_batch_concurrency}")

        if skip_timed_out_chunks and explicit_school_ids:
            timed_out_school_ids: list[int] = []
            for chunk_start in range(0, len(school_ids), nav_batch_concurrency):
                school_chunk = school_ids[chunk_start : chunk_start + nav_batch_concurrency]
                try:
                    coro = navigate_schools_batch(
                        db=db,
                        school_ids=school_chunk,
                        country_code=country,
                        max_concurrency=nav_batch_concurrency,
                    )
                    if _nav_timeout > 0:
                        chunk_timeout = _nav_timeout * max(
                            1, ceil(len(school_chunk) / nav_batch_concurrency)
                        )
                        chunk_results = await asyncio.wait_for(coro, timeout=chunk_timeout)
                    else:
                        chunk_results = await coro
                except asyncio.TimeoutError:
                    logger.error(
                        "Navigation timed out after %.0fs for school chunk %s",
                        _nav_timeout,
                        school_chunk,
                    )
                    await db.rollback()
                    timed_out_school_ids.extend(school_chunk)
                    chunk_results = [
                        {
                            "school_id": timed_out_school_id,
                            "success": False,
                            "reason": "Navigation timeout",
                        }
                        for timed_out_school_id in school_chunk
                    ]
                except Exception as exc:
                    logger.warning(
                        "Batch navigation chunk failed for %s; retrying sequentially: %s",
                        school_chunk,
                        exc,
                    )
                    await db.rollback()
                    chunk_results = []
                    for school_id in school_chunk:
                        try:
                            coro = _run_navigate_school(db, school_id, country)
                            if _nav_timeout > 0:
                                result = await asyncio.wait_for(coro, timeout=_nav_timeout)
                            else:
                                result = await coro
                        except asyncio.TimeoutError:
                            logger.error(
                                "Navigation timed out after %.0fs for school %s", _nav_timeout, school_id
                            )
                            await db.rollback()
                            result = {
                                "school_id": school_id,
                                "success": False,
                                "reason": "Navigation timeout",
                            }
                        except Exception as e:
                            logger.error(f"Error navigating school {school_id}: {e}")
                            await db.rollback()
                            result = {"school_id": school_id, "success": False, "reason": str(e)}
                        chunk_results.append(result)

                results.extend(chunk_results)
                for result in chunk_results:
                    if result and result.get("success"):
                        success_count += 1
                    else:
                        fail_count += 1
                    progress.update(task, advance=1)

            if timed_out_school_ids:
                console.print(
                    f"[yellow]  Timed out and skipped: {len(timed_out_school_ids)} schools[/yellow]"
                )
        else:
            try:
                results = await navigate_schools_batch(
                    db=db,
                    school_ids=school_ids,
                    country_code=country,
                    max_concurrency=nav_batch_concurrency,
                )
            except Exception as exc:
                logger.warning("Batch navigation failed; falling back to sequential mode: %s", exc)
                await db.rollback()
                results = []
                for school_id in school_ids:
                    try:
                        coro = _run_navigate_school(db, school_id, country)
                        if _nav_timeout > 0:
                            result = await asyncio.wait_for(coro, timeout=_nav_timeout)
                        else:
                            result = await coro
                    except asyncio.TimeoutError:
                        logger.error(
                            "Navigation timed out after %.0fs for school %s", _nav_timeout, school_id
                        )
                        await db.rollback()
                        result = {"school_id": school_id, "success": False, "reason": "Navigation timeout"}
                    except Exception as e:
                        logger.error(f"Error navigating school {school_id}: {e}")
                        await db.rollback()
                        result = {"school_id": school_id, "success": False, "reason": str(e)}
                    results.append(result)

            for result in results:
                if result and result.get("success"):
                    success_count += 1
                else:
                    fail_count += 1
                progress.update(task, advance=1)

    console.print(f"[green]✓ Navigation complete:[/green]")
    console.print(f"  Successful: {success_count}")
    console.print(f"  Failed: {fail_count}")
    return results


async def _run_all_stages(db, school_id: int, country: str):
    """Run all pipeline stages for a single school."""
    stages = ["discover-websites", "validate-urls", "navigate", "extract", "validate-data", "summarize"]

    for stage in stages:
        console.print(f"\n[cyan]Stage: {stage}[/cyan]")

        if stage == "discover-websites":
            await _run_discover_website(db, school_id, country)
        elif stage == "validate-urls":
            await _run_validate_url(db, school_id, country)
        elif stage == "navigate":
            await _run_navigate_school(db, school_id, country)
        elif stage == "extract":
            await _run_extract_school(db, school_id, country)
        elif stage == "validate-data":
            await _run_validate_data_school(db, school_id, country, run_spot_check=True)
        elif stage == "summarize":
            await _run_summarize_school(db, school_id, country)
        else:
            console.print(f"[yellow]{stage} not yet implemented[/yellow]")


@cli.command()
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--status", help="Reset all schools with this status")
@click.option(
    "--to",
    "to_status",
    type=click.Choice(
        [
            "pending",
            "failed_validate",
            "validated",
            "navigated",
            "extraction_failed",
            "extracted",
            "summarized",
            "no_official_website",
        ],
        case_sensitive=False,
    ),
    required=True,
    help="Target status",
)
@click.option("--country", default="bg", help="Country code")
def reset(school, school_id, status, to_status, country):
    """Reset school scrape status."""
    asyncio.run(_reset_status(school, school_id, status, to_status, country))


async def _reset_status(school_name, school_id, from_status, to_status, country):
    """Reset school scrape status."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select, update

    async with async_session_maker() as db:
        if school_name or school_id:
            # Single school
            if school_name:
                school_id = await _find_school_by_name(db, school_name, country)
                if not school_id:
                    return

            await db.execute(
                update(School).where(School.id == school_id).values(scrape_status=to_status)
            )
            await db.commit()
            console.print(f"[green]✓ Reset school {school_id} to '{to_status}'[/green]")

        elif from_status:
            # Batch reset
            result = await db.execute(
                update(School)
                .where(School.country_code == country, School.scrape_status == from_status)
                .values(scrape_status=to_status)
            )
            await db.commit()
            console.print(f"[green]✓ Reset {result.rowcount} schools from '{from_status}' to '{to_status}'[/green]")

        else:
            console.print("[red]Must specify --school, --school-id, or --status[/red]")


@cli.command()
@click.option("--city", help="Filter by city")
@click.option("--country", default="bg", help="Country code")
@click.option("--status", help="Filter by scrape status")
@click.option("--limit", type=int, default=20, help="Number of schools to show")
def list(city, country, status, limit):
    """List schools."""
    asyncio.run(_list_schools(city, country, status, limit))


async def _list_schools(city, country, status, limit):
    """List schools with filters."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select

    async with async_session_maker() as db:
        query = select(School).where(School.country_code == country)

        if city:
            query = query.where(School.city == city)

        if status:
            query = query.where(School.scrape_status == status)

        query = query.limit(limit)

        result = await db.execute(query)
        schools = result.scalars().all()

        if not schools:
            console.print("[yellow]No schools found[/yellow]")
            return

        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("ID", style="dim", width=6)
        table.add_column("Name", width=40)
        table.add_column("Type", width=12)
        table.add_column("Status", width=15)
        table.add_column("City", width=10)

        for school in schools:
            name = school.name_i18n.get("bg", school.name_i18n.get("en", "N/A"))
            table.add_row(
                str(school.id),
                name[:38] + "..." if len(name) > 40 else name,
                school.school_type,
                school.scrape_status,
                school.city or "N/A",
            )

        console.print(table)
        console.print(f"\n[dim]Showing {len(schools)} of {len(schools)} schools[/dim]")


@cli.command()
@click.option("--city", help="Filter by city")
@click.option("--country", default="bg", help="Country code")
def stats(city, country):
    """Show pipeline statistics."""
    asyncio.run(_show_stats(city, country))


async def _show_stats(city, country):
    """Show pipeline statistics."""
    from app.database import async_session_maker
    from app.models import School
    from sqlalchemy import select, func

    async with async_session_maker() as db:
        # Total schools
        query = select(func.count(School.id)).where(School.country_code == country)
        if city:
            query = query.where(School.city == city)

        result = await db.execute(query)
        total = result.scalar()

        # By status
        query = (
            select(School.scrape_status, func.count(School.id))
            .where(School.country_code == country)
            .group_by(School.scrape_status)
        )
        if city:
            query = query.where(School.city == city)

        result = await db.execute(query)
        status_counts = dict(result.all())

        # By type
        query = (
            select(School.school_type, func.count(School.id))
            .where(School.country_code == country)
            .group_by(School.school_type)
        )
        if city:
            query = query.where(School.city == city)

        result = await db.execute(query)
        type_counts = dict(result.all())

        # Display
        console.print(f"\n[bold cyan]Pipeline Statistics[/bold cyan]")
        console.print(f"Country: {country}")
        if city:
            console.print(f"City: {city}")
        console.print(f"\n[bold]Total schools: {total}[/bold]\n")

        # Status breakdown
        console.print("[bold]By Status:[/bold]")
        for status, count in sorted(status_counts.items()):
            console.print(f"  {status}: {count}")

        # Type breakdown
        console.print(f"\n[bold]By Type:[/bold]")
        for school_type, count in sorted(type_counts.items()):
            console.print(f"  {school_type}: {count}")



async def _run_validate_data_school(
    db,
    school_id: int,
    country: str,
    run_spot_check: bool = False,
):
    """Run deterministic Stage 6 validation for one school."""
    from app.scrapers.validator import validate_school_data

    console.print(f"  Validating extracted data for school {school_id}...")
    result = await validate_school_data(
        db=db,
        school_id=school_id,
        country_code=country,
        run_spot_check=run_spot_check,
    )

    status = result.get("status")
    if status == "validation_failed":
        console.print(f"[red]  Failed: {result.get('error', 'Unknown error')}[/red]")
        return result

    issue_counts = result.get("issue_counts") or {}
    error_count = int(issue_counts.get("error", 0))
    warning_count = int(issue_counts.get("warning", 0))
    status_color = "yellow" if status == "needs_review" else "green"
    console.print(
        f"[{status_color}]  Validation status: {status} "
        f"(errors={error_count}, warnings={warning_count}, auto_fixes={result.get('auto_fixes', 0)})[/{status_color}]"
    )

    spot = result.get("spot_check") or {}
    if spot:
        if spot.get("status") == "checked":
            kind_counts = spot.get("kind_counts") or {}
            console.print(
                "    [dim]"
                f"Spot-check: discrepancies={spot.get('discrepancies', 0)}, "
                f"has_discrepancy={spot.get('has_discrepancy', False)}, "
                f"contradiction={int(kind_counts.get('contradiction', 0))}, "
                f"omission={int(kind_counts.get('omission', 0))}, "
                f"unsupported={int(kind_counts.get('unsupported', 0))}"
                "[/dim]"
            )
            console.print("    [dim]Spot-check is monitoring-only; validation status is deterministic.[/dim]")
        else:
            console.print(
                f"    [dim]Spot-check: {spot.get('status')} ({spot.get('reason') or spot.get('error')})[/dim]"
            )

    return result


async def _run_validate_data_batch(
    db,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    force_validate: bool = False,
):
    """Run Stage 6 validation in batch mode, then sampled monitoring spot-checks."""
    from app.config import get_settings as _get_settings
    from app.database import async_session_maker
    from app.models import School
    from app.scrapers.validator import (
        has_current_validation_report,
        run_spot_check_for_school,
        validate_school_data,
    )
    from sqlalchemy import select

    settings = _get_settings()
    query = select(School.id, School.attributes).where(
        School.country_code == country,
        School.scrape_status.in_(["extracted", "summarized"]),
    )
    if city:
        query = query.where(School.city == city)
    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    school_rows = result.all()
    if force_validate:
        school_ids = [row[0] for row in school_rows]
    else:
        school_ids = [
            row[0] for row in school_rows if not has_current_validation_report(row[1], schema_version=1)
        ]

    if not school_ids:
        if force_validate:
            console.print("[yellow]No schools to validate (must be extracted or summarized)[/yellow]")
        else:
            console.print("[yellow]No schools to validate (all extracted/summarized schools already have current Stage 6 reports)[/yellow]")
        return

    requested_concurrency = max(1, int(getattr(settings, "validation_batch_concurrency", 1)))
    max_concurrency = 8
    concurrency = min(requested_concurrency, max_concurrency)
    if requested_concurrency > max_concurrency:
        console.print(
            f"[yellow]Requested concurrency {requested_concurrency} capped to {max_concurrency}[/yellow]"
        )

    console.print(f"[cyan]Running Stage 6 validation for {len(school_ids)} schools...[/cyan]")
    console.print(f"  Concurrency: {concurrency}")

    ok_count = 0
    review_count = 0
    failed_count = 0
    validated_ids: list[int] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Validating extracted data...", total=len(school_ids))
        semaphore = asyncio.Semaphore(concurrency)

        async def _validate_single(school_id: int) -> tuple[int, dict]:
            async with semaphore:
                async with async_session_maker() as school_db:
                    try:
                        out = await validate_school_data(
                            db=school_db,
                            school_id=school_id,
                            country_code=country,
                            run_spot_check=False,
                        )
                    except Exception as exc:
                        logger.error("Validation failed for school %s: %s", school_id, exc)
                        out = {"status": "validation_failed", "error": str(exc)}
                    return school_id, out

        tasks = [asyncio.create_task(_validate_single(school_id)) for school_id in school_ids]
        for completed in asyncio.as_completed(tasks):
            school_id, out = await completed
            status = out.get("status")
            if status == "ok":
                ok_count += 1
                validated_ids.append(school_id)
            elif status == "needs_review":
                review_count += 1
                validated_ids.append(school_id)
            else:
                failed_count += 1
            progress.update(task, advance=1)

    console.print("[green]✓ Validation complete:[/green]")
    console.print(f"  OK: {ok_count}")
    console.print(f"  Needs review: {review_count}")
    console.print(f"  Failed: {failed_count}")

    sample_size = int(getattr(settings, "spot_check_sample_size", 0))
    sample_ids = sorted(validated_ids)
    if sample_size == 0:
        sample_ids = []
    elif sample_size > 0:
        sample_ids = sorted(random.sample(sample_ids, min(sample_size, len(sample_ids))))
    elif sample_size < 0:
        # -1 sentinel means "all validated schools in this run".
        pass
    if not sample_ids:
        console.print("[yellow]No spot-checks scheduled (sample size is 0 or no validated schools).[/yellow]")
        return

    console.print(f"[cyan]Running capable-model spot-checks for {len(sample_ids)} schools...[/cyan]")
    console.print("  [dim]Spot-checks are monitoring-only and do not change Stage 6 pass/fail status.[/dim]")

    checked_count = 0
    discrepancy_count = 0
    spot_failed_count = 0
    contradiction_count = 0
    omission_count = 0
    unsupported_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Running spot-checks...", total=len(sample_ids))

        semaphore = asyncio.Semaphore(concurrency)

        async def _spot_check_single(school_id: int) -> tuple[int, dict]:
            async with semaphore:
                async with async_session_maker() as school_db:
                    try:
                        out = await run_spot_check_for_school(
                            db=school_db,
                            school_id=school_id,
                            country_code=country,
                        )
                    except Exception as exc:
                        logger.error("Spot-check failed for school %s: %s", school_id, exc)
                        out = {"status": "failed", "error": str(exc)}
                    return school_id, out

        tasks = [asyncio.create_task(_spot_check_single(school_id)) for school_id in sample_ids]
        for completed in asyncio.as_completed(tasks):
            _, out = await completed
            if out.get("status") == "checked":
                checked_count += 1
                if out.get("has_discrepancy"):
                    discrepancy_count += 1
                kind_counts = out.get("kind_counts") or {}
                contradiction_count += int(kind_counts.get("contradiction", 0))
                omission_count += int(kind_counts.get("omission", 0))
                unsupported_count += int(kind_counts.get("unsupported", 0))
            else:
                spot_failed_count += 1
            progress.update(task, advance=1)

    console.print("[green]✓ Spot-check complete:[/green]")
    console.print(f"  Checked: {checked_count}")
    console.print(f"  With discrepancies: {discrepancy_count}")
    console.print(f"  Failed/skipped: {spot_failed_count}")
    console.print(
        "  Discrepancy kinds: "
        f"contradiction={contradiction_count}, "
        f"omission={omission_count}, "
        f"unsupported={unsupported_count}"
    )

    if checked_count > 0:
        discrepancy_rate = discrepancy_count / checked_count
        threshold = float(getattr(settings, "spot_check_discrepancy_threshold", 0.15))
        console.print(f"  Discrepancy rate: {discrepancy_rate:.1%}")
        contradiction_rate = contradiction_count / checked_count
        console.print(f"  Contradiction rate: {contradiction_rate:.1%}")
        console.print("  [dim]Note: contradiction threshold is advisory for calibration only.[/dim]")
        if contradiction_rate > threshold:
            logger.info(
                "Spot-check monitoring alert: contradiction rate %.1f%% exceeded advisory threshold %.1f%%",
                contradiction_rate * 100,
                threshold * 100,
            )


async def _run_extract_school(db, school_id: int, country: str):
    """Run extraction stage for a single school."""
    from app.scrapers.extractor import extract_school

    console.print(f"  Extracting data for school {school_id}...")
    result = await extract_school(db=db, school_id=school_id, country_code=country)
    llm_stats = result.get("llm_stats") or {}

    if result.get("skipped"):
        console.print(f"[yellow]  Skipped: content unchanged[/yellow]")
    elif result.get("status") == "extracted":
        console.print(
            f"[green]  Success: pricing={result.get('pricing_count')} items, "
            f"general_info={result.get('general_info_success')}[/green]"
        )
        if result.get("details"):
            for detail in result.get("details"):
                console.print(f"    [dim]{detail}[/dim]")
    else:
        console.print(f"[red]  Failed: {result.get('error', 'Unknown error')}[/red]")
        if result.get("details"):
            for detail in result.get("details"):
                console.print(f"    [dim]{detail}[/dim]")

    if llm_stats:
        console.print(
            "    [dim]"
            f"LLM calls={llm_stats.get('total_calls', 0)}, "
            f"model_retries={llm_stats.get('model_retries', 0)}, "
            f"hard_fail={llm_stats.get('hard_failures', 0)} "
            f"({llm_stats.get('hard_failure_rate', 0):.2%})"
            "[/dim]"
        )
    return result


async def _run_extract_batch(
    db,
    country: str,
    city: str,
    limit: Optional[int],
    include_extracted: bool = False,
    school_ids: Optional[list[int]] = None,
):
    """Run extraction stage in batch mode."""
    from app.models import School
    from sqlalchemy import select

    explicit_school_ids = builtins.list(school_ids or [])
    query = select(School).where(
        School.country_code == country,
        School.website_url.isnot(None),
    )

    if explicit_school_ids:
        query = query.where(School.id.in_(explicit_school_ids))
    else:
        statuses = ["navigated", "extraction_failed"]
        if include_extracted:
            statuses.extend(["extracted", "summarized"])
        query = query.where(School.scrape_status.in_(statuses))

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.scalars().all()
    school_ids = [school.id for school in schools]

    if not school_ids:
        console.print("[yellow]No schools to extract (must be navigated or extraction_failed)[/yellow]")
        return

    if explicit_school_ids:
        status_label = "explicit repair selection"
    else:
        status_label = "navigated/failed + extracted/summarized" if include_extracted else "navigated/failed"
    console.print(f"[cyan]Extracting data for {len(school_ids)} schools...[/cyan]")
    console.print(f"  Status filter: {status_label}")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Extracting data...", total=len(school_ids))

        success_count = 0
        skipped_count = 0
        fail_count = 0

        from app.config import get_settings as _get_settings
        from app.database import async_session_maker

        settings = _get_settings()
        _ext_timeout = settings.extraction_school_timeout_seconds
        requested_concurrency = max(1, int(getattr(settings, "extraction_batch_concurrency", 1)))
        max_concurrency = 8
        concurrency = min(requested_concurrency, max_concurrency)

        console.print(f"  Concurrency: {concurrency}")
        if requested_concurrency > max_concurrency:
            console.print(
                f"[yellow]  Requested concurrency {requested_concurrency} capped to {max_concurrency}[/yellow]"
            )

        if concurrency == 1:
            for school_id in school_ids:
                try:
                    coro = _run_extract_school(db, school_id, country)
                    if _ext_timeout > 0:
                        result = await asyncio.wait_for(coro, timeout=_ext_timeout)
                    else:
                        result = await coro
                    if result.get("skipped"):
                        skipped_count += 1
                    elif result.get("status") == "extracted":
                        success_count += 1
                    else:
                        fail_count += 1
                except asyncio.TimeoutError:
                    logger.error(
                        "Extraction timed out after %.0fs for school %s", _ext_timeout, school_id
                    )
                    await db.rollback()
                    fail_count += 1
                except Exception as e:
                    logger.error(f"Error extracting school {school_id}: {e}")
                    await db.rollback()
                    fail_count += 1
                progress.update(task, advance=1)
        else:
            from app.scrapers.extractor import extract_school

            semaphore = asyncio.Semaphore(concurrency)

            async def _extract_single(school_id: int) -> tuple[int, dict]:
                async with semaphore:
                    try:
                        async with async_session_maker() as school_db:
                            coro = extract_school(db=school_db, school_id=school_id, country_code=country)
                            if _ext_timeout > 0:
                                result = await asyncio.wait_for(coro, timeout=_ext_timeout)
                            else:
                                result = await coro
                            return school_id, result
                    except asyncio.TimeoutError:
                        logger.error(
                            "Extraction timed out after %.0fs for school %s", _ext_timeout, school_id
                        )
                        return school_id, {
                            "school_id": school_id,
                            "status": "extraction_failed",
                            "error": "Extraction timeout",
                        }
                    except Exception as exc:
                        logger.error("Error extracting school %s: %s", school_id, exc)
                        return school_id, {
                            "school_id": school_id,
                            "status": "extraction_failed",
                            "error": str(exc),
                        }

            tasks = [asyncio.create_task(_extract_single(school_id)) for school_id in school_ids]
            for task_result in asyncio.as_completed(tasks):
                _, result = await task_result
                if result.get("skipped"):
                    skipped_count += 1
                elif result.get("status") == "extracted":
                    success_count += 1
                else:
                    fail_count += 1
                progress.update(task, advance=1)

    console.print(f"[green]✓ Extraction complete:[/green]")
    console.print(f"  Successful: {success_count}")
    console.print(f"  Skipped: {skipped_count}")
    console.print(f"  Failed: {fail_count}")


async def _run_summarize_school(db, school_id: int, country: str):
    """Run Stage 7 summarization for one school."""
    from app.scrapers.summarizer import summarize_school

    console.print(f"  Summarizing school {school_id}...")
    result = await summarize_school(db=db, school_id=school_id, country_code=country)
    status = result.get("status")
    if status == "summarized":
        console.print(
            f"[green]  Summarized "
            f"(input={int(result.get('input_tokens', 0) or 0)}, "
            f"output={int(result.get('output_tokens', 0) or 0)}, "
            f"cost=${float(result.get('token_cost_usd', 0.0) or 0.0):.6f})[/green]"
        )
    elif status == "skipped":
        console.print(f"[yellow]  Skipped: {result.get('reason', 'No reason provided')}[/yellow]")
    else:
        console.print(f"[red]  Failed: {result.get('reason', 'Unknown error')}[/red]")
    return result


async def _run_summarize_batch(
    db,
    country: str,
    city: Optional[str],
    limit: Optional[int],
):
    """Run Stage 7 summarization in batch mode."""
    from app.config import get_settings as _get_settings
    from app.database import async_session_maker
    from app.scrapers.summarizer import get_schools_requiring_summary, summarize_school

    settings = _get_settings()
    schools = await get_schools_requiring_summary(db=db, country_code=country, city=city, limit=limit)
    school_ids = [school.id for school in schools]

    if not school_ids:
        console.print("[yellow]No schools to summarize (all eligible summaries are current or ineligible)[/yellow]")
        return []

    requested_concurrency = max(1, int(getattr(settings, "summarization_batch_concurrency", 1)))
    max_concurrency = 8
    concurrency = min(requested_concurrency, max_concurrency)
    if requested_concurrency > max_concurrency:
        console.print(
            f"[yellow]Requested concurrency {requested_concurrency} capped to {max_concurrency}[/yellow]"
        )

    console.print(f"[cyan]Summarizing {len(school_ids)} schools...[/cyan]")
    console.print(f"  Concurrency: {concurrency}")

    summarized_count = 0
    skipped_count = 0
    failed_count = 0
    input_tokens = 0
    output_tokens = 0
    token_cost = 0.0
    results: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Generating summaries...", total=len(school_ids))
        semaphore = asyncio.Semaphore(concurrency)

        async def _summarize_single(school_id: int) -> tuple[int, dict]:
            async with semaphore:
                async with async_session_maker() as school_db:
                    try:
                        out = await summarize_school(
                            db=school_db,
                            school_id=school_id,
                            country_code=country,
                        )
                    except Exception as exc:
                        logger.error("Summarization failed for school %s: %s", school_id, exc)
                        out = {"status": "summary_failed", "reason": str(exc)}
                    return school_id, out

        tasks = [asyncio.create_task(_summarize_single(school_id)) for school_id in school_ids]
        for completed in asyncio.as_completed(tasks):
            _, out = await completed
            results.append(out)
            status = out.get("status")
            if status == "summarized":
                summarized_count += 1
                input_tokens += int(out.get("input_tokens", 0) or 0)
                output_tokens += int(out.get("output_tokens", 0) or 0)
                token_cost += float(out.get("token_cost_usd", 0.0) or 0.0)
            elif status == "skipped":
                skipped_count += 1
            else:
                failed_count += 1
            progress.update(task, advance=1)

    console.print("[green]✓ Summarization complete:[/green]")
    console.print(f"  Summarized: {summarized_count}")
    console.print(f"  Skipped: {skipped_count}")
    console.print(f"  Failed: {failed_count}")
    console.print(f"  Tokens: in={input_tokens}, out={output_tokens}")
    console.print(f"  Cost: ${token_cost:.6f}")
    return results


if __name__ == "__main__":
    cli()
