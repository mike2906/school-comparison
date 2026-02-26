"""Tests for Stage 3 website navigation."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup
from sqlalchemy import select

from app.models import School, SourcePage, ScrapeType
from app.scrapers.navigator import NavigatedPage, WebsiteNavigator, _DecodedKeywordURLScorer, navigate_school


@pytest.mark.asyncio
class TestWebsiteNavigator:
    async def test_classify_page_heuristics(self):
        navigator = WebsiteNavigator(country_code="bg")

        assert navigator.classify_page("https://school.bg/priem") == "admission"
        assert navigator.classify_page("https://school.bg/taksi") == "pricing"
        assert navigator.classify_page("https://school.bg/kontakti") == "contact"
        assert navigator.classify_page("https://school.bg/about-us") == "about"

    async def test_navigate_school_creates_source_pages(self, db_session, sample_schools):
        school = sample_schools[0]
        school.website_url = "https://school.bg"
        school.scrape_status = "validated"
        db_session.add(school)
        await db_session.commit()

        home_response = MagicMock()
        home_response.status_code = 200
        home_response.url = "https://school.bg"
        home_response.headers = {"content-type": "text/html; charset=utf-8"}
        home_response.text = """
        <html><head><title>School Home</title></head>
        <body>
            <p>Добре дошли</p>
            <a href="/priem">Прием</a>
            <a href="/kontakti">Контакти</a>
        </body></html>
        """
        home_response.raise_for_status = MagicMock()

        admission_response = MagicMock()
        admission_response.status_code = 200
        admission_response.url = "https://school.bg/priem"
        admission_response.headers = {"content-type": "text/html"}
        admission_response.text = "<html><body><p>Прием и записване</p></body></html>"

        contacts_response = MagicMock()
        contacts_response.status_code = 200
        contacts_response.url = "https://school.bg/kontakti"
        contacts_response.headers = {"content-type": "text/html"}
        contacts_response.text = "<html><body><p>Телефон и адрес</p></body></html>"

        async def get_side_effect(url, *args, **kwargs):
            if url == "https://school.bg":
                return home_response
            if url == "https://school.bg/priem":
                return admission_response
            if url == "https://school.bg/kontakti":
                return contacts_response
            raise AssertionError(f"Unexpected URL in test: {url}")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = get_side_effect
            mock_client_class.return_value = mock_client

            result = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

        assert result["success"] is True
        assert result["pages_found"] >= 3

        await db_session.refresh(school)
        assert school.scrape_status == "navigated"

        pages_result = await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
        pages = pages_result.scalars().all()

        assert len(pages) >= 3
        assert any(page.page_category == "admission" for page in pages)
        assert any(page.page_category == "contact" for page in pages)

    async def test_navigate_school_is_idempotent(self, db_session, sample_schools):
        school = sample_schools[1]
        school.website_url = "https://school-two.bg"
        school.scrape_status = "validated"
        db_session.add(school)
        await db_session.commit()

        home_response = MagicMock()
        home_response.status_code = 200
        home_response.url = "https://school-two.bg"
        home_response.headers = {"content-type": "text/html"}
        home_response.text = "<html><body><a href='/about'>За нас</a></body></html>"
        home_response.raise_for_status = MagicMock()

        about_response = MagicMock()
        about_response.status_code = 200
        about_response.url = "https://school-two.bg/about"
        about_response.headers = {"content-type": "text/html"}
        about_response.text = "<html><body><p>История на училището</p></body></html>"

        async def get_side_effect(url, *args, **kwargs):
            if url == "https://school-two.bg":
                return home_response
            if url == "https://school-two.bg/about":
                return about_response
            raise AssertionError(f"Unexpected URL in test: {url}")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = get_side_effect
            mock_client_class.return_value = mock_client

            first = await navigate_school(db=db_session, school_id=school.id, country_code="bg")
            second = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

        assert first["success"] is True
        assert second["success"] is True

        pages_result = await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
        pages = pages_result.scalars().all()
        assert len(pages) == 2
        assert all(page.scrape_count >= 2 for page in pages)

    async def test_navigate_school_normalizes_scheme_less_url(self, db_session, sample_schools):
        school = sample_schools[2]
        school.website_url = "school-three.bg"
        school.scrape_status = "validated"
        db_session.add(school)
        await db_session.commit()

        home_response = MagicMock()
        home_response.status_code = 200
        home_response.url = "https://school-three.bg"
        home_response.headers = {"content-type": "text/html"}
        home_response.text = "<html><body><p>Добре дошли в училището</p></body></html>"
        home_response.raise_for_status = MagicMock()

        async def get_side_effect(url, *args, **kwargs):
            if url == "https://school-three.bg":
                return home_response
            raise AssertionError(f"Unexpected URL in test: {url}")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = get_side_effect
            mock_client_class.return_value = mock_client

            result = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

        assert result["success"] is True
        assert result["final_url"] == "https://school-three.bg"

        await db_session.refresh(school)
        assert school.website_url == "https://school-three.bg"

    async def test_navigate_school_canonicalizes_bot_challenge_final_url(self, db_session, sample_schools):
        school = sample_schools[2]
        school.website_url = "https://dg185.bg"
        school.scrape_status = "validated"
        db_session.add(school)
        db_session.add(
            SourcePage(
                school_id=school.id,
                scrape_type=ScrapeType.WEBSITE,
                source_url="https://dg185.bg/.well-known/sgcaptcha/?r=%2F&y=ipc:old",
                content_hash="old",
                raw_markdown="# dg185.bg\nChecking the site connection security",
                is_valid=True,
            )
        )
        db_session.add(
            SourcePage(
                school_id=school.id,
                scrape_type=ScrapeType.WEBSITE,
                source_url="https://info-register.com/directory-entry",
                content_hash="offdomain",
                raw_markdown="Directory listing content",
                is_valid=True,
            )
        )
        await db_session.commit()

        home_response = MagicMock()
        home_response.status_code = 200
        home_response.url = "https://dg185.bg/.well-known/sgcaptcha/?r=%2F&y=ipc:1.2.3.4:99"
        home_response.headers = {"content-type": "text/html"}
        home_response.text = "<html><body><p>Checking the site connection security</p></body></html>"
        home_response.raise_for_status = MagicMock()

        deep_stub = WebsiteNavigator(country_code="bg")
        deep_stub.fetch_engine = "crawl4ai_deep"

        async def deep_empty_discover_pages(_url, *args, **kwargs):
            return (
                _url,
                [NavigatedPage(url=_url, category=None, markdown=None, content_hash="deep-empty")],
            )

        deep_stub.discover_pages = deep_empty_discover_pages

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = home_response
            mock_client_class.return_value = mock_client

            with patch.object(WebsiteNavigator, "_clone_for_engine", return_value=deep_stub):
                result = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

        assert result["success"] is False
        assert result["pages_with_content"] == 0
        assert result["final_url"] == "https://dg185.bg"

        await db_session.refresh(school)
        assert school.website_url == "https://dg185.bg"
        assert school.scrape_status == "validated"

        page_result = await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
        pages = page_result.scalars().all()
        assert len(pages) >= 2
        assert all(page.is_valid is False for page in pages)
        assert all(page.raw_markdown is None for page in pages)

    async def test_navigate_school_retries_after_transient_http_error(self, db_session, sample_schools):
        school = sample_schools[0]
        school.website_url = "https://retry-school.bg"
        school.scrape_status = "validated"
        db_session.add(school)
        await db_session.commit()

        home_response = MagicMock()
        home_response.status_code = 200
        home_response.url = "https://retry-school.bg"
        home_response.headers = {"content-type": "text/html"}
        home_response.text = "<html><body><p>Retry success</p></body></html>"
        home_response.raise_for_status = MagicMock()

        calls = {"home": 0}

        async def get_side_effect(url, *args, **kwargs):
            if url == "https://retry-school.bg":
                calls["home"] += 1
                if calls["home"] == 1:
                    raise httpx.ReadTimeout("timeout", request=MagicMock())
                return home_response
            raise AssertionError(f"Unexpected URL in test: {url}")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = get_side_effect
            mock_client_class.return_value = mock_client

            result = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

        assert result["success"] is True
        assert calls["home"] == 2

    async def test_navigate_school_failure_reason_includes_error_type_and_url(self, db_session, sample_schools):
        school = sample_schools[1]
        school.website_url = "https://broken-school.bg"
        school.scrape_status = "validated"
        db_session.add(school)
        await db_session.commit()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = httpx.ConnectError("conn failed", request=MagicMock())
            mock_client_class.return_value = mock_client

            result = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

        assert result["success"] is False
        assert "ConnectError" in result["reason"]
        assert "https://broken-school.bg" in result["reason"]

    async def test_navigate_school_returns_failure_when_no_extractable_content(self, db_session, sample_schools):
        school = sample_schools[1]
        school.website_url = "https://js-only-school.bg"
        school.scrape_status = "validated"
        db_session.add(school)
        await db_session.commit()

        home_response = MagicMock()
        home_response.status_code = 200
        home_response.url = "https://js-only-school.bg"
        home_response.headers = {"content-type": "text/html"}
        home_response.text = "<html><body><script>window.app={}</script></body></html>"
        home_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = home_response
            mock_client_class.return_value = mock_client

            result = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

        assert result["success"] is False
        assert result["pages_found"] == 1
        assert result["pages_with_content"] == 0
        assert "No extractable page content" in result["reason"]

        await db_session.refresh(school)
        assert school.scrape_status == "validated"

    async def test_normalize_url_preserves_non_tracking_query(self):
        navigator = WebsiteNavigator(country_code="bg")
        normalized = navigator._normalize_url("https://school.bg/page?lang=bg&utm_source=ads#section")
        assert normalized == "https://school.bg/page?lang=bg"

    async def test_is_same_site_url_ignores_www_prefix(self):
        navigator = WebsiteNavigator(country_code="bg")
        assert navigator._is_same_site_url("https://www.school.bg/about", "https://school.bg") is True
        assert navigator._is_same_site_url("https://admissions.school.bg/apply", "https://school.bg") is True
        assert navigator._is_same_site_url("https://other.bg/page", "https://school.bg") is False

    async def test_discover_pages_sets_user_agent_header(self):
        navigator = WebsiteNavigator(country_code="bg")

        home_response = MagicMock()
        home_response.status_code = 200
        home_response.url = "https://school.bg"
        home_response.headers = {"content-type": "text/html"}
        home_response.text = "<html><body></body></html>"
        home_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = home_response
            mock_client_class.return_value = mock_client

            await navigator.discover_pages("https://school.bg")

        headers = mock_client_class.call_args.kwargs.get("headers", {})
        assert "SchoolScraper/1.0" in headers.get("User-Agent", "")

    async def test_extract_text_content_strips_nav_boilerplate(self):
        navigator = WebsiteNavigator(country_code="bg")
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <header><a href="/">Начало</a><a href="/za-nas">За нас</a></header>
                <main>
                  <h1>Детска градина Пример</h1>
                  <p>Нашата мисия е развитие на децата чрез игра и учене.</p>
                </main>
                <footer><a href="/kontakti">Контакти</a></footer>
              </body>
            </html>
            """,
            "html.parser",
        )

        text = navigator._extract_text_content(soup)

        assert "Начало" not in text
        assert "Контакти" not in text
        assert "Детска градина Пример" in text
        assert "Нашата мисия е развитие на децата" in text

    async def test_extract_text_content_preserves_heading_and_list_structure(self):
        navigator = WebsiteNavigator(country_code="bg")
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <main>
                  <h2>Програми</h2>
                  <ul>
                    <li>Английски език</li>
                    <li>Музика</li>
                  </ul>
                  <table>
                    <tr><th>Такса</th><th>Стойност</th></tr>
                    <tr><td>Месечна</td><td>500 BGN</td></tr>
                  </table>
                </main>
              </body>
            </html>
            """,
            "html.parser",
        )

        text = navigator._extract_text_content(soup)

        assert "## Програми" in text
        assert "- Английски език" in text
        assert "- Музика" in text
        assert "Такса | Стойност" in text
        assert "Месечна | 500 BGN" in text

    async def test_extract_text_content_uses_trafilatura_when_enabled(self):
        navigator = WebsiteNavigator(country_code="bg")
        navigator.content_extractor = "trafilatura"
        soup = BeautifulSoup("<html><body><main><h1>Title</h1></main></body></html>", "html.parser")

        with patch.object(WebsiteNavigator, "_extract_text_with_trafilatura", return_value="Trafilatura text"):
            text = navigator._extract_text_content("<html></html>", soup)

        assert text == "Trafilatura text"

    async def test_extract_text_content_trafilatura_falls_back_to_bs4(self):
        navigator = WebsiteNavigator(country_code="bg")
        navigator.content_extractor = "trafilatura"
        soup = BeautifulSoup(
            "<html><body><main><h1>Fallback Title</h1><p>Fallback paragraph</p></main></body></html>",
            "html.parser",
        )

        with patch.object(WebsiteNavigator, "_extract_text_with_trafilatura", return_value=None):
            text = navigator._extract_text_content("<html></html>", soup)

        assert "Fallback Title" in text
        assert "Fallback paragraph" in text

    async def test_extract_text_content_uses_crawl4ai_when_enabled(self):
        navigator = WebsiteNavigator(country_code="bg")
        navigator.content_extractor = "crawl4ai"
        soup = BeautifulSoup("<html><body><main><h1>Title</h1></main></body></html>", "html.parser")

        with patch.object(WebsiteNavigator, "_extract_text_with_crawl4ai", return_value="Crawl4ai text") as mock_extract:
            text = navigator._extract_text_content("<html></html>", soup, source_url="https://school.bg/page")

        assert text == "Crawl4ai text"
        mock_extract.assert_called_once_with("<html></html>", base_url="https://school.bg/page")

    async def test_extract_text_content_crawl4ai_falls_back_to_bs4(self):
        navigator = WebsiteNavigator(country_code="bg")
        navigator.content_extractor = "crawl4ai"
        soup = BeautifulSoup(
            "<html><body><main><h1>Fallback Title</h1><p>Fallback paragraph</p></main></body></html>",
            "html.parser",
        )

        with patch.object(WebsiteNavigator, "_extract_text_with_crawl4ai", return_value=None):
            text = navigator._extract_text_content("<html></html>", soup, source_url="https://school.bg/page")

        assert "Fallback Title" in text
        assert "Fallback paragraph" in text

    async def test_discover_pages_uses_crawl4ai_fetch_engine(self):
        navigator = WebsiteNavigator(country_code="bg")
        navigator.fetch_engine = "crawl4ai"

        with (
            patch.object(WebsiteNavigator, "_discover_pages_with_crawl4ai", new_callable=AsyncMock) as crawl,
            patch.object(WebsiteNavigator, "_discover_pages_with_httpx", new_callable=AsyncMock) as httpx_fetch,
        ):
            crawl.return_value = ("https://school.bg", [])
            result = await navigator.discover_pages("https://school.bg")

        assert result == ("https://school.bg", [])
        crawl.assert_awaited_once_with("https://school.bg")
        httpx_fetch.assert_not_awaited()

    async def test_discover_pages_crawl4ai_fetch_engine_fallbacks_to_httpx(self):
        navigator = WebsiteNavigator(country_code="bg")
        navigator.fetch_engine = "crawl4ai"

        with (
            patch.object(WebsiteNavigator, "_discover_pages_with_crawl4ai", new_callable=AsyncMock) as crawl,
            patch.object(WebsiteNavigator, "_discover_pages_with_httpx", new_callable=AsyncMock) as httpx_fetch,
        ):
            crawl.side_effect = RuntimeError("crawl fail")
            httpx_fetch.return_value = ("https://school.bg", [])
            result = await navigator.discover_pages("https://school.bg")

        assert result == ("https://school.bg", [])
        crawl.assert_awaited_once_with("https://school.bg")
        httpx_fetch.assert_awaited_once_with("https://school.bg")

    async def test_discover_pages_uses_crawl4ai_deep_engine(self):
        navigator = WebsiteNavigator(country_code="bg")
        navigator.fetch_engine = "crawl4ai_deep"

        with (
            patch.object(WebsiteNavigator, "_discover_pages_with_crawl4ai_deep", new_callable=AsyncMock) as deep,
            patch.object(WebsiteNavigator, "_discover_pages_with_httpx", new_callable=AsyncMock) as httpx_fetch,
        ):
            deep.return_value = ("https://school.bg", [])
            result = await navigator.discover_pages("https://school.bg")

        assert result == ("https://school.bg", [])
        deep.assert_awaited_once_with("https://school.bg")
        httpx_fetch.assert_not_awaited()

    async def test_discover_pages_crawl4ai_deep_engine_fallbacks_to_httpx(self):
        navigator = WebsiteNavigator(country_code="bg")
        navigator.fetch_engine = "crawl4ai_deep"

        with (
            patch.object(WebsiteNavigator, "_discover_pages_with_crawl4ai_deep", new_callable=AsyncMock) as deep,
            patch.object(WebsiteNavigator, "_discover_pages_with_httpx", new_callable=AsyncMock) as httpx_fetch,
        ):
            deep.side_effect = RuntimeError("deep crawl fail")
            httpx_fetch.return_value = ("https://school.bg", [])
            result = await navigator.discover_pages("https://school.bg")

        assert result == ("https://school.bg", [])
        deep.assert_awaited_once_with("https://school.bg")
        httpx_fetch.assert_awaited_once_with("https://school.bg")

    async def test_crawl_result_is_bot_challenge_detects_status_202(self):
        navigator = WebsiteNavigator(country_code="bg")
        result = MagicMock()
        result.status_code = 202
        result.redirected_url = "https://dg185.bg/.well-known/sgcaptcha/?r=%2F"
        result.url = "https://dg185.bg"
        result.markdown = None
        result.html = ""
        result.cleaned_html = ""

        assert navigator._crawl_result_is_bot_challenge(result) is True

    async def test_crawl_result_is_bot_challenge_detects_payload_marker(self):
        navigator = WebsiteNavigator(country_code="bg")
        result = MagicMock()
        result.status_code = 200
        result.redirected_url = "https://dg185.bg/"
        result.url = "https://dg185.bg/"
        result.markdown = None
        result.html = "<html><body><p>Robot Challenge Screen</p><p>This page requires cookies to be enabled</p></body></html>"
        result.cleaned_html = result.html

        assert navigator._crawl_result_is_bot_challenge(result) is True

    async def test_deep_results_need_challenge_retry_only_when_all_successes_are_challenge(self):
        navigator = WebsiteNavigator(country_code="bg")

        challenge = MagicMock()
        challenge.success = True
        challenge.status_code = 202
        challenge.redirected_url = "https://dg185.bg/.well-known/sgcaptcha/?r=%2F"
        challenge.url = "https://dg185.bg"
        challenge.markdown = None
        challenge.html = ""
        challenge.cleaned_html = ""

        normal = MagicMock()
        normal.success = True
        normal.status_code = 200
        normal.redirected_url = "https://dg185.bg/"
        normal.url = "https://dg185.bg/"
        normal.markdown = "## За нас\nСъдържание"
        normal.html = "<html><body><h1>За нас</h1></body></html>"
        normal.cleaned_html = normal.html

        assert navigator._deep_results_need_challenge_retry([challenge]) is True
        assert navigator._deep_results_need_challenge_retry([challenge, normal]) is False

    async def test_navigate_school_uses_undetected_stealth_as_second_deep_fallback(self, db_session, sample_schools):
        school = sample_schools[0]
        school.website_url = "https://school.bg"
        school.scrape_status = "validated"
        db_session.add(school)
        await db_session.commit()

        calls: list[tuple[str, bool]] = []

        async def discover_side_effect(self, website_url: str):
            calls.append((self.fetch_engine, bool(getattr(self, "crawl4ai_use_undetected_stealth", False))))
            if self.fetch_engine == "httpx":
                return (
                    website_url,
                    [NavigatedPage(url=website_url, category=None, markdown=None, content_hash="h-httpx")],
                )
            if self.fetch_engine == "crawl4ai_deep" and not bool(
                getattr(self, "crawl4ai_use_undetected_stealth", False)
            ):
                return (
                    website_url,
                    [NavigatedPage(url=website_url, category=None, markdown=None, content_hash="h-deep-empty")],
                )
            return (
                website_url,
                [
                    NavigatedPage(
                        url=f"{website_url}/about",
                        category="about",
                        markdown="## За нас\nРеално съдържание за детската градина.",
                        content_hash="h-undetected-success",
                    )
                ],
            )

        with patch.object(WebsiteNavigator, "discover_pages", new=discover_side_effect):
            result = await navigate_school(db=db_session, school_id=school.id, country_code="bg")

        assert result["success"] is True
        assert result["pages_with_content"] == 1
        assert calls == [
            ("httpx", False),
            ("crawl4ai_deep", False),
            ("crawl4ai_deep", True),
        ]

    async def test_is_extractable_page_content_filters_bot_challenge_pages(self):
        navigator = WebsiteNavigator(country_code="bg")
        challenge_page = NavigatedPage(
            url="https://dg185.bg/.well-known/sgcaptcha/?r=%2F",
            category=None,
            markdown="# dg185.bg\nChecking the site connection security",
            content_hash="x",
        )
        real_page = NavigatedPage(
            url="https://dg185.bg/about",
            category="about",
            markdown="## За нас\nДетска градина с програма и прием.",
            content_hash="y",
        )

        assert navigator._is_extractable_page_content(challenge_page) is False
        assert navigator._is_extractable_page_content(real_page) is True

    async def test_dedupe_pages_by_url_keeps_best_extractable_variant(self):
        navigator = WebsiteNavigator(country_code="bg")
        duplicate_challenge = NavigatedPage(
            url="https://dg185.bg",
            category=None,
            markdown="# dg185.bg\nChecking the site connection security",
            content_hash="challenge",
        )
        duplicate_real = NavigatedPage(
            url="https://dg185.bg",
            category="about",
            markdown="## За нас\nДетска градина с прием и програма.",
            content_hash="real",
        )

        deduped = navigator._dedupe_pages_by_url([duplicate_challenge, duplicate_real])

        assert len(deduped) == 1
        assert deduped[0].url == "https://dg185.bg"
        assert deduped[0].category == "about"
        assert "прием и програма" in (deduped[0].markdown or "")
        assert navigator._is_extractable_page_content(deduped[0]) is True


class TestDecodedKeywordURLScorer:
    """Tests for the URL-decode-aware keyword scorer used in crawl4ai_deep."""

    def test_scores_ascii_keyword_match(self):
        scorer = _DecodedKeywordURLScorer(keywords=["about", "contact"], weight=1.0)
        score = scorer.score("https://school.bg/about-us")
        assert score > 0.0

    def test_scores_cyrillic_encoded_url(self):
        # /за-нас/ URL-encoded is /%D0%B7%D0%B0-%D0%BD%D0%B0%D1%81/
        scorer = _DecodedKeywordURLScorer(keywords=["за-нас", "контакти"], weight=1.0)
        encoded_url = "https://school.bg/%D0%B7%D0%B0-%D0%BD%D0%B0%D1%81/"
        score = scorer.score(encoded_url)
        assert score > 0.0

    def test_scores_zero_for_no_match(self):
        scorer = _DecodedKeywordURLScorer(keywords=["about", "contact"], weight=1.0)
        score = scorer.score("https://school.bg/gallery/photo123")
        assert score == 0.0

    def test_weight_scales_score(self):
        scorer_full = _DecodedKeywordURLScorer(keywords=["about"], weight=1.0)
        scorer_half = _DecodedKeywordURLScorer(keywords=["about"], weight=0.5)
        url = "https://school.bg/about"
        assert scorer_half.score(url) == pytest.approx(scorer_full.score(url) * 0.5)

    def test_weight_property(self):
        scorer = _DecodedKeywordURLScorer(keywords=["about"], weight=0.7)
        assert scorer.weight == pytest.approx(0.7)

    def test_stats_property_returns_something(self):
        scorer = _DecodedKeywordURLScorer(keywords=["about"])
        # stats can be None or any object; just verify it doesn't raise
        _ = scorer.stats

    def test_score_capped_at_one(self):
        scorer = _DecodedKeywordURLScorer(keywords=["a", "b", "c"], weight=1.0)
        # URL matches all 3 keywords
        score = scorer.score("https://school.bg/a/b/c")
        assert score <= 1.0
