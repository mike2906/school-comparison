from __future__ import annotations

"""
CLI tool for running scraping pipeline stages.

This provides a convenient interface for testing and debugging the scraping
pipeline. Every stage runs in this process.

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

    # Run every website stage (--sync is accepted for older scripts; runs are always synchronous)
    uv run python -m app.scrapers.cli run --stage all --limit 5

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

    # Repair wrong Sofia-oblast coordinates using official MON town metadata
    uv run python -m app.scrapers.cli repair-oblast-geocodes --city sofia
"""
import asyncio
import builtins
import datetime
import logging
from math import isfinite
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.table import Table

from app.scrapers.repairs import age_groups as age_group_repair
from app.scrapers.repairs import deposits as deposit_repair
from app.scrapers.repairs import display_names as display_name_audit_cmd
from app.scrapers.repairs import geocodes as geocode_repair
from app.scrapers.repairs import i18n as i18n_repair
from app.scrapers.repairs import locations as location_repair
from app.scrapers.repairs import websites as website_repair
from app.scrapers.stages import common as stage_common
from app.scrapers.stages import runner as stage_runner
from app.scrapers.stages import website as website_stage
from app.services.pipeline_runs import terminalize_stale_pipeline_runs
from app.services.provider_costs import reconcile_provider_cost

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


