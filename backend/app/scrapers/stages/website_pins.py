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
import math
import re
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Optional
from urllib.parse import parse_qs, unquote, urlparse

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.stages.social_links import Fetch, _contact_page, _http_fetch, page_links
from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.bg.address_match import same_building, same_house_number
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


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat = math.radians((a[0] + b[0]) / 2)
    dy = (a[0] - b[0]) * 111_320
    dx = (a[1] - b[1]) * 111_320 * math.cos(lat)
    return math.hypot(dx, dy)


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


async def _read_points(school: School, fetch: Fetch) -> Optional[list[tuple[float, float]]]:
    """Map points on the home page and the contact page, or None when the home page could
    not be read."""
    home = await fetch(school.website_url)
    if home is None:
        return None
    final, html = home
    points = map_points(html, final)
    contact = _contact_page(html, final, final)
    page = await fetch(contact) if contact else None
    if page is not None:
        for point in map_points(page[1], page[0]):
            if all(_distance_m(point, seen) >= SAME_POINT_METRES for seen in points):
                points.append(point)
    return points


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
    location = await db.get(SchoolLocation, location_id)
    # Another stage may have pinned it since the read; the lock waits for one still writing.
    await db.refresh(location, with_for_update=True)
    if not needs_pin(location):
        await db.rollback()
        return "pinned_meanwhile"
    school = await db.get(School, location.school_id)
    applied = await apply_geocode_result_to_location(
        db,
        location,
        GeocodingResult(
            success=True,
            lat=point[0],
            lng=point[1],
            provider="website",
            formatted_address=_address(location),
            method="website_map_link",
            precision="exact",
        ),
        school=school,
    )
    if not applied.success:
        await db.rollback()  # the gate cleared the old point; keep it
        return "rejected"
    tags = [
        tag
        for tag in location.location_tags or []
        if not str(tag).startswith("coords_source=") or tag == OFFICIAL_COORDS_TAG
    ]
    location.location_tags = [*tags, PIN_TAG]
    db.add(location)
    await db.commit()
    return "placed"
