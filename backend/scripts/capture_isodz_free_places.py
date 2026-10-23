"""Capture today's free places in Sofia's municipal kindergartens and ПГУ from ИСОДЗ.

Run by .github/workflows/capture-free-places.yml; needs no database. Reads every
kindergarten and ПГУ building's record (one request each) and writes one snapshot,
``<out>/bg/sofia/free-places.json``, which replaces the last one (git keeps the history).
Counts only (app.scrapers.sources.bg.kg_sofia_free_places). Docs: docs/FREE_PLACES.md

A scan that finds far fewer buildings than the last snapshot, or one in five records
failing, writes nothing and exits non-zero, so the site keeps the last good snapshot.

    uv run python -m scripts.capture_isodz_free_places --out ../free-places-data
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.scrapers.sources.bg.kg_sofia_free_places import building_free_places
from app.scrapers.sources.bg.kg_sofia_ratings import INSTITUTION_TYPES, PUBLIC_SITE, SOFIA_TZ, IsodzClient

logger = logging.getLogger("capture_isodz_free_places")

SNAPSHOT_PATH = Path("bg") / "sofia" / "free-places.json"
FAMILIES = ("kindergarten", "preparatory")
# Below this share of the last snapshot's buildings, the scan is treated as broken.
MIN_SHARE_OF_LAST = 0.8
MAX_FAILED_SHARE = 0.2


def snapshot_file(out: Path) -> Path:
    return out / SNAPSHOT_PATH


def check_scan(buildings: list[dict], failed: int, scanned: int, last: dict | None) -> str | None:
    """Why this scan must not replace the last snapshot, or None when it may."""
    if not buildings:
        return "no building has groups"
    if scanned and failed / scanned > MAX_FAILED_SHARE:
        return f"{failed} of {scanned} records failed"
    before = len((last or {}).get("buildings") or [])
    if before and len(buildings) < before * MIN_SHARE_OF_LAST:
        return f"{len(buildings)} buildings, the last snapshot had {before}"
    return None


async def scan(delay: float) -> tuple[list[dict], int, int, set[str]]:
    buildings: list[dict] = []
    failed = scanned = 0
    school_years: set[str] = set()
    seen: set[tuple[int, int]] = set()
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers={"User-Agent": "schooldecider.com free places"}) as http:
        client = IsodzClient(http, delay=delay)
        for list_type, (family, endpoint_type) in INSTITUTION_TYPES.items():
            if family not in FAMILIES:
                continue
            institutions = await client.institutions(list_type)
            logger.info("%s: %d institutions", list_type, len(institutions))
            for institution in institutions:
                if institution.get("id") is None:
                    continue
                # The ПГУ list repeats every kindergarten with a preparatory group: one
                # record, already read (and its groups staged) from the kindergarten list.
                key = (endpoint_type, int(institution["id"]))
                if key in seen:
                    continue
                seen.add(key)
                scanned += 1
                try:
                    payload = await client.dz(endpoint_type, int(institution["id"]))
                except RuntimeError as error:
                    failed += 1
                    logger.warning("%s", error)
                    continue
                items = payload.get("items") or {}
                school_years |= {items.get("schoolYear"), items.get("schoolYearNext")} - {None}
                building = building_free_places(payload, family)
                if building is not None:
                    buildings.append(building)
    buildings.sort(key=lambda b: (b["family"], len(b["source_id"]), b["source_id"]))
    return buildings, failed, scanned, school_years


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="checkout of the free-places-data branch")
    parser.add_argument("--delay", type=float, default=0.5, help="seconds between requests")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    path = snapshot_file(args.out)
    last = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    buildings, failed, scanned, school_years = asyncio.run(scan(args.delay))
    problem = check_scan(buildings, failed, scanned, last)
    if problem:
        sys.exit(f"Not written: {problem}")
    now = datetime.now(timezone.utc)
    snapshot = {
        "meta": {
            "source": "kg.sofia.bg",
            "source_url": f"{PUBLIC_SITE}spots",
            "retrieved_at": now.isoformat(timespec="seconds"),
            "as_of": now.astimezone(SOFIA_TZ).date().isoformat(),
            "school_years": sorted(school_years),
        },
        "buildings": buildings,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print({"buildings": len(buildings), "scanned": scanned, "failed": failed})


if __name__ == "__main__":
    main()
