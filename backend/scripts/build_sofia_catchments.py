#!/usr/bin/env python3
"""Build the static Sofia first-grade catchment snapshot the frontend reads.

Sources (both official, both keyed by the same GIS school id, which kg.sofia.bg calls
`esriId`):
- arcgis.sofia.bg `Schools/SchoolsZones` MapServer: layer 2 catchment polygons, layer 1
  school points with `adjacentezonesid` (neighbouring catchments), layer 3 districts.
- urbandata.sofia.bg "Адреси и прилежащи училища СО": every address with its catchment
  school and neighbouring schools.

Writes to frontend/public/catchments/bg/sofia/<campaign>/:
- catchments.json     one simplified polygon per school, with its name and neighbours
- districts.json      the 24 районa, simplified
- addresses.json      settlement + street -> catchment school -> house numbers

The sites block cloud IPs, so run this locally or in GitHub Actions. `--source-dir`
reads previously downloaded files instead of fetching (same names as the fetch saves).

    uv run python scripts/build_sofia_catchments.py --campaign 2026/2027
    uv run python scripts/build_sofia_catchments.py --campaign 2026/2027 --source-dir /tmp/src
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import httpx

ARCGIS = "https://arcgis.sofia.bg/arcgis/rest/services/Schools/SchoolsZones/MapServer"
ADDRESSES_URL = (
    "https://urbandata.sofia.bg/dataset/34e764b9-dc1f-466d-9eed-ff0d4123bab4/resource/"
    "f1a40d7a-2e38-46c7-81a1-d5d8bd45261f/download/addresses-and-associated-schools.xlsx"
)
LAYERS = {
    "school_catchment_polygons.geojson": 2,
    "schools_points.geojson": 1,
    "districts_rayoni_polygons.geojson": 3,
}
ADDRESS_FILES = ("addresses-and-associated-schools.xlsx", "addresses_and_schools.csv.gz")
OUT_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "catchments" / "bg" / "sofia"

# About 2 m at Sofia's latitude: invisible on the map, cuts the files several times.
TOLERANCE = 0.00002
DECIMALS = 5

# The map layer spells one district colloquially; use the official name (as kg.sofia.bg does).
DISTRICT_NAMES = {"Подуене": "Подуяне"}

COL_SETTLEMENT_CODE = "Код на населено място ГРАО"
COL_SETTLEMENT = "Име на населено място"
COL_DISTRICT = "Код на район"
COL_STREET_CODE = "Код на ПА ГРАО"
COL_STREET = "Наименование на пътна артерия"
COL_NUMBER = "Номер"
COL_SCHOOL = "ИД на прилежащо училище"
COL_NEIGHBOURS = "ИД на гранични прилежащи училища"


# --- sources ---------------------------------------------------------------------------

def fetch_sources(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for name, layer in LAYERS.items():
            response = client.get(
                f"{ARCGIS}/{layer}/query",
                params={"where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson"},
            )
            response.raise_for_status()
            (target / name).write_bytes(response.content)
        response = client.get(ADDRESSES_URL)
        response.raise_for_status()
        (target / ADDRESS_FILES[0]).write_bytes(response.content)


def read_xlsx_rows(data: bytes) -> list[dict[str, str]]:
    """The first sheet of an .xlsx as dicts, with the standard library only."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        shared = []
        if "xl/sharedStrings.xml" in book.namelist():
            root = ElementTree.fromstring(book.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t")) for si in root.findall("m:si", ns)]
        sheet = ElementTree.fromstring(book.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(f"{{{ns['m']}}}row"):
        values = {}
        for cell in row.findall("m:c", ns):
            column = re.match(r"[A-Z]+", cell.get("r")).group(0)
            kind = cell.get("t")
            if kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.iter(f"{{{ns['m']}}}t"))
            else:
                raw = cell.find("m:v", ns)
                value = "" if raw is None else raw.text or ""
                if kind == "s":
                    value = shared[int(value)]
            values[column] = value
        rows.append(values)
    header = rows[0]
    columns = sorted(header, key=lambda c: (len(c), c))
    return [{header[c]: r.get(c, "") for c in columns} for r in rows[1:]]


def read_address_rows(source: Path) -> list[dict[str, str]]:
    xlsx = source / ADDRESS_FILES[0]
    if xlsx.exists():
        return read_xlsx_rows(xlsx.read_bytes())
    with gzip.open(source / ADDRESS_FILES[1], "rt", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --- geometry --------------------------------------------------------------------------

def _perpendicular(point, start, end) -> float:
    (x, y), (x1, y1), (x2, y2) = point, start, end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return ((x - x1) ** 2 + (y - y1) ** 2) ** 0.5
    return abs(dy * x - dx * y + x2 * y1 - y2 * x1) / (dx * dx + dy * dy) ** 0.5


def simplify_ring(ring: list[list[float]], tolerance: float) -> list[list[float]]:
    """Douglas-Peucker on a closed ring, keeping it closed and at least a triangle."""
    if len(ring) <= 4:
        return ring
    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        first, last = stack.pop()
        index, distance = None, tolerance
        for i in range(first + 1, last):
            d = _perpendicular(ring[i], ring[first], ring[last])
            if d > distance:
                index, distance = i, d
        if index is not None:
            keep[index] = True
            stack += [(first, index), (index, last)]
    result = [p for p, k in zip(ring, keep) if k]
    return result if len(result) >= 4 else ring


def simplify_geometry(geometry: dict) -> dict:
    def ring(r):
        return [[round(x, DECIMALS), round(y, DECIMALS)] for x, y in simplify_ring(r, TOLERANCE)]

    if geometry["type"] == "Polygon":
        coordinates = [ring(r) for r in geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        coordinates = [[ring(r) for r in polygon] for polygon in geometry["coordinates"]]
    else:
        raise ValueError(f"unexpected geometry {geometry['type']}")
    return {"type": geometry["type"], "coordinates": coordinates}


# --- build -----------------------------------------------------------------------------

def split_ids(value: str) -> list[str]:
    return sorted({part.strip() for part in (value or "").split(",") if part.strip()}, key=int)


def house_number(raw: str) -> str:
    """`095` -> `95`, `001А` -> `1А`: as a parent types it."""
    match = re.match(r"^0*(\d*)(.*)$", raw.strip())
    digits, rest = match.group(1), match.group(2)
    return (digits or "0") + rest


def build(source: Path, campaign: str) -> tuple[dict, dict, dict, list[str]]:
    problems: list[str] = []
    polygons = json.loads((source / "school_catchment_polygons.geojson").read_text(encoding="utf-8"))
    points = json.loads((source / "schools_points.geojson").read_text(encoding="utf-8"))
    districts = json.loads((source / "districts_rayoni_polygons.geojson").read_text(encoding="utf-8"))
    rows = read_address_rows(source)

    neighbours = {}
    names = {}
    for feature in points["features"]:
        props = feature["properties"]
        school_id = str(props.get("integrationid") or "").strip()
        if school_id and props.get("adjacentezonesid"):
            neighbours[school_id] = split_ids(props["adjacentezonesid"])
            names[school_id] = props.get("fullname") or ""

    catchment_features = []
    for feature in polygons["features"]:
        props = feature["properties"]
        school_id = str(props.get("schoolid") or props.get("id"))
        if school_id not in neighbours:
            problems.append(f"polygon {school_id} has no school point with neighbours")
        catchment_features.append({
            "type": "Feature",
            "properties": {
                "id": school_id,
                "name": names.get(school_id) or props.get("name") or "",
                "neighbours": neighbours.get(school_id, []),
            },
            "geometry": simplify_geometry(feature["geometry"]),
        })
    polygon_ids = {f["properties"]["id"] for f in catchment_features}

    district_features = [{
        "type": "Feature",
        "properties": {
            "code": f["properties"]["num"],
            "name": DISTRICT_NAMES.get(f["properties"]["name_rajon"], f["properties"]["name_rajon"]),
        },
        "geometry": simplify_geometry(f["geometry"]),
    } for f in districts["features"]]

    streets: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row[COL_SETTLEMENT_CODE], row[COL_STREET_CODE])
        school_id = row[COL_SCHOOL].strip()
        if school_id not in polygon_ids:
            problems.append(f"address school {school_id} has no polygon")
        if split_ids(row[COL_NEIGHBOURS]) != neighbours.get(school_id, []):
            problems.append(f"address neighbours differ from the map for school {school_id}")
        street = streets.setdefault(key, {
            "settlement": row[COL_SETTLEMENT],
            "street": row[COL_STREET],
            "district": row[COL_DISTRICT],
            "numbers": defaultdict(set),
        })
        street["numbers"][school_id].add(house_number(row[COL_NUMBER]))

    def number_key(n: str):
        digits = re.match(r"\d+", n)
        return (int(digits.group(0)) if digits else 0, n)

    retrieved = date.today().isoformat()
    sources = [
        {"title": "Столична община: райони към училищата (ArcGIS)", "url": ARCGIS, "retrieved": retrieved},
        {"title": "Адреси и прилежащи училища СО (urbandata.sofia.bg)", "url": ADDRESSES_URL, "retrieved": retrieved},
    ]
    meta = {"campaign": campaign, "stage": "grade_1", "sources": sources}
    catchments = {"type": "FeatureCollection", "meta": meta, "features": catchment_features}
    district_collection = {"type": "FeatureCollection", "meta": {"sources": sources[:1]}, "features": district_features}
    addresses = {
        **meta,
        "fields": ["settlement_code", "street_code", "settlement", "street", "district", "numbers_by_school"],
        "streets": [
            [settlement_code, street_code, s["settlement"], s["street"], s["district"],
             {school: ",".join(sorted(numbers, key=number_key)) for school, numbers in sorted(s["numbers"].items())}]
            for (settlement_code, street_code), s in sorted(streets.items())
        ],
    }
    return catchments, district_collection, addresses, sorted(set(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--campaign", required=True, help="school year the catchments apply to, e.g. 2026/2027")
    parser.add_argument("--source-dir", type=Path, help="read downloaded files instead of fetching")
    parser.add_argument("--download-dir", type=Path, default=Path("/tmp/sofia-catchment-sources"))
    args = parser.parse_args()
    if not re.fullmatch(r"\d{4}/\d{4}", args.campaign):
        parser.error("--campaign looks like 2026/2027")

    source = args.source_dir
    if source is None:
        fetch_sources(args.download_dir)
        source = args.download_dir

    catchments, districts, addresses, problems = build(source, args.campaign)
    for problem in problems:
        print(f"PROBLEM: {problem}", file=sys.stderr)
    if problems:
        print("Not written: the sources disagree.", file=sys.stderr)
        return 1

    out = OUT_ROOT / args.campaign.replace("/", "-")
    out.mkdir(parents=True, exist_ok=True)
    for name, data in (("catchments.json", catchments), ("districts.json", districts), ("addresses.json", addresses)):
        path = out / name
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        print(f"{path}: {path.stat().st_size // 1024} KB")
    print(f"{len(catchments['features'])} catchments, {len(districts['features'])} districts, "
          f"{len(addresses['streets'])} streets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
