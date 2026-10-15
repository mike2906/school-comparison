"""Hand pin: place one hidden location at a point a person checked.

For the few schools no stage can place, where the evidence is the school's own (ЧСУ Дени
Дидро's site maps Тенев баир 2 but writes no street). The pin is a hand correction
(``geocode_meta.method`` = manual_fix): the write gate, the geocoder and a registry re-import
leave it alone, and the address and point it replaced are kept in ``manual_fix.previous``
(a re-import of that address finds this row). Only a hidden location (approximate or no
point) inside the city's bounds; default is a dry run.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School, SchoolLocation
from app.scrapers.stages.website_pins import needs_pin
from app.services.geocoding.bounds import get_city_bounds, point_in_bounds
from app.services.geocoding.write_gate import MANUAL_FIX_METHOD, OFFICIAL_COORDS_TAG, location_holding_point


async def set_hand_pin(
    db: AsyncSession,
    location_id: int,
    point: tuple[float, float],
    *,
    note: str,
    address: Optional[str] = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Pin the location at ``point`` (and give it ``address``); returns what was (or would be) done."""
    if not note.strip():
        raise ValueError("a hand pin needs a note saying what the point was checked against")
    address = (address or "").strip() or None
    location = await db.get(SchoolLocation, location_id)
    if location is None:
        return {"location_id": location_id, "outcome": "not_found"}
    await db.refresh(location, with_for_update=True)
    school = await db.get(School, location.school_id)
    bounds = get_city_bounds(school.country_code, school.city)
    previous = {
        "address_i18n": dict(location.address_i18n or {}),
        "district": location.district,
        "lat": location.lat,
        "lng": location.lng,
        "geocode_meta": location.geocode_meta,
    }
    report: dict[str, Any] = {
        "location_id": location_id,
        "school_id": location.school_id,
        "old_address": previous["address_i18n"],
        "old_point": (location.lat, location.lng),
        "address": address or previous["address_i18n"].get("bg"),
        "point": point,
    }
    if address:
        location.address_i18n = {"bg": address}
        location.district = None  # the old address's
    if not needs_pin(location):
        outcome = "already_shown"
    elif bounds is None:
        outcome = "no_city_bounds"  # the point cannot be checked
    elif not point_in_bounds(point[0], point[1], bounds):
        outcome = "outside_city"
    elif (holder := await location_holding_point(db, location, point[0], point[1])) is not None:
        outcome = "point_held"  # another address is pinned exactly there
        report["held_by_location_id"] = holder.id
    else:
        outcome = "placed"
    report["outcome"] = outcome
    if not apply or outcome != "placed":
        await db.rollback()  # releases the lock; nothing written
        return report
    location.lat, location.lng = point
    location.geocode_meta = {
        "method": MANUAL_FIX_METHOD,
        "provider": "manual",
        "precision": "exact",
        "formatted_address": (location.address_i18n or {}).get("bg"),
        "manual_fix": {"previous": previous, "note": note},
    }
    location.location_tags = [
        tag
        for tag in location.location_tags or []
        if not str(tag).startswith("coords_source=") or tag == OFFICIAL_COORDS_TAG
    ]
    db.add(location)
    await db.commit()
    return report
