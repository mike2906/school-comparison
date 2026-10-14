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
from dataclasses import dataclass
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

from app.ai.client import create_agent, extract_provider_cost_usd, get_model, get_model_costs
from app.config import CRAWLER_USER_AGENT, get_settings
from app.database import async_session_maker
from app.models import Pricing, School
from app.models.field_source import FieldSource, SourceConfidence, SourceType
from app.models.pipeline_run import PipelineRun
from app.models.pricing import PriceCategory, PriceSource
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
    check_discount,
    check_fee,
    fee_amounts,
    page_numbers,
    read_amounts,
)
from app.scrapers.fee_pages import fee_http_client, page_key, pdf_text
from app.scrapers.price_evidence import _PRICE_RE, PriceRow, _amount_key
from app.scrapers.shared_site_check import registrable_domain
from app.scrapers.stages import common as stage_common
from app.scrapers.stages import fee_images as fee_images_module
from app.scrapers.stages.fee_images import fee_images
from app.scrapers.validator import validate_school_data
from app.services.pipeline_runs import finalize_pipeline_run, pipeline_run_heartbeat, start_pipeline_run
from app.services.provider_costs import (
    ProviderCostCapExceeded,
    ProviderCostError,
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
MAX_LINKS_SHOWN = 150
MAX_READ_CHARS = 120_000
THIN_PAGE_CHARS = 300
RETRY_SECONDS = 2.0
READ_OUTPUT_TOKENS_RESERVE = 6000
PICK_COST_ESTIMATE_USD = 0.002
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
    def __init__(self, cap_usd: float):
        self.cap_usd = cap_usd
        self.spent_usd = 0.0

    def allows(self, estimate_usd: float) -> bool:
        return self.spent_usd + estimate_usd <= self.cap_usd


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
        model_settings={"temperature": settings.extraction_temperature, "parallel_tool_calls": False},
    )
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    pages = "\n".join(f"- {page.url} ({'shows prices' if page.prices else 'no prices'})" for page in read)
    shown = "\n".join(f"{url} | {text}" for url, text in links[:MAX_LINKS_SHOWN])
    try:
        result = await execute_billable_request(
            lambda: agent.run(f"Institution: {name}\n\nRead so far:\n{pages}\n\nLinks:\n{shown}"),
            model=get_model("cheap"),
            school_id=school.id,
            stage=stage,
            timeout_seconds=60,
        )
    except ProviderResponseUnattributed:
        # Counted at its assumed maximum, as the ledger counts it; the caller falls back to keyword links.
        spend.spent_usd += float(settings.provider_request_reserve_usd)
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
) -> list[PageRead]:
    """Up to 3 hops and 12 pages from the home page; robots.txt is respected.

    With ``read_pictures``, up to 4 pictures named for fees are read as pages of their own.
    The school-facts stage passes its own link prompt, fallback keywords and stage name."""
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
                    seen.add(page_key(picture[0]))
                    if not spend.allows(fee_images_module.IMAGE_COST_ESTIMATE_USD):
                        raise SpendCapReached(f"spent ${spend.spent_usd:.4f} of ${spend.cap_usd}")
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
                        picture_pages.append(PageRead(found[0], found[1], bool(_PRICE_RE.search(found[1]))))
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
        if not queue:
            queue = [url for url, label in unopened if fallback_links.search(f"{unquote(url)} {label}")][:MAX_PICKS]
        if not queue:
            break
    return read + picture_pages


# ---------------------------------------------------------------------------
# Read: one pricing-tier call over every page that shows a price
# ---------------------------------------------------------------------------


def reader_text(pages: list[PageRead]) -> str:
    """The text the reader sees: the pages one after another, cut to its limit."""
    return "\n\n".join(f"=== PAGE {page.url}\n{page.text}" for page in pages)[:MAX_READ_CHARS]


class FeeRead(list):
    """The fees of one read, with the discounts it also gave: a list to every reader."""

    discounts: list[AgentDiscount]


async def read_fees(school: School, pages: list[PageRead], spend: Spend, missed: Sequence[Decimal]) -> FeeRead:
    """Read the fees on ``pages``; ``missed`` names amounts an earlier read left out."""
    text = reader_text(pages)
    if missed:
        text += (
            "\n\nAn earlier read of these pages left out amounts that the school's stored fees have and "
            f"the pages still show: {', '.join(format(amount, 'f') for amount in missed)}. Read the pages again "
            "in full and list every fee, including every row of each fee table. Some of these "
            "amounts may be from an older year or not be fees: the rules above still decide."
        )
    in_price, out_price = get_model_costs("pricing")
    if not spend.allows((len(text) / 3 * in_price + READ_OUTPUT_TOKENS_RESERVE * out_price) / 1_000_000):
        raise SpendCapReached(f"spent ${spend.spent_usd:.4f} of ${spend.cap_usd}")
    agent = create_agent(
        "pricing",
        READER_PROMPT,
        AgentFees,
        retries=1,
        model_settings={"temperature": get_settings().extraction_temperature},
    )
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    result = await execute_billable_request(
        lambda: agent.run(f"Institution: {name} ({school.school_type} {school.education_level})\n\n{text}"),
        model=get_model("pricing"),
        school_id=school.id,
        stage=STAGE,
        timeout_seconds=120,
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
        "text_i18n": shown.text_i18n,
        "source_url": page.url if page else shown.source_url,
        "kept": checked is not None,
    }


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


