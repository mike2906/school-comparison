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

    # Repair wrong Sofia-oblast coordinates using official MON town metadata
    uv run python -m app.scrapers.cli repair-oblast-geocodes --city sofia
"""
import asyncio
import builtins
import datetime
from contextlib import nullcontext
from collections import defaultdict
from math import ceil, isfinite
import sys
import logging
import random
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse
from typing import Optional
import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

from app.services.pipeline_runs import (
    checkpoint_pipeline_run,
    finalize_pipeline_run,
    pipeline_run_heartbeat,
    stage_is_tracked,
    start_pipeline_run,
    terminalize_stale_pipeline_runs,
)
from app.services.provider_costs import provider_cost_scope
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

BILLABLE_STAGES = frozenset(
    {
        "recover-failed-urls",
        "validate-urls",
        "extract",
        "validate-data",
        "summarize",
        "all",
    }
)

console = Console()


def _stage_summary(processed: int = 0, succeeded: int = 0, failed: int = 0, skipped: int = 0) -> dict:
    """Per-stage school-outcome counts consumed by the PipelineRun lifecycle."""
    return {"processed": processed, "succeeded": succeeded, "failed": failed, "skipped": skipped}


def _add_llm_usage(total: dict, result: dict) -> None:
    """Add one school's bounded usage counters to a batch accumulator."""
    for key in ("input_tokens", "output_tokens"):
        try:
            value = int(result.get(key, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            value = 0
        total[key] += max(0, value)
    try:
        cost = float(result.get("token_cost_usd", 0.0) or 0.0)
    except (TypeError, ValueError, OverflowError):
        cost = 0.0
    if isfinite(cost) and cost >= 0:
        total["token_cost_usd"] += cost


def _stage_summary_with_usage(*, usage: dict, **counts: int) -> dict:
    """Enrich a count summary without changing existing count keys/contracts."""
    return {
        **_stage_summary(**counts),
        "input_tokens": int(usage["input_tokens"]),
        "output_tokens": int(usage["output_tokens"]),
        "token_cost_usd": round(float(usage["token_cost_usd"]), 6),
    }


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


def _navigate_summary(results) -> dict:
    """Convert `_run_navigate_batch`'s per-school results list into a stage summary.

    Navigation keeps returning its results list (a repair command consumes it), so
    the batch dispatch derives run counts here instead of changing that contract.
    """
    rows = builtins.list(results or [])
    succeeded = sum(1 for row in rows if row and row.get("success"))
    return _stage_summary(processed=len(rows), succeeded=succeeded, failed=len(rows) - succeeded)


def _navigation_failure_result(
    *,
    school_id: int,
    url: str | None,
    reason: str,
    timeout_phase: str | None,
    elapsed_seconds: float,
    attempt_count: int,
) -> dict:
    return {
        "school_id": school_id,
        "success": False,
        "reason": reason,
        "final_reason": reason,
        "url": url,
        "page_url": url,
        "timeout_phase": timeout_phase,
        "elapsed_seconds": round(max(0.0, elapsed_seconds), 3),
        "attempt_count": max(1, attempt_count),
    }


async def _persist_navigation_terminal_telemetry(
    db, results: list[dict], urls: dict[int, str | None]
) -> None:
    """Persist final per-school navigation evidence without broadening pipeline work."""
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    from app.models import School

    ids = [
        int(result["school_id"])
        for result in results
        if result and result.get("school_id")
    ]
    if not ids:
        return
    schools = builtins.list((await db.execute(select(School).where(School.id.in_(ids)))).scalars())
    result_by_id = {
        int(result["school_id"]): result
        for result in results
        if result and result.get("school_id")
    }
    for school in schools:
        result = result_by_id[school.id]
        attrs = dict(school.attributes or {})
        if result.get("success"):
            attrs.pop("navigation_terminal_failure", None)
        else:
            attrs["navigation_terminal_failure"] = {
                "url": result.get("url") or urls.get(school.id),
                "page_url": result.get("page_url") or result.get("url") or urls.get(school.id),
                "timeout_phase": result.get("timeout_phase"),
                "elapsed_seconds": float(result.get("elapsed_seconds", 0.0) or 0.0),
                "attempt_count": int(result.get("attempt_count", 1) or 1),
                "final_reason": (
                    result.get("final_reason")
                    or result.get("reason")
                    or "Navigation failed"
                ),
                "recorded_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
        school.attributes = attrs
        flag_modified(school, "attributes")
        db.add(school)
    await db.commit()


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
_CLASS_TEACHER_PAGE_RE = re.compile(r"(класни\s+ръководители|class\s+teachers?)", flags=re.IGNORECASE)
_AGE_GROUP_CLASS_PATTERNS = {
    "preschool": re.compile(r"(?im)^\s*(?:3|4)\.\s*група\b"),
    "grade_1_4": re.compile(r"(?im)^\s*(?:1|2|3|4)\s*[а-яa-z]\b"),
    "grade_5_7": re.compile(r"(?im)^\s*(?:5|6|7)\s*[а-яa-z]\b"),
    "grade_8_12": re.compile(r"(?im)^\s*(?:8|9|10|11|12)\s*[а-яa-z]\b"),
}


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
        _repair_age_groups_command(
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
        _repair_oblast_geocodes_command(
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
        _repair_out_of_bounds_geocodes_command(
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
        report = await _run_shared_site_check(
            db, country=country, city=city, school_ids=school_ids, dry_run=dry_run
        )
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        console.print(f"Report: {report_path}")


async def _run_shared_site_check(
    db,
    *,
    country: str,
    city: Optional[str] = None,
    school_ids: Optional[list[int]] = None,
    dry_run: bool = False,
) -> dict:
    """UF42(a): check websites shared by 2+ institutions; runs after website discovery."""
    from app.scrapers.shared_site_check import run_shared_site_check

    report = await run_shared_site_check(
        db, country=country, city=city, school_ids=school_ids, dry_run=dry_run
    )
    mode = "dry-run" if dry_run else "applied"
    console.print(f"[cyan]Shared-site check ({mode})[/cyan]")
    table = Table(show_header=True, header_style="bold cyan")
    for column in ("Group", "ID", "Level", "Decision", "Reason", "URL"):
        table.add_column(column)
    for group in report["groups"]:
        for row in group["members"]:
            table.add_row(
                group["group"],
                str(row["school_id"]),
                row["education_level"],
                row["action"],
                row["reason"],
                row["new_url"] or row["current_url"],
            )
    if report["groups"]:
        console.print(table)
    counts = report["counts"]
    console.print(
        f"groups={counts['groups']} institutions={counts['institutions']} "
        f"kept={counts['kept']} replaced={counts['replaced']} withheld={counts['withheld']}"
    )
    return report


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
        and stage.lower() in BILLABLE_STAGES
        and provider_cost_cap_usd is None
    ):
        raise click.UsageError(
            "--provider-cost-cap-usd is required for billable stages; "
            "provider calls fail closed without a per-run cap"
        )

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
        console.print(f"  Cohort IDs: {cohort_ids or 'automatic'}")
        console.print(f"  Force validate: {force_validate}")
        console.print(f"  Skip summarize: {skip_summarize}")
        console.print(f"  Provider cost cap: {provider_cost_cap_usd or 'required before execution'}")
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
                skip_summarize,
                cohort_ids,
                provider_cost_cap_usd,
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
    skip_summarize: bool = False,
    cohort_ids: Optional[list[int]] = None,
    provider_cost_cap_usd: Optional[float] = None,
):
    """Run pipeline stage synchronously."""
    from app.config import get_settings
    from app.database import async_session_maker

    if (
        stage in BILLABLE_STAGES
        and provider_cost_cap_usd is None
    ):
        raise ValueError("provider_cost_cap_usd is required for billable stages")

    async with async_session_maker() as db:
        if school_name or school_id:
            # Single school mode
            if school_name:
                school_id = await _find_school_by_name(db, school_name, country)
                if not school_id:
                    console.print(f"[red]School not found: {school_name}[/red]")
                    return

            console.print(f"[cyan]Running stage '{stage}' for school ID {school_id}[/cyan]")
            single_run = None
            if stage_is_tracked(stage):
                single_run = await start_pipeline_run(
                    db,
                    country=country,
                    city=city,
                    cli_stage=stage,
                    config={
                        "school_id": school_id,
                        "provider_cost_cap_usd": provider_cost_cap_usd,
                    },
                )
            heartbeat_context = (
                pipeline_run_heartbeat(
                    single_run.id,
                    session_factory=async_session_maker,
                    interval_seconds=float(get_settings().pipeline_heartbeat_interval_seconds),
                )
                if single_run is not None
                else nullcontext()
            )
            cost_context = (
                provider_cost_scope(
                    pipeline_run_id=single_run.id,
                    stage=stage,
                    cap_usd=provider_cost_cap_usd,
                    request_reserve_usd=float(get_settings().provider_request_reserve_usd),
                )
                if single_run is not None and provider_cost_cap_usd is not None
                else nullcontext()
            )
            single_error: Optional[str] = None
            with cost_context:
                async with heartbeat_context:
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
                                await _run_shared_site_check(db, country=country, school_ids=[school_id])
                            elif stage == "recover-failed-urls":
                                await _run_recover_failed_school(db, school_id, country)
                                await _run_shared_site_check(db, country=country, school_ids=[school_id])
                            elif stage == "validate-urls":
                                await _run_validate_url(db, school_id, country)
                            elif stage == "navigate":
                                await _run_navigate_school(db, school_id, country)
                            elif stage == "extract":
                                await _run_extract_school(db, school_id, country)
                            elif stage == "validate-data":
                                await _run_validate_data_school(
                                    db, school_id, country, run_spot_check=True
                                )
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
                        except Exception as exc:
                            single_error = str(exc)
                            console.print(f"[red]✗ Error: {single_error}[/red]")
                            logger.exception("Stage execution failed")
                            if db.in_transaction():
                                await db.rollback()
                        finally:
                            if single_run is not None:
                                await finalize_pipeline_run(
                                    db,
                                    single_run,
                                    country=country,
                                    city=city,
                                    stage_summaries=[
                                        _stage_summary(
                                            processed=1,
                                            succeeded=0 if single_error else 1,
                                            failed=1 if single_error else 0,
                                        )
                                    ],
                                    error_summary=single_error,
                                )
                                console.print(
                                    f"[dim]Recorded pipeline run {single_run.id} "
                                    f"({single_run.status.value}).[/dim]"
                                )

        else:
            # Batch mode
            console.print(f"[cyan]Running stage '{stage}' in batch mode[/cyan]")
            console.print(f"  City: {city}")
            console.print(f"  Limit: {limit or 'all'}")

            # Record a PipelineRun (+ quality-metrics snapshot) around tracked stages.
            run = None
            if stage_is_tracked(stage):
                run = await start_pipeline_run(
                    db,
                    country=country,
                    city=city,
                    cli_stage=stage,
                    config={
                        "limit": limit,
                        "include_navigated": include_navigated,
                        "include_extracted": include_extracted,
                        "force_validate": force_validate,
                        "skip_summarize": skip_summarize,
                        "explicit_cohort": cohort_ids is not None,
                        "provider_cost_cap_usd": provider_cost_cap_usd,
                    },
                )

            stage_summaries: list = []
            run_error: Optional[str] = None
            raised: Optional[BaseException] = None
            heartbeat_context = (
                pipeline_run_heartbeat(
                    run.id,
                    session_factory=async_session_maker,
                    interval_seconds=float(get_settings().pipeline_heartbeat_interval_seconds),
                )
                if run is not None
                else nullcontext()
            )
            cost_context = (
                provider_cost_scope(
                    pipeline_run_id=run.id,
                    stage=stage,
                    cap_usd=provider_cost_cap_usd,
                    request_reserve_usd=float(get_settings().provider_request_reserve_usd),
                )
                if run is not None and provider_cost_cap_usd is not None
                else nullcontext()
            )
            with cost_context:
                async with heartbeat_context:
                    try:
                        if stage == "discover":
                            stage_summaries.append(
                                await _run_discover_batch(db, country, city, limit, sample_ratio)
                            )
                        elif stage == "discover-websites":
                            stage_summaries.append(await _run_discover_websites_batch(db, country, city, limit))
                        elif stage == "recover-failed-urls":
                            stage_summaries.append(await _run_recover_failed_urls_batch(db, country, city, limit))
                        elif stage == "validate-urls":
                            stage_summaries.append(await _run_validate_urls_batch(db, country, city, limit))
                        elif stage == "navigate":
                            stage_summaries.append(
                                _navigate_summary(
                                    await _run_navigate_batch(
                                        db, country, city, limit, include_navigated=include_navigated
                                    )
                                )
                            )
                        elif stage == "extract":
                            stage_summaries.append(
                                await _run_extract_batch(
                                    db, country, city, limit, include_extracted=include_extracted
                                )
                            )
                        elif stage == "validate-data":
                            stage_summaries.append(
                                await _run_validate_data_batch(
                                    db, country, city, limit, force_validate=force_validate
                                )
                            )
                        elif stage == "summarize":
                            stage_summaries.append(await _run_summarize_batch(db, country, city, limit))
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
                            await _run_all_stages_batch(
                                db,
                                country=country,
                                city=city,
                                limit=limit,
                                include_navigated=include_navigated,
                                include_extracted=include_extracted,
                                force_validate=force_validate,
                                skip_summarize=skip_summarize,
                                requested_school_ids=cohort_ids,
                                pipeline_run=run,
                                stage_summaries=stage_summaries,
                            )
                        else:
                            console.print(f"[yellow]Batch mode for '{stage}' not yet implemented[/yellow]")
                    except Exception as exc:
                        raised = exc
                        run_error = str(exc)
                        logger.exception("Batch stage execution failed")
                        console.print(f"[red]✗ Error: {run_error}[/red]")
                        # A stage that raised mid-transaction leaves the session unusable;
                        # clear it so the run can still be finalized.
                        if db.in_transaction():
                            await db.rollback()
                    finally:
                        if run is not None:
                            await finalize_pipeline_run(
                                db,
                                run,
                                country=country,
                                city=city,
                                stage_summaries=stage_summaries,
                                error_summary=run_error,
                            )
                            console.print(f"[dim]Recorded pipeline run {run.id} ({run.status.value}).[/dim]")
            # Batch mode is the automation path: a stage failure must exit non-zero.
            if raised is not None:
                raise raised


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


def _sorted_age_groups(age_groups: set[str] | list[str]) -> list[str]:
    return sorted(set(age_groups), key=lambda value: (_AGE_GROUP_ORDER.get(value, 99), value))


def _select_age_group_repair_location(school):
    locations = builtins.list(school.locations or [])
    if not locations:
        return None
    if len(locations) == 1:
        return locations[0]

    primary = _primary_location_for_school(school)
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
            if any(pattern.search(normalized_text) for pattern in patterns):
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


def _reverse_result_tokens(payload: dict | None) -> set[str]:
    address = payload.get("address") if isinstance(payload, dict) else None
    if not isinstance(address, dict):
        return set()

    keys = ("city", "town", "village", "municipality", "county", "state_district", "state", "suburb")
    return {
        value.strip().lower()
        for key in keys
        if isinstance((value := address.get(key)), str) and value.strip()
    }


def _expected_locality_tokens(*values: Optional[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        if isinstance(value, str) and value.strip():
            tokens.add(value.strip().lower())
    return tokens


def _is_sofia_oblast_school(school) -> bool:
    attrs = dict(school.attributes or {})
    return attrs.get("moe_region_code") == 23 or attrs.get("moe_region_name") == "София-област"


def _location_out_of_bounds(location, bounds: dict[str, float]) -> bool:
    if location.lat is None or location.lng is None:
        return False
    try:
        lat = float(location.lat)
        lng = float(location.lng)
    except (TypeError, ValueError):
        return False

    from app.services.geocoding.bounds import point_in_bounds

    return not point_in_bounds(lat, lng, bounds)


def _out_of_bounds_repair_address_candidates(school, location) -> list[str]:
    attrs = dict(school.attributes or {})
    extracted = attrs.get("extracted") if isinstance(attrs.get("extracted"), dict) else {}
    contact = extracted.get("contact") if isinstance(extracted.get("contact"), dict) else {}

    raw_candidates: list[object] = [
        contact.get("address"),
        *(contact.get("addresses") if isinstance(contact.get("addresses"), builtins.list) else []),
    ]
    address_i18n = dict(location.address_i18n or {})
    raw_candidates.extend([address_i18n.get("bg"), address_i18n.get("en")])

    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in raw_candidates:
        if not isinstance(candidate, str):
            continue
        text = re.sub(r"\s+", " ", candidate).strip(" ,")
        if not text:
            continue
        variants = [
            text,
            re.sub(r",?\s*(?:п\.?\s*к\.?|ПК)\s*\d{4}\b", "", text, flags=re.IGNORECASE).strip(" ,"),
        ]
        for variant in variants:
            if not variant:
                continue
            key = variant.casefold()
            if key in seen:
                continue
            seen.add(key)
            candidates.append(variant)
    return candidates


def _sanitize_oblast_query_text(value: Optional[str]) -> str:
    if not isinstance(value, str):
        return ""

    sanitized = value
    sanitized = sanitized.replace("№", " ")
    sanitized = re.sub(r'["„“”]', " ", sanitized)
    sanitized = re.sub(r"\b[Гг][Рр]\.\s*", "", sanitized)
    sanitized = re.sub(r"\b[Сс]\.\s*", "", sanitized)
    sanitized = re.sub(r"\bобщ\.\s*", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bобщина\b", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bобл\.?\s*[^,]+", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bет\.\s*\d+\b", "", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bх\.\s*", "хаджи ", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\bСв\.\s*Св\.\s*", "Свети Свети ", sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"([A-Za-zА-Яа-я])([IVX]{1,4})(\d)", r"\1 \2 \3", sanitized)
    sanitized = re.sub(r"([A-Za-zА-Яа-я])(\d)", r"\1 \2", sanitized)
    sanitized = re.sub(r"(\d)([A-Za-zА-Яа-я])", r"\1 \2", sanitized)
    sanitized = re.sub(r"\s*-\s*", " ", sanitized)
    sanitized = re.sub(r"\s*,\s*", ", ", sanitized)
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized.strip(" ,")


def _abbreviate_school_query_name(value: Optional[str]) -> str:
    sanitized = _sanitize_oblast_query_text(value)
    replacements = [
        (r"\bГимназия с преподаване на чужди езици\b", "ГПЧЕ"),
        (r"\bПрофесионална гимназия\b", "ПГ"),
        (r"\bПрофилирана гимназия\b", "ПГ"),
        (r"\bСредно училище\b", "СУ"),
        (r"\bОсновно училище\b", "ОУ"),
        (r"\bНачално училище\b", "НУ"),
    ]
    for pattern, replacement in replacements:
        sanitized = re.sub(pattern, replacement, sanitized, flags=re.IGNORECASE)
    sanitized = re.sub(r"\s+", " ", sanitized)
    return sanitized.strip(" ,")


def _oblast_fallback_query_specs(
    *,
    address: str,
    school_name: Optional[str],
    town_name: Optional[str],
    municipality_name: Optional[str],
) -> list[tuple[str, str, set[str]]]:
    from app.services.geocoding.bg.geojson import GeoJSONProvider
    from app.services.geocoding.nominatim import NominatimProvider

    provider = NominatimProvider()
    geojson_provider = GeoJSONProvider()
    normalized_address = provider._normalize_bulgarian_address(address or "")
    sanitized_address = _sanitize_oblast_query_text(normalized_address)
    sanitized_town = _sanitize_oblast_query_text(town_name)
    sanitized_municipality = _sanitize_oblast_query_text(municipality_name)
    sanitized_school = _sanitize_oblast_query_text(school_name)
    abbreviated_school = _sanitize_oblast_query_text(
        geojson_provider._normalize_name(school_name or "").title()
    )
    acronym_school = _abbreviate_school_query_name(school_name)

    road_types = {
        "road",
        "residential",
        "tertiary",
        "secondary",
        "primary",
        "unclassified",
        "service",
        "pedestrian",
    }
    poi_types = {"school", "kindergarten", "college", "university"}
    locality_types = {"administrative", "village", "town", "hamlet", "suburb"}

    specs: list[tuple[str, str, set[str]]] = []
    seen: set[tuple[str, str]] = set()

    def add(query: str, precision: str, allowed_types: set[str]) -> None:
        cleaned = _sanitize_oblast_query_text(query)
        if not cleaned:
            return
        key = (cleaned.casefold(), precision)
        if key in seen:
            return
        seen.add(key)
        specs.append((cleaned, precision, allowed_types))

    if sanitized_school and sanitized_town:
        add(f"{sanitized_school}, {sanitized_town}", "poi", poi_types)
    if abbreviated_school and abbreviated_school != sanitized_school and sanitized_town:
        add(f"{abbreviated_school}, {sanitized_town}", "poi", poi_types)
    if acronym_school and acronym_school not in {sanitized_school, abbreviated_school} and sanitized_town:
        add(f"{acronym_school}, {sanitized_town}", "poi", poi_types)

    address_tokens = [token.strip() for token in sanitized_address.split(",") if token.strip()]
    locality_tokens = {
        token.casefold()
        for token in (sanitized_town, sanitized_municipality)
        if token
    }
    detail_tokens = [token for token in address_tokens if token.casefold() not in locality_tokens]
    detail_query = ", ".join(detail_tokens)
    detail_without_number = re.sub(r"\b\d+[A-Za-zА-Яа-я-]*\b", "", detail_query).strip(" ,")
    detail_without_number = re.sub(r"\s+", " ", detail_without_number).strip(" ,")

    if sanitized_town and detail_without_number:
        add(f"{sanitized_town} {detail_without_number}", "street", road_types)
    if sanitized_town and detail_query and detail_query != detail_without_number:
        add(f"{sanitized_town} {detail_query}", "street", road_types)

    address_is_locality_only = not re.search(r"\d", sanitized_address) and (
        not detail_query or detail_query.casefold() in locality_tokens
    )
    if sanitized_town and address_is_locality_only:
        if sanitized_municipality and sanitized_municipality.casefold() != sanitized_town.casefold():
            add(f"{sanitized_town}, {sanitized_municipality}", "locality", locality_types)
        add(sanitized_town, "locality", locality_types)

    return specs


async def _search_nominatim_query(client, *, query: str, country_code: str = "bg") -> list[dict]:
    response = await client.get(
        "https://nominatim.openstreetmap.org/search",
        params={
            "format": "jsonv2",
            "q": query,
            "limit": 3,
            "addressdetails": 1,
            "countrycodes": country_code,
        },
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, builtins.list) else []


async def _load_moe_lookup_maps() -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    import httpx

    from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }

    async def load_map(client: httpx.AsyncClient, url: str) -> dict[int, str]:
        try:
            response = await client.post(url, json={}, headers=headers)
            response.raise_for_status()
            return MoeRegistryAdapter._extract_code_label_map(response.json())
        except Exception as exc:
            logger.warning("Failed to load MoE lookup labels from %s: %s", url, exc)
            return {}

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        return (
            await load_map(client, MoeRegistryAdapter.REGIONS_URL),
            await load_map(client, MoeRegistryAdapter.MUNICIPALITIES_URL),
            await load_map(client, MoeRegistryAdapter.TOWNS_URL),
        )


async def _reverse_geocode_payload(client, *, lat: float, lng: float) -> dict:
    response = await client.get(
        "https://nominatim.openstreetmap.org/reverse",
        params={
            "format": "jsonv2",
            "lat": lat,
            "lon": lng,
            "zoom": 18,
            "addressdetails": 1,
        },
    )
    response.raise_for_status()
    return response.json()


async def _repair_oblast_geocodes_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    dry_run: bool,
):
    import httpx
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.config import get_settings
    from app.database import async_session_maker
    from app.models import School
    from app.services.geocoding.service import GeocodingService, nominatim_user_agent
    from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter

    settings = get_settings()
    user_agent = nominatim_user_agent(settings)

    async with async_session_maker() as db:
        if school_name and not school_id:
            school_id = await _find_school_by_name(db, school_name, country)
            if not school_id:
                console.print(f"[red]School not found: {school_name}[/red]")
                return

        region_map, municipality_map, town_map = await _load_moe_lookup_maps()

        query = (
            select(School)
            .options(selectinload(School.locations))
            .where(
                School.country_code == country,
                School.attributes["moe_region_code"].as_integer() == MoeRegistryAdapter.SOFIA_OBLAST_REGION,
            )
        )
        if city:
            query = query.where(School.city == city)
        if school_id:
            query = query.where(School.id == school_id)
        if limit:
            query = query.limit(limit)

        schools = (await db.execute(query)).scalars().all()
        if not schools:
            console.print("[yellow]No Sofia-oblast schools matched the repair filters.[/yellow]")
            return

        candidate_rows: list[dict[str, object]] = []
        reverse_client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        )

        try:
            for school in schools:
                attrs = dict(school.attributes or {})
                region_code = attrs.get("moe_region_code")
                municipality_code = attrs.get("moe_municipality_code")
                town_code = attrs.get("moe_town_code")

                region_name = (
                    region_map.get(region_code) or attrs.get("moe_region_name")
                    if isinstance(region_code, int)
                    else attrs.get("moe_region_name")
                )
                municipality_name = (
                    municipality_map.get(municipality_code) or attrs.get("moe_municipality_name")
                    if isinstance(municipality_code, int)
                    else attrs.get("moe_municipality_name")
                )
                town_name = (
                    town_map.get(town_code) or attrs.get("moe_town_name")
                    if isinstance(town_code, int)
                    else attrs.get("moe_town_name")
                )

                attrs["moe_region_name"] = region_name
                attrs["moe_municipality_name"] = municipality_name
                attrs["moe_town_name"] = town_name
                school.attributes = attrs

                for location in builtins.list(school.locations or []):
                    address_i18n = dict(location.address_i18n or {})
                    address_bg = address_i18n.get("bg") or address_i18n.get("en") or ""
                    expected_city = MoeRegistryAdapter._preferred_geocoding_city(school.city, attrs)
                    normalized_address = MoeRegistryAdapter._address_with_locality_hint(address_bg, expected_city)

                    reasons: list[str] = []
                    reverse_display = ""
                    reverse_tokens: set[str] = set()
                    expected_tokens = _expected_locality_tokens(town_name, municipality_name)

                    if normalized_address and normalized_address != address_bg:
                        address_i18n["bg"] = normalized_address
                        address_i18n.pop("en", None)
                        location.address_i18n = address_i18n
                        reasons.append("address-prefixed-with-town")

                    if location.lat is None or location.lng is None:
                        reasons.append("missing-coords")
                    else:
                        payload = await _reverse_geocode_payload(client=reverse_client, lat=float(location.lat), lng=float(location.lng))
                        reverse_display = payload.get("display_name") or ""
                        reverse_tokens = _reverse_result_tokens(payload)
                        if expected_tokens and not reverse_tokens.intersection(expected_tokens):
                            reasons.append("reverse-locality-mismatch")
                        await asyncio.sleep(1.05)

                    if reasons:
                        candidate_rows.append(
                            {
                                "school_id": school.id,
                                "location_id": location.id,
                                "school_label": _school_label(school),
                                "reasons": reasons,
                                "expected_city": expected_city or "",
                                "address": dict(location.address_i18n or {}).get("bg") or "",
                                "reverse_display": reverse_display,
                            }
                        )
        finally:
            await reverse_client.aclose()

        if not candidate_rows:
            console.print("[green]No Sofia-oblast coordinate repair candidates found.[/green]")
            if not dry_run:
                await db.commit()
            return

        console.print(f"[cyan]Found {len(candidate_rows)} Sofia-oblast geocode repair candidates[/cyan]")
        preview_table = Table(title="Oblast Geocode Repair Candidates")
        preview_table.add_column("School ID", style="cyan", no_wrap=True)
        preview_table.add_column("Location ID", style="cyan", no_wrap=True)
        preview_table.add_column("School")
        preview_table.add_column("Reasons")
        preview_table.add_column("Expected locality")
        preview_table.add_column("Address")
        for row in candidate_rows[:20]:
            preview_table.add_row(
                str(row["school_id"]),
                str(row["location_id"]),
                str(row["school_label"]),
                ", ".join(builtins.list(row["reasons"])),
                str(row["expected_city"]),
                str(row["address"]),
            )
        console.print(preview_table)
        if len(candidate_rows) > 20:
            console.print(f"[dim]Showing first 20 of {len(candidate_rows)} repair candidates[/dim]")

        if dry_run:
            return

        geocoder = GeocodingService(db)
        repaired = 0
        cleared = 0
        failed = 0

        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": user_agent},
        ) as verify_client:
            for row in candidate_rows:
                school = next((item for item in schools if item.id == row["school_id"]), None)
                if school is None:
                    continue
                location = next((item for item in builtins.list(school.locations or []) if item.id == row["location_id"]), None)
                if location is None:
                    continue

                result = await geocoder.geocode_location(location, force=True, country_code=country)
                if result.success and result.lat is not None and result.lng is not None:
                    expected_tokens = _expected_locality_tokens(str(row["expected_city"]))
                    if expected_tokens:
                        payload = await _reverse_geocode_payload(
                            client=verify_client,
                            lat=float(result.lat),
                            lng=float(result.lng),
                        )
                        reverse_tokens = _reverse_result_tokens(payload)
                        await asyncio.sleep(1.05)
                        if not reverse_tokens.intersection(expected_tokens):
                            location.lat = None
                            location.lng = None
                            tags = builtins.list(location.location_tags or [])
                            marker = "coords_cleared=oblast_locality_mismatch"
                            if marker not in tags:
                                tags.append(marker)
                            location.location_tags = tags
                            db.add(location)
                            await db.commit()
                            cleared += 1
                            continue
                    marker = "coords_cleared=oblast_locality_mismatch"
                    tags = [tag for tag in builtins.list(location.location_tags or []) if str(tag) != marker]
                    if tags != builtins.list(location.location_tags or []):
                        location.location_tags = tags
                        db.add(location)
                        await db.commit()
                    repaired += 1
                    continue

                if "reverse-locality-mismatch" in builtins.list(row["reasons"]):
                    location.lat = None
                    location.lng = None
                    tags = builtins.list(location.location_tags or [])
                    marker = "coords_cleared=oblast_locality_mismatch"
                    if marker not in tags:
                        tags.append(marker)
                    location.location_tags = tags
                    db.add(location)
                    await db.commit()
                    cleared += 1
                else:
                    attrs = dict(school.attributes or {})
                    fallback_specs = _oblast_fallback_query_specs(
                        address=str(row["address"]),
                        school_name=_school_label(school),
                        town_name=attrs.get("moe_town_name"),
                        municipality_name=attrs.get("moe_municipality_name"),
                    )
                    expected_tokens = _expected_locality_tokens(
                        attrs.get("moe_town_name"),
                        attrs.get("moe_municipality_name"),
                    )
                    fallback_match = None

                    for query, precision, allowed_types in fallback_specs:
                        search_results = await _search_nominatim_query(
                            verify_client,
                            query=query,
                            country_code=country,
                        )
                        await asyncio.sleep(1.05)
                        for item in search_results:
                            result_tokens = _reverse_result_tokens(item)
                            if expected_tokens and not result_tokens.intersection(expected_tokens):
                                continue
                            result_type = str(item.get("type") or "").strip().lower()
                            if allowed_types and result_type not in allowed_types:
                                continue
                            lat = item.get("lat")
                            lng = item.get("lon")
                            if lat is None or lng is None:
                                continue
                            try:
                                fallback_match = (float(lat), float(lng), precision)
                            except (TypeError, ValueError):
                                continue
                            break
                        if fallback_match:
                            break

                    if fallback_match:
                        lat, lng, precision = fallback_match
                        location.lat = lat
                        location.lng = lng
                        tags = [
                            tag
                            for tag in builtins.list(location.location_tags or [])
                            if not str(tag).startswith("coords_precision=")
                            and str(tag) != "coords_source=nominatim_approximate"
                            and str(tag) != "coords_cleared=oblast_locality_mismatch"
                        ]
                        tags.append("coords_source=nominatim_approximate")
                        tags.append(f"coords_precision={precision}")
                        location.location_tags = tags
                        db.add(location)
                        await db.commit()
                        repaired += 1
                    else:
                        failed += 1

        console.print(
            f"[green]Oblast repair complete[/green] repaired={repaired} cleared={cleared} failed={failed}"
        )


async def _repair_out_of_bounds_geocodes_command(
    *,
    school_name: Optional[str],
    school_id: Optional[int],
    city: Optional[str],
    country: str,
    limit: Optional[int],
    dry_run: bool,
):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.config import get_settings
    from app.database import async_session_maker
    from app.models import School
    from app.services.geocoding.bounds import get_city_bounds, point_in_bounds
    from app.services.geocoding.nominatim import NominatimProvider
    from app.services.geocoding.service import nominatim_user_agent

    bounds = get_city_bounds(country, city)
    if bounds is None:
        console.print(f"[yellow]No configured city bounds for {country}/{city}; nothing to repair.[/yellow]")
        return

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
        candidate_rows: list[dict[str, object]] = []
        skipped_oblast = 0

        for school in schools:
            if country == "bg" and (city or "").casefold() == "sofia" and _is_sofia_oblast_school(school):
                skipped_oblast += 1
                continue
            for location in builtins.list(school.locations or []):
                if not _location_out_of_bounds(location, bounds):
                    continue
                address_candidates = _out_of_bounds_repair_address_candidates(school, location)
                candidate_rows.append(
                    {
                        "school": school,
                        "location": location,
                        "school_id": school.id,
                        "location_id": location.id,
                        "school_label": _school_label(school),
                        "current": f"{location.lat}, {location.lng}",
                        "address": address_candidates[0] if address_candidates else "",
                        "address_candidates": address_candidates,
                    }
                )

        if not candidate_rows:
            console.print("[green]No out-of-bounds city geocode repair candidates found.[/green]")
            if skipped_oblast:
                console.print(f"[dim]Skipped {skipped_oblast} Sofia-oblast schools outside the Sofia municipality scope.[/dim]")
            return

        console.print(f"[cyan]Found {len(candidate_rows)} out-of-bounds city geocode repair candidates[/cyan]")
        if skipped_oblast:
            console.print(f"[dim]Skipped {skipped_oblast} Sofia-oblast schools outside the Sofia municipality scope.[/dim]")

        preview_table = Table(title="Out-of-Bounds Geocode Repair Candidates")
        preview_table.add_column("School ID", style="cyan", no_wrap=True)
        preview_table.add_column("Location ID", style="cyan", no_wrap=True)
        preview_table.add_column("School")
        preview_table.add_column("Current")
        preview_table.add_column("First repair address")
        for row in candidate_rows[:20]:
            preview_table.add_row(
                str(row["school_id"]),
                str(row["location_id"]),
                str(row["school_label"]),
                str(row["current"]),
                str(row["address"]),
            )
        console.print(preview_table)
        if len(candidate_rows) > 20:
            console.print(f"[dim]Showing first 20 of {len(candidate_rows)} repair candidates[/dim]")

        if dry_run:
            return

        geocoder = NominatimProvider(user_agent=nominatim_user_agent(get_settings()))
        repaired = 0
        failed = 0

        for row in candidate_rows:
            location = row["location"]
            if location is None:
                continue
            match = None
            for address in builtins.list(row["address_candidates"]):
                result = await geocoder.geocode(
                    address=str(address),
                    country_code=country,
                    city=city,
                    institution_name=(row["school"].name_i18n or {}).get("bg"),
                )
                if not result.success or result.lat is None or result.lng is None:
                    continue
                if not point_in_bounds(float(result.lat), float(result.lng), bounds):
                    logger.warning(
                        "Rejected repaired geocode outside bounds for location %s: %s, %s from %s",
                        getattr(location, "id", None),
                        result.lat,
                        result.lng,
                        address,
                    )
                    continue
                match = (result, str(address))
                break

            if not match:
                failed += 1
                continue

            result, matched_address = match
            location.lat = result.lat
            location.lng = result.lng
            tags = [
                tag
                for tag in builtins.list(location.location_tags or [])
                if not str(tag).startswith("coords_source=")
                and not str(tag).startswith("coords_repair=")
            ]
            tags.extend(
                [
                    "coords_source=nominatim_repair",
                    "coords_repair=out_of_bounds",
                ]
            )
            location.location_tags = tags
            db.add(location)
            await db.commit()
            console.print(
                f"[green]Repaired location {location.id}[/green] → "
                f"({result.lat}, {result.lng}) from {matched_address}"
            )
            repaired += 1

        console.print(f"[green]Out-of-bounds repair complete[/green] repaired={repaired} failed={failed}")


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
        pages_by_school: dict[int, list[dict[str, object]]] = defaultdict(builtins.list)
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


async def _repair_age_groups_command(
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
            school_id = await _find_school_by_name(db, school_name, country)
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
                await _run_navigate_batch(
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
                    _school_label(school),
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
            # Recovery sets new URLs; they go through the shared-site check like discovery.
            await _run_shared_site_check(db, country=country, school_ids=failed_validate_ids)

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
        return _stage_summary()

    console.print(f"[cyan]Found {len(adapters)} adapter(s)[/cyan]")

    created_total = 0
    updated_total = 0
    skipped_total = 0
    failed_adapters = 0
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

                created_total += int(result["created"])
                updated_total += int(result["updated"])
                skipped_total += int(result["skipped"])
                console.print(f"[green]✓ Discovery complete:[/green]")
                console.print(f"  Created: {result['created']}")
                console.print(f"  Updated: {result['updated']}")
                console.print(f"  Skipped: {result['skipped']}")

            except Exception as e:
                console.print(f"[red]✗ Error: {str(e)}[/red]")
                logger.exception("Discovery failed")
                # Discovery has no per-school unit that can fail, so count the whole
                # crashed adapter — otherwise a run whose only adapter throws records
                # COMPLETED with all-zero counts.
                failed_adapters += 1

    return _stage_summary(
        processed=created_total + updated_total + skipped_total + failed_adapters,
        succeeded=created_total + updated_total,
        failed=failed_adapters,
        skipped=skipped_total,
    )


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

    query = select(School.id, School.website_url, School.name_i18n, School.attributes).where(
        School.country_code == country,
        School.website_url.isnot(None),
    )

    if school_ids is None:
        statuses = statuses or ["pending", "failed_validate"]
        query = query.where(School.scrape_status.in_(statuses))
    else:
        query = query.where(School.id.in_(school_ids))

    if city:
        query = query.where(School.city == city)

    if limit:
        query = query.limit(limit)

    result = await db.execute(query)
    schools = result.all()

    if not schools:
        console.print("[yellow]No schools to validate[/yellow]")
        return {**_stage_summary(), "validated_school_ids": []}

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
        usage = {"input_tokens": 0, "output_tokens": 0, "token_cost_usd": 0.0}
        semaphore = asyncio.Semaphore(concurrency)

        async def _validate_single(
            school_id: int,
            website_url: str,
            school_name: Optional[str],
            school_aliases: list[str],
        ) -> tuple[int, str, dict]:
            async with semaphore:
                school_usage: dict = {}
                try:
                    result, _, _ = await validate_school_url(
                        school_id=school_id,
                        url=website_url,
                        country_code=country,
                        update_db=True,
                        school_name=school_name,
                        school_aliases=school_aliases,
                        usage_out=school_usage,
                    )
                    return school_id, result.value, school_usage
                except Exception as exc:
                    logger.error(f"Error validating school {school_id}: {exc}")
                    return school_id, "error", school_usage

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

        validated_school_ids: list[int] = []
        for completed in asyncio.as_completed(tasks):
            school_id, outcome, school_usage = await completed
            _add_llm_usage(usage, school_usage)
            if outcome == "valid":
                valid_count += 1
                validated_school_ids.append(school_id)
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

    return {
        **_stage_summary_with_usage(
            usage=usage,
            processed=len(schools),
            succeeded=valid_count,
            failed=invalid_count + error_count,
            skipped=ambiguous_count,
        ),
        "validated_school_ids": sorted(validated_school_ids),
    }


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
        return _stage_summary()

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

    # Recovery rediscovers websites too, so it gets the same shared-site check.
    await _run_shared_site_check(db, country=country, school_ids=school_ids)

    return _stage_summary(
        processed=len(school_ids),
        succeeded=validated_count,
        failed=still_failed_count,
        skipped=terminal_count + skipped_count,
    )


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
        return _stage_summary()

    console.print(f"[cyan]Discovering websites for {len(schools)} schools...[/cyan]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Discovering websites...", total=len(schools))

        found_count = 0
        updated_count = 0
        error_count = 0

        for school in schools:
            try:
                result = await _run_discover_website(db, school.id, country)
                if result and result.get("found"):
                    found_count += 1
                if result and result.get("updated"):
                    updated_count += 1
            except Exception as e:
                error_count += 1
                logger.error(f"Error discovering website for school {school.id}: {e}")
            progress.update(task, advance=1)

    console.print(f"[green]✓ Website discovery complete:[/green]")
    console.print(f"  Found: {found_count}")
    console.print(f"  Updated: {updated_count}")
    console.print(f"  Unchanged: {len(schools) - updated_count}")

    await _run_shared_site_check(db, country=country, school_ids=[school.id for school in schools])

    return _stage_summary(
        processed=len(schools),
        succeeded=found_count,
        failed=error_count,
        skipped=len(schools) - found_count - error_count,
    )


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

    explicit_selection = school_ids is not None
    explicit_school_ids = builtins.list(school_ids or [])
    if explicit_selection:
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
    school_urls = {school.id: school.website_url for school in schools}

    if not school_ids:
        console.print("[yellow]No schools to navigate[/yellow]")
        return

    if explicit_selection:
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

        async def _navigate_chunk_individually(
            school_chunk: list[int],
            *,
            prior_phase: str,
            prior_elapsed: float,
        ) -> list[dict]:
            isolated: list[dict] = []
            for isolated_school_id in school_chunk:
                started = time.monotonic()
                try:
                    coro = _run_navigate_school(db, isolated_school_id, country)
                    if _nav_timeout > 0:
                        item = await asyncio.wait_for(coro, timeout=_nav_timeout)
                    else:
                        item = await coro
                    item = dict(item or {})
                    item.setdefault("school_id", isolated_school_id)
                    item.setdefault("url", school_urls.get(isolated_school_id))
                    item.setdefault("page_url", item.get("url"))
                    item.setdefault("elapsed_seconds", round(time.monotonic() - started, 3))
                    item.setdefault("attempt_count", 2)
                    item.setdefault(
                        "timeout_phase", prior_phase if not item.get("success") else None
                    )
                    item.setdefault("final_reason", item.get("reason"))
                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - started
                    logger.error(
                        "Navigation timed out after %.0fs for school %s", _nav_timeout, isolated_school_id
                    )
                    await db.rollback()
                    item = _navigation_failure_result(
                        school_id=isolated_school_id,
                        url=school_urls.get(isolated_school_id),
                        reason="Navigation timeout",
                        timeout_phase="school_retry",
                        elapsed_seconds=prior_elapsed + elapsed,
                        attempt_count=2,
                    )
                except Exception as exc:
                    elapsed = time.monotonic() - started
                    logger.error("Error navigating school %s: %s", isolated_school_id, exc)
                    await db.rollback()
                    item = _navigation_failure_result(
                        school_id=isolated_school_id,
                        url=school_urls.get(isolated_school_id),
                        reason=str(exc),
                        timeout_phase=(
                            prior_phase if "timeout" in str(exc).lower() else None
                        ),
                        elapsed_seconds=prior_elapsed + elapsed,
                        attempt_count=2,
                    )
                isolated.append(item)
            return isolated

        if skip_timed_out_chunks and explicit_school_ids:
            timed_out_school_ids: list[int] = []
            for chunk_start in range(0, len(school_ids), nav_batch_concurrency):
                school_chunk = school_ids[chunk_start : chunk_start + nav_batch_concurrency]
                chunk_started = time.monotonic()
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
                    chunk_elapsed = time.monotonic() - chunk_started
                    logger.error(
                        "Navigation timed out after %.0fs for school chunk %s",
                        _nav_timeout,
                        school_chunk,
                    )
                    await db.rollback()
                    chunk_results = await _navigate_chunk_individually(
                        school_chunk,
                        prior_phase="batch_chunk",
                        prior_elapsed=chunk_elapsed,
                    )
                    timed_out_school_ids.extend(
                        int(item["school_id"])
                        for item in chunk_results
                        if not item.get("success") and item.get("timeout_phase")
                    )
                except Exception as exc:
                    logger.warning(
                        "Batch navigation chunk failed for %s; retrying sequentially: %s",
                        school_chunk,
                        exc,
                    )
                    await db.rollback()
                    chunk_results = await _navigate_chunk_individually(
                        school_chunk,
                        prior_phase="batch_chunk_error",
                        prior_elapsed=time.monotonic() - chunk_started,
                    )

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
    await _persist_navigation_terminal_telemetry(db, results, school_urls)
    return results


async def _run_all_stages(db, school_id: int, country: str):
    """Run all pipeline stages for a single school."""
    stages = ["discover-websites", "validate-urls", "navigate", "extract", "validate-data", "summarize"]

    for stage in stages:
        console.print(f"\n[cyan]Stage: {stage}[/cyan]")

        if stage == "discover-websites":
            await _run_discover_website(db, school_id, country)
            await _run_shared_site_check(db, country=country, school_ids=[school_id])
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


async def _select_all_stage_cohort(
    db,
    *,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    include_navigated: bool,
    include_extracted: bool,
    force_validate: bool,
    requested_school_ids: Optional[list[int]] = None,
) -> list[int]:
    """Select one deterministic cohort for every stage in a batch ``all`` run."""
    from app.models import School
    from sqlalchemy import select

    query = (
        select(School.id)
        .where(
            School.country_code == country,
            School.website_url.isnot(None),
        )
        .order_by(School.id)
    )
    if city:
        query = query.where(School.city == city)
    if requested_school_ids is not None:
        query = query.where(School.id.in_(requested_school_ids))
    else:
        statuses = {
            "pending",
            "failed_validate",
            "validated",
            "navigated",
            "extraction_failed",
        }
        if include_navigated or include_extracted or force_validate:
            statuses.update({"extracted", "summarized"})
        query = query.where(School.scrape_status.in_(sorted(statuses)))
        if limit is not None:
            query = query.limit(limit)

    selected = [row[0] for row in (await db.execute(query)).all()]
    if requested_school_ids is not None:
        missing = sorted(set(requested_school_ids) - set(selected))
        if missing:
            raise click.UsageError(
                "Cohort IDs are outside the requested country/city scope or have no website URL: "
                + ", ".join(str(school_id) for school_id in missing)
            )
    return selected


async def _run_all_stages_batch(
    db,
    *,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    include_navigated: bool,
    include_extracted: bool,
    force_validate: bool,
    skip_summarize: bool = False,
    requested_school_ids: Optional[list[int]] = None,
    pipeline_run=None,
    stage_summaries: Optional[list[dict]] = None,
) -> list[dict]:
    """Run the batch website pipeline over one fixed school-ID cohort.

    The original cohort is recorded for reproducibility. Each subsequent stage is
    narrowed to schools that produced usable fresh output in the preceding stage.
    Summaries are appended as stages finish so a later exception cannot erase usage
    and outcome accounting from already-completed stages.
    """
    school_ids = await _select_all_stage_cohort(
        db,
        country=country,
        city=city,
        limit=limit,
        include_navigated=include_navigated,
        include_extracted=include_extracted,
        force_validate=force_validate,
        requested_school_ids=requested_school_ids,
    )
    if not school_ids:
        console.print("[yellow]No schools matched the batch all-stage cohort[/yellow]")
        return []

    if pipeline_run is not None:
        config = dict(pipeline_run.config or {})
        config["cohort_school_ids"] = school_ids
        pipeline_run.config = config
        db.add(pipeline_run)
        await db.commit()

    console.print(
        f"[cyan]Selected fixed all-stage cohort: {len(school_ids)} schools "
        f"(IDs {school_ids[0]}–{school_ids[-1]})[/cyan]"
    )
    completed = stage_summaries if stage_summaries is not None else []

    url_summary = await _run_validate_urls_batch(
        db,
        country,
        city,
        None,
        school_ids=school_ids,
    )
    completed.append(url_summary)
    if pipeline_run is not None:
        await checkpoint_pipeline_run(
            db, pipeline_run, completed_stage="validate-urls", stage_summaries=completed
        )
    validated_url_ids = builtins.list(url_summary.get("validated_school_ids") or [])

    navigation_results = await _run_navigate_batch(
        db,
        country,
        city or "",
        None,
        include_navigated=include_navigated,
        school_ids=validated_url_ids,
        skip_timed_out_chunks=True,
    )
    completed.append(_navigate_summary(navigation_results))
    if pipeline_run is not None:
        await checkpoint_pipeline_run(
            db, pipeline_run, completed_stage="navigate", stage_summaries=completed
        )
    navigated_ids = sorted(
        int(result["school_id"])
        for result in (navigation_results or [])
        if result and result.get("success") and result.get("school_id") in school_ids
    )

    extract_summary = await _run_extract_batch(
        db,
        country,
        city or "",
        None,
        include_extracted=include_extracted,
        school_ids=navigated_ids,
    )
    completed.append(extract_summary)
    if pipeline_run is not None:
        await checkpoint_pipeline_run(
            db, pipeline_run, completed_stage="extract", stage_summaries=completed
        )
    extracted_ids = builtins.list(extract_summary.get("ready_school_ids") or [])

    validation_summary = await _run_validate_data_batch(
        db,
        country,
        city,
        None,
        force_validate=force_validate,
        school_ids=extracted_ids,
    )
    completed.append(validation_summary)
    if pipeline_run is not None:
        await checkpoint_pipeline_run(
            db, pipeline_run, completed_stage="validate-data", stage_summaries=completed
        )
    validated_ids = builtins.list(validation_summary.get("validated_school_ids") or [])

    if not skip_summarize:
        completed.append(
            await _run_summarize_batch(
                db,
                country,
                city,
                None,
                school_ids=validated_ids,
            )
        )
        if pipeline_run is not None:
            await checkpoint_pipeline_run(
                db, pipeline_run, completed_stage="summarize", stage_summaries=completed
            )
    return completed


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


@cli.command("data-quality")
@click.option("--city", help="Filter by city")
@click.option("--country", default="bg", help="Country code")
@click.option("--runs", type=int, default=5, show_default=True, help="Recent pipeline runs to list")
def data_quality(city, country, runs):
    """Show the data-quality scoreboard (six go-live metrics) + recent runs."""
    asyncio.run(_show_data_quality(city, country, runs))


async def _show_data_quality(city, country, runs):
    from app.database import async_session_maker
    from app.models import PipelineRun
    from app.services.data_quality import compute_quality_metrics
    from sqlalchemy import select

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
        w = metrics["website_validation_coverage"]
        table.add_row(
            "Publishable website-data coverage",
            _pct(w["coverage_pct"]),
            f"{w['with_report']}/{w['eligible']} eligible; "
            f"{w['published_without_report']} published without report",
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
    school_ids: Optional[list[int]] = None,
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
    query = select(School.id, School.attributes).where(School.country_code == country)
    if school_ids is None:
        query = query.where(School.scrape_status.in_(["extracted", "summarized"]))
    else:
        query = query.where(School.id.in_(school_ids))
    if city:
        query = query.where(School.city == city)
    if limit is not None:
        query = query.limit(limit)

    result = await db.execute(query)
    school_rows = result.all()
    if force_validate or school_ids is not None:
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
        return _stage_summary()

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
    spot_usage = {"input_tokens": 0, "output_tokens": 0, "token_cost_usd": 0.0}

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

    validation_summary = {
        **_stage_summary_with_usage(
            usage=spot_usage,
            processed=len(school_ids),
            succeeded=ok_count + review_count,
            failed=failed_count,
        ),
        "validated_school_ids": sorted(validated_ids),
    }

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
        return validation_summary

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
            _add_llm_usage(spot_usage, out)
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

    validation_summary.update(
        {
            "input_tokens": int(spot_usage["input_tokens"]),
            "output_tokens": int(spot_usage["output_tokens"]),
            "token_cost_usd": round(float(spot_usage["token_cost_usd"]), 6),
        }
    )

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

    return validation_summary


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

    explicit_selection = school_ids is not None
    explicit_school_ids = builtins.list(school_ids or [])
    query = select(School).where(
        School.country_code == country,
        School.website_url.isnot(None),
    )

    if explicit_selection:
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
        return _stage_summary()

    if explicit_selection:
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
        ready_school_ids: list[int] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "token_cost_usd": 0.0}

        from app.config import get_settings as _get_settings
        from app.database import async_session_maker
        from app.scrapers.extractor import keep_previous_website_data

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
                    _add_llm_usage(usage, result)
                    if result.get("skipped"):
                        skipped_count += 1
                        ready_school_ids.append(school_id)
                    elif result.get("status") == "extracted":
                        success_count += 1
                        ready_school_ids.append(school_id)
                    else:
                        fail_count += 1
                except asyncio.TimeoutError:
                    logger.error(
                        "Extraction timed out after %.0fs for school %s", _ext_timeout, school_id
                    )
                    await db.rollback()
                    await keep_previous_website_data(db, school_id)
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
                        async with async_session_maker() as school_db:
                            await keep_previous_website_data(school_db, school_id)
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
                school_id, result = await task_result
                _add_llm_usage(usage, result)
                if result.get("skipped"):
                    skipped_count += 1
                    ready_school_ids.append(school_id)
                elif result.get("status") == "extracted":
                    success_count += 1
                    ready_school_ids.append(school_id)
                else:
                    fail_count += 1
                progress.update(task, advance=1)

    console.print(f"[green]✓ Extraction complete:[/green]")
    console.print(f"  Successful: {success_count}")
    console.print(f"  Skipped: {skipped_count}")
    console.print(f"  Failed: {fail_count}")
    console.print(f"  Tokens: in={usage['input_tokens']}, out={usage['output_tokens']}")
    console.print(f"  Cost: ${usage['token_cost_usd']:.6f}")

    return {
        **_stage_summary_with_usage(
            processed=len(school_ids),
            succeeded=success_count,
            failed=fail_count,
            skipped=skipped_count,
            usage=usage,
        ),
        "ready_school_ids": sorted(ready_school_ids),
    }


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
    school_ids: Optional[list[int]] = None,
):
    """Run Stage 7 summarization in batch mode."""
    from app.config import get_settings as _get_settings
    from app.database import async_session_maker
    from app.scrapers.summarizer import get_schools_requiring_summary, summarize_school

    settings = _get_settings()
    if school_ids is None:
        schools = await get_schools_requiring_summary(db=db, country_code=country, city=city, limit=limit)
        selected_school_ids = [school.id for school in schools]
    else:
        selected_school_ids = builtins.list(school_ids)

    if not selected_school_ids:
        console.print("[yellow]No schools to summarize (all eligible summaries are current or ineligible)[/yellow]")
        return _stage_summary()

    requested_concurrency = max(1, int(getattr(settings, "summarization_batch_concurrency", 1)))
    max_concurrency = 8
    concurrency = min(requested_concurrency, max_concurrency)
    if requested_concurrency > max_concurrency:
        console.print(
            f"[yellow]Requested concurrency {requested_concurrency} capped to {max_concurrency}[/yellow]"
        )

    console.print(f"[cyan]Summarizing {len(selected_school_ids)} schools...[/cyan]")
    console.print(f"  Concurrency: {concurrency}")

    summarized_count = 0
    skipped_count = 0
    failed_count = 0
    usage = {"input_tokens": 0, "output_tokens": 0, "token_cost_usd": 0.0}
    results: list[dict] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Generating summaries...", total=len(selected_school_ids))
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

        tasks = [asyncio.create_task(_summarize_single(school_id)) for school_id in selected_school_ids]
        for completed in asyncio.as_completed(tasks):
            _, out = await completed
            results.append(out)
            _add_llm_usage(usage, out)
            status = out.get("status")
            if status == "summarized":
                summarized_count += 1
            elif status == "skipped":
                skipped_count += 1
            else:
                failed_count += 1
            progress.update(task, advance=1)

    console.print("[green]✓ Summarization complete:[/green]")
    console.print(f"  Summarized: {summarized_count}")
    console.print(f"  Skipped: {skipped_count}")
    console.print(f"  Failed: {failed_count}")
    console.print(f"  Tokens: in={usage['input_tokens']}, out={usage['output_tokens']}")
    console.print(f"  Cost: ${usage['token_cost_usd']:.6f}")
    return _stage_summary_with_usage(
        processed=len(selected_school_ids),
        succeeded=summarized_count,
        failed=failed_count,
        skipped=skipped_count,
        usage=usage,
    )


if __name__ == "__main__":
    cli()
