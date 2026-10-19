"""Fees-agent stage (R6 step 2): find a school's fee pages, read them once, check, store.

Plan: ``docs/AGENT_FEE_EXTRACTION.md``. Experimental until step 3 passes, so it is not part
of ``all``. The default is a dry run: nothing is written, the report says what would be.
Page-line checks live in ``extraction/agent_fees.py``; row replacement reuses the rules of
``extraction/price_step.py``.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urldefrag, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai.client import (
    MODEL_TIERS,
    create_agent,
    extract_provider_cost_usd,
    get_model,
    get_model_costs,
    get_provider,
)
from app.config import CRAWLER_USER_AGENT, get_settings
from app.database import async_session_maker
from app.models import Pricing, School
from app.models.field_source import FieldSource, SourceConfidence, SourceType
from app.models.pipeline_run import PipelineRun
from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.extraction import ExtractedPrice
from app.scrapers.extraction import llm_results, price_step, text_values
from app.scrapers.extraction.agent_fees import (
    READER_PROMPT,
    AgentDiscount,
    AgentFee,
    AgentFees,
    CheckedFee,
    _names_period,
    _without_option_lines,
    check_discount,
    check_fee,
    fee_amounts,
    number_spans,
    page_numbers,
    read_amounts,
)
from app.scrapers.fee_pages import fee_http_client, page_key, pdf_text
from app.scrapers.price_evidence import _PRICE_RE, PriceRow, _amount_key
from app.scrapers.shared_site_check import registrable_domain
from app.scrapers.stages import common as stage_common
from app.scrapers.stages import fee_check, school_pool
from app.scrapers.stages import fee_images as fee_images_module
from app.scrapers.stages.fee_images import fee_images
from app.scrapers.validator import validate_school_data
from app.services.pipeline_runs import finalize_pipeline_run, pipeline_run_heartbeat, start_pipeline_run
from app.services.provider_costs import (
    ProviderCostCapExceeded,
    ProviderCostError,
    ProviderCreditsExhausted,
    ProviderResponseUnattributed,
    execute_billable_request,
    provider_cost_scope,
)
from app.utils.academic_year import current_academic_year, normalize_academic_year
from app.utils.display_gating import _BGN_PER_EUR, blocked_pricing_row_ids, pricing_row_is_publishable
from app.utils.website_data import prepare_validation_rollover

logger = logging.getLogger(__name__)

STAGE = "fees-agent"
DEFAULT_MAX_USD = 0.50
MAX_HOPS = 3
MAX_PICKS = 4
MAX_FETCHES = 12
MAX_MUST_READ = 2
MAX_LINKS_SHOWN = 150
MAX_READ_CHARS = 120_000
THIN_PAGE_CHARS = 300
RETRY_SECONDS = 2.0
READ_OUTPUT_TOKENS_RESERVE = 6000
PICK_COST_ESTIMATE_USD = 0.002
# Output caps, so a call's worst case is known and the run's spend guard can count a call in
# flight, or one that timed out unbilled, at that worst case instead of the $0.10 default
# (which stopped a $1 Sofia run after $0.20-0.30 of real spend on 2026-10-16). 596's
# 49 fees took about 8k output tokens.
READ_MAX_OUTPUT_TOKENS = 32_000
PICK_MAX_OUTPUT_TOKENS = 1_000
MAX_PICK_INPUT_CHARS = 40_000
# When the picker returns nothing, follow links whose URL or text names fees or admissions.
_FEE_LINK_RE = re.compile(r"такс|цени|цена|прием|fee|tuition|pric|admission", re.IGNORECASE)
_SKIPPED = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".css", ".js", ".zip", ".mp4", ".mp3", ".doc", ".docx")

PICK_PROMPT = """You help find the pages of a school website that state its fees (tuition, monthly fee,
registration, food, transport, price lists, fee PDFs). You get the institution's name, the pages read so far
(with whether they already show prices), and the same-site links not yet opened, as "URL | link text".
Return up to 4 URLs, copied exactly from the list, most likely first, that are likely to state this
institution's fees or lead to them (admissions or "for parents" pages often do). Return none if nothing fits."""


class SpendCapReached(RuntimeError):
    """The next model call could take the run past its spend cap."""


class Spend:
    """The run's spend so far, plus the estimates of calls in flight (several schools read
    at once), checked against the cap before each call."""

    def __init__(self, cap_usd: float):
        self.cap_usd = cap_usd
        self.spent_usd = 0.0
        self.pending_usd = 0.0

    def allows(self, estimate_usd: float) -> bool:
        return self.spent_usd + self.pending_usd + estimate_usd <= self.cap_usd

    @contextmanager
    def hold(self, estimate_usd: float):
        """Count ``estimate_usd`` as spent while a call is in flight; refuse it over the cap."""
        if not self.allows(estimate_usd):
            raise SpendCapReached(f"spent ${self.spent_usd:.4f} of ${self.cap_usd}")
        self.pending_usd += estimate_usd
        try:
            yield
        finally:
            self.pending_usd -= estimate_usd


class Picks(BaseModel):
    # Required: with a default the cheap model calls the output tool with no URLs at all.
    urls: list[str] = Field(description="Up to 4 URLs copied exactly from the list; empty if none fits")


@dataclass
class PageRead:
    url: str
    text: str
    prices: bool
    note: Optional[str] = None


Finder = Callable[[School], Awaitable[list[PageRead]]]
Reader = Callable[[School, list[PageRead], Sequence[Decimal]], Awaitable[list[AgentFee]]]
Checker = Callable[[School, list[PageRead], list[AgentFee]], Awaitable[fee_check.FeeCheck]]


# ---------------------------------------------------------------------------
# Find: the home page, then the links a cheap model (or the keyword fallback) picks
# ---------------------------------------------------------------------------


def _same_site(url: str, site: str) -> bool:
    """The school's own domain, or the same name under another ending: an umbrella site
    (meridian22.org) often links each school's pages on its sister domain (meridian22.bg).
    Only for a school on a domain of its own: a home on sites.google.com or dgN.sofia.bg
    would otherwise take in every google.* or sofia.* link."""
    domain, home = registrable_domain(url), registrable_domain(site)
    if not domain or not home:
        return False
    if domain == home:
        return True
    host = (urlparse(site).hostname or "").lower().removeprefix("www.")
    name, home_name = domain.split(".", 1)[0], home.split(".", 1)[0]
    return host == home and len(home_name) >= 5 and name == home_name


async def _fetch(
    crawler, run_config, client, url: str
) -> tuple[str, str, list[tuple[str, str]], list[tuple[str, str, str]]]:
    """(final URL, text, same-site links, fee pictures). PDFs are read with pdfplumber, pages rendered."""
    if unquote(urlparse(url).path).lower().endswith(".pdf"):
        response = await client.get(url, follow_redirects=True, timeout=30)
        if response.status_code != 200:
            return url, "", [], []
        return str(response.url), await asyncio.to_thread(pdf_text, response.content, tables=True), [], []
    for attempt in range(2):  # a site that throttles returns an empty page now and then (583)
        result = await asyncio.wait_for(crawler.arun(url=url, config=run_config), timeout=60)
        if getattr(result, "success", False):
            break
        if attempt == 0:
            await asyncio.sleep(RETRY_SECONDS)
    else:
        return url, "", [], []
    markdown = getattr(result, "markdown", None)
    text = getattr(markdown, "raw_markdown", None) or str(markdown or "")
    found = getattr(result, "links", None) or {}
    links = [*(found.get("internal") or []), *(found.get("external") or [])]
    pairs = [(str(link["href"]), str(link.get("text") or "").strip()[:80]) for link in links if link.get("href")]
    final = str(getattr(result, "redirected_url", None) or url)
    return final, text, pairs, fee_images(getattr(result, "html", None) or "", final)


def _wordpress_api(url: str) -> tuple[str, dict[str, str]]:
    parsed = urlparse(url)
    slug = [part for part in parsed.path.split("/") if part][-1:] or ["home"]
    return f"{parsed.scheme}://{parsed.netloc}/wp-json/wp/v2/pages", {
        "slug": unquote(slug[0]),
        "_fields": "content,link",
    }


