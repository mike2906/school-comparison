"""Site-address stage: a hidden location takes the address its school's own site maps and names."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import School, SchoolLocation
from app.scrapers.stages import site_address as stage
from app.scrapers.stages import website_pins
from app.services.geocoding.bg import sofia_address_register as reg

HOME = "https://school.example/"
SCRIPT = "https://school.example/js/build.js"
REGISTER = reg.read_register(
    """rn;region;settlement;lareaunit;block;street;streetnum;entrance;n;e
116781;Банкя;гр. Банкя;"";"";ул. Тенев баир;2;"";42.703233;23.135833
116782;Банкя;гр. Банкя;"";"";ул. Тенев баир;4;"";42.703500;23.136200
11045;Витоша;гр. София;"";"";ул. Ветрушка;5А;"";42.665715;23.263970
"""
)
MAP_JS = "Component.GoogleMap.init({locations:[[42.703062,23.135729]]});"
CONTACT = '<p>2 Tenev Bair, Bankya, Sofia City Province, Bulgaria</p><script src="/js/build.js"></script>'


def _fetcher(pages):
    async def fetch(url):
        return (url, pages[url]) if url in pages else None

    return fetch


async def _school(db, *locations, **kwargs):
    school = School(
        **(
            dict(
                name_i18n={"bg": "ЧСУ Дени Дидро", "en": "Denis Diderot"},
                country_code="bg",
                school_type="private",
                education_level="school",
                city="sofia",
                website_url=HOME,
                scrape_status="validated",
            )
            | kwargs
        )
    )
    db.add(school)
    await db.flush()
    for location in locations:
        location.school_id = school.id
        db.add(location)
    await db.commit()
    return school


def _hidden(address='ул. "Майор Коста Паница"'):
    return SchoolLocation(
        address_i18n={"bg": address},
        district="Банкя",
        lat=42.7065,
        lng=23.1435,
        geocode_meta={"precision": "approximate"},
        location_tags=["coords_source=geojson"],
    )


def _shown(point):
    return SchoolLocation(
        address_i18n={"bg": "ул. Друга 1"},
        lat=point[0],
        lng=point[1],
        geocode_meta={"precision": "exact"},
        location_tags=[],
    )


@pytest.mark.asyncio
async def test_a_site_whose_script_map_and_text_give_a_register_address_moves_the_location_there(db_session):
    await _school(db_session, _hidden())
    pages = {HOME: CONTACT}
    report = await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher(pages), fetch_script=_fetcher({SCRIPT: MAP_JS})
    )
    assert report["outcomes"] == {"placed": 1}
    assert report["results"][0]["site_address"] == "гр. Банкя, ул. Тенев баир 2"

    report = await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher(pages), fetch_script=_fetcher({SCRIPT: MAP_JS}), apply=True
    )
    assert report["outcomes"] == {"placed": 1}
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    assert (row.lat, row.lng) == (42.703233, 23.135833)
    assert row.address_i18n == {"bg": "гр. Банкя, ул. Тенев баир 2"}
    assert row.geocode_meta["precision"] == "exact" and row.geocode_meta["method"] == "site_address"
    assert row.geocode_meta["evidence"]["previous_address_i18n"] == {"bg": 'ул. "Майор Коста Паница"'}
    assert row.district is None
    assert "address_source=website_contact" in row.location_tags
    assert "geocode=address_changed" not in row.location_tags  # that would re-geocode it

    # A re-import of the old registry address finds this row rather than adding it again.
    from app.scrapers.sources.base_adapter import _address_key, _address_keys

    assert _address_key({"bg": 'ул. "Майор Коста Паница"'}) in _address_keys(row)


@pytest.mark.asyncio
async def test_a_map_whose_address_the_site_does_not_name_gives_nothing(db_session):
    await _school(db_session, _hidden())
    pages = {HOME: '<p>Welcome</p><script src="/js/build.js"></script>'}
    report = await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher(pages), fetch_script=_fetcher({SCRIPT: MAP_JS})
    )
    assert report["outcomes"] == {"no_site_address": 1}


@pytest.mark.asyncio
async def test_the_site_address_of_a_shown_location_means_the_hidden_one_is_old(db_session):
    await _school(db_session, _hidden(), _shown((42.70325, 23.13584)))
    report = await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher({HOME: CONTACT}), fetch_script=_fetcher({SCRIPT: MAP_JS})
    )
    assert report["outcomes"] == {"already_shown": 1}


@pytest.mark.asyncio
async def test_an_unrelated_school_on_the_spot_or_two_hidden_locations_hold_it_back(db_session):
    await _school(db_session, _shown((42.70324, 23.13583)), name_i18n={"bg": "ДГ Друга", "en": "Other"})
    await _school(db_session, _hidden())
    report = await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher({HOME: CONTACT}), fetch_script=_fetcher({SCRIPT: MAP_JS})
    )
    assert report["outcomes"] == {"other_school_here": 1}


@pytest.mark.asyncio
async def test_two_hidden_locations_hold_it_back(db_session):
    await _school(db_session, _hidden(), _hidden("ул. Друга 3"))
    report = await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher({HOME: CONTACT}), fetch_script=_fetcher({SCRIPT: MAP_JS})
    )
    assert report["outcomes"] == {"several_hidden": 2}


@pytest.mark.asyncio
async def test_an_unreadable_site_changes_nothing(db_session):
    await _school(db_session, _hidden())
    report = await stage.run_site_address(db_session, register=REGISTER, fetch=_fetcher({}), fetch_script=_fetcher({}))
    assert report["outcomes"] == {"site_not_read": 1}


def test_script_points_read_map_code_within_the_city_only():
    bounds = {"south": 42.5, "north": 42.9, "west": 23.0, "east": 23.6}
    assert website_pins.script_points(MAP_JS, bounds) == [(42.703062, 23.135729)]
    assert website_pins.script_points("new google.maps.LatLng(42.70306, 23.13573)", bounds) == [(42.70306, 23.13573)]
    assert website_pins.script_points("{lat: 42.6657, lng: 23.2639}", bounds) == [(42.6657, 23.2639)]
    assert website_pins.script_points("{lat: 48.8566, lng: 2.3522}", bounds) == []  # Paris
    many = " ".join(f"[42.{60 + i}00, 23.3000]" for i in range(5))
    assert website_pins.script_points(many, bounds) == []  # a list of places, not the school


def test_register_near_finds_points_nearest_first():
    found = REGISTER.near((42.703062, 23.135729), 40)
    assert [p.label for p in found] == ["гр. Банкя, ул. Тенев баир 2"]


@pytest.mark.asyncio
async def test_two_schools_mapping_one_address_in_one_run_are_both_held_back(db_session):
    await _school(db_session, _hidden())
    await _school(db_session, _hidden(), name_i18n={"bg": "ЧДГ Друга", "en": "Other"}, website_url="https://other.example/")
    pages = {HOME: CONTACT, "https://other.example/": CONTACT.replace("/js/build.js", "https://other.example/js/build.js")}
    scripts = {SCRIPT: MAP_JS, "https://other.example/js/build.js": MAP_JS}
    report = await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher(pages), fetch_script=_fetcher(scripts)
    )
    assert report["outcomes"] == {"other_school_here": 2}


@pytest.mark.asyncio
async def test_an_address_only_in_the_map_script_counts_as_the_sites(db_session):
    """Дени Дидро's site is built in a script: the pages have no address text."""
    await _school(db_session, _hidden())
    pages = {HOME: '<p>Welcome</p><script src="/js/build.js"></script>'}
    script = MAP_JS + 'var contacts={address:"2 Tenev Bair, Bankya, Sofia City Province, Bulgaria"};'
    report = await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher(pages), fetch_script=_fetcher({SCRIPT: script})
    )
    assert report["outcomes"] == {"placed": 1}


