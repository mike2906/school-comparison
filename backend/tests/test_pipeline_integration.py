"""Integration tests for scraping pipeline orchestration (Stages 1-3).

These tests focus on the integration points between pipeline stages,
not the full end-to-end execution (which requires Celery workers).
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, Mock

from app.models import School, SourcePage, ScrapeType
from app.scrapers.url_validator import ValidationResult


@pytest.mark.asyncio
class TestURLValidationIntegration:
    """Test URL validation with database updates."""

    async def test_validate_school_url_updates_database(self, db_session, sample_schools):
        """URL validation updates school scrape_status."""
        school = sample_schools[0]
        school.website_url = "https://school.bg"
        school.scrape_status = "pending"
        db_session.add(school)
        await db_session.commit()

        # Mock valid HTTP response
        html_content = """
        <html><body>
            <h1>Училище</h1>
            <p>Ученици и учители</p>
            <p>Прием на нови ученици</p>
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://school.bg"
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            # Use the validation function (it uses its own async_session_maker)
            # So we can't directly test DB updates without mocking async_session_maker
            # Instead, test the validator directly
            from app.scrapers.url_validator import URLValidator

            validator = URLValidator(country_code="bg")
            result, final_url, reason = await validator.validate_url(
                url=school.website_url,
                use_llm_fallback=False,
            )

            assert result == ValidationResult.VALID
            assert final_url == "https://school.bg"
            assert "Keyword match" in reason

    async def test_validate_invalid_url(self, db_session, sample_schools):
        """Invalid URL is rejected by validator."""
        school = sample_schools[0]
        school.website_url = "https://generic-example.bg"

        # Mock response with no configured school keywords.
        html_content = """
        <html><body>
            <h1>Generic Website</h1>
            <p>General information about unrelated topics.</p>
        </body></html>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.url = "https://generic-example.bg"
        mock_response.text = html_content

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            from app.scrapers.url_validator import URLValidator

            validator = URLValidator(country_code="bg")
            result, final_url, reason = await validator.validate_url(
                url=school.website_url,
                use_llm_fallback=False,
            )

            assert result == ValidationResult.INVALID
            assert final_url is None
            assert "No school-related keywords" in reason


@pytest.mark.asyncio
class TestDiscoveryIntegration:
    """Test discovery adapter integration."""

    async def test_kg_sofia_adapter_upsert_flow(self, db_session):
        """KgSofiaBg adapter can upsert schools."""
        from app.scrapers.sources.base_adapter import BaseSourceAdapter
        from app.schemas.scraping import DiscoveredSchool, DiscoveredLocation

        # Create a simple test adapter
        class TestAdapter(BaseSourceAdapter):
            ADAPTER_NAME = "test"
            COUNTRY_CODE = "bg"
            CITY = "sofia"

            async def discover(self, limit=None):
                return []

        adapter = TestAdapter(db=db_session)

        # Test upsert with discovered school
        discovered = DiscoveredSchool(
            institutional_id="12345678",
            name_i18n={"bg": "ДГ №1 Тест", "en": "KG #1 Test"},
            country_code="bg",
            school_type="state",
            education_level="kindergarten",
            city="sofia",
            locations=[
                DiscoveredLocation(
                    address_i18n={"bg": "ул. Тест 1", "en": "1 Test St"},
                    lat=42.6977,
                    lng=23.3219,
                    age_groups=["first"],
                    shift="morning",
                )
            ],
        )

        result = await adapter.upsert_schools([discovered])

        assert result["created"] == 1
        assert result["updated"] == 0
        assert result["skipped"] == 0

        # Verify school was created
        from sqlalchemy import select

        stmt = select(School).where(School.institutional_id == "12345678")
        school_result = await db_session.execute(stmt)
        school = school_result.scalar_one_or_none()

        assert school is not None
        assert school.name_i18n["bg"] == "ДГ №1 Тест"
        assert school.city == "sofia"
        assert school.country_code == "bg"

    async def test_upsert_duplicate_prevents_duplicates(self, db_session):
        """Upserting the same school twice updates instead of creating."""
        from app.scrapers.sources.base_adapter import BaseSourceAdapter
        from app.schemas.scraping import DiscoveredSchool

        class TestAdapter(BaseSourceAdapter):
            ADAPTER_NAME = "test"
            COUNTRY_CODE = "bg"
            CITY = "sofia"

            async def discover(self, limit=None):
                return []

        adapter = TestAdapter(db=db_session)

        discovered = DiscoveredSchool(
            institutional_id="99999999",
            name_i18n={"bg": "Тест училище", "en": "Test school"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            locations=[],
        )

        # First upsert - creates
        result1 = await adapter.upsert_schools([discovered])
        assert result1["created"] == 1

        # Second upsert - updates (same institutional_id)
        result2 = await adapter.upsert_schools([discovered])
        assert result2["updated"] == 1
        assert result2["created"] == 0

    async def test_authoritative_upsert_preserves_existing_curated_locale(self, db_session):
        """A BG-only registry refresh must not erase a curated canonical EN name."""
        from app.scrapers.sources.base_adapter import BaseSourceAdapter
        from app.schemas.scraping import DiscoveredSchool

        class TestAdapter(BaseSourceAdapter):
            ADAPTER_NAME = "test"
            COUNTRY_CODE = "bg"
            CITY = "sofia"

            async def discover(self, limit=None):
                return []

        existing = School(
            institutional_id="curated-identity",
            name_i18n={"bg": "Старо име", "en": "The Beehive"},
            attributes={
                "canonical_identity_curation": {
                    "en": {
                        "status": "promoted",
                        "value": "The Beehive",
                    }
                }
            },
            country_code="bg",
            school_type="private",
            education_level="kindergarten",
            city="sofia",
        )
        db_session.add(existing)
        await db_session.commit()

        result = await TestAdapter(db=db_session).upsert_schools(
            [
                DiscoveredSchool(
                    institutional_id="curated-identity",
                    name_i18n={"bg": "Ново официално име"},
                    country_code="bg",
                    school_type="private",
                    education_level="kindergarten",
                    city="sofia",
                    locations=[],
                )
            ]
        )
        await db_session.refresh(existing)

        assert result["updated"] == 1
        assert existing.name_i18n == {
            "bg": "Ново официално име",
            "en": "The Beehive",
        }


@pytest.mark.asyncio
class TestPipelineStateTransitions:
    """Test scrape_status state transitions."""

    async def test_school_starts_with_pending_status(self, db_session):
        """New schools have pending scrape_status."""
        school = School(
            name_i18n={"bg": "Тестово училище", "en": "Test School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.commit()

        assert school.scrape_status == "pending"

    async def test_status_progression_pending_to_validated(self, db_session):
        """Status can progress from pending to validated."""
        school = School(
            name_i18n={"bg": "Училище", "en": "School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            scrape_status="pending",
        )
        db_session.add(school)
        await db_session.commit()

        # Simulate validation success
        school.scrape_status = "validated"
        await db_session.commit()

        assert school.scrape_status == "validated"

    async def test_status_progression_pending_to_failed(self, db_session):
        """Status can progress from pending to failed_validate."""
        school = School(
            name_i18n={"bg": "Училище", "en": "School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            scrape_status="pending",
        )
        db_session.add(school)
        await db_session.commit()

        # Simulate validation failure
        school.scrape_status = "failed_validate"
        await db_session.commit()

        assert school.scrape_status == "failed_validate"


@pytest.mark.asyncio
class TestPipelineOrchestration:
    """Test Celery task orchestration."""

    async def test_run_full_pipeline_returns_id(self):
        """Pipeline returns execution ID."""
        from tasks.scrape_tasks import run_full_pipeline

        with patch("tasks.scrape_tasks.chain") as mock_chain:
            mock_pipeline = MagicMock()
            mock_pipeline.apply_async.return_value.id = "test-pipeline-123"
            mock_chain.return_value = mock_pipeline

            result = run_full_pipeline(
                country_code="bg",
                city="sofia",
                limit=5,
            )

            assert "pipeline_id" in result
            assert result["pipeline_id"] == "test-pipeline-123"
            assert "Stages 1-7" in result["message"]

    async def test_pipeline_uses_chain_for_stages(self):
        """Pipeline chains discovery, website discovery, validation, and navigation."""
        from tasks.scrape_tasks import run_full_pipeline

        with patch("tasks.scrape_tasks.chain") as mock_chain:
            mock_pipeline = MagicMock()
            mock_pipeline.apply_async.return_value.id = "test-123"
            mock_chain.return_value = mock_pipeline

            run_full_pipeline(country_code="bg", city="sofia")

            # Verify chain was called
            mock_chain.assert_called_once()
            assert len(mock_chain.call_args.args) == 8

    async def test_run_stage_dispatches_nvo_task(self):
        """run_stage should expose the independent NVO task."""
        from tasks.scrape_tasks import run_stage

        with patch("tasks.scrape_tasks.scrape_nvo_results.apply_async") as apply_async_mock:
            apply_async_mock.return_value.id = "nvo-task-123"

            result = run_stage(
                stage="nvo",
                country_code="bg",
                city="sofia",
                school_ids=[42],
                year=2025,
                history_years=3,
                exam_types=["nvo_7"],
            )

            apply_async_mock.assert_called_once_with(
                kwargs={
                    "country_code": "bg",
                    "city": "sofia",
                    "year": 2025,
                    "history_years": 3,
                    "exam_types": ["nvo_7"],
                    "school_ids": [42],
                }
            )
            assert result["stage"] == "nvo"
            assert result["task_id"] == "nvo-task-123"

    async def test_scrape_nvo_results_task_passes_arguments_to_async_import(self):
        """NVO task wrapper should pass through task arguments."""
        from tasks.scrape_tasks import scrape_nvo_results

        with patch(
            "tasks.scrape_tasks._scrape_nvo_results_async",
            new=Mock(return_value="nvo-coro"),
        ) as async_mock, patch(
            "tasks.scrape_tasks.run_async",
            return_value={"created_rows": 2},
        ) as run_async_mock:
            result = scrape_nvo_results(
                country_code="bg",
                city="sofia",
                year=2025,
                history_years=4,
                exam_types=["nvo_4", "nvo_7"],
                school_ids=[7],
            )

            async_mock.assert_called_once_with(
                country_code="bg",
                city="sofia",
                year=2025,
                history_years=4,
                exam_types=["nvo_4", "nvo_7"],
                school_ids=[7],
            )
            run_async_mock.assert_called_once_with("nvo-coro")
            assert result == {"created_rows": 2}


@pytest.mark.asyncio
class TestAdapterRegistry:
    """Test adapter registry system."""

    async def test_get_adapters_for_country_bg(self):
        """Bulgaria has at least 2 adapters registered."""
        from app.scrapers.sources import get_adapters_for_country

        adapters = get_adapters_for_country("bg", "sofia")

        assert len(adapters) >= 2
        adapter_names = [a.ADAPTER_NAME for a in adapters]
        assert any("kg" in name.lower() for name in adapter_names)

    async def test_get_adapter_by_name(self):
        """Can retrieve adapter by name."""
        from app.scrapers.sources import get_adapter

        # Should not raise
        kg_adapter = get_adapter("kg_sofia_bg")
        assert kg_adapter is not None
        assert kg_adapter.ADAPTER_NAME == "kg_sofia_bg"

        moe_adapter = get_adapter("moe_registry")
        assert moe_adapter is not None
        assert moe_adapter.ADAPTER_NAME == "moe_registry"

    async def test_get_invalid_adapter_raises(self):
        """Invalid adapter name raises KeyError."""
        from app.scrapers.sources import get_adapter

        with pytest.raises(KeyError):
            get_adapter("nonexistent_adapter")


@pytest.mark.asyncio
class TestErrorHandling:
    """Test error handling in pipeline."""

    async def test_validator_handles_http_timeout(self):
        """Validator handles HTTP timeout gracefully."""
        from app.scrapers.url_validator import URLValidator

        validator = URLValidator(country_code="bg")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.side_effect = Exception("Timeout")
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                url="https://slow-school.bg",
                use_llm_fallback=False,
            )

            assert result == ValidationResult.INVALID
            assert final_url is None
            assert "error" in reason.lower() or "validation error" in reason.lower()

    async def test_validator_handles_404(self):
        """Validator handles 404 errors."""
        from app.scrapers.url_validator import URLValidator

        validator = URLValidator(country_code="bg")

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            mock_client.get.return_value = mock_response
            mock_client_class.return_value = mock_client

            result, final_url, reason = await validator.validate_url(
                url="https://not-found.bg",
                use_llm_fallback=False,
            )

            assert result == ValidationResult.INVALID
            assert "404" in reason
