"""Website-pins stage: place a hidden (approximate or missing) pin on the school's own map point.

A school's website often embeds a map of its building. That point is taken only when
OpenStreetMap puts it at our address: Nominatim's nearest building there must be in our
town, on our street, with exactly our house number. A wrong website, a map of another
branch, or a point dropped on the district centre then fails the check, so the website
itself does not have to be trusted. Two distinct points on the site that both pass are
left alone. Only cities with write bounds (Sofia) for now: the town check needs each
city's local name. No model calls. The default is a dry run that writes nothing. Not part
of ``all``; run it after geocoding.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.stages.social_links import Fetch, _contact_page, _http_fetch, page_links
from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.bg.address_match import same_building, same_house_number
from app.services.geocoding.bg.sofia_address_register import distance_m
from app.services.geocoding.bounds import get_city_bounds, point_in_bounds
from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG, apply_geocode_result_to_location, has_pinned_point
from app.utils.display_gating import UNVERIFIED_PIN_PRECISION
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

# (lat, lng) -> Nominatim's address parts for the nearest building, or None.
Reverse = Callable[[float, float], Awaitable[Optional[dict]]]

PIN_TAG = "coords_source=website_map_link"
# Map points closer than this are one building drawn twice.
SAME_POINT_METRES = 50
# Google embeds carry the pin as !3d<lat>!4d<lng> (or !2d<lng>!3d<lat> for the view).
_EMBED_RE = re.compile(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)|!2d(-?\d+\.\d+)!3d(-?\d+\.\d+)")
_PAIR_RE = re.compile(r"(-?\d{1,2}\.\d{3,})\s*,\s*(-?\d{1,3}\.\d{3,})")
_MAP_HOST_RE = re.compile(r"(^|\.)(google\.[a-z]{2,3}(\.[a-z]{2})?|openstreetmap\.org)$")


_distance_m = distance_m


def _link_point(url: str) -> Optional[tuple[float, float]]:
    """The point a Google Maps or OpenStreetMap link or embed is centred on, or None."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not _MAP_HOST_RE.search(host):
        return None
    if "google." in host and not host.startswith("maps.") and not parsed.path.startswith("/maps"):
        return None
    text = unquote(url)
    if match := _EMBED_RE.search(text):
        lat, lng = (match.group(1), match.group(2)) if match.group(1) else (match.group(4), match.group(3))
        return float(lat), float(lng)
    query = parse_qs(parsed.query)
    for name in ("mlat", "marker", "q", "query", "ll", "destination", "center"):
        for value in query.get(name, []):
            if name == "mlat" and query.get("mlon"):
                return float(value), float(query["mlon"][0])
            if match := _PAIR_RE.search(value):
                return float(match.group(1)), float(match.group(2))
    if match := re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", text):
        return float(match.group(1)), float(match.group(2))
    if match := re.search(r"map=\d+/(-?\d+\.\d+)/(-?\d+\.\d+)", parsed.fragment):  # openstreetmap.org/#map=
        return float(match.group(1)), float(match.group(2))
    return None


def map_points(html: str, base: str) -> list[tuple[float, float]]:
    """Distinct map points the page's links and map frames show."""
    points: list[tuple[float, float]] = []
    for url in page_links(html, base):
        point = _link_point(url)
        if point and all(_distance_m(point, seen) >= SAME_POINT_METRES for seen in points):
            points.append(point)
    return points


def nominatim_address(parts: Optional[dict]) -> Optional[str]:
    """"ул. <road> № <house number>" from Nominatim's address parts, or None without both."""
    road, number = (parts or {}).get("road"), (parts or {}).get("house_number")
    if not road or not number:
        return None
    return f'ул. "{road}" № {number}'


