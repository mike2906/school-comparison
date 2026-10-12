"""Offline extraction diff (tech-debt plan T6).

Runs the deterministic extraction path over every school's stored website pages and
writes one JSON line per school, then compares two such dumps. Run ``dump`` on the code
before a refactor and again after it: any difference is a behaviour change.

No network, no LLM, and no DB writes (the session is opened read-only).

    uv run python -m scripts.extraction_diff dump /tmp/before.jsonl
    git checkout <branch>
    uv run python -m scripts.extraction_diff dump /tmp/after.jsonl
    uv run python -m scripts.extraction_diff compare /tmp/before.jsonl /tmp/after.jsonl

Fee checks depend on today's date, so make both dumps on the same day.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select, text

from app.database import async_session_maker
from app.models.school import School
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.scrapers.deterministic import run_deterministic_extraction


async def dump(out_path: Path, session_maker=async_session_maker) -> None:
    async with session_maker() as session:
        if session.bind.dialect.name == "postgresql":
            await session.execute(text("SET TRANSACTION READ ONLY"))
        pages = (
            await session.execute(
                select(SourcePage)
                .where(
                    SourcePage.scrape_type == ScrapeType.WEBSITE,
                    SourcePage.is_valid.is_(True),
                    SourcePage.raw_markdown.isnot(None),
                )
                .order_by(SourcePage.school_id, SourcePage.id)
            )
        ).scalars().all()
        by_school: dict[int, list[SourcePage]] = {}
        for page in pages:
            by_school.setdefault(page.school_id, []).append(page)
        schools = (
            await session.execute(select(School).where(School.id.in_(by_school)).order_by(School.id))
        ).scalars().all()
        failed = 0
        with out_path.open("w", encoding="utf-8") as out:
            for school in schools:
                try:
                    result = run_deterministic_extraction(school, by_school[school.id])
                except Exception as exc:  # recorded, so a new crash shows up as a difference
                    failed += 1
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                line = {"school_id": school.id, "result": result}
                out.write(json.dumps(line, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        await session.rollback()
    print(f"{len(schools)} schools written to {out_path} ({failed} raised)")


def _load(path: Path) -> dict[int, dict]:
    rows = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        rows[row["school_id"]] = row["result"]
    return rows


def compare(before_path: Path, after_path: Path) -> int:
    before, after = _load(before_path), _load(after_path)
    changed = 0
    for school_id in sorted(set(before) | set(after)):
        old, new = before.get(school_id), after.get(school_id)
        if old == new:
            continue
        changed += 1
        if old is None or new is None:
            print(f"school {school_id}: only in {'after' if old is None else 'before'}")
            continue
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                print(f"school {school_id} [{key}]")
                print(f"  before: {json.dumps(old.get(key), ensure_ascii=False, sort_keys=True)}")
                print(f"  after:  {json.dumps(new.get(key), ensure_ascii=False, sort_keys=True)}")
    print(f"{changed} of {len(set(before) | set(after))} schools differ")
    return 1 if changed else 0


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "dump":
        asyncio.run(dump(Path(sys.argv[2])))
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "compare":
        return compare(Path(sys.argv[2]), Path(sys.argv[3]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
