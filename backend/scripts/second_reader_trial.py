"""Trial: a second, independent model reading of each school's fee pages (after Lane D R4).

For each school in the R4 reference set whose site states fees, this gathers the school's
stored fee pages, fetches each again without the crawler's 15,000-character cap, and asks
one model call to list every fee with the site's own label, category and period. It then
reports, against the reference set:

- how well the second reader alone does (fees it gets right, reference fees it finds);
- how an agreement gate would do: publish a stored row only when the second reader
  states the same fee;
- how many stored fee pages hit the 15,000-character cap;
- what it cost per school.

Nothing is written to the database. The model calls go straight to OpenRouter (no
pipeline run or ledger), and the script stops before a call that could take the total
past ``--max-usd``. Run from ``backend/`` against the launch DB::

    uv run python scripts/second_reader_trial.py ../docs/accuracy/reference_set.json --out trial.json
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from accuracy_report import _row_dict, match_any  # noqa: E402

from app.ai.client import get_model, get_model_costs, get_usage  # noqa: E402
from app.database import async_session_maker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Pricing, School  # noqa: E402
from app.models.pricing import PriceSource  # noqa: E402
from app.models.source_page import SourcePage  # noqa: E402
from app.scrapers.extractor import (  # noqa: E402
    _build_openrouter_model,
    _build_openrouter_model_settings,
    _extract_openrouter_cost_usd,
)
from app.scrapers.fee_pages import fee_http_client  # noqa: E402
from app.scrapers.navigator import WebsiteNavigator  # noqa: E402
from app.scrapers.price_evidence import _PRICE_RE  # noqa: E402

STORED_CAP = 15000
OUTPUT_TOKENS_RESERVE = 6000

Category = Literal[
    "tuition", "food", "transport", "activities", "registration", "materials",
    "extended_day", "uniforms", "extracurricular", "camp",
]  # fmt: skip
Period = Literal["monthly", "yearly", "one_time", "quarter", "term", "semester"]


class Fee(BaseModel):
    category: Category
    amount: Optional[float] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    currency: Literal["EUR", "BGN"]
    period: Optional[Period] = Field(default=None, description="null unless the site states it")
    label: str = Field(description="The site's own words for this fee: group, grade, plan")
    note: Optional[str] = None


class Fees(BaseModel):
    fees: list[Fee] = Field(default_factory=list)


SYSTEM_PROMPT = """You read a school's own web pages and list the fees it states.
Rules:
- Only fees of the named institution. Pages may cover a sibling school or kindergarten on the same site; skip their fees.
- Only the current or upcoming school year (2025/26 or 2026/27). If both appear, list the most recent.
- One entry per fee the site states. A discounted or early-payment price is its own entry, with a note.
- category: the monthly kindergarten fee and school tuition are tuition; a one-off enrolment or application fee is registration; learning support, therapy and similar services are activities; optional classes, clubs and sports charged on top are extracurricular.
- A fee table lists one fee per row and per column (grade, group, plan): list every one of them, all the way to the end. Never summarise a table or stop early.
- Skip lines without an amount (a percentage discount, a payment schedule).
- currency as printed (EUR or BGN); if both are printed, the EUR figure.
- period only when the site states it (per month, per year, one-time, per term...); otherwise null. Never guess.
- label: the site's own wording, short. Never invent a fee or an amount."""


def _pdf_text(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages[:20])


async def _gather(db, school: School, max_chars: int) -> tuple[list[dict], str]:
    pages = (
        (await db.execute(select(SourcePage).where(SourcePage.school_id == school.id, SourcePage.is_valid.is_(True))))
        .scalars()
        .all()
    )
    fee_pages = [p for p in pages if p.page_category == "pricing" or _PRICE_RE.search(p.raw_markdown or "")]
    navigator = WebsiteNavigator.__new__(WebsiteNavigator)
    navigator.MAX_CONTENT_CHARS = 10**7  # the trial reads pages whole
    stats, parts = [], []
    async with fee_http_client() as client:
        for page in fee_pages:
            stored = page.raw_markdown or ""
            live = ""
            try:
                response = await client.get(page.source_url, follow_redirects=True, timeout=20)
                kind = response.headers.get("content-type", "")
                if response.status_code == 200 and ("pdf" in kind or response.content[:5] == b"%PDF-"):
                    live = await asyncio.to_thread(_pdf_text, response.content)
                elif response.status_code == 200 and "html" in kind:
                    live = navigator._extract_main_content_text(response.text) or ""
            except Exception as exc:  # a failed fetch falls back to the stored copy
                live = ""
                print(f"  fetch failed {page.source_url}: {exc}", file=sys.stderr)
            text = live if len(live) > len(stored) else stored
            stats.append(
                {
                    "url": page.source_url,
                    "stored": len(stored),
                    "live": len(live),
                    "capped": len(stored) >= STORED_CAP - 10,
                }
            )
            parts.append(f"=== PAGE {page.source_url}\n{text}")
    return stats, "\n\n".join(parts)[:max_chars]


