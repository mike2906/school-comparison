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
        ("Частно училище за ранно чуждоезиково обучение ЕСПА", "ЕСПА", True),
        ("ЧСУ Васил Левски", "ЧСУ Христо Ботев", False),
        ("СПГЕ Джон Атанасов", "СПГЕ Попов", False),
        ("Частно основно училище Света София ЕООД", "ЧОУ „Света София“", True),
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


def test_check_pick_website_point():
    on_street = Candidate(point=NPMG_POINT, source="website_map", address='ул. "Бигла" № 52')
    assert _check(on_street) is None
    assert _check(Candidate(point=(41.0, 23.3), source="website_map", address='ул. "Бигла" № 52')) == "outside the city"
    assert _check(Candidate(point=NPMG_POINT, source="website_map", address='ул. "Бигла" № 52', town_ok=False)) == "another town"
    assert _check(on_street, other_pins=[((42.67050, 23.32980), "ул. Казбек 3")]) == "another school is pinned there"
    # Schools sharing one building may share its pin.
    assert _check(on_street, other_pins=[((42.67050, 23.32980), 'ул. "Бигла" № 56')]) is None
    assert _check(Candidate(point=NPMG_POINT, source="website_map", address='ул. "Бигла" № 150')) == "another house number"
    assert _check(Candidate(point=NPMG_POINT, source="website_map", address='ул. "Бигла"')) == "no house number to compare"
    assert _check(Candidate(point=NPMG_POINT, source="website_map", address=None)) == "not on our street"


def test_check_pick_osm_feature():
    named = Candidate(point=(42.68, 23.33), source="osm_school", name="НПМГ Чакалов", address='ул. "Бигла"')
    assert _check(named) is None
    # The street is always required, also for a well-named feature.
    assert _check(Candidate(point=(42.68, 23.33), source="osm_school", name="НПМГ Чакалов")) == "not on our street"
    elsewhere = Candidate(point=(42.68, 23.33), source="osm_school", name="НПМГ Чакалов", address='ул. "Казбек"')
    assert _check(elsewhere) == "not on our street"
    far_number = Candidate(point=(42.68, 23.33), source="osm_school", name="НПМГ Чакалов", address='ул. "Бигла" № 150')
    assert _check(far_number) == "another house number"
    twin = Candidate(point=(42.69, 23.34), source="osm_school", name="НПМГ Чакалов филиал", address='ул. "Бигла"')
    assert _check(named, [named, twin]) == "several OSM features have this name"
    kindergarten = Candidate(
        point=(42.68, 23.33), source="osm_school", name="НПМГ Чакалов", address='ул. "Бигла"', amenity="kindergarten"
    )
    assert _check(kindergarten) == "OSM maps another kind of institution there"
    # Our street and number, but OSM names another institution there.
    other = Candidate(point=(42.68, 23.33), source="osm_school", name="ДГ 12 Детски рай", address='ул. "Бигла" № 52')
    assert _check(other) == "OSM names another institution there"


def test_check_pick_private_and_state_names_differ():
    state = Candidate(point=(42.68, 23.33), source="osm_school", name="СУ Васил Левски", address='ул. "Бигла"')
    refused = stage.check_pick("ЧСУ Васил Левски", False, "ул. Бигла 56", state, [state], SOFIA, ())
    assert refused == "one name is a private institution's, the other not"
    private = Candidate(point=(42.68, 23.33), source="osm_school", name="Частно СУ Васил Левски", address='ул. "Бигла"')
    assert stage.check_pick("ЧСУ Васил Левски", False, "ул. Бигла 56", private, [private], SOFIA, ()) is None


def test_osm_name_at_our_exact_number_needs_one_shared_word():
    veda = Candidate(point=(42.68, 23.33), source="osm_school", name="Частно немско училище „Веда“", address='ул. "Бигла" № 56')
    assert stage.check_pick("ЧСУ с немски език Веда", False, "ул. Бигла 56", veda, [veda], SOFIA, ()) is None
    nearby = Candidate(point=(42.68, 23.33), source="osm_school", name="Частно училище „Веда“", address='ул. "Бигла" № 54')
    assert stage.check_pick("ЧСУ Веда Ломоносов", False, "ул. Бигла 56", nearby, [nearby], SOFIA, ()) == (
        "OSM names another institution there"
    )