async def _wordpress_text(client, url: str) -> Optional[str]:
    """A page's content from the WordPress REST API, for sites that render it with scripts
    (606 shows 106 characters to the crawler; its fee table is in the API). None when the
    site has no such API, so the finder stops asking it."""
    api, params = _wordpress_api(url)
    try:
        response = await client.get(api, params=params, timeout=30)
        if response.status_code >= 500:
            return ""  # throttled or down for a moment: ask again for the next page
        items = response.json() if response.status_code == 200 else None
    except Exception:
        return ""
    if not isinstance(items, list):
        return None
    # A slug repeats under different parents (/detska-gradina/taksi and /uchilishte/taksi):
    # only the page at this URL will do.
    item = next((i for i in items if isinstance(i, dict) and page_key(str(i.get("link") or "")) == page_key(url)), None)
    if item is None:
        return ""
    html = (item.get("content") or {}).get("rendered") or ""
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)


async def _pick(
    school: School,
    read: list[PageRead],
    links: list[tuple[str, str]],
    spend: Spend,
    prompt: str = PICK_PROMPT,
    stage: str = STAGE,
) -> list[str]:
    if not spend.allows(PICK_COST_ESTIMATE_USD):
        raise SpendCapReached(f"spent ${spend.spent_usd:.4f} of ${spend.cap_usd}")
    settings = get_settings()
    # One output call: 517/616's picker sent 23 parallel calls that Google ended with an
    # error and OpenRouter returned with no cost or usage.
    agent = create_agent(
        "cheap",
        prompt,
        Picks,
        retries=1,
        model_settings={
            "temperature": settings.extraction_temperature,
            "parallel_tool_calls": False,
            "max_tokens": PICK_MAX_OUTPUT_TOKENS,
        },
    )
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    pages = "\n".join(f"- {page.url} ({'shows prices' if page.prices else 'no prices'})" for page in read)
    shown = "\n".join(f"{url} | {text}" for url, text in links[:MAX_LINKS_SHOWN])[:MAX_PICK_INPUT_CHARS]
    try:
        with spend.hold(PICK_COST_ESTIMATE_USD):
            result = await execute_billable_request(
                lambda: agent.run(f"Institution: {name}\n\nRead so far:\n{pages}\n\nLinks:\n{shown}"),
                model=get_model("cheap"),
                school_id=school.id,
                stage=stage,
                timeout_seconds=60,
            )
    except ProviderResponseUnattributed:
        # Counted at its assumed maximum, as the ledger counts it; the caller falls back to keyword links.
        spend.spent_usd += request_reserves().get(get_model("cheap"), float(settings.provider_request_reserve_usd))
        raise
    spend.spent_usd += extract_provider_cost_usd(result)
    allowed = {url for url, _ in links}
    return [url for url in result.output.urls if url in allowed][:MAX_PICKS]


async def find_pages(
    crawler,
    run_config,
    client,
    school: School,
    spend: Spend,
    *,
    pick_prompt: str = PICK_PROMPT,
    fallback_links: re.Pattern = _FEE_LINK_RE,
    stage: str = STAGE,
    read_pictures: bool = False,
    must_read: Optional[re.Pattern] = None,
) -> list[PageRead]:
    """Up to 3 hops and 12 pages from the home page; robots.txt is respected.

    With ``read_pictures``, up to 4 pictures named for fees are read as pages of their own.
    The school-facts stage passes its own link prompt, fallback keywords and stage name, and
    ``must_read``: links whose URL or text it matches are opened ahead of the picker's choices
    (``MAX_MUST_READ`` in all), since the picker can fill its places with other pages."""
    from crawl4ai.utils import RobotsParser  # type: ignore

    robots = RobotsParser()
    wordpress = True  # until the API is found missing
    site = school.website_url
    read: list[PageRead] = []
    seen: set[tuple] = set()
    links: dict[str, str] = {}
    queue = [site]
    pictures: list[tuple[str, str, str]] = []
    picture_pages: list[PageRead] = []  # read after the pages, so they take none of the page slots
    must_left = MAX_MUST_READ

    async def allowed(url: str) -> bool:
        return await robots.can_fetch(url, CRAWLER_USER_AGENT)

    def spent(cost: float) -> None:
        spend.spent_usd += cost

    for hop in range(MAX_HOPS + 1):
        for url in queue:
            if page_key(url) in seen or len(read) >= MAX_FETCHES:
                continue
            seen.add(page_key(url))
            if not await robots.can_fetch(url, CRAWLER_USER_AGENT):
                read.append(PageRead(url, "", False, "robots.txt"))
                continue
            try:
                final, text, page_links, page_pictures = await _fetch(crawler, run_config, client, url)
            except Exception as exc:  # a failed page is recorded and skipped
                read.append(PageRead(url, "", False, str(exc)[:120]))
                continue
            if page_key(final) != page_key(url) and page_key(final) in seen:
                continue  # a second link to a page already read (often a percent-encoded redirect)
            seen.add(page_key(final))
            is_pdf = unquote(urlparse(final).path).lower().endswith(".pdf")
            if (
                wordpress
                and not is_pdf
                and len(text.strip()) < THIN_PAGE_CHARS
                and await robots.can_fetch(_wordpress_api(final)[0], CRAWLER_USER_AGENT)
            ):
                api_text = await _wordpress_text(client, final)
                wordpress = api_text is not None
                text = api_text or text
            read.append(PageRead(final, text, bool(_PRICE_RE.search(text))))
            if read_pictures:
                for picture in page_pictures:
                    if len(pictures) >= fee_images_module.MAX_IMAGES or page_key(picture[0]) in seen:
                        continue
                    if not fee_images_module.on_site(picture, site):
                        continue
                    seen.add(page_key(picture[0]))
                    with spend.hold(fee_images_module.IMAGE_COST_ESTIMATE_USD):
                        pictures.append(picture)
                        found = await fee_images_module.read_image(
                            client,
                            picture,
                            page_url=final,
                            site_url=site,
                            school_id=school.id,
                            stage=stage,
                            allowed=allowed,
                            on_cost=spent,
                        )
                    if found is not None:
                        # A fee table's picture often prints bare numbers ("15 350") under a
                        # currency heading, which the price pattern misses (594): any amount
                        # sends it to the reader.
                        transcription = found[1].split("\n", 1)[-1]  # after the heading line
                        picture_pages.append(PageRead(found[0], found[1], bool(re.search(r"\d{2}", transcription))))
            for href, label in page_links:
                href = urldefrag(href)[0]
                if _same_site(href, site) and not unquote(href).lower().endswith(_SKIPPED):
                    links.setdefault(href, label)
        if hop == MAX_HOPS or len(read) >= MAX_FETCHES:
            break
        unopened = [(url, label) for url, label in links.items() if page_key(url) not in seen]
        if not unopened:
            break
        try:
            queue = await _pick(school, read, unopened, spend, pick_prompt, stage)
        except ProviderResponseUnattributed as exc:  # an errored pick (517, 616): no cost to read
            logger.warning("%s link pick came back without a cost for school %s: %s", stage, school.id, exc)
            queue = []
        except (SpendCapReached, ProviderCostError):  # a cap, or a run no longer running
            raise
        except Exception as exc:  # a timed-out pick (541) falls back to the keyword links below
            logger.warning("fees-agent link pick failed for school %s: %s", school.id, exc)
            queue = []
        # Places the picker leaves free go to the keyword links, this year's first: 594's
        # picker chose the application forms over /school-fees-2026-2027/ in one run of two.
        keyword = [
            url for url, label in unopened if url not in queue and fallback_links.search(f"{unquote(url)} {label}")
        ]
        this_year = _this_years_pages()
        keyword.sort(key=lambda url: not this_year.search(f"{unquote(url)} {links.get(url, '')}"))
        queue = (queue + keyword)[:MAX_PICKS]
        if must_read is not None and must_left:
            # 172's picker took the testimonials over /kandidatstvane/ in two runs of two.
            must: list[str] = []
            for url, label in sorted(unopened, key=lambda link: not this_year.search(f"{unquote(link[0])} {link[1]}")):
                path = unquote(urlparse(url).path).lower()
                if len(must) < must_left and must_read.search(f"{unquote(url)} {label}") and not path.endswith(".pdf"):
                    if page_key(url) not in {page_key(m) for m in must}:
                        must.append(url)
            must_left -= len(must)
            queue = must + [url for url in queue if url not in must]
        if not queue:
            break
    # Pictures first: they are short, and the reader's text is cut at its limit from the end.
    return picture_pages + read


