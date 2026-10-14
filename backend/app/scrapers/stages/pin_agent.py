"""Pin-agent stage: a model picks a hidden location's building from points sources gave.

For a location the API publishes without a pin (approximate or none), gathers candidate
points: the school's own website map points (with OpenStreetMap's address there) and
OpenStreetMap schools and kindergartens near the location's street. One model call picks
the candidate that is this school's building, or none. The model never gives coordinates:
it picks a candidate by number, and code checks the pick before anything is written. The
candidate must be on our street (house number within ``MAX_NUMBER_GAP``) or be an OSM
school or kindergarten whose name matches the school's, and no other school may be pinned
on that spot. Anything else stays hidden. Runs after ``website-pins`` (which takes exact
address matches without a model). Default is a dry run that writes nothing. Not in ``all``.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.client import create_agent, extract_provider_cost_usd, get_model, get_model_costs
from app.config import CRAWLER_USER_AGENT, get_settings
from app.models import School, SchoolLocation
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.shared_site_check import level_family
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
from app.services.geocoding.bg.address_match import house_number, same_street
from app.services.geocoding.bounds import get_city_bounds, point_in_bounds
from app.services.provider_costs import execute_billable_request
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

STAGE = "pin-agent"
TIER = "pricing"  # cheap and follows instructions well (the fees and facts readers' tier)
DEFAULT_MAX_USD = 0.50
OUTPUT_TOKENS_RESERVE = 400
PIN_TAG = "coords_source=pin_agent"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# How far from the location's street point OSM schools are looked for.
SEARCH_RADIUS_M = 2000
# A building on our street whose number is this close to ours counts as ours (OSM and
# school addresses often differ by a building: НПМГ is "Бигла 56", OSM's building 52).
MAX_NUMBER_GAP = 4
# Another school's published pin this close means the candidate is that school's building.
OTHER_SCHOOL_M = 25
MAX_CANDIDATES = 12

# Name words shared by many institutions, which say nothing about which one it is.
_GENERIC_NAME_WORDS = {
    "училище", "средно", "основно", "обединено", "начално", "частно", "частна",
    "профилирана", "професионална", "гимназия", "детска", "градина", "ясла", "яслена",
    "школа", "школо", "център", "софия", "столична", "national", "school",
    "kindergarten", "international", "private", "the", "and", "for", "на", "по", "за", "и",
    "чоу", "чсу", "чдг", "дг", "оу", "су", "ну", "пг", "спг", "пмг", "чпг", "чну", "обу",
}

Lookup = Callable[[float, float], Awaitable[list[dict]]]
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
schools/kindergartens mapped in OpenStreetMap near the address (with their names).

Pick the candidate that is this institution's own building. Good evidence: an OSM name
that is this institution (same name, or same number and type); a website map point on the
address's street, at or next to the house number. Not evidence: being near, or a similar
but different institution (another number, another kindergarten of a chain, a state
school a private one shares a name with). A website can show several branches: pick only
the one at this address. If no candidate clearly fits, answer 0. Set sure=false unless
the evidence is clear. Never invent a candidate."""


def _words(text: str) -> set[str]:
    words = re.findall(r"[а-яa-z]+|\d+", (text or "").casefold().replace("ё", "е"))
    return {w for w in words if (w.isdigit() or len(w) >= 3) and w not in _GENERIC_NAME_WORDS}


def name_matches(school_name: str, osm_name: Optional[str]) -> bool:
    """Whether an OSM name is this institution: the same number when the school name has
    one, and otherwise two shared distinctive name words (one when ours has only one)."""
    ours, theirs = _words(school_name), _words(osm_name or "")
    numbers = {w for w in ours if w.isdigit()}
    if numbers:
        others = ours - numbers
        return bool(numbers & theirs) and (not others or bool(others & theirs))
    return bool(ours) and len(ours & theirs) >= min(2, len(ours))


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


