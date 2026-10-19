"""OSM-check stage: schools whose pin, number or website disagrees with OpenStreetMap are listed."""

from __future__ import annotations

import pytest

from app.models import School, SchoolLocation
from app.scrapers.stages import osm_check as stage

PIN = (42.6476, 23.2960)


def _feature(code="2000300", lat=PIN[0], lon=PIN[1], **tags):
    return {
        "type": "way",
        "center": {"lat": lat, "lon": lon},
        "tags": {"amenity": "school", "ref:BG:neispuo": code} | tags,
    }


def test_name_numbers_reads_leading_and_numbered_forms():
    assert stage.name_numbers("115 Основно училище „Св. св. Кирил и Методий“") == {"115"}
    assert stage.name_numbers("91. НЕГ „Проф. Константин Гълъбов“") == {"91"}
    assert stage.name_numbers("ДГ №97 Изгрев") == {"97"}
    assert stage.name_numbers("Частна детска градина „Маргаритка“") == set()


def test_a_pin_far_from_every_osm_feature_is_flagged():
    far = _feature(lat=PIN[0] + 0.01)  # about 1.1 km north
    assert [f["flag"] for f in stage.check_school("ЧСУ ЕСПА", None, [PIN], [far])] == ["pin_far"]
    near = _feature(lat=PIN[0] + 0.001)  # about 110 m: a wing or gate, not another building
    assert stage.check_school("ЧСУ ЕСПА", None, [PIN], [far, near]) == []


def test_a_different_school_number_is_flagged_but_a_missing_one_is_not():
    flags = stage.check_school("ДГ №97 Изгрев", None, [PIN], [_feature(name="ДГ №79 Изгрев")])
    assert [f["flag"] for f in flags] == ["number_differs"]
    assert (
        stage.check_school(
            "ДГ №97 Изгрев", None, [PIN], [_feature(name="ДГ „Изгрев“", official_name="Детска градина №97 „Изгрев“")]
        )
        == []
    )
    assert stage.check_school("ДГ №97 Изгрев", None, [PIN], [_feature(name="ДГ „Изгрев“")]) == []


def test_a_different_website_is_flagged_ignoring_www_and_shared_hosts():
    flags = stage.check_school(
        "115 ОУ", "https://ousvsvkirilimetodii.com/", [PIN], [_feature(website="https://ou115.com")]
    )
    assert [f["flag"] for f in flags] == ["website_differs"]
    assert (
        stage.check_school("115 ОУ", "https://www.ou115.com/", [PIN], [_feature(website="http://ou115.com/za-nas")])
        == []
    )
    assert (
        stage.check_school("115 ОУ", "https://ou115.com", [PIN], [_feature(website="https://facebook.com/ou115")]) == []
    )


@pytest.mark.asyncio
async def test_run_matches_schools_by_moe_code_and_skips_hidden_pins(db_session):
    tagged = School(
        name_i18n={"bg": "ДГ №97 Изгрев"},
        country_code="bg",
        school_type="state",
        education_level="kindergarten",
        city="sofia",
        institutional_id="2000300",
    )
    untagged = School(
        name_i18n={"bg": "ДГ №5"},
        country_code="bg",
        school_type="state",
        education_level="kindergarten",
        city="sofia",
        institutional_id="999",
    )
    db_session.add_all([tagged, untagged])
    await db_session.flush()
    db_session.add_all(
        [
            SchoolLocation(
                school_id=tagged.id,
                address_i18n={"bg": "ул. 1"},
                lat=PIN[0],
                lng=PIN[1],
                geocode_meta={"precision": "exact"},
            ),
            # A hidden, approximate point is not on the site, so it is not compared.
            SchoolLocation(
                school_id=untagged.id,
                address_i18n={"bg": "ул. 2"},
                lat=PIN[0],
                lng=PIN[1],
                geocode_meta={"precision": "approximate"},
            ),
        ]
    )
    await db_session.commit()
    osm = [_feature(code="02000300", lat=PIN[0] + 0.01, name="ДГ №97 Изгрев"), _feature(code="999", lat=PIN[0] + 0.02)]
    report = await stage.run_osm_check(db_session, osm)
    assert report["checked"] == 2
    assert report["flags"] == {"pin_far": 1}
    assert [r["school_id"] for r in report["results"]] == [tagged.id]


def test_issue_body_lists_each_school_with_a_link_and_credits_osm():
    report = {
        "checked": 2,
        "results": [{"school_id": 7, "name": "ДГ №97", "flags": [{"flag": "pin_far", "metres": 900}]}],
    }
    body = stage.issue_body(report)
    assert "- [7](https://schooldecider.com/schools/7) ДГ №97: pin 900 m from OSM's" in body
    stranger = {"flag": "number_differs", "osm_names": ["ДГ №79 @someone [x](https://evil)"]}
    body = stage.issue_body({"checked": 1, "results": [{"school_id": 7, "name": "ДГ №97", "flags": [stranger]}]})
    assert "`ДГ №79 @someone [x](https://evil)`" in body
    assert "© OpenStreetMap contributors" in body


def test_website_tags_with_several_values_subdomains_or_typos_are_not_false_alarms():
    assert stage.site_hosts("https://ou115.com;https://facebook.com/ou115") == {"ou115.com", "facebook.com"}
    assert stage.site_hosts("http://[school.bg") == set()
    assert (
        stage.check_school(
            "115 ОУ", "https://ou115.com", [PIN], [_feature(website="https://ou115.com;https://facebook.com/x")]
        )
        == []
    )
    assert stage.check_school("73 СУ", "https://sou73.bg", [PIN], [_feature(website="https://school.sou73.bg")]) == []
    assert stage.check_school("73 СУ", "https://sou73.bg", [PIN], [_feature(website="http://[sou73.bg")]) == []
