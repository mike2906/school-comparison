"""Sofia Municipality's official address register: an address to its point.

The register ("Адреси на територията на Столична община", ГИС-София open data, CC BY 4.0)
has every approved address in the municipality: street and house number, or complex,
block and entrance, each with its own WGS84 point. It is a CSV (``;``-separated) with the
columns ``rn;region;settlement;lareaunit;block;street;streetnum;entrance;n;e``.

Matching is strict, as a missing pin is safer than a wrong one: the street name (or
complex) must be the same once spelling is normalised, the house number (or block) exactly
ours, and every register point for it close together. "до бл. 227" (next to a block)
matches nothing. Our district settles an address that exists in more than one place.
"""

from __future__ import annotations

import csv
import io
import math
import re
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Optional

CSV_ZIP_URL = (
    "https://urbandata.sofia.bg/dataset/5dd1b862-7b4e-4fed-9061-96f1f1288c1e/resource/"
    "97dd5d1f-1274-4303-8c68-1e652895a56b/download/address_sofia_csv.zip"
)
DATASET_URL = "https://urbandata.sofia.bg/dataset/address_sofia-zip"

# Register points for one address further apart than this are different places.
STREET_SPREAD_M = 60
# A block's entrances can be a long building apart.
BLOCK_SPREAD_M = 150

SOFIA = "гр. софия"
_LATIN_TO_CYRILLIC = str.maketrans("AaBEeKkMHOoPpCcTXxy", "АаВЕеКкМНОоРрСсТХху")
_QUOTES_RE = re.compile(r"[\"'„“”«»]")
# The register's district names where ours differ.
_REGION_ALIASES = {"студентски град": "студентски", "подуене": "подуяне", "студетски": "студентски"}
# Words written in full or abbreviated: compared in their short form.
_SHORT = {
    "свети": "св", "света": "св", "професор": "проф", "генерал": "ген", "академик": "акад",
    "доктор": "д-р", "др": "д-р", "полковник": "полк", "подполковник": "подполк",
}
_NUMBER = r"(\d+[а-я]?)(?![а-я\d])"


@dataclass(frozen=True)
class RegisterPoint:
    region: str
    settlement: str
    complex: str
    block: str
    street: str
    number: str
    entrance: str
    lat: float
    lng: float


@dataclass(frozen=True)
class Parsed:
    """What an address of ours names: a settlement, an optional district, and either a
    street with a house number or a complex/street with a block (and entrance)."""

    settlement: Optional[str]  # "с. балша", "гр. банкя"; None means Sofia
    region: Optional[str]
    street: Optional[str]
    number: Optional[str]
    complex: Optional[str]
    block: Optional[str]
    entrance: Optional[str]
    near: bool  # "до бл." / "до №": next to it, not at it


@dataclass(frozen=True)
class Match:
    outcome: str  # "matched", "no_address", "not_in_register", "ambiguous"
    point: Optional[tuple[float, float]] = None
    rows: tuple[RegisterPoint, ...] = ()


def _clean(text: str) -> str:
    text = _QUOTES_RE.sub(" ", text.translate(_LATIN_TO_CYRILLIC).casefold())
    return re.sub(r"\s+", " ", text).strip()


def name_key(name: str) -> str:
    """A street or complex name in one spelling: no marker, quotes, dots or hyphens, short titles."""
    text = _clean(name)
    text = re.sub(r"^(?:ул|бул|пл|алея|ж\.?\s*к|жк|кв|м-ст|местност)(?![а-я])\.?\s*", "", text)
    words = re.findall(r"[а-я]+|\d+", text.replace("д-р", "др"))
    return " ".join(_SHORT.get(w, w) for w in words)


def _same_name(ours: str, theirs: str) -> bool:
    """Same name, also when ours shortens first names to initials ("П. Ю. Тодоров")."""
    if ours == theirs:
        return True
    a, b = ours.split(), theirs.split()
    if len(a) != len(b) or a[-1] != b[-1]:
        return False
    return all(x == y or (len(x) == 1 and y.startswith(x)) for x, y in zip(a[:-1], b[:-1]))


def _number(value: str) -> str:
    return _clean(value).replace(" ", "").upper()


