"""Pin-agent stage: a model picks a hidden location's building from points sources gave.

For a location the API publishes without a pin (approximate or none), gathers candidate
points: the school's own website map points (with OpenStreetMap's address there) and
OpenStreetMap schools and kindergartens near the location's point (anywhere in the city
when none near it is named as the school: the point may be a wrong geocode). One model call picks
the candidate that is this school's building, or none. The model never gives coordinates:
it picks a candidate by number, and code checks the pick before anything is written
(``check_pick``). The pick must always be on our street, its house number within
``MAX_NUMBER_GAP`` of ours when both are known; an OSM feature must also be named as this
school (the only one of that name, of the same kind, private or not alike); and no other
school may be pinned on that spot. An address with only a neighbourhood ("ж.к. Люлин 6") takes
an OSM feature named as the school within ``STREETLESS_M`` instead of the street; an OSM pick
the school's own site maps elsewhere is refused (a single-location school). Anything else stays hidden. Runs after ``website-pins`` (which takes exact
address matches without a model). Default is a dry run that writes nothing. Not in ``all``:
``scripts/refresh_pins.sh`` runs both weekly from cron.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Optional, Protocol

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import create_agent, extract_provider_cost_usd, get_model, get_model_costs
from app.config import CRAWLER_USER_AGENT, get_settings
from app.database import async_session_maker
from app.models import School, SchoolLocation
from app.models.pipeline_run import PipelineRun
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.shared_site_check import level_family
from app.scrapers.stages import common as stage_common
from app.scrapers.stages.confirm_pins import ELSEWHERE_M
from app.scrapers.stages.fees_agent import Spend, SpendCapReached
from app.scrapers.stages.social_links import Fetch, _http_fetch
from app.scrapers.stages.website_pins import (
    CITY_NAMES,
    Reverse,
    _address,
    _distance_m,
    _read_points,
    needs_pin,
    nominatim_address,
    same_town,
    write_pin,
)
from app.services.geocoding.bg.address_match import has_area, has_street, house_number, same_building, same_street
from app.services.geocoding.bounds import get_city_bounds, point_in_bounds
from app.services.pipeline_runs import finalize_pipeline_run, pipeline_run_heartbeat, start_pipeline_run
from app.services.provider_costs import ProviderCostError, execute_billable_request, provider_cost_scope
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

STAGE = "pin-agent"
TIER = "pricing"  # cheap and follows instructions well (the fees and facts readers' tier)
DEFAULT_MAX_USD = 0.50
OUTPUT_TOKENS_RESERVE = 400
PIN_TAG = "coords_source=pin_agent"
# Public Overpass servers, tried in turn: the main one often answers 504 when busy.
OVERPASS_URLS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
# How far from the location's street point OSM schools are looked for.
SEARCH_RADIUS_M = 2000
# A building on our street whose number is this close to ours counts as ours (OSM and
# school addresses often differ by a building: НПМГ is "Бигла 56", OSM's building 52).
MAX_NUMBER_GAP = 4
# Another school's published pin this close means the candidate is that school's building.
OTHER_SCHOOL_M = 25
MAX_CANDIDATES = 12
# Two OSM features of one name this close are one site mapped twice, not two branches.
SAME_PLACE_M = 60
# An address with no street (only "ж.к. Люлин 6"): an OSM feature named as this school this
# close to the location's point may be taken.
STREETLESS_M = 300

# Name words shared by many institutions, which say nothing about which one it is.
_GENERIC_NAME_WORDS = {
    "училище", "средно", "основно", "обединено", "начално", "частно", "частна",
    "профилирана", "професионална", "гимназия", "детска", "градина", "ясла", "яслена",
    "школа", "школо", "център", "софия", "столична", "national", "school",
    "kindergarten", "international", "private", "the", "and", "for", "на", "по", "за", "и",
    "чоу", "чсу", "чдг", "дг", "оу", "су", "ну", "пг", "спг", "пмг", "чпг", "чну", "обу",
    "еоод", "оод", "ад", "език", "езиков", "езици",
}
# What kind of institution a name is: abbreviations ("СУ", "СПГЕ", "ЧДГ") and words.
# Two letters at least: a lone "Г." or "У" is a person's initial.
_TYPE_ABBREVIATION_RE = re.compile(r"(?<![А-Яа-яA-Za-z])(?:[А-Я]{1,4}(?:У|Г|ГЕ)|[А-Я]{0,3}(?:ДГ|ДЗ|ДЯ))(?![А-Яа-я])")
_TYPE_WORD_RE = re.compile(r"училищ|гимназ|градин|ясла|колеж|лицей|school|kindergarten|college", re.IGNORECASE)
# A private institution's name: "ЧСУ", "ЧДГ", "Частна ...", "Private ...".
_PRIVATE_ABBREVIATION_RE = re.compile(r"(?<![А-Яа-яA-Za-z])Ч[А-Я]{0,3}(?:У|Г|ГЕ|ДГ|ДЗ|ДЯ)(?![А-Яа-я])")
# Ordinal names are numbers ("Първа английска гимназия" is not "Втора ...").
_ORDINALS = {
    "първа": "1", "първо": "1", "втора": "2", "второ": "2", "трета": "3", "трето": "3",
    "четвърта": "4", "четвърто": "4", "пета": "5", "пето": "5", "шеста": "6", "шесто": "6",
    "седма": "7", "седмо": "7", "осма": "8", "осмо": "8", "девета": "9", "девето": "9",
    "десета": "10", "десето": "10",
}
_PRIVATE_WORD_RE = re.compile(r"частн|private", re.IGNORECASE)


def private_status_differs(school_name: str, ours_private: bool, osm_name: Optional[str]) -> bool:
    """Whether the OSM name shows another private-or-state status than ours.

    "ЧСУ"/"частно" says private and a plain abbreviation ("СУ", "ОДЗ") says state, against
    the school's known status. A type word alone ("гимназия", "колеж") says state only
    against a name of ours that says private: "Немска езикова гимназия" is not "Частна
    немска езикова гимназия", while American College, private without "частно" in its
    name, may match itself. A bare name ("ЕСПА") says nothing."""
    if is_private_name(osm_name):
        return not ours_private
    if _TYPE_ABBREVIATION_RE.search(osm_name or ""):
        return ours_private
    if _TYPE_WORD_RE.search(osm_name or ""):
        return is_private_name(school_name)
    return False


def is_private_name(name: Optional[str]) -> bool:
    return bool(_PRIVATE_ABBREVIATION_RE.search(name or "") or _PRIVATE_WORD_RE.search(name or ""))

class Lookup(Protocol):
    """OSM elements within ``radius`` metres of a point (None: the whole city)."""

    def __call__(self, lat: float, lng: float, radius: Optional[float] = ...) -> Awaitable[list[dict]]: ...
Caller = Callable[[str, int], Awaitable["Pick"]]


@dataclass
class Candidate:
    point: tuple[float, float]
    source: str  # "website_map" | "osm_school"
    name: Optional[str] = None  # OSM feature name
    address: Optional[str] = None  # OSM's street and number there
    town_ok: bool = True
    amenity: Optional[str] = None  # OSM's school | kindergarten | college


class Pick(BaseModel):
    # Required fields: with defaults the cheap model calls the output tool empty.
    candidate: int = Field(description="The number of the candidate that is this school's building, or 0 for none")
    sure: bool = Field(description="True only when the evidence clearly shows it is this school's building")
    reason: str = Field(description="One short sentence: the evidence for the pick, or why none fits")


PROMPT = """You place a school or kindergarten on a map. You get the institution's name and
address and a numbered list of candidate points from sources: map points on the
institution's own website (with OpenStreetMap's street address at that point) and
schools/kindergartens mapped in OpenStreetMap near the address, or named as the
institution elsewhere in the city when our point may be wrong (with their names).

