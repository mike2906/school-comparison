#!/usr/bin/env python3
"""Match Sofia state locations to Sofia Municipality's official school/kindergarten points.

Source: the municipality's public ArcGIS layers (schools, kindergartens, nurseries), each
point with its name, address and район. A location is matched when the point's number and
type agree with the school name *and* the addresses agree; everything else is reported for
review and never written.

Always writes a report first. Default is a dry run; --apply writes only "match" rows:
coordinates (exact precision, through the geocoding write gate), a missing district, and a
`coords_source=sofia_municipal` tag, which stops later geocoding runs replacing the point.

    uv run python scripts/import_sofia_municipal_points.py
    uv run python scripts/import_sofia_municipal_points.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import async_session_maker
from app.models import School, SchoolLocation
from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG, apply_geocode_result_to_location

LAYERS_URL = (
    "https://arcgis.sofia.bg/arcgis/rest/services/School_AllPublic/Schools_AllPublic/MapServer"
)
# Layer id -> kind of institution.
LAYERS = {0: "school", 1: "kindergarten", 2: "nursery"}
PROVIDER = "sofia_municipal_arcgis"
DEFAULT_REPORT_ROOT = Path(__file__).parent.parent / "reports" / "municipal-points"

# The municipal layers' district spellings -> the ones already stored (kg.sofia.bg, OSM).
DISTRICT_ALIASES = {"Подуене": "Подуяне", "Студентски": "Студентски град", "Нови искър": "Нови Искър"}
# Latin letters that source data uses in place of look-alike Cyrillic ones.
_LATIN_TO_CYRILLIC = str.maketrans("AaBEeKkMHOoPpCcTXxy", "АаВЕеКкМНОоРрСсТХху")
# Address words that say nothing about where the building is.
_ADDRESS_STOPWORDS = {
    "гр", "град", "софия", "ул", "улица", "бул", "булевард", "жк", "ж", "к", "кв", "квартал",
    "район", "р", "н", "бл", "блок", "вх", "ет", "ап", "до", "и", "на", "с", "м", "в", "з",
    "столична", "сграда", "част", "мр", "микрорайон", "нови", "искър",
}
# Name words shared by many institutions.
_NAME_STOPWORDS = {
    "училище", "училища", "средно", "основно", "обединено", "начално", "профилирана",
    "професионална", "гимназия", "национална", "софийска", "детска", "градина", "яслени",
    "групи", "сграда", "почасова", "организация", "филиал", "към", "ученици", "проф",
}


@dataclass
class Point:
    kind: str
    name: str
    address: str
    district: Optional[str]
    lat: float
    lng: float


@dataclass
class Row:
    location: SchoolLocation
    school: School
    status: str
    point: Optional[Point] = None
    candidates: list[Point] = field(default_factory=list)
    distance_m: Optional[float] = None


def name_kind(name: str) -> str:
    """"nursery", "kindergarten" or "school" from an institution name."""
    text = name.translate(_LATIN_TO_CYRILLIC).upper()
    if re.search(r"\bСДЯ\b|ДЕТСКИ ЯСЛИ|\bЯСЛА\b", text):
        return "nursery"
    if re.search(r"\bДГ\b|ДЕТСКА ГРАДИНА|\bОДЗ\b|\bЦДГ\b", text):
        return "kindergarten"
    return "school"


def name_key(name: str) -> Optional[tuple[str, str]]:
    """(kind, number) from a numbered institution name, e.g. ("kindergarten", "82")."""
    number = re.search(r"(\d+)", name)
    return (name_kind(name), number.group(1).lstrip("0") or "0") if number else None


def address_parts(address: str) -> tuple[set[str], set[str]]:
    """(street words, numbers) of an address, normalized for comparison."""
    text = address.translate(_LATIN_TO_CYRILLIC).casefold()
    text = re.sub(r"гр\.\s*софия|\b1\d{3}\b", " ", text)  # city and postcodes
    # Ordinals are part of the street name (ул. "8-ми март", ул. "504-та"), not house numbers.
    ordinal = r"(\d+)\s*-\s*(?:ми|ви|ри|ти|та|то|ва|ра|ия|ят)\b"
    ordinals = {f"#{n.lstrip('0')}" for n in re.findall(ordinal, text)}
    text = re.sub(ordinal, " ", text)
    words = ordinals | {
        w for w in re.findall(r"[а-я]+", text)
        if len(w) >= 3 and w not in _ADDRESS_STOPWORDS
    }
    # House and block numbers when marked; otherwise every number (ул. 210 - II м. р.).
    # Unmarked numbers are often the complex ("Младост 4"), not the building.
    marked = re.findall(r"(?:№|\bno\.?|\bбл\.?)\s*(\d+)", text)
    numbers = {n.lstrip("0") or "0" for n in (marked or re.findall(r"\d+", text))}
    return words, numbers


def addresses_agree(ours: str, theirs: str) -> bool:
    """Same street (a shared word) and a shared number, unless neither gives one.

    Numbered streets (ул. 210 № 27) have no words; their numbers must then be equal.
    """
    our_words, our_numbers = address_parts(ours)
    their_words, their_numbers = address_parts(theirs)
    if not our_words and not their_words:
        return bool(our_numbers) and our_numbers == their_numbers
    if not our_words & their_words:
        return False
    if our_numbers or their_numbers:
        # A number on one side only may be another building of the same complex.
        return bool(our_numbers & their_numbers)
    return True


def name_words(name: str) -> set[str]:
    """Distinctive words of an institution name (not its type)."""
    text = name.translate(_LATIN_TO_CYRILLIC).casefold()
    return {w for w in re.findall(r"[а-я]{4,}", text) if w not in _NAME_STOPWORDS}


def building_suffix(name: str) -> str:
    """The building part of a name ("сграда 2" in "ДГ №6 … - сграда 2"); "" for the main one.

    Hourly-care entries ("- почасова организация") run in the main building.
    """
    suffix = name.rsplit(" - ", 1)[1] if " - " in name else ""
    suffix = re.sub(r"\s+", " ", suffix.translate(_LATIN_TO_CYRILLIC).casefold()).strip()
    return "" if "почасова" in suffix else suffix


def distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dy = (lat2 - lat1) * 111_320
    dx = (lng2 - lng1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def classify(location: SchoolLocation, school: School, points: list[Point]) -> Row:
    """Match one location to at most one municipal point."""
    name = (school.name_i18n or {}).get("bg") or ""
    address = (location.address_i18n or {}).get("bg") or ""
    key = name_key(name)
    same_name = [p for p in points if key and name_key(p.name) == key]
    if same_name:
        agreeing = [p for p in same_name if addresses_agree(address, p.address)]
        if len(agreeing) > 1:
            # Several buildings at matching addresses: pair "сграда 2" with "сграда 2".
            suffix = building_suffix(name)
            same_building = [p for p in agreeing if building_suffix(p.name) == suffix]
            if len(same_building) == 1:
                agreeing = same_building
        if len(agreeing) == 1:
            point = agreeing[0]
            our_suffix, their_suffix = building_suffix(name), building_suffix(point.name)
            if (
                our_suffix and their_suffix and our_suffix != their_suffix
                and not address_parts(address)[1]
            ):
                # Different named buildings and no house number to tie them together.
                return Row(location, school, "ambiguous", None, agreeing)
            return Row(location, school, "match", point, same_name)
        if len(agreeing) > 1:
            return Row(location, school, "ambiguous", None, agreeing)
        return Row(location, school, "name_only", None, same_name)
    # No numbered name (or no point with that number): same kind at the same address,
    # confirmed by at least two distinctive name words.
    by_address = [
        p for p in points if p.kind == name_kind(name) and addresses_agree(address, p.address)
    ]
    words = name_words(name)
    named = [p for p in by_address if len(words & name_words(p.name)) >= 2]
    if len(named) == 1:
        return Row(location, school, "match", named[0], by_address)
    if len(by_address) == 1:
        return Row(location, school, "address_only", None, by_address)
    return Row(location, school, "no_match", None, by_address)


def _canonical_district(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return DISTRICT_ALIASES.get(value, value) or None


def _shares_point_with_other_address(point: Point, points: list[Point]) -> bool:
    """A point copied onto another institution's coordinates (a source data error)."""
    return any(
        other is not point
        and (other.lat, other.lng) == (point.lat, point.lng)
        and not addresses_agree(point.address, other.address)
        for other in points
    )