def _this_years_pages() -> re.Pattern:
    """A URL or link text naming this school year or the next ("2026-2027", "2027/28")."""
    start = int(current_academic_year()[:4])
    years = [(str(year), str(year + 1)) for year in (start, start + 1)]
    return re.compile("|".join(rf"{first}\D{{0,3}}(?:{second}|{second[2:]})(?!\d)" for first, second in years))


# ---------------------------------------------------------------------------
# Read: one pricing-tier call over every page that shows a price
# ---------------------------------------------------------------------------


def reader_text(pages: list[PageRead]) -> str:
    """The text the reader sees: the pages one after another, cut to its limit."""
    return "\n\n".join(f"=== PAGE {page.url}\n{page.text}" for page in pages)[:MAX_READ_CHARS]


class FeeRead(list):
    """The fees of one read, with the discounts it also gave: a list to every reader."""

    discounts: list[AgentDiscount]


def reader_message(school: School, pages: list[PageRead], missed: Sequence[Decimal]) -> str:
    """The reader's whole input for one school; ``missed`` names amounts an earlier read left out."""
    text = reader_text(pages)
    if missed:
        text += (
            "\n\nAn earlier read of these pages left out amounts that the school's stored fees or a check of the "
            "pages have and "
            f"the pages still show: {', '.join(format(amount, 'f') for amount in missed)}. Read the pages again "
            "in full and list every fee, including every row of each fee table. Some of these "
            "amounts may be from an older year or not be fees: the rules above still decide."
        )
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    return f"Institution: {name} ({school.school_type} {school.education_level})\n\n{text}"


def reader_agent():
    """The pricing-tier reader; the fees eval runs the same agent on saved pages."""
    return create_agent(
        "pricing",
        READER_PROMPT,
        AgentFees,
        retries=1,
        model_settings={"temperature": get_settings().extraction_temperature, "max_tokens": READ_MAX_OUTPUT_TOKENS},
    )


def _worst_case_usd(tier: str, input_chars: int, max_output_tokens: int, attempts: int) -> float:
    """A call's most expensive outcome: every attempt resends the input and the previous
    attempts' output, reads at 1.5 characters a token (Cyrillic tokenizes densely) and
    writes the full output cap."""
    in_price, out_price = get_model_costs(tier)
    total, input_tokens = 0.0, input_chars / 1.5
    for _ in range(attempts):
        total += (input_tokens * in_price + max_output_tokens * out_price) / 1_000_000
        input_tokens += max_output_tokens
    return total


def request_reserves() -> dict[str, float]:
    """The run's spend guard counts a reader or picker call at its worst case (two attempts:
    the agent retries once on invalid output). Vision reads keep the default reserve."""
    reader = _worst_case_usd("pricing", MAX_READ_CHARS + len(READER_PROMPT) + 5_000, READ_MAX_OUTPUT_TOKENS, 2)
    picker = _worst_case_usd("cheap", MAX_PICK_INPUT_CHARS + len(PICK_PROMPT) + 5_000, PICK_MAX_OUTPUT_TOKENS, 2)
    # The read's fees follow the pages in the checker's input: a full page of fees is ~20K characters.
    checker = fee_check.worst_case_usd(MAX_READ_CHARS + len(fee_check.CHECK_PROMPT) + 25_000)
    worst = {"pricing": reader, "cheap": picker, fee_check.TIER: checker}
    reserves: dict[str, float] = {}
    for tier, usd in worst.items():
        model = get_model(tier)
        # MODEL_COSTS are the default models' prices, and a model another tier also resolves
        # to (an override, or vision) may serve calls bigger than this one: default reserve.
        sharing = [t for t in MODEL_TIERS if get_model(t) == model]
        if any(t not in worst or get_model(t) != MODEL_TIERS[t] for t in sharing):
            continue
        reserves[model] = max(usd, reserves.get(model, 0.0))  # one model on both tiers: the larger
    return reserves


async def _mark_reported(db: AsyncSession, school_ids: list[int]) -> None:
    """The checks of these schools reached GitHub: the next run may skip them while nothing changes.
    Until then a school is checked, and its wrong fees reported, again."""
    try:
        for school_id in school_ids:
            school = await db.get(School, school_id)
            await _lock_attributes(db, school)
            school.attributes = fee_check.mark_reported(school.attributes)
        await db.commit()
    except Exception:
        logger.exception("fees-agent could not record the reported fee checks")
        if db.in_transaction():
            await db.rollback()


async def check_read(school: School, pages: list[PageRead], fees: list[AgentFee], spend: Spend) -> fee_check.FeeCheck:
    """The fee check (stages/fee_check.py) over the pages the reader saw and the fees it gave."""
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""
    kind = f"{name} ({school.school_type} {school.education_level})"
    return await fee_check.check_fees(school.id, kind, reader_text(pages), fees, spend, stage=STAGE)


async def read_fees(school: School, pages: list[PageRead], spend: Spend, missed: Sequence[Decimal]) -> FeeRead:
    """Read the fees on ``pages``; ``missed`` names amounts an earlier read left out."""
    message = reader_message(school, pages, missed)
    in_price, out_price = get_model_costs("pricing")
    estimate = (len(message) / 3 * in_price + READ_OUTPUT_TOKENS_RESERVE * out_price) / 1_000_000
    if not spend.allows(estimate):  # before building the agent; ``hold`` below checks again
        raise SpendCapReached(f"spent ${spend.spent_usd:.4f} of ${spend.cap_usd}")
    agent = reader_agent()
    with spend.hold(estimate):
        result = await execute_billable_request(
            lambda: agent.run(message),
            model=get_model("pricing"),
            school_id=school.id,
            stage=STAGE,
            timeout_seconds=240,
        )
    spend.spent_usd += extract_provider_cost_usd(result)
    read = FeeRead(result.output.fees)
    read.discounts = result.output.discounts
    return read


# ---------------------------------------------------------------------------
# Check, build rows, replace
# ---------------------------------------------------------------------------


def _page_for(fee: AgentFee | AgentDiscount, pages: list[PageRead]) -> Optional[PageRead]:
    by_key = {page_key(page.url): page for page in pages}
    return by_key.get(page_key(fee.source_url)) or (pages[0] if len(pages) == 1 else None)


def _discount_report(discount: AgentDiscount, pages: list[PageRead]) -> dict[str, Any]:
    page = _page_for(discount, pages)
    checked = check_discount(discount, page.text) if page else None
    shown = checked or discount
    return {
        "kind": shown.kind,
        "percent": shown.percent,
        "amount": shown.amount,
        "currency": shown.currency,
        "child": shown.child,
        "text_i18n": shown.text_i18n.model_dump(),
        "source_url": page.url if page else shown.source_url,
        "kept": checked is not None,
    }


async def _lock_attributes(db: AsyncSession, school: School) -> None:
    """Re-read the school's attributes under a row lock before changing them.

    The read takes minutes and other stages (school facts, social links) write other
    attributes keys meanwhile; writing back the copy loaded before it would wipe them."""
    await db.refresh(school, attribute_names=["attributes"], with_for_update=True)


def _store_discounts(school: School, report: dict[str, Any]) -> None:
    """The kept discounts of this read; an empty list too: the site no longer offers them."""
    kept = [{k: v for k, v in d.items() if k != "kept"} for d in report["discounts"] if d["kept"]]
    block = {
        "_schema_version": 1,
        "read_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "website_url": school.website_url,
        "discounts": kept,
    }
    school.attributes = {**(school.attributes or {}), "fee_discounts": block}


def _new_source_page(school: School, page: PageRead, stored: Optional[SourcePage], new_id: int) -> SourcePage:
    """A stand-in for the page; a stored page keeps its id and its validity verdict."""
    now = text_values._utcnow_naive()
    return SourcePage(
        id=stored.id if stored else new_id,
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url=page.url,
        content_hash=hashlib.sha256(page.text.encode()).hexdigest(),
        page_category="pricing" if page.prices else None,
        raw_markdown=page.text,
        is_valid=stored.is_valid if stored else True,  # an unsettled verdict keeps its rows withheld
        last_scraped_at=now,
        last_changed_at=now,
        scrape_count=1,
    )


