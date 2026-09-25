#!/usr/bin/env python3
"""UF44 one-off: re-geocode Sofia pin gaps through GeocodingService, dry run first.

    uv run python scripts/geocode_pin_gaps_uf44.py dry-run
    uv run python scripts/geocode_pin_gaps_uf44.py dry-run 105,133   # re-check these only
    uv run python scripts/geocode_pin_gaps_uf44.py apply reports/uf44/<stamp>/proposed.json

Dry run: each location is geocoded in its own short transaction that is rolled back (the
changes proposed for earlier locations are replayed into it first, so later locations see
them). Row locks last one location's geocode, never the whole run. Order: B (pinned GeoJSON
name matches the multi-location guard rejects), C (pinned non-official locations at an
official building's address), D (state locations fixed individually with evidence), A (every
other unpinned Sofia location). Writes locations.csv, proposed.json and summary.json.

Apply writes exactly the proposed after-state, refusing if any location changed since.
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from app.config import get_settings
from app.database import engine
from app.models import School, SchoolLocation
from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.composite import CompositeGeocodingProvider
from app.services.geocoding.nominatim import NominatimProvider
from app.services.geocoding.service import (
    REGISTER_ADDRESS_CONFLICT,
    GeocodingService,
    nominatim_user_agent,
    same_school_shared_points,
)
from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG
from import_sofia_municipal_points import Row, _canonical_district, apply_match, district_at, fetch_points

REPORT_ROOT = Path(os.environ.get("UF44_REPORT_ROOT") or Path(__file__).parent.parent / "reports" / "uf44")
# Evidence-backed individual fixes: location id -> exact municipal point name.
OFFICIAL_BY_EVIDENCE = {
    3029: "ДГ №99 Брезичка - сграда бл.4",   # kg.sofia 708 "сграда бл. 4 почасова организация"
    2974: "ДГ №67 Чучулига - сграда бл. 8",  # kg.sofia 233, same name
    2979: "ДГ №70 Пролет (с яслени групи)",  # kg.sofia 111, same name; the one ДГ №70 point
    762: '108. СУ "Никола Беловеждов"',      # 108su.net: Район Искър, ж.к. Дружба 1
}
STATE_KEYS = ("lat", "lng", "district", "geocode_meta", "location_tags")


def _address(loc: SchoolLocation) -> str:
    return (loc.address_i18n or {}).get("bg") or (loc.address_i18n or {}).get("en") or ""


def _state(loc: SchoolLocation) -> dict:
    return json.loads(json.dumps({k: getattr(loc, k) for k in STATE_KEYS}, default=str))


class _RolledBack:
    """One short transaction that is always rolled back; service commits are savepoints."""

    async def __aenter__(self) -> AsyncSession:
        self.conn = await engine.connect()
        self.trans = await self.conn.begin()
        self.db = AsyncSession(
            bind=self.conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        return self.db

    async def __aexit__(self, *exc) -> None:
        await self.db.close()
        await self.trans.rollback()
        await self.conn.close()


async def _replay(db: AsyncSession, proposed: dict) -> None:
    for loc_id, change in proposed.items():
        loc = await db.get(SchoolLocation, int(loc_id))
        for key, value in change["after"].items():
            setattr(loc, key, value)
    await db.flush()


async def _counts(db: AsyncSession) -> dict:
    rows = (await db.execute(
        select(School.school_type, SchoolLocation.lat)
        .join(School, School.id == SchoolLocation.school_id)
        .where(School.city == "sofia")
    )).all()
    out: dict = {}
    for school_type, lat in rows:
        entry = out.setdefault(school_type, {"without_pin": 0, "total": 0})
        entry["total"] += 1
        entry["without_pin"] += lat is None
    return out


def _is_neighbourhood_centroid(meta: dict) -> bool:
    first = (meta.get("formatted_address") or "").split(",")[0].strip()
    return meta.get("provider") == "nominatim" and first.startswith(("ж.к.", "кв.", "в.з.", "м."))


async def _select(provider) -> tuple[list, dict]:
    async with _RolledBack() as db:
        service = GeocodingService(db=db, provider=provider)
        pairs = (await db.execute(
            select(SchoolLocation, School)
            .join(School, School.id == SchoolLocation.school_id)
            .where(School.city == "sofia")
            .order_by(SchoolLocation.id)
        )).all()
        groups: dict[str, list] = {"B": [], "C": [], "D": [], "A": []}
        for loc, school in pairs:
            meta = loc.geocode_meta or {}
            if loc.id in OFFICIAL_BY_EVIDENCE:
                groups["D"].append((loc.id, ""))
            elif loc.lat is None:
                groups["A"].append((loc.id, ""))
            elif OFFICIAL_COORDS_TAG in (loc.location_tags or []):
                continue
            else:
                reason = None
                if meta.get("method") == "geojson_name_match":
                    reason = await service._name_match_conflict(loc, _address(loc), GeocodingResult(
                        lat=loc.lat, lng=loc.lng, success=True, provider="geojson_bg",
                        method="geojson_name_match", precision="approximate",
                        formatted_address=meta.get("formatted_address"),
                    ))
                if reason:
                    groups["B"].append((loc.id, reason))
                    continue
                official = await service._official_point_at_same_address(loc, school, _address(loc), "bg")
                if official and (official.lat, official.lng) != (loc.lat, loc.lng):
                    groups["C"].append((loc.id, ""))
        schools = {loc.id: school for loc, school in pairs}
        work = [(g, loc_id, why) for g in "BCDA" for loc_id, why in groups[g]]
        return work, schools


async def dry_run(location_ids: list[int] | None = None) -> None:
    provider = CompositeGeocodingProvider(user_agent=nominatim_user_agent(get_settings()))
    points = {p.name: p for p in await fetch_points()}
    async with _RolledBack() as db:
        before_counts = await _counts(db)
    if location_ids:
        # Re-check given locations only (group R): forced re-geocode, every gate on.
        work, schools = [("R", loc_id, "") for loc_id in location_ids], {}
    else:
        work, schools = await _select(provider)
    proposed: dict = {}
    rows = []
    for group, loc_id, why in work:
        async with _RolledBack() as db:
            await _replay(db, proposed)
            loc = await db.get(SchoolLocation, loc_id)
            school = schools.get(loc_id) or await db.get(School, loc.school_id)
            before = _state(loc)
            if group == "D":
                point = points[OFFICIAL_BY_EVIDENCE[loc_id]]
                assert await apply_match(db, Row(loc, school, "match", point))
                error = None
            else:
                error = (await GeocodingService(db=db, provider=provider).geocode_location(
                    loc, force=True
                )).error
            after = _state(loc)
            address = _address(loc)
            expected = loc.district or NominatimProvider._address_areas(address)[1]
        meta = after["geocode_meta"] or {}
        moved = (after["lat"], after["lng"]) != (before["lat"], before["lng"])
        is_centroid = after["lat"] is not None and _is_neighbourhood_centroid(meta)
        # UF44 withheld new centroid pins; a re-check (R) takes the pipeline's result as is.
        centroid = moved and is_centroid and group != "R"
        if error == REGISTER_ADDRESS_CONFLICT:
            outcome = "address conflict, not changed"
        elif not moved:
            outcome = "unchanged pin" if after["lat"] is not None else "still no pin"
        elif after["lat"] is None:
            outcome = "PIN CLEARED"
        else:
            outcome = "new pin" if before["lat"] is None else "pin moved"
        apply = after != before and not centroid
        if apply:
            proposed[str(loc_id)] = {"before": before, "after": after}
        rows.append({
            "group": group, "location_id": loc_id, "school_id": school.id,
            "school_type": school.school_type, "school": (school.name_i18n or {}).get("bg"),
            "address": address,
            "old_status": (before["geocode_meta"] or {}).get("rejection_reason")
            or (before["geocode_meta"] or {}).get("status"),
            "old_method": (before["geocode_meta"] or {}).get("method"),
            "old_precision": (before["geocode_meta"] or {}).get("precision"),
            "old_lat": before["lat"], "old_lng": before["lng"], "guard_reason": why,
            "outcome": outcome, "new_lat": after["lat"], "new_lng": after["lng"],
            "provider": meta.get("provider"), "method": meta.get("method"),
            "precision": meta.get("precision"),
            "approximate": meta.get("precision") == "approximate" if after["lat"] is not None else "",
            "new_status": error or meta.get("rejection_reason") or meta.get("status"),
            "formatted_address": meta.get("formatted_address"),
            "expected_district": expected,
            "note": "neighbourhood centroid" if is_centroid else "",
            "apply": "yes" if apply else ("WITHHELD: neighbourhood centroid" if centroid else "no change"),
        })
        print(group, loc_id, outcome, after["lat"], after["lng"], meta.get("method"), flush=True)

    # District at each new point, outside any transaction.
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for row in rows:
            row["district_at_point"] = row["district_check"] = ""
            if row["apply"] != "yes" or row["new_lat"] is None:
                continue
            found = await district_at(client, row["new_lat"], row["new_lng"])
            expected = _canonical_district(row["expected_district"])
            row["district_at_point"] = found
            row["district_check"] = (
                "unknown (no district on record)" if not expected or not found
                else "ok" if expected.casefold() in found.casefold() or found.casefold() in expected.casefold()
                else "MISMATCH"
            )

    async with _RolledBack() as db:
        await _replay(db, proposed)
        after_counts = await _counts(db)
        shared = await same_school_shared_points(db, country_code="bg", city="sofia")
        shared = [{"school": a.school_id, "a": a.id, "b": b.id, "m": round(d)} for a, b, d in shared]

    out_dir = REPORT_ROOT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "locations.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "proposed.json").write_text(json.dumps(proposed, ensure_ascii=False, indent=1))
    outcomes: dict = {}
    for row in rows:
        key = f'{row["group"]}: {row["outcome"]}' + (" (withheld)" if row["apply"].startswith("WITHHELD") else "")
        outcomes[key] = outcomes.get(key, 0) + 1
    summary = {
        "before": before_counts, "after_if_applied": after_counts,
        "changes": len(proposed), "outcomes": outcomes, "shared_points_after": shared,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("Report:", out_dir)


async def apply(path: str) -> None:
    proposed = json.loads(Path(path).read_text())
    async with AsyncSession(engine, expire_on_commit=False) as db:
        for loc_id, change in proposed.items():
            loc = await db.get(SchoolLocation, int(loc_id))
            if _state(loc) != change["before"]:
                raise SystemExit(f"location {loc_id} changed since the dry run; nothing written")
            for key, value in change["after"].items():
                setattr(loc, key, value)
        await db.commit()
    print(f"Applied {len(proposed)} location changes")


if __name__ == "__main__":
    if sys.argv[1:2] == ["dry-run"]:
        asyncio.run(dry_run([int(i) for i in sys.argv[2].split(",")] if len(sys.argv) > 2 else None))
    elif sys.argv[1:2] == ["apply"] and len(sys.argv) == 3:
        asyncio.run(apply(sys.argv[2]))
    else:
        raise SystemExit(__doc__)