def test_a_bare_osm_name_is_not_called_state():
    espa = Candidate(point=(42.68, 23.33), source="osm_school", name="ЕСПА", address='ул. "Бигла"')
    assert stage.check_pick("Частно училище ЕСПА", False, "ул. Бигла 56", espa, [espa], SOFIA, ()) is None


def test_a_plain_type_word_is_not_called_state():
    # American College, Виктор Юго: private schools whose names carry no "частно".
    college = Candidate(point=(42.68, 23.33), source="osm_school", name="Американски колеж в София", address='ул. "Бигла" № 56')
    assert stage.check_pick("Американски колеж в София", False, "ул. Бигла 56", college, [college], SOFIA, (), private=True) is None
    hugo = Candidate(point=(42.68, 23.33), source="osm_school", name="Френско училище Виктор Юго", address='ул. "Бигла" № 56')
    assert stage.check_pick("Френско училище Виктор Юго", False, "ул. Бигла 56", hugo, [hugo], SOFIA, (), private=True) is None
    # An abbreviation still says it: a state "СУ" is not our private school.
    state = Candidate(point=(42.68, 23.33), source="osm_school", name="СУ Виктор Юго", address='ул. "Бигла" № 56')
    assert stage.check_pick("Френско училище Виктор Юго", False, "ул. Бигла 56", state, [state], SOFIA, (), private=True) == (
        "one name is a private institution's, the other not"
    )
    # A state school named with a type word is not our private namesake.
    neg = Candidate(point=(42.68, 23.33), source="osm_school", name="Немска езикова гимназия", address='ул. "Бигла" № 56')
    assert stage.check_pick("Частна немска езикова гимназия", False, "ул. Бигла 56", neg, [neg], SOFIA, (), private=True) == (
        "one name is a private institution's, the other not"
    )
    # OSM's "ЧСУ" agrees with a private school whose own name has no "частно".
    chsu = Candidate(point=(42.68, 23.33), source="osm_school", name="ЧСУ Виктор Юго", address='ул. "Бигла" № 56')
    assert stage.check_pick("Френско училище Виктор Юго", False, "ул. Бигла 56", chsu, [chsu], SOFIA, (), private=True) is None
    assert stage.check_pick("Френско училище Виктор Юго", False, "ул. Бигла 56", chsu, [chsu], SOFIA, (), private=False) == (
        "one name is a private institution's, the other not"
    )
    # A person's initial is not an abbreviation.
    initial = Candidate(point=(42.68, 23.33), source="osm_school", name="Американски колеж Г. Шипков", address='ул. "Бигла" № 56')
    assert stage.check_pick("Американски колеж Г. Шипков", False, "ул. Бигла 56", initial, [initial], SOFIA, (), private=True) is None


def test_one_site_mapped_twice_or_a_namesake_kindergarten_is_not_a_second_branch():
    school = Candidate(point=(42.68, 23.33), source="osm_school", name="ЧЕСУ Дорис Тенеди", address='ул. "Бигла" № 56')
    building = Candidate(point=(42.6803, 23.3302), source="osm_school", name="ЧЕСУ Дорис Тенеди", address='ул. "Бигла" № 56')
    kindergarten = Candidate(
        point=(42.70, 23.35), source="osm_school", name="ЧДГ Дорис Тенеди", address='ул. "Казбек" № 1', amenity="kindergarten"
    )
    branch = Candidate(point=(42.70, 23.35), source="osm_school", name="ЧЕСУ Дорис Тенеди", address='ул. "Казбек" № 1')
    args = ("ЧЕСУ Дорис Тенеди", False, "ул. Бигла 56", school)
    assert stage.check_pick(*args, [school, building, kindergarten], SOFIA, (), private=True) is None
    assert stage.check_pick(*args, [school, branch], SOFIA, (), private=True) == "several OSM features have this name"
    # Two branches across the street, each at its own address, are still two.
    across = Candidate(point=(42.6803, 23.3302), source="osm_school", name="ЧЕСУ Дорис Тенеди", address='ул. "Бигла" № 61')
    assert stage.check_pick(*args, [school, across], SOFIA, (), private=True) == "several OSM features have this name"


def test_is_private_name():
    assert stage.is_private_name("ЧДГ Детски свят")
    assert stage.is_private_name("Частна детска градина")
    assert not stage.is_private_name("НПМГ Акад. Л. Чакалов")
    assert not stage.is_private_name("138 СУ Проф. Васил Златарски")


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