_SINGLE_PAYMENT_RE = re.compile(r"^\s*1\s*[xх×]\s*(?:€|eur|лв\.?)?\s*[\d\s.,]+(?:€|eur|лв\.?)?\s*$", re.IGNORECASE)


# "Plan 1", "План 2 – на вноски", "Вариант Б": the name of a way of paying, which says nothing once the fee
# carries its payment options (596's plans 1-3).
_PAYMENT_PLAN_NAME_RE = re.compile(
    r"^\s*(?:plan|план|option|опция|вариант)\s*(?:\d+|[a-zа-я]|[ivx]+)\s*(?:$|[-–—:/(,.])", re.IGNORECASE
)


def _band(row: Pricing) -> tuple[str, str]:
    """A row's kind of fee and the group it is for, as the site writes the group."""
    return (getattr(row.category, "value", row.category), " ".join(str(row.age_group or "").split()).casefold())


def _single_payment(fee: AgentFee) -> bool:
    """The fee is written as one payment of its whole amount: "1 x € 8.950"."""
    return any(_SINGLE_PAYMENT_RE.match(text) for text in [fee.label, *fee.installments])


_DIGITS_RE = re.compile(r"\d+")


def _english_for(site_words: str | None, english: str | None) -> str | None:
    """The reader's English for the site's words, kept only when it states the same numbers
    ("Paid for 6 months at once" for "за 6 месеца"): a changed or added number is a wrong
    fee description, and the page then shows the site's own words instead."""
    english = " ".join((english or "").split())
    if not site_words or not english or len(english) > 100:
        return None
    if sorted(_DIGITS_RE.findall(site_words)) != sorted(_DIGITS_RE.findall(english)):
        return None
    return english


def _ungrouped_elsewhere(entry: tuple, built: list[tuple]) -> bool:
    """The entry has a group, and another page lists the same fee with none: 596's secondary
    sheet repeats the primary sheet's extras (transport, uniforms, registration) and a read
    may name the group "Secondary School" on that page only. One fee, kept as the ungrouped row.
    A plan name must match, or be blank on one side."""
    _, page, fields, _, _ = entry
    if fields["age_group"] is None:
        return False

    def same(other: dict) -> bool:
        plans = (fields["plan_name"], other["plan_name"])
        return None in plans or plans[0] == plans[1]

    rest = {name: value for name, value in fields.items() if name not in ("age_group", "plan_name")}
    return any(
        other_page.id != page.id
        and other["age_group"] is None
        and other_result.confidence >= entry[0].confidence  # never give way to a held row
        and same(other)
        and {name: value for name, value in other.items() if name not in ("age_group", "plan_name")} == rest
        for other_result, other_page, other, _, _ in built
    )


def _build_rows(
    school: School, checked: list[tuple[CheckedFee, SourcePage]]
) -> tuple[list[Pricing], list[FieldSource]]:
    rows: list[Pricing] = []
    sources: list[FieldSource] = []
    built = []
    for result, page in checked:
        fee = result.fee
        # The name of a way of paying says nothing once the fee carries its payment options.
        plan_name_dropped = bool(fee.payment_options and _PAYMENT_PLAN_NAME_RE.match(fee.plan_name or ""))
        fields = price_step._normalized_price_fields(
            ExtractedPrice(
                category=fee.category.value,
                amount=fee.amount,
                currency=fee.currency,
                period=fee.period.value if fee.period else None,
                amount_min=fee.amount_min,
                amount_max=fee.amount_max,
                plan_name=None if plan_name_dropped else fee.plan_name,
                academic_year=fee.academic_year,
                age_group=fee.age_group,
                confidence=result.confidence,
            )
        )
        if fields is not None:
            # Single payments of one plan price are the same fee whatever they are called.
            label = "" if _single_payment(fee) else " ".join(fee.label.split()).casefold()
            english = {
                "plan_name_en": None if plan_name_dropped else _english_for(fields.get("plan_name"), fee.plan_name_en),
                "age_group_en": _english_for(fields.get("age_group"), fee.age_group_en),
            }
            built.append((result, page, fields, label, english))

    # A plan paid "1 x 8950" restates the plan's 8950 a second time (596): drop such a single
    # payment when the same fee also comes without it, with or without its period.
    def unperiodic(fields: dict) -> tuple:
        return tuple(value for name, value in fields.items() if name != "period")

    restated = {unperiodic(f) for r, _, f, _, _ in built if not _single_payment(r.fee)}
    built = [b for b in built if not (_single_payment(b[0].fee) and unperiodic(b[2]) in restated)]
    built = [b for b in built if not _ungrouped_elsewhere(b, built)]
    # The same fee on two pages is one row (564 lists its fees on three). On one page a
    # second fee with the same fields but its own label is a fee of its own: two grade
    # tiers at one price when the reader left their groups empty. So each set of fields
    # comes from the one page that lists it most often, once per label.
    labels: dict[tuple, dict[Any, set[str]]] = {}
    for _, page, fields, label, _ in built:
        labels.setdefault(tuple(fields.values()), {}).setdefault(page.id, set()).add(label)
    taken: set[tuple] = set()
    for result, page, fields, label, english in built:
        key = tuple(fields.values())
        by_page = labels[key]
        if page.id != max(by_page, key=lambda page_id: len(by_page[page_id])) or (key, label) in taken:
            continue
        taken.add((key, label))
        fee = result.fee
        rows.append(
            Pricing(
                school_id=school.id,
                **fields,
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=page.source_url,
                source_page_id=page.id,
                source_page=page,
                pricing_context={
                    "extractor": "agent",
                    "confidence": result.confidence,
                    "checks": result.checks,
                    "evidence": fee.evidence,
                    "label": fee.label,
                    "installments": fee.installments,
                    "payment_options": [option.model_dump(exclude_none=True) for option in fee.payment_options],
                    "notes": fee.notes,
                    # The site's words in English for the English page; the columns keep the site's own.
                    **{key: value for key, value in english.items() if value},
                },
            )
        )
        sources.append(
            FieldSource(
                school_id=school.id,
                category="pricing",
                field_key=f"pricing.{fields['category'].value}.{fields['period'].value if fields['period'] else 'unstated'}",
                value_text=llm_results._format_price_value_text(
                    fields["amount"], fields["amount_min"], fields["amount_max"], fields["currency"]
                ),
                source_type=SourceType.SCRAPED_WEBSITE,
                source_url=page.source_url,
                scraped_at=text_values._utcnow_naive(),
                confidence=SourceConfidence.HIGH if result.confidence >= 0.8 else SourceConfidence.MEDIUM,
                confidence_score=result.confidence,
            )
        )
    _name_unnamed_alike(rows)
    return rows, sources


def _name_unnamed_alike(rows: list[Pricing]) -> None:
    """Fees of one kind, group, year and period that the reader left unnamed read as the
    same fee twice (596's uniforms: "Uniforms" 25, 14, 28, 18). Each takes the site's own
    label ("Sweatshirt") as its name when the labels tell them apart."""
    alike: dict[tuple, list[Pricing]] = {}
    for row in rows:
        if not row.plan_name:
            key = (row.category, row.age_group, row.period, row.academic_year)
            alike.setdefault(key, []).append(row)
    for group in alike.values():
        labels = [" ".join(str(row.pricing_context.get("label") or "").split()) for row in group]
        if len(group) > 1 and all(labels) and len({label.casefold() for label in labels}) == len(labels):
            for row, label in zip(group, labels):
                row.plan_name = label[:100]


async def _upsert_pages(db: AsyncSession, pages: dict[str, SourcePage], stored: dict[tuple, SourcePage]) -> None:
    """Write each page read as a ``source_pages`` row (in place when the page is known)."""
    for url, new in pages.items():
        existing = stored.get(page_key(url))
        if existing is None:
            new.id = None
            db.add(new)
            continue
        if existing.content_hash != new.content_hash:
            existing.content_hash, existing.last_changed_at = new.content_hash, new.last_changed_at
        # Validity stays as other stages set it; run_school skips pages they ruled out.
        existing.raw_markdown, existing.last_scraped_at = new.raw_markdown, new.last_scraped_at
        existing.scrape_count = (existing.scrape_count or 0) + 1
        existing.page_category = new.page_category or existing.page_category
        pages[url] = existing
    await db.flush()


