"""Open-days stage: watch a few pages of each school for new open days and application dates.

Meant to run often (daily in season, from ``scripts/watch_open_days.sh``). For each school
with an accepted website it opens the home page, the pages it watched before, the pages its
stored open-day facts came from, and up to ``MAX_NEW_LINKS`` home-page links named for open
days or admissions; no model call so far. Only when the lines around an open-day or
application mention changed since the last check (``extraction/open_days.mention_hash``)
does one reader call list upcoming dates, which then pass the school-facts checks: quote on
a page read, sign-up link on a page, and the judge. Kept facts join the school's stored
facts (``attributes["school_facts"]``); a stored date fact whose quote is gone from a page
read again is removed. What each page hashed to is kept in ``attributes["open_days_watch"]``
(internal, never in a response). The default is a dry run that writes nothing. Not part of
``all``. Design: ``docs/SCHOOL_FACTS.md`` (Open days).
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Optional
from urllib.parse import unquote, urldefrag, urljoin

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import get_model
from app.config import CRAWLER_USER_AGENT, get_settings
from app.database import async_session_maker
from app.models import School
from app.models.pipeline_run import PipelineRun
from app.scrapers.extraction import open_days as rules
from app.scrapers.extraction import school_facts as facts_rules
from app.scrapers.extraction.open_days import DateFact, DateFacts
from app.scrapers.extraction.school_facts import Graded
from app.scrapers.fee_pages import fee_http_client, page_key
from app.scrapers.stages import common as stage_common
from app.scrapers.stages import school_facts as facts_stage
from app.scrapers.stages import school_pool
from app.scrapers.stages.fees_agent import THIN_PAGE_CHARS, PageRead, Spend, SpendCapReached, _fetch, _same_site
from app.services.pipeline_runs import finalize_pipeline_run, pipeline_run_heartbeat, start_pipeline_run
from app.services.provider_costs import ProviderCostError, provider_cost_scope
from app.utils.school_facts import ATTRIBUTE_KEY, SITE_ACCEPTED_STATUSES, _site

logger = logging.getLogger(__name__)

STAGE = "open-days"
WATCH_KEY = "open_days_watch"
DEFAULT_MAX_USD = 0.30
MAX_PAGES = 6  # pages opened per school per check
MAX_NEW_LINKS = 3  # home-page links named for open days or admissions
MAX_FOLLOWED = 2  # links on a changed mention line (a news headline's article)

# (final URL, text, same-site links) for a URL; text "" when it could not be read.
Fetcher = Callable[[str], Awaitable[tuple[str, str, list[tuple[str, str]]]]]
Caller = facts_stage.Caller


def _date_facts(school: School) -> list[dict]:
    block = (school.attributes or {}).get(ATTRIBUTE_KEY) or {}
    return [f for f in block.get("facts") or [] if isinstance(f, dict) and _stored_has_dates(f)]


def _stored_has_dates(row: dict) -> bool:
    return bool(row.get("open_days") or row.get("applications_open") or row.get("applications_close"))


def _block_matches_site(school: School) -> bool:
    """No stored facts, or facts read from the school's current website (others are hidden)."""
    block = (school.attributes or {}).get(ATTRIBUTE_KEY)
    return not block or _site(block.get("website_url")) == _site(school.website_url)


