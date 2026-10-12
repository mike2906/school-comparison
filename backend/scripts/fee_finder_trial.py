"""Trial: a model-guided fee page finder, then one whole-text reading (after Lane D R4).

The second-reader trial showed that most lost fees sit on pages the crawl never stores.
This starts from each sampled school's home page, renders pages with the crawler's
browser, and lets a cheap model pick which same-site links (pages or PDFs) are likely to
state fees, up to three clicks deep. Everything found is then read in one call by the
pricing model (``second_reader_trial._read``) and scored against the R4 reference set.

Schools whose reviewer found no fees are included, so the trial also shows fees the
reference may have missed (or fees the model invents): those are listed for checking.

Nothing is written to the database. Model calls go straight to OpenRouter, and the script
stops before a call that could take the total past ``--max-usd``. Run from ``backend/``::

    uv run python scripts/fee_finder_trial.py ../docs/accuracy/reference_set.json --out finder.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urldefrag, urlparse

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from accuracy_report import _row_dict, match_any  # noqa: E402
from second_reader_trial import _pdf_text, _rate, _read  # noqa: E402

from app.ai.client import get_model, get_model_costs, get_usage  # noqa: E402
from app.config import CRAWLER_USER_AGENT  # noqa: E402
from app.database import async_session_maker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Pricing, School  # noqa: E402
from app.models.pricing import PriceSource  # noqa: E402
from app.scrapers.extractor import (  # noqa: E402
    _build_openrouter_model,
    _build_openrouter_model_settings,
    _extract_openrouter_cost_usd,
)
from app.scrapers.fee_pages import fee_http_client  # noqa: E402
from app.scrapers.price_evidence import _PRICE_RE  # noqa: E402
from app.scrapers.shared_site_check import registrable_domain  # noqa: E402

MAX_HOPS = 3
MAX_PICKS = 4
MAX_FETCHES = 12
MAX_LINKS_SHOWN = 150
# When the picker returns nothing, follow links whose URL or text names fees or admissions.
_FEE_LINK_RE = re.compile(r"такс|цени|цена|прием|fee|tuition|pric|admission", re.IGNORECASE)
_SKIPPED = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".css", ".js", ".zip", ".mp4", ".mp3", ".doc", ".docx")


class Picks(BaseModel):
    # Required: with a default the cheap model calls the output tool with no URLs at all.
    urls: list[str] = Field(description="Up to 4 URLs copied exactly from the list; empty if none fits")


PICK_PROMPT = """You help find the pages of a school website that state its fees (tuition, monthly fee,
registration, food, transport, price lists, fee PDFs). You get the institution's name, the pages read so far
(with whether they already show prices), and the same-site links not yet opened, as "URL | link text".
Return up to 4 URLs, copied exactly from the list, most likely first, that are likely to state this
institution's fees or lead to them (admissions or "for parents" pages often do). Return none if nothing fits."""


def _key(url: str) -> str:
    """One key per page: no fragment, decoded, host lowercased, no trailing slash or www."""
    parsed = urlparse(unquote(urldefrag(url)[0]))
    host = (parsed.hostname or "").removeprefix("www.")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{host}{parsed.path.rstrip('/')}{query}"


def _same_site(url: str, site: str) -> bool:
    domain = registrable_domain(url)
    return bool(domain) and domain == registrable_domain(site)


