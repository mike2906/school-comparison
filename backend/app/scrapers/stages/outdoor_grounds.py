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

import asyncio
import datetime
import logging
import math
import re
from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.stages.osm_check import neispuo_codes
from app.scrapers.stages.pin_agent import OVERPASS_URLS
from app.scrapers.stages.website_pins import needs_pin
from app.services.geocoding.bounds import get_city_bounds
from app.utils.i18n_resolver import resolve_name_i18n
from app.utils.outdoor_space import ATTRIBUTE_KEY
from app.utils.transliteration import transliterate_bulgarian

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
# A pin on the street edge or at the gate is still this plot's.
NEAR_M = 25
# Sofia has about 600 school plots mapped; far fewer means a cut answer, which must not
# wipe the stored plots.
MIN_PLOTS = 100
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


def _rings(element: dict) -> list[list[tuple[float, float]]]:
    """The element's outline(s) as (lat, lng) rings without the repeated last point."""
    if element.get("type") == "way":
        points = [(g["lat"], g["lon"]) for g in element.get("geometry") or []]
        return [points[:-1]] if len(points) >= 4 and points[0] == points[-1] else []
    pieces = [
        [(g["lat"], g["lon"]) for g in member.get("geometry") or []]
        for member in element.get("members") or []
        if member.get("type") == "way" and member.get("role") in ("outer", "")
    ]
    return _join(pieces)


def _name_tokens(*names: Optional[str]) -> set[str]:
    tokens = set()
    for name in names:
        latin = transliterate_bulgarian(str(name or "")).casefold()
        for word in re.findall(r"[a-z0-9]{4,}", latin):
            if word not in _GENERIC and transliterate_bulgarian(word).casefold() not in _GENERIC:
                tokens.add(word)
    return tokens


class Plot:
    def __init__(self, element: dict, rings: list[list[tuple[float, float]]]):
        tags = element.get("tags") or {}
        self.id = f"{element['type']}/{element['id']}"
        self.codes = neispuo_codes(tags)
        self.tokens = _name_tokens(tags.get("name"), tags.get("official_name"), tags.get("name:en"))
        self.lat0 = rings[0][0][0]
        self.rings = [[_xy(lat, lng, self.lat0) for lat, lng in ring] for ring in rings]
        self.area = sum(_area(ring) for ring in self.rings)

    def contains(self, xy: tuple[float, float]) -> bool:
        return any(_inside(xy, ring) for ring in self.rings)

    def distance(self, lat: float, lng: float) -> float:
        xy = _xy(lat, lng, self.lat0)
        return 0.0 if self.contains(xy) else min(_edge_distance(xy, ring) for ring in self.rings)


def read_plots(elements: Sequence[dict]) -> tuple[list[Plot], list[dict], list[dict]]:
    """Split the Overpass answer into school plots, buildings and play features."""
    plots, buildings, play = [], [], []
    for element in elements:
        tags = element.get("tags") or {}
        if tags.get("amenity") in ("school", "kindergarten") and (rings := _rings(element)):
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
    lat: float, lng: float, code: str, name_tokens: set[str], plots: Sequence[Plot]
) -> tuple[Optional[Plot], bool]:
    """The plot answering for a pin, and whether it is the school's own.

    A plot with the school's code or (no code) name within ``NEAR_M`` is its own; failing
    that, the smallest plot the pin is inside, which is another school's when it carries
    another code. A plot with no code and another name answers nothing."""
    near = [(plot, plot.distance(lat, lng)) for plot in plots]
    near = [(plot, d) for plot, d in near if d <= NEAR_M]
    own = [
        (plot, d) for plot, d in near if (code and code in plot.codes) or (not plot.codes and name_tokens & plot.tokens)
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
        ".plots out geom tags;"
        ".plots map_to_area->.pa;"
        "way(area.pa)[building];out geom tags;"
        f'nwr(area.pa)[leisure~"^({"|".join(_PLAY)})$"];out center tags;'
    )
    error: Optional[Exception] = None
    async with httpx.AsyncClient(headers={"User-Agent": CRAWLER_USER_AGENT}) as client:
        for attempt, url in enumerate([*OVERPASS_URLS, *OVERPASS_URLS]):  # each server twice
            if attempt:
                await asyncio.sleep(5 * attempt)
            try:
                response = await client.post(url, data={"data": query}, timeout=330)
                response.raise_for_status()
                body = response.json()
                if "runtime error" in str(body.get("remark") or ""):  # a timed-out, partial answer
                    raise ValueError(f"partial answer: {body['remark'][:120]}")
                return body.get("elements", [])
            except (httpx.HTTPError, ValueError) as exc:  # busy (429/504), down, or not JSON
                error = exc
    raise RuntimeError(f"no Overpass server answered: {error}")


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
    if apply and len(plots) < MIN_PLOTS:
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
        found: dict[str, dict[str, Any]] = {}
        for location in sorted(by_school.get(school.id, []), key=lambda loc: loc.id):
            if needs_pin(location):
                counts["no_pin"] += 1
                continue
            plot, own = match_location(location.lat, location.lng, code, tokens, plots)
            entry: dict[str, Any] = {"lat": location.lat, "lng": location.lng}
            if plot is not None and own:
                entry |= {"osm": plot.id, "own": True, **plot_details(plot, buildings, play)}
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
                        "shared_with_i18n": dict(names[other.id]),
                    }
            if plot is None:
                counts["none"] += 1
                continue
            counts["own" if entry["own"] else "shared"] += 1
            found[str(location.id)] = entry
        stored = (school.attributes or {}).get(ATTRIBUTE_KEY) or {}
        if (stored.get("locations") or {}) != found:
            changed += 1
        results.append({"school_id": school.id, "locations": found})
        if apply:
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