@pytest.mark.asyncio
async def test_a_reimport_of_the_old_address_syncs_the_moved_row_without_the_old_district(db_session):
    from app.schemas.scraping import DiscoveredLocation
    from app.scrapers.sources.base_adapter import _match_source_rows

    await _school(db_session, _hidden())
    await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher({HOME: CONTACT}), fetch_script=_fetcher({SCRIPT: MAP_JS}), apply=True
    )
    moved = (await db_session.execute(select(SchoolLocation))).scalar_one()
    old = DiscoveredLocation(address_i18n={"bg": 'ул. "Майор Коста Паница"'}, district="Банкя")
    matched, new = _match_source_rows([moved], [old])
    assert new == [] and matched[0][0] is moved and matched[0][1].district is None

    # Another row still at that address is the one the source means.
    other = _hidden()
    matched, _ = _match_source_rows([other, moved], [old])
    assert matched[0][0] is other and matched[0][1].district == "Банкя"


@pytest.mark.asyncio
async def test_a_wrong_website_puts_the_registered_address_back_and_hides_the_pin(db_session):
    from app.scrapers.url_validator import clear_website_derived_locations

    school = await _school(db_session, _hidden())
    await stage.run_site_address(
        db_session, register=REGISTER, fetch=_fetcher({HOME: CONTACT}), fetch_script=_fetcher({SCRIPT: MAP_JS}), apply=True
    )
    await clear_website_derived_locations(db_session, school.id)
    await db_session.commit()
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    assert row.address_i18n == {"bg": 'ул. "Майор Коста Паница"'}
    assert row.lat is None and "coords_source=school_site_address" not in row.location_tags
    assert "address_source=website_contact" not in row.location_tags


