"""School-facts stage: read a school's own pages and keep the concrete facts they state.

Finds pages with the fees agent's finder (its own link prompt), lists facts by topic in one
call, keeps a fact only when its quote is on a page read and a second call confirms the
quote states it, and stores the kept facts in ``attributes["school_facts"]``. The default
is a dry run that writes nothing; the report lists every fact and why it was dropped.
Not part of ``all``. Prompts and checks: ``app/scrapers/extraction/school_facts.py``.
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import create_agent, extract_provider_cost_usd, get_model, get_model_costs
from app.config import CRAWLER_USER_AGENT, get_settings
from app.database import async_session_maker
from app.models import School
from app.models.pipeline_run import PipelineRun
from app.scrapers.extraction import school_facts as facts_rules
from app.scrapers.extraction.school_facts import Fact, Facts, Supported
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.stages import common as stage_common
from app.scrapers.stages.fees_agent import PageRead, Spend, SpendCapReached, find_pages
from app.services.pipeline_runs import finalize_pipeline_run, pipeline_run_heartbeat, start_pipeline_run
from app.services.provider_costs import ProviderCostError, execute_billable_request, provider_cost_scope
from app.utils.school_facts import ATTRIBUTE_KEY

logger = logging.getLogger(__name__)

STAGE = "school-facts"
TIER = "pricing"  # the fee reader's model: cheap, and follows quoting rules well
DEFAULT_MAX_USD = 0.50
OUTPUT_TOKENS_RESERVE = 4000
MAX_READ_CHARS = 100_000
# Each page is cut on its own, so one long page (a staff list of 56,000 characters at 564)
# cannot push the admissions and activities pages after it out of the read.
MAX_PAGE_CHARS = 15_000
# A read of this many pages that keeps no fact replaces the stored facts; fewer pages read
# is treated as a partial crawl and keeps them.
FULL_READ_PAGES = 4

Finder = Callable[[School], Awaitable[list[PageRead]]]
Caller = Callable[[str, type, str, int], Awaitable[Any]]


def _estimate_usd(message: str) -> float:
    in_price, out_price = get_model_costs(TIER)
    return (len(message) / 3 * in_price + OUTPUT_TOKENS_RESERVE * out_price) / 1_000_000


def model_caller(spend: Spend) -> Caller:
    """One structured call on the stage's tier, within the run's spend cap."""

    async def call(prompt: str, schema: type, message: str, school_id: int) -> Any:
        if not spend.allows(_estimate_usd(message)):
            raise SpendCapReached(f"spent ${spend.spent_usd:.4f} of ${spend.cap_usd}")
        agent = create_agent(
            TIER, prompt, schema, retries=1, model_settings={"temperature": get_settings().extraction_temperature}
        )
        result = await execute_billable_request(
            lambda: agent.run(message), model=get_model(TIER), school_id=school_id, stage=STAGE, timeout_seconds=150
        )
        spend.spent_usd += extract_provider_cost_usd(result)
        return result.output

    return call


def _name(school: School) -> str:
    return (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or str(school.id)


def _site_chrome(pages: list[PageRead]) -> set[str]:
    """Lines on at least half the pages, and on three or more: the site's menu and footer."""
    if len(pages) < 4:
        return set()
    counts: dict[str, int] = {}
    for page in pages:
        for line in {line.strip() for line in page.text.splitlines() if line.strip()}:
            counts[line] = counts.get(line, 0) + 1
    return {line for line, count in counts.items() if count >= 3 and count * 2 >= len(pages)}


def page_text(pages: list[PageRead]) -> str:
    """The pages the reader sees, without the menu and footer every page repeats, each cut
    to ``MAX_PAGE_CHARS``, in the format the fees reader uses."""
    readable = [page for page in pages if page.text.strip()]
    chrome = _site_chrome(readable)
    parts = []
    for page in readable:
        body = "\n".join(line for line in page.text.splitlines() if line.strip() not in chrome)
        parts.append(f"=== PAGE {page.url}\n{body[:MAX_PAGE_CHARS]}")
    return "\n\n".join(parts)[:MAX_READ_CHARS]


