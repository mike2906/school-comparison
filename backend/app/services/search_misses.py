"""Daily counts of name searches that found no school (Mike agreed 2026-10-19).

Only the search text, cleaned up, and how often it came on each day are kept: no address,
time of day, session or anything else about who searched. Text that looks personal (an email,
a phone number, a web address) is not stored, and rows go after ``KEEP_DAYS``. Nothing reads
them back over the API: a name a parent typed can be personal (a child's name, a street), so
reading is Mike's call (2026-10-19, not decided yet); until then only the database holds them.

The table lives in the production database only, in a schema of its own (``telemetry``),
created on first use and outside the models, so Alembic never manages it. Launch-DB dumps
leave the schema out (``backup_offsite.sh``), so a publish, which replaces the data with a
launch-DB snapshot, keeps the counts.
"""

from __future__ import annotations

import datetime
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_LENGTH = 60
KEEP_DAYS = 90
# New texts accepted per day; past this, only texts already counted that day go up.
MAX_TEXTS_PER_DAY = 1000
_PERSONAL_RE = re.compile(r"@|https?:|www\.|\d[\d\s/-]{5,}\d")


def clean_query(raw: object) -> Optional[str]:
    """The text to count, or None for nothing worth keeping or anything that looks personal."""
    if not isinstance(raw, str):
        return None
    query = " ".join(raw.lower().split())
    if len(query) < 3 or len(query) > MAX_LENGTH or _PERSONAL_RE.search(query):
        return None
    return query


def _table(db: AsyncSession) -> str:
    # SQLite (tests) has no schemas.
    return "telemetry.search_misses" if db.bind.dialect.name == "postgresql" else "search_misses"


_ready: set[str] = set()


async def _ensure_table(db: AsyncSession) -> None:
    # Once per process on PostgreSQL; two processes racing on the first CREATE lose one count.
    # SQLite (tests) gets a fresh in-memory database under the same URL, so it always checks.
    key = str(db.bind.url)
    if key in _ready and db.bind.dialect.name == "postgresql":
        return
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("CREATE SCHEMA IF NOT EXISTS telemetry"))
    await db.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_table(db)} ("
            "day DATE NOT NULL, query VARCHAR(60) NOT NULL, count INTEGER NOT NULL, "
            "PRIMARY KEY (day, query))"
        )
    )
    await db.commit()
    _ready.add(key)


async def record_miss(db: AsyncSession, raw: object, today: Optional[datetime.date] = None) -> bool:
    """Count one search that found nothing; False when the text is not kept."""
    query = clean_query(raw)
    if query is None:
        return False
    day = today or datetime.date.today()
    await _ensure_table(db)
    table = _table(db)
    known = await db.scalar(text(f"SELECT 1 FROM {table} WHERE day = :day AND query = :query"), {"day": day, "query": query})
    if not known:
        texts_today = await db.scalar(text(f"SELECT count(*) FROM {table} WHERE day = :day"), {"day": day})
        if texts_today >= MAX_TEXTS_PER_DAY:
            return False
    await db.execute(text(f"DELETE FROM {table} WHERE day < :oldest"), {"oldest": day - datetime.timedelta(days=KEEP_DAYS)})
    await db.execute(
        text(
            f"INSERT INTO {table} (day, query, count) VALUES (:day, :query, 1) "
            f"ON CONFLICT (day, query) DO UPDATE SET count = {table}.count + 1"
        ),
        {"day": day, "query": query},
    )
    await db.commit()
    return True


async def list_misses(
    db: AsyncSession, *, days: int = 30, min_days: int = 2, today: Optional[datetime.date] = None
) -> list[dict]:
    """Texts that found nothing on at least ``min_days`` of the last ``days`` days, most searched first."""
    await _ensure_table(db)
    since = (today or datetime.date.today()) - datetime.timedelta(days=days - 1)
    rows = await db.execute(
        text(
            f"SELECT query, sum(count) AS searches, count(*) AS on_days FROM {_table(db)} "
            "WHERE day >= :since GROUP BY query HAVING count(*) >= :min_days "
            "ORDER BY searches DESC, query LIMIT 200"
        ),
        {"since": since, "min_days": min_days},
    )
    await db.commit()
    return [{"query": query, "searches": searches, "days": on_days} for query, searches, on_days in rows]