def _build_rows(
    school: School, checked: list[tuple[CheckedFee, SourcePage]]
) -> tuple[list[Pricing], list[FieldSource]]:
    rows: list[Pricing] = []
    sources: list[FieldSource] = []
    built = []
    for result, page in checked:
        fee = result.fee
        fields = price_step._normalized_price_fields(
            ExtractedPrice(
                category=fee.category.value,
                amount=fee.amount,
                currency=fee.currency,
                period=fee.period.value if fee.period else None,
                amount_min=fee.amount_min,
                amount_max=fee.amount_max,
                plan_name=None
                if fee.payment_options and _PAYMENT_PLAN_NAME_RE.match(fee.plan_name or "")
                else fee.plan_name,
                academic_year=fee.academic_year,
                age_group=fee.age_group,
                confidence=result.confidence,
            )
        )
        if fields is not None:
            # Single payments of one plan price are the same fee whatever they are called.
            label = "" if _single_payment(fee) else " ".join(fee.label.split()).casefold()
            built.append((result, page, fields, label))

    # A plan paid "1 x 8950" restates the plan's 8950 a second time (596): drop such a single
    # payment when the same fee also comes without it, with or without its period.
    def unperiodic(fields: dict) -> tuple:
        return tuple(value for name, value in fields.items() if name != "period")

    restated = {unperiodic(f) for r, _, f, _ in built if not _single_payment(r.fee)}
    built = [b for b in built if not (_single_payment(b[0].fee) and unperiodic(b[2]) in restated)]
    # The same fee on two pages is one row (564 lists its fees on three). On one page a
    # second fee with the same fields but its own label is a fee of its own: two grade
    # tiers at one price when the reader left their groups empty. So each set of fields
    # comes from the one page that lists it most often, once per label.
    labels: dict[tuple, dict[Any, set[str]]] = {}
    for _, page, fields, label in built:
        labels.setdefault(tuple(fields.values()), {}).setdefault(page.id, set()).add(label)
    taken: set[tuple] = set()
    for result, page, fields, label in built:
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
    return rows, sources


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
        "academic_year": fee.academic_year,
        "age_group": fee.age_group,
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
) -> dict[str, Any]:
    """Find, read, check and (with ``apply``) store one school's fees. Returns its report."""
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
    missed = await _missed_amounts(db, school, priced, fees)
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
        first = {amount for fee in fees for amount in read_amounts(fee)}
        added = [fee for fee in again if fee_amounts(fee) and not set(fee_amounts(fee)) <= first]
        # A page that prints both currencies: the lev twin of a euro fee is not a fee of its own.
        euro = _euro_amounts([*fees, *added])
        added = [fee for fee in added if not _lev_twin(fee, euro)]
        fees = [*fees, *added]
        report["reread"]["added"] = len(added)
    checked: list[tuple[CheckedFee, SourcePage]] = []
    for fee in fees:
        page = _page_for(fee, priced)
        result = check_fee(fee, page.text) if page else CheckedFee(fee, 0.0, False, ["dropped: not on a page read"])
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
            price_step._update_pricing_hold(school, [], 0)
            if discounts is not None:
                _store_discounts(school, report)
            await db.commit()
        return report
    if not apply:
        report["outcome"] = "would_write"
        return report
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
    school_id: Optional[int] = None,
    limit: Optional[int] = None,
    max_usd: float = DEFAULT_MAX_USD,
    apply: bool = False,
    after_id: Optional[int] = None,
    lines_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Run the agent over private and international schools with a website; returns the report.

    ``after_id`` starts after that school, to carry on a run that stopped. ``lines_path`` gets
    each school's report appended as one JSON line as soon as it is done, so a killed run
    keeps what it read (2026-10-13: a Sofia dry run killed at 55 minutes lost every read).
    """
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # type: ignore

    query = select(School).where(School.country_code == country, School.website_url.isnot(None))
    query = (
        query.where(School.id == school_id)
        if school_id
        else query.where(School.school_type.in_(["private", "international"]))
    )
    if city:
        query = query.where(School.city == city)
    if after_id is not None:
        query = query.where(School.id > after_id)
    # Ids, not objects: a rollback after one school's failure expires every loaded object.
    school_ids = (await db.execute(query.with_only_columns(School.id).order_by(School.id).limit(limit))).scalars().all()

    spend = Spend(max_usd)
    run = await start_pipeline_run(
        db, country=country, city=city, cli_stage=STAGE, config={"provider_cost_cap_usd": max_usd, "apply": apply}
    )
    run_id = run.id  # a rollback after a failed school expires ``run``
    settings = get_settings()
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
                for current_id in school_ids:
                    school = await db.get(School, current_id)
                    try:
                        results.append(
                            await run_school(
                                db,
                                school,
                                find=lambda s: find_pages(crawler, run_config, client, s, spend, read_pictures=True),
                                read=lambda s, pages, missed: read_fees(s, pages, spend, missed),
                                apply=apply,
                                gone=lambda url: page_gone(client, url),
                            )
                        )
                    except (SpendCapReached, ProviderCostCapExceeded) as exc:
                        error = f"stopped before school {current_id}: {exc}"
                        break
                    except Exception as exc:  # one school's failure leaves its rows as they are
                        logger.exception("fees-agent failed for school %s", current_id)
                        if db.in_transaction():
                            await db.rollback()
                        results.append({"school_id": current_id, "outcome": "error", "reason": str(exc)[:200]})
                    if lines_path is not None:
                        _append_line(lines_path, results[-1])
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
    return {
        "apply": apply,
        "pipeline_run_id": run_id,
        "schools": len(results),
        "spent_usd": round(spent, 6),
        "stopped": error,
        "kept_old_rows": [r["school_id"] for r in results if r["outcome"] == "kept_old_rows"],
        "outcomes": {o: sum(r["outcome"] == o for r in results) for o in {r["outcome"] for r in results}},
        "results": results,
    }
