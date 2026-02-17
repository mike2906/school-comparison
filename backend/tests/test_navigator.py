"""Tests for Stage 3 website navigation."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.models import School, SourcePage, ScrapeType
from app.scrapers.navigator import WebsiteNavigator, navigate_school


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
            <a href="/priem">Прием</a>
            <a href="/kontakti">Контакти</a>
        </body></html>
        """
        home_response.raise_for_status = MagicMock()

        admission_response = MagicMock()
        admission_response.status_code = 200
        admission_response.url = "https://school.bg/priem"
        admission_response.headers = {"content-type": "text/html"}
        admission_response.text = "<html><body>Прием и записване</body></html>"

        contacts_response = MagicMock()
        contacts_response.status_code = 200
        contacts_response.url = "https://school.bg/kontakti"
        contacts_response.headers = {"content-type": "text/html"}
        contacts_response.text = "<html><body>Телефон и адрес</body></html>"

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
        about_response.text = "<html><body>История на училището</body></html>"

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
        home_response.text = "<html><body>Добре дошли в училището</body></html>"
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
        home_response.text = "<html><body>Retry success</body></html>"
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

    async def test_normalize_url_preserves_non_tracking_query(self):
        navigator = WebsiteNavigator(country_code="bg")
        normalized = navigator._normalize_url("https://school.bg/page?lang=bg&utm_source=ads#section")
        assert normalized == "https://school.bg/page?lang=bg"

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
