"""Trial: the R6 plan's amount check, applied to a finished fee-finder run.

Re-fetches the pages each school's run found prices on, and keeps a fee only if its amount
is one of the numbers on those pages (spaces, thousands separators and decimal commas
normalised). Then rescores the kept fees against the run's reference fees, so the effect of
the check on accuracy and recall can be read off. ``--show`` prints the page lines that
hold each fee's amount for the named schools, for a hand check.

No model calls and nothing written to the database. Run from ``backend/``::

    uv run python scripts/fee_amount_check.py /tmp/lane-d-trial/fresh.json --show 515 585
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from accuracy_report import match_any  # noqa: E402
from fee_finder_trial import _fetch, _scored  # noqa: E402
from second_reader_trial import _rate  # noqa: E402

from app.config import CRAWLER_USER_AGENT  # noqa: E402
from app.scrapers.fee_pages import fee_http_client  # noqa: E402

_NUMBER_RE = re.compile(r"\d[\d  .,]*\d|\d")


def _values(token: str) -> set[Decimal]:
    """Every value a printed number can mean: '7 400', '1.200,00', '475,50', '7.400'."""
    token = token.replace(" ", "").replace(" ", "").strip(".,")
    digits = re.sub(r"\D", "", token)
    if not digits or len(digits) > 9:  # phone numbers and IDs
        return set()
    candidates = {token.replace(",", "").replace(".", "")}
    for decimal_mark in ".,":
        if decimal_mark in token:
            whole, _, frac = token.rpartition(decimal_mark)
            candidates.add(re.sub(r"\D", "", whole) + "." + frac)
    values = set()
    for candidate in candidates:
        try:
            values.add(Decimal(candidate).normalize())
        except InvalidOperation:
            pass
    return values


def page_numbers(text: str) -> set[Decimal]:
    """Joined tokens ('7 400') and their space-separated parts ('580 600' in a table row)."""
    tokens = _NUMBER_RE.findall(text)
    tokens += [part for token in tokens for part in re.split(r"[ \u00a0]+", token)]
    return {value for token in tokens for value in _values(token)}


def _amounts(fee: dict) -> list[Decimal]:
    keys = ("amount",) if fee.get("amount") is not None else ("amount_min", "amount_max")
    return [Decimal(str(fee[key])).normalize() for key in keys if fee.get(key) is not None]


def amount_on_page(fee: dict, numbers: set[Decimal]) -> bool:
    amounts = _amounts(fee)
    return bool(amounts) and all(amount in numbers for amount in amounts)


async def main(args: argparse.Namespace) -> None:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig  # type: ignore

    results = json.loads(Path(args.run).read_text(encoding="utf-8"))
    browser = BrowserConfig(headless=True, verbose=False, ignore_https_errors=True, user_agent=CRAWLER_USER_AGENT)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        check_robots_txt=True,
        page_timeout=30000,
        wait_until="domcontentloaded",
        delay_before_return_html=0.5,
        verbose=False,
    )
    show = set(args.show)
    async with AsyncWebCrawler(config=browser) as crawler, fee_http_client() as client:
        for r in results:
            text = ""
            for page in r["pages"]:
                if page.get("prices"):
                    try:
                        _, page_text, _ = await _fetch(crawler, run_config, client, page["url"])
                    except Exception as exc:  # a failed page leaves its fees unchecked
                        print(f"  school {r['school_id']}: fetch failed {page['url']}: {exc}", file=sys.stderr)
                        page_text = ""
                    text += "\n" + page_text
            numbers = page_numbers(text)
            for fee in r["reader_fees"]:
                fee["amount_on_page"] = amount_on_page(fee, numbers)
            if r["school_id"] in show:
                print(f"\n== school {r['school_id']}: {[p['url'] for p in r['pages'] if p.get('prices')]}")
                for fee in r["reader_fees"]:
                    amount = fee.get("amount") or (fee.get("amount_min"), fee.get("amount_max"))
                    print(
                        f"  {fee['category']} {amount} {fee['currency']} {fee['period']} [{fee['label']}]"
                        f" on page: {fee['amount_on_page']}"
                    )
                for line in text.splitlines():
                    if any(str(int(a)) in line.replace(" ", "") for f in r["reader_fees"] for a in _amounts(f)):
                        print(f"    | {line.strip()[:200]}")

    def score(keep) -> str:
        right = total = found = ref_total = 0
        for r in results:
            reference = _scored(r["reference_fees"])
            reader = _scored([fee for fee in r["reader_fees"] if keep(fee)])
            if not reference:
                continue
            total += len(reader)
            right += sum(bool(match_any(fee, reference)) for fee in reader)
            ref_total += len(reference)
            found += sum(bool(match_any(fee, reader)) for fee in reference)
        return f"right {_rate(right, total)} of its fees; found {_rate(found, ref_total)} of the site's fees"

    dropped = [(r["school_id"], fee) for r in results for fee in r["reader_fees"] if not fee["amount_on_page"]]
    print()
    print(f"without the amount check: {score(lambda fee: True)}")
    print(f"with the amount check:    {score(lambda fee: fee['amount_on_page'])}")
    print(f"fees the check drops ({len(dropped)}):")
    for school_id, fee in dropped:
        amount = fee.get("amount") or (fee.get("amount_min"), fee.get("amount_max"))
        print(f"  school {school_id}: {fee['category']} {amount} {fee['currency']} {fee['period']} [{fee['label']}]")
    Path(args.run).with_suffix(".checked.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run")
    parser.add_argument("--show", type=int, nargs="*", default=[])
    asyncio.run(main(parser.parse_args()))