def _ordered(kept: list[tuple[Fact, str]]) -> list[tuple[Fact, str]]:
    """At most 2 a topic and ``MAX_FACTS`` in all, cut in the reader's (most useful first)
    order, then put in topic order for display."""
    per_topic: dict[str, int] = {}
    out: list[tuple[Fact, str]] = []
    for item in kept:
        if per_topic.get(item[0].topic, 0) < 2 and len(out) < facts_rules.MAX_FACTS:
            per_topic[item[0].topic] = per_topic.get(item[0].topic, 0) + 1
            out.append(item)
    return sorted(out, key=lambda item: facts_rules.TOPICS.index(item[0].topic))


async def read_school(school: School, pages: list[PageRead], call: Caller) -> dict[str, Any]:
    """Read, check and judge; returns the report with the facts to store under ``store``."""
    readable = [(page.url, page.text) for page in pages if page.text.strip()]
    report: dict[str, Any] = {"pages": [{"url": p.url, "chars": len(p.text), "note": p.note} for p in pages]}
    if not readable:
        return report | {"outcome": "nothing_read"}
    text = page_text(pages)
    report["read_chars"] = len(text)  # after the menu is left out and each page is cut
    report["pages_read"] = len(readable)
    read: Facts = await call(
        facts_rules.READER_PROMPT,
        Facts,
        f"Institution: {_name(school)} ({school.school_type} {school.education_level})\n\n{text}",
        school.id,
    )
    on_page = [(fact, facts_rules.quote_on_page(fact, readable)) for fact in read.facts]
    quoted = [(fact, url) for fact, url in on_page if url]
    seen_quotes: set[str] = set()
    repeats: list[Fact] = []
    for fact, _ in quoted:  # one line split into several facts keeps the first
        key = facts_rules.quote_key(fact)
        if key in seen_quotes:
            repeats.append(fact)
        seen_quotes.add(key)
    quoted = [(fact, url) for fact, url in quoted if all(fact is not repeat for repeat in repeats)]
    supported: set[int] = set()
    if quoted:
        verdict: Supported = await call(
            facts_rules.JUDGE_PROMPT, Supported, facts_rules.judge_message(quoted), school.id
        )
        supported = set(verdict.passed)
    kept = _ordered([item for n, item in enumerate(quoted, 1) if n in supported])
    kept_ids = {id(fact) for fact, _ in kept}
    quoted_ids = {id(fact) for fact, _ in quoted}
    passed_ids = {id(fact) for n, (fact, _) in enumerate(quoted, 1) if n in supported}
    repeat_ids = {id(fact) for fact in repeats}

    def dropped(fact: Fact) -> Optional[str]:
        if id(fact) in kept_ids:
            return None
        if id(fact) in repeat_ids:
            return "same quote as an earlier fact"
        if id(fact) not in quoted_ids:
            return "quote not on page"
        return "over the topic or total limit" if id(fact) in passed_ids else "quote does not state it"

    status = {id(fact): dropped(fact) for fact in read.facts}
    report["facts"] = [
        fact.model_dump() | {"kept": status[id(fact)] is None, "dropped": status[id(fact)]} for fact in read.facts
    ]
    carried = _carried(school, readable, kept)
    store = _ordered(kept + carried)
    report["carried"] = [fact.fact_en for fact, _ in carried if any(fact is f for f, _ in store)]
    report["store"] = [facts_rules.stored_fact(fact, url) for fact, url in store]
    outcome = f"{len(kept)} of {len(read.facts)} kept"
    return report | {"outcome": outcome + (f", {len(report['carried'])} carried" if report["carried"] else "")}