Pick the candidate that is this institution's own building. Good evidence: an OSM name
that is this institution (same name, or same number and type); a website map point on the
address's street, at or next to the house number. Not evidence: being near, or a similar
but different institution (another number, another kindergarten of a chain, a state
school a private one shares a name with). A website can show several branches: pick only
the one at this address. If no candidate clearly fits, answer 0. Set sure=false unless
the evidence is clear. Never invent a candidate."""


def _words(text: str) -> set[str]:
    """Distinctive name words, cut to five letters so word forms agree ("немски", "немско")."""
    words = [_ORDINALS.get(w, w) for w in re.findall(r"[а-яa-z]+|\d+", (text or "").casefold().replace("ё", "е"))]
    return {w if w.isdigit() else w[:5] for w in words if (w.isdigit() or len(w) >= 3) and w not in _GENERIC_NAME_WORDS}


def name_matches(school_name: str, osm_name: Optional[str], *, at_our_number: bool = False) -> bool:
    """Whether an OSM name is this institution: the same number when the school name has
    one, and otherwise two shared distinctive name words (one when ours has only one, or
    when OSM puts the feature at our exact street and number)."""
    ours, theirs = _words(school_name), _words(osm_name or "")
    numbers = {w for w in ours if w.isdigit()}
    if numbers:
        others = ours - numbers
        return bool(numbers & theirs) and (not others or bool(others & theirs))
    if theirs and theirs <= _acronyms(school_name):  # OSM names it only by our acronym ("ЕСПА")
        return True
    return bool(ours) and len(ours & theirs) >= (1 if at_our_number else min(2, len(ours)))


def _acronyms(name: str) -> set[str]:
    """Distinctive all-capitals words of three or more letters ("ЕСПА", not "ЧСУ"); none
    in a name written all in capitals, where every word looks like one."""
    if name == name.upper():
        return set()
    return {w.casefold() for w in re.findall(r"(?<![А-Яа-яA-Za-z])[А-ЯA-Z]{3,}(?![А-Яа-яA-Za-z])", name)} - _GENERIC_NAME_WORDS


def _number(text: str) -> Optional[int]:
    found = house_number(text or "")
    match = re.match(r"\d+", found or "")
    return int(match.group()) if match else None


def on_our_street(address: str, candidate_address: Optional[str]) -> bool:
    """The candidate's OSM address is on our street, its number within MAX_NUMBER_GAP of ours."""
    if not candidate_address or not same_street(address, candidate_address):
        return False
    ours, theirs = _number(address), _number(candidate_address)
    return ours is not None and theirs is not None and abs(ours - theirs) <= MAX_NUMBER_GAP


