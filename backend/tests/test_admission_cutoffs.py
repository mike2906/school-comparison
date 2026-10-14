"""Cut-off archive -> per-school admission_info keys, and what the API serves of them."""

from types import SimpleNamespace

import pytest

from app.models import School, SchoolLocation
from app.services.admission_cutoffs import match_key, school_cutoff_summary, school_cutoffs
from scripts.import_cutoffs import apply_to_school, catchment_ids, derived_admission_keys, school_index


def _row(round_id, classified_on, *, queue="general", points=16.0, source_id="171", age_group="first",
         campaign="2026/2027", round_kind="current", name="ДГ №1"):
    return {
        "country": "bg", "city": "sofia", "stage": "kindergarten", "campaign": campaign,
        "rule_set": "bg/sofia/kindergarten/2026-2027@2026-03-12", "round_id": round_id,
        "classified_on": classified_on, "round_name": "Текущо класиране", "round_kind": round_kind,
        "source": "kg.sofia.bg", "source_id": source_id, "institution_name": name, "district": "Възраждане",
        "group_id": 7471, "group_label": "1-ва група, родени 2023 г.", "birth_year": 2023,
        "age_group": age_group, "queue": queue, "places": 6, "applicants_count": 16, "admitted_count": 6,
        "undersubscribed": False, "last_admitted_points": points, "tie_at_cutoff": True,
        "not_admitted_at_cutoff": 1, "source_url": f"https://kg.sofia.bg/api/stat-rating/rated/{round_id}/7471",
        "retrieved_at": "2026-10-14T09:00:00+00:00",
    }


def test_school_cutoffs_trim_fields_and_sort_newest_first():
    rows = [_row(443, "2026-09-04"), _row(445, "2026-10-02", points=8.0), _row(443, "2026-09-04", queue="sop", points=None)]
    out = school_cutoffs(rows)
    assert [r["round_id"] for r in out] == [445, 443, 443]
    assert "institution_name" not in out[0] and "retrieved_at" not in out[0] and "building" not in out[0]
    sop = next(r for r in out if r["queue"] == "sop")
    assert "last_admitted_points" not in sop  # nulls are dropped


def test_buildings_are_named_only_when_a_school_has_several():
    rows = [_row(443, "2026-09-04"), _row(443, "2026-09-04", source_id="479", name="ДГ №18 - сграда 2")]
    assert {r.get("building") for r in school_cutoffs(rows)} == {"ДГ №1", "ДГ №18 - сграда 2"}
    assert {s.get("building") for s in school_cutoff_summary(rows)} == {"ДГ №1", "ДГ №18 - сграда 2"}


def test_summary_takes_first_and_latest_general_round_of_the_latest_campaign():
    rows = [
        _row(300, "2025-09-05", campaign="2025/2026", points=20.0),
        _row(443, "2026-09-04", points=16.0),
        _row(444, "2026-09-18", points=12.0),
        _row(445, "2026-10-02", points=8.0),
        _row(445, "2026-10-02", queue="chronic", points=3.0),
    ]
    (entry,) = school_cutoff_summary(rows)
    assert entry["campaign"] == "2026/2027" and entry["rounds"] == 3
    assert entry["first"]["last_admitted_points"] == 16.0 and entry["first"]["round_id"] == 443
    assert entry["latest"]["last_admitted_points"] == 8.0
    assert "building" not in entry


def test_match_keys():
    assert match_key(_row(443, "2026-09-04")) == ("kg_sofia_id", "171")
    assert match_key({"source": "ruo-sofia-grad", "source_id": "2216306"}) == ("institutional_id", "2216306")


def test_school_index_prefers_a_schools_own_kg_id():
    family = SimpleNamespace(id=1, attributes={"kg_sofia_id": 36, "kg_sofia_ids": [36, 479]}, institutional_id="2000036")
    own = SimpleNamespace(id=2, attributes={"kg_sofia_id": 479}, institutional_id=None)
    index = school_index([family, own])
    assert index[("kg_sofia_id", "36")] == 1
    assert index[("kg_sofia_id", "479")] == 2
    assert index[("institutional_id", "2000036")] == 1


