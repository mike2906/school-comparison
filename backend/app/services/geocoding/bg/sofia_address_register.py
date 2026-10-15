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
_NUMBER = r"(\d+(?:[а-я]|\s[а-е])?)(?![а-я\d])"  # "5а", "79 б" (not "16 и 18")
# A street name used for two streets: one house number over this far apart.
NAMESAKE_M = 500
# What may follow a town's name when no comma does.
_MARKER = r"(?:ул|бул|пл|алея|ж\.?\s*к|жк|кв|район|р-н|бл|№)"


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
    label: str = ""  # the address as the register writes it, for display


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
    region_checked: bool = False  # our address or district names the register row's district


def _clean(text: str) -> str:
    text = _QUOTES_RE.sub(" ", text.translate(_LATIN_TO_CYRILLIC).casefold())
    return re.sub(r"\s+", " ", text).strip()


_STREET_KINDS = {"ул": "ул", "улица": "ул", "бул": "бул", "булевард": "бул", "пл": "пл", "площад": "пл", "алея": "алея"}
_STREET_KIND_RE = re.compile(r"^(ул|улица|бул|булевард|пл|площад|алея)(?![а-я])\.?\s*")


def name_key(name: str) -> str:
    """A complex name in one spelling: no marker, quotes, dots or hyphens, short titles;
    Roman numerals kept ("цар борис iii")."""
    text = _clean(name)
    text = re.sub(r"^(?:ж\.?\s*к|жк|кв|м-ст|местност)(?![а-я])\.?\s*", "", text)
    words = re.findall(r"[а-яa-z]+|\d+", text.replace("д-р", "др"))
    return " ".join(_SHORT.get(w, w) for w in words)


def street_key(name: str) -> str:
    """A street name as ``name_key`` with its kind first ("ул витоша" is not "бул витоша")."""
    text = _clean(name)
    kind = _STREET_KIND_RE.match(text)
    if kind is None:
        return ""
    rest = name_key(text[kind.end():])
    return f"{_STREET_KINDS[kind.group(1)]} {rest}" if rest else ""


def _same_name(ours: str, theirs: str) -> bool:
    """Same name, also when ours shortens first names to initials ("П. Ю. Тодоров")."""
    if ours == theirs:
        return True
    a, b = ours.split(), theirs.split()
    if len(a) != len(b) or a[-1] != b[-1]:
        return False
    return all(
        x == y or (len(x) == 1 and x.isalpha() and not x.isascii() and y.startswith(x))
        for x, y in zip(a[:-1], b[:-1])
    )


def _number(value: str) -> str:
    return _clean(value).replace(" ", "").upper()


def parse(address: str, district: Optional[str] = None) -> Parsed:
    text = _clean(address)
    settlement = None
    if match := re.search(rf"(?<![а-я])(с|гр)\.\s*([а-я][а-я ]*?)(?=,|$|\s+\d{{4}}|\s+{_MARKER}(?![а-я]))", text):
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
    if match := re.search(r"(?<![а-я])(?:ж\.?\s*к|жк|кв|м-ст)\.?\s*([^,]+)", text):
        complex_ = name_key(match.group(1))
    if match := re.search(r"(?<![а-я])((?:ул|бул|пл|алея)(?![а-я])\.?)\s*([^,№]+?)\s*(?:,|№|$)", text):
        kind = match.group(1)
        street = street_key(f"{kind} {match.group(2)}")
        tail = text[match.end() - 1:]
        if num := re.match(rf"\s*(?:№|,\s*№)\s*{_NUMBER}", tail):
            number = num.group(1)
        elif num := re.search(rf"\s{_NUMBER}$", match.group(2)):
            # "бул. Никола Вапцаров 47": the number written after the name.
            number = num.group(1)
            street = street_key(f"{kind} {match.group(2)[: num.start()]}")
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


def _label(row: dict) -> str:
    """"гр. Банкя, ул. Тенев баир 2", "гр. София, ж.к. Младост 4, бл. 460А, вх. 2"."""
    get = lambda key: (row.get(key) or "").strip()  # noqa: E731
    parts = [get("settlement"), get("lareaunit")]
    parts.append(" ".join(p for p in (get("street"), get("streetnum")) if p))
    if get("block"):
        parts.append(f"бл. {get('block')}")
    if get("entrance"):
        parts.append(f"вх. {get('entrance')}")
    return ", ".join(p for p in parts if p)