def parse(address: str, district: Optional[str] = None) -> Parsed:
    text = _clean(address)
    settlement = None
    if match := re.search(r"(?<![а-я])(с|гр)\.\s*([а-я][а-я ]*?)(?=,|$|\s+\d{4})", text):
        name = f"{match.group(1)}. {match.group(2).strip()}"
        settlement = None if name == SOFIA else name
    region = None
    if match := re.search(r"(?<![а-я])(?:район|р-н)\s+([а-я -]+?)(?=,|$)", text):
        region = match.group(1).strip()
    elif district:
        region = _clean(district)
    if region:
        region = _REGION_ALIASES.get(region, region)

    near = bool(re.search(r"(?<![а-я])до\s+(?:бл|№)", text))
    block = entrance = complex_ = street = number = None
    if match := re.search(r"(?<![а-я])бл\.?\s*(\d+[а-я]{0,2})(?![а-я\d])", text):
        block = match.group(1)
    if match := re.search(r"(?<![а-я])вх\.?\s*([а-я]|\d+)(?![а-я\d])", text):
        entrance = match.group(1)
    if match := re.search(r"(?<![а-я])(?:ж\.?\s*к|жк)\.?\s*([^,]+)", text):
        complex_ = name_key(match.group(1))
    if match := re.search(r"(?<![а-я])(?:ул|бул|пл|алея)(?![а-я])\.?\s*([^,№]+?)\s*(?:,|№|$)", text):
        street = name_key(match.group(1))
        tail = text[match.end() - 1:]
        if num := re.match(rf"\s*(?:№|,\s*№)\s*{_NUMBER}", tail):
            number = num.group(1)
        elif num := re.search(rf"\s{_NUMBER}$", match.group(1)):
            # "бул. Никола Вапцаров 47": the number written after the name.
            number = num.group(1)
            street = name_key(match.group(1)[: num.start()])
    elif match := re.search(rf"(?<![а-я])№\s*{_NUMBER}", text):
        number = match.group(1)
    return Parsed(
        settlement=settlement,
        region=region,
        street=street or None,
        number=_number(number) if number else None,
        complex=complex_ or None,
        block=_number(block) if block else None,
        entrance=_number(entrance) if entrance else None,
        near=near,
    )


def read_register(text: str) -> list[RegisterPoint]:
    """The register's rows from its CSV text."""
    points = []
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        try:
            lat, lng = float(row["n"]), float(row["e"])
        except (TypeError, ValueError):
            continue
        points.append(
            RegisterPoint(
                region=_clean(row.get("region") or ""),
                settlement=_clean(row.get("settlement") or ""),
                complex=name_key(row.get("lareaunit") or ""),
                block=_number(row.get("block") or ""),
                street=name_key(row.get("street") or ""),
                number=_number(row.get("streetnum") or ""),
                entrance=_number(row.get("entrance") or ""),
                lat=lat,
                lng=lng,
            )
        )
    return points


def read_register_zip(data: bytes) -> list[RegisterPoint]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        return read_register(archive.read(name).decode("utf-8-sig"))


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 111_320, (a[1] - b[1]) * 111_320 * math.cos(lat))


def _in_settlement(row: RegisterPoint, settlement: Optional[str]) -> bool:
    if settlement is None:  # Sofia, its кв. included ("гр. софия, кв. драгалевци")
        return row.settlement == SOFIA or row.settlement.startswith(SOFIA + ",")
    return row.settlement == settlement


def _together(rows: list[RegisterPoint], spread: float) -> Optional[tuple[float, float]]:
    """The rows' centre when every row is within ``spread`` of it, else None."""
    centre = (sum(r.lat for r in rows) / len(rows), sum(r.lng for r in rows) / len(rows))
    if all(_distance_m(centre, (r.lat, r.lng)) <= spread for r in rows):
        return round(centre[0], 6), round(centre[1], 6)
    return None


def _settle(rows: list[RegisterPoint], region: Optional[str], spread: float) -> Match:
    if not rows:
        return Match("not_in_register")
    point = _together(rows, spread)
    if point is None and region:
        rows = [r for r in rows if r.region == region] or rows
        point = _together(rows, spread)
    if point is None:
        return Match("ambiguous", rows=tuple(rows))
    return Match("matched", point, tuple(rows))


def match(parsed: Parsed, register: Iterable[RegisterPoint]) -> Match:
    """The register point for our address, or why there is none."""
    if parsed.near:
        return Match("no_address")
    rows = [r for r in register if _in_settlement(r, parsed.settlement)]
    if parsed.block:
        found = [
            r for r in rows
            if r.block == parsed.block
            and (
                (parsed.complex and r.complex and _same_name(parsed.complex, r.complex))
                or (parsed.street and r.street and _same_name(parsed.street, r.street))
            )
        ]
        if parsed.entrance:
            found = [r for r in found if r.entrance == parsed.entrance] or found
        return _settle(found, parsed.region, BLOCK_SPREAD_M)
    if parsed.street and parsed.number:
        found = [
            r for r in rows
            if r.number == parsed.number and r.street and _same_name(parsed.street, r.street)
        ]
        return _settle(found, parsed.region, STREET_SPREAD_M)
    return Match("no_address")