def check_pick(
    school_name: str,
    kindergarten: bool,
    address: str,
    candidate: Candidate,
    candidates: Sequence[Candidate],
    bounds: dict[str, float],
    other_pins: Sequence[tuple[float, float]],
) -> Optional[str]:
    """Why the picked candidate cannot be written, or None when it can.

    A name match counts only when it is the one OSM feature of that name among the
    candidates (a chain's branches share a name) and OSM gives it no other street."""
    if not point_in_bounds(*candidate.point, bounds):
        return "outside the city"
    if not candidate.town_ok:
        return "another town"
    if any(_distance_m(candidate.point, pin) < OTHER_SCHOOL_M for pin in other_pins):
        return "another school is pinned there"
    if on_our_street(address, candidate.address):
        return None
    if candidate.source == "osm_school" and (candidate.amenity == "kindergarten") != kindergarten:
        return "OSM maps another kind of institution there"
    if candidate.source == "osm_school" and name_matches(school_name, candidate.name):
        namesakes = [c for c in candidates if c.source == "osm_school" and name_matches(school_name, c.name)]
        if len(namesakes) > 1:
            return "several OSM features have this name"
        if candidate.address and not same_street(address, candidate.address):
            return "OSM gives the named building another street"
        return None
    return "neither on our street nor named as this school"


def candidates_message(school_name: str, kind: str, address: str, candidates: Sequence[Candidate]) -> str:
    lines = [f"Institution: {school_name} ({kind})", f"Address: {address}", "", "Candidates:"]
    for number, candidate in enumerate(candidates, start=1):
        if candidate.source == "website_map":
            lines.append(f"{number}. Map point on the institution's website; OSM address there: {candidate.address or 'unknown'}")
        else:
            lines.append(f"{number}. OSM {candidate.name!r}; OSM address: {candidate.address or 'none given'}")
    return "\n".join(lines)


def _osm_address(tags: dict) -> Optional[str]:
    street, number = tags.get("addr:street"), tags.get("addr:housenumber")
    return nominatim_address({"road": street, "house_number": number})


def osm_lookup(client: httpx.AsyncClient) -> Lookup:
    """Named OSM schools, kindergartens and colleges within SEARCH_RADIUS_M of a point."""

    async def lookup(lat: float, lng: float) -> list[dict]:
        query = (
            f"[out:json][timeout:25];nwr(around:{SEARCH_RADIUS_M},{lat},{lng})"
            '[amenity~"^(school|kindergarten|college)$"][name];out center tags;'
        )
        response = await client.post(OVERPASS_URL, data={"data": query}, timeout=40)
        response.raise_for_status()
        return response.json().get("elements", [])

    return lookup


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
            result = await execute_billable_request(
                lambda: agent.run(message), model=get_model(TIER), school_id=school_id, stage=STAGE, timeout_seconds=90
            )
        spend.spent_usd += extract_provider_cost_usd(result)
        return result.output

    return call


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
    other_pins: Sequence[tuple[float, float]],
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
        for candidate in osm_candidates(await lookup(float(location.lat), float(location.lng))):
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
    refused = check_pick(
        name, level_family(school.education_level) == "kindergarten", address, chosen, candidates, bounds, other_pins
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
    pins_by_school: dict[int, list[tuple[float, float]]] = {}
    for loc in pinned:
        if not needs_pin(loc):
            pins_by_school.setdefault(loc.school_id, []).append((float(loc.lat), float(loc.lng)))

    spend = Spend(max_usd)
    call = call or model_caller(spend)
    if reverse is None:
        from app.services.geocoding.nominatim import NominatimProvider
        from app.services.geocoding.service import nominatim_user_agent

        reverse = NominatimProvider(user_agent=nominatim_user_agent(get_settings())).reverse_address

    results: list[dict[str, Any]] = []
    stopped: Optional[str] = None

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
                        bounds=bounds, town=town, other_pins=others,
                    )
                except SpendCapReached as exc:
                    stopped = str(exc)
                    return
                except Exception as exc:  # one location's lookups must not stop the run
                    logger.exception("pin-agent failed for location %s", location.id)
                    row |= {"outcome": "error", "note": str(exc)[:200]}
                results.append(row)

    if fetch is None or lookup is None:
        from crawl4ai.utils import RobotsParser  # type: ignore

        async with fee_http_client() as client, httpx.AsyncClient(headers={"User-Agent": CRAWLER_USER_AGENT}) as osm:
            await _run(fetch or _http_fetch(client, RobotsParser()), lookup or osm_lookup(osm))
    else:
        await _run(fetch, lookup)

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
        "stopped": stopped, "results": results,
    }
