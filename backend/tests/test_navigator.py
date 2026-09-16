"""Tests for scraper navigation."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.models import School, ScrapeType, SourcePage
from app.scrapers.navigator import (
    BatchDiscoverOutcome,
    NavigatedPage,
    WebsiteNavigator,
    navigate_school,
    navigate_schools_batch,
)


def test_classify_page_treats_school_profile_slug_as_about():
    navigator = WebsiteNavigator(country_code="bg")

    assert navigator.classify_page("https://pberon.com/za-chou-d-r-petar-beron/") == "about"


def test_classify_page_treats_tseni_slug_as_pricing():
    navigator = WebsiteNavigator(country_code="bg")

    assert navigator.classify_page("https://school.fusion.bg/priem/grafik-i-tseni/") == "pricing"


def test_classify_page_treats_preschool_admission_slug_as_admission():
    navigator = WebsiteNavigator(country_code="bg")

    assert navigator.classify_page("https://21su.bg/priem/predutchilishtni-grupi/") == "admission"


def test_extract_markdown_prefers_richer_markdown_candidate():
    navigator = WebsiteNavigator(country_code="bg")

    crawl_result = type(
        "Result",
        (),
        {
            "markdown": type(
                "Markdown",
                (),
                {
                    "fit_markdown": "# За училището\nКратък текст",
                    "raw_markdown": "# За училището\nКратък текст\n## Contact Us\nгр. София, ул. Флора Кънева №14",
                },
            )(),
        },
    )()

    extracted = navigator._extract_markdown(crawl_result)

    assert extracted is not None
    assert "Флора Кънева №14" in extracted


def test_extract_markdown_appends_html_contact_signals_from_site_chrome():
    navigator = WebsiteNavigator(country_code="bg")

    crawl_result = type(
        "Result",
        (),
        {
            "markdown": type(
                "Markdown",
                (),
                {
                    "fit_markdown": "# За ЧОУ\nОсновно съдържание",
                    "raw_markdown": "# За ЧОУ\nОсновно съдържание",
                },
            )(),
            "cleaned_html": """
                <html>
                  <body>
                    <ul class="all-contacts">
                      <li class="d-adress">
                        <a href="https://www.google.com/maps?ll=42.65034,23.319464&x=1">19. гр. София, ул. Флора Кънева №14</a>
                      </li>
                      <li class="contact-f">
                        <a href="tel:+35921234567">02/1234567</a>
                      </li>
                    </ul>
                    <article><h1>За ЧОУ</h1><p>Основно съдържание</p></article>
                  </body>
                </html>
            """,
        },
    )()

    extracted = navigator._extract_markdown(crawl_result)

    assert extracted is not None
    assert "Address: гр. София, ул. Флора Кънева №14" in extracted
    assert "Coordinates: 42.650340, 23.319464" in extracted
    assert "Phone: 02/1234567" in extracted


def test_extract_markdown_retains_structured_identity_signals_without_ocr():
    navigator = WebsiteNavigator(country_code="bg")
    crawl_result = type(
        "Result",
        (),
        {
            "markdown": "Admissions and curriculum information.",
            "html": """
                <html>
                  <head>
                    <title>Admissions | ABC KinderCare Centre</title>
                    <meta property="og:site_name" content="ABC KinderCare Centre">
                    <script type="application/ld+json">
                      {"@type": "School", "name": "ABC KinderCare Centre"}
                    </script>
                  </head>
                  <body>
                    <header><img class="site-logo" src="logo.svg" alt="ABC KinderCare Centre"></header>
                    <main><p>Admissions and curriculum information for families in Sofia.</p></main>
                  </body>
                </html>
            """,
        },
    )()

    extracted = navigator._extract_markdown(crawl_result)

    assert extracted is not None
    assert "## HTML identity signals" in extracted
    assert "Admissions | ABC KinderCare Centre" not in extracted
    assert "\nABC KinderCare Centre" in extracted


def test_extract_html_identity_signals_ignores_non_logo_image_text():
    navigator = WebsiteNavigator(country_code="bg")

    signals = navigator._extract_html_identity_signals(
        '<html><body><img src="event.jpg" alt="Visit School"></body></html>'
    )

    assert signals == []


def test_extract_html_identity_signals_ignores_generic_organization_json_ld():
    navigator = WebsiteNavigator(country_code="bg")

    signals = navigator._extract_html_identity_signals(
        """
        <html><body>
          <script type="application/ld+json">
            {"@type": "Organization", "name": "ABC Studio"}
          </script>
        </body></html>
        """
    )

    assert signals == []


@pytest.mark.parametrize(
    "schema_type",
    ["https://schema.org/School", "http://schema.org/Preschool", "schema:ChildCare"],
)
def test_extract_html_identity_signals_accepts_school_schema_type_iris(schema_type):
    navigator = WebsiteNavigator(country_code="bg")

    signals = navigator._extract_html_identity_signals(
        f"""
        <html><body>
          <script type="application/ld+json">
            {{"@type": "{schema_type}", "name": "ABC KinderCare Centre"}}
          </script>
        </body></html>
        """
    )

    assert signals == ["ABC KinderCare Centre"]


def test_append_identity_signals_reserves_space_on_max_length_page():
    navigator = WebsiteNavigator(country_code="bg")
    full_page = "x" * navigator.MAX_CONTENT_CHARS

    extracted = navigator._append_identity_signals(
        full_page,
        ["ABC KinderCare Centre"],
    )

    assert extracted is not None
    assert len(extracted) <= navigator.MAX_CONTENT_CHARS
    assert extracted.endswith("## HTML identity signals\nABC KinderCare Centre")


def test_extract_markdown_reserves_space_for_identity_and_contact_signals():
    navigator = WebsiteNavigator(country_code="bg")
    crawl_result = type(
        "Result",
        (),
        {
            "markdown": "x" * navigator.MAX_CONTENT_CHARS,
            "html": """
                <html>
                  <head><meta property="og:site_name" content="ABC KinderCare Centre"></head>
                  <body><footer><a href="tel:+35921234567">02/1234567</a></footer></body>
                </html>
            """,
        },
    )()

    extracted = navigator._extract_markdown(crawl_result)

    assert extracted is not None
    assert len(extracted) <= navigator.MAX_CONTENT_CHARS
    assert "## HTML identity signals\nABC KinderCare Centre" in extracted
    assert extracted.endswith("## HTML contact signals\nPhone: 02/1234567")


def test_extract_markdown_prefers_focused_main_content_html_over_menu_heavy_markdown():
    navigator = WebsiteNavigator(country_code="bg")

    crawl_result = type(
        "Result",
        (),
        {
            "markdown": type(
                "Markdown",
                (),
                {
                    "raw_markdown": "\n".join(
                        [
                            "НАЧАЛО",
                            "АКТУАЛНО",
                            "ЗА НАС",
                            "УЧИЛИЩЕ",
                            "ПРИЕМ",
                            "ОБУЧЕНИЕ",
                            "ПРОЕКТИ",
                        ]
                        * 500
                    ),
                },
            )(),
            "html": """
                <html>
                  <body>
                    <div id="layout-menu">
                      <ul class="top-menu">
                        <li>НАЧАЛО</li>
                        <li>АКТУАЛНО</li>
                      </ul>
                    </div>
                    <div id="layout-page-105">
                      <h3>Класни ръководители</h3>
                      <div class="Text inline-block" id="text-edit-2870">
                        <table class="table-inner">
                          <tr><th>Клас</th><th>Класен ръководител</th></tr>
                          <tr><td>3. група</td><td>Вергиния Николова</td></tr>
                          <tr><td>4. група</td><td>Елка Вълкова</td></tr>
                          <tr><td>1 а</td><td>Дорина Христова</td></tr>
                          <tr><td>4 в</td><td>Петя Крачунова</td></tr>
                          <tr><td>5 а</td><td>Стефани Витанова</td></tr>
                        </table>
                      </div>
                    </div>
                  </body>
                </html>
            """,
        },
    )()

    extracted = navigator._extract_markdown(crawl_result)

    assert extracted is not None
    assert "3. група | Вергиния Николова" in extracted
    assert "1 а | Дорина Христова" in extracted
    assert "5 а | Стефани Витанова" in extracted
    assert "Класни ръководители" in extracted


def test_extract_map_link_coordinates_supports_center_param():
    navigator = WebsiteNavigator(country_code="bg")

    coords = navigator._extract_map_link_coordinates(
        "https://www.google.com/maps?center=42.65034,23.319464&zoom=16"
    )

    assert coords == (42.65034, 23.319464)


def test_build_run_config_uses_raw_html_for_about_and_contact_pages():
    navigator = WebsiteNavigator(country_code="bg")

    about_config = navigator._build_run_config("https://pberon.com/za-chou-d-r-petar-beron/")
    contact_config = navigator._build_run_config("https://pberon.com/kontakti/")
    class_teachers_config = navigator._build_run_config("https://21su.bg/obuchenie/klasni-rakovoditeli/")
    program_config = navigator._build_run_config("https://pberon.com/programirane/")

    assert about_config.markdown_generator.content_source == "raw_html"
    assert contact_config.markdown_generator.content_source == "raw_html"
    assert class_teachers_config.markdown_generator.content_source == "raw_html"
    assert program_config.markdown_generator.content_source == "cleaned_html"


def test_build_run_config_can_bypass_cache_for_evidence_refresh():
    from crawl4ai import CacheMode

    ordinary = WebsiteNavigator(country_code="bg")
    refresh = WebsiteNavigator(country_code="bg", bypass_cache=True)

    ordinary_config = ordinary._build_run_config("https://school.bg/fees")
    refresh_config = refresh._build_run_config("https://school.bg/fees")

    assert ordinary_config.cache_mode == CacheMode.ENABLED
    assert refresh_config.cache_mode == CacheMode.BYPASS


def test_live_refresh_collects_only_usable_results_for_cache_replacement():
    navigator = WebsiteNavigator(country_code="bg", bypass_cache=True)
    shorter_duplicate = type(
        "Result",
        (),
        {
            "success": True,
            "url": "https://school.bg/fees",
            "redirected_url": None,
            "markdown": "Fees",
            "html": "<html>Fees</html>",
            "metadata": {},
            "head_fingerprint": "old-head",
            "cache_status": "miss",
        },
    )()
    usable = type(
        "Result",
        (),
        {
            "success": True,
            "url": "https://school.bg/fees",
            "redirected_url": None,
            "markdown": "Current fees",
            "html": "<html>Current fees</html>",
            "metadata": {},
            "head_fingerprint": "same-head",
            "cache_status": "miss",
        },
    )()
    challenge = type(
        "Result",
        (),
        {
            "success": True,
            "url": "https://school.bg/admission",
            "redirected_url": None,
            "markdown": "Checking the site connection security",
            "html": "<html>Checking the site connection security</html>",
            "metadata": {},
            "head_fingerprint": "challenge-head",
            "cache_status": "miss",
        },
    )()
    removed = type(
        "Result",
        (),
        {
            "success": False,
            "url": "https://school.bg/old-fees",
            "redirected_url": "https://school.bg/not-found",
            "markdown": None,
            "html": "",
            "metadata": {},
            "head_fingerprint": "removed-head",
            "cache_status": "miss",
            "status_code": 404,
        },
    )()
    cacheable_results = []
    removal_cache_urls = []

    final_url, pages = navigator._extract_pages_from_results(
        normalized_url="https://school.bg",
        results_obj=[shorter_duplicate, usable, challenge, removed],
        cacheable_results=cacheable_results,
        removal_cache_urls=removal_cache_urls,
    )

    assert len(pages) == 3
    assert final_url == "https://school.bg/fees"
    assert pages[-1].url == "https://school.bg/old-fees"
    assert cacheable_results == [usable]
    assert set(removal_cache_urls) == {
        "https://school.bg/old-fees",
        "https://school.bg/not-found",
    }


@pytest.mark.asyncio
async def test_live_refresh_replaces_and_verifies_usable_cache_entry():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from crawl4ai.async_database import async_db_manager

    navigator = WebsiteNavigator(country_code="bg", bypass_cache=True)
    live_result = SimpleNamespace(
        url="https://school.bg/fees",
        redirected_url="https://www.school.bg/fees",
        html="<html>Fresh fees</html>",
    )
    cache_write = AsyncMock()
    cache_read = AsyncMock(
        return_value=SimpleNamespace(html="<html>Fresh fees</html>")
    )

    with (
        patch.object(async_db_manager, "acache_url", new=cache_write),
        patch.object(async_db_manager, "aget_cached_url", new=cache_read),
    ):
        await navigator._replace_cache_with_usable_results([live_result])

    assert [call.args[0].url for call in cache_write.await_args_list] == [
        "https://school.bg/fees",
        "https://www.school.bg/fees",
    ]
    assert [call.args[0] for call in cache_read.await_args_list] == [
        "https://school.bg/fees",
        "https://www.school.bg/fees",
    ]


@pytest.mark.asyncio
async def test_live_refresh_fails_when_cache_replacement_cannot_be_verified():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from crawl4ai.async_database import async_db_manager

    navigator = WebsiteNavigator(country_code="bg", bypass_cache=True)
    live_result = SimpleNamespace(url="https://school.bg/fees", html="<html>Fresh fees</html>")

    with (
        patch.object(async_db_manager, "acache_url", new=AsyncMock()),
        patch.object(async_db_manager, "aget_cached_url", new=AsyncMock(return_value=None)),
        pytest.raises(RuntimeError, match="Failed to refresh Crawl4AI cache"),
    ):
        await navigator._replace_cache_with_usable_results([live_result])


@pytest.mark.asyncio
async def test_live_refresh_removes_and_verifies_definitive_removal_cache_entries():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from crawl4ai.async_database import async_db_manager

    navigator = WebsiteNavigator(country_code="bg", bypass_cache=True)
    db_execute = AsyncMock()

    async def execute_operation(operation):
        await operation(SimpleNamespace(execute=db_execute))

    execute_with_retry = AsyncMock(side_effect=execute_operation)
    cache_read = AsyncMock(return_value=None)
    with (
        patch.object(
            async_db_manager,
            "execute_with_retry",
            new=execute_with_retry,
        ),
        patch.object(async_db_manager, "aget_cached_url", new=cache_read),
    ):
        await navigator._remove_cache_entries(
            ["https://school.bg/old-fees", "https://school.bg/old-fees"]
        )

    execute_with_retry.assert_awaited_once()
    db_execute.assert_awaited_once_with(
        "DELETE FROM crawled_data WHERE url IN (?)",
        ("https://school.bg/old-fees",),
    )
    cache_read.assert_awaited_once_with("https://school.bg/old-fees")


@pytest.mark.asyncio
async def test_navigate_school_creates_source_pages(db_session):
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
        NavigatedPage(
            url="https://school.bg",
            category="about",
            markdown="Добре дошли",
            content_hash="hash-home",
            cache_status="miss",
        ),
        NavigatedPage(
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

    with patch("app.scrapers.navigator.WebsiteNavigator.discover_pages", new=fake_discover_pages):
        result = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

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
@pytest.mark.parametrize(
    (
        "initial_status",
        "new_hash",
        "new_markdown",
        "final_url",
        "expected_status",
        "expected_change",
    ),
    [
        ("extracted", "same-hash", "Current fees", "https://school.bg", "extracted", False),
        ("summarized", "same-hash", "Current fees", "https://school.bg", "summarized", False),
        ("extracted", "changed-hash", "Changed fees", "https://school.bg", "navigated", True),
        ("extracted", "same-hash", "Changed fees", "https://school.bg", "navigated", True),
        (
            "extracted",
            "same-hash",
            "Current fees",
            "https://www.school.bg",
            "extracted",
            False,
        ),
    ],
)
async def test_live_navigation_preserves_completed_status_only_when_unchanged(
    db_session,
    initial_status,
    new_hash,
    new_markdown,
    final_url,
    expected_status,
    expected_change,
):
    school = School(
        name_i18n={"bg": "Published school"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status=initial_status,
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add(
        SourcePage(
            school_id=school.id,
            scrape_type=ScrapeType.WEBSITE,
            source_url="https://school.bg/fees",
            page_category="pricing",
            raw_markdown="Current fees",
            content_hash="same-hash",
            is_valid=True,
        )
    )
    await db_session.commit()

    pages = [
        NavigatedPage(
            url=f"{final_url.rstrip('/')}/fees",
            category="pricing",
            markdown=new_markdown,
            content_hash=new_hash,
            cache_status="miss",
        )
    ]

    async def fake_discover_pages(self, website_url: str):
        return final_url, pages

    from unittest.mock import patch

    with patch("app.scrapers.navigator.WebsiteNavigator.discover_pages", new=fake_discover_pages):
        result = await navigate_school(
            db=db_session,
            school_id=school.id,
            country_code="bg",
            bypass_cache=True,
        )

    await db_session.refresh(school)
    assert school.scrape_status == expected_status
    assert result["material_change"] is expected_change
    assert result["status_preserved"] is (not expected_change)
    assert school.website_url == final_url


@pytest.mark.asyncio
async def test_live_navigation_same_site_redirect_does_not_hide_content_change(db_session):
    school = School(
        name_i18n={"bg": "Redirected school"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add(
        SourcePage(
            school_id=school.id,
            scrape_type=ScrapeType.WEBSITE,
            source_url="https://school.bg/fees",
            page_category="pricing",
            raw_markdown="Old fees",
            content_hash="old-hash",
            is_valid=True,
        )
    )
    await db_session.commit()

    pages = [
        NavigatedPage(
            url="https://school.bg/fees",
            category="pricing",
            markdown="New fees",
            content_hash="new-hash",
            cache_status="miss",
        )
    ]

    async def fake_discover_pages(self, website_url: str):
        return "https://www.school.bg", pages

    from unittest.mock import patch

    with patch("app.scrapers.navigator.WebsiteNavigator.discover_pages", new=fake_discover_pages):
        result = await navigate_school(
            db=db_session,
            school_id=school.id,
            country_code="bg",
            bypass_cache=True,
        )

    await db_session.refresh(school)
    assert school.scrape_status == "navigated"
    assert result["material_change"] is True
    assert result["status_preserved"] is False


@pytest.mark.asyncio
async def test_failed_live_navigation_keeps_completed_school_and_evidence(db_session):
    school = School(
        name_i18n={"bg": "School behind a CAPTCHA"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()
    existing_page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url="https://school.bg",
        page_category="about",
        raw_markdown="Published school information",
        content_hash="published-hash",
        is_valid=True,
    )
    db_session.add(existing_page)
    await db_session.commit()

    pages = [
        NavigatedPage(
            url="https://school.bg/.well-known/sgcaptcha/",
            category=None,
            markdown="Checking the site connection security",
            content_hash="captcha-hash",
            cache_status="miss",
        )
    ]

    async def fake_discover_pages(self, website_url: str):
        return website_url, pages

    from unittest.mock import patch

    with patch("app.scrapers.navigator.WebsiteNavigator.discover_pages", new=fake_discover_pages):
        result = await navigate_school(
            db=db_session,
            school_id=school.id,
            country_code="bg",
            bypass_cache=True,
        )

    await db_session.refresh(school)
    await db_session.refresh(existing_page)
    assert result["success"] is False
    assert school.scrape_status == "extracted"
    assert existing_page.is_valid is True
    assert existing_page.raw_markdown == "Published school information"
    assert result["invalidated"] == 0
    assert result["material_change"] is False
    assert result["status_preserved"] is True


@pytest.mark.asyncio
async def test_live_navigation_does_not_invalidate_pages_omitted_by_limited_crawl(db_session):
    school = School(
        name_i18n={"bg": "School with many pages"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()
    home_page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url="https://school.bg",
        page_category="about",
        raw_markdown="Current home page",
        content_hash="home-hash",
        is_valid=True,
    )
    omitted_fees_page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url="https://school.bg/fees",
        page_category="pricing",
        raw_markdown="Stored fees",
        content_hash="fees-hash",
        is_valid=True,
    )
    db_session.add_all([home_page, omitted_fees_page])
    await db_session.commit()

    pages = [
        NavigatedPage(
            url="https://school.bg",
            category="about",
            markdown="Current home page",
            content_hash="home-hash",
            cache_status="miss",
        )
    ]

    async def fake_discover_pages(self, website_url: str):
        return website_url, pages

    from unittest.mock import patch

    with patch("app.scrapers.navigator.WebsiteNavigator.discover_pages", new=fake_discover_pages):
        result = await navigate_school(
            db=db_session,
            school_id=school.id,
            country_code="bg",
            bypass_cache=True,
        )

    await db_session.refresh(school)
    await db_session.refresh(omitted_fees_page)
    assert school.scrape_status == "extracted"
    assert omitted_fees_page.is_valid is True
    assert result["invalidated"] == 0
    assert result["material_change"] is False
    assert result["status_preserved"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unusable_markdown",
    ["Checking the site connection security", None],
)
async def test_live_navigation_preserves_existing_page_on_partial_unusable_response(
    db_session, unusable_markdown
):
    school = School(
        name_i18n={"bg": "School with partial challenge"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()
    home_page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url="https://school.bg",
        page_category="about",
        raw_markdown="Current home page",
        content_hash="home-hash",
        is_valid=True,
    )
    challenged_fees_page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url="https://school.bg/fees",
        page_category="pricing",
        raw_markdown="Stored fees",
        content_hash="fees-hash",
        is_valid=True,
    )
    db_session.add_all([home_page, challenged_fees_page])
    await db_session.commit()

    pages = [
        NavigatedPage(
            url="https://school.bg",
            category="about",
            markdown="Current home page",
            content_hash="home-hash",
            cache_status="miss",
        ),
        NavigatedPage(
            url="https://school.bg/fees",
            category="pricing",
            markdown=unusable_markdown,
            content_hash="captcha-hash",
            cache_status="miss",
        ),
    ]

    async def fake_discover_pages(self, website_url: str):
        return website_url, pages

    from unittest.mock import patch

    with patch("app.scrapers.navigator.WebsiteNavigator.discover_pages", new=fake_discover_pages):
        result = await navigate_school(
            db=db_session,
            school_id=school.id,
            country_code="bg",
            bypass_cache=True,
        )

    await db_session.refresh(school)
    await db_session.refresh(challenged_fees_page)
    assert school.scrape_status == "extracted"
    assert challenged_fees_page.is_valid is True
    assert challenged_fees_page.raw_markdown == "Stored fees"
    assert result["invalidated"] == 0
    assert result["material_change"] is False
    assert result["status_preserved"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "response_url", "bypass_cache"),
    [
        (404, "https://school.bg/fees", True),
        (410, "https://school.bg/fees/", True),
        (404, "https://school.bg/fees", False),
    ],
)
async def test_live_navigation_invalidates_definitively_removed_page(
    db_session, status_code, response_url, bypass_cache
):
    school = School(
        name_i18n={"bg": "School with removed fees"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://school.bg",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()
    removed_page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url="https://school.bg/fees",
        page_category="pricing",
        raw_markdown="Stored fees",
        content_hash="fees-hash",
        is_valid=True,
    )
    db_session.add(removed_page)
    await db_session.commit()

    pages = [
        NavigatedPage(
            url=response_url,
            category="pricing",
            markdown="Page not found",
            content_hash="removed-hash",
            cache_status="miss",
            status_code=status_code,
        )
    ]

    async def fake_discover_pages(self, website_url: str):
        return website_url, pages

    from unittest.mock import patch

    with patch("app.scrapers.navigator.WebsiteNavigator.discover_pages", new=fake_discover_pages):
        result = await navigate_school(
            db=db_session,
            school_id=school.id,
            country_code="bg",
            bypass_cache=bypass_cache,
        )

    await db_session.refresh(school)
    await db_session.refresh(removed_page)
    assert result["success"] is False
    assert school.scrape_status == "navigated"
    assert removed_page.is_valid is False
    assert removed_page.raw_markdown is None
    assert result["invalidated"] == 1
    assert result["material_change"] is True
    assert result["status_preserved"] is False


@pytest.mark.asyncio
async def test_navigate_school_returns_failure_when_no_extractable_content(db_session):
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
        NavigatedPage(
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

    with patch("app.scrapers.navigator.WebsiteNavigator.discover_pages", new=fake_discover_pages):
        result = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

    assert result["success"] is False
    assert result["pages_with_content"] == 0


@pytest.mark.asyncio
async def test_navigate_schools_batch_uses_discover_many_and_persists(db_session):
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
        "https://school0.bg": BatchDiscoverOutcome(
            seed_url="https://school0.bg",
            final_url="https://school0.bg",
            pages=[
                NavigatedPage(
                    url="https://school0.bg/about",
                    category="about",
                    markdown="About",
                    content_hash="hash-a",
                )
            ],
        ),
        "https://school1.bg": BatchDiscoverOutcome(
            seed_url="https://school1.bg",
            final_url="https://school1.bg",
            pages=[
                NavigatedPage(
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

    with patch("app.scrapers.navigator.WebsiteNavigator.discover_pages_many", new=fake_discover_many):
        results = await navigate_schools_batch(
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
async def test_navigate_schools_batch_retries_failed_outcome_sequentially(db_session):
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
            "https://school.bg": BatchDiscoverOutcome(
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
                NavigatedPage(
                    url="https://school.bg/about",
                    category="about",
                    markdown="About",
                    content_hash="hash-about",
                )
            ],
        )

    from unittest.mock import patch

    with (
        patch("app.scrapers.navigator.WebsiteNavigator.discover_pages_many", new=fake_discover_many),
        patch("app.scrapers.navigator.WebsiteNavigator.discover_pages", new=fake_discover_pages),
    ):
        results = await navigate_schools_batch(
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


@pytest.mark.asyncio
async def test_discover_pages_many_falls_back_per_chunk_on_timeout():
    navigator = WebsiteNavigator(country_code="bg")
    chunk_calls: list[list[str]] = []

    async def fake_discover_many_chunk(self, website_urls, *, max_concurrency):
        chunk_calls.append(list(website_urls))
        if website_urls[0] == "https://school0.bg":
            raise asyncio.TimeoutError("chunk timeout")
        return {
            "https://school2.bg": BatchDiscoverOutcome(
                seed_url="https://school2.bg",
                final_url="https://school2.bg",
                pages=[
                    NavigatedPage(
                        url="https://school2.bg/about",
                        category="about",
                        markdown="About",
                        content_hash="hash-about",
                    )
                ],
            )
        }

    async def fake_discover_pages(self, website_url: str):
        return (
            website_url,
            [
                NavigatedPage(
                    url=f"{website_url}/about",
                    category="about",
                    markdown=f"About {website_url}",
                    content_hash=f"hash-{website_url}",
                )
            ],
        )

    from unittest.mock import patch

    with (
        patch.object(WebsiteNavigator, "_discover_pages_many_chunk", new=fake_discover_many_chunk),
        patch.object(WebsiteNavigator, "discover_pages", new=fake_discover_pages),
    ):
        outcomes = await navigator.discover_pages_many(
            ["https://school0.bg", "https://school1.bg", "https://school2.bg"],
            max_concurrency=2,
        )

    assert chunk_calls == [
        ["https://school0.bg", "https://school1.bg"],
        ["https://school2.bg"],
    ]
    assert set(outcomes) == {"https://school0.bg", "https://school1.bg", "https://school2.bg"}
    assert outcomes["https://school0.bg"].pages[0].url == "https://school0.bg/about"
    assert outcomes["https://school1.bg"].pages[0].url == "https://school1.bg/about"
    assert outcomes["https://school2.bg"].pages[0].url == "https://school2.bg/about"
