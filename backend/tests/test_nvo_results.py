from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.models.country import Country
from app.models.exam_results import ExamResult
from app.models.school import School
from app.models.scrape_log import ScrapeLog, ScrapeType
from app.scrapers import nvo_results
from app.scrapers.nvo_results import (
    NvoResource,
    NvoSliceError,
    import_nvo_results,
    parse_dataset_resources,
    parse_index_dataset_links,
    parse_nvo_csv,
)
from app.utils.display_gating import NVO_MIN_PUPILS


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


def _resource(exam_type: str = "nvo_7", year: int = 2025) -> NvoResource:
    resource_id = {
        "nvo_4": "af1c2e60-3f7e-4ad1-b393-979f8e56735c",
        "nvo_7": "1bd20af9-df82-4908-aae4-d2d2a0eaef81",
        "nvo_10": "d85471ff-a58c-468a-a1d9-9de9d1efb4b8",
    }[exam_type]
    return NvoResource(
        exam_type=exam_type,
        year=year,
        title=f"{exam_type} {year}",
        dataset_url="https://data.egov.bg/data/view/example",
        resource_view_url=f"https://data.egov.bg/data/resourceView/{resource_id}",
        download_url=f"https://data.egov.bg/resource/download/{resource_id}/csv",
    )


def _csv_text(*rows: str) -> str:
    header = (
        '"Област","Община","Населено място","Училище","Код по НЕИСПУО",'
        '"БЕЛ Явили се","БЕЛ Ср. успех в точки","МАТ Явили се","МАТ Ср. успех в точки"'
    )
    return "\n".join([header, *rows])