async def check_school(school: School, fetch: Fetcher, call: Caller, today: datetime.date) -> dict[str, Any]:
    """Open the watched pages, call the model only on a changed mention; returns the report
    with ``watch`` (the hashes to store) and, when the facts change, ``add`` (rows) and
    ``remove`` (ids of stored rows)."""
    site = school.website_url
    state = (school.attributes or {}).get(WATCH_KEY) or {}
    watch = state.get("pages") or {}
    report: dict[str, Any] = {"pages": []}
    pages: list[PageRead] = []
    seen: set[tuple] = set()

    async def read(url: str, limit: int = MAX_PAGES) -> Optional[tuple[str, list[tuple[str, str]]]]:
        if page_key(url) in seen or len(pages) >= limit:
            return None
        seen.add(page_key(url))
        try:
            final, text, links = await fetch(url)
        except Exception as exc:  # a failed page is recorded and skipped; its stored hash stays
            report["pages"].append({"url": url, "note": str(exc)[:120]})
            return None
        seen.add(page_key(final))
        if not _same_site(final, site):  # redirected to another site: its dates are not this school's
            report["pages"].append({"url": url, "note": f"redirected to {final}"})
            return None
        pages.append(PageRead(url, text, False))  # the URL asked for: the watch list's key
        report["pages"].append({"url": url, "chars": len(text)})
        return text, links

    home = await read(site)
    if not home or not home[0].strip():
        return report | {"outcome": "not_read"}
    named = [url for url, label in home[1] if _same_site(url, site) and rules.LINK_RE.search(f"{url} {label}")]
    named = named[:MAX_NEW_LINKS]
    open_day_links = [url for url in named if rules.MENTION_RE.search(unquote(url))]
    # Stored dates' pages, then links named for open days, then watched pages, then admissions
    # links; ``MAX_FOLLOWED`` places are left for the articles a changed headline links.
    queue = [*(row.get("source_url") for row in _date_facts(school)), *open_day_links, *watch, *named]
    for url in queue:
        if url:
            await read(urldefrag(url)[0], MAX_PAGES - MAX_FOLLOWED)

    hashes = {page.url: rules.mention_hash(page.text) for page in pages if page.text.strip()}
    # A page whose dates were stored and are gone (a full facts read did not carry them) is
    # read again even when its lines did not change.
    lost = {page_key(url) for url in state.get("dated") or []} - {
        page_key(row.get("source_url") or "") for row in _date_facts(school)
    }
    changed = [
        page
        for page in pages
        if hashes.get(page.url) and (hashes[page.url] != watch.get(page.url) or page_key(page.url) in lost)
    ]
    for page in list(changed):  # a changed headline: open the article it links
        for link in rules.mention_links(page.text)[:MAX_FOLLOWED]:
            url = urldefrag(urljoin(page.url, link))[0]
            if _same_site(url, site) and await read(url):
                new = pages[-1]
                hashes[new.url] = rules.mention_hash(new.text)
                if hashes[new.url] and hashes[new.url] != watch.get(new.url):
                    changed.append(new)
    # Pages that mention nothing are dropped from the list; a page that could not be read keeps its hash.
    report["watch"] = {url: h for url, h in {**watch, **hashes}.items() if h}
    report["changed"] = [page.url for page in changed]

    readable = [(page.url, page.text) for page in pages if page.text.strip()]
    removed = _gone(school, pages)
    kept: list[tuple[DateFact, str, str]] = []
    if changed:
        kept, report["facts"], relinked = await _read_dates(school, changed, readable, call, today, removed)
        removed += relinked
    if not kept and not removed:
        return report | {"outcome": "changed, nothing new" if changed else "unchanged"}
    # Merged into the stored facts at write time (``merged``), under the row lock.
    report["add"] = [facts_rules.stored_fact(fact, url, rank) for fact, url, rank in kept]
    report["remove"] = [_fact_id(row) for row in removed]
    report["removed"] = [row["text_i18n"]["en"] for row in removed]
    report["added"] = [fact.fact_en for fact, _, _ in kept]
    return report | {"outcome": f"{len(kept)} added, {len(removed)} removed"}


