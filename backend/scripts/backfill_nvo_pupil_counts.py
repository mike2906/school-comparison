#!/usr/bin/env python3
"""One-off: fill `exam_results.pupil_count` by re-reading the official files already imported.

    uv run python scripts/backfill_nvo_pupil_counts.py

The NVO import discovers its files from the io.mon.bg index, which may refuse automated
requests. This re-reads each file an existing row already cites (`source_url`, on
data.egov.bg) through the normal import, so the "явили се" count lands next to each
average. Same writes as `cli run --stage nvo`, for the years and exams already stored.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker
from app.models.exam_results import ExamResult
from app.scrapers.nvo_results import _RESOURCE_ID_RE, NvoResource, import_nvo_results


async def main() -> None:
    async with async_session_maker() as db:
        rows = await db.execute(
            select(ExamResult.exam_type, ExamResult.year, ExamResult.source_url)
            .where(ExamResult.metric == "average_score", ExamResult.source_url.is_not(None))
            .distinct()
        )
        resources = []
        for exam_type, year, source_url in sorted(rows.all()):
            match = _RESOURCE_ID_RE.search(source_url)
            if match is None:
                print(f"skipped {exam_type} {year}: {source_url} is not a data.egov.bg resource")
                continue
            resources.append(
                NvoResource(
                    exam_type=exam_type,
                    year=year,
                    title=f"{exam_type} {year}",
                    dataset_url=source_url,
                    resource_view_url=source_url,
                    download_url=f"https://data.egov.bg/resource/download/{match.group(1)}/csv",
                )
            )
        summary = await import_nvo_results(db, resources=resources)
        for key in ("created_rows", "updated_rows", "unmatched_rows", "errors"):
            print(f"{key}: {summary[key]}")
        missing = await db.execute(
            select(ExamResult.exam_type, ExamResult.year, ExamResult.subject, ExamResult.school_id)
            .where(ExamResult.metric == "average_score", ExamResult.pupil_count.is_(None))
            .order_by(ExamResult.exam_type, ExamResult.year, ExamResult.school_id)
        )
        missing_rows = missing.all()
        print(f"rows still without a pupil count: {len(missing_rows)}")
        for row in missing_rows[:50]:
            print("  ", *row)
        # A result without a count is published as before, so do not pass silently.
        if missing_rows or summary["errors"]:
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
