"""Measure price accuracy against the reviewed reference set (Lane D R4).

The reference (``docs/accuracy/reference_set.json``) holds the fees a reviewer read from
each sampled school's own site without seeing the pipeline's output. This compares them
with the school's stored website price rows and with what the API publishes now, and
reports two rates:

- **published and wrong**: published rows that match no fee on the school's site;
- **correct but withheld**: stored rows that match a fee on the site but are not published.

A row matches a reference fee when the category agrees, the amount agrees within 1 %
(BGN at the fixed 1.95583 to the euro), and the period agrees or the row has none (those
are counted separately: a fee shown without its period can still mislead). Schools the
reviewer could not read (``"unreachable": true``) are left out of the rates and listed.
Read-only.

Run from ``backend/`` against the launch DB::

    uv run python scripts/accuracy_report.py ../docs/accuracy/reference_set.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Pricing  # noqa: E402
from app.models.pricing import PriceSource  # noqa: E402

BGN_PER_EUR = Decimal("1.95583")
TOLERANCE = Decimal("0.01")


def _value(value: Any) -> str | None:
    value = getattr(value, "value", value)
    return str(value).lower() if value is not None else None


def _eur(amount: Any, currency: str | None) -> Decimal | None:
    if amount is None:
        return None
    value = Decimal(str(amount))
    return value / BGN_PER_EUR if (currency or "").upper() == "BGN" else value


def _amounts(item: dict[str, Any]) -> list[Decimal]:
    currency = item.get("currency")
    keys = ("amount",) if item.get("amount") is not None else ("amount_min", "amount_max")
    return [eur for eur in (_eur(item.get(key), currency) for key in keys) if eur is not None]


def _same_amount(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= TOLERANCE * max(a, b)


def _period(row: dict[str, Any], fee: dict[str, Any]) -> str | None:
    """'exact', 'no_period' (the site states a period the row lacks) or None (they differ)."""
    row_period, fee_period = _value(row.get("period")), _value(fee.get("period"))
    if fee_period is None or row_period == fee_period:
        return "exact"  # nothing to compare when the site states no period
    return "no_period" if row_period is None else None


def match(row: dict[str, Any], fee: dict[str, Any]) -> str | None:
    """'exact', 'no_period' (the row lacks the period the site states) or None."""
    if _value(row.get("category")) != _value(fee.get("category")):
        return None
    row_amounts, fee_amounts = _amounts(row), _amounts(fee)
    if not row_amounts or len(row_amounts) != len(fee_amounts):
        return None
    if not all(_same_amount(a, b) for a, b in zip(sorted(row_amounts), sorted(fee_amounts))):
        return None
    return _period(row, fee)


def match_any(row: dict[str, Any], fees: list[dict[str, Any]]) -> str | None:
    """The best match among the site's fees. A range row also matches when both its ends are
    fees the site states on their own (one range across age groups)."""
    kinds = {kind for kind in (match(row, fee) for fee in fees) if kind}
    if not kinds and row.get("amount") is None and row.get("amount_min") is not None:
        ends = [
            {kind for kind in (match({**row, "amount": row[key]}, fee) for fee in fees) if kind}
            for key in ("amount_min", "amount_max")
        ]
        if all(ends):
            kinds = {"exact"} if all("exact" in end for end in ends) else {"no_period"}
    return "exact" if "exact" in kinds else next(iter(kinds), None)


def _row_dict(row: Pricing) -> dict[str, Any]:
    return {
        "id": row.id,
        "category": _value(row.category),
        "amount": row.amount,
        "amount_min": row.amount_min,
        "amount_max": row.amount_max,
        "currency": row.currency,
        "period": _value(row.period),
        "label": " / ".join(part for part in (row.age_group, row.plan_name) if part),
    }


async def main(reference_path: Path) -> None:
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    totals = {"published": 0, "published_wrong": 0, "matched": 0, "matched_withheld": 0, "no_period": 0}
    fees_total = fees_published = 0
    lines = []
    skipped = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://report") as client:
        for school in reference["schools"]:
            school_id = school["school_id"]
            if school.get("unreachable"):
                skipped.append(school_id)
                continue
            fees = school.get("prices") or []
            async with async_session_maker() as db:
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
            response = await client.get(f"/schools/{school_id}")
            if response.status_code == 404:  # not listed at all: nothing of it is published
                published_ids = set()
            else:
                response.raise_for_status()
                published_ids = {row["id"] for row in response.json().get("pricing") or []}

            wrong = []
            published_rows = [row for row in stored if row["id"] in published_ids]
            for row in stored:
                kind = match_any(row, fees)
                published = row["id"] in published_ids
                totals["published"] += published
                if kind:
                    totals["matched"] += 1
                    totals["matched_withheld"] += not published
                    totals["no_period"] += published and kind == "no_period"
                elif published:
                    totals["published_wrong"] += 1
                    wrong.append(row)
            found = sum(any(match_any(row, [fee]) for row in published_rows) for fee in fees)
            fees_total += len(fees)
            fees_published += found
            lines.append(
                f"school {school_id}: site fees {len(fees)}, stored {len(stored)}, published {len(published_rows)}, "
                f"site fees published {found}, published wrong {len(wrong)}"
                + "".join(
                    f"\n    wrong row {row['id']}: {row['category']} {row['amount'] or (row['amount_min'], row['amount_max'])}"
                    f" {row['currency']} {row['period']} [{row['label']}]"
                    for row in wrong
                )
            )

    def rate(part: int, whole: int) -> str:
        return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else f"{part}/0"

    print("\n".join(lines))
    print()
    print(f"schools: {len(reference['schools']) - len(skipped)} compared, unreadable and left out: {skipped}")
    print(f"published and wrong: {rate(totals['published_wrong'], totals['published'])} of published rows")
    print(f"correct but withheld: {rate(totals['matched_withheld'], totals['matched'])} of rows matching the site")
    print(f"published without a period the site states: {totals['no_period']}")
    print(f"site fees that are published: {rate(fees_published, fees_total)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reference", type=Path)
    asyncio.run(main(parser.parse_args().reference))
