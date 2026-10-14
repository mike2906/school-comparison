#!/usr/bin/env python3
"""Load the admission cut-off archive into ``schools.admission_info`` (docs/ADMISSION_CUTOFFS.md).

Reads a checkout of the ``cutoff-data`` branch, matches each row to a school (kg.sofia.bg
institution id via ``attributes.kg_sofia_id(s)``, РУО rows via the НЕИСПУО code), and
rewrites only the derived keys: ``cutoffs``, ``cutoff_summary``, ``kg_sofia_ids``,
``admin_district`` and ``catchment_ids`` (the school's Sofia GIS ids, from
``attributes.kg_sofia_esri_id(s)``, which the calculator uses to look up catchment areas). The archive is the source of truth, so re-running is safe and a school
whose rows disappear loses those keys.

Default is a dry run that prints what would change; ``--apply`` writes.

    git -C .. worktree add ../cutoff-archive origin/cutoff-data
    uv run python scripts/import_cutoffs.py --archive ../../cutoff-archive [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker  # noqa: E402
from app.models import School  # noqa: E402
from app.services.admission_cutoffs import (  # noqa: E402
    iter_rows,
    match_key,
    school_cutoff_summary,
    school_cutoffs,
)

DERIVED_KEYS = ("cutoffs", "cutoff_summary", "kg_sofia_ids", "admin_district", "catchment_ids")


def school_index(schools: list[Any]) -> dict[tuple[str, str], int]:
    """(match kind, id) -> school id. A school's own kg_sofia_id wins over a family listing."""
    index: dict[tuple[str, str], int] = {}
    for school in sorted(schools, key=lambda s: s.id):
        attrs = school.attributes or {}
        for kg_id in attrs.get("kg_sofia_ids") or []:
            if kg_id is not None:
                index.setdefault(("kg_sofia_id", str(kg_id)), school.id)
    for school in schools:
        attrs = school.attributes or {}
        if attrs.get("kg_sofia_id") is not None:
            index[("kg_sofia_id", str(attrs["kg_sofia_id"]))] = school.id
        if school.institutional_id:
            index[("institutional_id", str(school.institutional_id))] = school.id
    return index


def catchment_ids(attributes: dict[str, Any] | None) -> dict[str, list[str]]:
    attrs = attributes or {}
    ids = {str(value) for value in attrs.get("kg_sofia_esri_ids") or [] if value is not None}
    if attrs.get("kg_sofia_esri_id") is not None:
        ids.add(str(attrs["kg_sofia_esri_id"]))
    return {"bg-sofia-gis": sorted(ids, key=lambda v: (len(v), v))} if ids else {}


def derived_admission_keys(rows: list[dict[str, Any]], attributes: dict[str, Any] | None = None) -> dict[str, Any]:
    kg_rows = [row for row in rows if row.get("source") == "kg.sofia.bg"]
    keys: dict[str, Any] = {
        "cutoffs": school_cutoffs(rows),
        "cutoff_summary": school_cutoff_summary(rows),
        "catchment_ids": catchment_ids(attributes),
    }
    if kg_rows:
        keys["kg_sofia_ids"] = sorted({str(row["source_id"]) for row in kg_rows}, key=int)
        districts = Counter(row["district"] for row in kg_rows if row.get("district"))
        if districts:
            keys["admin_district"] = districts.most_common(1)[0][0]
    return keys


def apply_to_school(school: Any, keys: dict[str, Any]) -> bool:
    info = dict(school.admission_info or {})
    before = {key: info.get(key) for key in DERIVED_KEYS}
    for key in DERIVED_KEYS:
        info.pop(key, None)
    info.update({key: value for key, value in keys.items() if value})
    if {key: info.get(key) for key in DERIVED_KEYS} == before:
        return False
    school.admission_info = info  # a new dict, so SQLAlchemy sees the change
    return True


async def run(archive: Path, apply: bool) -> dict[str, Any]:
    rows = list(iter_rows(archive))
    async with async_session_maker() as db:
        schools = list((await db.execute(select(School).where(School.country_code == "bg"))).scalars())
        index = school_index(schools)
        by_school: dict[int, list[dict[str, Any]]] = {}
        unmatched: Counter = Counter()
        for row in rows:
            school_id = index.get(match_key(row))
            if school_id is None:
                unmatched[(row["stage"], row["source_id"], row.get("institution_name"))] += 1
                continue
            by_school.setdefault(school_id, []).append(row)

        changed = 0
        for school in schools:
            keys = derived_admission_keys(by_school.get(school.id, []), school.attributes)
            if apply_to_school(school, keys):
                changed += 1
        if apply:
            await db.commit()
        else:
            await db.rollback()

    unmatched_by_stage = Counter(stage for stage, _, _ in unmatched)
    summary = {
        "rows": len(rows),
        "schools_with_cutoffs": len(by_school),
        "schools_changed": changed,
        "unmatched_institutions": len(unmatched),
        "unmatched_by_stage": dict(unmatched_by_stage),
        "applied": apply,
    }
    print(summary)
    for (stage, source_id, name), count in sorted(unmatched.items())[:40]:
        print(f"  unmatched {stage} {source_id} {name} ({count} rows)")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--archive", type=Path, required=True, help="checkout of the cutoff-data branch")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    asyncio.run(run(args.archive, args.apply))


if __name__ == "__main__":
    main()
