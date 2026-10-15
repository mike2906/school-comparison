"""Confirm-pins stage: publish a hidden pin that already stands on our building.

Many hidden (approximate) points are right: an EU GeoJSON or Nominatim point at the school's
own house. A point is confirmed when OpenStreetMap's nearest building there is in our town,
on our street, with exactly our house number (the same check as ``website-pins``). That
proves the pin matches the address we show, not that the address is current, so a school
whose own website maps it only somewhere else (over ``ELSEWHERE_M`` away) is left hidden:
the website suggests it moved. No model calls. Default is a dry run. Sofia only for now
(``website_pins.CITY_NAMES``). Run it before ``website-pins`` and ``pin-agent``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.stages.social_links import Fetch, _http_fetch
from app.scrapers.stages.website_pins import (
    CITY_NAMES,
    Reverse,
    _address,
    _distance_m,
    _read_points,
    needs_pin,
    place,
    write_pin,
)
from app.services.geocoding.bounds import get_city_bounds
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

PIN_TAG = "coords_source=reverse_confirmed"
# A website map point this far from ours, with none closer, says the school is elsewhere.
ELSEWHERE_M = 300


async def run_confirm_pins(
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
    """Run over the city's hidden locations that have a point and an address; returns the report."""
    bounds, town = get_city_bounds(country, city), CITY_NAMES.get((country, (city or "").casefold()))
    if bounds is None or town is None:
        raise ValueError(f"confirm-pins needs a city with write bounds and a local name, not {city!r}")
    query = (
        select(School.id)
        .where(School.country_code == country, func.lower(School.city) == city.lower())
        .order_by(School.id)
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    ids = list((await db.execute(query)).scalars().all())

    if reverse is None:
        from app.config import get_settings
        from app.services.geocoding.nominatim import NominatimProvider
        from app.services.geocoding.service import nominatim_user_agent

        reverse = NominatimProvider(user_agent=nominatim_user_agent(get_settings())).reverse_address

    async def _run(fetch: Fetch) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for school_id in ids:
            if limit is not None and len({r["school_id"] for r in results}) >= limit:
                break
            school = await db.get(School, school_id)
            locations = (
                (await db.execute(select(SchoolLocation).where(SchoolLocation.school_id == school_id).order_by(SchoolLocation.id)))
                .scalars()
                .all()
            )
            hidden = [
                loc for loc in locations if needs_pin(loc) and _address(loc) and loc.lat is not None and loc.lng is not None
            ]
            if not hidden:
                continue
            site_points: list[tuple[float, float]] = []
            if school.website_url and school.scrape_status in SITE_ACCEPTED_STATUSES:
                try:
                    site_points = await _read_points(school, fetch) or []
                except Exception:  # an unreadable site says nothing either way
                    logger.exception("confirm-pins could not read school %s's site", school_id)
            for location in hidden:
                point = (location.lat, location.lng)
                row = {"school_id": school_id, "location_id": location.id, "address": _address(location), "point": point}
                if site_points and all(_distance_m(point, p) > ELSEWHERE_M for p in site_points):
                    results.append({**row, "outcome": "website_map_elsewhere", "site_points": site_points})
                    continue
                try:
                    found = await place(_address(location), [point], reverse, bounds, town)
                except Exception as exc:  # Nominatim down: report, keep the location as it is
                    results.append({**row, "outcome": "not_checked", "note": str(exc)[:200]})
                    continue
                outcome = "placed" if found["outcome"] == "placed" else "not_at_address"
                results.append({**row, "outcome": outcome, "checked": found["checked"]})
        return results

    if fetch is None:
        from crawl4ai.utils import RobotsParser  # type: ignore

        async with fee_http_client() as client:
            results = await _run(_http_fetch(client, RobotsParser()))
    else:
        results = await _run(fetch)
    if apply:
        for result in results:
            if result["outcome"] == "placed":
                written = await write_pin(
                    db,
                    result["location_id"],
                    result["point"],
                    provider="nominatim_reverse",
                    method="reverse_confirmed",
                    tag=PIN_TAG,
                    evidence={"checked": result["checked"]},
                )
                result["outcome"] = written
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
    return {"apply": apply, "locations": len(results), "outcomes": outcomes, "results": results}
