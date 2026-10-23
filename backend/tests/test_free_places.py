"""Free kindergarten places: ИСОДЗ record -> snapshot -> per-school counts -> API."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import School
from app.routers import schools as schools_router
from app.scrapers.sources.bg.kg_sofia_free_places import building_free_places
from app.services import free_places
from scripts.capture_isodz_free_places import check_scan


def _spot(kind, capacity, enrolled, free, ranked):
    return {"type": kind, "capacity": capacity, "zapisani": enrolled, "svobodni": free, "nezapisani": ranked, "uncommitted": 0}


def _dz(groups, dz_id=171, name="ДГ №1 Червената шапчица", feature=None):
    return {
        "items": {
            "dz": {"id": dz_id, "nameFull": name, "region": "Възраждане", "currentGroups": groups, "featureGroups": feature},
            "schoolYear": "2026/2027",
            "schoolYearNext": "2027/2028",
        }
    }


def _group(group_id, birth_year, label, spots, active=1):
    return {"groupId": group_id, "draft": birth_year, "label": label, "spots": spots, "countGroups": 1.0, "active": active}


NURSERY = _group(9006, 2024, "2-ра ясла, родени 2024 г.", [
    _spot("general", 22, 22, 0, 0), _spot("chronic", 0, 0, 0, 0), _spot("sop", 0, 0, 0, 0), _spot("workgroup", -1, 0, -1, -1),
])
FIRST = _group(9007, 2023, "1-ва група, родени 2023 г.", [
    _spot("general", 26, 25, 0, 1), _spot("chronic", 1, 0, 1, 0), _spot("sop", 1, 0, 1, 0),
])
PRESCHOOL = _group(9010, 2020, "Подготвителна група, родени 2020 г.", [_spot("general", 52, 40, 12, 0)])


def test_building_keeps_counts_per_queue_and_drops_empty_queues():
    building = building_free_places(_dz([NURSERY, FIRST, PRESCHOOL]), "kindergarten")
    assert building["source_id"] == "171"
    by_year = {g["birth_year"]: g for g in building["groups"]}
    assert list(by_year) == [2024, 2023, 2020]  # newest birth year first
    assert by_year[2024]["stage"] == "nursery"
    assert by_year[2024]["age_group"] == "nursery"
    assert by_year[2024]["queues"] == {"general": {"capacity": 22, "enrolled": 22, "free": 0, "ranked_not_enrolled": 0}}
    assert set(by_year[2023]["queues"]) == {"general", "chronic", "sop"}
    assert by_year[2023]["queues"]["general"]["ranked_not_enrolled"] == 1
    assert by_year[2020]["stage"] == "kindergarten"
    assert by_year[2020]["age_group"] == "preschool"
    assert by_year[2020]["campaign"] == "2026/2027"


def test_school_preparatory_groups_and_next_year():
    nxt = _group(9900, 2021, "Подготвителна група, родени 2021 г.", [_spot("general", 22, 0, 22, 0)])
    building = building_free_places(_dz([PRESCHOOL], dz_id=366, name="5. ОУ \"Иван Вазов\"", feature=[nxt]), "preparatory")
    assert [(g["birth_year"], g["campaign"], g["stage"]) for g in building["groups"]] == [
        (2020, "2026/2027", "preparatory"), (2021, "2027/2028", "preparatory"),
    ]


def test_inactive_unknown_and_placeless_groups_are_skipped():
    inactive = _group(1, 2023, "1-ва група, родени 2023 г.", [_spot("general", 20, 0, 20, 0)], active=0)
    unknown = _group(2, 2023, "Сборна група", [_spot("general", 20, 0, 20, 0)])
    placeless = _group(3, 2023, "1-ва група, родени 2023 г.", [_spot("general", -1, 0, -1, -1)])
    assert building_free_places(_dz([inactive, unknown, placeless]), "kindergarten") is None
    assert building_free_places({"items": {"dz": None}}, "kindergarten") is None


def test_check_scan_refuses_a_broken_scan():
    buildings = [{"source_id": str(i)} for i in range(100)]
    assert check_scan(buildings, 0, 120, {"buildings": buildings}) is None
    assert check_scan([], 0, 0, None) == "no building has groups"
    assert "failed" in check_scan(buildings, 30, 120, None)
    assert "last snapshot had 100" in check_scan(buildings[:70], 0, 120, {"buildings": buildings})


def _snapshot(buildings, retrieved_at=None):
    retrieved_at = retrieved_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"meta": {"retrieved_at": retrieved_at, "as_of": retrieved_at[:10], "source_url": "https://kg.sofia.bg/spots"}, "buildings": buildings}


def test_freshness():
    now = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)
    assert free_places.snapshot_is_fresh(_snapshot([], (now - timedelta(hours=30)).isoformat()), now)
    assert not free_places.snapshot_is_fresh(_snapshot([], (now - timedelta(hours=49)).isoformat()), now)
    assert not free_places.snapshot_is_fresh({"meta": {}}, now)


def test_places_by_school_matches_kindergartens_by_id_and_schools_by_number():
    kindergarten = building_free_places(_dz([FIRST, PRESCHOOL]), "kindergarten")
    branch = building_free_places(_dz([NURSERY], dz_id=479, name="ДГ №1, филиал"), "kindergarten")
    school_pg = building_free_places(_dz([PRESCHOOL], dz_id=366, name="5. ОУ \"Иван Вазов\""), "preparatory")
    stray = building_free_places(_dz([PRESCHOOL], dz_id=999, name="ДГ без съвпадение"), "kindergarten")
    schools = [
        SimpleNamespace(id=10, attributes={"kg_sofia_id": 171, "kg_sofia_ids": [171, 479]}, institutional_id=None,
                        school_type="state", education_level="kindergarten", name_i18n={"bg": "ДГ 1"}),
        SimpleNamespace(id=20, attributes={}, institutional_id="2205005", school_type="state",
                        education_level="primary", name_i18n={"bg": "5 ОУ Иван Вазов"}),
    ]
    out, unmatched = free_places.places_by_school(_snapshot([kindergarten, branch, school_pg, stray]), schools)
    assert unmatched == 1
    assert {e["birth_year"] for e in out[10]} == {2024, 2023, 2020}
    assert {e["building"] for e in out[10]} == {"ДГ №1 Червената шапчица", "ДГ №1, филиал"}
    first = next(e for e in out[10] if e["birth_year"] == 2023)
    assert (first["free"], first["ranked_not_enrolled"], first["free_sop"], first["free_chronic"]) == (0, 1, 1, 1)
    assert out[20] == [{
        "birth_year": 2020, "campaign": "2026/2027", "stage": "preparatory", "age_group": "preschool",
        "label": "Подготвителна група, родени 2020 г.", "capacity": 52, "enrolled": 40, "free": 12, "ranked_not_enrolled": 0,
    }]


@pytest.mark.asyncio
async def test_api_serves_places_per_school_and_nothing_when_stale(client, db_session, monkeypatch):
    school = School(name_i18n={"bg": "ДГ 1", "en": "KG 1"}, country_code="bg", city="sofia", school_type="state",
                    education_level="kindergarten", attributes={"kg_sofia_id": 171})
    db_session.add(school)
    await db_session.commit()
    await db_session.refresh(school)

    snapshot = _snapshot([building_free_places(_dz([PRESCHOOL]), "kindergarten")])

    async def fake_fetch(url, timeout=20.0):
        return snapshot

    monkeypatch.setattr(free_places, "fetch_snapshot", fake_fetch)
    response = await client.get("/schools/free-places")
    assert response.status_code == 200
    body = response.json()
    assert body["as_of"] == snapshot["meta"]["as_of"]
    assert body["schools"][str(school.id)][0]["free"] == 12

    schools_router._free_places_cache.clear()
    snapshot = _snapshot(snapshot["buildings"], (datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
    body = (await client.get("/schools/free-places")).json()
    assert body["schools"] == {}
    assert body["retrieved_at"] == snapshot["meta"]["retrieved_at"]

    schools_router._free_places_cache.clear()
    assert (await client.get("/schools/free-places?city=plovdiv")).json()["schools"] == {}


@pytest.mark.asyncio
async def test_fetch_reuses_the_snapshot_on_304(monkeypatch):
    import httpx

    seen = []

    def handler(request):
        seen.append(request.headers.get("if-none-match"))
        if request.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"meta": {"as_of": "2026-10-23"}}, headers={"ETag": '"v1"'})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(free_places.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(free_places, "_last", {})
    first = await free_places.fetch_snapshot("https://example.test/free-places.json")
    second = await free_places.fetch_snapshot("https://example.test/free-places.json")
    assert first == second == {"meta": {"as_of": "2026-10-23"}}
    assert seen == [None, '"v1"']


def test_first_grade_records_match_by_school_number_never_by_kindergarten_id():
    grade_1 = _group(9500, 2019, "1-ви клас, родени 2019 г.", [_spot("general", 78, 75, 3, 0)])
    # A 1st-grade record whose id equals a kindergarten's ИСОДЗ id.
    school_record = building_free_places(_dz([grade_1], dz_id=171, name="5. ОУ \"Иван Вазов\""), "grade_1")
    assert school_record["groups"][0]["stage"] == "grade_1"
    assert "age_group" not in school_record["groups"][0] or school_record["groups"][0]["age_group"] is None
    schools = [
        SimpleNamespace(id=10, attributes={"kg_sofia_id": 171}, institutional_id=None,
                        school_type="state", education_level="kindergarten", name_i18n={"bg": "ДГ 1"}),
        SimpleNamespace(id=20, attributes={}, institutional_id="2205005", school_type="state",
                        education_level="primary", name_i18n={"bg": "5 ОУ Иван Вазов"}),
    ]
    out, unmatched = free_places.places_by_school(_snapshot([school_record]), schools)
    assert unmatched == 0
    assert list(out) == [20]
    assert out[20][0]["free"] == 3 and out[20][0]["stage"] == "grade_1"


async def _matchable_schools_read_only_what_matching_needs(db_session):
    from app.models.country import Country

    if await db_session.get(Country, "bg") is None:
        db_session.add(Country(code="bg", name_i18n={"bg": "България", "en": "Bulgaria"}, education_config={}, map_config={},
                               supported_languages=["bg", "en"], default_language="bg", default_currency="EUR"))
        await db_session.flush()
    db_session.add_all([
        School(name_i18n={"bg": "ДГ 1"}, country_code="bg", city="sofia", school_type="state",
               education_level="kindergarten", attributes={"kg_sofia_id": 171, "kg_sofia_ids": [171, 479], "school_facts": {"x": 1}},
               admission_info={"cutoffs": [{"round_id": 1}] * 50}),
        School(name_i18n={"bg": "5 ОУ Иван Вазов"}, country_code="bg", city="sofia", school_type="state",
               education_level="primary", institutional_id="2205005"),
    ])
    await db_session.commit()
    schools = sorted(await free_places.matchable_schools(db_session), key=lambda s: s.education_level)
    assert schools[0].attributes == {"kg_sofia_id": 171, "kg_sofia_ids": [171, 479]}
    assert schools[1].attributes == {"kg_sofia_id": None, "kg_sofia_ids": None}
    assert schools[1].institutional_id == "2205005"
    snapshot = _snapshot([
        building_free_places(_dz([FIRST], dz_id=479, name="ДГ 1, филиал"), "kindergarten"),
        building_free_places(_dz([PRESCHOOL], dz_id=366, name="5. ОУ \"Иван Вазов\""), "preparatory"),
    ])
    out, unmatched = free_places.places_by_school(snapshot, schools)
    assert unmatched == 0 and len(out) == 2


@pytest.mark.asyncio
async def test_matchable_schools(db_session):
    await _matchable_schools_read_only_what_matching_needs(db_session)


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_matchable_schools_postgres(db_session):
    await _matchable_schools_read_only_what_matching_needs(db_session)