EURO_TOLERANCE = Decimal("0.005")  # a site's own conversion is often rounded: 1369 лв. for 700 €


def _amount_group(row: Any) -> tuple[str, ...]:
    return _amount_key(PriceRow.from_pricing(row))


class _InEuro:
    """An old BGN row seen at the EUR amounts the read gives for it, so rule 6 checks its
    period, plan and count like any other row (330's 899.68 BGN is 460 EUR)."""

    def __init__(self, row: Pricing, amounts: tuple[Any, Any, Any]):
        self._row = row
        self.currency = "EUR"
        self.amount, self.amount_min, self.amount_max = amounts

    def __getattr__(self, name: str) -> Any:
        return getattr(self._row, name)


def _in_euro(row: Pricing, proposed: list[Pricing]) -> Optional[_InEuro]:
    if (row.currency or "").upper() != "BGN":
        return None
    euro = [  # the same kind of fee: 200 лв. of meals is not a 102 € bus fee
        Decimal(str(value))
        for new in proposed
        if (new.currency or "").upper() == "EUR" and new.category == row.category
        for value in (new.amount, new.amount_min, new.amount_max)
        if value is not None
    ]
    converted = []
    for value in (row.amount, row.amount_min, row.amount_max):
        if value is None:
            converted.append(None)
            continue
        target = Decimal(str(value)) / _BGN_PER_EUR
        match = next((amount for amount in euro if abs(amount - target) <= target * EURO_TOLERANCE), None)
        if match is None:
            return None
        converted.append(match)
    return _InEuro(row, tuple(converted))


async def _superseded_rows(db: AsyncSession, school: School, rows: list[Pricing]) -> list[Pricing]:
    """The stored website rows the read replaces: the years it produced (as the pipeline
    does), and a row dated this school year whose fee the read gives with no year. The
    pipeline already treats an undated old row as the fee its dated re-read names; this is
    the other way round. At 170 the read gave 4500, 4700, 5100 and 4000 EUR with no year
    and the rows stored under 2026-2027 stayed beside them."""
    replaced = await price_step._find_superseded_rows(db, school, rows)
    undated_fees = {
        (row.category, float(row.amount), (row.currency or "").upper())
        for row in rows
        if row.amount is not None and not str(row.academic_year or "").strip()
    }
    if not undated_fees:
        return replaced
    current = current_academic_year()
    stored = await db.execute(
        select(Pricing)
        .where(Pricing.school_id == school.id, Pricing.source == PriceSource.SCRAPED_WEBSITE)
        .options(selectinload(Pricing.source_page).load_only(SourcePage.id, SourcePage.is_valid))
    )
    ids = {row.id for row in replaced}
    replaced.extend(
        row
        for row in stored.scalars().all()
        if row.id not in ids
        and row.amount is not None
        and normalize_academic_year(row.academic_year) == current
        and (row.category, float(row.amount), (row.currency or "").upper()) in undated_fees
    )
    return replaced


def _refiled(olds: list[Any], proposed: list[Pricing]) -> bool:
    """True when the read files an old tuition row's amount as another kind of fee, so its row
    replaces the old one even without the old row's period. The old pipeline filed every
    amount on a fee page as tuition (506's learning support, stored as yearly tuition).

    Only one old row and one read row at that amount, in the same currency and year: a missed
    fee that shares its amount with an unrelated one is still kept.
    """
    if len(olds) != 1 or olds[0].category != PriceCategory.TUITION:
        return False
    old = olds[0]
    old_year = normalize_academic_year(old.academic_year)

    def same_year(new: Pricing) -> bool:
        year = normalize_academic_year(new.academic_year)
        return year == old_year or (year is None and old_year in (None, current_academic_year()))

    key = _amount_group(old)
    matches = [
        new
        for new in proposed
        if _amount_group(new) == key and (new.currency or "").upper() == (old.currency or "").upper() and same_year(new)
    ]
    return len(matches) == 1 and matches[0].category != PriceCategory.TUITION


def _rows_to_keep(
    school: School,
    pages: list[SourcePage],
    superseded: list[Pricing],
    proposed: list[Pricing],
    listed: Iterable[tuple[str, Decimal]] = (),
) -> tuple[list[Pricing], set[tuple[str, ...]], list[str]]:
    """The superseded rows rule 6 says replacing would lose, the amounts they hold, and why.

    Checked one amount at a time, so a fee the read missed keeps its own row and does not
    hold back the rest of the school's fees.
    """
    blocked = blocked_pricing_row_ids(school.attributes if isinstance(school.attributes, dict) else None)
    groups: dict[tuple[str, ...], list[Any]] = {}
    for row in superseded:
        seen_as = _in_euro(row, proposed) or row
        groups.setdefault(_amount_group(seen_as), []).append(seen_as)
    kept: list[Pricing] = []
    keys: set[tuple[str, ...]] = set()
    reasons: list[str] = []
    for key, olds in groups.items():
        if _refiled(olds, proposed):
            continue
        lost = price_step._pricing_replacement_regressions(school, pages, olds, proposed, listed)
        if lost:  # only what rule 6 protects: a withheld or blocked row at that amount still goes
            rows = [old._row if isinstance(old, _InEuro) else old for old in olds]
            protected = [row for row in rows if row.id not in blocked and pricing_row_is_publishable(row)]
            if protected:
                kept.extend(protected)
                keys.add(key)
                reasons.extend(lost)
    return kept, keys, reasons


async def _missed_amounts(
    db: AsyncSession, school: School, pages: list[PageRead], fees: list[AgentFee]
) -> list[Decimal]:
    """Amounts of the school's published website rows that the pages show and the read left out.

    Only rows of this school year or later, or undated: an older year's table still on the
    page is history, not a missed fee. A lev amount the read gives in euro is not missed.
    """
    stored = await db.execute(
        select(Pricing)
        .where(Pricing.school_id == school.id, Pricing.source == PriceSource.SCRAPED_WEBSITE)
        .options(selectinload(Pricing.source_page).load_only(SourcePage.id, SourcePage.is_valid))
    )
    blocked = blocked_pricing_row_ids(school.attributes if isinstance(school.attributes, dict) else None)
    current = current_academic_year()
    read = {amount for fee in fees for amount in read_amounts(fee)}
    euro = _euro_amounts(fees)
    published = set()
    for row in stored.scalars().all():
        year = normalize_academic_year(row.academic_year)
        if row.amount is None or row.id in blocked or (year and year < current) or not pricing_row_is_publishable(row):
            continue
        amount = Decimal(str(row.amount)).normalize()
        if (row.currency or "").upper() == "BGN":
            target = amount / _BGN_PER_EUR
            if any(abs(value - target) <= target * EURO_TOLERANCE for value in euro.get(row.category.value, ())):
                continue
        published.add(amount)
    # What the reader can see (not what was cut off), without the page headers' URLs.
    shown = page_numbers(re.sub(r"^=== PAGE .*$", "", reader_text(pages), flags=re.MULTILINE))
    return sorted((published & shown) - read)


async def _stored_proofs(db: AsyncSession, school: School) -> list[Pricing]:
    """The school's published agent rows, with the evidence that proved them."""
    stored = await db.execute(
        select(Pricing)
        .where(Pricing.school_id == school.id, Pricing.source == PriceSource.SCRAPED_WEBSITE)
        .options(selectinload(Pricing.source_page).load_only(SourcePage.id, SourcePage.is_valid))
    )
    blocked = blocked_pricing_row_ids(school.attributes if isinstance(school.attributes, dict) else None)
    return [
        row
        for row in stored.scalars().all()
        if isinstance(row.pricing_context, dict)
        and row.pricing_context.get("extractor") == "agent"
        and isinstance(row.pricing_context.get("evidence"), str)
        and row.pricing_context["evidence"].strip()
        and row.id not in blocked
        and pricing_row_is_publishable(row)
    ]


def _checked(fee: AgentFee, page: PageRead, proofs: list[Pricing]) -> CheckedFee:
    result = check_fee(fee, page.text)
    if not result.kept or result.fee.period is None:
        result = _with_stored_proof(result, page.text, proofs, fee.period)
    return result


