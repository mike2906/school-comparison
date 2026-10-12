"""Store the offer end date on existing price rows, without a full re-validation.

Validation now stores ``pricing_context.offer_ends_on`` on a row whose page offers its
price only until a date, and the publish gate withholds the row the day after. Rows
validated before that change carry no date until their school is validated again. This
runs only the pricing steps of validation for each school, in a transaction it rolls
back, and reads off the dates they would store; ``--apply`` then writes just those dates.

Run from ``backend/``::

    uv run python scripts/backfill_offer_ends.py            # counts only
    uv run python scripts/backfill_offer_ends.py --apply    # writes the dates
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker  # noqa: E402
from app.models import Pricing, School  # noqa: E402
from app.models.pricing import PriceSource  # noqa: E402
from app.schemas.validation import ValidationReport  # noqa: E402
from app.scrapers.validation import pricing_checks  # noqa: E402
from app.scrapers.validator import _check_price_evidence  # noqa: E402
from app.utils.display_gating import OFFER_ENDS_KEY, pricing_row_is_publishable  # noqa: E402


def _offer_end(row: Pricing) -> str | None:
    context = row.pricing_context if isinstance(row.pricing_context, dict) else {}
    return context.get(OFFER_ENDS_KEY)


async def _wanted_ends(school_id: int) -> dict[int, str | None]:
    """The offer end dates validation would change today, by row id.

    Rows the evidence step leaves alone (older academic years, a sibling's page, no page
    text) keep what they have, so they are not in the result.
    """
    async with async_session_maker() as db:
        tx = await db.begin()
        try:
            school = await db.get(School, school_id)
            rows = (
                await db.execute(
                    select(Pricing)
                    .where(Pricing.school_id == school_id, Pricing.source == PriceSource.SCRAPED_WEBSITE)
                    .order_by(Pricing.id)
                )
            ).scalars().all()
            before = {row.id: _offer_end(row) for row in rows}
            report = ValidationReport(validated_at="", status="ok", issue_counts={"error": 0, "warning": 0})
            pricing_checks._check_pricing_rows(report, rows)
            await _check_price_evidence(db, report, school, rows)
            return {row.id: _offer_end(row) for row in rows if _offer_end(row) != before[row.id]}
        finally:
            await tx.rollback()


async def main(apply: bool) -> None:
    async with async_session_maker() as db:
        school_ids = (
            await db.execute(
                select(Pricing.school_id)
                .where(Pricing.source == PriceSource.SCRAPED_WEBSITE)
                .distinct()
                .order_by(Pricing.school_id)
            )
        ).scalars().all()

    wanted: dict[int, str | None] = {}
    for school_id in school_ids:
        wanted.update(await _wanted_ends(school_id))

    today = datetime.date.today()
    async with async_session_maker() as db:
        rows = (
            await db.execute(
                select(Pricing)
                .where(Pricing.school_id.in_(school_ids), Pricing.source == PriceSource.SCRAPED_WEBSITE)
                .options(selectinload(Pricing.source_page))
            )
        ).scalars().all()
        after = {row.id: wanted.get(row.id, _offer_end(row)) for row in rows}
        dated = [row for row in rows if after[row.id]]
        changed = [row for row in rows if row.id in wanted]
        published_now = {row.id for row in rows if pricing_row_is_publishable(row)}
        ended = {row.id for row in dated if datetime.date.fromisoformat(after[row.id]) < today}

        print(f"scraped price rows: {len(rows)} in {len(school_ids)} schools")
        print(f"rows with an offer end date: {len(dated)} in {len({row.school_id for row in dated})} schools")
        print(f"  of which already ended: {len(ended)} ({len(published_now & ended)} pass the row gate now)")
        print(f"rows whose stored date would change: {len(changed)}")
        for row in sorted(dated, key=lambda r: (r.school_id, r.id)):
            amount = row.amount if row.amount is not None else row.amount_min
            print(f"  school {row.school_id} row {row.id}: {amount} {row.currency} until {after[row.id]}")

        if not apply:
            print("dry run: nothing written (use --apply)")
            return
        for row in changed:
            ends = wanted[row.id]
            pricing_checks.store_offer_end(row, datetime.date.fromisoformat(ends) if ends else None)
        await db.commit()
        print(f"wrote {len(changed)} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the dates (default: counts only)")
    asyncio.run(main(parser.parse_args().apply))
