"""Shared helpers for the pipeline stage runners: run summaries and school lookup."""

from __future__ import annotations

import builtins
import logging
from math import isfinite
from typing import Optional

from rich.console import Console
from rich.table import Table

logger = logging.getLogger(__name__)
console = Console()


def stage_summary(processed: int = 0, succeeded: int = 0, failed: int = 0, skipped: int = 0) -> dict:
    """Per-stage school-outcome counts consumed by the PipelineRun lifecycle."""
    return {"processed": processed, "succeeded": succeeded, "failed": failed, "skipped": skipped}


def add_llm_usage(total: dict, result: dict) -> None:
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


def stage_summary_with_usage(*, usage: dict, **counts: int) -> dict:
    """Enrich a count summary without changing existing count keys/contracts."""
    return {
        **stage_summary(**counts),
        "input_tokens": int(usage["input_tokens"]),
        "output_tokens": int(usage["output_tokens"]),
        "token_cost_usd": round(float(usage["token_cost_usd"]), 6),
    }


def navigate_summary(results) -> dict:
    """Convert `navigate_stage.run_navigate_batch`'s per-school results list into a stage summary.

    Navigation keeps returning its results list (a repair command consumes it), so
    the batch dispatch derives run counts here instead of changing that contract.
    """
    rows = builtins.list(results or [])
    succeeded = sum(1 for row in rows if row and row.get("success"))
    return stage_summary(processed=len(rows), succeeded=succeeded, failed=len(rows) - succeeded)


async def find_school_by_name(db, name: str, country: str) -> Optional[int]:
    """Find school by fuzzy name match."""
    from sqlalchemy import String, cast, or_, select

    from app.models import School

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


def school_label(school) -> str:
    name_i18n = school.name_i18n or {}
    return name_i18n.get("bg") or name_i18n.get("en") or f"School {school.id}"


def primary_location_for_school(school):
    locations = builtins.list(school.locations or [])
    if not locations:
        return None
    return sorted(locations, key=lambda loc: (not bool(loc.is_primary), loc.id or 0))[0]
