"""Website-pins stage: reading map points, the address check, and what --apply writes.

Page fetches and Nominatim are stubbed."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import School, SchoolLocation
from app.scrapers.stages import website_pins as stage
from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG

HOME = "https://school.example/"
CONTACT = "https://school.example/kontakti"
EMBED = (
    "https://www.google.com/maps/embed?pb=!1m18!1m12!1m3!1d2932.8!2d23.2810!3d42.6660"
    "!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!3m3!1m2!1s0x0%3A0x0!2z!5e0!3m2!1sbg!2sbg!4v1"
)
POINT = (42.6661, 23.28123)
SOFIA = {"south": 42.50, "west": 23.10, "north": 42.86, "east": 23.60}


def _at(road, number, town="София", key="city"):
    return {"road": road, "house_number": number, key: town}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (EMBED, (42.6660, 23.2810)),
        ("https://www.google.com/maps/place/X/@42.66613,23.28123,17z/data=!3m1!4b1!4m6!3m5!3d42.66614!4d23.28124", (42.66614, 23.28124)),
        ("https://maps.google.com/?q=42.66613,23.28123", (42.66613, 23.28123)),
        ("https://www.google.com/maps/@42.66613,23.28123,17z", (42.66613, 23.28123)),
        ("https://www.openstreetmap.org/?mlat=42.66613&mlon=23.28123#map=18/42.66613/23.28123", (42.66613, 23.28123)),
        ("https://www.openstreetmap.org/#map=18/42.66613/23.28123", (42.66613, 23.28123)),
        # Not a map: a search page, another site with numbers, a map with no point.
        ("https://www.google.com/search?q=42.66613,23.28123", None),
        ("https://google.com/search?q=42.66613,23.28123", None),
        ("https://maps.google.com.evil.example/?q=42.66613,23.28123", None),
        ("https://school.example/page?ll=42.66613,23.28123", None),
        ("https://maps.google.com/?q=Kazbek+55", None),
    ],
)
def test_link_point(url, expected):
    assert stage._link_point(url) == expected


def test_map_points_drops_the_same_building_drawn_twice():
    html = (
        f'<iframe src="{EMBED}"></iframe>'
        '<a href="https://maps.google.com/?q=42.66605,23.28105">map</a>'
        '<a href="https://maps.google.com/?q=42.7000,23.3200">branch</a>'
    )
    assert stage.map_points(html, HOME) == [(42.66605, 23.28105), (42.70, 23.32)]


def _reverse(addresses):
    async def reverse(lat, lng):
        return addresses.get((lat, lng))

    return reverse


@pytest.mark.asyncio
async def test_place_takes_the_one_point_osm_puts_at_the_address():
    reverse = _reverse(
        {
            (42.6661, 23.28123): _at("Казбек", "55"),
            (42.70, 23.32): _at("Казбек", "12"),
        }
    )
    found = await stage.place('ул. "Казбек" № 55, партер', [(42.70, 23.32), POINT], reverse, SOFIA, "софия")
    assert found["outcome"] == "placed" and found["point"] == POINT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("parts", "outcome"),
    [
        (_at("Казбек", "57"), "no_match"),  # the neighbour
        (_at("Бигла", "55"), "no_match"),  # another street
        ({"road": "Казбек", "city": "София"}, "no_match"),  # OSM has no number there
        (None, "no_match"),
        (_at("Казбек", "55", "Банкя", "town"), "no_match"),  # namesake street in another town
    ],
)
async def test_place_needs_town_street_and_house_number(parts, outcome):
    found = await stage.place('ул. "Казбек" № 55', [POINT], _reverse({POINT: parts}), SOFIA, "софия")
    assert found["outcome"] == outcome and found["point"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("address", "parts", "outcome"),
    [
        # A block or a neighbour's letter is not our house number.
        ('ул. "Казбек" № 5, бл. 12', _at("Казбек", "12"), "no_match"),
        ('ж.к. Люлин 5, ул. "Ловеч" № 3, до бл. 525', _at("Ловеч", "525"), "no_match"),
        ('ул. "Казбек" № 55А', _at("Казбек", "55Б"), "no_match"),
        ('ул. "Казбек" № 55А', _at("Казбек", "55а"), "placed"),
        # An address naming its own town needs that town, not the city.
        ('гр. Банкя, ул. "Васил Левски" № 10', _at("Васил Левски", "10"), "no_match"),
        ('гр. Банкя, ул. "Васил Левски" № 10', _at("Васил Левски", "10", "Банкя", "town"), "placed"),
        ('гр. София - 1000, ул. "Казбек" № 55', _at("Казбек", "55"), "placed"),
        ('гр. София ж.к. Люлин, ул. "Казбек" № 55', _at("Казбек", "55"), "placed"),
        ('с. Казичене, ул. "Христо Ботев" 4', _at("Христо Ботев", "4", "Казичене", "village"), "placed"),
    ],
)
async def test_place_exact_house_and_town(address, parts, outcome):
    found = await stage.place(address, [POINT], _reverse({POINT: parts}), SOFIA, "софия")
    assert found["outcome"] == outcome


@pytest.mark.asyncio
async def test_place_leaves_two_matching_points_and_points_outside_the_city():
    far = (42.6700, 23.2900)
    reverse = _reverse({POINT: _at("Казбек", "55"), far: _at("Казбек", "55")})
    assert (await stage.place("ул. Казбек 55", [POINT, far], reverse, SOFIA, "софия"))["outcome"] == "several"
    bounds = {"south": 42.0, "west": 22.0, "north": 42.5, "east": 23.0}
    assert (await stage.place("ул. Казбек 55", [POINT], reverse, bounds, "софия"))["outcome"] == "no_match"
    assert (await stage.place("ул. Казбек 55", [], reverse, SOFIA, "софия"))["outcome"] == "no_points"


def _school(**kwargs):
    defaults = dict(
        name_i18n={"bg": "Детска градина", "en": "Kindergarten"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
        website_url=HOME,
        scrape_status="validated",
    )
    return School(**(defaults | kwargs))


def _fetcher(pages):
    async def fetch(url):
        return (url, pages[url]) if url in pages else None

    return fetch


@pytest.mark.asyncio
async def test_apply_pins_hidden_locations_only_and_dry_run_writes_nothing(db_session):
    school = _school()
    db_session.add(school)
    await db_session.flush()
    approximate = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Казбек" № 55'},
        lat=42.68,
        lng=23.30,
        geocode_meta={"precision": "approximate", "method": "geojson_name_match"},
        location_tags=["coords_source=geojson"],
    )
    official = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Казбек" № 55'},
        lat=42.6,
        lng=23.2,
        geocode_meta={"precision": "exact"},
        location_tags=[OFFICIAL_COORDS_TAG],
    )
    db_session.add_all([approximate, official])
    await db_session.commit()
    fetch = _fetcher({HOME: f'<a href="{CONTACT}">Контакти</a>', CONTACT: '<a href="https://maps.google.com/?q=42.6661,23.28123">map</a>'})
    reverse = _reverse({POINT: _at("Казбек", "55")})

    report = await stage.run_website_pins(db_session, school_ids=[school.id], fetch=fetch, reverse=reverse)
    assert report["outcomes"] == {"placed": 1}
    await db_session.refresh(approximate)
    assert (approximate.lat, approximate.geocode_meta["precision"]) == (42.68, "approximate")

    report = await stage.run_website_pins(db_session, school_ids=[school.id], fetch=fetch, reverse=reverse, apply=True)
    assert [r["location_id"] for r in report["results"]] == [approximate.id]
    rows = {loc.id: loc for loc in (await db_session.execute(select(SchoolLocation))).scalars()}
    assert (rows[approximate.id].lat, rows[approximate.id].lng) == POINT
    assert rows[approximate.id].geocode_meta["precision"] == "exact"
    assert rows[approximate.id].geocode_meta["method"] == "website_map_link"
    assert rows[approximate.id].location_tags == [stage.PIN_TAG]
    assert (rows[official.id].lat, rows[official.id].lng) == (42.6, 23.2)


@pytest.mark.asyncio
async def test_a_site_not_read_keeps_the_location(db_session):
    school = _school()
    db_session.add(school)
    await db_session.flush()
    db_session.add(SchoolLocation(school_id=school.id, address_i18n={"bg": "ул. Казбек 55"}))
    await db_session.commit()
    report = await stage.run_website_pins(db_session, school_ids=[school.id], fetch=_fetcher({}), reverse=_reverse({}), apply=True)
    assert report["outcomes"] == {"not_read": 1}


@pytest.mark.asyncio
async def test_a_city_without_bounds_is_refused(db_session):
    with pytest.raises(ValueError):
        await stage.run_website_pins(db_session, city="plovdiv", fetch=_fetcher({}), reverse=_reverse({}))