async def _read_dates(
    school: School,
    changed: list[PageRead],
    readable: list[tuple[str, str]],
    call: Caller,
    today: datetime.date,
    removed: Sequence[dict] = (),
) -> tuple[list[tuple[DateFact, str, str]], list[dict], list[dict]]:
    """The reader's upcoming dates that pass the facts checks: (fact, page, grade), the
    report rows, and the stored rows they replace (same quote, a new sign-up link)."""
    text = "\n\n".join(f"=== PAGE {page.url}\n{rules.excerpt(page.text, page.url)}" for page in changed)
    read: DateFacts = await call(
        rules.READER_PROMPT.format(today=today.isoformat()),
        DateFacts,
        f"Institution: {facts_stage._name(school)} ({school.school_type} {school.education_level})\n\n{text}",
        school.id,
    )
    rows, quoted, relinked = [], [], []
    stored: dict[str, Optional[dict]] = {
        facts_rules.quote_key(fact): row
        for row in ((school.attributes or {}).get(ATTRIBUTE_KEY) or {}).get("facts") or []
        if isinstance(row, dict)
        and all(row is not gone for gone in removed)
        and (fact := facts_rules.stored_as_fact(row))
    }
    for fact in read.facts:
        url = facts_rules.quote_on_page(fact, readable)
        why = (
            "quote not on page" if not url else "no date" if not rules.has_dates(fact) else rules.upcoming(fact, today)
        )
        if why is None:
            facts_rules.check_link(fact, readable)
            key = facts_rules.quote_key(fact)
            if key in stored:
                old = stored[key]
                # The same line with a new sign-up link replaces the stored fact; else it is a repeat.
                if old is not None and fact.register_url and fact.register_url != old.get("register_url"):
                    relinked.append(old)
                else:
                    why = "already stored"
            stored[key] = None  # the reader repeating a fact
        if why is None:
            quoted.append((fact, url))
        rows.append(fact.model_dump() | {"dropped": why})
    if not quoted:
        return [], rows, []
    verdict: Graded = await call(
        facts_rules.JUDGE_PROMPT,
        Graded,
        rules.judge_note(today, [fact for fact, _ in quoted]) + facts_rules.judge_message(quoted),
        school.id,
    )
    kept = []
    for n, (fact, url) in enumerate(quoted, 1):
        rank = verdict.rank(n)
        # An upcoming open day is worth showing: the judge's "minor" (it grades for choosing a
        # school, not for visiting one) would keep it out of the dates at a glance.
        if rank:
            kept.append((fact, url, "more" if rank == "minor" else rank))
        next(row for row in rows if row["quote"] == fact.quote)["dropped"] = None if rank else "quote does not state it"
    # A stored fact is replaced only by a new one that passed.
    kept_keys = {facts_rules.quote_key(fact) for fact, _, _ in kept}
    relinked = [
        row for row in relinked if (f := facts_rules.stored_as_fact(row)) and facts_rules.quote_key(f) in kept_keys
    ]
    return kept, rows, relinked


def _gone(school: School, pages: list[PageRead]) -> list[dict]:
    """Stored date facts from a page read again in full whose quote is no longer on it. (A
    sign-up link moved to a new form changes the hash; the new read replaces the fact.)"""
    full = [(page.url, page.text) for page in pages if len(page.text.strip()) >= THIN_PAGE_CHARS]
    read_keys = {page_key(url) for url, _ in full}
    gone = []
    for row in _date_facts(school):
        fact = facts_rules.stored_as_fact(row)
        if fact is None or page_key(row.get("source_url") or "") not in read_keys:
            continue
        if not facts_rules.quote_on_page(fact, full):
            gone.append(row)
    return gone


def _fact_id(row: dict) -> tuple[str, str]:
    return str(row.get("source_url")), str(row.get("quote"))


def merged(stored: list, add: list[dict], remove: Sequence[tuple[str, str]]) -> list[dict]:
    """The stored facts without the removed ones, with the new ones first, key facts first,
    ``MAX_FACTS`` at most: over the limit, the least useful fact without a date goes."""
    gone = {tuple(item) for item in remove}
    rows = add + [row for row in stored if isinstance(row, dict) and _fact_id(row) not in gone]
    rank = {name: n for n, name in enumerate(facts_rules.RANKS)}
    rows.sort(key=lambda row: rank.get(row.get("rank"), 0))
    while len(rows) > facts_rules.MAX_FACTS:
        undated = [n for n, row in enumerate(rows) if not _stored_has_dates(row)]
        del rows[undated[-1] if undated else -1]
    return rows


