"""Site-address stage: a hidden Sofia location takes the address its school's own site gives.

Mike's rule (2026-10-15): the school's own website wins over the ministry register's address
unless it is plainly out of date. Some schools register a legal seat (ЧСУ Дени Дидро: "ул.
Майор Коста Паница", no number) but say on their site where they are (Тенев баир 2, Банкя).
A site gives an address when its map (a linked map, or the coordinates its own scripts draw
a map with) sits within ``NEAR_M`` of a point in Sofia's address register and its pages
name that register address. A site also gives an address it names that the EU education
map's feature for the school gives too (two sources agreeing; ``map_feature_address``). Then
the school's one hidden location takes that address and point. Held back when the site gives several addresses, when the address is one of the
school's shown locations already (the hidden one is then an old address), when an unrelated
school is pinned there (or another school's site gives the same address), or when the
school has more than one hidden location (which one moved is unclear).

The site also settles a location by itself (``site_fixes``), shown or hidden: a site map
point at the register point of the location's own address places a hidden one there, or
moves a shown one pinned far away (ЧДГ Росица's "Кирил и Методий 70" was pinned in
Оборище; the site maps the one in Иваняне); a shown location whose address is not in the
register takes the register address the site names at our pin (ЧДГ Христо Ботев's "ул. 315
- III м. р." is "ул. Петър Бонев 3"). Official and hand pins are never changed. No model
calls. Default is a dry run. Sofia only. Run after ``register-pins``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.campus_sync import WEBSITE_CONTACT_ADDRESS_TAG
from app.scrapers.fee_pages import fee_http_client
from app.scrapers.stages.confirm_pins import ELSEWHERE_M
from app.scrapers.stages.register_pins import SAME_BUILDING_M, _name_words, download_register
from app.scrapers.stages.social_links import Fetch, _http_fetch
from app.scrapers.stages.website_pins import _address, _distance_m, needs_pin, read_site, write_pin
from app.services.geocoding.bg import sofia_address_register as register_lib
from app.services.geocoding.bg.geojson import GEOJSON_NAME_MATCH
from app.services.geocoding.bounds import get_city_bounds
from app.services.geocoding.write_gate import has_pinned_point
from app.utils.school_facts import SITE_ACCEPTED_STATUSES

logger = logging.getLogger(__name__)

PIN_TAG = "coords_source=school_site_address"
# A site's map point this close to a register point is at that address.
NEAR_M = 40
FIXED = ("placed", "moved", "readdressed")


def _address_key(point: register_lib.RegisterPoint) -> tuple[str, ...]:
    """One address, whichever entrance of a block."""
    return (point.settlement, point.complex, point.block, point.street, point.number)


def site_addresses(
    points: list[tuple[float, float]],
    page: register_lib.PageText,
    register: register_lib.Register,
    listed: Sequence[tuple[str, tuple[float, float]]] = (),
) -> dict[tuple[str, ...], tuple[register_lib.RegisterPoint, Optional[str]]]:
    """Register addresses the site names and either maps or that a ``listed`` map feature
    (its address and point) gives too, with that feature's address; one point each."""
    found: dict[tuple[str, ...], tuple[register_lib.RegisterPoint, Optional[str]]] = {}
    for point in points:
        for candidate in register.near(point, NEAR_M):
            if _address_key(candidate) not in found and register_lib.names_address(
                page, register_lib.parse(candidate.label)
            ):
                found[_address_key(candidate)] = (candidate, None)
    for address, feature in listed:
        parsed = register_lib.parse(address)
        result = register_lib.match(parsed, register)
        if (
            result.outcome == "matched"
            # The feature stands at its address: a feature matched to us by a namesake's name
            # is that namesake's, and its address one our site has no reason to name.
            and _distance_m(feature, result.point) <= ELSEWHERE_M
            and register_lib.names_address(page, parsed)
        ):
            row = replace(result.rows[0], lat=result.point[0], lng=result.point[1])
            found.setdefault(_address_key(row), (row, address))
    return found


