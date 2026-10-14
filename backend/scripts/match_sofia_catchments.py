#!/usr/bin/env python3
"""Give Sofia schools their first-grade catchment ids (``admission_info.catchment_ids``).

The catchment snapshot (``scripts/build_sofia_catchments.py``) keys each catchment by the
municipality's GIS school id (``integrationid`` on arcgis.sofia.bg ``Schools/SchoolsZones``
layer 1). That id is not the kg.sofia.bg ``esriId`` kindergartens carry, so schools are
matched to the layer-1 points the same way ``import_sofia_municipal_points.py`` matches
locations: number and type from the name, and agreeing addresses. Only points that have a
catchment in the snapshot are used, so a school's half-day entry never competes with it.

This script owns the ``bg-sofia-gis`` ids: a matched school gets them, every other school
loses them. Default is a dry run that prints the changes and what stayed unmatched.

    uv run python scripts/match_sofia_catchments.py --campaign 2026/2027 [--apply]
    uv run python scripts/match_sofia_catchments.py --points schools_points.geojson
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker  # noqa: E402
from app.models import School  # noqa: E402
from scripts.import_sofia_municipal_points import Point, classify  # noqa: E402

POINTS_URL = "https://arcgis.sofia.bg/arcgis/rest/services/Schools/SchoolsZones/MapServer/1/query"
SNAPSHOT_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "catchments" / "bg" / "sofia"
PROVIDER = "bg-sofia-gis"


@dataclass
class GisPoint(Point):
    gis_id: str = ""


def snapshot_ids(campaign: str) -> dict[str, str]:
    """GIS id -> school name of every catchment in the snapshot."""
    path = SNAPSHOT_ROOT / campaign.replace("/", "-") / "catchments.json"
    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    return {str(f["properties"]["id"]): f["properties"]["name"] for f in features}


def points_from_geojson(payload: dict[str, Any], wanted: set[str]) -> list[GisPoint]:
    """Layer-1 school points that have a catchment in the snapshot."""
    points = []
    for feature in payload["features"]:
        props = feature.get("properties") or {}
        gis_id = str(props.get("integrationid") or "").strip()
        coords = (feature.get("geometry") or {}).get("coordinates")
        if gis_id not in wanted or not coords:
            continue
        points.append(GisPoint(
            kind="school",
            name=(props.get("fullname") or props.get("name") or "").strip(),
            address=(props.get("fulladdress") or "").strip(),
            district=None,
            lat=float(coords[1]),
            lng=float(coords[0]),
            gis_id=gis_id,
        ))
    return points


def match_schools(schools: list[Any], points: list[GisPoint]) -> tuple[dict[int, list[str]], dict[str, list[int]]]:
    """(school id -> GIS ids, GIS id -> school ids claimed by more than one school).

    A school with several buildings can match several catchments. A catchment two schools
    match is left out for both and reported.
    """
    claims: dict[str, set[int]] = {}
    for school in schools:
        for location in school.locations or []:
            row = classify(location, school, points)
            if row.status == "match":
                claims.setdefault(row.point.gis_id, set()).add(school.id)
    by_school: dict[int, list[str]] = {}
    conflicts = {gis_id: sorted(ids) for gis_id, ids in claims.items() if len(ids) > 1}
    for gis_id, ids in claims.items():
        if gis_id not in conflicts:
            by_school.setdefault(next(iter(ids)), []).append(gis_id)
    return {sid: sorted(ids, key=lambda v: (len(v), v)) for sid, ids in by_school.items()}, conflicts


def apply_ids(school: Any, ids: list[str]) -> bool:
    """Set (or clear) the school's ``bg-sofia-gis`` ids; True when that changed anything."""
    info = dict(school.admission_info or {})
    catchments = dict(info.get("catchment_ids") or {})
    if (catchments.get(PROVIDER) or []) == ids:
        return False
    if ids:
        catchments[PROVIDER] = ids
    else:
        catchments.pop(PROVIDER, None)
    if catchments:
        info["catchment_ids"] = catchments
    else:
        info.pop("catchment_ids", None)
    school.admission_info = info  # a new dict, so SQLAlchemy sees the change
    return True


def load_points(path: Path | None) -> dict[str, Any]:
    if path:
        return json.loads(path.read_text(encoding="utf-8"))
    response = httpx.get(
        POINTS_URL,
        params={"where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


async def run(campaign: str, points_path: Path | None, apply: bool) -> dict[str, Any]:
    catchments = snapshot_ids(campaign)
    points = points_from_geojson(load_points(points_path), set(catchments))
    async with async_session_maker() as db:
        schools = list((await db.execute(
            select(School)
            .where(School.country_code == "bg", School.city == "sofia")
            .options(selectinload(School.locations))
        )).scalars())
        candidates = [s for s in schools if s.education_level != "kindergarten"]
        by_school, conflicts = match_schools(candidates, points)
        changed = [s for s in schools if apply_ids(s, by_school.get(s.id, []))]
        if apply:
            await db.commit()
        else:
            await db.rollback()

    matched = {gis_id for ids in by_school.values() for gis_id in ids}
    names = {s.id: (s.name_i18n or {}).get("bg") for s in schools}
    summary = {
        "catchments": len(catchments),
        "points_with_catchment": len(points),
        "schools_matched": len(by_school),
        "catchments_matched": len(matched),
        "conflicts": len(conflicts),
        "schools_changed": len(changed),
        "applied": apply,
    }
    print(summary)
    for gis_id, ids in sorted(conflicts.items()):
        print(f"  conflict {gis_id} {catchments[gis_id]}: schools {ids}")
    for gis_id in sorted(set(catchments) - matched, key=lambda v: (len(v), v)):
        print(f"  unmatched {gis_id} {catchments[gis_id]}")
    for school in changed:
        print(f"  {school.id} {names[school.id]}: {by_school.get(school.id, [])}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--campaign", default="2026/2027", help="catchment snapshot to match against")
    parser.add_argument("--points", type=Path, help="saved layer-1 GeoJSON (default: fetch it)")
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    asyncio.run(run(args.campaign, args.points, args.apply))


if __name__ == "__main__":
    main()