@pytest.mark.asyncio
async def test_two_schools_cannot_take_the_same_building_in_one_run(db_session):
    first, _ = await _seed(db_session)
    second = _school(name_i18n={"bg": "ЧСУ Бигла", "en": "Bigla"})
    db_session.add(second)
    await db_session.flush()
    db_session.add(SchoolLocation(
        school_id=second.id, address_i18n={"bg": "ул. Бигла 54"}, lat=42.66, lng=23.31,
        geocode_meta={"precision": "approximate"},
    ))
    await db_session.commit()
    pages = {HOME: f'<a href="https://maps.google.com/?q={NPMG_POINT[0]},{NPMG_POINT[1]}">map</a>'}
    stubs, _ = _stubs(
        Pick(candidate=1, sure=True, reason="website point"),
        pages=pages,
        reverse={NPMG_POINT: {"road": "Бигла", "house_number": "52", "city": "София"}},
    )
    report = await stage.run_pin_agent(db_session, school_ids=[first.id, second.id], **stubs)
    assert [r["outcome"] for r in report["results"]] == ["placed", "refused"]
    assert report["results"][1]["refused"] == "another school is pinned there"


def test_osm_street_without_number_is_kept():
    [candidate] = stage.osm_candidates(
        [{"type": "way", "center": {"lat": 42.6, "lon": 23.3}, "tags": {"name": "X", "addr:street": "Казбек"}}]
    )
    assert candidate.address == 'ул. "Казбек"'


@pytest.mark.asyncio
async def test_city_osm_reads_the_city_once_tries_the_next_server_and_filters_by_distance(monkeypatch):
    import httpx

    monkeypatch.setattr(stage.asyncio, "sleep", _no_sleep)
    calls = []
    near = {"type": "node", "lat": 42.6005, "lon": 23.3005}
    far = {"type": "way", "center": {"lat": 42.75, "lon": 23.45}}

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(504)
        return httpx.Response(200, json={"elements": [near, far]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        city = stage.CityOsm(client, SOFIA)
        assert await city(42.6, 23.3) == [near]
        assert await city(42.75, 23.45) == [far]
    assert calls == list(stage.OVERPASS_URLS)  # the second server answered; no more queries


@pytest.mark.asyncio
async def test_city_osm_goes_on_without_osm_when_no_server_answers(monkeypatch):
    import httpx

    monkeypatch.setattr(stage.asyncio, "sleep", _no_sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(504))) as client:
        city = stage.CityOsm(client, SOFIA)
        assert await city(42.6, 23.3) == []
        assert await city(42.7, 23.3) == []
    assert city.failed


async def _no_sleep(seconds):
    return None


@pytest.mark.asyncio
async def test_the_stage_can_start_a_pipeline_run_for_its_spend(db_session):
    from app.services.pipeline_runs import start_pipeline_run

    run = await start_pipeline_run(db_session, country="bg", city="sofia", cli_stage=stage.STAGE)
    assert run.id


def test_review_round_two_cases():
    # A weaker relaxed match is refused while a fully named feature exists nearby.
    prince = Candidate(point=(42.68, 23.33), source="osm_school", name="Принц", address='ул. "Бигла" № 56', amenity="kindergarten")
    full = Candidate(point=(42.69, 23.34), source="osm_school", name="Малкият принц", address='ул. "Бигла" № 70', amenity="kindergarten")
    assert stage.check_pick("ЧДГ Малкият принц", True, "ул. Бигла 56", prince, [prince, full], SOFIA, ()) == (
        "several OSM features have this name"
    )
    # Our side's private-ness comes from the school type, and ОДЗ names a type.
    odz = Candidate(point=(42.68, 23.33), source="osm_school", name="ОДЗ №5 Звездица", address='ул. "Бигла" № 56', amenity="kindergarten")
    assert stage.check_pick("Звездица", True, "ул. Бигла 56", odz, [odz], SOFIA, (), private=True) == (
        "one name is a private institution's, the other not"
    )
    assert not stage.is_private_name("ДГ 26 ЧАЙКА")
    assert stage.is_private_name("ЧЕСУ Дорис Тенеди")
    # Ordinals are numbers; an all-capitals name has no acronyms.
    assert not stage.name_matches("Втора английска езикова гимназия", "Първа английска езикова гимназия")
    assert not stage.name_matches("ЧДГ МАЛКИЯТ ПРИНЦ", "Принц")


@pytest.mark.asyncio
async def test_city_osm_treats_a_timed_out_partial_answer_as_failed(monkeypatch):
    import httpx

    monkeypatch.setattr(stage.asyncio, "sleep", _no_sleep)
    partial = {"remark": "runtime error: Query timed out in \"query\" at line 1", "elements": []}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=partial))) as client:
        city = stage.CityOsm(client, SOFIA)
        assert await city(42.6, 23.3) == []
    assert city.failed