async def run_school(
    db: AsyncSession, school: School, *, fetch: Fetcher, call: Caller, apply: bool, today: datetime.date
) -> dict[str, Any]:
    report: dict[str, Any] = {"school_id": school.id, "name": facts_stage._name(school), "website": school.website_url}
    report |= await check_school(school, fetch, call, today)
    add, remove, watch = report.pop("add", None), report.pop("remove", None), report.pop("watch", None)
    if not apply or watch is None:
        return report
    # The check takes a while and other stages write other attributes keys meanwhile:
    # re-read the row under a lock so only these keys change. A website changed meanwhile
    # makes this check's dates another site's: nothing is written.
    checked_site = school.website_url
    await db.refresh(school, attribute_names=["attributes", "website_url"], with_for_update=True)
    if _site(school.website_url) != _site(checked_site):
        await db.rollback()
        return report | {"outcome": "website changed during the check"}
    attributes = dict(school.attributes or {})
    if add is not None:
        block = attributes.get(ATTRIBUTE_KEY) or facts_stage.facts_block(
            [], get_model(facts_stage.TIER), school.website_url
        )
        facts = merged(block.get("facts") or [], add, remove)
        attributes[ATTRIBUTE_KEY] = {**block, "facts": facts, "dates_checked_at": _now()}
        report["stored"] = len(facts)
    dated = [
        row.get("source_url")
        for row in (attributes.get(ATTRIBUTE_KEY) or {}).get("facts") or []
        if isinstance(row, dict) and _stored_has_dates(row)
    ]
    attributes[WATCH_KEY] = {"checked_at": _now(), "pages": watch, "dated": sorted({url for url in dated if url})}
    school.attributes = attributes
    await db.commit()
    return report


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


async def run_open_days(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = None,
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    max_usd: float = DEFAULT_MAX_USD,
    apply: bool = False,
    workers: int = 1,
) -> dict[str, Any]:
    """Check schools with an accepted website, ``workers`` at a time; returns the report.
    ``facts_changed`` counts schools whose stored facts changed (what a publish is for)."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # type: ignore
    from crawl4ai.utils import RobotsParser  # type: ignore

    query = select(School.id, School.website_url).where(
        School.country_code == country,
        School.website_url.isnot(None),
        School.scrape_status.in_(SITE_ACCEPTED_STATUSES),
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    if city:
        query = query.where(func.lower(School.city) == city.lower())
    rows = (await db.execute(query.order_by(School.id).limit(limit))).all()
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
    call = facts_stage.model_caller(spend, stage=STAGE)
    today = datetime.date.today()
    robots = RobotsParser()
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

                async def fetch(url: str) -> tuple[str, str, list[tuple[str, str]]]:
                    if not await robots.can_fetch(url, CRAWLER_USER_AGENT):
                        raise RuntimeError("robots.txt")
                    final, text, links, _ = await _fetch(crawler, run_config, client, url)
                    return final, text, links

                async def one(school_db: AsyncSession, current_id: int) -> dict[str, Any]:
                    school = await school_db.get(School, current_id)
                    if not _block_matches_site(school):
                        return {"school_id": current_id, "outcome": "facts from another website"}
                    return await run_school(school_db, school, fetch=fetch, call=call, apply=apply, today=today)

                results, error = await school_pool.run_schools(
                    db, ids, one, workers=workers, stop_on=(SpendCapReached, ProviderCostError), sites=sites
                )
    except Exception as exc:
        logger.exception("open-days run %s stopped", run_id)
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
        key = result["outcome"] if "added" not in result["outcome"] else "facts changed"
        outcomes[key] = outcomes.get(key, 0) + 1
    return {
        "apply": apply,
        "pipeline_run_id": run_id,
        "schools": len(results),
        "outcomes": outcomes,
        "facts_changed": sum("stored" in r for r in results) if apply else outcomes.get("facts changed", 0),
        "spent_usd": round(max(spend.spent_usd, float(run.total_llm_cost_usd or 0)), 6),
        "stopped": error,
        "results": results,
    }
