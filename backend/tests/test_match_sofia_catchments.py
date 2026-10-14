"""Matching Sofia schools to their first-grade catchment ids."""
from types import SimpleNamespace

from scripts.match_sofia_catchments import apply_ids, match_schools, points_from_geojson


def _feature(gis_id, fullname, address, lng=23.33, lat=42.69):
    return {
        "type": "Feature",
        "properties": {"integrationid": gis_id, "fullname": fullname, "fulladdress": address},
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
    }


POINTS = {"features": [
    _feature("616", '1. СУ "Пенчо П. Славейков"', "УЛ. СТАРА ПЛАНИНА № 11"),
    # The same school's half-day entry has no catchment, so it never competes.
    _feature("680", '1. СУ "Пенчо П. Славейков" (полудневна организация)', 'ул. "Стара планина" № 11'),
    _feature("515", '78. СУ "Христо Смирненски"', 'ул. "Царибродска" № 5'),
]}


def _school(school_id, name, *addresses, level="school", lat=42.69, lng=23.33):
    locations = [SimpleNamespace(address_i18n={"bg": a}, lat=lat, lng=lng) for a in addresses]
    return SimpleNamespace(id=school_id, name_i18n={"bg": name}, locations=locations, education_level=level, admission_info={})


def test_points_keep_only_catchments_in_the_snapshot():
    points = points_from_geojson(POINTS, {"616", "515"})
    assert [p.gis_id for p in points] == ["616", "515"]
    assert (points[0].lat, points[0].lng) == (42.69, 23.33)


def test_schools_match_by_number_and_address():
    points = points_from_geojson(POINTS, {"616", "515"})
    first = _school(320, '1 СУ "Пенчо П. Славейков"', 'гр. София, ул. "Стара планина" 11')
    other = _school(321, '78 СУ "Никола Вапцаров"', "ул. Пиротска 5")  # another street and patron
    by_school, conflicts = match_schools([first, other], points)
    assert by_school == {320: ["616"]}
    assert conflicts == {}


def test_a_catchment_two_schools_match_goes_to_neither():
    points = points_from_geojson(POINTS, {"616"})
    one = _school(1, '1 СУ "Пенчо П. Славейков"', 'ул. "Стара планина" 11')
    two = _school(2, '1. средно училище "Пенчо Славейков"', 'ул. "Стара планина" № 11')
    by_school, conflicts = match_schools([one, two], points)
    assert by_school == {}
    assert conflicts == {"616": [1, 2]}


def test_ids_are_set_and_cleared_without_touching_other_keys():
    kindergarten = SimpleNamespace(admission_info={"cutoffs": [1], "catchment_ids": {"bg-sofia-gis": ["171"]}})
    assert apply_ids(kindergarten, []) is True
    assert kindergarten.admission_info == {"cutoffs": [1]}
    school = SimpleNamespace(admission_info={"catchment_ids": {"other-city": ["9"]}})
    assert apply_ids(school, ["616"]) is True
    assert school.admission_info == {"catchment_ids": {"other-city": ["9"], "bg-sofia-gis": ["616"]}}
    assert apply_ids(school, ["616"]) is False


def test_number_and_patron_match_when_addresses_are_spelled_differently():
    points = points_from_geojson(POINTS, {"616", "515"})
    near = _school(255, '78 СУ "Христо Смирненски"', "ж.к. Илинден, бл. 4", lat=42.695, lng=23.335)
    assert match_schools([near], points)[0] == {255: ["515"]}
    far = _school(256, '78 СУ "Христо Смирненски"', "ж.к. Илинден, бл. 4", lat=42.75, lng=23.40)
    assert match_schools([far], points)[0] == {}
    other_patron = _school(257, '78 СУ "Никола Вапцаров"', "ж.к. Илинден, бл. 4")
    assert match_schools([other_patron], points)[0] == {}


def test_a_catchment_with_several_buildings_still_matches_by_name():
    buildings = {"features": [
        _feature("608", '141. ОУ "Народни будители"', "ж.к. Люлин, бл. 1", lng=23.25, lat=42.71),
        _feature("608", '141. ОУ "Народни будители" - сграда 2', "ж.к. Люлин, бл. 9", lng=23.26, lat=42.72),
    ]}
    points = points_from_geojson(buildings, {"608"})
    school = _school(313, '141 ОУ "Народни будители"', "ул. 1 № 1", lat=42.711, lng=23.251)
    assert match_schools([school], points)[0] == {313: ["608"]}