def _numbers_far_apart(address: str, candidate_address: str) -> bool:
    ours, theirs = _number(address), _number(candidate_address)
    return ours is not None and theirs is not None and abs(ours - theirs) > MAX_NUMBER_GAP


def check_pick(
    school_name: str,
    kindergarten: bool,
    address: str,
    candidate: Candidate,
    candidates: Sequence[Candidate],
    bounds: dict[str, float],
    other_pins: Sequence[tuple[tuple[float, float], str]],
    *,
    private: Optional[bool] = None,
    near: Optional[tuple[float, float]] = None,
    site_points: Sequence[tuple[float, float]] = (),
) -> Optional[str]:
    """Why the picked candidate cannot be written, or None when it can.

    ``candidate.address`` is OSM's street (and number) for the point: its tags, else
    Nominatim's nearest building. The street must always be ours. A website point also
    needs a house number near ours. An OSM feature needs no number but must be named as
    this school, the only feature of that name among the candidates (a chain's branches
    share one), of the same kind, and private or not as the school is."""
    if not point_in_bounds(*candidate.point, bounds):
        return "outside the city"
    if not candidate.town_ok:
        return "another town"
    if any(
        _distance_m(candidate.point, pin) < OTHER_SCHOOL_M and not same_building(address, pin_address)
        for pin, pin_address in other_pins
    ):  # schools sharing one building (the same address) may share its pin
        return "another school is pinned there"
    # Our address names no street ("ж.к. Люлин 6"): an OSM feature named as this school, next
    # to the location's point, stands in for the street (the name checks below still apply).
    streetless = (
        candidate.source == "osm_school"
        and not has_street(address)
        and has_area(address)
        and near is not None
        and _distance_m(near, candidate.point) <= STREETLESS_M
    )
    if not streetless:
        if not candidate.address or not same_street(address, candidate.address):
            return "not on our street"
        if _numbers_far_apart(address, candidate.address):
            return "another house number"
    if candidate.source != "osm_school":
        return None if on_our_street(address, candidate.address) else "no house number to compare"
    if (candidate.amenity == "kindergarten") != kindergarten:
        return "OSM maps another kind of institution there"
    # The school's own site wins (Mike, 2026-10-15): not where its map shows none of it.
    if site_points and all(_distance_m(candidate.point, p) > ELSEWHERE_M for p in site_points):
        return "the school's site maps it elsewhere"
    at_our_number = (
        not streetless and _number(address) is not None and _number(address) == _number(candidate.address)
    )
    if not name_matches(school_name, candidate.name, at_our_number=at_our_number):
        return "OSM names another institution there"
    ours_private = is_private_name(school_name) if private is None else private
    if private_status_differs(school_name, ours_private, candidate.name):
        return "one name is a private institution's, the other not"
    if any(
        c is not candidate
        and c.source == "osm_school"
        and (c.amenity == "kindergarten") == kindergarten  # a school's kindergarten is another institution
        # A school and a high school of one name ("ЧОУ" and "ЧПГ Образователни технологии")
        # are two institutions, when the pick is of our kind.
        and not (
            _type_family(c.name) not in (None, _type_family(candidate.name))
            and _type_family(candidate.name) == _type_family(school_name)
        )
        # One site mapped twice (a node and its building): close by, at no other address.
        and not (
            _distance_m(c.point, candidate.point) <= SAME_PLACE_M
            and (not c.address or not candidate.address or same_building(c.address, candidate.address))
        )
        and name_matches(school_name, c.name, at_our_number=True)
        for c in candidates
    ):
        return "several OSM features have this name"
    return None


