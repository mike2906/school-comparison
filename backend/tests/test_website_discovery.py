"""Tests for website discovery stage."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.models import School, SourcePage, ScrapeType
from app.scrapers.website_discovery import WebsiteDiscoverer, discover_school_website


@pytest.mark.asyncio
class TestWebsiteDiscovery:
    """Test website discovery and normalization behavior."""

    @pytest.fixture(autouse=True)
    def _reset_provider_flags(self):
        WebsiteDiscoverer.reset_provider_state()
        yield
        WebsiteDiscoverer.reset_provider_state()

    async def test_discovers_by_normalizing_existing_url(self, db_session):
        school = School(
            name_i18n={"bg": "Тест училище", "en": "Test School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url="school.bg",
            scrape_status="pending",
        )
        db_session.add(school)
        await db_session.commit()

        result = await discover_school_website(
            db=db_session,
            school_id=school.id,
            country_code="bg",
            use_search_fallback=False,
        )

        await db_session.refresh(school)
        assert result["found"] is True
        assert result["updated"] is True
        assert school.website_url == "https://school.bg"

        page_result = await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.scrape_type == ScrapeType.DISCOVERY,
                SourcePage.source_url == "https://school.bg",
            )
        )
        assert page_result.scalar_one_or_none() is not None

    async def test_discovers_from_email_domain_and_resets_failed_status(self, db_session):
        school = School(
            name_i18n={"bg": "Тест училище", "en": "Test School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url=None,
            scrape_status="failed_validate",
            attributes={"moe_email": "office@school123.bg"},
        )
        db_session.add(school)
        await db_session.commit()

        result = await discover_school_website(
            db=db_session,
            school_id=school.id,
            country_code="bg",
            use_search_fallback=False,
        )

        await db_session.refresh(school)
        assert result["found"] is True
        assert result["website_url"] == "https://school123.bg"
        assert school.website_url == "https://school123.bg"
        assert school.scrape_status == "pending"

    async def test_skips_non_aligned_generic_email_domain_candidate(self, db_session):
        school = School(
            name_i18n={"bg": "ЧАСТНА ДЕТСКА ГРАДИНА МАРГАРИТКА", "en": "Private Kindergarten Margaritka"},
            country_code="bg",
            school_type="private",
            education_level="kindergarten",
            city="sofia",
            website_url=None,
            scrape_status="failed_validate",
            attributes={"moe_email": "office@edu.mon.bg"},
        )
        db_session.add(school)
        await db_session.commit()

        result = await discover_school_website(
            db=db_session,
            school_id=school.id,
            country_code="bg",
            use_search_fallback=False,
        )

        await db_session.refresh(school)
        assert result["found"] is False
        assert result["terminal_status"] == "no_official_website"
        assert school.scrape_status == "no_official_website"
        assert school.website_url is None

    async def test_uses_search_fallback_when_no_local_candidate(self, db_session):
        school = School(
            name_i18n={"bg": "Училище Тест", "en": "Test School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url=None,
            source_url="https://ri-api.mon.bg/data/get/public-register",
            scrape_status="pending",
            attributes={},
        )
        db_session.add(school)
        await db_session.commit()

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {
            "results": [
                {"url": "https://facebook.com/school"},
                {"url": "https://official-school.bg"},
            ]
        }

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result = await discover_school_website(
                db=db_session,
                school_id=school.id,
                country_code="bg",
                use_search_fallback=True,
            )

        await db_session.refresh(school)
        assert result["found"] is True
        assert school.website_url == "https://official-school.bg"

    async def test_failed_validate_forces_fresh_search_candidate(self, db_session):
        school = School(
            name_i18n={"bg": "Училище Тест", "en": "Test School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url="https://old-school.bg",
            scrape_status="failed_validate",
            attributes={},
        )
        db_session.add(school)
        await db_session.commit()

        with patch.object(
            WebsiteDiscoverer,
            "_search_candidates",
            new=AsyncMock(return_value=["https://fresh-school.bg"]),
        ):
            result = await discover_school_website(
                db=db_session,
                school_id=school.id,
                country_code="bg",
                use_search_fallback=True,
            )

        await db_session.refresh(school)
        assert result["found"] is True
        assert result["updated"] is True
        assert school.website_url == "https://fresh-school.bg"
        assert school.scrape_status == "pending"

    async def test_failed_validate_no_candidates_sets_terminal_status(self, db_session):
        school = School(
            name_i18n={"bg": "Училище Без Сайт", "en": "No Site School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url="https://old-school.bg",
            scrape_status="failed_validate",
            attributes={},
        )
        db_session.add(school)
        await db_session.commit()

        with patch.object(WebsiteDiscoverer, "_search_candidates", new=AsyncMock(return_value=[])):
            result = await discover_school_website(
                db=db_session,
                school_id=school.id,
                country_code="bg",
                use_search_fallback=True,
            )

        await db_session.refresh(school)
        assert result["found"] is False
        assert result["terminal_status"] == "no_official_website"
        assert school.scrape_status == "no_official_website"
        assert school.website_url is None

    async def test_blocks_directory_and_document_urls(self, db_session):
        school = School(
            name_i18n={"bg": "Училище Тест", "en": "Test School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url=None,
            scrape_status="pending",
            attributes={},
        )
        db_session.add(school)
        await db_session.commit()

        with patch.object(
            WebsiteDiscoverer,
            "_search_candidates",
            new=AsyncMock(
                return_value=[
                    "https://papagal.bg/eik/12345",
                    "https://example.com/files/fees.pdf",
                    "https://official-school.bg",
                ]
            ),
        ):
            result = await discover_school_website(
                db=db_session,
                school_id=school.id,
                country_code="bg",
                use_search_fallback=True,
            )

        await db_session.refresh(school)
        assert result["found"] is True
        assert school.website_url == "https://official-school.bg"

    async def test_search_candidates_uses_simplified_name_variant(self):
        discoverer = WebsiteDiscoverer(country_code="bg")
        discoverer.search_providers = ["searxng"]

        school = School(
            id=113,
            name_i18n={"bg": "ДГ №11 Мики Маус (с яслени групи)"},
            country_code="bg",
            school_type="state",
            education_level="kindergarten",
            city="sofia",
            scrape_status="pending",
        )

        async def fake_searxng(query: str, school_id: int) -> list[str]:
            if "(с яслени групи)" in query:
                return [
                    "https://example-1.bg/a",
                    "https://example-2.bg/a",
                    "https://example-3.bg/a",
                    "https://example-4.bg/a",
                    "https://example-5.bg/a",
                ]
            return ["https://dg11-mikimaus.com/"]

        with patch.object(discoverer, "_search_searxng", new=AsyncMock(side_effect=fake_searxng)) as mocked:
            links = await discoverer._search_candidates(school)

        called_queries = [call.kwargs["query"] for call in mocked.await_args_list]
        assert any("(с яслени групи)" in query for query in called_queries)
        assert any("ДГ №11 Мики Маус sofia официален сайт" == query for query in called_queries)
        assert "https://dg11-mikimaus.com/" in links

    async def test_build_search_queries_includes_brand_variant_without_legal_suffix(self):
        discoverer = WebsiteDiscoverer(country_code="bg")
        queries = discoverer._build_search_queries(
            school_name='"ЧАСТНА ДЕТСКА ГРАДИНА БЪЛГАРАНЧЕ" ЕООД',
            city="sofia",
        )

        assert any("българанче sofia официален сайт" == query.lower() for query in queries)
        assert any("частна детска градина българанче" == query.lower() for query in queries)

    async def test_pick_best_candidate_prefers_root_url(self):
        discoverer = WebsiteDiscoverer(country_code="bg")
        seen: set[str] = set()
        candidates = [
            ("https://mladost.info/spravochnik/uslugi/", "search"),
            ("https://dg11-mikimaus.com/obshti-usloviya/", "search"),
            ("https://dg11-mikimaus.com/", "search"),
        ]

        best_url, method = discoverer._pick_best_candidate(candidates, seen)

        assert best_url == "https://dg11-mikimaus.com"
        assert method == "search"

    async def test_pick_best_candidate_prefers_school_aligned_domain(self):
        discoverer = WebsiteDiscoverer(country_code="bg")
        seen: set[str] = set()
        candidates = [
            ("https://detskitegradini.com/detski-gradini-sofia/", "search"),
            ("https://5dg.eu/%D0%BF%D1%80%D0%BE%D1%84%D0%B8%D0%BB-%D0%BD%D0%B0-%D0%BA%D1%83%D0%BF%D1%83%D0%B2%D0%B0%D1%87%D0%B0/", "search"),
        ]

        best_url, method = discoverer._pick_best_candidate(
            candidates,
            seen,
            school_name="ДГ №5 Надежда",
        )

        assert best_url == "https://5dg.eu"
        assert method == "search"

    async def test_search_candidates_expands_result_to_site_root(self):
        discoverer = WebsiteDiscoverer(country_code="bg")
        discoverer.search_providers = ["searxng"]

        school = School(
            id=114,
            name_i18n={"bg": "ДГ №5 Надежда"},
            country_code="bg",
            school_type="state",
            education_level="kindergarten",
            city="sofia",
            scrape_status="pending",
        )

        with patch.object(
            discoverer,
            "_search_searxng",
            new=AsyncMock(
                return_value=[
                    "https://5dg.eu/%D0%BF%D1%80%D0%BE%D1%84%D0%B8%D0%BB-%D0%BD%D0%B0-%D0%BA%D1%83%D0%BF%D1%83%D0%B2%D0%B0%D1%87%D0%B0/",
                ]
            ),
        ):
            links = await discoverer._search_candidates(school)

        assert "https://5dg.eu" in links
        assert "https://5dg.eu/%D0%BF%D1%80%D0%BE%D1%84%D0%B8%D0%BB-%D0%BD%D0%B0-%D0%BA%D1%83%D0%BF%D1%83%D0%B2%D0%B0%D1%87%D0%B0/" in links

    async def test_search_candidates_uses_fallback_only_when_primary_empty(self):
        discoverer = WebsiteDiscoverer(country_code="bg")
        discoverer.search_providers = ["searxng", "brave", "duckduckgo"]

        school = School(
            id=115,
            name_i18n={"bg": "ДГ №5 Надежда"},
            country_code="bg",
            school_type="state",
            education_level="kindergarten",
            city="sofia",
            scrape_status="pending",
        )

        with (
            patch.object(discoverer, "_search_searxng", new=AsyncMock(return_value=["https://5dg.eu"])) as searxng_mock,
            patch.object(discoverer, "_search_brave", new=AsyncMock(return_value=["https://brave-result.bg"])) as brave_mock,
            patch.object(
                discoverer,
                "_search_duckduckgo",
                new=AsyncMock(return_value=["https://duck-result.bg"]),
            ) as duck_mock,
        ):
            links = await discoverer._search_candidates(school)

        assert "https://5dg.eu" in links
        assert searxng_mock.await_count >= 1
        brave_mock.assert_not_awaited()
        duck_mock.assert_not_awaited()

    async def test_search_candidates_falls_back_when_primary_has_only_non_viable_results(self):
        discoverer = WebsiteDiscoverer(country_code="bg")
        discoverer.search_providers = ["searxng", "brave"]

        school = School(
            id=116,
            name_i18n={"bg": "ДГ №5 Надежда"},
            country_code="bg",
            school_type="state",
            education_level="kindergarten",
            city="sofia",
            scrape_status="pending",
        )

        with (
            patch.object(discoverer, "_search_searxng", new=AsyncMock(return_value=["https://facebook.com/school"])) as searxng_mock,
            patch.object(discoverer, "_search_brave", new=AsyncMock(return_value=["https://5dg.eu"])) as brave_mock,
        ):
            links = await discoverer._search_candidates(school)

        assert "https://5dg.eu" in links
        assert searxng_mock.await_count >= 1
        assert brave_mock.await_count >= 1

    async def test_email_domain_alignment_rejects_unrelated_domain(self):
        discoverer = WebsiteDiscoverer(country_code="bg")
        school_tokens = discoverer._extract_school_name_tokens("ДГ №5 Надежда")

        assert discoverer._is_school_aligned_email_domain("example-business.bg", school_tokens) is False

    async def test_disabled_provider_reenables_after_cooldown(self):
        discoverer = WebsiteDiscoverer(country_code="bg")
        discoverer.provider_disable_seconds = 1
        discoverer._disable_provider("searxng", "test")
        assert "searxng" in WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC

        WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC["searxng"] = 0.0
        with patch.object(discoverer, "_search_searxng", new=AsyncMock(return_value=["https://5dg.eu"])) as mock:
            links = await discoverer._search_provider("searxng", "query", school_id=999)

        assert links == ["https://5dg.eu"]
        assert "searxng" not in WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC
        assert WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC.get("searxng") is None
        assert mock.await_count == 1

    async def test_recover_reuses_initial_search_candidates_when_provider_disabled(self, db_session):
        """recover_failed_school must reuse candidates from the initial discover() pass.

        Scenario: Brave returns [high-score-suspended.bg, lower-score-real.bg].
        First validation picks high-score-suspended.bg → fails (suspended).
        Brave is now disabled (429).  Without the fix, the retry loop re-searches and
        gets nothing.  With the fix, lower-score-real.bg is tried from the saved pool.
        """
        from app.scrapers.url_validator import ValidationResult
        from app.scrapers.website_discovery import WebsiteDiscoverer

        school = School(
            name_i18n={"bg": "ЧАСТНА ДЕТСКА ГРАДИНА ЗВЕЗДИЧКА ЕООД"},
            country_code="bg",
            school_type="private",
            education_level="kindergarten",
            city="sofia",
            website_url="https://yox.bg/частна-детска-градина-звездичка",
            scrape_status="failed_validate",
            attributes={},
        )
        db_session.add(school)
        await db_session.commit()

        discoverer = WebsiteDiscoverer(country_code="bg")

        # Brave returns two candidates on the FIRST call; subsequent calls return nothing
        # (simulating 429 rate-limit after first use).
        search_call_count = 0

        async def mock_search(s):
            nonlocal search_call_count
            search_call_count += 1
            if search_call_count == 1:
                return ["https://zvezdichka-sofia.com/", "https://dg185.bg/"]
            return []

        # zvezdichka-sofia.com → suspended (INVALID)
        # dg185.bg → bot-protected (VALID)
        async def mock_validate(school_id, url, country_code, update_db, school_name):
            await db_session.refresh(school)
            if "zvezdichka" in url:
                school.scrape_status = "failed_validate"
                await db_session.commit()
                return ValidationResult.INVALID, None, "suspended"
            else:
                school.scrape_status = "validated"
                await db_session.commit()
                return ValidationResult.VALID, url, "Bot protection detected"

        with (
            patch.object(discoverer, "_search_candidates", new=AsyncMock(side_effect=mock_search)),
            patch(
                "app.scrapers.url_validator.validate_school_url",
                new=AsyncMock(side_effect=mock_validate),
            ),
        ):
            result = await discoverer.recover_failed_school(
                db=db_session,
                school=school,
                max_attempts=4,
            )

        await db_session.refresh(school)
        # The fix: dg185.bg was tried from the cached candidate pool
        assert result["validation_result"] == "valid"
        assert school.scrape_status == "validated"
        # Only ONE search call was made, not two
        assert search_call_count == 1

    async def test_searxng_403_falls_back_to_brave_and_disables_searxng(self, db_session):
        school = School(
            name_i18n={"bg": "Училище Тест", "en": "Test School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            website_url=None,
            scrape_status="pending",
            attributes={},
        )
        db_session.add(school)
        await db_session.commit()

        searxng_response = MagicMock()
        searxng_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "forbidden",
            request=MagicMock(),
            response=MagicMock(status_code=403),
        )
        brave_response = MagicMock()
        brave_response.raise_for_status.return_value = None
        brave_response.text = """
        <html><body>
          <a href="https://official-school.bg">official</a>
        </body></html>
        """

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None

            async def fake_get(url, params=None):
                if "localhost:8080/search" in url:
                    return searxng_response
                if "search.brave.com" in url:
                    return brave_response
                raise AssertionError(f"Unexpected search URL: {url}")

            mock_client.get.side_effect = fake_get
            mock_client_class.return_value = mock_client

            first = await discover_school_website(
                db=db_session,
                school_id=school.id,
                country_code="bg",
                use_search_fallback=True,
            )
            second = await discover_school_website(
                db=db_session,
                school_id=school.id,
                country_code="bg",
                use_search_fallback=True,
            )

        assert first["found"] is True
        assert second["found"] is True
        assert "searxng" in WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC
        assert "brave" not in WebsiteDiscoverer._DISABLED_UNTIL_MONOTONIC

        searxng_calls = [
            call for call in mock_client.get.await_args_list
            if "localhost:8080/search" in str(call.kwargs.get("url", "")) or "localhost:8080/search" in str(call.args[0])
        ]
        brave_calls = [
            call for call in mock_client.get.await_args_list
            if "search.brave.com" in str(call.kwargs.get("url", "")) or "search.brave.com" in str(call.args[0])
        ]
        # First run: SearXNG + Brave. Second run uses discovered URL directly (no search call).
        assert len(searxng_calls) == 1
        assert len(brave_calls) >= 1