LIDICE = reg.read_register(
    """rn;region;settlement;lareaunit;block;street;streetnum;entrance;n;e
87568;Слатина;гр. София;"";"";ул. Лидице;14;"";42.678350;23.362693
"""
)


def _old_address(method="geojson_name_match", point=(42.67838, 23.36281)):
    # Мечо Пух: our address is an old one; the EU map feature's and the site's are current.
    return SchoolLocation(
        address_i18n={"bg": 'ул. "Стара планина" № 16'},
        lat=point[0],
        lng=point[1],
        geocode_meta={"precision": "approximate", "method": method, "formatted_address": "УЛ. ЛИДИЦЕ 14, 1000 СТОЛИЧНА"},
        location_tags=["coords_source=geojson"],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, point, page, outcome",
    [
        ("geojson_name_match", (42.67838, 23.36281), "<p>кв. Гео Милев, ул. Лидице 14</p>", "placed"),
        # The site must name it too.
        ("geojson_name_match", (42.67838, 23.36281), "<p>кв. Гео Милев</p>", "no_site_address"),
        # Not a map feature's address.
        ("nominatim_fallback", (42.67838, 23.36281), "<p>кв. Гео Милев, ул. Лидице 14</p>", "no_site_address"),
        # A feature 2 km from its own address: not one to trust.
        ("geojson_name_match", (42.69638, 23.36281), "<p>кв. Гео Милев, ул. Лидице 14</p>", "no_site_address"),
    ],
)
async def test_a_map_feature_address_the_site_names_moves_the_location_there(db_session, method, point, page, outcome):
    await _school(db_session, _old_address(method, point), name_i18n={"bg": "ЧДГ Мечо Пух"})
    report = await stage.run_site_address(db_session, register=LIDICE, fetch=_fetcher({HOME: page}), apply=True)
    assert report["outcomes"] == {outcome: 1}
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    if outcome == "placed":
        assert (row.lat, row.lng) == (42.67835, 23.362693)
        assert row.address_i18n == {"bg": "гр. София, ул. Лидице 14"}
        assert row.geocode_meta["precision"] == "exact"
        assert row.geocode_meta["evidence"]["map_feature_address"] == "УЛ. ЛИДИЦЕ 14, 1000 СТОЛИЧНА"
    else:
        assert row.geocode_meta["precision"] == "approximate"


def test_a_map_feature_with_our_own_address_is_left_to_register_pins():
    location = _old_address()
    location.address_i18n = {"bg": 'ул. "Лидице" № 14'}
    assert stage.map_feature_address(location) is None


def test_a_rejected_or_overwritten_map_feature_is_not_a_source():
    rejected = _old_address()
    rejected.geocode_meta = {**rejected.geocode_meta, "status": "rejected"}
    assert stage.map_feature_address(rejected) is None
    moved = _old_address()
    moved.geocode_meta = {**moved.geocode_meta, "status": "accepted", "candidate": {"lat": 42.0, "lng": 23.0}}
    assert stage.map_feature_address(moved) is None
    assert stage.map_feature_address(_old_address()) == ("УЛ. ЛИДИЦЕ 14, 1000 СТОЛИЧНА", (42.67838, 23.36281))


ROSICA = reg.read_register(
    """rn;region;settlement;lareaunit;block;street;streetnum;entrance;n;e
62102;Нови Искър;с. Иваняне;"";"";ул. Св. св. Кирил и Методий;70;"";42.710562;23.187797
5501;Оборище;гр. София;"";"";ул. Св. св. Кирил и Методий;70;"";42.702660;23.325270
33030;Студентски;гр. София;"";"";бул. Св. Климент Охридски;49;"";42.647790;23.356500
110113;Люлин;гр. София;"";"";ул. Петър Бонев;3;"";42.710100;23.255100
110114;Люлин;гр. София;"";"";ул. Петър Бонев;5;"";42.710400;23.255400
"""
)


