"""ДЗИ importer tests.

The CSV fixtures follow the layouts a third-party parser reads from these files
(atanasster/electionsbg, scripts/schools/build_index.ts); they are not copies of the
official files, which were unreachable when this was written.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.models.country import Country
from app.models.exam_results import ExamResult
from app.models.school import School
from app.schemas.school import SchoolListResponse
from app.scrapers import dzi_results
from app.scrapers.dzi_results import (
    DziSliceError,
    import_dzi_results,
    parse_dzi_csv,
    parse_dzi_dataset_resources,
    subject_key,
)
from app.scrapers.nvo_results import NvoResource

RESOURCE_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"


def _resource(year: int = 2025) -> NvoResource:
    return NvoResource(
        exam_type="dzi",
        year=year,
        title=f"ДЗИ {year}",
        dataset_url=dzi_results.DZI_DATASET_URL,
        resource_view_url=f"https://data.egov.bg/data/resourceView/{RESOURCE_ID}",
        download_url=f"https://data.egov.bg/resource/download/{RESOURCE_ID}/csv",
    )


MODERN_HEADER = (
    '"Област","Община","Населено място","Училище","Код по НЕИСПУО",'
    '"Бр. БЕЛ(ООП) З","Ср.усп. БЕЛ(ООП) З","Бр. Мат(ПП) З","Ср.усп. Мат(ПП) З",'
    '"Бр. АЕ B2(ПП) З","Ср.усп. АЕ B2(ПП) З"'
)


def _modern_csv(*rows: str) -> str:
    return "\r\n".join([MODERN_HEADER, *rows])


async def _seed_country(db_session):
    db_session.add(
        Country(
            code="bg",
            name_i18n={"bg": "България", "en": "Bulgaria"},
            education_config={"age_groups": [], "education_levels": [], "school_types": [], "shifts": [], "exam_types": []},
            map_config={},
            supported_languages=["bg", "en"],
            default_language="bg",
            default_currency="BGN",
        )
    )
    await db_session.commit()


@pytest.mark.parametrize(
    "label, expected",
    [
        ("БЕЛ(ООП)", "bulgarian_oop"),
        ("Мат(ПП)", "math_pp"),
        ("Мат(ООП)", "math_oop"),
        ("АЕ B2(ПП)", "english_b2_pp"),
        ("АЕ В1(ООП)", "english_b1_oop"),  # Cyrillic В
        ("Геогр(ПП)", "geography_pp"),
        ("ИТ(ПП)", "it_pp"),
        ("Инф(ПП)", "informatics_pp"),
        ("Фил(ПП)", "philosophy_pp"),
        ("Физ(ПП)", "physics_pp"),
        ("БЕЛ", "bulgarian"),
        # 2024–2026 headers: level after the curriculum, session tag already stripped.
        ("АЕ(ПП) B1", "english_b1_pp"),
        ("АЕ(ПП) B1.1", "english_b11_pp"),
        ("АЕ(ПП) B2", "english_b2_pp"),
        ("РЕ(ПП) B1", "russian_b1_pp"),
        ("БЗО(ПП)", "biology_pp"),
        ("ГИ(ПП)", "geography_pp"),
        ("ИИ(ПП)", None),
        ("ИсЕ(ПП) B2", "spanish_b2_pp"),
        ("ИтЕ(ПП) B2", "italian_b2_pp"),
        ("ФА(ПП)", "physics_pp"),
        ("ХООС(ПП)", "chemistry_pp"),
        ("МУЗ(ПП)", "music_pp"),
        ("Пр(ПП)", None),
    ],
)
def test_subject_key(label, expected):
    assert subject_key(label) == expected


def test_parse_dataset_resources_keeps_the_mandatory_may_june_session_per_year():
    def link(uuid_char: str, title: str) -> str:
        return f'<a href="/data/resourceView/{uuid_char * 8}-{uuid_char * 4}-{uuid_char * 4}-{uuid_char * 4}-{uuid_char * 12}">Ресурс – {title}</a>'

    html = "".join(
        [
            link("a", "Резултати от ДЗИ по училища, задължителни, сесия май-юни, учебна 2024/2025"),
            link("b", "Резултати от ДЗИ по училища, задължителни, сесия август-септември, учебна 2024/2025"),
            link("c", "Резултати от ДЗИ по училища, по желание, сесия май-юни, учебна 2024/2025"),
            link("e", "Резултати от ДЗИ по училища, задължителни, майска сесия 2023"),
            link("f", "Резултати от ДЗИ по училища, задължителни, сесия май-юни, учебна 2025/2026"),
        ]
    )

    resources = parse_dzi_dataset_resources(html)

    assert [(r.year, r.resource_view_url[-1]) for r in resources] == [(2026, "f"), (2025, "a"), (2023, "e")]
    assert resources[0].exam_type == "dzi"
    assert resources[0].download_url == f"https://data.egov.bg/resource/download/{'f' * 8}-{'f' * 4}-{'f' * 4}-{'f' * 4}-{'f' * 12}/csv"


def test_parse_modern_single_row_header():
    entries, stats = parse_dzi_csv(
        _resource(2025),
        _modern_csv(
            '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ Пенчо Славейков","2201001","110","4,85","40","5.12","35","5.40"',
            '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","ОБЩО ЗА ОБЛАСТТА","","5000","4.30","","","",""',
        ),
    )

    assert {(e.subject, e.value, e.sat_count) for e in entries} == {
        ("bulgarian_oop", 4.85, 110),
        ("math_pp", 5.12, 40),
        ("english_b2_pp", 5.40, 35),
    }
    assert all(e.institutional_id == "2201001" and e.exam_type == "dzi" for e in entries)
    assert stats["empty_rows"] == 1  # the total row has no school code


def test_parse_2026_header_with_line_breaks_inside_cells():
    header = (
        '"Област","Община","Населено място","Училище","Код по\nНЕИСПУО",'
        '"Бр.\nБЕЛ(ООП) З","Ср.усп.\nБЕЛ(ООП) З"'
    )
    entries, _ = parse_dzi_csv(
        _resource(2026),
        "\n".join([header, '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ","2201001","90","4.50"']),
    )

    assert [(e.subject, e.value, e.sat_count) for e in entries] == [("bulgarian_oop", 4.5, 90)]


def test_parse_2023_three_row_header_with_admin_code():
    rows = [
        '"Област","Община","Населено място","Училище","Код по Админ","БЕЛ(ООП)","","Мат(ПП)",""',
        '"","","","","","З","","З",""',
        '"","","","","","Бр.","Ср.усп.","Бр.","Ср.усп."',
        '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ","2201001","100","4.20","30","3.95"',
    ]

    entries, _ = parse_dzi_csv(_resource(2023), "\n".join(rows))

    assert {(e.subject, e.value, e.sat_count) for e in entries} == {
        ("bulgarian_oop", 4.2, 100),
        ("math_pp", 3.95, 30),
    }


def test_parse_withholds_a_grade_whose_pupil_count_is_suppressed():
    entries, stats = parse_dzi_csv(
        _resource(),
        _modern_csv('"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ","2201001","–","4.85","40","5.12","",""'),
    )

    assert [e.subject for e in entries] == ["math_pp"]
    assert stats["missing_counts"] == 1
    assert stats["missing_subject_values"] == 1  # nobody sat English


def test_parse_reports_unknown_subjects_instead_of_guessing_a_name():
    header = '"Област","Община","Населено място","Училище","Код по НЕИСПУО","Бр. БЕЛ(ООП) З","Ср.усп. БЕЛ(ООП) З","Бр. ХЛМ(ПП) З","Ср.усп. ХЛМ(ПП) З"'
    entries, stats = parse_dzi_csv(
        _resource(),
        "\n".join([header, '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ","2201001","90","4.50","10","5.00"']),
    )

    assert [e.subject for e in entries] == ["bulgarian_oop"]
    assert stats["unknown_subjects"] == ["ХЛМ(ПП)"]


def test_parse_refuses_a_file_in_points_not_grades():
    with pytest.raises(DziSliceError, match="points"):
        parse_dzi_csv(
            _resource(),
            _modern_csv(
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ","2201001","100","72.5","40","65.0","",""',
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","2 СУ","2201002","100","4.50","40","5.0","",""',
            ),
        )


def test_parse_refuses_a_file_without_subject_pairs():
    with pytest.raises(DziSliceError, match="no subject column pairs"):
        parse_dzi_csv(
            _resource(),
            '"Област","Община","Населено място","Училище","Код по НЕИСПУО","БЕЛ"\n"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ","2201001","4.5"',
        )


@pytest.mark.asyncio
async def test_import_stores_grades_under_their_own_exam_type_and_metric(db_session, monkeypatch):
    await _seed_country(db_session)
    school = School(
        name_i18n={"bg": "1 СУ Пенчо Славейков"},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="sofia",
        institutional_id="2201001",
    )
    other_city = School(
        name_i18n={"bg": "СУ Варна"},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="varna",
        institutional_id="3001001",
    )
    db_session.add_all([school, other_city])
    await db_session.commit()
    db_session.add(
        ExamResult(
            school_id=school.id, year=2025, exam_type="nvo_10", subject="bulgarian",
            metric="average_score", value=55.0, pupil_count=100,
        )
    )
    await db_session.commit()

    monkeypatch.setattr(
        dzi_results,
        "_fetch_text",
        AsyncMock(
            return_value=_modern_csv(
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ Пенчо Славейков","2201001","110","4.85","40","5.12","",""',
                '"ВАРНА","ВАРНА","ГР.ВАРНА","СУ Варна","3001001","80","4.00","","","",""',
            )
        ),
    )

    summary = await import_dzi_results(db_session, resources=[_resource(2025)])

    assert summary["created_rows"] == 2
    assert summary["years_imported"] == [2025]
    assert summary["matched_schools"] == 1
    rows = (await db_session.execute(select(ExamResult).where(ExamResult.exam_type == "dzi"))).scalars().all()
    assert {(r.school_id, r.subject, float(r.value), r.metric, r.pupil_count) for r in rows} == {
        (school.id, "bulgarian_oop", 4.85, "average_grade", 110),
        (school.id, "math_pp", 5.12, "average_grade", 40),
    }
    nvo = (await db_session.execute(select(ExamResult).where(ExamResult.exam_type == "nvo_10"))).scalar_one()
    assert float(nvo.value) == 55.0

    # A second run updates in place and keeps years no longer in the listing.
    summary = await import_dzi_results(db_session, resources=[_resource(2025)])
    assert summary["created_rows"] == 0
    assert summary["updated_rows"] == 2


@pytest.mark.asyncio
async def test_import_records_a_slice_failure_when_the_portal_returns_its_web_page(db_session, monkeypatch):
    await _seed_country(db_session)
    monkeypatch.setattr(
        dzi_results, "_fetch_text", AsyncMock(return_value="<!DOCTYPE html><html><body>portal</body></html>")
    )

    summary = await import_dzi_results(db_session, resources=[_resource(2025)])

    assert summary["years_imported"] == []
    assert "web page" in summary["slice_failures"][0]["error"]


@pytest.mark.asyncio
async def test_discover_reads_the_dataset_page():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == dzi_results.DZI_DATASET_URL
        return httpx.Response(
            200,
            text=f'<a href="/data/resourceView/{RESOURCE_ID}">Ресурс – ДЗИ задължителни, сесия май-юни, учебна 2025/2026</a>',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resources = await dzi_results.discover_dzi_resources(client=client)

    assert [r.year for r in resources] == [2026]


def test_list_response_leaves_out_dzi_rows():
    rows = [
        SimpleNamespace(year=2025, exam_type="nvo_7", subject="math", metric="average_score", value=60.0, pupil_count=50),
        SimpleNamespace(year=2025, exam_type="dzi", subject="bulgarian_oop", metric="average_grade", value=4.5, pupil_count=50),
    ]

    kept = SchoolListResponse.withhold_thin_exam_results(rows)

    assert [row.exam_type for row in kept] == ["nvo_7"]


@pytest.mark.asyncio
async def test_exam_averages_keeps_dzi_grades_apart_and_to_two_decimals(client, db_session):
    await _seed_country(db_session)
    schools = [
        School(name_i18n={"bg": f"СУ {i}"}, country_code="bg", school_type="state", education_level="upper_secondary", city="sofia")
        for i in range(2)
    ]
    db_session.add_all(schools)
    await db_session.flush()
    db_session.add_all(
        [
            ExamResult(school_id=schools[0].id, year=2025, exam_type="dzi", subject="bulgarian_oop", metric="average_grade", value=4.45, pupil_count=50),
            ExamResult(school_id=schools[1].id, year=2025, exam_type="dzi", subject="bulgarian_oop", metric="average_grade", value=4.60, pupil_count=50),
            # Too few pupils: kept out of the benchmark.
            ExamResult(school_id=schools[1].id, year=2025, exam_type="dzi", subject="math_pp", metric="average_grade", value=6.0, pupil_count=2),
            ExamResult(school_id=schools[0].id, year=2025, exam_type="nvo_10", subject="bulgarian", metric="average_score", value=55.0),
        ]
    )
    await db_session.commit()

    payload = (await client.get("/schools/exam-averages?country_code=bg")).json()

    assert payload["by_year"]["dzi"]["2025"] == {"bulgarian_oop": 4.53}
    assert payload["by_year"]["nvo_10"]["2025"] == {"bulgarian": 55.0}
    assert "dzi" not in payload["overall"]


def test_parse_keeps_each_language_level_and_skips_vocational_exams():
    header = (
        '"Област","Община","Населено място","Училище","Код по НЕИСПУО",'
        '"Бр. АЕ(ПП) B1-З","Ср.усп. АЕ(ПП) B1-З","Бр. АЕ(ПП) B1.1-З","Ср.усп. АЕ(ПП) B1.1-З",'
        '"Бр. АЕ(ПП) B2-З","Ср.усп. АЕ(ПП) B2-З","Бр. ДИППК-тест","Ср.усп. ДИППК-тест"'
    )
    entries, stats = parse_dzi_csv(
        _resource(2025),
        "\n".join([header, '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ","2201001","10","4.10","12","4.20","50","5.30","8","5.00"']),
    )

    assert {(e.subject, e.value) for e in entries} == {
        ("english_b1_pp", 4.1),
        ("english_b11_pp", 4.2),
        ("english_b2_pp", 5.3),
    }
    assert stats["unknown_subjects"] == []
    assert stats["subject_labels"]["АЕ(ПП) B2"] == "english_b2_pp"


def test_parse_names_the_columns_that_clash():
    header = (
        '"Област","Община","Населено място","Училище","Код по НЕИСПУО",'
        '"Бр. Био(ПП)","Ср.усп. Био(ПП)","Бр. БЗО(ПП)","Ср.усп. БЗО(ПП)"'
    )
    with pytest.raises(DziSliceError, match="biology_pp ← Био\\(ПП\\), БЗО\\(ПП\\)"):
        parse_dzi_csv(
            _resource(),
            "\n".join([header, '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","1 СУ","2201001","10","4.10","12","4.20"']),
        )


@pytest.mark.asyncio
async def test_fetch_retries_a_failed_connection_but_not_an_http_error(monkeypatch):
    from app.scrapers import nvo_results

    monkeypatch.setattr(nvo_results.asyncio, "sleep", AsyncMock())
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate has expired", request=request)
        return httpx.Response(200, content="ok".encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await nvo_results._fetch_text(client, "https://data.egov.bg/x") == "ok"
    assert calls["n"] == 3

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await nvo_results._fetch_text(client, "https://data.egov.bg/x")

    def always_fails(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("certificate has expired", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(always_fails)) as client:
        with pytest.raises(httpx.ConnectError):
            await nvo_results._fetch_text(client, "https://data.egov.bg/x")