def _cell(lat: float, lng: float) -> tuple[int, int]:
    return round(lat * 1000), round(lng * 1000)  # about 110 m by 80 m


class Register:
    """The register's points, looked up by house number and by block."""

    def __init__(self, points: list[RegisterPoint]):
        self.points = points
        self.by_number: dict[str, list[RegisterPoint]] = {}
        self.by_block: dict[str, list[RegisterPoint]] = {}
        self.by_cell: dict[tuple[int, int], list[RegisterPoint]] = {}
        for point in points:
            self.by_cell.setdefault(_cell(point.lat, point.lng), []).append(point)
            if point.number:
                self.by_number.setdefault(point.number, []).append(point)
            if point.block:
                self.by_block.setdefault(point.block, []).append(point)
        # Street names that are two streets in one town (ул. Стара планина in Оборище and
        # in Кремиковци): the same house number twice, far apart.
        self.namesakes: set[tuple[str, str]] = set()
        for number_points in self.by_number.values():
            seen: dict[tuple[str, str], tuple[float, float]] = {}
            for point in number_points:
                if not point.street:
                    continue
                key = (_town(point.settlement), point.street)
                first = seen.setdefault(key, (point.lat, point.lng))
                if distance_m(first, (point.lat, point.lng)) > NAMESAKE_M:
                    self.namesakes.add(key)

    def __len__(self) -> int:
        return len(self.points)

    def near(self, point: tuple[float, float], metres: float) -> list[RegisterPoint]:
        """Register points within ``metres`` (up to about 80) of ``point``, nearest first."""
        lat, lng = _cell(*point)
        found = [
            p
            for dlat in (-1, 0, 1)
            for dlng in (-1, 0, 1)
            for p in self.by_cell.get((lat + dlat, lng + dlng), [])
            if distance_m(point, (p.lat, p.lng)) <= metres
        ]
        return sorted(found, key=lambda p: distance_m(point, (p.lat, p.lng)))


def read_register(text: str) -> Register:
    """The register from its CSV text."""
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
                street=street_key(row.get("street") or ""),
                number=_number(row.get("streetnum") or ""),
                entrance=_number(row.get("entrance") or ""),
                lat=lat,
                lng=lng,
                label=_label(row),
            )
        )
    return Register(points)


def read_register_zip(data: bytes) -> Register:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        return read_register(archive.read(name).decode("utf-8-sig"))


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Metres between two nearby points (equirectangular; fine at city scale)."""
    lat = math.radians((a[0] + b[0]) / 2)
    return math.hypot((a[0] - b[0]) * 111_320, (a[1] - b[1]) * 111_320 * math.cos(lat))


def _town(settlement: str) -> str:
    """Sofia's кв. settlements are Sofia."""
    return SOFIA if settlement.startswith(SOFIA) else settlement


def _in_settlement(row: RegisterPoint, settlement: Optional[str]) -> bool:
    if settlement is None:  # Sofia, its кв. included ("гр. софия, кв. драгалевци")
        return row.settlement == SOFIA or row.settlement.startswith(SOFIA + ",")
    return row.settlement == settlement


def _together(rows: list[RegisterPoint], spread: float) -> Optional[tuple[float, float]]:
    """The rows' centre when every row is within ``spread`` of it, else None."""
    centre = (sum(r.lat for r in rows) / len(rows), sum(r.lng for r in rows) / len(rows))
    if all(distance_m(centre, (r.lat, r.lng)) <= spread for r in rows):
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
    return Match("matched", point, tuple(rows), region_checked=bool(region) and all(r.region == region for r in rows))