async def _read(school: School, text: str, tier: str) -> tuple[list[dict], float, int, int]:
    agent = Agent(
        model=_build_openrouter_model(tier),
        system_prompt=SYSTEM_PROMPT,
        output_type=Fees,
        retries=1,
        model_settings=_build_openrouter_model_settings(tier),
    )
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
    level = f"{school.school_type} {school.education_level}"
    result = await agent.run(f"Institution: {name} ({level})\n\n{text}")
    tokens_in, tokens_out = get_usage(result)
    fees = [fee.model_dump() for fee in result.output.fees]
    return fees, _extract_openrouter_cost_usd(result), tokens_in, tokens_out


def _rate(part: int, whole: int) -> str:
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else f"{part}/0"


async def main(args: argparse.Namespace) -> None:
    from httpx import ASGITransport, AsyncClient

    reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
    schools = [s for s in reference["schools"] if s.get("prices") and not s.get("unreachable")]
    in_price, out_price = get_model_costs(args.tier)
    print(f"model {get_model(args.tier)}, {len(schools)} schools, cap ${args.max_usd}")
    spent = 0.0
    results = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://trial") as api:
        for ref in schools:
            school_id = ref["school_id"]
            async with async_session_maker() as db:
                school = await db.get(School, school_id)
                stats, text = await _gather(db, school, args.max_chars)
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
            estimate = (len(text) / 3 * in_price + OUTPUT_TOKENS_RESERVE * out_price) / 1_000_000
            if spent + estimate > args.max_usd:
                print(f"stopping before school {school_id}: spent ${spent:.4f}, next call up to ${estimate:.4f}")
                break
            fees, cost, tokens_in, tokens_out = ([], 0.0, 0, 0)
            if text.strip():
                fees, cost, tokens_in, tokens_out = await _read(school, text, args.tier)
            spent += cost
            response = await api.get(f"/schools/{school_id}")
            published = (
                {row["id"] for row in response.json().get("pricing") or []} if response.status_code == 200 else set()
            )
            results.append(
                {
                    "school_id": school_id,
                    "pages": stats,
                    "chars_sent": len(text),
                    "cost_usd": cost,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "reader_fees": fees,
                    "reference_fees": ref["prices"],
                    "stored_rows": [
                        {
                            **{k: (str(v) if k.startswith("amount") and v is not None else v) for k, v in row.items()},
                            "published": row["id"] in published,
                        }
                        for row in stored
                    ],
                }
            )
            print(f"school {school_id}: {len(stats)} pages, {len(text)} chars, {len(fees)} fees read, ${cost:.4f}")

    Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _summarise(results, spent)


def _summarise(results: list[dict], spent: float) -> None:
    reader_right = reader_total = ref_found = ref_total = 0
    gate_pub = gate_wrong = now_pub = now_wrong = right_rows = right_kept = right_kept_now = 0
    capped = pages = 0
    for r in results:
        reference, reader = r["reference_fees"], r["reader_fees"]
        reader_total += len(reader)
        reader_right += sum(bool(match_any(fee, reference)) for fee in reader)
        ref_total += len(reference)
        ref_found += sum(bool(match_any(fee, reader)) for fee in reference)
        pages += len(r["pages"])
        capped += sum(p["capped"] for p in r["pages"])
        for row in r["stored_rows"]:
            right = bool(match_any(row, reference))
            agreed = bool(match_any(row, reader))
            # The agreement gate: a row that passes today's gates publishes only if the reader agrees.
            if row["published"]:
                now_pub += 1
                now_wrong += not right
            if row["published"] and agreed:
                gate_pub += 1
                gate_wrong += not right
            if right:
                right_rows += 1
                right_kept_now += row["published"]
                right_kept += row["published"] and agreed
    n = len(results)
    print()
    print(f"schools read: {n}, spent ${spent:.4f} (${spent / n if n else 0:.4f} per school)")
    print(f"fee pages stored at the 15,000-char cap: {capped} of {pages}")
    print(
        f"second reader alone: right {_rate(reader_right, reader_total)} of its fees; "
        f"found {_rate(ref_found, ref_total)} of the site's fees"
    )
    print(
        f"published today: wrong {_rate(now_wrong, now_pub)}; correct rows published {_rate(right_kept_now, right_rows)}"
    )
    print(
        f"with the agreement gate: wrong {_rate(gate_wrong, gate_pub)}; correct rows published {_rate(right_kept, right_rows)}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reference")
    parser.add_argument("--out", default="second_reader_trial.json")
    parser.add_argument("--tier", default="pricing")
    parser.add_argument("--max-usd", type=float, default=0.45)
    parser.add_argument("--max-chars", type=int, default=120_000)
    asyncio.run(main(parser.parse_args()))