def _type_family(name: Optional[str]) -> Optional[str]:
    """What kind of institution a name says: "kindergarten", "gymnasium" (ПГ, ПМГ, СПГЕ,
    "гимназия") or "school" (ОУ, СУ, СОУ, "училище"); None when it does not say."""
    match = _TYPE_ABBREVIATION_RE.search(name or "")
    if match:
        abbreviation = match.group()
        if re.search(r"Д[ГЗЯ]$", abbreviation):
            return "kindergarten"
        return "school" if abbreviation.endswith("У") else "gymnasium"
    text = (name or "").casefold()
    # "Училище с детска градина" is a school.
    for family, word in (("gymnasium", "гимназ"), ("school", "училищ"), ("kindergarten", "градин")):
        if word in text:
            return family
    return None


def candidates_message(school_name: str, kind: str, address: str, candidates: Sequence[Candidate]) -> str:
    lines = [f"Institution: {school_name} ({kind})", f"Address: {address}", "", "Candidates:"]
    for number, candidate in enumerate(candidates, start=1):
        if candidate.source == "website_map":
            lines.append(f"{number}. Map point on the institution's website; OSM address there: {candidate.address or 'unknown'}")
        else:
            lines.append(f"{number}. OSM {candidate.name!r}; OSM address: {candidate.address or 'none given'}")
    return "\n".join(lines)


def _osm_address(tags: dict) -> Optional[str]:
    """OSM's street, with the number when tagged; a street without a number still counts."""
    street, number = tags.get("addr:street"), tags.get("addr:housenumber")
    if not street:
        return None
    return nominatim_address({"road": street, "house_number": number}) or f'ул. "{street}"'


class CityOsm:
    """Every named OSM school, kindergarten and college in the city, read once per run.

    One city-wide query instead of one per location: the public Overpass servers often
    answer 504 when busy, and a run of a hundred small queries meets that many times.
    When no server answers, the run goes on with website candidates only (``failed``)."""

    def __init__(self, client: httpx.AsyncClient, bounds: dict[str, float]):
        self.client, self.bounds = client, bounds
        self.elements: Optional[list[dict]] = None
        self.failed: Optional[str] = None
        self._lock = asyncio.Lock()

    async def _read(self) -> list[dict]:
        b = self.bounds
        query = (
            f"[out:json][timeout:180];nwr({b['south']},{b['west']},{b['north']},{b['east']})"
            '[amenity~"^(school|kindergarten|college)$"][name];out center tags;'
        )
        error: Optional[Exception] = None
        for attempt, url in enumerate([*OVERPASS_URLS, *OVERPASS_URLS]):  # each server twice
            if attempt:
                await asyncio.sleep(5 * attempt)
            try:
                response = await self.client.post(url, data={"data": query}, timeout=200)
                response.raise_for_status()
                body = response.json()
                if "runtime error" in str(body.get("remark") or ""):  # a timed-out, partial answer
                    raise ValueError(f"partial answer: {body['remark'][:120]}")
                return body.get("elements", [])
            except (httpx.HTTPError, ValueError) as exc:  # busy (429/504), down, or not JSON
                error = exc
        raise RuntimeError(f"no Overpass server answered: {error}")

    async def __call__(self, lat: float, lng: float, radius: Optional[float] = SEARCH_RADIUS_M) -> list[dict]:
        async with self._lock:
            if self.elements is None and self.failed is None:
                try:
                    self.elements = await self._read()
                except RuntimeError as exc:
                    logger.warning("pin-agent goes on without OSM candidates: %s", exc)
                    self.failed = str(exc)[:200]
                    self.elements = []
        return [
            element
            for element in self.elements
            if (centre := element.get("center") or element).get("lat") is not None
            and centre.get("lon") is not None
            and (radius is None or _distance_m((lat, lng), (float(centre["lat"]), float(centre["lon"]))) <= radius)
        ]


