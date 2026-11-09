"""Outdoor-grounds stage: the OpenStreetMap school plot round each pinned building.

For every precisely pinned location it finds the school or kindergarten plot drawn round
the pin and stores, under ``attributes["outdoor_grounds"]["locations"][<location id>]``:

* ``own``: the plot is this school's (its MoE code ``ref:BG:neispuo``, or, on a plot with
  no code, its name), with the open ground (plot less the buildings on it) and the pitches
  and playgrounds mapped inside;
* ``shared_with_i18n``: the plot carries another school's code; we name that school from
  our own data, never from OSM's text.

A plot with no code and another name, or no plot, stores nothing: OSM rarely draws a
private kindergarten's garden, so a missing plot is not "no yard". The pin the plot was
found for is stored too; ``app.utils.outdoor_space`` ignores the plot once the pin moves.

Map data © OpenStreetMap contributors, ODbL 1.0: the stored answers are a derived
database, served openly by the API with the credit beside them (docs/OUTDOOR_SPACE.md).
No model calls; one Overpass query.
"""

from __future__ import annotations

import datetime
import logging
import math
import re
from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.stages.osm_check import name_numbers, neispuo_codes
from app.scrapers.stages.pin_agent import overpass_post
from app.scrapers.stages.website_pins import needs_pin
from app.services.geocoding.bounds import get_city_bounds
from app.utils.i18n_resolver import resolve_name_i18n
from app.utils.outdoor_space import ATTRIBUTE_KEY
from app.utils.school_facts import publication_attributes
from app.utils.transliteration import transliterate_bulgarian

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# A pin on the street edge or at the gate is still this plot's.
NEAR_M = 25
# Sofia has about 600 school plots mapped; far fewer means a cut answer, which must not
# wipe the stored plots.
MIN_PLOTS = 100
# Own grounds smaller than this (after the buildings) are not claimed.
MIN_OPEN_M2 = 100
_PLAY = ("pitch", "playground", "track")

# Words every school name has: they say nothing about which school a plot is.
_GENERIC = {
    "частно",
    "частна",
    "частен",
    "средно",
    "основно",
    "начално",
    "обединено",
    "училище",
    "гимназия",
    "профилирана",
    "професионална",
    "езикова",
    "езиково",
    "детска",
    "детски",
    "градина",
    "ясла",
    "комплекс",
    "център",
    "school",
    "international",
    "private",
    "kindergarten",
    "college",
    "sofia",
    "софия",
    "chastno",
    "chastna",
    "sredno",
    "osnovno",
    "nachalno",
    "uchilishte",
    "gimnaziya",
    "detska",
    "chsu",
    "chou",
    "chnu",
    "chdg",
    "chpg",
    "cheg",
    "chesu",
    "chezg",
    "gradina",
    "park",
    "парк",
    "skuul",
    "скуул",
    "language",
    "academy",
    "академия",
}


def _xy(lat: float, lng: float, lat0: float) -> tuple[float, float]:
    """Metres on a local flat projection (fine across one plot)."""
    return lng * 111_320 * math.cos(math.radians(lat0)), lat * 110_540


def _area(ring: Sequence[tuple[float, float]]) -> float:
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))) / 2


def _inside(point: tuple[float, float], ring: Sequence[tuple[float, float]]) -> bool:
    x, y = point
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _edge_distance(point: tuple[float, float], ring: Sequence[tuple[float, float]]) -> float:
    px, py = point
    best = math.inf
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        dx, dy = x2 - x1, y2 - y1
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
        best = min(best, math.hypot(px - x1 - t * dx, py - y1 - t * dy))
    return best


def _join(pieces: list[list[tuple[float, float]]]) -> list[list[tuple[float, float]]]:
    """Closed rings from a relation's outer ways, which OSM may split into pieces."""
    rings, open_ = [], [p for p in pieces if len(p) > 1]
    while open_:
        ring = list(open_.pop())
        while ring[0] != ring[-1]:
            for i, piece in enumerate(open_):
                if piece[0] == ring[-1]:
                    ring += piece[1:]
                elif piece[-1] == ring[-1]:
                    ring += piece[-2::-1]
                else:
                    continue
                open_.pop(i)
                break
            else:
                break  # an unclosed ring: left out
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring[:-1])
    return rings


