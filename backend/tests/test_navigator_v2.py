"""Tests for scraper navigation v2."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import School, ScrapeType, SourcePage
from app.scrapers.v2.navigator import (
    BatchDiscoverOutcomeV2,
    NavigatedPageV2,
    navigate_school_v2,
    navigate_schools_v2_batch,
)


@pytest.mark.asyncio
async def test_navigate_school_v2_creates_source_pages(db_session):
    school = School(
        name_i18n={"bg": "Тестово училище"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="validated",
    )
    db_session.add(school)
    await db_session.commit()

    pages = [
        NavigatedPageV2(
            url="https://school.bg",
            category="about",
            markdown="Добре дошли",
            content_hash="hash-home",
            cache_status="miss",
        ),
        NavigatedPageV2(
            url="https://school.bg/priem",
            category="admission",
            markdown="Прием и записване",
            content_hash="hash-admission",
            cache_status="hit",
        ),
    ]

    async def fake_discover_pages(self, website_url: str):
        assert website_url == "https://school.bg"
        return "https://school.bg", pages

    from unittest.mock import patch

    with patch("app.scrapers.v2.navigator.WebsiteNavigatorV2.discover_pages", new=fake_discover_pages):
        result = await navigate_school_v2(db=db_session, school_id=school.id, country_code="bg")

    assert result["success"] is True
    assert result["pages_found"] == 2
    assert result["pages_with_content"] == 2

    await db_session.refresh(school)
    assert school.scrape_status == "navigated"

    rows = (
        await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
    ).scalars().all()
    assert len(rows) == 2
    assert any(row.page_category == "admission" for row in rows)


@pytest.mark.asyncio
async def test_navigate_school_v2_returns_failure_when_no_extractable_content(db_session):
    school = School(
        name_i18n={"bg": "Тестово училище"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="validated",
    )
    db_session.add(school)
    await db_session.commit()

    pages = [
        NavigatedPageV2(
            url="https://school.bg/.well-known/sgcaptcha/?r=%2F",
            category=None,
            markdown="Checking the site connection security",
            content_hash="hash-bot",
            cache_status="miss",
        ),
    ]

    async def fake_discover_pages(self, website_url: str):
        return website_url, pages

    from unittest.mock import patch

    with patch("app.scrapers.v2.navigator.WebsiteNavigatorV2.discover_pages", new=fake_discover_pages):
        result = await navigate_school_v2(db=db_session, school_id=school.id, country_code="bg")

    assert result["success"] is False
    assert result["pages_with_content"] == 0


@pytest.mark.asyncio
async def test_navigate_schools_v2_batch_uses_discover_many_and_persists(db_session):
    schools = []
    for idx in range(2):
        school = School(
            name_i18n={"bg": f"Тестово училище {idx}"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url=f"https://school{idx}.bg",
            scrape_status="validated",
        )
        db_session.add(school)
        schools.append(school)
    await db_session.commit()

    outcomes = {
        "https://school0.bg": BatchDiscoverOutcomeV2(
            seed_url="https://school0.bg",
            final_url="https://school0.bg",
            pages=[
                NavigatedPageV2(
                    url="https://school0.bg/about",
                    category="about",
                    markdown="About",
                    content_hash="hash-a",
                )
            ],
        ),
        "https://school1.bg": BatchDiscoverOutcomeV2(
            seed_url="https://school1.bg",
            final_url="https://school1.bg",
            pages=[
                NavigatedPageV2(
                    url="https://school1.bg/admission",
                    category="admission",
                    markdown="Admission",
                    content_hash="hash-b",
                )
            ],
        ),
    }

    async def fake_discover_many(self, website_urls, *, max_concurrency):
        assert max_concurrency == 2
        assert set(website_urls) == {"https://school0.bg", "https://school1.bg"}
        return outcomes

    from unittest.mock import patch

    with patch("app.scrapers.v2.navigator.WebsiteNavigatorV2.discover_pages_many", new=fake_discover_many):
        results = await navigate_schools_v2_batch(
            db=db_session,
            school_ids=[schools[0].id, schools[1].id],
            country_code="bg",
            max_concurrency=2,
        )

    assert len(results) == 2
    assert all(row["success"] for row in results)

    for school in schools:
        await db_session.refresh(school)
        assert school.scrape_status == "navigated"

    rows = (
        await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id.in_([schools[0].id, schools[1].id]),
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
    ).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_navigate_schools_v2_batch_retries_failed_outcome_sequentially(db_session):
    school = School(
        name_i18n={"bg": "Тестово училище"},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="validated",
    )
    db_session.add(school)
    await db_session.commit()

    async def fake_discover_many(self, website_urls, *, max_concurrency):
        return {
            "https://school.bg": BatchDiscoverOutcomeV2(
                seed_url="https://school.bg",
                final_url=None,
                pages=[],
                error="Timeout waiting for selector 'body'",
            )
        }

    async def fake_discover_pages(self, website_url: str):
        return (
            website_url,
            [
                NavigatedPageV2(
                    url="https://school.bg/about",
                    category="about",
                    markdown="About",
                    content_hash="hash-about",
                )
            ],
        )

    from unittest.mock import patch

    with (
        patch("app.scrapers.v2.navigator.WebsiteNavigatorV2.discover_pages_many", new=fake_discover_many),
        patch("app.scrapers.v2.navigator.WebsiteNavigatorV2.discover_pages", new=fake_discover_pages),
    ):
        results = await navigate_schools_v2_batch(
            db=db_session,
            school_ids=[school.id],
            country_code="bg",
            max_concurrency=2,
        )

    assert len(results) == 1
    assert results[0]["success"] is True

    rows = (
        await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
    ).scalars().all()
    assert len(rows) == 1
