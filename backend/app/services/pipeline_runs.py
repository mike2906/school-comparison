"""PipelineRun lifecycle helpers (P1.6).

Wraps a batch pipeline run so every execution records a ``PipelineRun`` row with
a data-quality metrics snapshot. Previously the model was never written.
"""

from __future__ import annotations

import asyncio
import datetime
import math
from contextlib import asynccontextmanager
from typing import Any, Iterable, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import (
    PipelineRun,
    PipelineStage,
    PipelineStatus,
    ProviderRequestLedger,
    ProviderRequestStatus,
)
from app.services.data_quality import compute_quality_metrics

# CLI stage string -> coarse PipelineStage enum. Stages absent here (e.g. "nvo",
# which AGENTS.md documents as independent of the website pipeline) are not tracked.
_CLI_STAGE_TO_ENUM: dict[str, PipelineStage] = {
    "discover": PipelineStage.DISCOVER,
    "discover-websites": PipelineStage.DISCOVER,
    "recover-failed-urls": PipelineStage.VALIDATE_URLS,
    "validate-urls": PipelineStage.VALIDATE_URLS,
    "navigate": PipelineStage.NAVIGATE,
    "extract": PipelineStage.EXTRACT,
    "validate-data": PipelineStage.VALIDATE_DATA,
    "summarize": PipelineStage.SUMMARIZE,
    "all": PipelineStage.FULL,
}


def stage_is_tracked(cli_stage: str) -> bool:
    return cli_stage in _CLI_STAGE_TO_ENUM


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.timezone.utc)
    return value.astimezone(datetime.timezone.utc)


async def start_pipeline_run(
    db: AsyncSession,
    *,
    country: str,
    city: Optional[str],
    cli_stage: str,
    config: Optional[dict[str, Any]] = None,
) -> PipelineRun:
    run = PipelineRun(
        id=str(uuid4()),
        country_code=country,
        city=city,
        stage=_CLI_STAGE_TO_ENUM[cli_stage],
        status=PipelineStatus.RUNNING,
        heartbeat_at=_now(),
        config={**(config or {}), "cli_stage": cli_stage},
    )
    db.add(run)
    await db.commit()
    return run


async def heartbeat_pipeline_run(db: AsyncSession, run_id: str) -> bool:
    """Refresh a live run heartbeat; terminal runs are intentionally untouched."""
    run = await db.get(PipelineRun, run_id)
    if run is None or run.status != PipelineStatus.RUNNING:
        return False
    run.heartbeat_at = _now()
    db.add(run)
    await db.commit()
    return True


@asynccontextmanager
async def pipeline_run_heartbeat(
    run_id: str,
    *,
    session_factory,
    interval_seconds: float,
):
    """Keep a run live using independent short transactions."""
    stop = asyncio.Event()

    async def _beat() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0.05, interval_seconds))
            except asyncio.TimeoutError:
                async with session_factory() as heartbeat_db:
                    if not await heartbeat_pipeline_run(heartbeat_db, run_id):
                        return

    task = asyncio.create_task(_beat())
    try:
        yield
    finally:
        stop.set()
        await task


async def checkpoint_pipeline_run(
    db: AsyncSession,
    run: PipelineRun,
    *,
    completed_stage: str,
    stage_summaries: Iterable[Any],
) -> PipelineRun:
    """Persist completed-stage evidence without terminalizing the run."""
    summaries = list(stage_summaries)
    counts = _aggregate_counts(summaries)
    usage = _aggregate_usage(summaries)
    run.last_completed_stage = completed_stage
    run.schools_processed = counts["processed"]
    run.schools_succeeded = counts["succeeded"]
    run.schools_failed = counts["failed"]
    run.schools_skipped = counts["skipped"]
    run.total_llm_cost_usd = usage["token_cost_usd"]
    run.heartbeat_at = _now()
    db.add(run)
    await db.commit()
    return run


async def terminalize_stale_pipeline_runs(
    db: AsyncSession,
    *,
    stale_before: datetime.datetime,
    now: Optional[datetime.datetime] = None,
) -> list[PipelineRun]:
    """Terminalize only RUNNING rows whose persisted heartbeat is stale.

    A completed-stage checkpoint is durable evidence of partial work. Merely having
    a RUNNING status is never treated as evidence that its process is dead.
    """
    terminalized: list[PipelineRun] = []
    rows = (
        await db.execute(
            select(PipelineRun)
            .where(PipelineRun.status == PipelineStatus.RUNNING)
            .order_by(PipelineRun.started_at, PipelineRun.id)
            .with_for_update(skip_locked=True)
        )
    ).scalars()
    terminalized_at = now or _now()
    for run in rows:
        heartbeat = run.heartbeat_at or run.started_at
        if heartbeat is None or _as_utc(heartbeat) >= _as_utc(stale_before):
            continue
        has_evidence = bool(run.last_completed_stage) or any(
            (run.schools_processed, run.schools_succeeded, run.schools_failed, run.schools_skipped)
        )
        run.status = PipelineStatus.PARTIAL if has_evidence else PipelineStatus.FAILED
        run.completed_at = terminalized_at
        run.terminalization_reason = (
            f"stale heartbeat ({_as_utc(heartbeat).isoformat()}) before cutoff "
            f"{_as_utc(stale_before).isoformat()}; recovered by deterministic terminalizer"
        )
        db.add(run)
        terminalized.append(run)
    if terminalized:
        await db.commit()
    return terminalized


