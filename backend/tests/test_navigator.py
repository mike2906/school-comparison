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
