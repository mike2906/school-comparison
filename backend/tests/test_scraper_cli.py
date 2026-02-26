"""Tests for scraper CLI batch helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import School
from app.scrapers import cli as scraper_cli
from app.scrapers.url_validator import ValidationResult


@pytest.mark.asyncio
async def test_run_recover_failed_school_delegates_to_discoverer_public_api(db_session):
    school = School(
        name_i18n={"bg": "Тест училище"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://broken.bg",
        scrape_status="failed_validate",
    )
    db_session.add(school)
    await db_session.commit()

    expected = {
        "school_id": school.id,
        "status": "validated",
        "validation_result": "valid",
        "attempts": 1,
    }
    with patch(
        "app.scrapers.website_discovery.WebsiteDiscoverer.recover_failed_school",
        new=AsyncMock(return_value=expected),
    ) as recover_mock:
        out = await scraper_cli._run_recover_failed_school(db_session, school.id, "bg")

    assert out == expected
    recover_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_validate_urls_batch_caps_configured_concurrency(db_session):
    school = School(
        name_i18n={"bg": "Тест училище"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://test-school.bg",
        scrape_status="pending",
    )
    db_session.add(school)
    await db_session.commit()

    settings = SimpleNamespace(url_validation_concurrency=50, url_validation_max_concurrency=3)
    captured_semaphore_values: list[int] = []
    real_semaphore = asyncio.Semaphore

    def semaphore_spy(value: int):
        captured_semaphore_values.append(value)
        return real_semaphore(value)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch(
            "app.scrapers.url_validator.validate_school_url",
            new=AsyncMock(return_value=(ValidationResult.VALID, "https://test-school.bg", "ok")),
        ),
        patch("app.scrapers.cli.asyncio.Semaphore", side_effect=semaphore_spy),
    ):
        await scraper_cli._run_validate_urls_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
            statuses=["pending"],
        )

    assert captured_semaphore_values[0] == 3


@pytest.mark.asyncio
async def test_run_recover_failed_urls_batch_uses_isolated_session_helper(db_session):
    for idx in range(3):
        db_session.add(
            School(
                name_i18n={"bg": f"Тест {idx}"},
                country_code="bg",
                school_type="state",
                education_level="primary",
                city="sofia",
                website_url="https://broken.bg",
                scrape_status="failed_validate",
            )
        )
    await db_session.commit()

    settings = SimpleNamespace(url_recovery_concurrency=20, url_validation_max_concurrency=4)
    recover_mock = AsyncMock(
        side_effect=[
            {"status": "validated"},
            {"terminal": True, "status": "no_official_website"},
            {"status": "failed_validate"},
        ]
    )
    real_semaphore = asyncio.Semaphore
    captured_semaphore_values: list[int] = []

    def semaphore_spy(value: int):
        captured_semaphore_values.append(value)
        return real_semaphore(value)

    with (
        patch("app.config.get_settings", return_value=settings),
        patch("app.scrapers.cli._recover_failed_school_with_new_session", new=recover_mock),
        patch("app.scrapers.cli.asyncio.Semaphore", side_effect=semaphore_spy),
    ):
        await scraper_cli._run_recover_failed_urls_batch(
            db=db_session,
            country="bg",
            city="sofia",
            limit=None,
        )

    assert recover_mock.await_count == 3
    assert captured_semaphore_values[0] == 4


@pytest.mark.asyncio
async def test_run_navigate_batch_rolls_back_and_continues_after_school_error(db_session: AsyncSession):
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
    settings = SimpleNamespace(nav_school_timeout_seconds=0)

    with (
        patch("app.config.get_settings", return_value=settings),
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
    assert rollback_spy.await_count == 1


@pytest.mark.asyncio
async def test_run_extract_batch_rolls_back_and_continues_after_school_error(db_session: AsyncSession):
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
    settings = SimpleNamespace(extraction_school_timeout_seconds=0)

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