def _same_address(a: register_lib.Parsed, b: register_lib.Parsed) -> bool:
    if a.street and b.street and a.number:
        return a.number == b.number and register_lib.same_name(a.street, b.street)
    if a.complex and b.complex and a.block:
        return a.block == b.block and register_lib.same_name(a.complex, b.complex)
    return False


def site_fixes(
    locations: Sequence[SchoolLocation],
    points: list[tuple[float, float]],
    page: register_lib.PageText,
    register: register_lib.Register,
) -> dict[int, tuple[str, register_lib.RegisterPoint]]:
    """``{location id: (outcome, register point)}`` for the locations the site settles by
    itself: "placed" or "moved" (a site map point at the register point of the location's
    own address; moved when a shown pin is over ``ELSEWHERE_M`` from it), "readdressed" (a
    shown location whose address is not in the register, at a register address the site
    names within ``NEAR_M`` of our pin)."""
    fixes: dict[int, tuple[str, register_lib.RegisterPoint]] = {}
    for location in locations:
        if has_pinned_point(location):
            continue
        ours = register_lib.parse(_address(location))
        at_map = {
            _address_key(candidate): candidate
            for point in points
            for candidate in register.near(point, NEAR_M)
            if _same_address(ours, register_lib.parse(candidate.label))
        }
        hidden = needs_pin(location)
        fix: Optional[tuple[str, register_lib.RegisterPoint]] = None
        if len(at_map) == 1:
            (candidate,) = at_map.values()
            if hidden:
                fix = ("placed", candidate)
            elif _distance_m((location.lat, location.lng), (candidate.lat, candidate.lng)) > ELSEWHERE_M:
                fix = ("moved", candidate)
        elif not hidden and register_lib.match(ours, register).outcome == "no_address":
            at_pin = site_addresses([(location.lat, location.lng)], page, register)
            if len(at_pin) == 1:
                ((candidate, _),) = at_pin.values()
                fix = ("readdressed", candidate)
        # Another of our locations there already: this one is an old address of it.
        if fix and not any(
            other.id != location.id
            and not needs_pin(other)
            and _distance_m((fix[1].lat, fix[1].lng), (other.lat, other.lng)) <= NEAR_M
            for other in locations
        ):
            fixes[location.id] = fix
    return fixes