def osm_candidates(elements: Sequence[dict]) -> list[Candidate]:
    found = []
    for element in elements:
        centre = element.get("center") or element
        if centre.get("lat") is None or centre.get("lon") is None:
            continue
        tags = element.get("tags") or {}
        found.append(
            Candidate(
                point=(float(centre["lat"]), float(centre["lon"])),
                source="osm_school",
                name=tags.get("name"),
                address=_osm_address(tags),
                amenity=tags.get("amenity"),
            )
        )
    return found


def model_caller(spend: Spend) -> Caller:
    async def call(message: str, school_id: int) -> Pick:
        in_price, out_price = get_model_costs(TIER)
        estimate = (len(message) / 3 * in_price + OUTPUT_TOKENS_RESERVE * out_price) / 1_000_000
        if not spend.allows(estimate):
            raise SpendCapReached(f"spent ${spend.spent_usd:.4f} of ${spend.cap_usd}")
        agent = create_agent(TIER, PROMPT, Pick, retries=1, model_settings={"temperature": 0})
        with spend.hold(estimate):
            try:
                result = await execute_billable_request(
                    lambda: agent.run(message), model=get_model(TIER), school_id=school_id, stage=STAGE, timeout_seconds=90
                )
            except Exception:
                spend.spent_usd += estimate  # a failed call may still have been charged
                raise
        # No cost reported is not free: count the estimate (retries can cost more than one call).
        spend.spent_usd += extract_provider_cost_usd(result) or estimate
        return result.output

    return call


# More features than this named as the school across the city: a common name, not a clue.
MAX_NAMED_IN_CITY = 3


async def _named_in_city(
    name: str, address: str, point: tuple[float, float], lookup: Lookup, reverse: Reverse, town: str
) -> list[Candidate]:
    """OSM features named as the school anywhere in the city, for a location whose point may
    be a wrong geocode (none near it is named so). Each in our town by Nominatim, which also
    gives its street when OSM does not. check_pick still wants our street and house number."""
    elements = [e for e in await lookup(*point, radius=None) if name_matches(name, (e.get("tags") or {}).get("name"))]
    if len(elements) > MAX_NAMED_IN_CITY:
        return []
    found = []
    for candidate in osm_candidates(elements):
        parts = await reverse(*candidate.point)
        candidate.town_ok = same_town(address, parts, town)
        candidate.address = candidate.address or nominatim_address(parts)
        found.append(candidate)
    return found


