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


@pytest.mark.asyncio
async def test_a_hand_pin_on_another_schools_exact_point_is_refused(db_session):
    location_id = await _location(db_session)
    other_id = await _location(db_session)
    await set_hand_pin(db_session, other_id, (42.70, 23.13), note="a", address="ул. Друга 1", apply=True)
    report = await set_hand_pin(db_session, location_id, (42.70, 23.13), note="b", apply=True)
    assert report["outcome"] == "point_held" and report["held_by_location_id"] == other_id


@pytest.mark.asyncio
async def test_a_register_location_moves_to_the_sites_campus(db_session):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.school import SchoolLocationAgeGroupShift
    from app.scrapers.campus_sync import CAMPUS_TAG, WEBSITE_CONTACT_ADDRESS_TAG
    from app.scrapers.stages.hand_pin import move_to_site_campus

    # 630: the register gives Waldorf the kindergarten's Горски пътник 44; its site says Хумболт 7.
    school = School(name_i18n={"bg": "ЧСУ Райнов"}, country_code="bg", school_type="private",
                    education_level="upper_secondary", city="sofia")
    db_session.add(school)
    await db_session.flush()
    register = SchoolLocation(school_id=school.id, address_i18n={"bg": 'ул. "Горски пътник" № 44'},
                              lat=42.6726, lng=23.3162, is_primary=True, location_tags=["source=moe_registry"])
    register.age_group_shifts = [SchoolLocationAgeGroupShift(age_group=g) for g in ("grade_1_4", "grade_8_12")]
    campus = SchoolLocation(school_id=school.id, address_i18n={"bg": 'ул. "Александър фон Хумболт" № 7'},
                            lat=42.6785, lng=23.357, is_primary=False,
                            location_tags=[WEBSITE_CONTACT_ADDRESS_TAG, CAMPUS_TAG])
    db_session.add_all([register, campus])
    await db_session.commit()
    register_id, campus_id, school_id = register.id, campus.id, school.id

    dry = await move_to_site_campus(db_session, register_id, campus_id, note="waldorf.bg/contacts")
    assert dry["outcome"] == "moved" and dry["point"] == (42.6785, 23.357)
    assert (await db_session.get(SchoolLocation, campus_id)) is not None  # nothing written
    # Only toward a site campus.
    assert (await move_to_site_campus(db_session, campus_id, register_id, note="x"))["outcome"] == "not_a_site_campus"

    await move_to_site_campus(db_session, register_id, campus_id, note="waldorf.bg/contacts", apply=True)
    db_session.expire_all()
    rows = (await db_session.execute(
        select(SchoolLocation).options(selectinload(SchoolLocation.age_group_shifts))
        .where(SchoolLocation.school_id == school_id)
    )).scalars().all()
    [moved] = rows
    assert moved.id == register_id and moved.is_primary
    assert moved.address_i18n == {"bg": 'ул. "Александър фон Хумболт" № 7'} and (moved.lat, moved.lng) == (42.6785, 23.357)
    assert sorted(moved.age_groups) == ["grade_1_4", "grade_8_12"]
    assert moved.geocode_meta["manual_fix"]["previous"]["address_i18n"] == {"bg": 'ул. "Горски пътник" № 44'}
    assert has_pinned_point(moved)


@pytest.mark.asyncio
async def test_a_shown_pin_moves_only_when_asked_and_to_the_sites_address(db_session):
    # 300 ESPA: the register's address is on no page of espa.bg, which gives its own buildings.
    location_id = await _location(db_session)
    first = (42.703233, 23.135833)
    await set_hand_pin(db_session, location_id, first, note="site map", address="ул. Тенев баир 2", apply=True)
    point = (42.6510561, 23.3378609)
    assert (await set_hand_pin(db_session, location_id, point, note="espa.bg footer", apply=True))["outcome"] == "already_shown"
    no_address = await set_hand_pin(db_session, location_id, point, note="espa.bg", move_shown=True, apply=True)
    assert no_address["outcome"] == "move_needs_address"
    moved = await set_hand_pin(
        db_session, location_id, point, note="espa.bg footer", address='ул. „Проф. Кирил Попов“ № 13', move_shown=True, apply=True
    )
    assert moved["outcome"] == "placed"
    row = await db_session.get(SchoolLocation, location_id)
    await db_session.refresh(row)
    assert (row.lat, row.lng) == point and row.address_i18n == {"bg": 'ул. „Проф. Кирил Попов“ № 13'}
    assert row.geocode_meta["manual_fix"]["previous"]["address_i18n"] == {"bg": "ул. Тенев баир 2"}
    # The chain keeps the register's address, so a re-import still finds the row.
    from app.scrapers.sources.base_adapter import _is_pinned_location

    assert _is_pinned_location(row, {"bg": 'ул. "Майор Коста Паница"'})
    same = await set_hand_pin(
        db_session, location_id, point, note="x", address="ул. Проф. Кирил Попов 13", move_shown=True
    )
    assert same["outcome"] == "move_needs_address"  # the address it already has