# What a stored row's evidence can stand in for: the read's own copy of the line is not the page's.
_NOT_COPIED = ("dropped: evidence line is not on the page", "dropped: evidence line does not hold the amount")
# How far from the amount's line a stored heading or sentence may be found today and still be
# read as the one over it: a heading that moved to another table no longer heads this amount.
_PROOF_LINES_AWAY = 12


def _lines(text: str) -> list[str]:
    return [" ".join(line.split()).casefold() for line in text.splitlines() if line.strip()]


def _amount_lines(evidence: str, fee: AgentFee) -> list[str]:
    amounts = set(fee_amounts(fee))
    return [line for line in _lines(evidence) if amounts & page_numbers(line)]


def _holds(text: str, line: str) -> bool:
    """The page line holds the evidence line as whole words: "€ 300" is not in "€ 3000"."""
    return re.search(rf"(?<!\w){re.escape(line)}(?!\w)", text) is not None


def _prints_once(line: str, fee: AgentFee) -> bool:
    """The line prints the fee's amount once: "Начален | € 3000 | Гимназия | € 3000" is two groups."""
    return all(sum(amount in values for _, _, values in number_spans(line)) == 1 for amount in fee_amounts(fee))


def _proof_still_reads(evidence: str, fee: AgentFee, page_lines: list[str]) -> bool:
    """Every line of a stored proof is on today's page within a few lines of the amount's own."""
    places = [[index for index, text in enumerate(page_lines) if _holds(text, line)] for line in _lines(evidence)]
    starts = {
        index for line in _amount_lines(evidence, fee) for index, text in enumerate(page_lines) if _holds(text, line)
    }
    return all(places) and any(
        all(any(abs(index - start) <= _PROOF_LINES_AWAY for index in found) for found in places) for start in starts
    )


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def _same_group(row: Pricing, fee: AgentFee) -> bool:
    def norm(value: Optional[str]) -> str:
        return " ".join(str(value or "").split()).casefold()

    return norm(row.age_group) == norm(fee.age_group) and norm(row.plan_name) == norm(fee.plan_name)


def _with_stored_proof(
    result: CheckedFee, page_text: str, proofs: list[Pricing], read_period: Optional[PricePeriod]
) -> CheckedFee:
    """The fee checked with the evidence a stored row of the same fee was proved by, when the
    read's own evidence falls short and that proof still holds on today's page.

    The reader does not copy the same lines every run. In the 2026-10-15 Sofia dry run it
    left a table's heading or the sentence above it out of the evidence (610's "Таксата за
    обучение е на годишна база."), so the period was cleared, and it rebuilt table rows in
    a layout the page does not have (596's secondary tuition), so the fee was dropped; on
    unchanged pages. A stored proof is used only when it is this fee's: for a period, it
    holds the same line of the page for the amount as the new read, a line the page prints
    once (two groups at one price are two lines); for a dropped fee, it is the same group
    and plan. Its lines must still sit near the amount, and it goes through the same checks
    on today's page, so nothing comes back that the page no longer shows.
    """
    fee = result.fee
    key = _amount_key(PriceRow(fee.category.value, fee.amount, fee.amount_min, fee.amount_max, None, None, None))
    year = normalize_academic_year(fee.academic_year)
    page_lines = _lines(page_text)
    same_fee = [
        row
        for row in proofs
        if row.category == fee.category
        and (row.currency or "").upper() == fee.currency.upper()
        and _amount_group(row) == key
        and normalize_academic_year(row.academic_year) in (year, None)
        and _proof_still_reads(row.pricing_context["evidence"], fee, page_lines)
    ]
    if not result.kept and result.checks and result.checks[0] in _NOT_COPIED:
        evidence = {row.pricing_context["evidence"] for row in same_fee if _same_group(row, fee)}
        if len(evidence) != 1:  # none, or stored rows of this group that read differently
            return result
        trial = check_fee(fee.model_copy(update={"evidence": evidence.pop()}), page_text)
        if not trial.kept:
            return result
        note = "evidence from the stored row: the read's copy is not on the page"
        result = CheckedFee(trial.fee, trial.confidence, True, [*trial.checks, note])
    if result.fee.period is not None:
        return result
    fee = result.fee
    unique = {
        line
        for line in _amount_lines(fee.evidence, fee)
        if _prints_once(line, fee) and sum(_holds(text, line) for text in page_lines) == 1
    }
    same_line = [row for row in same_fee if unique & set(_amount_lines(row.pricing_context["evidence"], fee))]
    group = " ".join((fee.age_group or "").split()).casefold()
    own_lines = _amount_lines(fee.evidence, fee)
    if not same_line and group and all(_holds(line, group) for line in own_lines):
        # The read copied the table in another layout than the stored proof: 596's PDF prints
        # its tuition as text under "ANNUAL TUITION FEES" and again as cells fifty lines down,
        # and a re-read quoted the cells alone. A proof of the same named group still stands
        # when both amount lines name the group ("Grade 0"), and the read's own line names no
        # other period (a payment option's "9 x € 750 monthly" aside), and the stored proof holds every word of the read's line: "Роботика |
        # 1–4 клас | 300 лв." is not the "Шах | 1–4 клас | 300 лв. годишно".
        same_line = [
            row
            for row in same_fee
            if _same_group(row, fee)
            and all(_holds(line, group) for line in _amount_lines(row.pricing_context["evidence"], fee))
            and _words("\n".join(own_lines)) <= _words(row.pricing_context["evidence"])
            and not any(
                _names_period(other, _without_option_lines(own_lines, fee), fee_amounts(fee), fee.category)
                for other in PricePeriod
                if other is not row.period
            )
        ]
    periods = {row.period for row in same_line}
    if len(periods) != 1 or None in periods:  # none, or stored rows of this fee that disagree
        return result
    period = periods.pop()
    if read_period not in (None, period):  # the read says another period: no proof settles which
        return result
    evidence = "\n".join([fee.evidence, *(row.pricing_context["evidence"] for row in same_line)])
    trial = check_fee(fee.model_copy(update={"period": period, "evidence": evidence}), page_text)
    if not trial.kept or trial.fee.period != period or trial.confidence < result.confidence:
        return result
    note = f"period {period.value} from the stored row's evidence, still on the page"
    return CheckedFee(trial.fee, trial.confidence, True, [*result.checks, note])


def _euro_amounts(fees: list[AgentFee]) -> dict[str, set[Decimal]]:
    """The euro amounts read, by category: 200 лв. of meals is not a 102 € bus fee."""
    euro: dict[str, set[Decimal]] = {}
    for fee in fees:
        if (fee.currency or "").upper() == "EUR":
            euro.setdefault(fee.category.value, set()).update(fee_amounts(fee))
    return euro


def _lev_twin(fee: AgentFee, euro: dict[str, set[Decimal]]) -> bool:
    if (fee.currency or "").upper() != "BGN":
        return False
    targets = [amount / _BGN_PER_EUR for amount in fee_amounts(fee)]
    values = euro.get(fee.category.value, ())
    return bool(targets) and all(any(abs(v - t) <= t * EURO_TOLERANCE for v in values) for t in targets)


def _fee_report(result: CheckedFee, page: Optional[PageRead]) -> dict[str, Any]:
    fee = result.fee
    return {
        "category": fee.category.value,
        "amount": fee.amount,
        "amount_min": fee.amount_min,
        "amount_max": fee.amount_max,
        "currency": fee.currency,
        "period": fee.period.value if fee.period else None,
        "label": fee.label,
        "plan_name": fee.plan_name,
        "plan_name_en": fee.plan_name_en,
        "academic_year": fee.academic_year,
        "age_group": fee.age_group,
        "age_group_en": fee.age_group_en,
        "payment_options": [option.model_dump(exclude_none=True) for option in fee.payment_options],
        "kept": result.kept,
        "confidence": result.confidence,
        "checks": result.checks,
        "source_url": page.url if page else fee.source_url,
        "evidence": fee.evidence,
    }


async def page_gone(client, url: str) -> bool:
    """The page answers 404 or 410. A site that does not answer now is not gone."""
    try:  # the status line is enough: a stored fee PDF is not downloaded again
        async with client.stream("GET", url, follow_redirects=True, timeout=30) as response:
            return response.status_code in {404, 410}
    except httpx.HTTPError:
        return False