async def place_location(
    school: School,
    location: SchoolLocation,
    website_points: Optional[list[tuple[float, float]]],
    *,
    reverse: Reverse,
    lookup: Lookup,
    call: Caller,
    bounds: dict[str, float],
    town: str,
    other_pins: Sequence[tuple[tuple[float, float], str]],
    only_location: bool = True,
) -> dict[str, Any]:
    """The report row for one location; ``point`` is set when the pick passed the checks."""
    address = _address(location)
    name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en") or ""
    candidates: list[Candidate] = []
    for point in website_points or []:
        if not point_in_bounds(*point, bounds):
            continue
        parts = await reverse(*point)
        candidates.append(
            Candidate(point=point, source="website_map", address=nominatim_address(parts), town_ok=same_town(address, parts, town))
        )
    if location.lat is not None and location.lng is not None:
        point = (float(location.lat), float(location.lng))
        found = osm_candidates(await lookup(*point))
        if not any(name_matches(name, c.name, at_our_number=True) for c in found):
            found += await _named_in_city(name, address, point, lookup, reverse, town)
        for candidate in found:
            if all(_distance_m(candidate.point, seen.point) >= 20 for seen in candidates):
                candidates.append(candidate)
    # Most relevant first: our street, then a matching name, then the rest.
    candidates.sort(key=lambda c: (not on_our_street(address, c.address), not name_matches(name, c.name)))
    candidates = candidates[:MAX_CANDIDATES]
    row: dict[str, Any] = {"candidates": [asdict(c) for c in candidates]}
    if not candidates:
        return row | {"outcome": "no_candidates"}
    pick = await call(candidates_message(name, school.education_level or "", address, candidates), school.id)
    row["pick"] = pick.model_dump()
    if not 1 <= pick.candidate <= len(candidates):
        return row | {"outcome": "none_fits"}
    if not pick.sure:
        return row | {"outcome": "not_sure"}
    chosen = candidates[pick.candidate - 1]
    if chosen.address is None or (chosen.source == "osm_school" and _number(chosen.address) is None):
        # OSM tags gave no street (or no number): take Nominatim's nearest building there.
        parts = await reverse(*chosen.point)
        found = nominatim_address(parts)
        if found and (chosen.address is None or same_street(found, chosen.address)):
            chosen.address = found
        chosen.town_ok = chosen.town_ok and same_town(address, parts, town)
        row["candidates"][pick.candidate - 1] = asdict(chosen)
    refused = check_pick(
        name, level_family(school.education_level) == "kindergarten", address, chosen, candidates, bounds, other_pins,
        private=school.school_type != "state",
        near=(float(location.lat), float(location.lng)) if location.lat is not None and location.lng is not None else None,
        # A school of several locations may map only its main campus (as in confirm-pins).
        site_points=[p for p in website_points or [] if point_in_bounds(*p, bounds)] if only_location else (),
    )
    if refused:
        return row | {"outcome": "refused", "refused": refused}
    return row | {"outcome": "placed", "point": chosen.point, "chosen": asdict(chosen)}


