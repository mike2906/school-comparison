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
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import create_agent, extract_provider_cost_usd, get_model, get_model_costs
from app.config import CRAWLER_USER_AGENT, get_settings
from app.database import async_session_maker
from app.models import School
from app.models.pipeline_run import PipelineRun
from app.scrapers.extraction import school_facts as facts_rules
from app.scrapers.extraction.school_facts import Fact, Facts, Graded
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.stages import common as stage_common
from app.scrapers.stages import school_pool
from app.scrapers.stages.fees_agent import PageRead, Spend, SpendCapReached, find_pages
from app.services.pipeline_runs import finalize_pipeline_run, pipeline_run_heartbeat, start_pipeline_run
from app.services.provider_costs import ProviderCostError, execute_billable_request, provider_cost_scope
from app.utils.school_facts import ATTRIBUTE_KEY, LANGUAGES_READ_KEY, SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

STAGE = "school-facts"
TIER = "facts"  # Haiku 5.5 on Anthropic's API (app/ai/client.py MODEL_TIERS)
DEFAULT_MAX_USD = 0.50
OUTPUT_TOKENS_RESERVE = 6000  # up to 20 facts, each with its quote, in two languages
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


def model_caller(spend: Spend, stage: str = STAGE) -> Caller:
    """One structured call on the stage's tier, within the run's spend cap."""

    async def call(prompt: str, schema: type, message: str, school_id: int) -> Any:
        estimate = _estimate_usd(message)
        if not spend.allows(estimate):  # before building the agent; ``hold`` below checks again
            raise SpendCapReached(f"spent ${spend.spent_usd:.4f} of ${spend.cap_usd}")
        agent = create_agent(
            TIER, prompt, schema, retries=1, model_settings={"temperature": get_settings().extraction_temperature}
        )
        with spend.hold(estimate):
            result = await execute_billable_request(
                lambda: agent.run(message), model=get_model(TIER), school_id=school_id, stage=stage, timeout_seconds=150
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


def _without_chrome(pages: list[PageRead]) -> list[tuple[str, str]]:
    """(url, text) of each page without the menu and footer, so a quote's context lines are
    the page's own heading, not the menu entry of the same name."""
    chrome = _site_chrome([page for page in pages if page.text.strip()])
    return [
        (page.url, "\n".join(line for line in page.text.splitlines() if line.strip() not in chrome)) for page in pages
    ]


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


Ranked = tuple[Fact, str, str]  # the fact, the page its quote is on, its grade


def _ordered(facts: list[Ranked]) -> list[Ranked]:
    """Key facts first, then more, then minor, each in the reader's (most useful first)
    order, ``MAX_FACTS`` at most. The API keeps this order to choose which key facts show."""
    ranked = sorted(facts, key=lambda item: facts_rules.RANKS.index(item[2]))
    return ranked[: facts_rules.MAX_FACTS]


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
    for fact, _ in quoted:  # a sign-up link the pages do not have is dropped, the fact kept
        facts_rules.check_link(fact, readable)
    seen_quotes: set[tuple[str, str]] = set()
    repeats: list[Fact] = []
    for fact, _ in quoted:  # one line split into several facts on a topic keeps the first
        key = (fact.topic, facts_rules.quote_key(fact))
        if key in seen_quotes:
            repeats.append(fact)
        seen_quotes.add(key)
    quoted = [(fact, url) for fact, url in quoted if all(fact is not repeat for repeat in repeats)]
    grades: dict[int, str] = {}
    if quoted:
        verdict: Graded = await call(
            facts_rules.JUDGE_PROMPT, Graded, facts_rules.judge_message(quoted, _without_chrome(pages)), school.id
        )
        grades = {n: rank for n in range(1, len(quoted) + 1) if (rank := verdict.rank(n))}
    passed = [(fact, url, grades[n]) for n, (fact, url) in enumerate(quoted, 1) if n in grades]
    kept = _ordered(passed)
    kept_ids = {id(fact) for fact, _, _ in kept}
    quoted_ids = {id(fact) for fact, _ in quoted}
    passed_ids = {id(fact) for fact, _, _ in passed}
    repeat_ids = {id(fact) for fact in repeats}

    def dropped(fact: Fact) -> Optional[str]:
        if id(fact) in kept_ids:
            return None
        if id(fact) in repeat_ids:
            return "same quote as an earlier fact"
        if id(fact) not in quoted_ids:
            return "quote not on page"
        return "over the limit" if id(fact) in passed_ids else "quote does not state it"

    status = {id(fact): dropped(fact) for fact in read.facts}
    rank_of = {id(fact): rank for fact, _, rank in passed}
    report["facts"] = [
        fact.model_dump()
        | {"kept": status[id(fact)] is None, "dropped": status[id(fact)], "rank": rank_of.get(id(fact))}
        for fact in read.facts
    ]
    carried = _carried(school, readable, kept)
    store = _ordered(kept + carried)
    report["carried"] = [fact.fact_en for fact, _, _ in carried if any(fact is f for f, _, _ in store)]
    report["store"] = [facts_rules.stored_fact(fact, url, rank) for fact, url, rank in store]
    outcome = f"{len(kept)} of {len(read.facts)} kept"
    return report | {"outcome": outcome + (f", {len(report['carried'])} carried" if report["carried"] else "")}


def _stored_on_site(school: School) -> list[dict[str, Any]]:
    """Stored facts read from the school's current website: after its site is replaced (587,
    drujba.org to dgs.drujba.org), facts from the old one are neither re-read nor carried."""
    from app.scrapers.shared_site_check import site_id

    stored = ((school.attributes or {}).get(ATTRIBUTE_KEY) or {}).get("facts") or []
    site = site_id(school.website_url)
    return [row for row in stored if isinstance(row, dict) and site and site_id(row.get("source_url")) == site]


def stored_fact_pages(school: School) -> list[str]:
    """The pages the stored key facts came from, then the others', most facts first.

    Read every time, so a stored fact is carried over (or dropped) on its own page's current
    text rather than lost because the picker chose other pages this run (514's /school-of-arts/)."""
    rows = _stored_on_site(school)
    counts = Counter((row.get("rank") != "key", row["source_url"]) for row in rows)
    return list(dict.fromkeys(url for (_, url), _ in sorted(counts.items(), key=lambda item: (item[0][0], -item[1]))))


def _carried(school: School, readable: list[tuple[str, str]], kept: list[Ranked]) -> list[Ranked]:
    """Stored facts whose quote is still on a page read, and not the quote of a new fact,
    with the grade they were stored with.

    Reads vary from run to run; a fact accepted before stays while its page still says it,
    with no model call, so the school's facts do not change on every refresh. New facts
    come first within each grade."""
    new_quotes = {facts_rules.quote_key(fact) for fact, _, _ in kept}
    carried = []
    for row in _stored_on_site(school):
        fact = facts_rules.stored_as_fact(row)
        if fact is None or fact.topic not in facts_rules.TOPICS or facts_rules.quote_key(fact) in new_quotes:
            continue
        url = facts_rules.quote_on_page(fact, readable)
        if url:
            facts_rules.check_link(fact, readable)  # a link the school has since removed goes
            new_quotes.add(facts_rules.quote_key(fact))
            carried.append((fact, url, row.get("rank") if row.get("rank") in facts_rules.RANKS else "key"))
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
        # Only a full read can list every taught language: not a partial crawl whose language
        # pages timed out, nor the open-days check's block (open_days.py).
        if report.get("pages_read", 0) >= FULL_READ_PAGES:
            block[LANGUAGES_READ_KEY] = True
        # The read takes a while and other stages write other attributes keys meanwhile:
        # re-read the row under a lock so only this key changes.
        await db.refresh(school, attribute_names=["attributes"], with_for_update=True)
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
    workers: int = 1,
    after_id: Optional[int] = None,
    lines_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Run over schools with an accepted website (all types, or the given ids), ``workers``
    schools at a time; returns the report.

    ``after_id`` starts after that school, to carry on a run that stopped or was split over
    several nights; ``lines_path`` gets each school's report as one JSON line, in id order,
    so ``after_id`` with the last line's id skips no school."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # type: ignore

    # Only websites URL validation accepted: facts from an unchecked URL would not be shown.
    query = select(School.id).where(
        School.country_code == country,
        School.website_url.isnot(None),
        School.scrape_status.in_(SITE_ACCEPTED_STATUSES),
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    if after_id is not None:
        query = query.where(School.id > after_id)
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
    call = model_caller(spend)
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
                        must_read=facts_rules.ADMISSION_LINK_RE,
                        first_urls=stored_fact_pages(school),
                        pick_tier=TIER,  # the picker too: no OpenRouter call in a facts run
                    )

                async def one(school_db: AsyncSession, current_id: int) -> dict[str, Any]:
                    # A failure here is that school's error (school_pool): its stored facts stay.
                    school = await school_db.get(School, current_id)
                    return await run_school(school_db, school, find=find, call=call, apply=apply)

                results, error = await school_pool.run_schools(
                    db,
                    ids,
                    one,
                    workers=workers,
                    stop_on=(SpendCapReached, ProviderCostError),
                    sites=sites,
                    on_result=(lambda result: school_pool.append_line(lines_path, result)) if lines_path else None,
                )
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
