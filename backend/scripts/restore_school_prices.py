"""Put one school's website price rows back as they were in a pg_dump backup.

For undoing a fee re-read that came out worse than what is live (596 on 2026-10-15). The
school's scraped-website ``pricing`` rows and their ``field_sources`` evidence are replaced by
the dump's, in one transaction; nothing else changes. Run ``run --stage validate-data`` for
the school afterwards, so its blocked-row list matches the restored rows.

Run from ``backend/`` on the machine with the launch DB (the dump is read with the database
container's own ``pg_restore``)::

    uv run python scripts/restore_school_prices.py --school-id 596 --dump ~/backups/x.dump
    uv run python scripts/restore_school_prices.py --school-id 596 --dump ~/backups/x.dump --apply
"""

from __future__ import annotations

import argparse
import asyncio
import io
import os
import re
import subprocess
from pathlib import Path

from sqlalchemy import text

from app.database import async_session_maker

TABLES = ("pricing", "field_sources")
# Only what a fee re-read replaces: the school's scraped-website rows.
KEEP = {
    "pricing": "school_id = :school_id AND source = 'SCRAPED_WEBSITE'",
    "field_sources": "school_id = :school_id AND source_type = 'SCRAPED_WEBSITE' AND category = 'pricing'",
}
_COPY_RE = re.compile(r"^COPY public\.(\w+) \((.*)\) FROM stdin;$")


def copy_blocks(dump: Path, pg_restore: list[str]) -> dict[str, tuple[list[str], bytes]]:
    """The dump's COPY data for TABLES: each table's columns and its rows in COPY text format."""
    args = [*pg_restore, "--data-only", "-f", "-", *(f"--table={table}" for table in TABLES)]
    with dump.open("rb") as source:
        out = subprocess.run(args, stdin=source, capture_output=True, check=True).stdout
    blocks: dict[str, tuple[list[str], bytes]] = {}
    lines = iter(out.split(b"\n"))
    for line in lines:
        match = _COPY_RE.match(line.decode())
        if not match:
            continue
        rows = []
        for row in lines:
            if row == b"\\.":
                break
            rows.append(row + b"\n")
        blocks[match[1]] = ([column.strip().strip('"') for column in match[2].split(",")], b"".join(rows))
    return blocks


async def restore(
    school_id: int, blocks: dict[str, tuple[list[str], bytes]], apply: bool, session_maker=async_session_maker
) -> None:
    async with session_maker() as db:
        raw = (await (await db.connection()).get_raw_connection()).driver_connection
        for table in TABLES:
            columns, rows = blocks[table]
            await db.execute(text(f"CREATE TEMP TABLE restore_{table} (LIKE {table}) ON COMMIT DROP"))
            await raw.copy_to_table(f"restore_{table}", source=io.BytesIO(rows), columns=columns, format="text")
            params = {"school_id": school_id}
            now = (await db.execute(text(f"SELECT count(*) FROM {table} WHERE {KEEP[table]}"), params)).scalar()
            then = (
                await db.execute(text(f"SELECT count(*) FROM restore_{table} WHERE {KEEP[table]}"), params)
            ).scalar()
            print(f"{table}: {now} rows now, {then} in the dump")
            await db.execute(text(f"DELETE FROM {table} WHERE {KEEP[table]}"), params)
            await db.execute(text(f"INSERT INTO {table} SELECT * FROM restore_{table} WHERE {KEEP[table]}"), params)
        if apply:
            await db.commit()
            print("restored")
        else:
            await db.rollback()
            print("dry run: nothing written (add --apply)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--school-id", type=int, required=True)
    parser.add_argument("--dump", type=Path, required=True)
    parser.add_argument("--container", default=os.environ.get("BACKUP_DB_CONTAINER", "sofia_schools_db"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    pg_restore = ["docker", "exec", "-i", args.container, "pg_restore"]
    asyncio.run(restore(args.school_id, copy_blocks(args.dump.expanduser(), pg_restore), args.apply))


if __name__ == "__main__":
    main()
