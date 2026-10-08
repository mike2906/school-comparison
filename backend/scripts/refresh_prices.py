"""Re-read the fees of the given schools: crawl, extract prices only, validate.

General information, status and summaries are left alone. For each school the rows the
API would publish before and after are printed, so a run can be read school by school.
Try it on a copy of the database first (``DATABASE_URL``); the run commits.

Run from ``backend/``::

    uv run python scripts/refresh_prices.py --school-id 568 --school-id 529
    uv run python scripts/refresh_prices.py --cohort-file ids.txt --skip-navigation
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

from app.database import async_session_maker
from app.models import Pricing, School
from app.scrapers.extractor import extract_school
from app.scrapers.navigator import navigate_schools_batch
from app.utils.display_gating import (
    blocked_pricing_row_ids,
    implausible_tuition_row_ids,
    pricing_row_is_publishable,
)

NAVIGATION_CHUNK = 9


async def published_rows(school_id: int) -> list[str]:
    """The school's price rows as the API would publish them, one line each."""
    async with async_session_maker() as db:
        school = (
            await db.execute(
                select(School)
                .where(School.id == school_id)
                .options(selectinload(School.pricing).selectinload(Pricing.source_page))
            )
        ).scalar_one()
        publishable = [row for row in school.pricing if pricing_row_is_publishable(row)]
        blocked = blocked_pricing_row_ids(school.attributes) | implausible_tuition_row_ids(publishable)
        lines = []
        for row in publishable:
            if row.id in blocked:
                continue
            amount = row.amount if row.amount is not None else f"{row.amount_min}-{row.amount_max}"
            label = " / ".join(part for part in (row.age_group, row.plan_name) if part)
            lines.append(
                f"{row.category.value} {amount} {row.currency} "
                f"{row.period.value if row.period else 'no period'} "
                f"{row.academic_year or 'no year'} [{label}] {row.source_url}"
            )
        return sorted(lines)


async def _statuses(db, school_ids: list[int]) -> dict[int, str | None]:
    rows = await db.execute(select(School.id, School.scrape_status).where(School.id.in_(school_ids)))
    return {school_id: status for school_id, status in rows.all()}


async def _restore_statuses(db, statuses: dict[int, str | None]) -> None:
    """Undo the crawl's ``navigated`` status.

    Navigation marks a school ``navigated`` so that the full extraction runs next. This
    run re-reads the fees only, and a school left ``navigated`` would have its website
    data and its validation report withheld by the API.
    """
    schools = (await db.execute(select(School).where(School.id.in_(statuses)))).scalars().all()
    for school in schools:
        if school.scrape_status == "navigated" and statuses[school.id] != "navigated":
            school.scrape_status = statuses[school.id]
    await db.commit()


async def refresh(school_ids: list[int], *, navigate: bool, country: str) -> None:
    async with async_session_maker() as db:
        print("database:", (await db.execute(text("select current_database()"))).scalar())
    before = {school_id: await published_rows(school_id) for school_id in school_ids}

    if navigate:
        for start in range(0, len(school_ids), NAVIGATION_CHUNK):
            chunk = school_ids[start : start + NAVIGATION_CHUNK]
            async with async_session_maker() as db:
                statuses = await _statuses(db, chunk)
                for result in await navigate_schools_batch(db, chunk, country_code=country):
                    if not result.get("success"):
                        print(f"school {result.get('school_id')}: navigation: {result.get('reason')}")
                await _restore_statuses(db, statuses)

    for school_id in school_ids:
        async with async_session_maker() as db:
            result = await extract_school(db, school_id, country, prices_only=True)
        after = await published_rows(school_id)
        print(f"\n== school {school_id}: {'; '.join(result.get('details') or [result.get('error') or ''])}")
        for line in before[school_id]:
            if line not in after:
                print("  - " + line)
        for line in after:
            print(("    " if line in before[school_id] else "  + ") + line)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--school-id", type=int, action="append", default=[])
    parser.add_argument("--cohort-file", type=Path, help="school ids separated by commas or whitespace")
    parser.add_argument("--skip-navigation", action="store_true", help="use the stored pages")
    parser.add_argument("--country", default="bg")
    args = parser.parse_args()
    school_ids = list(args.school_id)
    if args.cohort_file:
        school_ids += [int(part) for part in args.cohort_file.read_text().replace(",", " ").split()]
    if not school_ids:
        parser.error("give --school-id or --cohort-file")
    asyncio.run(refresh(list(dict.fromkeys(school_ids)), navigate=not args.skip_navigation, country=args.country))


if __name__ == "__main__":
    main()
