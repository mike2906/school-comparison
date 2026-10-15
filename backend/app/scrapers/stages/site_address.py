"""Site-address stage: a hidden Sofia location takes the address its school's own site gives.

Mike's rule (2026-10-15): the school's own website wins over the ministry register's address
unless it is plainly out of date. Some schools register a legal seat (ЧСУ Дени Дидро: "ул.
Майор Коста Паница", no number) but say on their site where they are (Тенев баир 2, Банкя).
A site gives an address when its map (a linked map, or the coordinates its own scripts draw
a map with) sits within ``NEAR_M`` of a point in Sofia's address register and its pages
name that register address. Then the school's one hidden location takes that address and
point. Held back when the site gives several addresses, when the address is one of the
school's shown locations already (the hidden one is then an old address), when an unrelated
school is pinned there, or when the school has more than one hidden location (which one
moved is unclear). No model calls. Default is a dry run. Sofia only. Run after
``register-pins``.
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
from app.scrapers.stages.register_pins import SAME_BUILDING_M, _name_words, download_register
from app.scrapers.stages.social_links import Fetch, _http_fetch
from app.scrapers.stages.website_pins import _address, _distance_m, needs_pin, read_site, write_pin
from app.services.geocoding.bg import sofia_address_register as register_lib
from app.services.geocoding.bounds import get_city_bounds
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

PIN_TAG = "coords_source=school_site_address"
# A site's map point this close to a register point is at that address.
NEAR_M = 40


def _address_key(point: register_lib.RegisterPoint) -> tuple[str, ...]:
    """One address, whichever entrance of a block."""
    return (point.settlement, point.complex, point.block, point.street, point.number)


def site_addresses(
    points: list[tuple[float, float]], page: register_lib.PageText, register: register_lib.Register
) -> dict[tuple[str, ...], register_lib.RegisterPoint]:
    """Register addresses the site both maps and names, one point each."""
    found: dict[tuple[str, ...], register_lib.RegisterPoint] = {}
    for point in points:
        for candidate in register.near(point, NEAR_M):
            if _address_key(candidate) not in found and register_lib.names_address(
                page, register_lib.parse(candidate.label)
            ):
                found[_address_key(candidate)] = candidate
    return found


async def run_site_address(
    db: AsyncSession,
    *,
    country: str = "bg",
    city: str = "sofia",
    school_ids: Sequence[int] = (),
    limit: Optional[int] = None,
    apply: bool = False,
    register: Optional[register_lib.Register] = None,
    fetch: Optional[Fetch] = None,
    fetch_script: Optional[Fetch] = None,
) -> dict[str, Any]:
    """Run over the city's schools with an accepted website and a hidden location."""
    if (country, (city or "").casefold()) != ("bg", "sofia"):
        raise ValueError(f"site-address has Sofia's address register only, not {city!r}")
    bounds = get_city_bounds(country, city)
    query = (
        select(School.id)
        .where(
            School.country_code == country,
            func.lower(School.city) == city.lower(),
            School.website_url.is_not(None),
            School.scrape_status.in_(SITE_ACCEPTED_STATUSES),
        )
        .order_by(School.id)
    )
    if school_ids:
        query = query.where(School.id.in_(list(school_ids)))
    ids = list((await db.execute(query)).scalars().all())
    names = {sid: _name_words(name) for sid, name in (await db.execute(select(School.id, School.name_i18n))).all()}
    pinned = [
        (loc.school_id, (loc.lat, loc.lng))
        for loc in (await db.execute(select(SchoolLocation).where(SchoolLocation.lat.is_not(None)))).scalars()
        if not needs_pin(loc)
    ]

    async def _run(fetch: Fetch, fetch_script: Optional[Fetch]) -> list[dict[str, Any]]:
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
            hidden = [loc for loc in locations if needs_pin(loc)]
            if not hidden:
                continue
            if register is None:
                register = await download_register()
            rows = [
                {
                    "school_id": school_id,
                    "location_id": loc.id,
                    "address": _address(loc),
                    "old_point": (loc.lat, loc.lng) if loc.lat is not None and loc.lng is not None else None,
                }
                for loc in hidden
            ]
            try:
                site = await read_site(school, fetch, fetch_script=fetch_script, bounds=bounds)
            except Exception:
                logger.exception("site-address could not read school %s's site", school_id)
                site = None
            if site is None:
                results += [{**row, "outcome": "site_not_read"} for row in rows]
                continue
            points, text = site
            found = site_addresses(points, register_lib.page_text(text), register)
            shown = [(loc.lat, loc.lng) for loc in locations if not needs_pin(loc) and loc.lat is not None]
            outcome, extra = "placed", {}
            if not found:
                outcome = "no_site_address"
            elif len(found) > 1:
                outcome, extra = "several_site_addresses", {"site_addresses": [p.label for p in found.values()]}
            else:
                (address,) = found.values()
                point = (address.lat, address.lng)
                extra = {"site_address": address.label, "point": point, "site_points": points}
                if any(_distance_m(point, other) <= ELSEWHERE_M for other in shown):
                    outcome = "already_shown"  # the hidden location is an old address
                elif any(
                    other != school_id
                    and not (names.get(other, set()) & names.get(school_id, set()))
                    and _distance_m(point, spot) <= SAME_BUILDING_M
                    for other, spot in pinned
                ):
                    outcome = "other_school_here"
                elif len(hidden) > 1:
                    outcome = "several_hidden"
            for row in rows:
                if outcome == "placed" and row["old_point"]:
                    row["moved_m"] = round(_distance_m(row["old_point"], extra["point"]))
                results.append({**row, **extra, "outcome": outcome})
        return results

    if fetch is None:
        from crawl4ai.utils import RobotsParser  # type: ignore

        async with fee_http_client() as client:
            robots = RobotsParser()
            results = await _run(
                _http_fetch(client, robots), _http_fetch(client, robots, types=("javascript", "ecmascript"))
            )
    else:
        results = await _run(fetch, fetch_script)
    if apply:
        for result in results:
            if result["outcome"] == "placed":
                result["outcome"] = await write_pin(
                    db,
                    result["location_id"],
                    result["point"],
                    provider="school_site",
                    method="site_address",
                    tag=PIN_TAG,
                    evidence={"site_address": result["site_address"], "site_points": result["site_points"]},
                    unchanged_from=result["old_point"],
                    address=result["site_address"],
                )
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
    return {"apply": apply, "locations": len(results), "outcomes": outcomes, "results": results}
