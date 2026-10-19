"""Publish diff: a publish that takes away what many schools show now is stopped."""

from __future__ import annotations

import pytest

from app.models import School, SchoolLocation
from app.scrapers.stages import publish_diff as stage


def _card(school_id, *, pin=True, fees=False, name="ДГ №97"):
    return {
        "id": school_id,
        "name_i18n": {"bg": name},
        "resolved_name_i18n": {"bg": name, "en": "Kindergarten No. 97"},
        "locations": [{"lat": 42.7 if pin else None, "lng": 23.3 if pin else None}],
        "pricing": [{"amount": 500}] if fees else [],
        "exam_results": [],
        "admission_info": None,
        "attributes": {"class_size": None, "filter_tags": []},
        "attributes_i18n": {"bg": {"languages_of_instruction": []}},
    }


def test_card_features_ignore_empty_values():
    assert stage.card_features(_card(1, pin=False)) == {"listed"}
    full = _card(1, fees=True) | {
        "exam_results": [{"year": 2026}],
        "admission_info": {"cutoffs": [{"points": 20}]},
        "attributes_i18n": {"bg": {"languages_of_instruction": ["английски"]}},
    }
    assert stage.card_features(full) == {
        "listed",
        "pin",
        "fees",
        "exam_results",
        "admission:cutoffs",
        "details:languages_of_instruction",
    }


def test_wiping_one_field_stops_although_other_fields_remain():
    def card(i, facilities):
        return _card(i) | {
            "attributes_i18n": {"bg": {"languages_of_instruction": ["английски"], "facilities": ["басейн"] if facilities else []}}
        }

    report = stage.diff_lists([card(i, True) for i in range(1, 101)], [card(i, False) for i in range(1, 101)])
    assert report["features"]["details:facilities"]["stop"]
    assert not report["features"]["details:languages_of_instruction"]["lost"]
    assert "school details (facilities): 100 schools lose it" in stage.summary(report)


def test_a_few_losses_pass_and_many_stop_the_publish():
    live = [_card(i, fees=True) for i in range(1, 101)]
    # Five schools lose their fees: normal churn from a re-read.
    report = stage.diff_lists(live, [_card(i, fees=i > 5) for i in range(1, 101)])
    assert not report["stop"]
    assert len(report["features"]["fees"]["lost"]) == 5
    # Forty lose them at once: a wipe.
    report = stage.diff_lists(live, [_card(i, fees=i > 40) for i in range(1, 101)])
    assert report["stop"] and report["features"]["fees"]["stop"]
    assert not report["features"]["pin"]["stop"]
    # Gains elsewhere do not hide losses.
    report = stage.diff_lists(live, [_card(i, fees=i > 40) for i in range(1, 101)] + [_card(i, fees=True) for i in range(101, 200)])
    assert report["features"]["fees"]["stop"]


def test_schools_leaving_the_list_have_a_lower_bar():
    live = [_card(i) for i in range(1, 201)]
    assert not stage.diff_lists(live, live[5:])["stop"]
    report = stage.diff_lists(live, live[6:])
    assert report["features"]["listed"]["stop"]
    # A school that left is not counted again as losing its pin.
    assert report["features"]["pin"]["lost"] == []


def test_issue_body_lists_the_schools_with_links():
    live = [_card(i, fees=True, name="ДГ `№97` [x](https://evil)") for i in range(1, 31)]
    report = stage.diff_lists(live, [_card(i) for i in range(1, 31)])
    body = stage.issue_body(report, max_lines=3)
    assert "## 30 schools lose fees (30 -> 0, limit 10)" in body
    assert "- [1](https://schooldecider.com/schools/1) `ДГ '№97' [x](https://evil)`" in body
    assert "- and 27 more" in body
    assert "PUBLISH_ALLOW_DROPS=1" in body
    assert "pin" not in body.split("##", 1)[1]


@pytest.mark.asyncio
async def test_the_new_list_is_the_one_the_api_serves(client, db_session):
    school = School(
        name_i18n={"bg": "ДГ №97"},
        country_code="bg",
        school_type="state",
        education_level="kindergarten",
        city="sofia",
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add(
        SchoolLocation(school_id=school.id, address_i18n={"bg": "ул. 1"}, lat=42.7, lng=23.3, geocode_meta={"precision": "exact"})
    )
    await db_session.commit()
    served = (await client.get("/schools", params={"country_code": "bg", "city": "sofia"})).json()
    built = await stage.build_new_list(db_session)
    assert built == served
    assert stage.card_features(built[0]) >= {"listed", "pin"}