# Our address's own locality ("гр. Банкя", "с. Казичене"), when it names one.
_LOCALITY_RE = re.compile(
    r"(?<![а-яa-z])(?:гр|с)\.\s*([а-я][а-я -]*?)\s*(?=[,\d(]|\s-|(?:ж\.?\s*к|жк|кв|р-н|ул|бул)[.\s]|$)"
)
# The town Nominatim gives for a point, from the most to the least local.
_PLACE_KEYS = ("village", "town", "city")
# A city's local name, for addresses that do not name their town.
CITY_NAMES = {("bg", "sofia"): "софия"}


def same_town(address: str, parts: Optional[dict], default_town: str) -> bool:
    """Whether Nominatim's place is the town our address names (else ``default_town``)."""
    match = _LOCALITY_RE.search((address or "").casefold())
    wanted = match.group(1).strip() if match else default_town
    found = next((parts[key] for key in _PLACE_KEYS if isinstance((parts or {}).get(key), str)), "")
    return bool(wanted) and found.casefold().strip() == wanted


def _address(location: SchoolLocation) -> str:
    address = location.address_i18n or {}
    return address.get("bg") or address.get("en") or ""


def needs_pin(location: SchoolLocation) -> bool:
    """A location published with no pin: an approximate point, or none, and not one pinned
    by an official source or by hand."""
    if has_pinned_point(location):
        return False
    meta = location.geocode_meta if isinstance(location.geocode_meta, dict) else {}
    return location.lat is None or location.lng is None or meta.get("precision") == UNVERIFIED_PIN_PRECISION


async def place(
    address: str,
    points: Sequence[tuple[float, float]],
    reverse: Reverse,
    bounds: dict[str, float],
    town: str,
) -> dict[str, Any]:
    """``{"outcome": ..., "point": (lat, lng) | None, "checked": [...]}`` for one address.

    Outcomes: "placed" (exactly one point is at the address), "several" (more than one),
    "no_match", "no_points".
    """
    checked, matches = [], []
    for lat, lng in points:
        if not point_in_bounds(lat, lng, bounds):
            checked.append({"point": (lat, lng), "found": None, "note": "outside the city"})
            continue
        parts = await reverse(lat, lng)
        found = nominatim_address(parts)
        # same_building matches the street but takes any shared number (a block number,
        # 55а for 55б); the house number itself must be exactly ours.
        agrees = (
            bool(found)
            and same_building(address, found)
            and same_house_number(parts.get("house_number"), address)
            and same_town(address, parts, town)
        )
        checked.append({"point": (lat, lng), "found": found, "agrees": agrees})
        if agrees:
            matches.append((lat, lng))
    if not points:
        outcome = "no_points"
    elif len(matches) == 1:
        outcome = "placed"
    else:
        outcome = "several" if matches else "no_match"
    return {"outcome": outcome, "point": matches[0] if outcome == "placed" else None, "checked": checked}


# Coordinates a script draws its map with: LatLng(42.70, 23.13), {lat: 42.70, lng: 23.13},
# locations: [[42.70, 23.13]].
_SCRIPT_POINT_RES = (
    re.compile(r"LatLng\(\s*(-?\d{1,2}\.\d{4,})\s*,\s*(-?\d{1,3}\.\d{4,})\s*\)"),
    re.compile(r"\blat[\"']?\s*:\s*(-?\d{1,2}\.\d{4,})\s*,\s*[\"']?(?:lng|lon)[\"']?\s*:\s*(-?\d{1,3}\.\d{4,})"),
    re.compile(r"\[\s*(-?\d{1,2}\.\d{4,})\s*,\s*(-?\d{1,3}\.\d{4,})\s*\]"),
)
# A script with more points than this is a list of places (a store finder), not the school.
MAX_SCRIPT_POINTS = 3
MAX_SCRIPTS = 5


def script_points(text: str, bounds: dict[str, float]) -> list[tuple[float, float]]:
    """Distinct points inside ``bounds`` that a script's map code names (none when it names
    more than ``MAX_SCRIPT_POINTS``)."""
    points: list[tuple[float, float]] = []
    for pattern in _SCRIPT_POINT_RES:
        for lat, lng in pattern.findall(text):
            point = (float(lat), float(lng))
            if point_in_bounds(*point, bounds) and all(
                _distance_m(point, seen) >= SAME_POINT_METRES for seen in points
            ):
                points.append(point)
    return points if len(points) <= MAX_SCRIPT_POINTS else []


