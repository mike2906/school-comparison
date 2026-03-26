"""Tests for scraper CLI stage helpers (canonical)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import School
from app.scrapers import cli as scraper_cli


@pytest.mark.asyncio
async def test_stage_choices_use_canonical_names():
    run_command = scraper_cli.run
    stage_param = next(param for param in run_command.params if param.name == "stage")
    choices = set(stage_param.type.choices)

    assert "nvo" in choices
    assert "navigate" in choices
    assert "extract" in choices
    assert "all" in choices
    assert "navigate-v2" not in choices
    assert "extract-v2" not in choices
    assert "all-v2" not in choices


@pytest.mark.asyncio
async def test_run_all_stages_routes_to_canonical_handlers(db_session):
    school = School(
        name_i18n={"bg": "Тест"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="validated",
    )
    db_session.add(school)
    await db_session.commit()

    with (
        patch.object(scraper_cli, "_run_discover_website", new=AsyncMock()) as discover_mock,
        patch.object(scraper_cli, "_run_validate_url", new=AsyncMock()) as validate_mock,
        patch.object(scraper_cli, "_run_navigate_school", new=AsyncMock()) as nav_mock,
        patch.object(scraper_cli, "_run_extract_school", new=AsyncMock()) as extract_mock,
        patch.object(scraper_cli, "_run_validate_data_school", new=AsyncMock()) as validate_data_mock,
        patch.object(scraper_cli, "_run_summarize_school", new=AsyncMock()) as summarize_mock,
    ):
        await scraper_cli._run_all_stages(db_session, school.id, "bg")

    discover_mock.assert_awaited_once()
    validate_mock.assert_awaited_once()
    nav_mock.assert_awaited_once()
    extract_mock.assert_awaited_once()
    validate_data_mock.assert_awaited_once()
    summarize_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_sync_routes_nvo_stage_to_import_helper(db_session):
    school = School(
        name_i18n={"bg": "НВО тест"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
    )
    db_session.add(school)
    await db_session.commit()

    with patch.object(scraper_cli, "_run_nvo_import", new=AsyncMock()) as nvo_mock:
        await scraper_cli._run_sync(
            school_name=None,
            school_id=school.id,
            stage="nvo",
            city="sofia",
            country="bg",
            limit=None,
            year=2025,
            history_years=5,
            exam_types=["nvo_4"],
            sample_ratio=0.0,
            include_navigated=False,
            include_extracted=False,
            force_validate=False,
        )

    nvo_mock.assert_awaited_once()
    kwargs = nvo_mock.await_args.kwargs
    assert kwargs["country"] == "bg"
    assert kwargs["city"] == "sofia"
    assert kwargs["year"] == 2025
    assert kwargs["history_years"] == 5
    assert kwargs["exam_types"] == ["nvo_4"]
    assert kwargs["school_ids"] == [school.id]


@pytest.mark.asyncio
async def test_discover_websites_batch_includes_missing_website_rows_beyond_failed_validate(db_session):
    eligible_missing = School(
        name_i18n={"bg": "Липсващ сайт"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
        website_url=None,
        scrape_status="extraction_failed",
    )
    eligible_pending = School(
        name_i18n={"bg": "Чакащ сайт"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url=None,
        scrape_status="pending",
    )
    terminal_no_site = School(
        name_i18n={"bg": "Без сайт"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url=None,
        scrape_status="no_official_website",
    )
    extraction_failed_with_site = School(
        name_i18n={"bg": "Екстракция със сайт"},
        country_code="bg",
        school_type="private",
        education_level="kindergarten",
        city="sofia",
        website_url="https://existing-school.bg",
        scrape_status="extraction_failed",
    )
    validated = School(
        name_i18n={"bg": "Валидиран"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="validated",
    )
    db_session.add_all([eligible_missing, eligible_pending, terminal_no_site, extraction_failed_with_site, validated])
    await db_session.commit()

    with patch.object(scraper_cli, "_run_discover_website", new=AsyncMock(return_value={"found": False})) as discover_mock:
        await scraper_cli._run_discover_websites_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
        )

    called_ids = {call.args[1] for call in discover_mock.await_args_list}
    assert eligible_missing.id in called_ids
    assert eligible_pending.id in called_ids
    assert terminal_no_site.id not in called_ids
    assert extraction_failed_with_site.id not in called_ids
    assert validated.id not in called_ids


@pytest.mark.asyncio
async def test_run_navigate_batch_rolls_back_and_continues_after_school_error(db_session):
    for idx in range(2):
        db_session.add(
            School(
                name_i18n={"bg": f"Навигация {idx}"},
                country_code="bg",
                school_type="state",
                education_level="primary",
                city="sofia",
                website_url=f"https://n{idx}.school.bg",
                scrape_status="validated",
            )
        )
    await db_session.commit()

    run_mock = AsyncMock(side_effect=[RuntimeError("boom"), {"success": True}])
    rollback_spy = AsyncMock(wraps=db_session.rollback)
    settings = SimpleNamespace(nav_school_timeout_seconds=0, nav_batch_concurrency=3)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.scrapers.navigator.navigate_schools_batch", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch.object(scraper_cli, "_run_navigate_school", new=run_mock),
        patch.object(db_session, "rollback", new=rollback_spy),
    ):
        await scraper_cli._run_navigate_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=False,
        )

    assert run_mock.await_count == 2
    assert rollback_spy.await_count == 2


@pytest.mark.asyncio
async def test_run_navigate_batch_uses_batch_crawler_results(db_session):
    schools = []
    for idx in range(2):
        school = School(
            name_i18n={"bg": f"Навигация {idx}"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url=f"https://n{idx}.school.bg",
            scrape_status="validated",
        )
        db_session.add(school)
        schools.append(school)
    await db_session.commit()

    settings = SimpleNamespace(nav_school_timeout_seconds=0, nav_batch_concurrency=2)
    batch_results = [
        {"school_id": schools[0].id, "success": True},
        {"school_id": schools[1].id, "success": False},
    ]

    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.scrapers.navigator.navigate_schools_batch", new=AsyncMock(return_value=batch_results)) as batch_mock,
        patch.object(scraper_cli, "_run_navigate_school", new=AsyncMock()) as single_mock,
    ):
        await scraper_cli._run_navigate_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=False,
        )

    batch_mock.assert_awaited_once()
    single_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_navigate_batch_skips_timed_out_explicit_chunks(db_session):
    schools = []
    for idx in range(3):
        school = School(
            name_i18n={"bg": f"Навигация {idx}"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url=f"https://n{idx}.school.bg",
            scrape_status="validated",
        )
        db_session.add(school)
        schools.append(school)
    await db_session.commit()
    school_ids = [school.id for school in schools]

    settings = SimpleNamespace(nav_school_timeout_seconds=10, nav_batch_concurrency=2)
    batch_mock = AsyncMock(
        side_effect=[
            asyncio.TimeoutError(),
            [{"school_id": schools[2].id, "success": True}],
        ]
    )
    rollback_spy = AsyncMock(wraps=db_session.rollback)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.scrapers.navigator.navigate_schools_batch", new=batch_mock),
        patch.object(db_session, "rollback", new=rollback_spy),
        patch.object(scraper_cli, "_run_navigate_school", new=AsyncMock()) as single_mock,
    ):
        results = await scraper_cli._run_navigate_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=True,
            school_ids=school_ids,
            skip_timed_out_chunks=True,
        )

    assert [result["school_id"] for result in results] == school_ids
    assert [result["success"] for result in results] == [False, False, True]
    assert rollback_spy.await_count == 1
    single_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_extract_batch_rolls_back_and_continues_after_school_error(db_session):
    for idx in range(2):
        db_session.add(
            School(
                name_i18n={"bg": f"Екстракция {idx}"},
                country_code="bg",
                school_type="private",
                education_level="primary",
                city="sofia",
                website_url=f"https://e{idx}.school.bg",
                scrape_status="navigated",
            )
        )
    await db_session.commit()

    run_mock = AsyncMock(side_effect=[RuntimeError("boom"), {"status": "extracted"}])
    rollback_spy = AsyncMock(wraps=db_session.rollback)
    settings = SimpleNamespace(extraction_school_timeout_seconds=0, extraction_batch_concurrency=1)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch.object(scraper_cli, "_run_extract_school", new=run_mock),
        patch.object(db_session, "rollback", new=rollback_spy),
    ):
        await scraper_cli._run_extract_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_extracted=False,
        )

    assert run_mock.await_count == 2
    assert rollback_spy.await_count == 1


@pytest.mark.asyncio
async def test_run_extract_batch_with_explicit_school_ids_ignores_status_filter(db_session):
    school = School(
        name_i18n={"bg": "Изрично училище"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://explicit.school.bg",
        scrape_status="pending",
    )
    db_session.add(school)
    await db_session.commit()

    settings = SimpleNamespace(extraction_school_timeout_seconds=0, extraction_batch_concurrency=1)
    run_mock = AsyncMock(return_value={"status": "extracted"})

    with (
        patch("app.config.get_settings", return_value=settings),
        patch.object(scraper_cli, "_run_extract_school", new=run_mock),
    ):
        await scraper_cli._run_extract_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_extracted=True,
            school_ids=[school.id],
        )

    run_mock.assert_awaited_once_with(db_session, school.id, "bg")
