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


async def move_to_site_campus(
    db: AsyncSession,
    location_id: int,
    campus_id: int,
    *,
    note: str,
    apply: bool = False,
) -> dict[str, Any]:
    """Give a shown register location its school's own site address, the site's campus location.

    For a register address the school's site gives to someone else (630 Waldorf: the register
    says Горски пътник 44, the kindergarten's building; waldorf.bg/contacts puts the school at
    Хумболт 7). Mike's rule (2026-10-15): the school's own site wins. The register location
    takes the campus's address and point as a hand correction (``manual_fix.previous`` keeps
    the register's, so a re-import finds the row), adds the campus's age groups, and the
    campus row goes: one building, one location, with the register's grades. Default is a
    dry run."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models import SchoolLocationAgeGroupShift
    from app.scrapers.campus_sync import CAMPUS_TAG

    if not note.strip():
        raise ValueError("a move needs a note saying where the site gives the address")
    rows = {
        row.id: row
        for row in (
            await db.execute(
                select(SchoolLocation)
                .options(selectinload(SchoolLocation.age_group_shifts))
                .where(SchoolLocation.id.in_([location_id, campus_id]))
                .with_for_update()
            )
        ).scalars()
    }
    location, campus = rows.get(location_id), rows.get(campus_id)
    report: dict[str, Any] = {"location_id": location_id, "campus_id": campus_id}
    if location is None or campus is None:
        outcome = "not_found"
    elif location.school_id != campus.school_id:
        outcome = "different_schools"
    elif CAMPUS_TAG not in (campus.location_tags or []):
        outcome = "not_a_site_campus"  # the evidence is the site's own address
    elif CAMPUS_TAG in (location.location_tags or []):
        outcome = "not_a_register_location"
    elif campus.lat is None or campus.lng is None or needs_pin(campus):
        outcome = "campus_not_pinned"
    else:
        outcome = "moved"
    report["outcome"] = outcome
    if outcome != "moved":
        await db.rollback()
        return report
    report.update(
        old_address=(location.address_i18n or {}).get("bg"),
        address=(campus.address_i18n or {}).get("bg"),
        point=(campus.lat, campus.lng),
        age_groups=sorted({link.age_group for link in location.age_group_shifts}
                          | {link.age_group for link in campus.age_group_shifts}),
    )
    if not apply:
        await db.rollback()
        return report
    previous = {
        "address_i18n": dict(location.address_i18n or {}),
        "district": location.district,
        "lat": location.lat,
        "lng": location.lng,
        "geocode_meta": location.geocode_meta,
    }
    have = {link.age_group for link in location.age_group_shifts}
    for link in campus.age_group_shifts:
        if link.age_group not in have:
            db.add(SchoolLocationAgeGroupShift(
                location_id=location.id, age_group=link.age_group, shift=link.shift,
                has_organised_groups=link.has_organised_groups,
            ))
    location.address_i18n = dict(campus.address_i18n or {})
    location.district = campus.district
    location.lat, location.lng = campus.lat, campus.lng
    location.geocode_meta = {
        "method": MANUAL_FIX_METHOD,
        "provider": "manual",
        "precision": "exact",
        "formatted_address": (campus.address_i18n or {}).get("bg"),
        "manual_fix": {"previous": previous, "note": note, "from_campus_location_id": campus.id},
    }
    location.location_tags = [
        tag
        for tag in location.location_tags or []
        if not str(tag).startswith("coords_source=") or tag == OFFICIAL_COORDS_TAG
    ]
    await db.delete(campus)
    await db.commit()
    return report
