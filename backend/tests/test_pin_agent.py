"""Pin-agent stage: the code checks on the model's pick, and what --apply writes.

Websites, Nominatim, OSM and the model are stubbed."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import School, SchoolLocation
from app.scrapers.stages import pin_agent as stage
from app.scrapers.stages.pin_agent import Candidate, Pick

SOFIA = {"south": 42.50, "west": 23.10, "north": 42.86, "east": 23.60}
HOME = "https://school.example/"
NPMG_POINT = (42.67046, 23.32978)


@pytest.mark.parametrize(
    ("school", "osm", "expected"),
    [
        ("Национална природо-математическа гимназия „Акад. Л. Чакалов“", "НПМГ „Акад. Любомир Чакалов“", True),
        ("138 СУ „Проф. Васил Златарски“", "138 СУЗИЕ Проф. Васил Златарски", True),
        ("138 СУ „Проф. Васил Златарски“", "38 ОУ Васил Априлов", False),
        ("ЧДГ Детски свят", "ДГ 12 Детски рай", False),
        ("138 СУ „Проф. Васил Златарски“", "ДГ 138 Звездица", False),
        ("ДГ 138", "ДГ № 138 Звездица", True),
        ("ЧДГ Детски свят", "Частна детска градина Детски свят", True),
    ],
)
def test_name_matches(school, osm, expected):
    assert stage.name_matches(school, osm) is expected


@pytest.mark.parametrize(
    ("theirs", "expected"),
    [
        ('ул. "Бигла" № 52', True),  # OSM numbers the next building
        ('ул. "Бигла" № 60', True),
        ('ул. "Бигла" № 61', False),
        ('ул. "Казбек" № 56', False),
        (None, False),
    ],
)
def test_on_our_street(theirs, expected):
    assert stage.on_our_street("ул. Бигла 56", theirs) is expected


def _check(candidate, candidates=None, other_pins=()):
    return stage.check_pick("НПМГ Акад. Чакалов", False, "ул. Бигла 56", candidate, candidates or [candidate], SOFIA, other_pins)


def test_check_pick():
    on_street = Candidate(point=NPMG_POINT, source="website_map", address='ул. "Бигла" № 52')
    assert _check(on_street) is None
    assert _check(Candidate(point=(41.0, 23.3), source="website_map", address='ул. "Бигла" № 52')) == "outside the city"
    assert _check(Candidate(point=NPMG_POINT, source="website_map", address='ул. "Бигла" № 52', town_ok=False)) == "another town"
    assert _check(on_street, other_pins=[(42.67050, 23.32980)]) == "another school is pinned there"
    named = Candidate(point=(42.68, 23.33), source="osm_school", name="НПМГ Чакалов")
    assert _check(named) is None
    twin = Candidate(point=(42.69, 23.34), source="osm_school", name="НПМГ Чакалов филиал")
    assert _check(named, [named, twin]) == "several OSM features have this name"
    elsewhere = Candidate(point=(42.68, 23.33), source="osm_school", name="НПМГ Чакалов", address='ул. "Казбек" № 3')
    assert _check(elsewhere) == "OSM gives the named building another street"
    kindergarten = Candidate(point=(42.68, 23.33), source="osm_school", name="НПМГ Чакалов", amenity="kindergarten")
    assert _check(kindergarten) == "OSM maps another kind of institution there"
    website_named = Candidate(point=(42.68, 23.33), source="website_map", name="НПМГ", address='ул. "Казбек" № 3')
    assert _check(website_named) == "neither on our street nor named as this school"


def _school(**kwargs):
    defaults = dict(
        name_i18n={"bg": "НПМГ Акад. Чакалов", "en": "NPMG"},
        country_code="bg",
        school_type="state",
        education_level="secondary",
        city="sofia",
        website_url=HOME,
        scrape_status="validated",
    )
    return School(**(defaults | kwargs))


def _stubs(pick, osm=(), pages=None, reverse=None):
    async def fetch(url):
        return (url, (pages or {})[url]) if url in (pages or {}) else None

    async def rev(lat, lng):
        return (reverse or {}).get((lat, lng))

    async def lookup(lat, lng):
        return list(osm)

    messages = []

    async def call(message, school_id):
        messages.append(message)
        return pick

    return {"fetch": fetch, "reverse": rev, "lookup": lookup, "call": call}, messages


async def _seed(db_session):
    school = _school()
    db_session.add(school)
    await db_session.flush()
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": "ул. Бигла 56"},
        lat=42.66,
        lng=23.31,
        geocode_meta={"precision": "approximate", "method": "geojson_name_match"},
    )
    db_session.add(location)
    await db_session.commit()
    return school, location


@pytest.mark.asyncio
async def test_apply_writes_a_checked_pick_with_its_evidence(db_session):
    school, location = await _seed(db_session)
    pages = {HOME: f'<a href="https://maps.google.com/?q={NPMG_POINT[0]},{NPMG_POINT[1]}">map</a>'}
    stubs, messages = _stubs(
        Pick(candidate=1, sure=True, reason="website point on Бигла"),
        osm=[{"type": "node", "lat": 42.70, "lon": 23.35, "tags": {"name": "38 ОУ"}}],
        pages=pages,
        reverse={NPMG_POINT: {"road": "Бигла", "house_number": "52", "city": "София"}},
    )
    dry = await stage.run_pin_agent(db_session, school_ids=[school.id], **stubs)
    assert dry["outcomes"] == {"placed": 1}
    assert "1. Map point on the institution's website; OSM address there: ул. \"Бигла\" № 52" in messages[0]

    report = await stage.run_pin_agent(db_session, school_ids=[school.id], apply=True, **stubs)
    assert report["outcomes"] == {"placed": 1}
    stored = (await db_session.execute(select(SchoolLocation))).scalars().one()
    assert (stored.lat, stored.lng) == NPMG_POINT
    assert stored.geocode_meta["method"] == "agent_pick" and stored.geocode_meta["precision"] == "exact"
    assert stored.geocode_meta["evidence"]["reason"] == "website point on Бигла"
    assert stored.location_tags == [stage.PIN_TAG]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pick", "outcome"),
    [
        (Pick(candidate=0, sure=False, reason="none"), "none_fits"),
        (Pick(candidate=1, sure=False, reason="maybe"), "not_sure"),
        (Pick(candidate=7, sure=True, reason="made up"), "none_fits"),
        (Pick(candidate=1, sure=True, reason="near"), "refused"),  # 38 ОУ is not this school
    ],
)
async def test_unsure_invented_or_unchecked_picks_write_nothing(db_session, pick, outcome):
    school, location = await _seed(db_session)
    stubs, _ = _stubs(pick, osm=[{"type": "node", "lat": 42.70, "lon": 23.35, "tags": {"name": "38 ОУ"}}])
    report = await stage.run_pin_agent(db_session, school_ids=[school.id], apply=True, **stubs)
    assert report["outcomes"] == {outcome: 1}
    await db_session.refresh(location)
    assert (location.lat, location.geocode_meta["precision"]) == (42.66, "approximate")


@pytest.mark.asyncio
async def test_no_candidates_makes_no_model_call(db_session):
    school, _ = await _seed(db_session)
    stubs, messages = _stubs(Pick(candidate=1, sure=True, reason="x"))
    report = await stage.run_pin_agent(db_session, school_ids=[school.id], **stubs)
    assert report["outcomes"] == {"no_candidates": 1} and messages == []
