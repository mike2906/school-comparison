"""Register-pins stage: a hidden Sofia location is pinned on its official address point."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import School, SchoolLocation
from app.scrapers.stages import register_pins as stage
from app.services.geocoding.bg import sofia_address_register as reg

HOME = "https://school.example/"
REGISTER = reg.read_register(
    """rn;region;settlement;lareaunit;block;street;streetnum;entrance;n;e
11045;Витоша;гр. София;"";"";ул. Ветрушка;5А;"";42.665715;23.263970
"""
)
OLD = (42.66, 23.27)


def _school(**kwargs):
    defaults = dict(
        name_i18n={"bg": "ЧДГ Звездичка", "en": "Zvezdichka"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
    )
    return School(**(defaults | kwargs))


def _hidden(school_id, address='район Витоша, ул. "Ветрушка" № 5а'):
    return SchoolLocation(
        school_id=school_id,
        address_i18n={"bg": address},
        lat=OLD[0],
        lng=OLD[1],
        geocode_meta={"precision": "approximate", "method": "nominatim_fallback"},
        location_tags=["coords_source=nominatim"],
    )


def _fetcher(pages):
    async def fetch(url):
        return (url, pages[url]) if url in pages else None

    return fetch


async def _add(db, school, *locations):
    db.add(school)
    await db.flush()
    for location in locations:
        location.school_id = school.id
        db.add(location)
    await db.commit()


@pytest.mark.asyncio
async def test_a_hidden_location_is_pinned_on_its_register_point_and_a_dry_run_writes_nothing(db_session):
    await _add(db_session, _school(), _hidden(None))
    report = await stage.run_register_pins(db_session, register=REGISTER, fetch=_fetcher({}))
    assert report["outcomes"] == {"placed": 1}
    assert report["results"][0]["moved_m"] > 0
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    assert row.geocode_meta["precision"] == "approximate"

    report = await stage.run_register_pins(db_session, register=REGISTER, fetch=_fetcher({}), apply=True)
    assert report["outcomes"] == {"placed": 1}
    await db_session.refresh(row)
    assert (row.lat, row.lng) == (42.665715, 23.26397)
    assert row.geocode_meta["precision"] == "exact" and row.geocode_meta["method"] == "address_register"
    assert row.location_tags == [stage.PIN_TAG]


@pytest.mark.asyncio
async def test_an_address_not_in_the_register_stays_hidden(db_session):
    await _add(db_session, _school(), _hidden(None, address='ул. "Ветрушка" № 7'))
    report = await stage.run_register_pins(db_session, register=REGISTER, fetch=_fetcher({}), apply=True)
    assert report["outcomes"] == {"not_in_register": 1}
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    assert row.geocode_meta["precision"] == "approximate"


@pytest.mark.asyncio
async def test_a_school_whose_website_maps_it_elsewhere_or_cannot_be_read_stays_hidden(db_session):
    await _add(db_session, _school(website_url=HOME, scrape_status="validated"), _hidden(None))
    far = '<a href="https://maps.google.com/?q=42.7000,23.3200">map</a>'
    report = await stage.run_register_pins(db_session, register=REGISTER, fetch=_fetcher({HOME: far}), apply=True)
    assert report["outcomes"] == {"website_map_elsewhere": 1}
    report = await stage.run_register_pins(db_session, register=REGISTER, fetch=_fetcher({}))
    assert report["outcomes"] == {"site_not_read": 1}
    # The site's map at the register point does not hold it back.
    near = '<a href="https://maps.google.com/?q=42.66575,23.26400">map</a>'
    report = await stage.run_register_pins(db_session, register=REGISTER, fetch=_fetcher({HOME: near}))
    assert report["outcomes"] == {"placed": 1}


@pytest.mark.asyncio
async def test_a_pinned_location_is_left_alone(db_session):
    pinned = _hidden(None)
    pinned.geocode_meta = {"precision": "exact"}
    await _add(db_session, _school(), pinned)
    report = await stage.run_register_pins(db_session, register=REGISTER, fetch=_fetcher({}), apply=True)
    assert report["locations"] == 0


@pytest.mark.asyncio
async def test_only_sofia_has_a_register(db_session):
    with pytest.raises(ValueError):
        await stage.run_register_pins(db_session, city="plovdiv", register=REGISTER, fetch=_fetcher({}))


@pytest.mark.asyncio
async def test_a_pin_moving_kilometres_needs_our_district_to_agree(db_session):
    far = _hidden(None, address='ул. "Ветрушка" № 5а')
    far.lat, far.lng = 42.70, 23.32  # about 6 km away
    await _add(db_session, _school(), far)
    report = await stage.run_register_pins(db_session, register=REGISTER, fetch=_fetcher({}))
    assert report["outcomes"] == {"moved_far": 1}
    far.address_i18n = {"bg": 'район Витоша, ул. "Ветрушка" № 5а'}
    await db_session.commit()
    report = await stage.run_register_pins(db_session, register=REGISTER, fetch=_fetcher({}))
    assert report["outcomes"] == {"placed": 1}
