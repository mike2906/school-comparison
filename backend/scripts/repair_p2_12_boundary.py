#!/usr/bin/env python3
"""Apply the bounded, deterministic stored-data repairs from P2.12(b)-(d).

This script makes no HTTP or LLM calls. It corrects the one identified coordinate
collision from the checked-in Bulgarian education GeoJSON and verifies that the
three unresolved cohort locations plus school 161 are already safely withheld.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker
from app.models import School, SchoolLocation
from app.services.geocoding.service import geocode_failure_is_terminal
from app.utils.website_data import WEBSITE_DATA_WITHHELD_KEY


COLLISION_LOCATION_ID = 119
COLLISION_OTHER_LOCATION_ID = 1147
COLLISION_OLD_COORDS = (42.6548862, 23.4008491)
COLLISION_CORRECT_COORDS = (42.66442, 23.39475)
COLLISION_SOURCE_FEATURE_ID = "BG_30912207846"
TERMINAL_LOCATION_IDS = (1143, 1145, 1188)
SCHOOL_161_ID = 161
SCHOOL_161_URL = "https://www.5ou-ivanvazov.com"


def _coords(location: SchoolLocation) -> tuple[float | None, float | None]:
    return location.lat, location.lng


def _coords_match(
    actual: tuple[float | None, float | None],
    expected: tuple[float, float],
) -> bool:
    return all(
        value is not None and abs(float(value) - target) < 1e-8
        for value, target in zip(actual, expected)
    )


async def repair_collision(db: AsyncSession, *, apply: bool) -> dict[str, Any]:
    location = await db.get(SchoolLocation, COLLISION_LOCATION_ID)
    other = await db.get(SchoolLocation, COLLISION_OTHER_LOCATION_ID)
    if location is None or other is None:
        raise RuntimeError("P2.12 collision locations 119/1147 are missing")
    if location.school_id != 116 or "5036" not in str(location.address_i18n):
        raise RuntimeError("location 119 identity/address drifted; refusing repair")
    if other.school_id != 595 or "5006" not in str(other.address_i18n):
        raise RuntimeError("location 1147 identity/address drifted; refusing repair")
    if not _coords_match(_coords(other), COLLISION_OLD_COORDS):
        raise RuntimeError("location 1147 coordinates drifted; refusing repair")

    if _coords_match(_coords(location), COLLISION_CORRECT_COORDS):
        action = "already_correct"
    elif _coords_match(_coords(location), COLLISION_OLD_COORDS):
        action = "corrected" if apply else "would_correct"
        if apply:
            location.lat, location.lng = COLLISION_CORRECT_COORDS
            location.geocode_meta = {
                "status": "accepted",
                "provider": "geojson_bg",
                "method": "geojson_name_match",
                "precision": "exact",
                "formatted_address": "Ж.К.ДРУЖБА-1 | УЛ.50-36, 1592 СТОЛИЧНА",
                "candidate": {
                    "lat": COLLISION_CORRECT_COORDS[0],
                    "lng": COLLISION_CORRECT_COORDS[1],
                },
                "source_feature_id": COLLISION_SOURCE_FEATURE_ID,
                "repair": "P2.12(b)",
            }
    else:
        raise RuntimeError(
            f"location 119 coordinates drifted: {_coords(location)}; refusing repair"
        )

    return {
        "location_id": COLLISION_LOCATION_ID,
        "other_location_id": COLLISION_OTHER_LOCATION_ID,
        "action": action,
        "correct_coords": list(COLLISION_CORRECT_COORDS),
        "source_feature_id": COLLISION_SOURCE_FEATURE_ID,
    }


async def audit_terminal_locations(db: AsyncSession) -> list[int]:
    verified: list[int] = []
    for location_id in TERMINAL_LOCATION_IDS:
        location = await db.get(SchoolLocation, location_id)
        if location is None:
            raise RuntimeError(f"terminal location {location_id} is missing")
        if location.lat is not None or location.lng is not None:
            raise RuntimeError(f"terminal location {location_id} unexpectedly has coordinates")
        if not geocode_failure_is_terminal(location.geocode_meta):
            raise RuntimeError(
                f"terminal location {location_id} lacks complete failure evidence"
            )
        verified.append(location_id)
    return verified


async def audit_school_161(db: AsyncSession) -> bool:
    school = await db.get(School, SCHOOL_161_ID)
    if school is None:
        raise RuntimeError("school 161 is missing")
    attributes = school.attributes if isinstance(school.attributes, dict) else {}
    candidate = str(attributes.get("website_candidate_url") or "").rstrip("/")
    if candidate != SCHOOL_161_URL:
        raise RuntimeError(f"school 161 candidate URL drifted: {candidate!r}")
    if school.website_url is not None:
        raise RuntimeError("school 161 unexpectedly has a published website_url")
    if school.scrape_status != "failed_validate":
        raise RuntimeError(f"school 161 scrape status drifted: {school.scrape_status!r}")
    if attributes.get(WEBSITE_DATA_WITHHELD_KEY) is not True:
        raise RuntimeError("school 161 is not withheld")
    return True


async def run_repair(db: AsyncSession, *, apply: bool) -> dict[str, Any]:
    collision = await repair_collision(db, apply=apply)
    terminal_locations = await audit_terminal_locations(db)
    school_161_withheld = await audit_school_161(db)
    if apply:
        await db.commit()
    return {
        "applied": apply,
        "collision": collision,
        "terminal_locations": terminal_locations,
        "school_161_withheld": school_161_withheld,
        "network_calls": 0,
        "llm_calls": 0,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the guarded coordinate repair",
    )
    args = parser.parse_args()
    async with async_session_maker() as db:
        result = await run_repair(db, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