def test_a_streetless_address_takes_a_named_osm_feature_next_to_our_point():
    """Ерих Кестнер's address is only "ж. к. Люлин 6"; OSM names the school next to our point."""
    kestner = Candidate(point=(42.712226, 23.253326), source="osm_school", name="ЧНЕГ „Ерих Кестнер“")
    args = ("Частна немска гимназия Ерих Кестнер", False, "ж. к. Люлин 6", kestner, [kestner], SOFIA, ())
    assert stage.check_pick(*args, private=True, near=(42.7125, 23.2540)) is None
    # Too far from our point, or another name, or no point to compare: refused.
    assert stage.check_pick(*args, private=True, near=(42.73, 23.28)) == "not on our street"
    assert stage.check_pick(*args, private=True) == "not on our street"
    other = Candidate(point=(42.712226, 23.253326), source="osm_school", name="ЧОУ Веда")
    assert stage.check_pick("Частна немска гимназия Ерих Кестнер", False, "ж. к. Люлин 6", other, [other], SOFIA, (), private=True, near=(42.7125, 23.2540)) == (
        "OSM names another institution there"
    )
    # An address with a street still needs the street.
    assert stage.check_pick("Частна немска гимназия Ерих Кестнер", False, "ул. Бигла 56", kestner, [kestner], SOFIA, (), private=True, near=(42.7125, 23.2540)) == (
        "not on our street"
    )


def test_the_schools_site_map_elsewhere_refuses_an_osm_pick():
    named = Candidate(point=(42.68, 23.33), source="osm_school", name="НПМГ Чакалов", address='ул. "Бигла"')
    far_site = Candidate(point=(42.70, 23.33), source="website_map", address="ул. Казбек 3")
    near_site = Candidate(point=(42.6805, 23.3305), source="website_map", address='ул. "Бигла" № 52')
    check = lambda points: stage.check_pick(  # noqa: E731
        "НПМГ Акад. Чакалов", False, "ул. Бигла 56", named, [named], SOFIA, (), site_points=points
    )
    assert check([far_site.point]) == "the school's site maps it elsewhere"
    assert check([far_site.point, near_site.point]) is None
    assert check([]) is None


def test_a_school_and_a_high_school_of_one_name_are_two_institutions():
    school = Candidate(point=(42.678, 23.3808), source="osm_school", name="ЧОУ „Образователни технологии“", address="ул. Гео Милев 158")
    high = Candidate(point=(42.6808, 23.3815), source="osm_school", name="ЧПГ „Образователни технологии“", address="ул. Гео Милев 158")
    args = ("Частна профилирана гимназия Образователни технологии", False, "ул. Гео Милев 158")
    assert stage.check_pick(*args, high, [school, high], SOFIA, (), private=True) is None
    # The model picking the other kind's building is still refused.
    assert stage.check_pick(*args, school, [school, high], SOFIA, (), private=True) == "several OSM features have this name"
    # Two spellings of one kind (СОУ, СУ) are still two branches.
    sou = Candidate(point=(42.678, 23.3808), source="osm_school", name="СОУ Христо Ботев", address="ул. Гео Милев 158")
    su = Candidate(point=(42.69, 23.39), source="osm_school", name="СУ Христо Ботев", address="ул. Гео Милев 1")
    assert stage.check_pick("Средно училище Христо Ботев", False, "ул. Гео Милев 158", sou, [sou, su], SOFIA, (), private=False) == (
        "several OSM features have this name"
    )


def test_a_streetless_address_needs_a_neighbourhood():
    kestner = Candidate(point=(42.712226, 23.253326), source="osm_school", name="ЧНЕГ „Ерих Кестнер“")
    assert stage.check_pick("Частна немска гимназия Ерих Кестнер", False, "гр. София", kestner, [kestner], SOFIA, (), private=True, near=(42.7125, 23.2540)) == (
        "not on our street"
    )


def test_type_family():
    assert stage._type_family("ЧПГ Образователни технологии") == "gymnasium"
    assert stage._type_family("СОУ Христо Ботев") == "school"
    assert stage._type_family("ЧДГ Слънце") == "kindergarten"
    assert stage._type_family("Частно средно училище с детска градина Х") == "school"
    assert stage._type_family("Слънце") is None
