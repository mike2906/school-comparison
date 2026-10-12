"""Validate-data stage runners (Stage 6, with spot checks)."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Optional

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from app.scrapers.stages import common as stage_common

logger = logging.getLogger(__name__)
console = Console()


async def run_validate_data_school(
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


async def run_validate_data_batch(
    db,
    country: str,
    city: Optional[str],
    limit: Optional[int],
    force_validate: bool = False,
    school_ids: Optional[list[int]] = None,
):
    """Run Stage 6 validation in batch mode, then sampled monitoring spot-checks."""
    from sqlalchemy import select

    from app.config import get_settings as _get_settings
    from app.database import async_session_maker
    from app.models import School
    from app.scrapers.validator import (
        has_current_validation_report,
        run_spot_check_for_school,
        validate_school_data,
    )

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
        return stage_common.stage_summary()

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
        **stage_common.stage_summary_with_usage(
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
            stage_common.add_llm_usage(spot_usage, out)
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