def test_parse_index_dataset_links_extracts_all_exam_types():
    html = """
    <html><body>
      <a href="https://data.egov.bg/data/view/5613e75f-2b1b-4244-9f54-b27580a91dfb">НВО 4 клас</a>
      <a href="https://data.egov.bg/data/view/b56288b6-25aa-4049-9aa6-de2cd4cdabf8">НВО 7 клас</a>
      <a href="https://data.egov.bg/data/view/2f801b2f-d4cb-4ddb-a23d-3e372339c80f">НВО 10 клас</a>
    </body></html>
    """

    result = parse_index_dataset_links(html)

    assert result == {
        "nvo_4": "https://data.egov.bg/data/view/5613e75f-2b1b-4244-9f54-b27580a91dfb",
        "nvo_7": "https://data.egov.bg/data/view/b56288b6-25aa-4049-9aa6-de2cd4cdabf8",
        "nvo_10": "https://data.egov.bg/data/view/2f801b2f-d4cb-4ddb-a23d-3e372339c80f",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("index_status, index_body", [(403, "Forbidden"), (200, "<html></html>")])
async def test_discover_uses_known_dataset_pages_when_index_is_unusable(index_status, index_body):
    resource_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.host == "io.mon.bg":
            return httpx.Response(index_status, text=index_body)
        return httpx.Response(
            200,
            text=f'<a href="/data/resourceView/{resource_id}">Ресурс – учебна 2025/2026 година</a>',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        resources = await nvo_results.discover_nvo_resources(["nvo_7"], client=client)

    assert requested == [
        nvo_results.IO_MON_NVO_INDEX_URL,
        nvo_results.KNOWN_NVO_DATASET_URLS["nvo_7"],
    ]
    assert [(r.exam_type, r.year, r.download_url) for r in resources] == [
        ("nvo_7", 2026, f"https://data.egov.bg/resource/download/{resource_id}/csv")
    ]


def test_parse_dataset_resources_returns_latest_years_sorted():
    html = """
    <html><body>
      <a href="/data/resourceView/old">Ресурс – Резултати по училища от националното външно оценяване за VII клас - учебна 2022/2023 година</a>
      <a href="/data/resourceView/new">Ресурс – Резултати по училища от националното външно оценяване за VII клас - учебна 2024/2025 година</a>
      <a href="/data/resourceView/mid">Ресурс – Резултати по училища от националното външно оценяване за VII клас - учебна 2023/2024 година</a>
    </body></html>
    """.replace("/data/resourceView/old", "/data/resourceView/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa") \
        .replace("/data/resourceView/new", "/data/resourceView/bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb") \
        .replace("/data/resourceView/mid", "/data/resourceView/cccccccc-cccc-cccc-cccc-cccccccccccc")

    resources = parse_dataset_resources("nvo_7", "https://data.egov.bg/data/view/demo", html)

    assert [resource.year for resource in resources] == [2025, 2024, 2023]


def test_parse_nvo_csv_extracts_subject_scores_and_missing_subject_gaps():
    resource = _resource("nvo_7", 2025)
    csv_text = _csv_text(
        '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","35 СУ Добри Войников","222222","120","78.5","120","65.25"',
        '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","36 СУ Максим Горки","333333","80","70.1","80",""',
    )

    entries, stats = parse_nvo_csv(resource, csv_text)

    assert len(entries) == 3
    assert {entry.subject for entry in entries} == {"bulgarian", "math"}
    assert stats["missing_subject_values"] == 1
    assert entries[0].year == 2025
    assert entries[0].source_url == resource.resource_view_url


def test_parse_nvo_csv_skips_zero_average_when_nobody_sat_the_subject():
    # Rows as published for 2025 nvo_10: "0","0" means no pupil sat that subject.
    resource = _resource("nvo_10", 2025)
    csv_text = _csv_text(
        '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","ЧСУ Дружба","2203720","0","0","1","80"',
        '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","ЧСУ Орфей","2208526","0","0","0","0"',
        '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","35 СУ Добри Войников","222222","0","55.5","","0"',
    )

    entries, stats = parse_nvo_csv(resource, csv_text)

    assert [(entry.institutional_id, entry.subject, entry.value) for entry in entries] == [
        ("2203720", "math", 80.0)
    ]
    assert stats["missing_subject_values"] == 5
    assert stats["empty_rows"] == 2


def test_parse_nvo_csv_skips_zero_participants_in_legacy_reversed_headers():
    resource = _resource("nvo_4", 2021)
    csv_text = "\n".join(
        [
            '"Област","Община","Населено място","Код по Админ","Училище",'
            '"Явили се БЕЛ","Ср. успех в точки БЕЛ","Явили се МАТ","Ср. успех в точки МАТ"',
            '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","2 208 526","ЧСУ Орфей","0","0","12","71,5"',
        ]
    )

    entries, stats = parse_nvo_csv(resource, csv_text)

    assert [(entry.subject, entry.value) for entry in entries] == [("math", 71.5)]
    assert stats["missing_subject_values"] == 1


def test_parse_nvo_csv_handles_legacy_two_row_header_with_admin_code():
    resource = _resource("nvo_4", 2023)
    csv_text = "\n".join(
        [
            '"Област","Община","Населено място","Училище","Код по Админ","БЕЛ","","МАТ",""',
            '"","","","","","Явили се","Ср. успех в точки","Явили се","Ср. успех в точки"',
            '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","35 СУ Добри Войников","222222","120","78.5","120","65.25"',
        ]
    )

    entries, stats = parse_nvo_csv(resource, csv_text)

    assert len(entries) == 2
    assert {entry.subject for entry in entries} == {"bulgarian", "math"}
    assert all(entry.institutional_id == "222222" for entry in entries)
    assert stats["missing_subject_values"] == 0


def test_parse_nvo_csv_handles_legacy_preamble_and_secondary_subject_row():
    resource = _resource("nvo_7", 2023)
    csv_text = "\n".join(
        [
            '"Резултати по училища от националното външно оценяване за VII клас - учебна 2022/2023 година","","","","","","","",""',
            '"Максимален бал","","","","","","","",""',
            '"Български език и литература (БЕЛ)","","100","","","","","",""',
            '"Математика (МАТ)","","100","","","","","",""',
            '"Област","Община","Населено място","Училище","Код по Админ","БЕЛ","","МАТ",""',
            '"","","","","","Явили се","Ср. успех в точки","Явили се","Ср. успех в точки"',
            '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","36 СУ Максим Горки","333333","80","70.1","80","61.4"',
        ]
    )

    entries, _ = parse_nvo_csv(resource, csv_text)

    assert len(entries) == 2
    assert {entry.subject for entry in entries} == {"bulgarian", "math"}
    assert all(entry.city_key == "sofia" for entry in entries)


def test_parse_nvo_csv_handles_legacy_2021_reversed_subject_headers():
    resource = _resource("nvo_7", 2021)
    csv_text = "\n".join(
        [
            '"Област","Община","Населено място","Училище","Код по Админ","Явили се","Ср. успех в точки","Явили се","Ср. успех в точки"',
            '"","","","","","БЕЛ","БЕЛ","МАТ","МАТ"',
            '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","37 СУ Елин Пелин","444444","84","67,13","84","47,46"',
        ]
    )

    entries, _ = parse_nvo_csv(resource, csv_text)

    assert len(entries) == 2
    values = {entry.subject: entry.value for entry in entries}
    assert values["bulgarian"] == 67.13
    assert values["math"] == 47.46


def test_parse_nvo_csv_raises_when_required_subject_column_missing():
    resource = _resource("nvo_4", 2025)
    csv_text = (
        '"Училище","Код по НЕИСПУО","Населено място","БЕЛ Ср. успех в точки"\n'
        '"Тест училище","123456","ГР.СОФИЯ","81.5"'
    )

    with pytest.raises(NvoSliceError):
        parse_nvo_csv(resource, csv_text)


@pytest.mark.asyncio
async def test_import_nvo_results_matches_by_institutional_id_and_logs_run(db_session, monkeypatch):
    await _seed_country(db_session)
    school = School(
        name_i18n={"bg": "35 СУ Добри Войников", "en": "35 SU Dobri Voynikov"},
        country_code="bg",
        school_type="state",
        education_level="lower_secondary",
        city="sofia",
        institutional_id="222222",
    )
    db_session.add(school)
    await db_session.commit()

    resource = _resource("nvo_7", 2025)
    monkeypatch.setattr(nvo_results, "discover_nvo_resources", AsyncMock(return_value=[resource]))
    monkeypatch.setattr(
        nvo_results,
        "_download_resource_csv",
        AsyncMock(
            return_value=_csv_text(
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","35 СУ Добри Войников","222222","120","78.5","120","65.25"'
            )
        ),
    )

    summary = await import_nvo_results(
        db_session,
        country_code="bg",
        city="sofia",
        year=2025,
        exam_types=["nvo_7"],
    )

    assert summary["created_rows"] == 2
    assert summary["updated_rows"] == 0
    assert summary["matched_schools"] == 1
    assert summary["years_imported"] == [2025]

    result = await db_session.execute(select(ExamResult))
    exam_results = result.scalars().all()
    assert len(exam_results) == 2
    assert {row.subject for row in exam_results} == {"bulgarian", "math"}
    assert all(row.metric == "average_score" for row in exam_results)
    assert all(row.source_url == resource.resource_view_url for row in exam_results)

    log_result = await db_session.execute(select(ScrapeLog).where(ScrapeLog.scrape_type == ScrapeType.NVO))
    log = log_result.scalar_one()
    assert log.school_id is None


@pytest.mark.asyncio
async def test_import_nvo_results_falls_back_to_unique_name_city_match(db_session, monkeypatch):
    await _seed_country(db_session)
    db_session.add(
        School(
            name_i18n={"bg": "36 СУ Максим Горки", "en": "36 SU Maxim Gorki"},
            country_code="bg",
            school_type="state",
            education_level="lower_secondary",
            city="sofia",
        )
    )
    await db_session.commit()

    resource = _resource("nvo_7", 2025)
    monkeypatch.setattr(nvo_results, "discover_nvo_resources", AsyncMock(return_value=[resource]))
    monkeypatch.setattr(
        nvo_results,
        "_download_resource_csv",
        AsyncMock(
            return_value=_csv_text(
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","36 СУ Максим Горки","","80","70.1","80","61.4"'
            )
        ),
    )

    summary = await import_nvo_results(
        db_session,
        country_code="bg",
        city="sofia",
        exam_types=["nvo_7"],
        year=2025,
    )

    assert summary["created_rows"] == 2
    assert summary["unmatched_rows"] == 0


@pytest.mark.asyncio
async def test_import_nvo_results_skips_ambiguous_name_city_match(db_session, monkeypatch):
    await _seed_country(db_session)
    db_session.add_all(
        [
            School(
                name_i18n={"bg": "37 СУ Елин Пелин"},
                country_code="bg",
                school_type="state",
                education_level="lower_secondary",
                city="sofia",
            ),
            School(
                name_i18n={"bg": "37 СУ Елин Пелин"},
                country_code="bg",
                school_type="state",
                education_level="lower_secondary",
                city="sofia",
            ),
        ]
    )
    await db_session.commit()

    resource = _resource("nvo_7", 2025)
    monkeypatch.setattr(nvo_results, "discover_nvo_resources", AsyncMock(return_value=[resource]))
    monkeypatch.setattr(
        nvo_results,
        "_download_resource_csv",
        AsyncMock(
            return_value=_csv_text(
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","37 СУ Елин Пелин","","80","70.1","80","61.4"'
            )
        ),
    )

    summary = await import_nvo_results(
        db_session,
        country_code="bg",
        city="sofia",
        year=2025,
        exam_types=["nvo_7"],
    )

    assert summary["created_rows"] == 0
    assert summary["unmatched_rows"] == 2


@pytest.mark.asyncio
async def test_import_nvo_results_updates_existing_rows_and_scraped_at(db_session, monkeypatch):
    await _seed_country(db_session)
    school = School(
        name_i18n={"bg": "38 СУ Васил Левски"},
        country_code="bg",
        school_type="state",
        education_level="lower_secondary",
        city="sofia",
        institutional_id="383838",
    )
    db_session.add(school)
    await db_session.commit()

    resource = _resource("nvo_7", 2025)
    monkeypatch.setattr(nvo_results, "discover_nvo_resources", AsyncMock(return_value=[resource]))
    monkeypatch.setattr(
        nvo_results,
        "_download_resource_csv",
        AsyncMock(
            return_value=_csv_text(
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","38 СУ Васил Левски","383838","80","70.1","80","61.4"'
            )
        ),
    )

    first_time = datetime(2026, 1, 1, 12, 0, 0)
    second_time = datetime(2026, 1, 2, 12, 0, 0)
    monkeypatch.setattr(nvo_results, "_utcnow", lambda: first_time)
    first_summary = await import_nvo_results(db_session, country_code="bg", city="sofia", year=2025)
    assert first_summary["created_rows"] == 2

    monkeypatch.setattr(nvo_results, "_utcnow", lambda: second_time)
    second_summary = await import_nvo_results(db_session, country_code="bg", city="sofia", year=2025)
    assert second_summary["updated_rows"] == 2

    result = await db_session.execute(select(ExamResult).order_by(ExamResult.subject))
    rows = result.scalars().all()
    assert all(row.scraped_at == second_time for row in rows)


@pytest.mark.asyncio
async def test_import_nvo_results_matches_register_code_whatever_settlement_the_file_names(
    db_session, monkeypatch
):
    """Sofia municipality's villages and mistyped city cells still belong to our Sofia schools."""
    await _seed_country(db_session)
    village = School(
        name_i18n={"bg": "117. Средно училище"},
        country_code="bg",
        school_type="state",
        education_level="secondary",
        city="sofia",
        institutional_id="2210117",
    )
    mistyped = School(
        name_i18n={"bg": "Професионална гимназия по транспорт"},
        country_code="bg",
        school_type="state",
        education_level="secondary",
        city="sofia",
        institutional_id="2207412",
    )
    db_session.add_all([village, mistyped])
    await db_session.commit()

    monkeypatch.setattr(nvo_results, "discover_nvo_resources", AsyncMock(return_value=[_resource("nvo_7", 2025)]))
    monkeypatch.setattr(
        nvo_results,
        "_download_resource_csv",
        AsyncMock(
            return_value=_csv_text(
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.БУХОВО","117. СУ","2210117","23","69.98","23","49.28"',
                '"СОФИЯ-ГРАД","СТОЛИЧНА","офия 14 р-н Искър","ПГ по транспорт","2207412","72","31.69","72","15.52"',
                '"ПЛОВДИВ","ПЛОВДИВ","ГР.ПЛОВДИВ","Друго училище","515151","100","82.2","100","79.1"',
                # In our municipality, under a code we do not have: a school missing from the DB.
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.БАНКЯ","ЧНУ Фоти","2201505","12","70.1","12","66.3"',
            )
        ),
    )

    summary = await import_nvo_results(db_session, country_code="bg", city="sofia", year=2025, exam_types=["nvo_7"])

    assert summary["matched_schools"] == 2
    assert summary["created_rows"] == 4
    assert summary["skipped_rows"] == 2
    assert summary["unmatched_rows"] == 2


@pytest.mark.asyncio
async def test_import_nvo_results_ignores_city_filter_for_explicit_school_ids(db_session, monkeypatch):
    await _seed_country(db_session)
    school = School(
        name_i18n={"bg": "Математическа гимназия Пловдив"},
        country_code="bg",
        school_type="state",
        education_level="lower_secondary",
        city="plovdiv",
        institutional_id="515151",
    )
    db_session.add(school)
    await db_session.commit()

    resource = _resource("nvo_7", 2025)
    monkeypatch.setattr(nvo_results, "discover_nvo_resources", AsyncMock(return_value=[resource]))
    monkeypatch.setattr(
        nvo_results,
        "_download_resource_csv",
        AsyncMock(
            return_value=_csv_text(
                '"ПЛОВДИВ","ПЛОВДИВ","ГР.ПЛОВДИВ","Математическа гимназия Пловдив","515151","100","82.2","100","79.1"'
            )
        ),
    )

    summary = await import_nvo_results(
        db_session,
        country_code="bg",
        city="sofia",
        year=2025,
        exam_types=["nvo_7"],
        school_ids=[school.id],
    )

    assert summary["created_rows"] == 2
    assert summary["matched_schools"] == 1


@pytest.mark.asyncio
async def test_import_nvo_results_records_slice_failures_on_network_error(db_session, monkeypatch):
    await _seed_country(db_session)
    db_session.add(
        School(
            name_i18n={"bg": "39 СУ Христо Смирненски"},
            country_code="bg",
            school_type="state",
            education_level="lower_secondary",
            city="sofia",
            institutional_id="393939",
        )
    )
    await db_session.commit()

    resource = _resource("nvo_7", 2025)
    monkeypatch.setattr(nvo_results, "discover_nvo_resources", AsyncMock(return_value=[resource]))
    request = httpx.Request("GET", resource.download_url)
    monkeypatch.setattr(
        nvo_results,
        "_download_resource_csv",
        AsyncMock(side_effect=httpx.ConnectError("boom", request=request)),
    )

    summary = await import_nvo_results(
        db_session,
        country_code="bg",
        city="sofia",
        year=2025,
        exam_types=["nvo_7"],
    )

    assert summary["created_rows"] == 0
    assert len(summary["slice_failures"]) == 1
    assert summary["errors"]

    result = await db_session.execute(select(ExamResult))
    assert result.scalars().all() == []


@pytest.mark.asyncio
async def test_exam_averages_endpoint_uses_canonical_average_score_metric(client, db_session):
    await _seed_country(db_session)
    school = School(
        name_i18n={"bg": "40 СУ Луи Пастьор"},
        country_code="bg",
        school_type="state",
        education_level="lower_secondary",
        city="sofia",
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add_all(
        [
            ExamResult(
                school_id=school.id,
                year=2025,
                exam_type="nvo_7",
                subject="bulgarian",
                metric="average_score",
                value=72.5,
            ),
            ExamResult(
                school_id=school.id,
                year=2025,
                exam_type="nvo_7",
                subject="math",
                metric="average_score",
                value=61.0,
            ),
            ExamResult(
                school_id=school.id,
                year=2025,
                exam_type="nvo_7",
                subject="foreign_language",
                metric="average_score",
                value=80.0,
            ),
            ExamResult(
                school_id=school.id,
                year=2025,
                exam_type="nvo_7",
                subject="history",
                metric="school_average",
                value=99.0,
            ),
        ]
    )
    await db_session.commit()

    response = await client.get("/schools/exam-averages?country_code=bg")

    assert response.status_code == 200
    payload = response.json()
    assert payload["by_year"]["nvo_7"]["2025"]["bulgarian"] == 72.5
    assert payload["by_year"]["nvo_7"]["2025"]["math"] == 61.0
    assert payload["by_year"]["nvo_7"]["2025"]["foreign_language"] == 80.0
    assert "history" not in payload["by_year"]["nvo_7"]["2025"]


def test_parse_nvo_csv_reads_the_pupil_count_next_to_each_average():
    resource = _resource("nvo_10", 2025)
    csv_text = _csv_text(
        '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","ЧСУ Дружба","2203720","0","0","1","80"',
        '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","35 СУ Добри Войников","222222","118","55.5","120","41"',
    )

    entries, _ = parse_nvo_csv(resource, csv_text)

    assert [(entry.institutional_id, entry.subject, entry.sat_count) for entry in entries] == [
        ("2203720", "math", 1),
        ("222222", "bulgarian", 118),
        ("222222", "math", 120),
    ]


def test_parse_nvo_csv_reads_the_pupil_count_in_legacy_reversed_headers():
    resource = _resource("nvo_4", 2021)
    csv_text = "\n".join(
        [
            '"Област","Община","Населено място","Код по Админ","Училище",'
            '"Явили се БЕЛ","Ср. успех в точки БЕЛ","Явили се МАТ","Ср. успех в точки МАТ"',
            '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","2 208 526","ЧСУ Орфей","3","60","12","71,5"',
        ]
    )

    entries, _ = parse_nvo_csv(resource, csv_text)

    assert [(entry.subject, entry.sat_count) for entry in entries] == [("bulgarian", 3), ("math", 12)]


def test_parse_nvo_csv_leaves_the_pupil_count_unknown_when_the_file_has_none():
    resource = _resource("nvo_7", 2025)
    csv_text = "\n".join(
        [
            '"Област","Община","Населено място","Училище","Код по НЕИСПУО",'
            '"БЕЛ Ср. успех в точки","МАТ Ср. успех в точки"',
            '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","35 СУ Добри Войников","222222","78.5","65.25"',
        ]
    )

    entries, _ = parse_nvo_csv(resource, csv_text)

    assert [entry.sat_count for entry in entries] == [None, None]


@pytest.mark.asyncio
async def test_import_nvo_results_stores_the_pupil_count_and_backfills_existing_rows(db_session, monkeypatch):
    await _seed_country(db_session)
    school = School(
        name_i18n={"bg": "ЧСУ Дружба"},
        country_code="bg",
        school_type="private",
        education_level="upper_secondary",
        city="sofia",
        institutional_id="2203720",
    )
    db_session.add(school)
    await db_session.flush()
    # A row imported before the count was stored.
    db_session.add(
        ExamResult(
            school_id=school.id,
            year=2025,
            exam_type="nvo_10",
            subject="math",
            metric="average_score",
            value=80,
        )
    )
    await db_session.commit()

    resource = _resource("nvo_10", 2025)
    monkeypatch.setattr(nvo_results, "discover_nvo_resources", AsyncMock(return_value=[resource]))
    monkeypatch.setattr(
        nvo_results,
        "_download_resource_csv",
        AsyncMock(
            return_value=_csv_text(
                '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","ЧСУ Дружба","2203720","14","61.5","1","80"'
            )
        ),
    )

    summary = await import_nvo_results(db_session, country_code="bg", city="sofia", year=2025)

    assert (summary["created_rows"], summary["updated_rows"]) == (1, 1)
    rows = (await db_session.execute(select(ExamResult).order_by(ExamResult.subject))).scalars().all()
    assert [(row.subject, row.pupil_count) for row in rows] == [("bulgarian", 14), ("math", 1)]


@pytest.mark.asyncio
async def test_import_nvo_results_keeps_a_stored_pupil_count_when_the_file_has_none(db_session, monkeypatch):
    await _seed_country(db_session)
    school = School(
        name_i18n={"bg": "ЧСУ Дружба"},
        country_code="bg",
        school_type="private",
        education_level="upper_secondary",
        city="sofia",
        institutional_id="2203720",
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add(
        ExamResult(
            school_id=school.id,
            year=2025,
            exam_type="nvo_10",
            subject="math",
            metric="average_score",
            value=80,
            pupil_count=1,
        )
    )
    await db_session.commit()

    resource = _resource("nvo_10", 2025)
    monkeypatch.setattr(nvo_results, "discover_nvo_resources", AsyncMock(return_value=[resource]))
    monkeypatch.setattr(
        nvo_results,
        "_download_resource_csv",
        AsyncMock(
            return_value="\n".join(
                [
                    '"Област","Община","Населено място","Училище","Код по НЕИСПУО",'
                    '"БЕЛ Ср. успех в точки","МАТ Ср. успех в точки"',
                    '"СОФИЯ-ГРАД","СТОЛИЧНА","ГР.СОФИЯ","ЧСУ Дружба","2203720","","82"',
                ]
            )
        ),
    )

    await import_nvo_results(db_session, country_code="bg", city="sofia", year=2025)

    row = (await db_session.execute(select(ExamResult))).scalar_one()
    assert (float(row.value), row.pupil_count) == (82.0, 1)


@pytest.mark.asyncio
async def test_exam_averages_endpoint_leaves_out_results_with_too_few_pupils(client, db_session):
    await _seed_country(db_session)
    schools = [
        School(
            name_i18n={"bg": f"School {idx}"},
            country_code="bg",
            school_type="state",
            education_level="upper_secondary",
            city="sofia",
        )
        for idx in range(3)
    ]
    db_session.add_all(schools)
    await db_session.flush()
    for school, value, pupil_count in zip(
        schools, (40.0, 50.0, 90.0), (120, None, NVO_MIN_PUPILS - 1)
    ):
        db_session.add(
            ExamResult(
                school_id=school.id,
                year=2025,
                exam_type="nvo_10",
                subject="math",
                metric="average_score",
                value=value,
                pupil_count=pupil_count,
            )
        )
    await db_session.commit()

    response = await client.get("/schools/exam-averages?country_code=bg")

    assert response.status_code == 200
    assert response.json()["by_year"]["nvo_10"]["2025"]["math"] == 45.0