async def run_school(
    db: AsyncSession,
    school: School,
    *,
    find: Finder,
    read: Reader,
    apply: bool = False,
    gone: Optional[Callable[[str], Awaitable[bool]]] = None,
    check: Optional[Checker] = None,
) -> dict[str, Any]:
    """Find, read, check and (with ``apply``) store one school's fees. Returns its report.

    ``check`` (the fee check) reads the pages against the first read; the amounts it says
    were missed join the second read's.
    """
    report: dict[str, Any] = {"school_id": school.id, "outcome": None, "pages": [], "fees": [], "discounts": []}
    pages = await find(school)
    report["pages"] = [{"url": p.url, "chars": len(p.text), "prices": p.prices, "note": p.note} for p in pages]
    # A known page keeps its id; a new one gets a negative placeholder until ``apply`` stores it.
    stored_pages = (await db.execute(select(SourcePage).where(SourcePage.school_id == school.id))).scalars().all()
    # Matched by page_key so a trailing slash or encoding difference finds the stored page.
    stored = {page_key(page.source_url): page for page in stored_pages if page.scrape_type == ScrapeType.WEBSITE}
    # A price page the finder did not reach may be gone: 538's old fees address answers 404 and
    # its last-year rows stayed beside the new ones. Marked invalid, its rows stop being published
    # and the keep rule no longer holds them. A dry run only reports it.
    if gone is not None:
        reached = {page_key(page.url) for page in pages}
        priced_ids = set(
            (
                await db.execute(
                    select(Pricing.source_page_id).where(
                        Pricing.school_id == school.id, Pricing.source == PriceSource.SCRAPED_WEBSITE
                    )
                )
            ).scalars()
        )
        for page in stored.values():
            if page.id in priced_ids and page.is_valid and page_key(page.source_url) not in reached:
                if await gone(page.source_url):
                    report.setdefault("pages_gone", []).append(page.source_url)
                    if apply:
                        page.is_valid = False
        if apply and report.get("pages_gone"):  # saved now, whatever the read below does
            await db.commit()
    priced = [page for page in pages if page.prices]
    if not priced:
        report["outcome"] = "kept_old_rows"
        report["reason"] = "no page showing prices"
        return report

    fees = await read(school, priced, ())
    # The first read's; a reread only adds fees. None (a reader that gives no discounts)
    # leaves the stored ones alone; an empty list means the pages offer none now.
    discounts = getattr(fees, "discounts", None)
    ruled_out = [page.url for page in priced if getattr(stored.get(page_key(page.url)), "is_valid", None) is False]
    if ruled_out:
        report["outcome"] = "kept_old_rows"
        report["reason"] = f"page ruled invalid by an earlier stage: {ruled_out[0]}"
        return report
    source_pages = {
        page.url: _new_source_page(school, page, stored.get(page_key(page.url)), -(index + 1))
        for index, page in enumerate(pages)
        if page.text.strip()
    }
    # The reader can stop part way down a fee table: at 170 it gave the same 5 of 14 fees in
    # three runs. When the pages still show amounts the published rows have and the read left
    # out, read once more, naming them, and add the fees at amounts the first read did not give.
    # A fee the first read gave but the checks drop counts as missed too: 596's first read
    # stitched its secondary tuition from lines not on the page in one run of two.
    proofs = await _stored_proofs(db, school)
    passing = [fee for fee in fees if (page := _page_for(fee, priced)) and _checked(fee, page, proofs).kept]
    missed = await _missed_amounts(db, school, priced, passing)
    digest = fee_check.pages_hash(reader_text(priced))
    if check is not None and fee_check.already_checked(school.attributes, digest, passing):
        report["check"] = {"skipped": "pages unchanged since the last check"}
    elif check is not None:
        try:
            found = await check(school, priced, passing)
        except ProviderCreditsExhausted:  # the checker's account refuses: stop, or every school pays a read for it
            raise
        except (SpendCapReached, ProviderCostCapExceeded) as exc:  # the read is paid for: use it
            report["check"] = {"error": str(exc)}
        except ProviderCostError:  # the run is no longer running: write nothing
            raise
        except Exception as exc:  # a failed check leaves the read as it was
            logger.warning("fees-agent check failed for school %s: %s", school.id, exc)
            report["check"] = {"error": str(exc)[:200]}
        else:
            report["check"] = fee_check.check_report(found, passing)
            extra = fee_check.amounts_to_reread(found, reader_text(priced), passing)
            report["check"]["reread"] = [format(amount, "f") for amount in extra]
            missed = sorted(set(missed) | set(extra))
            if apply:  # saved now: the check is paid for whatever the rest of the read does
                await _lock_attributes(db, school)
                school.attributes = fee_check.checked(school.attributes, digest, passing, reported=not found.wrong)
                await db.commit()
    if missed:
        report["reread"] = {"missed": [format(amount, "f") for amount in missed], "added": 0}
        try:
            again = await read(school, priced, missed)
        except (SpendCapReached, ProviderCostCapExceeded) as exc:  # the first read is paid for: use it
            report["reread"]["error"] = str(exc)  # the next school's first read stops the run
            again = []
        except ProviderCostError:  # the run is no longer running: write nothing
            raise
        except Exception as exc:  # a timed-out or malformed second read: the first still stands
            logger.warning("fees-agent second read failed for school %s: %s", school.id, exc)
            report["reread"]["error"] = str(exc)[:200]
            again = []
        first = {amount for fee in passing for amount in read_amounts(fee)}
        added = [fee for fee in again if fee_amounts(fee) and not set(fee_amounts(fee)) <= first]
        # A page that prints both currencies: the lev twin of a euro fee is not a fee of its own.
        euro = _euro_amounts([*passing, *added])
        added = [fee for fee in added if not _lev_twin(fee, euro)]
        fees = [*fees, *added]
        report["reread"]["added"] = len(added)
    checked: list[tuple[CheckedFee, SourcePage]] = []
    for fee in fees:
        page = _page_for(fee, priced)
        result = _checked(fee, page, proofs) if page else CheckedFee(fee, 0.0, False, ["dropped: not on a page read"])
        report["fees"].append(_fee_report(result, page))
        if result.kept and page:
            checked.append((result, source_pages[page.url]))

    if discounts is not None:
        report["discounts"] = [_discount_report(discount, priced) for discount in discounts]

    rows, sources = _build_rows(school, checked)
    publishable = [row for row in rows if pricing_row_is_publishable(row)]
    superseded = await _superseded_rows(db, school, rows)
    # Amounts read as another way to pay a fee: an old row holding one (596's "2 x 4.575" was a
    # row of its own) is listed under the new fee, not lost.
    # Only a fee that publishes: a held fee's options must not let the old rows go.
    listed = {
        (row.category.value, (row.currency or "").upper(), Decimal(str(option[key])).normalize())
        for row in publishable
        for option in (row.pricing_context or {}).get("payment_options") or []
        for key in ("total", "instalment", "first_payment")
        if option.get(key) is not None
    }
    # An old row named for a payment plan ("Plan 3") of a group whose new fee carries its
    # payment options is one of that fee's instalments, even at an amount the read left out:
    # kept, it would sit beside the fee as a fee of its own again (596's re-read kept 25).
    scheduled = {_band(row) for row in publishable if (row.pricing_context or {}).get("payment_options")}
    planned = [
        row for row in superseded if _band(row) in scheduled and _PAYMENT_PLAN_NAME_RE.match(row.plan_name or "")
    ]
    published_before = [
        row
        for row in superseded
        if pricing_row_is_publishable(row)
        and row not in planned
        and (
            row.amount is None
            or (row.category.value, (row.currency or "").upper(), Decimal(str(row.amount)).normalize()) not in listed
        )
    ]
    report |= {
        "rows_new": len(rows),
        "rows_new_publishable": len(publishable),
        "rows_old_published": len(published_before),
    }

    # A thin or failed read must not wipe good prices or make them flicker.
    if not publishable or 2 * len(publishable) < len(published_before):
        report["outcome"] = "kept_old_rows"
        report["reason"] = (
            "nothing publishable read"
            if not publishable
            else f"{len(publishable)} publishable rows against {len(published_before)} published"
        )
        return report

    # Old rows the read would lose (a fee it missed, a period it dropped) stay, instead of
    # holding the whole school; new rows at those amounts give way to them.
    kept, kept_amounts, regressions = _rows_to_keep(
        school,
        [*stored_pages, *source_pages.values()],
        [row for row in superseded if row not in planned],
        publishable,
        [(category, amount) for category, _, amount in listed],
    )
    if kept:  # by amount alone: a refiled category or currency is still the same fee
        superseded = [row for row in superseded if row not in kept]
        pairs = [(row, source) for row, source in zip(rows, sources) if _amount_group(row) not in kept_amounts]
        report["rows_new_dropped"] = len(rows) - len(pairs)
        rows, sources = [row for row, _ in pairs], [source for _, source in pairs]
    report["regressions"] = regressions
    report["rows_old_kept"] = len(kept)
    if not rows and not superseded:
        report["outcome"] = "kept_old_rows"
        report["reason"] = "every fee read is already stored"
        if apply:  # an earlier run's hold no longer applies
            await _lock_attributes(db, school)
            price_step._update_pricing_hold(school, [], 0)
            if discounts is not None:
                _store_discounts(school, report)
            await db.commit()
        return report
    if not apply:
        report["outcome"] = "would_write"
        return report
    await _lock_attributes(db, school)
    price_step._update_pricing_hold(school, [], len(rows))  # clears a hold an earlier run left

    await _upsert_pages(db, source_pages, stored)
    for row in rows:  # rows were built against the placeholder pages
        row.source_page = source_pages[row.source_url]
        row.source_page_id = row.source_page.id
    for source in sources:
        source.scraped_at = text_values._utcnow_naive()
    await price_step._write_price_rows(db, school, [row.id for row in superseded], rows, sources)
    if discounts is not None:
        _store_discounts(school, report)
    # The new rows stay withheld until Stage 6 has checked them, as after an extraction.
    school.attributes = prepare_validation_rollover(school.attributes)
    await db.commit()
    report["outcome"] = "written"
    try:  # the rows are written; if Stage 6 breaks they stay withheld until it next runs
        validation = await validate_school_data(db, school.id, school.country_code, run_spot_check=False)
        report["validation"] = validation.get("status")
    except Exception as exc:
        logger.exception("Stage 6 failed after fees-agent wrote school %s", school.id)
        if db.in_transaction():
            await db.rollback()
        report["validation"] = f"failed: {str(exc)[:200]}"
    return report


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------