def _carried(school: School, readable: list[tuple[str, str]], kept: list[tuple[Fact, str]]) -> list[tuple[Fact, str]]:
    """Stored facts whose quote is still on a page read, and not the quote of a new fact.

    Reads vary from run to run; a fact accepted before stays while its page still says it,
    with no model call, so the school's facts do not change on every refresh. New facts
    come first, so they win a topic's places."""
    stored = ((school.attributes or {}).get(ATTRIBUTE_KEY) or {}).get("facts") or []
    new_quotes = {facts_rules.quote_key(fact) for fact, _ in kept}
    carried = []
    for row in stored:
        fact = facts_rules.stored_as_fact(row) if isinstance(row, dict) else None
        if fact is None or fact.topic not in facts_rules.TOPICS or facts_rules.quote_key(fact) in new_quotes:
            continue
        url = facts_rules.quote_on_page(fact, readable)
        if url:
            new_quotes.add(facts_rules.quote_key(fact))
            carried.append((fact, url))
    return carried


def facts_block(facts: list[dict], model: str, website_url: Optional[str]) -> dict[str, Any]:
    return {
        "_schema_version": facts_rules.SCHEMA_VERSION,
        "read_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "website_url": website_url,
        "model": model,
        "facts": facts,
    }


async def run_school(db: AsyncSession, school: School, *, find: Finder, call: Caller, apply: bool) -> dict[str, Any]:
    report = {
        "school_id": school.id,
        "name": _name(school),
        "type": f"{school.school_type} {school.education_level}",
        "website": school.website_url,
    }
    report |= await read_school(school, await find(school), call)
    facts = report.pop("store", None)
    stored = ((school.attributes or {}).get(ATTRIBUTE_KEY) or {}).get("facts")
    # A site not read, or read with no fact kept from only a few pages (often a partial
    # crawl: a thin home page and the rest timed out), keeps the facts stored before. A read
    # that keeps facts, or reads the site in full and keeps none, replaces them. The API
    # only shows facts read from the school's current website.
    if facts is None or (not facts and stored and report.get("pages_read", 0) < FULL_READ_PAGES):
        report["kept_stored_facts"] = bool(stored)
    elif apply:
        block = facts_block(facts, get_model(TIER), school.website_url)
        school.attributes = {**(school.attributes or {}), ATTRIBUTE_KEY: block}
        await db.commit()
        report["stored"] = len(facts)
    return report


async def run_school_facts(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = None,
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    max_usd: float = DEFAULT_MAX_USD,
    apply: bool = False,
) -> dict[str, Any]:
    """Run over schools with a website (all types, or the given ids); returns the report."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # type: ignore

    query = select(School.id).where(School.country_code == country, School.website_url.isnot(None))
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    ids = (await db.execute(query.order_by(School.id).limit(limit))).scalars().all()

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
    call = model_caller(spend)
    results: list[dict[str, Any]] = []
    error: Optional[str] = None
    try:
        with provider_cost_scope(
            pipeline_run_id=run.id,
            stage=STAGE,
            cap_usd=max_usd,
            request_reserve_usd=float(settings.provider_request_reserve_usd),
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

                async def find(school: School) -> list[PageRead]:
                    return await find_pages(
                        crawler,
                        run_config,
                        client,
                        school,
                        spend,
                        pick_prompt=facts_rules.PICK_PROMPT,
                        fallback_links=facts_rules.FALLBACK_LINK_RE,
                        stage=STAGE,
                    )

                for current_id in ids:
                    school = await db.get(School, current_id)
                    try:
                        results.append(await run_school(db, school, find=find, call=call, apply=apply))
                    except (SpendCapReached, ProviderCostError) as exc:  # a cap, or a run no longer running
                        error = f"stopped before school {current_id}: {exc}"
                        break
                    except Exception as exc:  # one school's failure leaves its stored facts as they are
                        logger.exception("school-facts failed for school %s", current_id)
                        if db.in_transaction():
                            await db.rollback()
                        results.append({"school_id": current_id, "outcome": "error", "reason": str(exc)[:200]})
    except Exception as exc:
        logger.exception("school-facts run %s stopped", run_id)
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
    spent = max(spend.spent_usd, float(run.total_llm_cost_usd or 0))
    return {
        "apply": apply,
        "pipeline_run_id": run_id,
        "model": get_model(TIER),
        "schools": len(results),
        "spent_usd": round(spent, 6),
        "stopped": error,
        "results": results,
    }