def _aggregate_counts(stage_summaries: Iterable[Any]) -> dict[str, int]:
    processed = succeeded = failed = skipped = 0
    for summary in stage_summaries:
        if not isinstance(summary, dict):
            # Batch runners that don't report per-school outcomes (e.g. navigate,
            # which returns a results list) are simply not counted.
            continue
        processed += int(summary.get("processed", 0) or 0)
        succeeded += int(summary.get("succeeded", 0) or 0)
        failed += int(summary.get("failed", 0) or 0)
        skipped += int(summary.get("skipped", 0) or 0)
    return {"processed": processed, "succeeded": succeeded, "failed": failed, "skipped": skipped}


def _non_negative_int(value: Any) -> int:
    """Coerce trusted stage usage to a safe persisted counter."""
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, parsed)


def _non_negative_float(value: Any) -> float:
    """Coerce cost values while preventing NaN/inf from reaching JSON."""
    try:
        parsed = float(value or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if math.isfinite(parsed) and parsed >= 0 else 0.0


def _aggregate_usage(stage_summaries: Iterable[Any]) -> dict[str, int | float]:
    input_tokens = output_tokens = 0
    token_cost_usd = 0.0
    for summary in stage_summaries:
        if not isinstance(summary, dict):
            continue
        input_tokens += _non_negative_int(summary.get("input_tokens"))
        output_tokens += _non_negative_int(summary.get("output_tokens"))
        token_cost_usd += _non_negative_float(summary.get("token_cost_usd"))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "token_cost_usd": round(token_cost_usd, 6),
    }


async def finalize_pipeline_run(
    db: AsyncSession,
    run: PipelineRun,
    *,
    country: str,
    city: Optional[str],
    stage_summaries: Iterable[Any],
    error_summary: Optional[str] = None,
) -> PipelineRun:
    # Materialize once because callers may supply a generator and both aggregates
    # must see the same stage results.
    summaries = list(stage_summaries)
    counts = _aggregate_counts(summaries)
    usage = _aggregate_usage(summaries)
    ledger_rows = list(
        (
            await db.execute(
                select(ProviderRequestLedger).where(
                    ProviderRequestLedger.pipeline_run_id == run.id
                )
            )
        ).scalars()
    )
    if ledger_rows:
        attributed = [
            row for row in ledger_rows if row.status == ProviderRequestStatus.ATTRIBUTED
        ]
        usage = {
            "input_tokens": sum(int(row.input_tokens or 0) for row in attributed),
            "output_tokens": sum(int(row.output_tokens or 0) for row in attributed),
            "token_cost_usd": round(
                sum(float(row.provider_cost_usd or 0) for row in attributed), 6
            ),
        }
    run.schools_processed = counts["processed"]
    run.schools_succeeded = counts["succeeded"]
    run.schools_failed = counts["failed"]
    run.schools_skipped = counts["skipped"]
    run.total_llm_cost_usd = usage["token_cost_usd"]
    incomplete_ledger_count = sum(
        row.status != ProviderRequestStatus.ATTRIBUTED for row in ledger_rows
    )
    ledger_error = (
        f"provider cost attribution incomplete for {incomplete_ledger_count} request(s)"
        if incomplete_ledger_count
        else None
    )
    run.error_summary = error_summary or ledger_error
    quality_metrics = await compute_quality_metrics(db, country=country, city=city)
    run.metrics = {
        **quality_metrics,
        "llm_usage": usage,
        "provider_cost_ledger": {
            "requests": len(ledger_rows),
            "attributed": sum(
                row.status == ProviderRequestStatus.ATTRIBUTED for row in ledger_rows
            ),
            "uncertain": incomplete_ledger_count,
        },
    }
    run.completed_at = _now()
    run.heartbeat_at = run.completed_at

    if error_summary:
        run.status = PipelineStatus.FAILED
    elif incomplete_ledger_count:
        run.status = (
            PipelineStatus.PARTIAL if counts["succeeded"] else PipelineStatus.FAILED
        )
    elif counts["failed"] and counts["succeeded"]:
        run.status = PipelineStatus.PARTIAL
    elif counts["failed"]:
        run.status = PipelineStatus.FAILED
    else:
        run.status = PipelineStatus.COMPLETED

    db.add(run)
    await db.commit()
    return run
