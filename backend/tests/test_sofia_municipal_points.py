"""Matching Sofia locations to the municipality's official points."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import School, SchoolLocation
from app.services.geocoding.service import GeocodingService
from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG
from scripts.import_sofia_municipal_points import (
    Point,
    Row,
    _canonical_district,
    _shares_point_with_other_address,
    addresses_agree,
    apply_match,
    building_suffix,
    classify,
    name_key,
)


def _point(name, address, lat=42.7, lng=23.3, kind="kindergarten", district="Младост"):
    return Point(kind=kind, name=name, address=address, district=district, lat=lat, lng=lng)


def _classify(name, address, points):
    location = SimpleNamespace(address_i18n={"bg": address})
    school = SimpleNamespace(name_i18n={"bg": name})
    return classify(location, school, points)


def test_name_key_reads_kind_and_number():
    assert name_key("ДГ №82 Джани Родари (с яслени групи)") == ("kindergarten", "82")
    assert name_key("СДЯ №28 ") == ("nursery", "28")
    assert name_key('127. СУ "Иван Николаевич Денкоглу"') == ("school", "127")
    assert name_key("Първа английска езикова гимназия") is None


@pytest.mark.parametrize(
    ("ours", "theirs", "agree"),
    [
        ('гр. София, ул. "Беласица", №26', 'ул. "Беласица" № 26', True),
        # A number in the street name is not the house number.
        ('гр. София, ул. "8-ми март", №15', 'ул. "8-ми март" № 28', False),
        ('гр. София, ул. "8-ми март", №15', 'ул. "8-ми март" № 15', True),
        # The complex number (Младост 4) is not the house number.
        (
            'гр. София, ж.к. "Младост-4", ул. "Детска мечта" № 22',
            'ж.к. "Младост 4", ул. "Детска мечта" № 20',
            False,
        ),
        # Numbered streets have no words; the numbers must be equal.
        ("гр. София, ул. 210 № 27 - II м. р.", "ул. 210 № 27 - II м. р.", True),
        ("гр. София, ул. 210 № 27 - II м. р.", "ул. 208 № 17 - II м. р.", False),
        # A block on one side only may be another building of the complex.
        ("гр. София, ж.к. Бели брези, бл. 6", "ж.к. Бели брези", False),
        ("гр. София, ж.к. Бели брези,", "ж.к. Бели брези", True),
        ('бул."АЛЕКСАНДЪР СТАМБОЛИЙСКИ" №125-А', 'бул. "Александър Стамболийски" № 125А', True),
    ],
)
def test_addresses_agree(ours, theirs, agree):
    assert addresses_agree(ours, theirs) is agree


def test_building_suffix_treats_hourly_care_as_main_building():
    assert building_suffix("ДГ №65 Слънчево детство - почасова организация") == ""
    assert building_suffix("ДГ №6 Вълшебен свят - сграда 2") == "сграда 2"
    assert building_suffix("ДГ №6 Вълшебен свят (с яслени групи)") == ""


def test_classify_pairs_buildings_at_the_same_address():
    main = _point("ДГ №65 Слънчево детство (с яслени групи)", 'ул. "Александър Момчев" № 2')
    second = _point("ДГ №65 Слънчево детство - сграда 2", 'ул. "Александър Момчев" № 2', lat=42.71)
    points = [main, second]

    row = _classify("ДГ №65 Слънчево детство - почасова организация", 'ул. "Александър Момчев", № 2', points)
    assert (row.status, row.point) == ("match", main)
    row = _classify("ДГ №65 Слънчево детство - сграда 2", 'ул. "Александър Момчев", № 2', points)
    assert (row.status, row.point) == ("match", second)


def test_classify_leaves_differently_named_buildings_without_a_number_for_review():
    point = _point("ДГ №142  - сграда бл. 19", 'ж.к. "Лагера"')
    row = _classify("ДГ №142  - сграда ул. Хайдушка поляна", 'гр. София, ж.к. "Лагера"', [point])
    assert row.status == "ambiguous"


def test_classify_unnumbered_school_needs_matching_name_words():
    aeg = _point("Първа английска езикова гимназия", 'гр. София, бул. „Княз Александър Дондуков“ № 60', kind="school")
    ou = _point('112. ОУ "Стоян Заимов"', "БУЛ. КНЯЗ АЛЕКСАНДЪР ДОНДУКОВ № 60", kind="school")
    row = _classify("Първа английска езикова гимназия", 'бул. "Дондуков" № 60', [aeg, ou])
    assert (row.status, row.point) == ("match", aeg)


def test_classify_reports_a_different_address_as_name_only():
    point = _point("ДГ №24 Надежда (с яслени групи)", 'ул. "Kумановски бой" № 16')
    row = _classify("ДГ №24 Надежда - сграда 2", 'гр. София, ул. "Царевец", №34', [point])
    assert row.status == "name_only"


def test_point_copied_onto_another_address_is_detected():
    main = _point("ДГ №102 Кременица", 'кв. Курило, ул. "Кременица" № 18')
    copy = _point("ДГ №102 Кременица - сграда кв. Гниляне", "Гниляне")
    other = _point("ДГ №1 Червената шапчица", 'ул. "Брегалница" № 48', lat=42.69)
    assert _shares_point_with_other_address(copy, [main, copy, other])
    assert not _shares_point_with_other_address(other, [main, copy, other])


def test_canonical_district_uses_stored_spellings():
    assert _canonical_district("Подуене") == "Подуяне"
    assert _canonical_district("Студентски") == "Студентски град"
    assert _canonical_district(" Младост ") == "Младост"
    assert _canonical_district(None) is None


async def _location(db_session, **kwargs):
    school = School(
        name_i18n={"bg": "ДГ №4 Слънчо"},
        country_code="bg",
        city="sofia",
        school_type="state",
        education_level="kindergarten",
    )
    db_session.add(school)
    await db_session.flush()
    location = SchoolLocation(
        school_id=school.id, address_i18n={"bg": 'ул. "Ела" № 6'}, is_primary=True, **kwargs
    )
    db_session.add(location)
    await db_session.commit()
    return school, location


@pytest.mark.asyncio
async def test_apply_match_writes_point_district_and_tag(db_session):
    school, location = await _location(
        db_session,
        lat=42.70,
        lng=23.28,
        location_tags=["source=kg_sofia_bg", "coords_source=nominatim_approximate"],
    )
    point = _point("ДГ №4 Слънчо", "Ела № 6", lat=42.6812, lng=23.2012, district="Витоша")

    assert await apply_match(db_session, Row(location, school, "match", point))

    assert (location.lat, location.lng) == (42.6812, 23.2012)
    assert location.district == "Витоша"
    assert location.location_tags == ["source=kg_sofia_bg", OFFICIAL_COORDS_TAG]
    assert location.geocode_meta["precision"] == "exact"


@pytest.mark.asyncio
async def test_forced_geocode_keeps_official_point(db_session):
    _, location = await _location(
        db_session, lat=42.6812, lng=23.2012, location_tags=[OFFICIAL_COORDS_TAG]
    )
    provider = AsyncMock()
    service = GeocodingService(db=db_session, provider=provider)

    result = await service.geocode_location(location, force=True)

    assert (result.success, result.provider) == (True, "cached")
    provider.geocode.assert_not_awaited()
    assert (location.lat, location.lng) == (42.6812, 23.2012)


@pytest.mark.asyncio
async def test_forced_geocode_keeps_hand_corrected_pin(db_session):
    meta = {"status": "accepted", "method": "manual_fix", "manual_fix": {"source": "OSM"}}
    _, location = await _location(db_session, lat=42.6474, lng=23.3564, geocode_meta=meta)
    provider = AsyncMock()
    service = GeocodingService(db=db_session, provider=provider)

    result = await service.geocode_location(location, force=True)

    assert (result.success, result.provider) == (True, "cached")
    provider.geocode.assert_not_awaited()
    assert (location.lat, location.lng) == (42.6474, 23.3564)
    assert location.geocode_meta == meta


@pytest.mark.asyncio
async def test_fill_districts_uses_exact_pins_only(db_session, monkeypatch):
    from scripts import import_sofia_municipal_points as script

    _, exact = await _location(
        db_session, lat=42.6509, lng=23.3315, geocode_meta={"precision": "exact"}
    )
    _, approximate = await _location(
        db_session, lat=42.66, lng=23.34, geocode_meta={"precision": "approximate"}
    )
    monkeypatch.setattr(script, "district_at", AsyncMock(return_value="Лозенец"))

    await script.fill_districts(db_session, apply=True)

    assert exact.district == "Лозенец"
    assert approximate.district is None
    script.district_at.assert_awaited_once()
