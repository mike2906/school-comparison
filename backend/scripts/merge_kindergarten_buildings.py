#!/usr/bin/env python3
"""One-off: fold kg.sofia.bg building entries into their parent kindergarten.

    uv run python scripts/merge_kindergarten_buildings.py dry-run [--hourly]
    uv run python scripts/merge_kindergarten_buildings.py apply [--hourly]

kg.sofia.bg lists some buildings of a municipal kindergarten as records of their own
("ДГ №62 Зорница - сграда 2", "... - филиал с. Яна"). The adapter merges a building into
its kindergarten only when district and phone match, so the others became separate
schools. `--hourly` adds the "- почасова организация" records (an hourly-attendance
admission stream of the same kindergarten, not a building).

Per entry: its locations move to the parent (a location at the parent's own address and
pin is dropped, after its age groups are added to the parent's row), its kg.sofia
registry page moves to the parent, its other source pages and field sources are deleted,
its kg.sofia record ids are added to the parent's `kg_sofia_ids` (so discovery matches
the record to the parent and does not recreate the school), and the entry is deleted.

Both modes run the same statements in one transaction; dry-run rolls it back. The run
stops without writing if an entry has no single parent, or has exam, pricing or
spot-check rows.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import engine
from app.scrapers.sources.base_adapter import _address_key

BUILDING_RE = re.compile(r"\s-\s*(?:експериментална\s+)?(?:сграда|филиал)\b.*$", re.IGNORECASE)
HOURLY_RE = re.compile(r"\s-\s*почасова организация.*$", re.IGNORECASE)
SAME_PIN_DEGREES = 0.0003  # about 30 m


def _base(name: str) -> str:
    """Kindergarten name without the building suffix or the "(с яслени групи)" note."""
    name = HOURLY_RE.sub("", BUILDING_RE.sub("", name))
    return " ".join(re.sub(r"\(.*?\)", " ", name).casefold().split())


def _same_pin(a, b) -> bool:
    if None in (a.lat, a.lng, b.lat, b.lng):
        return True
    return abs(a.lat - b.lat) < SAME_PIN_DEGREES and abs(a.lng - b.lng) < SAME_PIN_DEGREES


def _ids(attrs: dict, single: str, many: str) -> set[str]:
    values = {attrs.get(single), *(attrs.get(many) or [])}
    return {str(value) for value in values if value is not None}


async def _count(conn, table: str, school_id: int) -> int:
    return (await conn.execute(text(f"select count(*) from {table} where school_id = :id"), {"id": school_id})).scalar()


async def _locations(conn, school_id: int):
    return (
        await conn.execute(
            text("select id, address_i18n, lat, lng, is_primary from school_locations where school_id = :id order by id"),
            {"id": school_id},
        )
    ).all()


async def main(mode: str, hourly: bool) -> None:
    totals = dict.fromkeys(
        ("schools_deleted", "locations_moved", "locations_dropped", "age_group_rows_added",
         "age_group_rows_deleted", "registry_pages_moved", "source_pages_deleted",
         "field_sources_deleted", "parents_updated"), 0)
    parents_touched: set[int] = set()

    async with engine.connect() as conn:
        trans = await conn.begin()
        kindergartens = (
            await conn.execute(
                text(
                    "select id, name_i18n->>'bg' as name, attributes from schools "
                    "where country_code = 'bg' and city = 'sofia' and school_type = 'state' "
                    "and education_level = 'kindergarten' and institutional_id is null order by id"
                )
            )
        ).all()

        def is_entry(name: str) -> bool:
            return bool(BUILDING_RE.search(name) or HOURLY_RE.search(name))

        parents_by_base: dict[str, list] = {}
        for row in kindergartens:
            if not is_entry(row.name):
                parents_by_base.setdefault(_base(row.name), []).append(row)

        entries = [
            row for row in kindergartens
            if BUILDING_RE.search(row.name) or (hourly and HOURLY_RE.search(row.name))
        ]
        print(f"mode={mode} hourly={hourly} entries={len(entries)}\n")

        for entry in entries:
            candidates = parents_by_base.get(_base(entry.name), [])
            if len(candidates) != 1:
                raise SystemExit(f"{entry.id} {entry.name}: {len(candidates)} parents; nothing written")
            parent = candidates[0]
            for table in ("exam_results", "pricing", "spot_check_results"):
                if await _count(conn, table, entry.id):
                    raise SystemExit(f"{entry.id} {entry.name}: has {table} rows; nothing written")

            print(f"{entry.id} {entry.name}\n  -> parent {parent.id} {parent.name}")
            parent_locations = await _locations(conn, parent.id)
            for loc in await _locations(conn, entry.id):
                address = (loc.address_i18n or {}).get("bg")
                twins = [p for p in parent_locations if _address_key(p.address_i18n) == _address_key(loc.address_i18n)]
                twin = next((p for p in twins if _same_pin(p, loc)), None)
                if twin is None:
                    await conn.execute(
                        text("update school_locations set school_id = :parent, is_primary = false where id = :id"),
                        {"parent": parent.id, "id": loc.id},
                    )
                    note = "  (same address text as a parent location, different pin: check)" if twins else ""
                    print(f"  move location {loc.id}: {address}{note}")
                    totals["locations_moved"] += 1
                    continue
                added = (
                    await conn.execute(
                        text(
                            "insert into location_age_group_shifts (location_id, age_group, shift, has_organised_groups) "
                            "select :twin, age_group, shift, has_organised_groups from location_age_group_shifts "
                            "where location_id = :id and age_group not in "
                            "(select age_group from location_age_group_shifts where location_id = :twin) "
                            "returning age_group"
                        ),
                        {"twin": twin.id, "id": loc.id},
                    )
                ).scalars().all()
                deleted = (
                    await conn.execute(
                        text("delete from location_age_group_shifts where location_id = :id"), {"id": loc.id}
                    )
                ).rowcount
                await conn.execute(text("delete from school_locations where id = :id"), {"id": loc.id})
                print(
                    f"  drop location {loc.id}: {address} (parent location {twin.id} is the same place"
                    + (f"; it gains {', '.join(sorted(added))}" if added else "")
                    + ")"
                )
                totals["locations_dropped"] += 1
                totals["age_group_rows_added"] += len(added)
                totals["age_group_rows_deleted"] += deleted

            moved_pages = (
                await conn.execute(
                    text("update source_pages set school_id = :parent where school_id = :id and source_url like 'kg://%'"),
                    {"parent": parent.id, "id": entry.id},
                )
            ).rowcount
            deleted_pages = (
                await conn.execute(text("delete from source_pages where school_id = :id"), {"id": entry.id})
            ).rowcount
            deleted_sources = (
                await conn.execute(text("delete from field_sources where school_id = :id"), {"id": entry.id})
            ).rowcount
            print(
                f"  registry pages moved: {moved_pages}; other source pages deleted: {deleted_pages}; "
                f"field sources deleted: {deleted_sources}"
            )

            # Re-read: an earlier entry of the same parent may already have added ids.
            attrs = dict(
                (await conn.execute(text("select attributes from schools where id = :id"), {"id": parent.id})).scalar()
                or {}
            )
            entry_attrs = dict(entry.attributes or {})
            kg_ids = _ids(attrs, "kg_sofia_id", "kg_sofia_ids") | _ids(entry_attrs, "kg_sofia_id", "kg_sofia_ids")
            esri_ids = _ids(attrs, "kg_sofia_esri_id", "kg_sofia_esri_ids") | _ids(
                entry_attrs, "kg_sofia_esri_id", "kg_sofia_esri_ids"
            )
            attrs["kg_sofia_ids"] = sorted(kg_ids)
            attrs["kg_sofia_esri_ids"] = sorted(esri_ids)
            attrs["kg_sofia_merged_buildings"] = True
            source_refs = dict(attrs.get("source_refs") or {})
            kg_ref = dict(source_refs.get("kg_sofia_bg") or {})
            kg_ref["record_ids"] = sorted(kg_ids)
            kg_ref["esri_ids"] = sorted(esri_ids)
            source_refs["kg_sofia_bg"] = kg_ref
            attrs["source_refs"] = source_refs
            await conn.execute(
                text("update schools set attributes = cast(:attrs as json), updated_at = now() where id = :id"),
                {"attrs": json.dumps(attrs, ensure_ascii=False), "id": parent.id},
            )
            print(f"  parent kg_sofia_ids: {', '.join(sorted(kg_ids, key=int))}")

            await conn.execute(text("delete from schools where id = :id"), {"id": entry.id})
            print(f"  delete school {entry.id}\n")

            totals["schools_deleted"] += 1
            totals["registry_pages_moved"] += moved_pages
            totals["source_pages_deleted"] += deleted_pages
            totals["field_sources_deleted"] += deleted_sources
            parents_touched.add(parent.id)

        totals["parents_updated"] = len(parents_touched)
        print("totals: " + json.dumps(totals))
        if mode == "apply":
            await trans.commit()
            print("committed")
        else:
            await trans.rollback()
            print("dry run: rolled back, nothing written")
    await engine.dispose()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("dry-run", "apply") or set(args[1:]) - {"--hourly"}:
        raise SystemExit(__doc__)
    asyncio.run(main(args[0], "--hourly" in args))
