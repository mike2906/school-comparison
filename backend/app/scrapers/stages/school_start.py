"""School-start stage: the start times stored facts state, without reading the site again.

The school-facts stage reads them with every facts read (``extraction/school_start.py``);
this fills them in for facts read before, or again after the rules change. Writes only the
facts block's ``start_times``. The default is a dry run that writes nothing. Not part of ``all``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_model
from app.config import get_settings
from app.models import School
from app.models.pipeline_run import PipelineRun
from app.scrapers.extraction.school_start import day_facts, read_start_times
from app.scrapers.stages import common as stage_common
from app.scrapers.stages.fees_agent import Spend, SpendCapReached
from app.scrapers.stages.school_facts import TIER, Caller, model_caller
from app.services.pipeline_runs import finalize_pipeline_run, start_pipeline_run
from app.services.provider_costs import ProviderCostError, provider_cost_scope
from app.utils.school_facts import ATTRIBUTE_KEY, SCHEMA_VERSION
from app.utils.school_start import START_TIMES_KEY

logger = logging.getLogger(__name__)

STAGE = "school-start"
DEFAULT_MAX_USD = 0.30


async def run_school_start(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = None,
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    max_usd: float = DEFAULT_MAX_USD,
    missing_only: bool = True,
    apply: bool = False,
    call: Optional[Caller] = None,
) -> dict[str, Any]:
    """Run over schools whose stored facts write a clock time on the school day (the given
    ids, or all); ``missing_only`` skips a block that has start times already."""
    query = select(School.id).where(School.country_code == country)
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    ids = list((await db.execute(query.order_by(School.id))).scalars().all())

    spend = Spend(max_usd)
    run = await start_pipeline_run(
        db, country=country, city=city, cli_stage=STAGE, config={"provider_cost_cap_usd": max_usd, "apply": apply}
    )
    run_id = run.id
    call = call or model_caller(spend, STAGE)
    results: list[dict[str, Any]] = []
    error: Optional[str] = None
    try:
        with provider_cost_scope(
            pipeline_run_id=run.id,
            stage=STAGE,
            cap_usd=max_usd,
            request_reserve_usd=float(get_settings().provider_request_reserve_usd),
            max_in_flight=1,
        ):
            for school_id in ids:
                if limit is not None and len(results) >= limit:
                    break
                school = await db.get(School, school_id)
                block = (school.attributes or {}).get(ATTRIBUTE_KEY)
                if not isinstance(block, dict) or block.get("_schema_version") != SCHEMA_VERSION:
                    continue
                if not day_facts(block.get("facts") or []):
                    continue
                if missing_only and START_TIMES_KEY in block:
                    continue
                result: dict[str, Any] = {"school_id": school_id, "name_bg": (school.name_i18n or {}).get("bg")}
                try:
                    starts = await read_start_times(block.get("facts") or [], call, school_id)
                except (SpendCapReached, ProviderCostError) as exc:
                    error = str(exc)[:200]
                    break
                except Exception as exc:  # one school's call must not stop the run
                    logger.exception("school-start failed for school %s", school_id)
                    results.append(result | {"outcome": "error", "note": str(exc)[:200]})
                    continue
                result |= {"outcome": "found" if starts else "none", "start_times": {b: s["time"] for b, s in starts.items()}}
                if apply:
                    # Only this key of the facts block changes, on the row as it is now.
                    await db.refresh(school, attribute_names=["attributes"], with_for_update=True)
                    current = (school.attributes or {}).get(ATTRIBUTE_KEY)
                    if isinstance(current, dict) and current.get("facts") == block.get("facts"):
                        school.attributes = {**school.attributes, ATTRIBUTE_KEY: {**current, START_TIMES_KEY: starts}}
                        result["stored"] = True
                    await db.commit()
                results.append(result)
    except Exception as exc:
        logger.exception("school-start run %s stopped", run_id)
        if db.in_transaction():
            await db.rollback()
        error = f"run stopped: {str(exc)[:200]}"
    failed = sum(r["outcome"] == "error" for r in results)
    run = await db.get(PipelineRun, run_id, populate_existing=True)
    await finalize_pipeline_run(
        db,
        run,
        country=country,
        city=city,
        stage_summaries=[
            stage_common.stage_summary(processed=len(results), succeeded=len(results) - failed, failed=failed)
        ],
        error_summary=error,
    )
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
    return {
        "apply": apply,
        "pipeline_run_id": run_id,
        "model": get_model(TIER),
        "schools": len(results),
        "outcomes": outcomes,
        "spent_usd": round(max(spend.spent_usd, float(run.total_llm_cost_usd or 0)), 6),
        "stopped": error,
        "results": results,
    }