def _rings(element: dict, inner: bool = False) -> list[list[tuple[float, float]]]:
    """The element's outer (or a relation's inner) rings as (lat, lng) points without the
    repeated last point."""
    if element.get("type") == "way":
        points = [(g["lat"], g["lon"]) for g in element.get("geometry") or []]
        return [points[:-1]] if not inner and len(points) >= 4 and points[0] == points[-1] else []
    roles = ("inner",) if inner else ("outer", "")
    pieces = [
        [(g["lat"], g["lon"]) for g in member.get("geometry") or []]
        for member in element.get("members") or []
        if member.get("type") == "way" and member.get("role") in roles
    ]
    return _join(pieces)


def _name_tokens(*names: Optional[str]) -> set[str]:
    """Distinctive words of the names: not generic words, and not a capitalised Cyrillic
    abbreviation ("ГИЧЕ", "НПГПТО"), which our own names spell out."""
    tokens = set()
    for name in names:
        words = re.findall(r"\w+", str(name or ""))
        if any(w != w.upper() for w in words):  # a name written all in capitals keeps its words
            words = [w for w in words if not re.fullmatch(r"[А-Я]{2,}", w)]
        latin = transliterate_bulgarian(" ".join(words)).casefold()
        for word in re.findall(r"[a-z0-9]{4,}", latin):
            if word not in _GENERIC and transliterate_bulgarian(word).casefold() not in _GENERIC:
                tokens.add(word)
    return tokens


class Plot:
    """A school plot: outer rings less a multipolygon's holes (another plot, a block of flats)."""

    def __init__(self, element: dict, rings: list[list[tuple[float, float]]]):
        tags = element.get("tags") or {}
        names = [tags.get("name"), tags.get("official_name"), tags.get("name:en")]
        self.id = f"{element['type']}/{element['id']}"
        self.codes = neispuo_codes(tags)
        self.tokens = _name_tokens(*names)
        self.numbers = set().union(*(name_numbers(n) for n in names if n))
        self.lat0 = rings[0][0][0]
        self.rings = [[_xy(lat, lng, self.lat0) for lat, lng in ring] for ring in rings]
        self.holes = [[_xy(lat, lng, self.lat0) for lat, lng in ring] for ring in _rings(element, inner=True)]
        self.area = max(sum(_area(ring) for ring in self.rings) - sum(_area(ring) for ring in self.holes), 0.0)

    def contains(self, xy: tuple[float, float]) -> bool:
        return any(_inside(xy, ring) for ring in self.rings) and not any(_inside(xy, hole) for hole in self.holes)

    def distance(self, lat: float, lng: float) -> float:
        xy = _xy(lat, lng, self.lat0)
        return 0.0 if self.contains(xy) else min(_edge_distance(xy, ring) for ring in self.rings + self.holes)

    def named_for(self, tokens: set[str], numbers: set[str], known_codes: frozenset = frozenset()) -> bool:
        """A plot is a school's by name when every distinctive word of its name is in the
        school's name and no school number differs ("Христо Ботев" is not "Христо
        Смирненски", "ДГ №79" is not "ДГ №97"). Only for a plot with no MoE code, or with
        codes none of our schools carry: municipal kindergartens have no code with us."""
        if self.codes & known_codes or not self.tokens or not self.tokens <= tokens:
            return False
        return not (self.numbers and numbers and not self.numbers & numbers)