async def fetch_points() -> list[Point]:
    points: list[Point] = []
    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for layer, kind in LAYERS.items():
            response = await client.get(
                f"{LAYERS_URL}/{layer}/query",
                params={"where": "1=1", "outFields": "*", "outSR": 4326, "f": "json"},
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("exceededTransferLimit"):
                raise RuntimeError(f"Layer {layer} is paginated; fetch all pages first")
            for feature in payload["features"]:
                attrs, geometry = feature["attributes"], feature.get("geometry") or {}
                if geometry.get("x") is None or geometry.get("y") is None:
                    continue
                points.append(Point(
                    kind=kind,
                    name=(attrs.get("fullname") or "").strip(),
                    address=(attrs.get("fulladdress") or "").strip(),
                    district=_canonical_district(attrs.get("name_rajon")),
                    lat=float(geometry["y"]),
                    lng=float(geometry["x"]),
                ))
    return points


def write_report(rows: list[Row], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "locations.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "status", "location_id", "school_id", "name", "address", "district",
            "current_lat", "current_lng", "current_precision", "current_method",
            "point_name", "point_address", "point_district", "point_lat", "point_lng",
            "distance_m", "candidates",
        ])
        for row in rows:
            loc, point = row.location, row.point
            meta = loc.geocode_meta or {}
            writer.writerow([
                row.status, loc.id, row.school.id, (row.school.name_i18n or {}).get("bg"),
                (loc.address_i18n or {}).get("bg"), loc.district, loc.lat, loc.lng,
                meta.get("precision"), meta.get("method"),
                point.name if point else "", point.address if point else "",
                point.district if point else "", point.lat if point else "",
                point.lng if point else "",
                "" if row.distance_m is None else round(row.distance_m),
                " | ".join(f"{p.name} @ {p.address}" for p in row.candidates[:5]),
            ])
    return path


