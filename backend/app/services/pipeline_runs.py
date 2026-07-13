"""PipelineRun lifecycle helpers (P1.6).

Wraps a batch pipeline run so every execution records a ``PipelineRun`` row with
a data-quality metrics snapshot. Previously the model was never written.
"""

from __future__ import annotations

import datetime
import math
from typing import Any, Iterable, Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import PipelineRun, PipelineStage, PipelineStatus
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
        config={**(config or {}), "cli_stage": cli_stage},
    )
    db.add(run)
    await db.commit()
    return run


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
    run.schools_processed = counts["processed"]
    run.schools_succeeded = counts["succeeded"]
    run.schools_failed = counts["failed"]
    run.schools_skipped = counts["skipped"]
    run.total_llm_cost_usd = usage["token_cost_usd"]
    run.error_summary = error_summary
    quality_metrics = await compute_quality_metrics(db, country=country, city=city)
    run.metrics = {**quality_metrics, "llm_usage": usage}
    run.completed_at = _now()

    if error_summary:
        run.status = PipelineStatus.FAILED
    elif counts["failed"] and counts["succeeded"]:
        run.status = PipelineStatus.PARTIAL
    elif counts["failed"]:
        run.status = PipelineStatus.FAILED
    else:
        run.status = PipelineStatus.COMPLETED

    db.add(run)
    await db.commit()
    return run