def match(parsed: Parsed, register: Register) -> Match:
    """The register point for our address, or why there is none."""
    if parsed.near:
        return Match("no_address")
    result = Match("no_address")
    if parsed.block:
        found = [
            r for r in register.by_block.get(parsed.block, [])
            if _in_settlement(r, parsed.settlement)
            and (
                (parsed.complex and r.complex and _same_name(parsed.complex, r.complex))
                or (parsed.street and r.street and _same_name(parsed.street, r.street))
            )
        ]
        if parsed.entrance:
            found = [r for r in found if r.entrance == parsed.entrance] or found
        result = _settle(found, parsed.region, BLOCK_SPREAD_M)
        if result.outcome == "matched":
            return result
    if parsed.street and parsed.number:
        # Also for "ул. X № 10, бл. 2" whose block the register files elsewhere.
        found = [
            r for r in register.by_number.get(parsed.number, [])
            if _in_settlement(r, parsed.settlement) and r.street and _same_name(parsed.street, r.street)
        ]
        if any((_town(r.settlement), r.street) in register.namesakes for r in found):
            # Two streets of this name: only our district says which, and it must be named.
            found = [r for r in found if parsed.region and r.region == parsed.region]
            street = _settle(found, parsed.region, STREET_SPREAD_M) if found else Match("ambiguous")
        else:
            street = _settle(found, parsed.region, STREET_SPREAD_M)
        if street.outcome == "matched" or result.outcome == "no_address":
            return street
    return result


def _latin(text: str) -> str:
    from app.utils.transliteration import transliterate_bulgarian

    return transliterate_bulgarian(text).casefold()


@dataclass(frozen=True)
class PageText:
    """A page's text in our spelling and in Latin letters, prepared once per site."""

    cyrillic: str
    latin: str


def page_text(text: str) -> PageText:
    # Latin text keeps its own letters (``_clean`` turns look-alikes into Cyrillic).
    latin = re.sub(r"\s+", " ", _QUOTES_RE.sub(" ", text))
    return PageText(_clean(text), _latin(latin))


_KIND_BEFORE_RE = re.compile(r"(?:^|\W)(ул|улица|бул|булевард|ul|ulitsa|bul|blvd|boulevard)\W{0,3}$")
_KIND_OF = {"улица": "ул", "ul": "ул", "ulitsa": "ул", "bul": "бул", "булевард": "бул", "blvd": "бул", "boulevard": "бул"}


def names_address(page: PageText, parsed: Parsed) -> bool:
    """Whether a page states our address: the end of our street's name (its last two words,
    initials left out) right next to our exact house number, either way round, in Bulgarian
    or in Latin letters ("ул. Ралевица № 80А", "80A Ralevitsa Street"); for a block, the
    complex with its number next to the block number ("Младост 4, бл. 460А"). A street of
    another kind named just before ("бул. Витоша" for our "ул. Витоша") does not count."""
    kind = None
    if parsed.street and parsed.number:
        kind, *name = parsed.street.split()
        name = [w for w in name if len(w) > 1][-2:]
        number, marker = parsed.number, r"(?:(?:№|no\.?|#)\s*)?"
    elif parsed.block and parsed.complex:
        name, number, marker = parsed.complex.split(), parsed.block, r"(?:(?:бл|bl)\.?\s*)?"
    else:
        return False
    if not name or not any(len(w) >= 3 and not w.isascii() for w in name):
        return False  # a name of initials, digits or a Roman numeral alone says too little
    for text, convert in ((page.cyrillic, lambda w: w), (page.latin, _latin)):
        words = r"\W+".join(re.escape(convert(w)) for w in name)
        split = re.fullmatch(r"(\d+)(\D*)", number.casefold())
        if split is None:
            return False
        digits, letter = split.groups()
        num = re.escape(digits) + (r"\s?" + re.escape(convert(letter)) if letter else "")
        patterns = (
            rf"(?<!\w){words}(?!\w)\W{{1,6}}{marker}{num}(?![\w/-])",
            rf"(?<![\w/-]){num}\s*,?\s*(?:(?:ул|бул|ul|str)\.?\s*)?{words}(?!\w)",
        )
        for pattern in patterns:
            for found in re.finditer(pattern, text):
                said = _KIND_BEFORE_RE.search(text[max(0, found.start() - 12): found.start()])
                if kind and said and _KIND_OF.get(said.group(1), said.group(1)) != kind:
                    continue
                return True
    return False