def test_catchment_ids_from_esri_ids():
    assert catchment_ids({"kg_sofia_esri_id": 36, "kg_sofia_esri_ids": ["36", "479"]}) == {"bg-sofia-gis": ["36", "479"]}
    assert catchment_ids({}) == {}


def test_apply_replaces_only_derived_keys_and_reports_change():
    school = SimpleNamespace(admission_info={"status": "accepting", "cutoffs": [{"old": 1}]}, attributes={})
    keys = derived_admission_keys([_row(443, "2026-09-04")], {"kg_sofia_esri_id": 171})
    assert apply_to_school(school, keys) is True
    info = school.admission_info
    assert info["status"] == "accepting"
    assert info["kg_sofia_ids"] == ["171"] and info["admin_district"] == "Възраждане"
    assert info["catchment_ids"] == {"bg-sofia-gis": ["171"]}
    assert info["cutoffs"][0]["round_id"] == 443
    assert apply_to_school(school, keys) is False
    # Rows gone from the archive -> derived keys gone.
    assert apply_to_school(school, derived_admission_keys([], {})) is True
    assert school.admission_info == {"status": "accepting"}


@pytest.mark.asyncio
async def test_list_response_leaves_out_the_round_history(seeded_db, seeded_client):
    rows = [_row(443, "2026-09-04"), _row(445, "2026-10-02", points=8.0)]
    school = School(
        name_i18n={"bg": "ДГ №1"}, country_code="bg", city="sofia", school_type="state",
        education_level="kindergarten", admission_info={"status": "accepting", **derived_admission_keys(rows, {})},
    )
    seeded_db.add(school)
    await seeded_db.flush()
    seeded_db.add(SchoolLocation(school_id=school.id, address_i18n={"bg": "София"}, lat=42.7, lng=23.3, is_primary=True))
    await seeded_db.commit()

    listed = next(r for r in (await seeded_client.get("/schools")).json() if r["id"] == school.id)
    assert "cutoffs" not in listed["admission_info"]
    assert listed["admission_info"]["cutoff_summary"][0]["latest"]["last_admitted_points"] == 8.0
    detail = (await seeded_client.get(f"/schools/{school.id}")).json()
    assert len(detail["admission_info"]["cutoffs"]) == 2
    assert detail["admission_info"]["kg_sofia_ids"] == ["171"]


def _grade8(campaign, round_id, code, min_score, quota=False, **extra):
    return {
        "country": "bg", "city": "sofia", "stage": "grade_8", "campaign": campaign,
        "rule_set": "bg/national/grade_8", "round_id": round_id, "classified_on": f"{campaign[:4]}-07-1{round_id}",
        "round_kind": "general", "source": "ruo-sofia-grad", "source_id": "2216164", "institution_name": "164. ГПИЕ",
        "class_code": code, "class_name": "Испански", "queue": "general", "quota": quota,
        "min_score": min_score, "max_score": 490.0, **extra,
    }


def test_summary_drops_classes_only_offered_in_earlier_years():
    rows = [_grade8("2025/2026", 1, "2561", 470.0), _grade8("2026/2027", 1, "03909", 480.0)]
    assert [(e["campaign"], e["class_code"]) for e in school_cutoff_summary(rows)] == [("2026/2027", "03909")]


def test_summary_keeps_quota_minima():
    rows = [_grade8("2026/2027", 1, "02561", 475.0, quota=True, min_score_boys=475.0, min_score_girls=483.5)]
    (entry,) = school_cutoff_summary(rows)
    assert entry["quota"] is True
    assert (entry["first"]["min_score_boys"], entry["first"]["min_score_girls"]) == (475.0, 483.5)