def _iframe(lat, lng):
    return f'<iframe src="https://maps.google.com/maps?q={lat},{lng}&z=16&output=embed"></iframe>'


def _at(address, point, method="nominatim_fallback", **meta):
    return SchoolLocation(
        address_i18n={"bg": address},
        lat=point[0],
        lng=point[1],
        geocode_meta={"precision": "exact", "method": method, **meta},
        location_tags=[],
    )


@pytest.mark.asyncio
async def test_a_site_map_at_our_own_address_elsewhere_moves_a_shown_pin_and_places_a_hidden_one(db_session):
    # ЧДГ Росица: "Кирил и Методий 70" was pinned in Оборище; the site maps the one in Иваняне.
    await _school(
        db_session,
        _at('ул. "Св. Св. Кирил и Методий" № 70', (42.7026617, 23.3252662)),
        _hidden('бул. "Св. Климент Охридски" № 49'),
        name_i18n={"bg": "ЧДГ Росица"},
    )
    page = "<p>кв. Иваняне</p>" + _iframe(42.710605, 23.187756) + _iframe(42.64779160424871, 23.356343120771477)
    report = await stage.run_site_address(db_session, register=ROSICA, fetch=_fetcher({HOME: page}), apply=True)
    assert report["outcomes"] == {"moved": 1, "placed": 1}
    rows = (await db_session.execute(select(SchoolLocation).order_by(SchoolLocation.id))).scalars().all()
    assert (rows[0].lat, rows[0].lng) == (42.710562, 23.187797)
    assert rows[0].address_i18n == {"bg": "с. Иваняне, ул. Св. св. Кирил и Методий 70"}
    assert (rows[1].lat, rows[1].lng) == (42.64779, 23.3565)


@pytest.mark.asyncio
async def test_a_shown_address_not_in_the_register_takes_the_one_the_site_names_at_our_pin(db_session):
    # ЧДГ Христо Ботев: the register's "ул. 315 - III м. р." is ул. Петър Бонев 3.
    await _school(db_session, _at("гр. София, ул. 315 - III м. р.", (42.71012, 23.25512), method="agent_pick"))
    page = "<p>кв. Люлин, ул.Петър Бонев №3</p>"
    report = await stage.run_site_address(db_session, register=ROSICA, fetch=_fetcher({HOME: page}), apply=True)
    assert report["outcomes"] == {"readdressed": 1}
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    assert row.address_i18n == {"bg": "гр. София, ул. Петър Бонев 3"}
    assert row.geocode_meta["evidence"]["previous_address_i18n"] == {"bg": "гр. София, ул. 315 - III м. р."}


@pytest.mark.asyncio
async def test_a_shown_register_address_a_hand_pin_or_a_near_pin_is_left_alone(db_session):
    await _school(
        db_session,
        _at("ул. Петър Бонев 5", (42.71012, 23.25512)),  # the register's own address: kept
        _at('ул. "Св. Св. Кирил и Методий" № 70', (42.7026617, 23.3252662), method="manual_fix"),
    )
    page = "<p>ул. Петър Бонев №3</p>" + _iframe(42.710605, 23.187756)
    report = await stage.run_site_address(db_session, register=ROSICA, fetch=_fetcher({HOME: page}))
    assert report["outcomes"] == {}


@pytest.mark.asyncio
async def test_an_official_pin_keeps_its_point_and_takes_the_address_its_site_names_there(db_session):
    from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG

    location = _at("гр. София, ул. 315 - III м. р.", (42.71012, 23.25512), method="municipal_point")
    location.location_tags = [OFFICIAL_COORDS_TAG]
    await _school(db_session, location)
    page = "<p>кв. Люлин, ул.Петър Бонев №3</p>"
    report = await stage.run_site_address(db_session, register=ROSICA, fetch=_fetcher({HOME: page}), apply=True)
    assert report["outcomes"] == {"relabelled": 1}
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    assert (row.lat, row.lng) == (42.71012, 23.25512)
    assert row.address_i18n == {"bg": "гр. София, ул. Петър Бонев 3"}
    assert row.geocode_meta["method"] == "municipal_point"
    assert row.geocode_meta["evidence"]["previous_address_i18n"] == {"bg": "гр. София, ул. 315 - III м. р."}