async def run_pin_agent(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: str = "sofia",
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    max_usd: float = DEFAULT_MAX_USD,
    apply: bool = False,
    fetch: Optional[Fetch] = None,
    reverse: Optional[Reverse] = None,
    lookup: Optional[Lookup] = None,
    call: Optional[Caller] = None,
) -> dict[str, Any]:
    bounds, town = get_city_bounds(country, city), CITY_NAMES.get((country, (city or "").casefold()))
    if bounds is None or town is None:
        raise ValueError(f"pin-agent needs a city with write bounds and a local name, not {city!r}")
    query = select(School.id).where(School.country_code == country, func.lower(School.city) == city.lower())
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    ids = list((await db.execute(query.order_by(School.id))).scalars().all())

    # Published pins of every school, to refuse a building another school already holds.
    pinned = (
        await db.execute(select(SchoolLocation).where(SchoolLocation.lat.isnot(None), SchoolLocation.lng.isnot(None)))
    ).scalars().all()
    pins_by_school: dict[int, list[tuple[tuple[float, float], str]]] = {}
    for loc in pinned:
        if not needs_pin(loc):
            pins_by_school.setdefault(loc.school_id, []).append(((float(loc.lat), float(loc.lng)), _address(loc)))

    spend = Spend(max_usd)
    injected_call = call is not None
    call = call or model_caller(spend)
    if reverse is None:
        from app.services.geocoding.nominatim import NominatimProvider
        from app.services.geocoding.service import nominatim_user_agent

        reverse = NominatimProvider(user_agent=nominatim_user_agent(get_settings())).reverse_address

    results: list[dict[str, Any]] = []
    stopped: Optional[str] = None
    osm_failed: list[Optional[str]] = []

    async def _run(fetch: Fetch, lookup: Lookup) -> None:
        nonlocal stopped
        schools_done = 0
        for school_id in ids:
            if limit is not None and schools_done >= limit:
                break
            school = await db.get(School, school_id)
            locations = (
                await db.execute(select(SchoolLocation).where(SchoolLocation.school_id == school_id).order_by(SchoolLocation.id))
            ).scalars().all()
            hidden = [loc for loc in locations if needs_pin(loc) and _address(loc)]
            if not hidden:
                continue
            schools_done += 1
            website_points = None
            if school.website_url and school.scrape_status in SITE_ACCEPTED_STATUSES:
                try:
                    website_points = await _read_points(school, fetch)
                except Exception:  # a site that is down still leaves the OSM candidates
                    logger.exception("pin-agent could not read the website of school %s", school_id)
            others = [pin for other, pins in pins_by_school.items() if other != school_id for pin in pins]
            for location in hidden:
                row = {"school_id": school_id, "location_id": location.id, "address": _address(location)}
                try:
                    row |= await place_location(
                        school, location, website_points, reverse=reverse, lookup=lookup, call=call,
                        bounds=bounds, town=town, other_pins=others, only_location=len(locations) == 1,
                    )
                except (SpendCapReached, ProviderCostError) as exc:
                    stopped = str(exc)
                    return
                except Exception as exc:  # one location's lookups must not stop the run
                    logger.exception("pin-agent failed for location %s", location.id)
                    row |= {"outcome": "error", "note": str(exc)[:200]}
                if row["outcome"] == "placed":  # later schools may not pick the same building
                    pins_by_school.setdefault(school_id, []).append((row["point"], row["address"]))
                results.append(row)

    async def _with_sources() -> None:
        if fetch is not None and lookup is not None:
            await _run(fetch, lookup)
            return
        from crawl4ai.utils import RobotsParser  # type: ignore

        async with fee_http_client() as client, httpx.AsyncClient(headers={"User-Agent": CRAWLER_USER_AGENT}) as osm:
            city_osm = CityOsm(osm, bounds)
            await _run(fetch or _http_fetch(client, RobotsParser()), lookup or city_osm)
            osm_failed.append(city_osm.failed)

    if injected_call:
        await _with_sources()
    else:
        # Billed calls are reserved and attributed to a pipeline run, as in the other agents.
        settings = get_settings()
        run = await start_pipeline_run(
            db, country=country, city=city, cli_stage=STAGE, config={"provider_cost_cap_usd": max_usd, "apply": apply}
        )
        run_id = run.id
        error: Optional[str] = None
        try:
            with provider_cost_scope(
                pipeline_run_id=run_id,
                stage=STAGE,
                cap_usd=max_usd,
                request_reserve_usd=float(settings.provider_request_reserve_usd),
            ):
                async with pipeline_run_heartbeat(
                    run_id,
                    session_factory=async_session_maker,
                    interval_seconds=float(settings.pipeline_heartbeat_interval_seconds),
                ):
                    await _with_sources()
        except Exception as exc:
            logger.exception("pin-agent run %s stopped", run_id)
            if db.in_transaction():
                await db.rollback()
            error = stopped = f"run stopped: {str(exc)[:200]}"
        failed = sum(r["outcome"] == "error" for r in results)
        try:
            run = await db.get(PipelineRun, run_id, populate_existing=True)
            spend.spent_usd = max(spend.spent_usd, float(run.total_llm_cost_usd or 0))
            await finalize_pipeline_run(
                db,
                run,
                country=country,
                city=city,
                stage_summaries=[
                    stage_common.stage_summary(processed=len(results), succeeded=len(results) - failed, failed=failed)
                ],
                error_summary=error or stopped,
            )
        except Exception:  # the run's bookkeeping must not lose the picks and the report
            logger.exception("pin-agent could not finalize run %s", run_id)
            if db.in_transaction():
                await db.rollback()

    if apply:
        # Written after all the reading, each row locked only for its own short write.
        for row in results:
            if row["outcome"] == "placed":
                row["outcome"] = await write_pin(
                    db, row["location_id"], row["point"], provider=row["chosen"]["source"], method="agent_pick",
                    tag=PIN_TAG, evidence={"chosen": row["chosen"], "reason": row["pick"]["reason"]},
                )
    outcomes: dict[str, int] = {}
    for row in results:
        outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
    return {
        "apply": apply, "locations": len(results), "outcomes": outcomes, "spent_usd": round(spend.spent_usd, 4),
        "stopped": stopped, "osm_unavailable": next((f for f in osm_failed if f), None), "results": results,
    }