async def _fetch(crawler, run_config, client, url: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(final URL, text, same-page links). PDFs are read with pdfplumber, pages rendered."""
    path = unquote(urlparse(url).path).lower()
    if path.endswith(".pdf"):
        response = await client.get(url, follow_redirects=True, timeout=30)
        if response.status_code != 200:
            return url, "", []
        return str(response.url), await asyncio.to_thread(_pdf_text, response.content), []
    result = await asyncio.wait_for(crawler.arun(url=url, config=run_config), timeout=60)
    if not getattr(result, "success", False):
        return url, "", []
    markdown = getattr(result, "markdown", None)
    text = getattr(markdown, "raw_markdown", None) or str(markdown or "")
    links = (getattr(result, "links", None) or {}).get("internal") or []
    pairs = [(str(link["href"]), str(link.get("text") or "").strip()[:80]) for link in links if link.get("href")]
    return str(getattr(result, "redirected_url", None) or url), text, pairs


async def _pick(name: str, read: list[dict], links: list[tuple[str, str]]) -> tuple[list[str], float, int, int]:
    agent = Agent(
        model=_build_openrouter_model("cheap"),
        system_prompt=PICK_PROMPT,
        output_type=Picks,
        retries=1,
        model_settings=_build_openrouter_model_settings("cheap"),
    )
    pages = "\n".join(f"- {page['url']} ({'shows prices' if page['prices'] else 'no prices'})" for page in read)
    shown = "\n".join(f"{url} | {text}" for url, text in links[:MAX_LINKS_SHOWN])
    result = await agent.run(f"Institution: {name}\n\nRead so far:\n{pages}\n\nLinks:\n{shown}")
    tokens_in, tokens_out = get_usage(result)
    allowed = {url for url, _ in links}
    picks = [url for url in result.output.urls if url in allowed][:MAX_PICKS]
    return picks, _extract_openrouter_cost_usd(result), tokens_in, tokens_out


async def _find(crawler, run_config, client, school: School, budget) -> tuple[list[dict], float]:
    from crawl4ai.utils import RobotsParser  # type: ignore

    robots = RobotsParser()
    site = school.website_url
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    read: list[dict] = []
    seen: set[str] = set()
    links: dict[str, str] = {}
    spent = 0.0
    queue = [site]
    for hop in range(MAX_HOPS + 1):
        for url in queue:
            key = _key(url)
            if key in seen or len(read) >= MAX_FETCHES:
                continue
            seen.add(key)
            if not await robots.can_fetch(url, CRAWLER_USER_AGENT):
                read.append({"url": url, "hop": hop, "prices": False, "text": "", "note": "robots.txt"})
                continue
            try:
                final, text, page_links = await _fetch(crawler, run_config, client, url)
            except Exception as exc:  # a failed page is recorded and skipped
                read.append({"url": url, "hop": hop, "prices": False, "text": "", "note": str(exc)[:120]})
                continue
            if _key(final) != key and _key(final) in seen:
                continue  # a second link to a page already read (often a percent-encoded redirect)
            seen.add(_key(final))
            read.append({"url": final, "hop": hop, "prices": bool(_PRICE_RE.search(text)), "text": text})
            for href, label in page_links:
                href = urldefrag(href)[0]
                if _same_site(href, site) and not unquote(href).lower().endswith(_SKIPPED):
                    links.setdefault(href, label)
        if hop == MAX_HOPS or len(read) >= MAX_FETCHES:
            break
        unopened = [(url, label) for url, label in links.items() if _key(url) not in seen]
        if not unopened or not budget(spent + 0.002):
            break
        queue, cost, _, _ = await _pick(name, read, unopened)
        spent += cost
        if not queue:
            queue = [url for url, label in unopened if _FEE_LINK_RE.search(f"{unquote(url)} {label}")][:MAX_PICKS]
        if not queue:
            break
    return read, spent


async def main(args: argparse.Namespace) -> None:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # type: ignore
    from httpx import ASGITransport, AsyncClient

    reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
    schools = [s for s in reference["schools"] if not s.get("unreachable")]
    in_price, out_price = get_model_costs("pricing")
    print(
        f"pick model {get_model('cheap')}, read model {get_model('pricing')}, {len(schools)} schools, cap ${args.max_usd}"
    )
    state = {"spent": 0.0}

    def budget(estimate: float) -> bool:
        return state["spent"] + estimate <= args.max_usd

    browser = BrowserConfig(headless=True, verbose=False, ignore_https_errors=True, user_agent=CRAWLER_USER_AGENT)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        check_robots_txt=True,
        page_timeout=30000,
        wait_until="domcontentloaded",
        delay_before_return_html=0.5,
        exclude_external_links=True,
        verbose=False,
    )
    results = []
    async with (
        AsyncWebCrawler(config=browser) as crawler,
        fee_http_client() as client,
        AsyncClient(transport=ASGITransport(app=app), base_url="http://trial") as api,
    ):
        for ref in schools:
            school_id = ref["school_id"]
            async with async_session_maker() as db:
                school = await db.get(School, school_id)
                stored = [
                    _row_dict(row)
                    for row in (
                        await db.execute(
                            select(Pricing).where(
                                Pricing.school_id == school_id, Pricing.source == PriceSource.SCRAPED_WEBSITE
                            )
                        )
                    ).scalars()
                ]
            if not budget(0.01):
                print(f"stopping before school {school_id}: spent ${state['spent']:.4f}")
                break
            pages, pick_cost = await _find(crawler, run_config, client, school, budget)
            state["spent"] += pick_cost
            priced = [page for page in pages if page["prices"]]
            text = "\n\n".join(f"=== PAGE {page['url']}\n{page['text']}" for page in priced)[: args.max_chars]
            fees, read_cost = [], 0.0
            estimate = (len(text) / 3 * in_price + 6000 * out_price) / 1_000_000
            if text.strip() and budget(estimate):
                fees, read_cost, _, _ = await _read(school, text, "pricing")
            state["spent"] += read_cost
            response = await api.get(f"/schools/{school_id}")
            published = (
                {row["id"] for row in response.json().get("pricing") or []} if response.status_code == 200 else set()
            )
            results.append(
                {
                    "school_id": school_id,
                    "pages": [
                        {k: v for k, v in page.items() if k != "text"} | {"chars": len(page["text"])} for page in pages
                    ],
                    "cost_usd": pick_cost + read_cost,
                    "reader_fees": fees,
                    "reference_fees": ref.get("prices") or [],
                    "no_fees_on_site": bool(ref.get("no_fees_on_site")),
                    "stored_rows": [
                        {
                            **{k: (str(v) if k.startswith("amount") and v is not None else v) for k, v in row.items()},
                            "published": row["id"] in published,
                        }
                        for row in stored
                    ],
                }
            )
            print(
                f"school {school_id}: {len(pages)} pages opened, {len(priced)} with prices, "
                f"{len(fees)} fees read, ${pick_cost + read_cost:.4f}"
            )

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _summarise(results, state["spent"])


def _scored(fees: list[dict]) -> list[dict]:
    """Fees as scored: lines without an amount dropped, and activities and extracurricular
    counted as one category (the reference and the reader split optional classes differently)."""
    return [
        fee | {"category": "activities" if fee["category"] == "extracurricular" else fee["category"]}
        for fee in fees
        if fee.get("amount") is not None or fee.get("amount_min") is not None
    ]


def _summarise(results: list[dict], spent: float) -> None:
    right = total = found = ref_total = pub_found = 0
    extra = []
    for r in results:
        reference, reader = _scored(r["reference_fees"]), _scored(r["reader_fees"])
        if not reference:
            extra += [(r["school_id"], fee) for fee in reader]
            continue
        total += len(reader)
        right += sum(bool(match_any(fee, reference)) for fee in reader)
        ref_total += len(reference)
        found += sum(bool(match_any(fee, reader)) for fee in reference)
        published = _scored([row for row in r["stored_rows"] if row["published"]])
        pub_found += sum(any(match_any(row, [fee]) for row in published) for fee in reference)
    n = len(results)
    print()
    print(f"schools: {n}, spent ${spent:.4f} (${spent / n if n else 0:.4f} per school)")
    print(
        f"finder + reader: right {_rate(right, total)} of its fees; found {_rate(found, ref_total)} of the site's fees"
    )
    print(f"published today: {_rate(pub_found, ref_total)} of the site's fees")
    print(f"fees read at schools whose reviewer found none ({len(extra)}), to check by hand:")
    for school_id, fee in extra:
        amount = fee.get("amount") or (fee.get("amount_min"), fee.get("amount_max"))
        print(f"  school {school_id}: {fee['category']} {amount} {fee['currency']} {fee['period']} [{fee['label']}]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reference")
    parser.add_argument("--out", default="fee_finder_trial.json")
    parser.add_argument("--max-usd", type=float, default=0.45)
    parser.add_argument("--max-chars", type=int, default=120_000)
    asyncio.run(main(parser.parse_args()))