def read_plots(elements: Sequence[dict]) -> tuple[list[Plot], list[dict], list[dict]]:
    """Split the Overpass answer into school plots, buildings and play features."""
    plots, buildings, play, seen = [], [], [], set()
    for element in elements:
        # A school outline that is also a building comes back from both of the query's
        # building and plot parts: counted once.
        key = (element.get("type"), element.get("id"))
        if key in seen:
            continue
        seen.add(key)
        tags = element.get("tags") or {}
        # A school mapped as a building outline is a building, not grounds round one.
        if tags.get("amenity") in ("school", "kindergarten") and "building" not in tags and (rings := _rings(element)):
            plots.append(Plot(element, rings))
        elif tags.get("leisure") in _PLAY:
            play.append(element)
        elif "building" in tags and (rings := _rings(element)):
            buildings.append({"rings": rings})
    return plots, buildings, play


def plot_details(plot: Plot, buildings: Sequence[dict], play: Sequence[dict]) -> dict[str, Any]:
    """Open ground (plot less the buildings whose middle is on it), pitches and playgrounds."""
    built = 0.0
    for building in buildings:
        for ring in building["rings"]:
            middle = (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))
            if plot.contains(_xy(*middle, plot.lat0)):
                built += _area([_xy(lat, lng, plot.lat0) for lat, lng in ring])
    counts = {"pitch": 0, "playground": 0}
    for feature in play:
        centre = feature.get("center") or feature
        if centre.get("lat") is None or centre.get("lon") is None:
            continue
        kind = (feature.get("tags") or {}).get("leisure")
        if kind == "track":
            kind = "pitch"
        if plot.contains(_xy(centre["lat"], centre["lon"], plot.lat0)):
            counts[kind] += 1
    open_m2 = max(plot.area - built, 0)
    return {
        "open_m2": int(round(open_m2, -2)),  # to the nearest 100 m²: OSM outlines are rough
        "pitches": counts["pitch"],
        "playgrounds": counts["playground"],
    }


def match_location(
    lat: float,
    lng: float,
    code: str,
    name_tokens: set[str],
    plots: Sequence[Plot],
    numbers: frozenset = frozenset(),
    known_codes: frozenset = frozenset(),
) -> tuple[Optional[Plot], bool]:
    """The plot answering for a pin, and whether it is the school's own.

    A plot with the school's code within ``NEAR_M`` is its own, and so is one named for it
    whose codes (if any) are not among ``known_codes``, those of our schools; failing
    that, the smallest plot the pin is inside, which is another school's when it carries
    another code. A plot with no code and another name answers nothing."""
    near = [(plot, plot.distance(lat, lng)) for plot in plots]
    near = [(plot, d) for plot, d in near if d <= NEAR_M]
    own = [
        (plot, d)
        for plot, d in near
        if (code and code in plot.codes) or plot.named_for(name_tokens, set(numbers), known_codes)
    ]
    if own:
        return min(own, key=lambda pd: (pd[1], pd[0].area))[0], True
    inside = [plot for plot, d in near if d == 0 and plot.codes]
    if inside:
        return min(inside, key=lambda plot: plot.area), False
    return None, False


async def read_osm(country: str = "bg", city: str = "sofia") -> list[dict]:
    """School plots with outlines, the buildings on them and their pitches and playgrounds."""
    import httpx

    from app.config import CRAWLER_USER_AGENT

    b = get_city_bounds(country, city)
    if b is None:
        raise ValueError(f"no bounds for {country}/{city}")
    box = f"({b['south']},{b['west']},{b['north']},{b['east']})"
    query = (
        "[out:json][timeout:300];"
        f'(way[amenity~"^(school|kindergarten)$"]{box};'
        f'relation[amenity~"^(school|kindergarten)$"][type=multipolygon]{box};)->.plots;'
        ".plots out geom;"  # body verbosity: "tags" would leave out a relation's members
        ".plots map_to_area->.pa;"
        "way(area.pa)[building];out geom tags;"
        f'nwr(area.pa)[leisure~"^({"|".join(_PLAY)})$"];out center tags;'
    )
    async with httpx.AsyncClient(headers={"User-Agent": CRAWLER_USER_AGENT}) as client:
        return await overpass_post(client, query, timeout=330)


