"""Tests for the isolated pricing-evidence refresh operation."""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import School, ScrapeType, SourcePage
from app.scrapers.navigator import NavigatedPage
from scripts import refresh_pricing_evidence


def _source_page(
    school_id: int,
    index: int,
    *,
    scrape_type: ScrapeType = ScrapeType.WEBSITE,
    category: str = "about",
) -> SourcePage:
    return SourcePage(
        school_id=school_id,
        scrape_type=scrape_type,
        source_url=f"https://school.bg/old-{scrape_type.value}-{index}",
        page_category=category,
        raw_markdown=f"Stored page {index}",
        content_hash=f"old-hash-{index}",
        is_valid=True,
    )


async def _create_school(db_session) -> School:
    school = School(
        name_i18n={"bg": "Изолирано училище"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="summarized",
    )
    db_session.add(school)
    await db_session.flush()
    return school


@pytest.mark.parametrize(
    ("existing_count", "required_count"),
    [(0, 1), (1, 1), (2, 2), (3, 3), (10, 8)],
)
def test_required_live_page_count_is_conservative(existing_count, required_count):
    assert (
        refresh_pricing_evidence.required_live_page_count(existing_count)
        == required_count
    )


@pytest.mark.parametrize(
    ("url", "canonical_url", "expected"),
    [
        (
            "https://wordpress.com/landing",
            "https://school.wordpress.com",
            False,
        ),
        (
            "https://sites.google.com/view/other-school/fees",
            "https://sites.google.com/view/our-school",
            False,
        ),
        (
            "https://sites.google.com/view/our-school/fees",
            "https://sites.google.com/view/Our-School",
            False,
        ),
        (
            "https://www.school.bg/fees",
            "https://school.bg",
            True,
        ),
        (
            "https://sites.google.com/view/our-school/fees",
            "https://sites.google.com/view/our-school",
            True,
        ),
        (
            "https://sites.google.com/",
            "https://sites.google.com/",
            False,
        ),
    ],
)
def test_operational_site_identity_is_conservative(url, canonical_url, expected):
    assert (
        refresh_pricing_evidence._has_same_site_identity(url, canonical_url)
        is expected
    )


@pytest.mark.parametrize(
    ("website_url", "redirect_url"),
    [
        ("https://school.wordpress.com", "https://wordpress.com"),
        (
            "https://sites.google.com/view/our-school",
            "https://sites.google.com/view/other-school",
        ),
    ],
)
@pytest.mark.asyncio
async def test_wrong_hosted_site_identity_leaves_existing_evidence_untouched(
    db_session,
    website_url,
    redirect_url,
):
    school = await _create_school(db_session)
    school.website_url = website_url
    old_page = _source_page(school.id, 0, category="pricing")
    db_session.add(old_page)
    await db_session.commit()

    live_pages = [
        NavigatedPage(
            url=redirect_url,
            category="pricing",
            markdown="Provider or another tenant content",
            content_hash="wrong-site",
            cache_status="miss",
        )
    ]

    async def fake_discover_pages(self, website_url: str):
        return redirect_url, live_pages

    with (
        patch.object(
            refresh_pricing_evidence,
            "_current_database_name",
            new=AsyncMock(
                return_value=refresh_pricing_evidence.ISOLATED_DATABASE_NAME
            ),
        ),
        patch(
            "app.scrapers.navigator.WebsiteNavigator.discover_pages",
            new=fake_discover_pages,
        ),
        pytest.raises(
            refresh_pricing_evidence.RefreshRejected,
            match="redirected off the school site",
        ),
    ):
        await refresh_pricing_evidence.refresh_school_pricing_evidence(
            db_session,
            school_id=school.id,
        )

    await db_session.refresh(old_page)
    assert old_page.raw_markdown == "Stored page 0"


@pytest.mark.asyncio
async def test_refresh_refuses_non_isolated_database(db_session):
    school = await _create_school(db_session)
    await db_session.commit()

    with (
        patch.object(
            refresh_pricing_evidence,
            "_current_database_name",
            new=AsyncMock(return_value="sofia_schools"),
        ),
        pytest.raises(
            refresh_pricing_evidence.RefreshRejected,
            match="Refusing to refresh outside the isolated database",
        ),
    ):
        await refresh_pricing_evidence.refresh_school_pricing_evidence(
            db_session,
            school_id=school.id,
        )


@pytest.mark.asyncio
async def test_partial_live_crawl_leaves_existing_evidence_untouched(db_session):
    school = await _create_school(db_session)
    old_pages = [
        _source_page(
            school.id,
            index,
            category="pricing" if index == 0 else "about",
        )
        for index in range(10)
    ]
    db_session.add_all(old_pages)
    await db_session.commit()

    live_pages = [
        NavigatedPage(
            url="https://school.bg",
            category="about",
            markdown="Only the home page loaded",
            content_hash="live-home",
            cache_status="miss",
        )
    ]

    async def fake_discover_pages(self, website_url: str):
        assert self.bypass_cache is True
        return website_url, live_pages

    with (
        patch.object(
            refresh_pricing_evidence,
            "_current_database_name",
            new=AsyncMock(
                return_value=refresh_pricing_evidence.ISOLATED_DATABASE_NAME
            ),
        ),
        patch(
            "app.scrapers.navigator.WebsiteNavigator.discover_pages",
            new=fake_discover_pages,
        ),
        pytest.raises(
            refresh_pricing_evidence.RefreshRejected,
            match="Live crawl appears incomplete",
        ),
    ):
        await refresh_pricing_evidence.refresh_school_pricing_evidence(
            db_session,
            school_id=school.id,
        )

    rows_result = await db_session.execute(
        select(SourcePage).where(SourcePage.school_id == school.id)
    )
    rows = rows_result.scalars().all()
    assert len(rows) == 10
    assert {row.raw_markdown for row in rows} == {
        f"Stored page {index}" for index in range(10)
    }


@pytest.mark.asyncio
async def test_cache_hit_leaves_existing_evidence_untouched(db_session):
    school = await _create_school(db_session)
    old_page = _source_page(school.id, 0, category="pricing")
    db_session.add(old_page)
    await db_session.commit()

    live_pages = [
        NavigatedPage(
            url="https://school.bg/fees",
            category="pricing",
            markdown="Current fees",
            content_hash="live-fees",
            cache_status="hit",
        )
    ]

    async def fake_discover_pages(self, website_url: str):
        return website_url, live_pages

    with (
        patch.object(
            refresh_pricing_evidence,
            "_current_database_name",
            new=AsyncMock(
                return_value=refresh_pricing_evidence.ISOLATED_DATABASE_NAME
            ),
        ),
        patch(
            "app.scrapers.navigator.WebsiteNavigator.discover_pages",
            new=fake_discover_pages,
        ),
        pytest.raises(
            refresh_pricing_evidence.RefreshRejected,
            match="unexpectedly reported 1 cache hit",
        ),
    ):
        await refresh_pricing_evidence.refresh_school_pricing_evidence(
            db_session,
            school_id=school.id,
        )

    await db_session.refresh(old_page)
    assert old_page.is_valid is True
    assert old_page.raw_markdown == "Stored page 0"


@pytest.mark.asyncio
async def test_missing_live_pricing_page_leaves_existing_evidence_untouched(
    db_session,
):
    school = await _create_school(db_session)
    old_pages = [
        _source_page(school.id, 0, category="pricing"),
        _source_page(school.id, 1),
        _source_page(school.id, 2),
    ]
    db_session.add_all(old_pages)
    await db_session.commit()

    live_pages = [
        NavigatedPage(
            url=f"https://school.bg/page-{index}",
            category="about",
            markdown=f"Current page {index}",
            content_hash=f"live-{index}",
            cache_status="miss",
        )
        for index in range(3)
    ]

    async def fake_discover_pages(self, website_url: str):
        return website_url, live_pages

    with (
        patch.object(
            refresh_pricing_evidence,
            "_current_database_name",
            new=AsyncMock(
                return_value=refresh_pricing_evidence.ISOLATED_DATABASE_NAME
            ),
        ),
        patch(
            "app.scrapers.navigator.WebsiteNavigator.discover_pages",
            new=fake_discover_pages,
        ),
        pytest.raises(
            refresh_pricing_evidence.RefreshRejected,
            match="lost the pricing page",
        ),
    ):
        await refresh_pricing_evidence.refresh_school_pricing_evidence(
            db_session,
            school_id=school.id,
        )

    rows_result = await db_session.execute(
        select(SourcePage).where(
            SourcePage.school_id == school.id,
            SourcePage.scrape_type == ScrapeType.WEBSITE,
        )
    )
    assert len(rows_result.scalars().all()) == 3


@pytest.mark.asyncio
async def test_complete_live_crawl_replaces_only_website_evidence(db_session):
    school = await _create_school(db_session)
    old_pages = [
        _source_page(school.id, 0, category="pricing"),
        _source_page(school.id, 1),
        _source_page(school.id, 2),
    ]
    registry_page = _source_page(
        school.id,
        99,
        scrape_type=ScrapeType.REGISTRY,
    )
    db_session.add_all([*old_pages, registry_page])
    await db_session.commit()

    live_pages = [
        NavigatedPage(
            url="https://school.bg",
            category="about",
            markdown="Current school information",
            content_hash="live-about",
            cache_status="miss",
        ),
        NavigatedPage(
            url="https://school.bg/fees",
            category="pricing",
            markdown="Current 2026-2027 fees",
            content_hash="live-fees",
            cache_status="miss",
        ),
        NavigatedPage(
            url="https://school.bg/contact",
            category="contact",
            markdown="Current contact details",
            content_hash="live-contact",
            cache_status="miss",
        ),
    ]

    async def fake_discover_pages(self, website_url: str):
        assert self.bypass_cache is True
        return website_url, live_pages

    with (
        patch.object(
            refresh_pricing_evidence,
            "_current_database_name",
            new=AsyncMock(
                return_value=refresh_pricing_evidence.ISOLATED_DATABASE_NAME
            ),
        ),
        patch(
            "app.scrapers.navigator.WebsiteNavigator.discover_pages",
            new=fake_discover_pages,
        ),
    ):
        result = await refresh_pricing_evidence.refresh_school_pricing_evidence(
            db_session,
            school_id=school.id,
        )

    website_result = await db_session.execute(
        select(SourcePage).where(
            SourcePage.school_id == school.id,
            SourcePage.scrape_type == ScrapeType.WEBSITE,
        )
    )
    website_pages = website_result.scalars().all()
    assert {page.source_url for page in website_pages} == {
        "https://school.bg",
        "https://school.bg/fees",
        "https://school.bg/contact",
    }
    assert all(page.is_valid for page in website_pages)

    registry_result = await db_session.execute(
        select(SourcePage).where(
            SourcePage.school_id == school.id,
            SourcePage.scrape_type == ScrapeType.REGISTRY,
        )
    )
    assert registry_result.scalar_one().id == registry_page.id
    await db_session.refresh(school)
    assert school.scrape_status == "navigated"
    assert result["cache_hits"] == 0
    assert result["baseline_useful_pages"] == 3
    assert result["minimum_required_pages"] == 3
    assert result["fresh_useful_pages"] == 3