async def apply_match(db, row: Row) -> bool:
    loc, point = row.location, row.point
    result = GeocodingResult(
        lat=point.lat,
        lng=point.lng,
        success=True,
        provider=PROVIDER,
        method="municipal_point",
        precision="exact",
        formatted_address=f"{point.name}, {point.address}",
    )
    result = await apply_geocode_result_to_location(db, loc, result, school=row.school)
    if not result.success:
        return False
    if not loc.district and point.district:
        loc.district = point.district
    tags = [
        tag for tag in (loc.location_tags or [])
        if not str(tag).startswith(("coords_source=", "coords_precision="))
    ]
    loc.location_tags = [*tags, OFFICIAL_COORDS_TAG]
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the 'match' rows")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    args = parser.parse_args()

    points = await fetch_points()
    print(f"Fetched {len(points)} municipal points")
    async with async_session_maker() as db:
        pairs = (await db.execute(
            select(SchoolLocation, School)
            .join(School, School.id == SchoolLocation.school_id)
            .where(School.city == "sofia", School.school_type == "state")
            .order_by(SchoolLocation.id)
        )).all()
        rows = [classify(loc, school, points) for loc, school in pairs]
        for row in rows:
            if row.status == "match" and _shares_point_with_other_address(row.point, points):
                row.status, row.candidates = "shared_point", [row.point]
                row.point = None
        for row in rows:
            if row.point and row.location.lat is not None and row.location.lng is not None:
                row.distance_m = distance_m(
                    row.location.lat, row.location.lng, row.point.lat, row.point.lng
                )

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report = write_report(rows, args.report_root / stamp)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.status] = counts.get(row.status, 0) + 1
        print(f"Report: {report}")
        print(", ".join(f"{status}: {n}" for status, n in sorted(counts.items())))

        if not args.apply:
            print("Dry run; nothing written.")
            return
        written = 0
        for row in rows:
            if row.status == "match" and await apply_match(db, row):
                written += 1
        await db.commit()
        print(f"Wrote {written} municipal points")


if __name__ == "__main__":
    asyncio.run(main())
