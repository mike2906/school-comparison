"""Tests for scraper CLI v2 stages."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models import School
from app.scrapers import cli as scraper_cli


@pytest.mark.asyncio
async def test_stage_choices_include_v2_variants():
    run_command = scraper_cli.run
    stage_param = next(param for param in run_command.params if param.name == "stage")
    choices = set(stage_param.type.choices)

    assert "navigate-v2" in choices
    assert "extract-v2" in choices
    assert "all-v2" in choices


@pytest.mark.asyncio
async def test_run_all_stages_v2_routes_to_v2_handlers(db_session):
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
        patch.object(scraper_cli, "_run_navigate_school_v2", new=AsyncMock()) as nav_mock,
        patch.object(scraper_cli, "_run_extract_school_v2", new=AsyncMock()) as extract_mock,
    ):
        await scraper_cli._run_all_stages_v2(db_session, school.id, "bg")

    discover_mock.assert_awaited_once()
    validate_mock.assert_awaited_once()
    nav_mock.assert_awaited_once()
    extract_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_navigate_batch_v2_rolls_back_and_continues_after_school_error(db_session):
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
        patch("app.scrapers.v2.navigator.navigate_schools_v2_batch", new=AsyncMock(side_effect=RuntimeError("boom"))),
        patch.object(scraper_cli, "_run_navigate_school_v2", new=run_mock),
        patch.object(db_session, "rollback", new=rollback_spy),
    ):
        await scraper_cli._run_navigate_batch_v2(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=False,
        )

    assert run_mock.await_count == 2
    assert rollback_spy.await_count == 2


@pytest.mark.asyncio
async def test_run_navigate_batch_v2_uses_batch_crawler_results(db_session):
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
        patch("app.scrapers.v2.navigator.navigate_schools_v2_batch", new=AsyncMock(return_value=batch_results)) as batch_mock,
        patch.object(scraper_cli, "_run_navigate_school_v2", new=AsyncMock()) as single_mock,
    ):
        await scraper_cli._run_navigate_batch_v2(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_navigated=False,
        )

    batch_mock.assert_awaited_once()
    single_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_extract_batch_v2_rolls_back_and_continues_after_school_error(db_session):
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
        patch.object(scraper_cli, "_run_extract_school_v2", new=run_mock),
        patch.object(db_session, "rollback", new=rollback_spy),
    ):
        await scraper_cli._run_extract_batch_v2(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            include_extracted=False,
        )

    assert run_mock.await_count == 2
    assert rollback_spy.await_count == 1