def _public_name(school: School) -> dict[str, str]:
    """The name the school's own page shows (its website's Latin name included)."""
    attrs = publication_attributes(
        school.attributes, school.scrape_status, school.website_url, school.education_level, school.admission_info
    )
    return dict(resolve_name_i18n(school.name_i18n, attrs))


async def run_outdoor_grounds(
    db: AsyncSession,
    elements: Sequence[dict],
    *,
    country: str = "bg",
    city: str = "sofia",
    apply: bool = False,
) -> dict[str, Any]:
    """Find each pinned location's plot; with ``apply``, store them (replacing the last run's)."""
    plots, buildings, play = read_plots(elements)
    if len(plots) < MIN_PLOTS:
        raise RuntimeError(f"only {len(plots)} school plots in the OSM answer: not storing a cut answer")
    schools = (
        (
            await db.execute(
                select(School)
                .where(School.country_code == country, func.lower(School.city) == city.lower())
                .order_by(School.id)
            )
        )
        .scalars()
        .all()
    )
    by_code = {code: school for school in schools if (code := (school.institutional_id or "").strip().lstrip("0"))}
    known_codes = frozenset(by_code)
    names = {school.id: resolve_name_i18n(school.name_i18n, school.attributes) for school in schools}
    rows = (
        await db.execute(select(SchoolLocation).where(SchoolLocation.school_id.in_([s.id for s in schools])))
    ).scalars()
    by_school: dict[int, list[SchoolLocation]] = {}
    for location in rows:
        by_school.setdefault(location.school_id, []).append(location)

    results, counts, changed = [], {"own": 0, "shared": 0, "none": 0, "no_pin": 0}, 0
    for school in schools:
        code = (school.institutional_id or "").strip().lstrip("0")
        tokens = _name_tokens(*names[school.id].values())
        numbers = frozenset().union(*(name_numbers(n) for n in names[school.id].values()))
        found: dict[str, dict[str, Any]] = {}
        for location in sorted(by_school.get(school.id, []), key=lambda loc: loc.id):
            if needs_pin(location):
                counts["no_pin"] += 1
                continue
            plot, own = match_location(location.lat, location.lng, code, tokens, plots, numbers, known_codes)
            entry: dict[str, Any] = {"lat": location.lat, "lng": location.lng}
            details = plot_details(plot, buildings, play) if plot is not None and own else {}
            if plot is not None and own and details["open_m2"] < MIN_OPEN_M2:
                plot = None  # buildings cover the plot: no ground to speak of
            elif plot is not None and own:
                entry |= {"osm": plot.id, "own": True, **details}
            elif plot is not None:
                others = [by_code[c] for c in sorted(plot.codes) if c in by_code and by_code[c].id != school.id]
                if not others:
                    plot = None
                else:
                    other = others[0]
                    entry |= {
                        "osm": plot.id,
                        "own": False,
                        "shared_with_id": other.id,
                        "shared_with_i18n": _public_name(other),
                    }
            if plot is None:
                counts["none"] += 1
                continue
            counts["own" if entry["own"] else "shared"] += 1
            found[str(location.id)] = entry
        stored = (school.attributes or {}).get(ATTRIBUTE_KEY) or {}
        differs = (stored.get("locations") or {}) != found
        changed += differs
        results.append({"school_id": school.id, "locations": found})
        if apply and differs:
            await db.refresh(school, attribute_names=["attributes"], with_for_update=True)
            attrs = dict(school.attributes or {})
            if found:
                attrs[ATTRIBUTE_KEY] = {
                    "_schema_version": SCHEMA_VERSION,
                    "read_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "locations": found,
                }
            else:
                attrs.pop(ATTRIBUTE_KEY, None)
            school.attributes = attrs
    if apply:
        await db.commit()
    return {
        "apply": apply,
        "schools": len(schools),
        "plots": len(plots),
        "locations": counts,
        "changed": changed,  # schools whose stored plots this run changes (or would)
        "results": [r for r in results if r["locations"]],
    }
