"""Register-pins stage: pin a hidden Sofia location on the city's official address point.

Sofia Municipality's address register (``geocoding/bg/sofia_address_register``) has a point
for every approved street and house number and every block entrance, which OpenStreetMap
mostly lacks. A hidden location whose address matches one register point (or one block's
entrances, close together) is pinned there. The school's own site wins: one whose website
maps it only elsewhere (none of its map points within ``ELSEWHERE_M`` of the register point
or of its other locations) is left hidden, as the site may say it moved, unless the site's
text states our address (stray and old maps are common); one whose website cannot be read
is left hidden. A pin moving over ``FAR_M`` needs our district or the site to agree. No
model calls. Default is a dry run. Sofia only.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.stages.confirm_pins import ELSEWHERE_M
from app.scrapers.stages.social_links import Fetch, _http_fetch
from app.scrapers.stages.website_pins import _address, _distance_m, needs_pin, read_site, write_pin
from app.services.geocoding.bg import sofia_address_register as register_lib
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

PIN_TAG = "coords_source=sofia_address_register"
PROVIDER = "sofia_address_register"
# A pin moving further than this needs our address's district, or the school's site, to agree.
FAR_M = 3000


async def download_register() -> register_lib.Register:
    import asyncio

    import httpx

    from app.config import CRAWLER_USER_AGENT

    async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers={"User-Agent": CRAWLER_USER_AGENT}) as client:
        for attempt in (1, 2, 3):
            try:
                response = await client.get(register_lib.CSV_ZIP_URL)
                response.raise_for_status()
                return register_lib.read_register_zip(response.content)
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                await asyncio.sleep(attempt * 5)
    raise AssertionError("unreachable")


async def run_register_pins(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: str = "sofia",
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    apply: bool = False,
    register: Optional[register_lib.Register] = None,
    fetch: Optional[Fetch] = None,
) -> dict[str, Any]:
    """Run over the city's hidden locations that have an address; returns the report."""
    if (country, (city or "").casefold()) != ("bg", "sofia"):
        raise ValueError(f"register-pins has Sofia's address register only, not {city!r}")
    query = (
        select(School.id)
        .where(School.country_code == country, func.lower(School.city) == city.lower())
        .order_by(School.id)
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    ids = list((await db.execute(query)).scalars().all())

    async def _run(fetch: Fetch) -> list[dict[str, Any]]:
        nonlocal register
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
            hidden = [loc for loc in locations if needs_pin(loc) and _address(loc)]
            if not hidden:
                continue
            if register is None:  # only once a location needs it
                register = await download_register()
            found, parsed = {}, {}
            for location in hidden:
                parsed[location.id] = register_lib.parse(_address(location), location.district)
                found[location.id] = register_lib.match(parsed[location.id], register)
            site: Optional[tuple[list[tuple[float, float]], str]] = ([], "")
            if any(m.outcome == "matched" for m in found.values()) and school.website_url and (
                school.scrape_status in SITE_ACCEPTED_STATUSES
            ):
                try:
                    site = await read_site(school, fetch)
                except Exception:
                    logger.exception("register-pins could not read school %s's site", school_id)
                    site = None
            site_points, text = site if site is not None else ([], "")
            prepared = register_lib.page_text(text)
            others = [
                (loc.lat, loc.lng) for loc in locations if not needs_pin(loc) and loc.lat is not None and loc.lng is not None
            ]
            for location in hidden:
                result = found[location.id]
                old = (location.lat, location.lng) if location.lat is not None and location.lng is not None else None
                row = {
                    "school_id": school_id,
                    "location_id": location.id,
                    "address": _address(location),
                    "old_point": old,
                    "outcome": result.outcome,
                }
                if result.outcome == "ambiguous":
                    row["candidates"] = sorted({(r.lat, r.lng) for r in result.rows})[:10]
                if result.outcome != "matched":
                    results.append(row)
                    continue
                row["point"] = result.point
                row["moved_m"] = round(_distance_m(old, result.point)) if old else None
                row["register"] = [
                    {k: v for k, v in vars(r).items() if v} for r in result.rows[:5]
                ]
                # The school's own site wins: its text giving our address outweighs its map (a
                # stray or old map is common); a map with every point at the register point backs it.
                site_names_it = register_lib.names_address(prepared, parsed[location.id])
                site_map_here = bool(site_points) and all(
                    _distance_m(result.point, p) <= ELSEWHERE_M for p in site_points
                )
                if site_names_it:
                    row["site_names_address"] = True
                if (
                    row["moved_m"] is not None
                    and row["moved_m"] > FAR_M
                    and not result.region_checked
                    and not (site_names_it or site_map_here)
                ):
                    # As far as another district: our district or the school's site must agree.
                    row["outcome"] = "moved_far"
                elif site is None:
                    row["outcome"] = "site_not_read"
                elif (
                    site_points
                    and not site_names_it
                    and not any(_distance_m(point, p) <= ELSEWHERE_M for point in [result.point, *others] for p in site_points)
                ):
                    row["outcome"] = "website_map_elsewhere"
                    row["site_points"] = site_points
                else:
                    row["outcome"] = "placed"
                results.append(row)
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
                result["outcome"] = await write_pin(
                    db,
                    result["location_id"],
                    result["point"],
                    provider=PROVIDER,
                    method="address_register",
                    tag=PIN_TAG,
                    evidence={"register": result["register"], "dataset": register_lib.DATASET_URL},
                    unchanged_from=result["old_point"],
                )
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
    return {"apply": apply, "locations": len(results), "outcomes": outcomes, "results": results}
