"""Site-agents stage: one crawl of each school's site for both the facts and the fees readers.

Per school, the school-facts finder and reader run first, then (private and international
schools only) the fees finder and reader, on one page cache: the home page and any page
both link pickers choose are fetched once. Each agent keeps its own link prompt, reader,
checks and write path; this stage only shares the fetches and runs schools side by side.
Dry run by default. Plan: ``docs/SITE_AGENT_RUNS.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_model
from app.config import CRAWLER_USER_AGENT, get_settings
from app.database import async_session_maker
from app.models import School
from app.models.pipeline_run import PipelineRun
from app.scrapers.extraction import school_facts as facts_rules
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.stages import common as stage_common
from app.scrapers.stages import fees_agent, school_facts, school_pool
from app.scrapers.stages.fees_agent import SiteCache, Spend, SpendCapReached, find_pages
from app.services.pipeline_runs import finalize_pipeline_run, pipeline_run_heartbeat, start_pipeline_run
from app.services.provider_costs import ProviderCostCapExceeded, ProviderCostError, provider_cost_scope
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

STAGE = "site-agents"
DEFAULT_MAX_USD = 0.50
FEE_SCHOOL_TYPES = ("private", "international")
# What stops the run, as in each stage on its own: school facts stop on any provider-cost
# error (a run no longer running); the fees agent only on a cap, and records an
# unattributed response as that school's error.
_FACTS_STOPS = (SpendCapReached, ProviderCostError)
_FEES_STOPS = (SpendCapReached, ProviderCostCapExceeded)


async def run_site_agents(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = None,
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    max_usd: float = DEFAULT_MAX_USD,
    apply: bool = False,
    workers: int = school_pool.DEFAULT_WORKERS,
) -> dict[str, Any]:
    """Facts for every school with an accepted website, fees for every private and
    international school with a website, as the two stages pick them on their own;
    returns the report, one entry per school."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # type: ignore

    # The union of what the two stages read on their own: facts need a website URL
    # validation accepted, fees any website of a private or international school.
    query = select(School.id).where(
        School.country_code == country,
        School.website_url.isnot(None),
        School.scrape_status.in_(SITE_ACCEPTED_STATUSES) | School.school_type.in_(FEE_SCHOOL_TYPES),
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    rows = (await db.execute(query.add_columns(School.website_url).order_by(School.id).limit(limit))).all()
    ids = [row.id for row in rows]
    sites = {row.id: row.website_url for row in rows}

    spend = Spend(max_usd)
    run = await start_pipeline_run(
        db, country=country, city=city, cli_stage=STAGE, config={"provider_cost_cap_usd": max_usd, "apply": apply}
    )
    run_id = run.id
    settings = get_settings()
    browser = BrowserConfig(headless=True, verbose=False, ignore_https_errors=True, user_agent=CRAWLER_USER_AGENT)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        check_robots_txt=True,
        page_timeout=30000,
        wait_until="domcontentloaded",
        delay_before_return_html=0.5,
        exclude_external_links=False,
        verbose=False,
    )
    call = school_facts.model_caller(spend)
    results: list[dict[str, Any]] = []
    error: Optional[str] = None
    try:
        with provider_cost_scope(
            pipeline_run_id=run.id,
            stage=STAGE,
            cap_usd=max_usd,
            request_reserve_usd=float(settings.provider_request_reserve_usd),
            max_in_flight=workers,
        ):
            async with (
                pipeline_run_heartbeat(
                    run.id,
                    session_factory=async_session_maker,
                    interval_seconds=float(settings.pipeline_heartbeat_interval_seconds),
                ),
                AsyncWebCrawler(config=browser) as crawler,
                fee_http_client() as client,
            ):

                async def one(school_db: AsyncSession, current_id: int) -> dict[str, Any]:
                    cache = SiteCache()
                    report: dict[str, Any] = {"school_id": current_id}
                    school = await school_db.get(School, current_id)

                    async def find_facts(s: School):
                        return await find_pages(
                            crawler,
                            run_config,
                            client,
                            s,
                            spend,
                            pick_prompt=facts_rules.PICK_PROMPT,
                            fallback_links=facts_rules.FALLBACK_LINK_RE,
                            stage=school_facts.STAGE,
                            cache=cache,
                        )

                    async def find_fees(s: School):
                        return await find_pages(crawler, run_config, client, s, spend, read_pictures=True, cache=cache)

                    steps = []
                    if school.scrape_status in SITE_ACCEPTED_STATUSES:
                        steps.append(
                            (
                                "facts",
                                _FACTS_STOPS,
                                lambda: school_facts.run_school(
                                    school_db, school, find=find_facts, call=call, apply=apply
                                ),
                            )
                        )
                    if school.school_type in FEE_SCHOOL_TYPES:
                        steps.append(
                            (
                                "fees",
                                _FEES_STOPS,
                                lambda: fees_agent.run_school(
                                    school_db,
                                    school,
                                    find=find_fees,
                                    read=lambda s, pages, missed: fees_agent.read_fees(s, pages, spend, missed),
                                    apply=apply,
                                    gone=lambda url: fees_agent.page_gone(client, url),
                                ),
                            )
                        )
                    for name, stops, step in steps:
                        try:
                            report[name] = await step()
                        except stops:
                            raise
                        except Exception as exc:  # one agent's failure leaves its stored data as it is
                            logger.exception("site-agents %s failed for school %s", name, current_id)
                            if school_db.in_transaction():
                                await school_db.rollback()
                            report[name] = {"school_id": current_id, "outcome": "error", "reason": str(exc)[:200]}
                            school = await school_db.get(School, current_id)  # the rollback expired it
                    report["fetches"] = cache.fetches
                    report["fetches_saved"] = cache.hits
                    return report

                results, error = await school_pool.run_schools(
                    db, ids, one, workers=workers, stop_on=(*_FACTS_STOPS, ProviderCostCapExceeded), sites=sites
                )
    except Exception as exc:
        logger.exception("site-agents run %s stopped", run_id)
        if db.in_transaction():
            await db.rollback()
        error = f"run stopped: {str(exc)[:200]}"
    failed = sum(any(r.get(n, {}).get("outcome") == "error" for n in ("facts", "fees")) for r in results)
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
    spent = max(spend.spent_usd, float(run.total_llm_cost_usd or 0))
    return {
        "apply": apply,
        "pipeline_run_id": run_id,
        "model": get_model(school_facts.TIER),
        "schools": len(results),
        "spent_usd": round(spent, 6),
        "stopped": error,
        "results": results,
    }