def _pipeline_run_cost_usd(run) -> float:
    """Read persisted run cost, including the legacy dedicated column fallback."""
    metrics = run.metrics if isinstance(run.metrics, dict) else {}
    usage = metrics.get("llm_usage") if isinstance(metrics.get("llm_usage"), dict) else {}
    raw_cost = usage.get("token_cost_usd", run.total_llm_cost_usd)
    try:
        cost = float(raw_cost or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return cost if isfinite(cost) and cost >= 0 else 0.0


def _read_cohort_file(path: Path) -> list[int]:
    """Read positive school IDs from a newline/comma file with ``#`` comments."""
    tokens: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.partition("#")[0]
        tokens.extend(line.replace(",", " ").split())
    if not tokens:
        raise click.UsageError(f"Cohort file is empty: {path}")

    school_ids: list[int] = []
    seen: set[int] = set()
    for token in tokens:
        try:
            school_id = int(token)
        except ValueError as exc:
            raise click.UsageError(f"Invalid school ID {token!r} in cohort file {path}") from exc
        if school_id <= 0:
            raise click.UsageError(f"School IDs must be positive; got {school_id} in {path}")
        if school_id in seen:
            raise click.UsageError(f"Duplicate school ID {school_id} in cohort file {path}")
        seen.add(school_id)
        school_ids.append(school_id)
    return sorted(school_ids)


@click.group()
def cli():
    """Sofia School Comparison - Scraping Pipeline CLI"""
    pass


@cli.command("terminalize-stale-runs")
@click.option(
    "--stale-after-seconds",
    type=click.IntRange(min=1),
    default=None,
    help="Heartbeat age required before a RUNNING run may be terminalized",
)
def terminalize_stale_runs(stale_after_seconds: Optional[int]):
    """Mark only heartbeat-confirmed stale pipeline runs terminal."""
    asyncio.run(_terminalize_stale_runs(stale_after_seconds))


async def _terminalize_stale_runs(stale_after_seconds: Optional[int]) -> None:
    from app.config import get_settings
    from app.database import async_session_maker

    settings = get_settings()
    age_seconds = stale_after_seconds or int(settings.pipeline_stale_after_seconds)
    now = datetime.datetime.now(datetime.timezone.utc)
    async with async_session_maker() as db:
        runs = await terminalize_stale_pipeline_runs(
            db,
            stale_before=now - datetime.timedelta(seconds=age_seconds),
            now=now,
        )
    if not runs:
        console.print("[green]No stale RUNNING pipeline runs found.[/green]")
        return
    for run in runs:
        console.print(
            f"[yellow]{run.id}: {run.status.value}; last_stage="
            f"{run.last_completed_stage or 'none'}; succeeded={run.schools_succeeded}; "
            f"failed={run.schools_failed}[/yellow]"
        )


@cli.command("reconcile-provider-cost")
@click.option("--run-id", required=True, help="PipelineRun UUID")
@click.option(
    "--provider-total-cost-usd",
    required=True,
    type=click.FloatRange(min=0.0),
    help="Exact provider-reported total for the run reconciliation window",
)
def reconcile_provider_cost_command(run_id: str, provider_total_cost_usd: float):
    """Persist an exact provider-total versus request-ledger discrepancy."""
    asyncio.run(_reconcile_provider_cost_command(run_id, provider_total_cost_usd))


async def _reconcile_provider_cost_command(run_id: str, provider_total_cost_usd: float) -> None:
    from app.database import async_session_maker

    async with async_session_maker() as db:
        payload = await reconcile_provider_cost(
            db,
            pipeline_run_id=run_id,
            provider_total_cost_usd=provider_total_cost_usd,
        )
    console.print(f"[green]Reconciled provider cost for {run_id}[/green]")
    console.print(f"  Ledger: ${payload['ledger_total_cost_usd']:.8f}")
    console.print(f"  Provider: ${payload['provider_total_cost_usd']:.8f}")
    console.print(f"  Discrepancy: ${payload['discrepancy_usd']:.8f}")


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
        i18n_repair.repair_i18n_command(
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
        website_repair.repair_websites_command(
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


@cli.command("repair-age-groups")
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to inspect")
@click.option("--dry-run", is_flag=True, help="Preview affected schools without writing data")
@click.option("--refresh-navigation", is_flag=True, help="Rerun website navigation before inferring age groups")
def repair_age_groups(school, school_id, city, country, limit, dry_run, refresh_navigation):
    """Repair missing location age groups from official website evidence."""
    asyncio.run(
        age_group_repair.repair_age_groups_command(
            school_name=school,
            school_id=school_id,
            city=city,
            country=country,
            limit=limit,
            dry_run=dry_run,
            refresh_navigation=refresh_navigation,
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
        display_name_audit_cmd.audit_display_names_command(
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
        location_repair.repair_locations_command(
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


@cli.command("repair-oblast-geocodes")
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to inspect")
@click.option("--dry-run", is_flag=True, help="Preview affected schools without writing data")
def repair_oblast_geocodes(school, school_id, city, country, limit, dry_run):
    """Repair stale Sofia-oblast coordinates using official MON town and municipality data."""
    asyncio.run(
        geocode_repair.repair_oblast_geocodes_command(
            school_name=school,
            school_id=school_id,
            city=city,
            country=country,
            limit=limit,
            dry_run=dry_run,
        )
    )


@cli.command("repair-out-of-bounds-geocodes")
@click.option("--school", help="School name (fuzzy match)")
@click.option("--school-id", type=int, help="School ID")
@click.option("--city", default="sofia", help="City to filter by")
@click.option("--country", default="bg", help="Country code")
@click.option("--limit", type=int, help="Limit number of schools to inspect")
@click.option("--dry-run", is_flag=True, help="Preview affected locations without writing data")
def repair_out_of_bounds_geocodes(school, school_id, city, country, limit, dry_run):
    """Repair city-scoped coordinates that fall outside the configured city bounds."""
    asyncio.run(
        geocode_repair.repair_out_of_bounds_geocodes_command(
            school_name=school,
            school_id=school_id,
            city=city,
            country=country,
            limit=limit,
            dry_run=dry_run,
        )
    )


@cli.command("shared-site-check")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Only groups containing this school (repeatable)")
@click.option("--city", default=None, help="Only groups with a member in this city (default: whole country)")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--dry-run", is_flag=True, help="Report decisions without writing data")
@click.option(
    "--report",
    "report_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the full per-institution report as JSON",
)
def shared_site_check_command(school_ids, city, country, dry_run, report_path):
    """Keep, replace (brand hub -> campus) or withhold websites shared by 2+ institutions."""
    asyncio.run(
        _shared_site_check_command(
            school_ids=builtins.list(school_ids) or None,
            city=city,
            country=country,
            dry_run=dry_run,
            report_path=report_path,
        )
    )


async def _shared_site_check_command(*, school_ids, city, country, dry_run, report_path):
    import json

    from app.database import async_session_maker

    async with async_session_maker() as db:
        report = await website_stage.check_shared_sites(
            db, country=country, city=city, school_ids=school_ids, dry_run=dry_run
        )
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("fees-agent")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools, any type (repeatable)")
@click.option("--city", default=None, help="Only schools in this city")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--max-usd", type=float, default=0.50, show_default=True, help="Spend cap for the run")
@click.option("--apply", "apply_changes", is_flag=True, help="Write rows (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
@click.option("--after-id", type=int, default=None, help="Start after this school id (carry on a stopped run)")
@click.option(
    "--workers", type=click.IntRange(1, 16), default=8, show_default=True, help="Schools read at a time (1-16)"
)
@click.option(
    "--save-pages",
    "pages_dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Also write each school's found pages here (the fees eval's input)",
)
@click.option(
    "--no-check", "no_check", is_flag=True, help="Skip the fee check after each read (stages/fee_check.py)"
)
def fees_agent_command(
    school_ids, city, country, limit, max_usd, apply_changes, report_path, after_id, workers, pages_dir, no_check
):
    """Read private and international schools' fee pages with the fee agent (experimental, not in `all`)."""
    asyncio.run(
        _fees_agent_command(
            school_ids=school_ids,
            city=city,
            country=country,
            limit=limit,
            max_usd=max_usd,
            apply=apply_changes,
            report_path=report_path,
            after_id=after_id,
            workers=workers,
            pages_dir=pages_dir,
            check=not no_check,
        )
    )


async def _fees_agent_command(
    *, school_ids, city, country, limit, max_usd, apply, report_path, after_id, workers, pages_dir, check
):
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import fees_agent as fees_agent_stage

    lines_path = None
    if report_path is not None:  # each school's line is appended as it finishes (never emptied)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        lines_path = report_path.with_name(f"{report_path.stem}.schools.jsonl")
        console.print(f"Per-school lines: {lines_path}")
    async with async_session_maker() as db:
        report = await fees_agent_stage.run_fees_agent(
            db,
            country=country,
            city=city,
            school_ids=school_ids,
            limit=limit,
            max_usd=max_usd,
            apply=apply,
            after_id=after_id,
            lines_path=lines_path,
            workers=workers,
            pages_dir=pages_dir,
            check=check,
        )
    console.print(
        f"{'Applied' if apply else 'Dry run'}: {report['schools']} schools, ${report['spent_usd']:.4f} spent, "
        f"outcomes {report['outcomes']}"
    )
    if report["stopped"]:
        console.print(f"[yellow]{report['stopped']}[/yellow]")
    if report["kept_old_rows"]:
        console.print(f"Kept old rows: {report['kept_old_rows']}")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("school-facts")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--city", default=None, help="Only schools in this city")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--max-usd", type=float, default=0.50, show_default=True, help="Spend cap for the run")
@click.option("--apply", "apply_changes", is_flag=True, help="Store facts (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
@click.option("--after-id", type=int, default=None, help="Start after this school id (carry on a stopped run)")
@click.option(
    "--workers", type=click.IntRange(1, 16), default=8, show_default=True, help="Schools read at a time (1-16)"
)
def school_facts_command(school_ids, city, country, limit, max_usd, apply_changes, report_path, after_id, workers):
    """Read schools' own pages for concrete facts by topic (experimental, not in `all`)."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import school_facts as school_facts_stage

    lines_path = None
    if report_path is not None:  # each school's line is appended as it finishes (never emptied)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        lines_path = report_path.with_name(f"{report_path.stem}.schools.jsonl")

    async def _run():
        async with async_session_maker() as db:
            return await school_facts_stage.run_school_facts(
                db,
                country=country,
                city=city,
                school_ids=school_ids,
                limit=limit,
                max_usd=max_usd,
                apply=apply_changes,
                workers=workers,
                after_id=after_id,
                lines_path=lines_path,
            )

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['schools']} schools, ${report['spent_usd']:.4f}")
    for result in report["results"]:
        console.print(f"  {result['school_id']}: {result['outcome']}")
    if report["stopped"]:
        console.print(f"[yellow]{report['stopped']}[/yellow]")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("open-days")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Check these schools (repeatable)")
@click.option("--city", default=None, help="Only schools in this city")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--max-usd", type=float, default=0.30, show_default=True, help="Provider spend cap for the run")
@click.option("--apply", "apply_changes", is_flag=True, help="Store dates and page hashes (default: dry run)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
@click.option(
    "--workers", type=click.IntRange(1, 16), default=8, show_default=True, help="Schools checked at a time (1-16)"
)
def open_days_command(school_ids, city, country, limit, max_usd, apply_changes, report_path, workers):
    """Check schools' pages for new open days and application dates (model only on a change)."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import open_days as open_days_stage

    async def _run():
        async with async_session_maker() as db:
            return await open_days_stage.run_open_days(
                db,
                country=country,
                city=city,
                school_ids=school_ids,
                limit=limit,
                max_usd=max_usd,
                apply=apply_changes,
                workers=workers,
            )

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['schools']} schools, {report['outcomes']}, ${report['spent_usd']:.4f}")
    if report["stopped"]:
        console.print(f"[yellow]{report['stopped']}[/yellow]")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("social-links")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--city", default=None, help="Only schools in this city")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--apply", "apply_changes", is_flag=True, help="Store links (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def social_links_command(school_ids, city, country, limit, apply_changes, report_path):
    """Find the Facebook page and Instagram profile each school's website links (no model calls)."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import social_links as social_links_stage

    async def _run():
        async with async_session_maker() as db:
            return await social_links_stage.run_social_links(
                db, country=country, city=city, school_ids=school_ids, limit=limit, apply=apply_changes
            )

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['schools']} schools, outcomes {report['outcomes']}, found {report['found']}")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("website-name")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--city", default=None, help="Only schools in this city")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option(
    "--school-type", "school_types", multiple=True, default=("private", "international"), show_default=True,
    help="School types to read when no --school-id is given (repeatable)",
)
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--max-usd", type=float, default=0.30, show_default=True, help="Spend cap for the run")
@click.option("--apply", "apply_changes", is_flag=True, help="Store names (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def website_name_command(school_ids, city, country, school_types, limit, max_usd, apply_changes, report_path):
    """Read the Latin name each school's website uses, for its English name and search."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import website_name as website_name_stage

    async def _run():
        async with async_session_maker() as db:
            return await website_name_stage.run_website_name(
                db, country=country, city=city, school_ids=school_ids, school_types=school_types,
                limit=limit, max_usd=max_usd, apply=apply_changes,
            )

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['schools']} schools, {report['outcomes']}, ${report['spent_usd']:.4f}")
    for result in report["results"]:
        if result.get("answer"):
            console.print(f"  {result['school_id']}: {result['answer']} ({result['dropped'] or 'kept'})")
    if report["stopped"]:
        console.print(f"[yellow]{report['stopped']}[/yellow]")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("translate-texts")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--city", default=None, help="Only schools in this city")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--max-usd", type=float, default=0.20, show_default=True, help="Spend cap for the run")
@click.option("--apply", "apply_changes", is_flag=True, help="Store translations (default is a dry run that writes nothing)")
@click.option(
    "--plan", "plan_only", is_flag=True,
    help="Only say whether --apply would change anything (exit 10 if so); no model call",
)
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def translate_texts_command(school_ids, city, country, max_usd, apply_changes, plan_only, report_path):
    """Translate the fee names, class names and list items pages show in the other language."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import text_translations as text_translations_stage

    async def _run():
        async with async_session_maker() as db:
            return await text_translations_stage.run_text_translations(
                db, country=country, city=city, school_ids=school_ids, max_usd=max_usd, apply=apply_changes,
                plan=plan_only,
            )

    report = asyncio.run(_run())
    if plan_only:
        console.print(f"Plan: {report['schools_to_change']} schools to change, to ask {report['asked']}")
        from app.ai.client import missing_key

        asked = report["asked"]
        if (sum(asked.values()) if isinstance(asked, dict) else asked) and (key := missing_key("translation")):
            # A checkout whose .env lacks the key (a stale worktree copy) must not publish
            # untranslated text quietly.
            console.print(f"[red]{key} is not set in this checkout's backend/.env[/red]")
            raise SystemExit(78)
        raise SystemExit(10 if report["schools_to_change"] else 0)
    mode = "Applied" if apply_changes else "Dry run"
    console.print(
        f"{mode}: {report['schools']} schools, {report['schools_changed']} changed, asked {report['asked']}, "
        f"translated {report['translated']}, ${report['spent_usd']:.4f}"
    )
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")
    if report["stopped"]:
        console.print(f"[yellow]{report['stopped']}[/yellow]")
        raise SystemExit(1)


@cli.command("confirm-pins")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--city", default="sofia", show_default=True, help="City (only Sofia has the bounds and town name yet)")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--apply", "apply_changes", is_flag=True, help="Write pins (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def confirm_pins_command(school_ids, city, country, limit, apply_changes, report_path):
    """Publish hidden pins that OpenStreetMap puts at our exact street and house number."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import confirm_pins as confirm_pins_stage

    async def _run():
        async with async_session_maker() as db:
            return await confirm_pins_stage.run_confirm_pins(
                db, country=country, city=city, school_ids=school_ids, limit=limit, apply=apply_changes
            )

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['locations']} locations, outcomes {report['outcomes']}")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("hand-pin")
@click.option("--location-id", type=int, required=True, help="The hidden location")
@click.option("--lat", type=float, required=True)
@click.option("--lng", type=float, required=True)
@click.option("--address", default=None, help="Its address, when the point's differs from the stored one (Bulgarian)")
@click.option("--note", required=True, help="What the point was checked against")
@click.option("--apply", "apply_changes", is_flag=True, help="Write the pin (default is a dry run that writes nothing)")
def hand_pin_command(location_id, lat, lng, address, note, apply_changes):
    """Pin one hidden location at a point a person checked (a manual_fix)."""
    from app.database import async_session_maker
    from app.scrapers.stages.hand_pin import set_hand_pin

    async def _run():
        async with async_session_maker() as db:
            return await set_hand_pin(
                db, location_id, (lat, lng), note=note, address=address, apply=apply_changes
            )

    report = asyncio.run(_run())
    written = apply_changes and report["outcome"] == "placed"
    console.print(f"{'Applied' if written else 'Dry run' if not apply_changes else 'Not written'}: {report}")
    if report["outcome"] != "placed":
        raise SystemExit(1)


@cli.command("move-to-site-campus")
@click.option("--location-id", type=int, required=True, help="The register location the site contradicts")
@click.option("--campus-id", type=int, required=True, help="The school's site campus location at its real address")
@click.option("--note", required=True, help="Where the school's site gives the address")
@click.option("--apply", "apply_changes", is_flag=True, help="Write the move (default is a dry run)")
def move_to_site_campus_command(location_id, campus_id, note, apply_changes):
    """Give a register location the address and point its school's own site gives (a manual_fix)."""
    from app.database import async_session_maker
    from app.scrapers.stages.hand_pin import move_to_site_campus

    async def _run():
        async with async_session_maker() as db:
            return await move_to_site_campus(db, location_id, campus_id, note=note, apply=apply_changes)

    report = asyncio.run(_run())
    console.print(f"{'Applied' if apply_changes and report['outcome'] == 'moved' else 'Dry run' if not apply_changes else 'Not written'}: {report}")
    if report["outcome"] != "moved":
        raise SystemExit(1)


@cli.command("register-pins")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--register-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="A downloaded register CSV or its zip (default: download it)")
@click.option("--apply", "apply_changes", is_flag=True, help="Write pins (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def register_pins_command(school_ids, limit, register_file, apply_changes, report_path):
    """Pin hidden Sofia locations on Sofia Municipality's official address point."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import register_pins as register_pins_stage
    from app.services.geocoding.bg.sofia_address_register import read_register_file

    register = read_register_file(register_file) if register_file is not None else None

    async def _run():
        async with async_session_maker() as db:
            return await register_pins_stage.run_register_pins(
                db, school_ids=school_ids, limit=limit, apply=apply_changes, register=register
            )

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['locations']} locations, outcomes {report['outcomes']}")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("site-address")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--register-file", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="A downloaded register CSV or its zip (default: download it)")
@click.option("--apply", "apply_changes", is_flag=True, help="Write addresses and pins (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def site_address_command(school_ids, limit, register_file, apply_changes, report_path):
    """Give hidden Sofia locations the address their school's own website maps and names."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import site_address as site_address_stage
    from app.services.geocoding.bg.sofia_address_register import read_register_file

    register = read_register_file(register_file) if register_file is not None else None

    async def _run():
        async with async_session_maker() as db:
            return await site_address_stage.run_site_address(
                db, school_ids=school_ids, limit=limit, apply=apply_changes, register=register
            )

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['locations']} locations, outcomes {report['outcomes']}")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("site-phones")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--apply", "apply_changes", is_flag=True, help="Write phones (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def site_phones_command(school_ids, limit, apply_changes, report_path):
    """Show the phone a school's own website gives instead of the register's."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import site_phones as site_phones_stage

    async def _run():
        async with async_session_maker() as db:
            return await site_phones_stage.run_site_phones(db, school_ids=school_ids, limit=limit, apply=apply_changes)

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['locations']} locations, outcomes {report['outcomes']}")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("website-pins")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--city", default="sofia", show_default=True, help="City (only Sofia has the bounds and town name yet)")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--apply", "apply_changes", is_flag=True, help="Write pins (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def website_pins_command(school_ids, city, country, limit, apply_changes, report_path):
    """Pin hidden locations on their website's map point when OpenStreetMap puts it at the address."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import website_pins as website_pins_stage

    async def _run():
        async with async_session_maker() as db:
            return await website_pins_stage.run_website_pins(
                db, country=country, city=city, school_ids=school_ids, limit=limit, apply=apply_changes
            )

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['locations']} locations, outcomes {report['outcomes']}")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("pin-agent")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--city", default="sofia", show_default=True, help="City (only Sofia has the bounds and town name yet)")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--max-usd", type=float, default=0.50, show_default=True, help="Spend cap for the run")
@click.option("--apply", "apply_changes", is_flag=True, help="Write pins (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def pin_agent_command(school_ids, city, country, limit, max_usd, apply_changes, report_path):
    """Let a model pick hidden locations' buildings from website and OSM candidates, checked in code."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import pin_agent as pin_agent_stage

    async def _run():
        async with async_session_maker() as db:
            return await pin_agent_stage.run_pin_agent(
                db, country=country, city=city, school_ids=school_ids, limit=limit, max_usd=max_usd, apply=apply_changes
            )

    report = asyncio.run(_run())
    mode = "Applied" if apply_changes else "Dry run"
    console.print(f"{mode}: {report['locations']} locations, outcomes {report['outcomes']}, ${report['spent_usd']:.4f}")
    if report["stopped"]:
        console.print(f"[yellow]{report['stopped']}[/yellow]")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("osm-check")
@click.option("--city", default="sofia", show_default=True, help="City (only Sofia has bounds yet)")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
@click.option("--issue-body", "issue_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the GitHub issue text")
def osm_check_command(city, country, report_path, issue_path):
    """List schools whose pin, number or website disagrees with OpenStreetMap (writes nothing)."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import osm_check

    async def _run():
        elements = await osm_check.read_osm(country, city)
        async with async_session_maker() as db:
            return await osm_check.run_osm_check(db, elements, country=country, city=city)

    report = asyncio.run(_run())
    console.print(f"Checked {report['checked']} of {report['schools']} schools against OSM: flags {report['flags']}")
    if issue_path is not None:
        issue_path.write_text(osm_check.issue_body(report), encoding="utf-8")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        console.print(f"Report: {report_path}")


@cli.command("check-website")
@click.option("--school-id", type=int, required=True, help="School ID")
@click.option("--url", required=True, help="The site to check, e.g. one search does not find")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--apply", "apply_changes", is_flag=True, help="Store the site if it is valid (default writes nothing)")
@click.option("--replace", is_flag=True, help="Let a valid site replace the school's accepted one")
@click.option("--section", is_flag=True, help="With --replace: the URL is the school's own section of its current site")
@click.option("--alias", "aliases", multiple=True, help="Another name the school uses on its site (repeatable); kept when stored")
def check_website_command(school_id, url, country, apply_changes, replace, section, aliases):
    """Validate a given site for a school with the usual checks; with --apply a valid one is stored."""
    from app.database import async_session_maker

    async def _run():
        async with async_session_maker() as db:
            return await website_stage.run_validate_url(
                db, school_id, country, url=url, apply=apply_changes, replace=replace, section=section,
                aliases=tuple(aliases),
            )

    asyncio.run(_run())


@cli.command("find-websites")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Search for these schools, any type (repeatable)")
@click.option("--city", default="sofia", show_default=True, help="City")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option(
    "--retry-days", type=int, default=90, show_default=True, help="Skip a school searched this many days ago or less"
)
@click.option("--apply", "apply_changes", is_flag=True, help="Store the sites found (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def find_websites_command(school_ids, city, country, limit, retry_days, apply_changes, report_path):
    """Search for the websites of private schools that have none; store only those validation accepts (free)."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import find_websites

    async def _run():
        async with async_session_maker() as db:
            return await find_websites.run_find_websites(
                db,
                country=country,
                city=city,
                school_ids=school_ids,
                limit=limit,
                retry_days=retry_days,
                apply=apply_changes,
            )

    report = asyncio.run(_run())
    console.print(
        f"{'Applied' if apply_changes else 'Dry run'}: {report['searched']} of {report['schools_without_site']} "
        f"schools without a site searched, {report['found']} found, {report['errors']} errors"
    )
    for school in report["schools"]:
        if school.get("found"):
            console.print(f"  {school['school_id']} {school['name_bg']}: {school['found']}", markup=False)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


@cli.command("flag-deposits")
@click.option("--apply", "apply_changes", is_flag=True, help="Flag the rows (default is a dry run that writes nothing)")
def flag_deposits_command(apply_changes):
    """Flag stored registration fees whose own words name a deposit, as a new read would."""
    asyncio.run(deposit_repair.flag_deposits_command(apply=apply_changes))


@cli.command("site-city")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Check these schools (repeatable)")
@click.option("--city", default="sofia", show_default=True, help="City")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--limit", type=int, default=None, help="Maximum schools")
@click.option("--apply", "apply_changes", is_flag=True, help="Withhold the sites hit (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def site_city_command(school_ids, city, country, limit, apply_changes, report_path):
    """Withhold accepted websites whose home page title names another town or numbered school."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import site_city

    async def _run():
        async with async_session_maker() as db:
            return await site_city.run_site_city(
                db, country=country, city=city, school_ids=school_ids, limit=limit, apply=apply_changes
            )

    report = asyncio.run(_run())
    console.print(
        f"Checked {report['checked']} sites ({report['unread']} not read); another town or school on {len(report['hits'])}"
    )
    for hit in report["hits"]:
        state = "withheld" if hit.get("withheld") else "would withhold"
        console.print(f"  {hit['school_id']} {hit['name_bg']}: {hit['website']} names {hit['town'] or hit['school']} ({state})")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


@cli.command("site-grades")
@click.option("--school-id", "school_ids", type=int, multiple=True, help="Run these schools (repeatable)")
@click.option("--city", default="sofia", show_default=True, help="City")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--apply", "apply_changes", is_flag=True, help="Write the bands (default is a dry run that writes nothing)")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
def site_grades_command(school_ids, city, country, apply_changes, report_path):
    """Add or hide schools' grade bands by what their own published site data says they teach."""
    import json

    from app.database import async_session_maker
    from app.scrapers.stages import site_grades

    async def _run():
        async with async_session_maker() as db:
            return await site_grades.run_site_grades(
                db, country=country, city=city, school_ids=school_ids, apply=apply_changes
            )

    report = asyncio.run(_run())
    console.print(f"Checked {report['checked']} schools; bands change on {len(report['changes'])}")
    for change in report["changes"]:
        console.print(f"  {change['school_id']} {change['name_bg']}: register {change['register']}, add {change['add']}, hide {change['hide']}, remove {change['remove']}")
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


@cli.command("publish-diff")
@click.option("--city", default="sofia", show_default=True, help="City")
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--live-api", default="https://api.schooldecider.com", show_default=True, help="The live API to compare with")
@click.option("--out", "report_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the JSON report")
@click.option("--issue-body", "issue_path", type=click.Path(dir_okay=False, path_type=Path), help="Write the GitHub issue text when stopping")
def publish_diff_command(city, country, live_api, report_path, issue_path):
    """Before a publish: exit 3 if many schools would lose something they show now (writes nothing).

    Exit 75 when the live list can't be read: nothing to compare with. A launch DB the list
    can't be built from fails like any error, so the publish stops.
    """
    import json

    import httpx

    from app.database import async_session_maker
    from app.scrapers.stages import publish_diff

    async def _run():
        try:
            live = await publish_diff.read_live_list(live_api, country=country, city=city)
        except (httpx.HTTPError, ValueError) as exc:
            console.print(f"Could not read the live list: {exc}", markup=False)
            raise SystemExit(75) from exc
        async with async_session_maker() as db:
            new = await publish_diff.build_new_list(db, country=country, city=city)
        return publish_diff.diff_lists(live, new)

    report = asyncio.run(_run())
    console.print(publish_diff.summary(report), markup=False)
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if report["stop"]:
        if issue_path is not None:
            issue_path.write_text(publish_diff.issue_body(report), encoding="utf-8")
        raise SystemExit(3)


@cli.command("promote-curated-identities")
@click.option(
    "--school-id",
    "school_ids",
    type=int,
    multiple=True,
    required=True,
    help="Explicit school ID to promote (repeatable)",
)
@click.option("--city", help="Required city scope", required=True)
@click.option("--country", default="bg", show_default=True, help="Country code")
@click.option("--promoted-by", required=True, help="Actor applying the reviewed curation")
@click.option(
    "--commit",
    "commit_changes",
    is_flag=True,
    help="Persist eligible promotions (default is dry-run)",
)
def promote_curated_identities_command(
    school_ids,
    city,
    country,
    promoted_by,
    commit_changes,
):
    """Promote reviewed website identities into canonical localized names."""
    asyncio.run(
        _promote_curated_identities_command(
            school_ids=school_ids,
            city=city,
            country=country,
            promoted_by=promoted_by,
            commit_changes=commit_changes,
        )
    )


async def _promote_curated_identities_command(
    *,
    school_ids,
    city,
    country,
    promoted_by,
    commit_changes,
):
    from app.database import async_session_maker
    from app.services.identity_curation import promote_curated_identities

    async with async_session_maker() as db:
        result = await promote_curated_identities(
            db,
            school_ids=school_ids,
            country=country,
            city=city,
            promoted_by=promoted_by,
            commit=commit_changes,
        )

    mode = "commit" if commit_changes else "dry-run"
    console.print(f"[cyan]Canonical identity promotion ({mode})[/cyan]")
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("ID", style="dim")
    table.add_column("English identity")
    table.add_column("Decision")
    table.add_column("Reason")
    for row in result["rows"]:
        table.add_row(
            str(row["school_id"]),
            row["english_name"] or "—",
            row["decision"],
            row["reason"] or "—",
        )
    console.print(table)
    console.print(
        "  ".join(
            [
                f"requested={result['requested']}",
                f"eligible={result['eligible']}",
                f"promoted={result['promoted']}",
                f"already={result['already_promoted']}",
                f"conflicts={result['conflicts']}",
                f"ineligible={result['ineligible']}",
            ]
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
            "dzi",
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
@click.option("--year", type=int, help="Specific NVO or ДЗИ exam year to import")
@click.option(
    "--history-years",
    type=int,
    default=5,
    show_default=True,
    help="Number of recent NVO or ДЗИ years to import when --year is omitted",
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
    "--cohort-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="For batch all stage, process the exact school IDs listed in this file",
)
@click.option(
    "--force-validate",
    is_flag=True,
    help="For validate-data stage, include schools that already have a current validation report",
)
@click.option(
    "--skip-summarize",
    is_flag=True,
    help="For batch all stage, stop after validation without generating summaries",
)
@click.option(
    "--provider-cost-cap-usd",
    type=click.FloatRange(min=0.0, min_open=True),
    default=None,
    help="Required per-run hard cap before a billable stage may dispatch a provider request",
)
@click.option("--sync", is_flag=True, help="No effect; kept so older commands still work. Runs are always synchronous.")
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
    cohort_file,
    force_validate,
    skip_summarize,
    provider_cost_cap_usd,
    sync,
    dry_run,
):
    """Run a pipeline stage."""
    if cohort_file is not None:
        if stage.lower() != "all" or school or school_id:
            raise click.UsageError("--cohort-file is only valid for batch --stage all")
        if limit is not None:
            raise click.UsageError("--cohort-file cannot be combined with --limit")
        cohort_ids = _read_cohort_file(cohort_file)
    else:
        cohort_ids = None

    if skip_summarize and (stage.lower() != "all" or school or school_id):
        raise click.UsageError("--skip-summarize is only valid for batch --stage all")

    if (
        not dry_run
        and stage.lower() in stage_runner.BILLABLE_STAGES
        and provider_cost_cap_usd is None
    ):
        raise click.UsageError(
            "--provider-cost-cap-usd is required for billable stages; "
            "provider calls fail closed without a per-run cap"
        )

    if dry_run:
        console.print("[yellow]DRY RUN - would execute:[/yellow]")
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
        console.print(f"  Cohort IDs: {cohort_ids or 'automatic'}")
        console.print(f"  Force validate: {force_validate}")
        console.print(f"  Skip summarize: {skip_summarize}")
        console.print(f"  Provider cost cap: {provider_cost_cap_usd or 'required before execution'}")
        return

    asyncio.run(
        stage_runner.run_sync(
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
            skip_summarize,
            cohort_ids,
            provider_cost_cap_usd,
        )
    )


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
    from sqlalchemy import update

    from app.database import async_session_maker
    from app.models import School

    async with async_session_maker() as db:
        if school_name or school_id:
            # Single school
            if school_name:
                school_id = await stage_common.find_school_by_name(db, school_name, country)
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
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import School

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


@cli.command("data-quality")
@click.option("--city", help="Filter by city")
@click.option("--country", default="bg", help="Country code")
@click.option("--runs", type=int, default=5, show_default=True, help="Recent pipeline runs to list")
def data_quality(city, country, runs):
    """Show the data-quality scoreboard (six go-live metrics) + recent runs."""
    asyncio.run(_show_data_quality(city, country, runs))


async def _show_data_quality(city, country, runs):
    from sqlalchemy import select

    from app.database import async_session_maker
    from app.models import PipelineRun
    from app.services.data_quality import compute_quality_metrics

    async with async_session_maker() as db:
        metrics = await compute_quality_metrics(db, country=country, city=city)

        console.print("\n[bold cyan]Data-quality scoreboard[/bold cyan]")
        console.print(f"Country: {country}" + (f"  City: {city}" if city else ""))
        console.print(f"Schools in scope: {metrics['schools_in_scope']}\n")

        def _pct(value):
            return "n/a" if value is None else f"{value:.1f}%"

        table = Table(show_header=True, header_style="bold")
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        table.add_column("Detail")

        v = metrics["validation_ok"]
        table.add_row("Validation OK", _pct(v["pct"]), f"{v['ok']}/{v['total']} schools")
        table.add_row(
            "Validation-report coverage",
            _pct(v["coverage_pct"]),
            f"{v['with_report']}/{v['total']} schools",
        )
        w = metrics["withheld_website_data"]
        reasons = ", ".join(f"{name.replace('_', ' ')} {count}" for name, count in w["reasons"].items())
        table.add_row(
            "Website data withheld",
            _pct(w["withheld_pct"]),
            f"{w['withheld']}/{w['with_data']} schools with website data; {reasons}",
        )
        table.add_row(
            "Published website data validated OK",
            _pct(w["published_ok_pct"]),
            f"{w['published_ok']}/{w['published']} published",
        )
        table.add_row("Duplicate coordinate groups", str(metrics["duplicate_coordinate_groups"]), "points shared by ≥2 schools")
        p = metrics["location_precision_exact"]
        table.add_row(
            "Location precision=exact",
            _pct(p["pct"]),
            f"{p['exact']}/{p['geocoded']} geocoded",
        )
        table.add_row(
            "Precision-metadata coverage",
            _pct(p["coverage_pct"]),
            f"{p['with_precision']}/{p['geocoded']} geocoded",
        )
        d = metrics["display_name_overrides"]
        table.add_row(
            "Display-name overrides",
            _pct(d["pct"]),
            f"{d['overrides']}/{d['total']} schools",
        )
        table.add_row(
            "Display-name candidates",
            _pct(d["candidate_coverage_pct"]),
            f"{d['candidates']}/{d['total']} schools; "
            f"{_pct(d['conversion_pct'])} corroborated",
        )
        c = metrics["curated_identity_promotions"]
        table.add_row(
            "Curated EN identities blocked",
            str(c["blocked"]),
            f"{c['published']}/{c['eligible']} published; "
            f"{c['conflicts']} conflicts",
        )
        s = metrics["spot_check_discrepancy_rate"]
        rate = "n/a" if s["rate"] is None else f"{s['rate']:.1%}"
        table.add_row("Spot-check discrepancy rate", rate, f"{s['discrepancies']}/{s['schools_checked']} checked")
        g = metrics["pricing_rows_failing_gates"]
        table.add_row("Pricing rows failing gates", _pct(g["pct"]), f"{g['failing']}/{g['total']} scraped rows")
        console.print(table)
        if w["withheld_school_ids"]:
            console.print(f"Website data withheld for school ids: {w['withheld_school_ids']}")

        if runs > 0:
            runs_query = select(PipelineRun).where(PipelineRun.country_code == country)
            if city:
                runs_query = runs_query.where(PipelineRun.city == city)
            recent = builtins.list(
                (
                    await db.execute(
                        runs_query.order_by(PipelineRun.started_at.desc()).limit(runs)
                    )
                )
                .scalars()
                .all()
            )
            console.print(f"\n[bold]Recent pipeline runs ({len(recent)}):[/bold]")
            if not recent:
                console.print("  [dim]none recorded yet[/dim]")
            for run in recent:
                started = run.started_at.strftime("%Y-%m-%d %H:%M") if run.started_at else "?"
                console.print(
                    f"  {started}  {run.config.get('cli_stage', run.stage.value) if run.config else run.stage.value:<14} "
                    f"{run.status.value:<10} "
                    f"processed={run.schools_processed} ok={run.schools_succeeded} failed={run.schools_failed} "
                    f"cost=${_pipeline_run_cost_usd(run):.6f}"
                )


@cli.command()
@click.option("--city", help="Filter by city")
@click.option("--country", default="bg", help="Country code")
def stats(city, country):
    """Show pipeline statistics."""
    asyncio.run(_show_stats(city, country))


async def _show_stats(city, country):
    """Show pipeline statistics."""
    from sqlalchemy import func, select

    from app.database import async_session_maker
    from app.models import School

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
        console.print("\n[bold cyan]Pipeline Statistics[/bold cyan]")
        console.print(f"Country: {country}")
        if city:
            console.print(f"City: {city}")
        console.print(f"\n[bold]Total schools: {total}[/bold]\n")

        # Status breakdown
        console.print("[bold]By Status:[/bold]")
        for status, count in sorted(status_counts.items()):
            console.print(f"  {status}: {count}")

        # Type breakdown
        console.print("\n[bold]By Type:[/bold]")
        for school_type, count in sorted(type_counts.items()):
            console.print(f"  {school_type}: {count}")


if __name__ == "__main__":
    cli()