async def _saved(found: Awaitable[list[PageRead]], school: School, pages_dir: Optional[Path]) -> list[PageRead]:
    """The found pages, also written to ``pages_dir/<id>.json`` when given."""
    pages = await found
    if pages_dir is not None:
        pages_dir.mkdir(parents=True, exist_ok=True)
        saved = {
            "school_id": school.id,
            "name_i18n": school.name_i18n,
            "school_type": school.school_type,
            "education_level": school.education_level,
            "website_url": school.website_url,
            "found_at": datetime.date.today().isoformat(),
            "pages": [asdict(page) for page in pages],
        }
        (pages_dir / f"{school.id}.json").write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    return pages


def _append_line(path: Path, result: dict[str, Any]) -> None:
    try:  # a failed write loses one line, not the rest of the run
        with path.open("a", encoding="utf-8") as lines:
            lines.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
    except (OSError, TypeError, ValueError):
        logger.exception("fees-agent could not write the line for school %s", result.get("school_id"))


async def run_fees_agent(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: Optional[str] = None,
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    max_usd: float = DEFAULT_MAX_USD,
    apply: bool = False,
    after_id: Optional[int] = None,
    lines_path: Optional[Path] = None,
    workers: int = 1,
    pages_dir: Optional[Path] = None,
    check: bool = True,
) -> dict[str, Any]:
    """Run the agent over private and international schools with a website; returns the report.

    ``check`` runs the fee check (stages/fee_check.py) after each school's first read; it is
    off, with a warning, when its tier calls Anthropic and no ANTHROPIC_API_KEY is set.

    ``workers`` schools are read at a time, each with its own session; lines are still
    appended in id order, so ``after_id`` with the last line's id skips no school.

    ``after_id`` starts after that school, to carry on a run that stopped. ``lines_path`` gets
    each school's report appended as one JSON line as soon as it is done, so a killed run
    keeps what it read (2026-10-13: a Sofia dry run killed at 55 minutes lost every read).

    ``pages_dir`` gets each school's found pages as ``<id>.json``: the fees eval's input
    (``scripts/fees_eval.py``).
    """
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # type: ignore

    query = select(School).where(School.country_code == country, School.website_url.isnot(None))
    query = (
        query.where(School.id.in_(school_ids))
        if school_ids
        else query.where(School.school_type.in_(["private", "international"]))
    )
    if city:
        query = query.where(School.city == city)
    if after_id is not None:
        query = query.where(School.id > after_id)
    # Ids, not objects: a rollback after one school's failure expires every loaded object.
    rows = (
        await db.execute(query.with_only_columns(School.id, School.website_url).order_by(School.id).limit(limit))
    ).all()
    run_ids = [row.id for row in rows]
    sites = {row.id: row.website_url for row in rows}

    settings = get_settings()
    if check and get_provider(fee_check.TIER) == "anthropic" and not settings.anthropic_api_key:
        logger.warning("fees-agent: no ANTHROPIC_API_KEY, so the fee check is off")
        check = False
    spend = Spend(max_usd)
    run = await start_pipeline_run(
        db,
        country=country,
        city=city,
        cli_stage=STAGE,
        config={"provider_cost_cap_usd": max_usd, "apply": apply, "check": check},
    )
    run_id = run.id  # a rollback after a failed school expires ``run``
    browser = BrowserConfig(headless=True, verbose=False, ignore_https_errors=True, user_agent=CRAWLER_USER_AGENT)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        check_robots_txt=True,
        page_timeout=30000,
        wait_until="domcontentloaded",
        delay_before_return_html=0.5,
        exclude_external_links=False,  # _same_site keeps the school's own and sister domains
        verbose=False,
    )
    results: list[dict[str, Any]] = []
    error: Optional[str] = None
    try:  # a failed browser start must still close the run
        with provider_cost_scope(
            pipeline_run_id=run.id,
            stage=STAGE,
            cap_usd=max_usd,
            request_reserve_usd=float(settings.provider_request_reserve_usd),
            max_in_flight=workers,
            model_reserves=request_reserves(),
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
                    # A failure here is that school's error (school_pool): its rows stay as they are.
                    return await run_school(
                        school_db,
                        await school_db.get(School, current_id),
                        find=lambda s: _saved(
                            find_pages(crawler, run_config, client, s, spend, read_pictures=True), s, pages_dir
                        ),
                        read=lambda s, pages, missed: read_fees(s, pages, spend, missed),
                        apply=apply,
                        gone=lambda url: page_gone(client, url),
                        check=(lambda s, pages, fees: check_read(s, pages, fees, spend)) if check else None,
                    )

                results, error = await school_pool.run_schools(
                    db,
                    run_ids,
                    one,
                    workers=workers,
                    stop_on=(SpendCapReached, ProviderCostCapExceeded),
                    sites=sites,
                    on_result=(lambda result: _append_line(lines_path, result)) if lines_path is not None else None,
                )
    except Exception as exc:
        logger.exception("fees-agent run %s stopped", run_id)
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
    # The ledger also holds a call billed just before the cap stopped the run.
    spent = max(spend.spent_usd, float(run.total_llm_cost_usd or 0))
    # A dry run is a trial: only a run that writes reports its wrong-looking fees.
    reported = fee_check.open_wrong_fees_issue(results, run_id=run_id) if apply and check else None
    if reported and not reported.startswith("not reported"):
        await _mark_reported(db, [r["school_id"] for r in results if (r.get("check") or {}).get("wrong")])
    return {
        "apply": apply,
        "check": check,
        "wrong_fees": reported,
        "pipeline_run_id": run_id,
        "schools": len(results),
        "spent_usd": round(spent, 6),
        "stopped": error,
        "kept_old_rows": [r["school_id"] for r in results if r["outcome"] == "kept_old_rows"],
        "outcomes": {o: sum(r["outcome"] == o for r in results) for o in {r["outcome"] for r in results}},
        "results": results,
    }
