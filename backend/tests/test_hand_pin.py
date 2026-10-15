"""Hand pin: one hidden location at a checked point, kept as a hand correction."""

from __future__ import annotations

import pytest

from app.models import School, SchoolLocation
from app.scrapers.stages.hand_pin import set_hand_pin
from app.services.geocoding.write_gate import has_pinned_point


async def _location(db, **kwargs):
    school = School(
        name_i18n={"bg": "ЧСУ Дени Дидро"},
        country_code="bg",
        school_type="private",
        education_level="school",
        city="sofia",
    )
    db.add(school)
    await db.flush()
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Майор Коста Паница"'},
        district="Банкя",
        lat=42.7065,
        lng=23.1435,
        geocode_meta={"precision": "approximate"},
        location_tags=["coords_source=geojson", "source=moe_registry"],
        **kwargs,
    )
    db.add(location)
    await db.commit()
    return location.id


@pytest.mark.asyncio
async def test_a_hand_pin_places_a_hidden_location_and_keeps_what_it_replaced(db_session):
    location_id = await _location(db_session)
    point = (42.703233, 23.135833)
    dry = await set_hand_pin(db_session, location_id, point, note="site map", address="гр. Банкя, ул. Тенев баир 2")
    assert dry["outcome"] == "placed"
    assert (await db_session.get(SchoolLocation, location_id)).geocode_meta["precision"] == "approximate"

    await set_hand_pin(db_session, location_id, point, note="site map", address="гр. Банкя, ул. Тенев баир 2", apply=True)
    row = await db_session.get(SchoolLocation, location_id)
    await db_session.refresh(row)
    assert (row.lat, row.lng) == point and row.district is None
    assert row.address_i18n == {"bg": "гр. Банкя, ул. Тенев баир 2"}
    assert row.geocode_meta["precision"] == "exact" and has_pinned_point(row)
    assert row.geocode_meta["manual_fix"]["previous"]["address_i18n"] == {"bg": 'ул. "Майор Коста Паница"'}
    assert row.location_tags == ["source=moe_registry"]

    again = await set_hand_pin(db_session, location_id, (42.7, 23.13), note="x", apply=True)
    assert again["outcome"] == "already_shown"


@pytest.mark.asyncio
async def test_a_hand_pin_outside_the_city_or_without_a_note_is_refused(db_session):
    location_id = await _location(db_session)
    report = await set_hand_pin(db_session, location_id, (41.0, 25.0), note="typo", apply=True)
    assert report["outcome"] == "outside_city"
    with pytest.raises(ValueError):
        await set_hand_pin(db_session, location_id, (42.70, 23.13), note=" ")
