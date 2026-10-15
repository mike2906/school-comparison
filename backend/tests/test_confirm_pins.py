"""Confirm-pins stage: a hidden point at our exact address is published. Fetches and Nominatim are stubbed."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import School, SchoolLocation
from app.scrapers.stages import confirm_pins as stage

HOME = "https://school.example/"
POINT = (42.65174, 23.33425)


def _school(**kwargs):
    defaults = dict(
        name_i18n={"bg": "Детска градина", "en": "Kindergarten"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
    )
    return School(**(defaults | kwargs))


def _hidden(school_id, address='ул. "Яна Язова" № 6', point=POINT):
    return SchoolLocation(
        school_id=school_id,
        address_i18n={"bg": address},
        lat=point[0],
        lng=point[1],
        geocode_meta={"precision": "approximate", "method": "geojson_name_match"},
        location_tags=["coords_source=geojson"],
    )


def _reverse(parts):
    async def reverse(lat, lng):
        return parts

    return reverse


def _fetcher(pages):
    async def fetch(url):
        return (url, pages[url]) if url in pages else None

    return fetch


AT_OURS = {"road": "Яна Язова", "house_number": "6", "city": "София"}


@pytest.mark.asyncio
async def test_a_hidden_point_at_our_house_is_published_and_a_dry_run_writes_nothing(db_session):
    school = _school()
    db_session.add(school)
    await db_session.flush()
    location = _hidden(school.id)
    db_session.add(location)
    await db_session.commit()

    report = await stage.run_confirm_pins(db_session, fetch=_fetcher({}), reverse=_reverse(AT_OURS))
    assert report["outcomes"] == {"placed": 1}
    await db_session.refresh(location)
    assert location.geocode_meta["precision"] == "approximate"

    report = await stage.run_confirm_pins(db_session, fetch=_fetcher({}), reverse=_reverse(AT_OURS), apply=True)
    assert report["outcomes"] == {"placed": 1}
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    assert (row.lat, row.lng) == POINT
    assert row.geocode_meta["precision"] == "exact" and row.geocode_meta["method"] == "reverse_confirmed"
    assert row.location_tags == [stage.PIN_TAG]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "parts",
    [
        {"road": "Яна Язова", "house_number": "8", "city": "София"},  # the neighbour
        {"road": "Яна Язова", "city": "София"},  # street only
        {"road": "Бигла", "house_number": "6", "city": "София"},
    ],
)
async def test_a_point_not_at_our_house_stays_hidden(db_session, parts):
    school = _school()
    db_session.add(school)
    await db_session.flush()
    db_session.add(_hidden(school.id))
    await db_session.commit()
    report = await stage.run_confirm_pins(db_session, fetch=_fetcher({}), reverse=_reverse(parts), apply=True)
    assert report["outcomes"] == {"not_at_address": 1}


@pytest.mark.asyncio
async def test_a_school_whose_website_maps_it_elsewhere_stays_hidden(db_session):
    school = _school(website_url=HOME, scrape_status="validated")
    db_session.add(school)
    await db_session.flush()
    db_session.add(_hidden(school.id))
    await db_session.commit()
    far = '<a href="https://maps.google.com/?q=42.7000,23.3200">map</a>'
    report = await stage.run_confirm_pins(db_session, fetch=_fetcher({HOME: far}), reverse=_reverse(AT_OURS))
    assert report["outcomes"] == {"website_map_elsewhere": 1}
    # A website map at our building does not hold it back.
    near = '<a href="https://maps.google.com/?q=42.65180,23.33420">map</a>'
    report = await stage.run_confirm_pins(db_session, fetch=_fetcher({HOME: near}), reverse=_reverse(AT_OURS))
    assert report["outcomes"] == {"placed": 1}