def map_feature_address(location: SchoolLocation) -> Optional[tuple[str, tuple[float, float]]]:
    """The address and point of the EU education map's feature our hidden point was matched
    to by name, when its address is not ours. It is often the school's current address where
    ours is an old one (Мечо Пух: ours "ул. Стара планина 16", the feature's and the site's
    "ул. Лидице 14")."""
    meta = location.geocode_meta or {}
    address = meta.get("formatted_address")
    if (
        meta.get("method") != GEOJSON_NAME_MATCH
        or meta.get("status", "accepted") != "accepted"  # a rejected feature's address is kept too
        or not address
        or location.lat is None
        or location.lng is None
        # The point must be the feature's, not one another source wrote after it.
        or (meta.get("candidate") and (meta["candidate"].get("lat"), meta["candidate"].get("lng")) != (location.lat, location.lng))
    ):
        return None
    if _same_address(register_lib.parse(address), register_lib.parse(_address(location))):
        return None  # ours already: register-pins places it
    return address, (location.lat, location.lng)


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
    """Run over the city's schools with an accepted website and a location that is not an
    official or hand pin."""
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
    in_city = select(School.id).where(School.country_code == country, func.lower(School.city) == city.lower())
    names = {
        sid: _name_words(name)
        for sid, name in (await db.execute(select(School.id, School.name_i18n).where(School.id.in_(in_city)))).all()
    }
    pinned = [
        (loc.school_id, (loc.lat, loc.lng))
        for loc in (
            await db.execute(
                select(SchoolLocation).where(SchoolLocation.lat.is_not(None), SchoolLocation.school_id.in_(in_city))
            )
        ).scalars()
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
            if all(has_pinned_point(loc) for loc in locations):
                continue
            if register is None:
                register = await download_register()

            def _row(loc: SchoolLocation, school_id: int = school_id) -> dict[str, Any]:
                return {
                    "school_id": school_id,
                    "location_id": loc.id,
                    "address": _address(loc),
                    "old_point": (loc.lat, loc.lng) if loc.lat is not None and loc.lng is not None else None,
                }

            rows = [_row(loc) for loc in hidden]
            try:
                site = await read_site(school, fetch, fetch_script=fetch_script, bounds=bounds)
            except Exception:
                logger.exception("site-address could not read school %s's site", school_id)
                site = None
            if site is None:
                results += [{**row, "outcome": "site_not_read"} for row in rows]
                continue
            points, text = site
            page = register_lib.page_text(text)
            fixes = site_fixes(locations, points, page, register)
            for loc in locations:
                if loc.id not in fixes:
                    continue
                outcome, address = fixes[loc.id]
                row = {
                    **_row(loc),
                    "site_address": address.label,
                    "point": (address.lat, address.lng),
                    "site_points": points,
                    "outcome": outcome,
                }
                if row["old_point"]:
                    row["moved_m"] = round(_distance_m(row["old_point"], row["point"]))
                if any(
                    other != school_id
                    and not (names.get(other, set()) & names.get(school_id, set()))
                    and _distance_m(row["point"], spot) <= SAME_BUILDING_M
                    for other, spot in pinned
                ):
                    row["outcome"] = "other_school_here"
                results.append(row)
            hidden = [loc for loc in hidden if loc.id not in fixes]
            rows = [row for row in rows if row["location_id"] not in fixes]
            if not hidden:
                continue
            listed = [a for loc in hidden if (a := map_feature_address(loc))]
            found = site_addresses(points, page, register, listed)
            shown = [
                (fixes[loc.id][1].lat, fixes[loc.id][1].lng) if loc.id in fixes else (loc.lat, loc.lng)
                for loc in locations
                if (loc.id in fixes or not needs_pin(loc)) and loc.lat is not None
            ]
            outcome, extra = "placed", {}
            if not found:
                outcome = "no_site_address"
            elif len(found) > 1:
                outcome, extra = "several_site_addresses", {"site_addresses": [p.label for p, _ in found.values()]}
            else:
                ((address, feature_address),) = found.values()
                point = (address.lat, address.lng)
                extra = {"site_address": address.label, "point": point, "site_points": points}
                if feature_address:
                    extra["map_feature_address"] = feature_address
                if any(_distance_m(point, other) <= NEAR_M for other in shown):
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
        # Two unrelated schools whose sites give one address: which one is there is unclear.
        placed = [r for r in results if r["outcome"] in FIXED]
        for row in placed:
            if any(
                other["school_id"] != row["school_id"]
                and not (names.get(other["school_id"], set()) & names.get(row["school_id"], set()))
                and _distance_m(row["point"], other["point"]) <= SAME_BUILDING_M
                for other in placed
            ):
                row["outcome"] = "other_school_here"
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
            if result["outcome"] in FIXED:
                fixed = result["outcome"]
                result["outcome"] = await write_pin(
                    db,
                    result["location_id"],
                    result["point"],
                    provider="school_site",
                    method="site_address",
                    tag=PIN_TAG,
                    evidence={
                        "site_address": result["site_address"],
                        "site_points": result["site_points"],
                        **({"map_feature_address": a} if (a := result.get("map_feature_address")) else {}),
                    },
                    unchanged_from=result["old_point"],
                    address=result["site_address"],
                    # The address is the site's: shared-site checks must not count it as
                    # registry evidence. Not READDRESSED_TAG: that re-geocodes the location.
                    extra_tags=(WEBSITE_CONTACT_ADDRESS_TAG,),
                    shown=fixed != "placed",
                )
                if result["outcome"] == "placed":
                    result["outcome"] = fixed
    outcomes: dict[str, int] = {}
    for result in results:
        outcomes[result["outcome"]] = outcomes.get(result["outcome"], 0) + 1
    return {"apply": apply, "locations": len(results), "outcomes": outcomes, "results": results}