def _same_site_scripts(html: str, base: str) -> list[str]:
    host = urlparse(base).hostname
    urls = []
    for tag in BeautifulSoup(html, "html.parser").find_all("script", src=True):
        url = urljoin(base, tag["src"])
        if urlparse(url).hostname == host and url not in urls:
            urls.append(url)
    return urls[:MAX_SCRIPTS]


async def _script_map_points(
    pages: list[tuple[str, str]], fetch_script: Fetch, bounds: dict[str, float]
) -> list[tuple[float, float]]:
    """Points a page's own scripts (inline, then same-site files) draw its map with."""
    for _final, html in pages:
        inline = " ".join(t.get_text() for t in BeautifulSoup(html, "html.parser").find_all("script", src=False))
        if points := script_points(inline, bounds):
            return points
    for final, html in pages:
        for url in _same_site_scripts(html, final):
            script = await fetch_script(url)
            if script is not None and (points := script_points(script[1], bounds)):
                return points
    return []


def page_text(html: str) -> str:
    """A page's visible text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()
    return soup.get_text(" ")


async def read_site(
    school: School,
    fetch: Fetch,
    *,
    fetch_script: Optional[Fetch] = None,
    bounds: Optional[dict[str, float]] = None,
) -> Optional[tuple[list[tuple[float, float]], str]]:
    """Map points and visible text of the home page and the contact page, or None when the
    home page could not be read. With ``fetch_script`` and ``bounds``, a site whose pages
    link no map is read for the points its own scripts draw a map with."""
    home = await fetch(school.website_url)
    if home is None:
        return None
    final, html = home
    pages = [home]
    points, texts = map_points(html, final), [page_text(html)]
    contact = _contact_page(html, final, final)
    page = await fetch(contact) if contact else None
    if page is not None:
        pages.append(page)
        texts.append(page_text(page[1]))
        for point in map_points(page[1], page[0]):
            if all(_distance_m(point, seen) >= SAME_POINT_METRES for seen in points):
                points.append(point)
    if not points and fetch_script is not None and bounds is not None:
        points = await _script_map_points(pages, fetch_script, bounds)
    return points, "\n".join(texts)


async def _read_points(school: School, fetch: Fetch) -> Optional[list[tuple[float, float]]]:
    """Map points on the home page and the contact page, or None when the home page could
    not be read."""
    site = await read_site(school, fetch)
    return None if site is None else site[0]


async def run_website_pins(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: str = "sofia",
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    apply: bool = False,
    fetch: Optional[Fetch] = None,
    reverse: Optional[Reverse] = None,
) -> dict[str, Any]:
    """Run over schools whose website URL validation accepted and that have a location
    without a published pin; returns the report."""
    query = (
        select(School.id)
        .where(
            School.country_code == country,
            School.website_url.isnot(None),
            School.scrape_status.in_(SITE_ACCEPTED_STATUSES),
        )
        .order_by(School.id)
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    query = query.where(func.lower(School.city) == city.lower())
    ids = list((await db.execute(query)).scalars().all())

    if reverse is None:
        from app.config import get_settings
        from app.services.geocoding.nominatim import NominatimProvider
        from app.services.geocoding.service import nominatim_user_agent

        reverse = NominatimProvider(user_agent=nominatim_user_agent(get_settings())).reverse_address

    bounds, town = get_city_bounds(country, city), CITY_NAMES.get((country, (city or "").casefold()))
    if bounds is None or town is None:
        raise ValueError(f"website-pins needs a city with write bounds and a local name, not {city!r}")

    async def _run(fetch: Fetch) -> list[dict[str, Any]]:
        results = []
        for school_id in ids:
            if limit is not None and len({r["school_id"] for r in results}) >= limit:
                break
            school = await db.get(School, school_id)
            locations = (
                (await db.execute(select(SchoolLocation).where(SchoolLocation.school_id == school_id).order_by(SchoolLocation.id)))
                .scalars()
                .all()
            )
            hidden = [location for location in locations if needs_pin(location) and _address(location)]
            if not hidden:
                continue
            try:
                points = await _read_points(school, fetch)
            except Exception as exc:  # one school's site must not stop the run
                logger.exception("website-pins failed for school %s", school_id)
                points, note = None, str(exc)[:200]
            else:
                note = None
            for location in hidden:
                row = {"school_id": school_id, "location_id": location.id, "address": _address(location)}
                if points is None:
                    results.append({**row, "outcome": "not_read", **({"note": note} if note else {})})
                    continue
                try:
                    found = await place(_address(location), points, reverse, bounds, town)
                except Exception as exc:  # Nominatim down: report, keep the location as it is
                    results.append({**row, "outcome": "not_checked", "note": str(exc)[:200]})
                    continue
                results.append({**row, **found})
        return results

    if fetch is None:
        from crawl4ai.utils import RobotsParser  # type: ignore

        async with fee_http_client() as client:
            results = await _run(_http_fetch(client, RobotsParser()))
    else:
        results = await _run(fetch)
    if apply:
        # Written after all the reading, each row locked only for its own short write.
        for result in results:
            if result["outcome"] == "placed":
                result["outcome"] = await _write(db, result["location_id"], result["point"])
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
    return {"apply": apply, "locations": len(results), "outcomes": outcomes, "results": results}


async def _write(db: AsyncSession, location_id: int, point: tuple[float, float]) -> str:
    return await write_pin(db, location_id, point, provider="website", method="website_map_link", tag=PIN_TAG)


async def write_pin(
    db: AsyncSession,
    location_id: int,
    point: tuple[float, float],
    *,
    provider: str,
    method: str,
    tag: str,
    evidence: Optional[dict[str, Any]] = None,
    unchanged_from: Optional[tuple[float, float]] = None,
    address: Optional[str] = None,
) -> str:
    """Pin a still-hidden location at ``point`` (exact), under a short row lock, and commit.

    Returns "placed", "pinned_meanwhile" (another stage pinned it since the read) or
    "rejected" (the write gate refused it; the old point is kept). With ``unchanged_from``,
    a location whose point moved since the read is left alone ("moved_meanwhile"). With
    ``address``, the location's address becomes it (the old one is kept in the evidence)."""
    location = await db.get(SchoolLocation, location_id)
    # Another stage may have pinned it since the read; the lock waits for one still writing.
    await db.refresh(location, with_for_update=True)
    if not needs_pin(location):
        await db.rollback()
        return "pinned_meanwhile"
    if unchanged_from is not None and (location.lat, location.lng) != tuple(unchanged_from):
        await db.rollback()
        return "moved_meanwhile"
    school = await db.get(School, location.school_id)
    previous = dict(location.address_i18n or {})
    if address is not None:
        location.address_i18n = {"bg": address}  # English is derived from it for display
    applied = await apply_geocode_result_to_location(
        db,
        location,
        GeocodingResult(
            success=True,
            lat=point[0],
            lng=point[1],
            provider=provider,
            formatted_address=_address(location),
            method=method,
            precision="exact",
        ),
        school=school,
    )
    if not applied.success:
        await db.rollback()  # the gate cleared the old point; keep it
        return "rejected"
    if address is not None:
        evidence = {**(evidence or {}), "previous_address_i18n": previous}
    if evidence:
        location.geocode_meta = {**location.geocode_meta, "evidence": evidence}
    tags = [
        existing
        for existing in location.location_tags or []
        if not str(existing).startswith("coords_source=") or existing == OFFICIAL_COORDS_TAG
    ]
    location.location_tags = [*tags, tag]
    db.add(location)
    await db.commit()
    return "placed"
