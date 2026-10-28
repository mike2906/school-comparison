"""Outdoor space per school building: what a parent can be told, and from which source.

Schools write about the yard they have, never that they have none, and a building on the
map with no school grounds drawn round it can still have a play area (Maple Bear in the
Kambanite office block, seen on a visit 2026-10-26). So the answers only ever add what a
source shows; "no outdoor space" comes from a visit alone, and everything else is "not
known". Sources, strongest first (docs/OUTDOOR_SPACE.md):

1. a visit (``outdoor_visits.json``);
2. the school's own website, through its shown facts, when the school has one building;
3. OpenStreetMap: the school's own plot round the pin, or another school's
   (``attributes["outdoor_grounds"]``, written by the ``outdoor-grounds`` stage);
4. the register address: on a floor of a larger building, or in another school's building.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

ATTRIBUTE_KEY = "outdoor_grounds"
# A stored plot answers for a pin only while the pin stays where the stage saw it.
MOVED_PIN_M = 30

# A fact about the school's own outdoor space: a yard, garden, outdoor playground or pitch.
# Sports and clubs alone ("football", "athletics") are not, nor are indoor halls. A court,
# "игрище" or "площадка" can be in a gym ("Физкултурен салон с баскетболно игрище"), so
# those count only in a fact that names no indoor hall.
_OUTDOOR_FACT_RE = re.compile(
    r"\bдвор(?!ец|ц)|стадион|градин(?:а|ата|и) за игр|зелен[аи] площ|открит\w* (?:басейн|площ|игр|корт)|"
    r"\bcourtyard|\byards?\b|\bschoolyard|sports? grounds?|outdoor|open-air|\bgardens?\b|green (?:area|space)s?|"
    r"running track|football (?:pitch|field)s?|футболно игрище",
    re.IGNORECASE,
)
_PLAY_AREA_RE = re.compile(r"площадк|игрищ|playground|\bpitch|\bcourts?\b|корт", re.IGNORECASE)
_INDOOR_RE = re.compile(r"\bindoor|\bhalls?\b|\bgym|салон|зала|закрит|вътрешн", re.IGNORECASE)
# Not outdoor space: "детска градина" is a kindergarten.
_NOT_OUTDOOR_RE = re.compile(r"детск\w* градин|kindergarten", re.IGNORECASE)

# The register address puts the school in another school's building ("в сградата на 47 СУ")...
_OTHER_BUILDING_RE = re.compile(r"сград(?:а|ата) на ", re.IGNORECASE)
# ...or on a floor of a larger one: a floor, flat, ground floor or block entrance.
# "бл." alone is not enough: many schools with their own yard give the nearest block.
_FLOOR_RE = re.compile(r"\bет\.|\bетаж|\bпартер|\bап\.|\bвх\.", re.IGNORECASE)


@lru_cache(maxsize=1)
def _visits() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("outdoor_visits.json")
    return json.loads(path.read_text(encoding="utf-8"))["visits"]


def _address_bg(location: Any) -> str:
    address = getattr(location, "address_i18n", None)
    return str((address or {}).get("bg") or "") if isinstance(address, Mapping) else ""


def outdoor_facts(facts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The shown website facts that describe the school's own outdoor space."""
    found = []
    for fact in facts or []:
        # "minor" facts are not shown on the page, so they do not show here either.
        if fact.get("topic") != "facilities" or fact.get("rank") == "minor":
            continue
        texts = fact.get("text_i18n") or {}
        words = _NOT_OUTDOOR_RE.sub(" ", " ".join(str(texts.get(lang) or "") for lang in ("bg", "en")))
        if _OUTDOOR_FACT_RE.search(words) or (_PLAY_AREA_RE.search(words) and not _INDOOR_RE.search(words)):
            found.append({"text_i18n": dict(texts), "source_url": fact.get("source_url")})
    return found


def address_answer(address_bg: str) -> Optional[str]:
    """``other_building`` or ``floor`` from the register address, or None."""
    if _OTHER_BUILDING_RE.search(address_bg):
        return "other_building"
    if _FLOOR_RE.search(address_bg):
        return "floor"
    return None


def _visit(school_id: int, address_bg: str) -> Optional[dict[str, Any]]:
    for visit in _visits():
        if visit["school_id"] == school_id and visit["address_contains"].casefold() in address_bg.casefold():
            return visit
    return None


def _pin_moved(stored: Mapping[str, Any], location: Any) -> bool:
    from app.services.geocoding.bg.sofia_address_register import distance_m

    lat, lng = getattr(location, "lat", None), getattr(location, "lng", None)
    if lat is None or lng is None or stored.get("lat") is None or stored.get("lng") is None:
        return True
    return distance_m((lat, lng), (stored["lat"], stored["lng"])) > MOVED_PIN_M


def location_answers(
    school_id: int,
    locations: Iterable[Any],
    attributes: Any,
    shown_facts: Optional[Iterable[Mapping[str, Any]]],
) -> dict[str, Any]:
    """``{"locations": [...], "website": [...]}`` for the API.

    Each location gets ``kind``: ``grounds`` (outdoor space at this building), ``shared``
    (in another school's grounds; whether pupils use them is not known), ``other_building``
    or ``floor`` (from the address; yard not known), ``none`` (a visit only) or ``unknown``,
    with its ``source``. The website's outdoor facts answer for the building when the
    school has one; with several, the page rarely says which, so they are listed for the
    school under ``website``.
    """
    locations = list(locations)
    attrs = attributes if isinstance(attributes, Mapping) else {}
    stored = attrs.get(ATTRIBUTE_KEY) if isinstance(attrs.get(ATTRIBUTE_KEY), Mapping) else {}
    plots = stored.get("locations") if isinstance(stored.get("locations"), Mapping) else {}
    facts = outdoor_facts(shown_facts or [])
    one_building = len(locations) == 1
    answers = []
    for location in locations:
        address = _address_bg(location)
        answer: dict[str, Any] = {"location_id": location.id, "kind": "unknown", "source": None}
        plot = plots.get(str(location.id))
        if plot is not None and (not isinstance(plot, Mapping) or _pin_moved(plot, location)):
            plot = None
        if visit := _visit(school_id, address):
            answer.update(
                kind=visit["kind"], source="visit", checked_on=visit["checked_on"], text_i18n=visit["text_i18n"]
            )
        elif one_building and facts:
            answer.update(kind="grounds", source="website", facts=facts)
        elif plot and plot.get("own"):
            answer.update(kind="grounds", source="osm", **_plot_details(plot))
        elif plot and plot.get("shared_with_i18n"):
            answer.update(
                kind="shared",
                source="osm",
                shared_with_id=plot.get("shared_with_id"),
                shared_with_i18n=plot["shared_with_i18n"],
            )
        elif kind := address_answer(address):
            answer.update(kind=kind, source="address")
        # A website or visit answer still shows the mapped plot's size and pitches.
        if answer["source"] in {"website", "visit"} and answer["kind"] == "grounds" and plot and plot.get("own"):
            answer.update(_plot_details(plot))
        answers.append(answer)
    return {"locations": answers, "website": [] if one_building else facts}


def _plot_details(plot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "open_m2": plot.get("open_m2"),
        "pitches": plot.get("pitches") or 0,
        "playgrounds": plot.get("playgrounds") or 0,
    }
