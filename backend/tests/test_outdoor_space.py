"""Outdoor space per building: OSM plots round pins, address flags, website facts, visits."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import School, SchoolLocation
from app.scrapers.stages import outdoor_grounds as stage
from app.utils import outdoor_space

PIN = (42.6476, 23.2960)
_ids = iter(range(1, 10_000))


def _square(lat, lng, half=0.0005):
    corners = [(lat - half, lng - half), (lat - half, lng + half), (lat + half, lng + half), (lat + half, lng - half)]
    return [{"lat": a, "lon": b} for a, b in corners + corners[:1]]


def _plot(code=None, lat=PIN[0], lng=PIN[1], half=0.0005, **tags):
    tags = {"amenity": "school", **tags}
    if code:
        tags["ref:BG:neispuo"] = code
    return {"type": "way", "id": next(_ids), "geometry": _square(lat, lng, half), "tags": tags}


def _location(id=1, address="ул. Тест 1", lat=PIN[0], lng=PIN[1]):
    return SimpleNamespace(id=id, address_i18n={"bg": address}, lat=lat, lng=lng)


def test_plot_area_and_open_ground_less_buildings():
    plot = stage.read_plots([_plot("1")])[0][0]
    assert 6_500 < plot.area < 10_000  # about 82 m by 111 m
    building = {"type": "way", "id": 5, "geometry": _square(*PIN, half=0.0001), "tags": {"building": "school"}}
    pitch = {"type": "way", "id": 6, "center": {"lat": PIN[0] + 0.0003, "lon": PIN[1]}, "tags": {"leisure": "pitch"}}
    outside = {"type": "node", "id": 7, "lat": PIN[0] + 0.01, "lon": PIN[1], "tags": {"leisure": "playground"}}
    plots, buildings, play = stage.read_plots([_plot("1"), building, pitch, outside])
    details = stage.plot_details(plots[0], buildings, play)
    assert details["pitches"] == 1 and details["playgrounds"] == 0
    assert abs(details["open_m2"] - (plots[0].area - 16.4 * 22.1)) <= 50  # a 16 m by 22 m building


def test_a_relation_split_into_pieces_is_joined():
    ring = _square(*PIN)
    relation = {
        "type": "relation",
        "id": 9,
        "tags": {"amenity": "school", "type": "multipolygon", "ref:BG:neispuo": "1"},
        "members": [
            {"type": "way", "role": "outer", "geometry": ring[:3]},
            {"type": "way", "role": "outer", "geometry": ring[2:][::-1]},  # reversed piece
        ],
    }
    plots = stage.read_plots([relation])[0]
    assert len(plots) == 1 and plots[0].contains(stage._xy(*PIN, plots[0].lat0))


def test_own_plot_by_code_then_by_name_and_another_schools_plot():
    plots = stage.read_plots([_plot("2000300")])[0]
    plot, own = stage.match_location(*PIN, "2000300", set(), plots)
    assert plot is plots[0] and own
    # A pin inside another school's coded plot: shared, not own.
    plot, own = stage.match_location(*PIN, "999", set(), plots)
    assert plot is plots[0] and not own
    # No code on the plot: its name decides; a generic word ("училище") does not count.
    named = stage.read_plots([_plot(name="ЧСУ „Дружба“")])[0]
    assert stage.match_location(*PIN, "", stage._name_tokens("Частно средно училище „Дружба“"), named)[1]
    assert stage.match_location(*PIN, "", stage._name_tokens("Частно средно училище „Питагор“"), named) == (None, False)


def test_a_pin_just_outside_counts_for_its_own_plot_only():
    plots = stage.read_plots([_plot("1")])[0]
    lat = PIN[0] + 0.0005 + 0.0001  # about 11 m north of the edge
    assert stage.match_location(lat, PIN[1], "1", set(), plots)[1]
    assert stage.match_location(lat, PIN[1], "2", set(), plots) == (None, False)
    far = PIN[0] + 0.0005 + 0.0005  # about 55 m
    assert stage.match_location(far, PIN[1], "1", set(), plots) == (None, False)


@pytest.mark.asyncio
async def test_run_stores_own_and_shared_plots_and_refuses_a_cut_answer(db_session):
    host = School(
        name_i18n={"bg": "47 СУ"},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="sofia",
        institutional_id="2000047",
    )
    tenant = School(
        name_i18n={"bg": "ЧСУ Артис"},
        country_code="bg",
        school_type="private",
        education_level="upper_secondary",
        city="sofia",
        institutional_id="2000999",
    )
    db_session.add_all([host, tenant])
    await db_session.flush()
    exact = {"precision": "exact"}
    host_loc = SchoolLocation(
        school_id=host.id, address_i18n={"bg": "ул. 1"}, lat=PIN[0], lng=PIN[1], geocode_meta=exact
    )
    tenant_loc = SchoolLocation(
        school_id=tenant.id, address_i18n={"bg": "ул. 1, ет. 3"}, lat=PIN[0], lng=PIN[1], geocode_meta=exact
    )
    db_session.add_all([host_loc, tenant_loc])
    await db_session.commit()
    elements = [_plot("02000047")] + [_plot(str(n), lat=PIN[0] + n * 0.01) for n in range(1, stage.MIN_PLOTS)]
    report = await stage.run_outdoor_grounds(db_session, elements, apply=True)
    assert report["locations"]["own"] == 1 and report["locations"]["shared"] == 1
    assert report["changed"] == 2
    again = await stage.run_outdoor_grounds(db_session, elements)
    assert again["changed"] == 0
    await db_session.refresh(host)
    await db_session.refresh(tenant)
    assert host.attributes["outdoor_grounds"]["locations"][str(host_loc.id)]["own"] is True
    shared = tenant.attributes["outdoor_grounds"]["locations"][str(tenant_loc.id)]
    assert shared["shared_with_id"] == host.id and shared["shared_with_i18n"]["bg"] == "47 СУ"
    with pytest.raises(RuntimeError):
        await stage.run_outdoor_grounds(db_session, elements[:5], apply=True)


def test_answers_take_the_strongest_source():
    loc = _location(address="ул. Витошки Камбани 9")
    plot = {"locations": {"1": {"lat": PIN[0], "lng": PIN[1], "own": True, "open_m2": 4400, "pitches": 3}}}
    # A visit beats the rest.
    visit = outdoor_space.location_answers(596, [loc], {}, None)["locations"][0]
    assert visit["kind"] == "grounds" and visit["source"] == "visit" and visit["checked_on"] == "2026-10-26"
    # The website answers for a school with one building, with the mapped plot's size.
    facts = [{"topic": "facilities", "text_i18n": {"en": "A courtyard with play equipment."}, "source_url": "u"}]
    site = outdoor_space.location_answers(1, [_location()], {"outdoor_grounds": plot}, facts)["locations"][0]
    assert site["source"] == "website" and site["open_m2"] == 4400
    osm = outdoor_space.location_answers(1, [_location()], {"outdoor_grounds": plot}, [])["locations"][0]
    assert (osm["kind"], osm["source"], osm["pitches"]) == ("grounds", "osm", 3)
    # A moved pin drops the stored plot.
    moved = outdoor_space.location_answers(1, [_location(lat=PIN[0] + 0.01)], {"outdoor_grounds": plot}, [])
    assert moved["locations"][0]["kind"] == "unknown"


def test_several_buildings_list_website_facts_for_the_school():
    facts = [{"topic": "facilities", "text_i18n": {"en": "Green spaces and yards around the main building"}}]
    answers = outdoor_space.location_answers(1, [_location(1), _location(2, "ул. 2")], {}, facts)
    assert [a["kind"] for a in answers["locations"]] == ["unknown", "unknown"]
    assert len(answers["website"]) == 1


@pytest.mark.parametrize(
    ("address", "kind"),
    [
        ('кв. Иван Вазов, ул. "Димитър Манов" № 18, ет. 3 (сграда на 47 СУ "Христо Г. Данов")', "other_building"),
        ('ул. "Кадемлия" 15, в сградата на ПГ по транспорт Макгахан", партер.', "other_building"),
        ("ж.к. Младост 4, бл. 456, вх. 1, ап. 7", "floor"),
        ("ул. Гео Милев № 158, корпус 4, ет. 3 и ет. 4", "floor"),
        ("ж.к. Люлин-9, бл. 964", None),  # a block number alone: often the yard's neighbour
        ("ул. Витошки Камбани 9", None),
    ],
)
def test_address_answers(address, kind):
    assert outdoor_space.address_answer(address) == kind


@pytest.mark.parametrize(
    ("text", "outdoor"),
    [
        ("The kindergarten has a spacious outdoor yard.", True),
        ("Sports facilities include a gym, indoor swimming pool, tennis court and football pitch.", True),
        ("Двор с катерушки и пясъчник.", True),
        ("Extracurricular activities include football, taekwondo and chess.", False),
        ("The school has a large gym and a library.", False),
        ("Частна детска градина с английски език.", False),
        ("The preschool has an indoor playground.", False),
    ],
)
def test_outdoor_facts(text, outdoor):
    facts = [{"topic": "facilities", "text_i18n": {"en": text}, "source_url": "u"}]
    assert bool(outdoor_space.outdoor_facts(facts)) is outdoor
