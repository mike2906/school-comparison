"""Tests for geocoding service."""
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.nominatim import NominatimProvider
from app.services.geocoding.service import GeocodingService
from app.scrapers.cli import _oblast_fallback_query_specs
from app.models import School, SchoolLocation


class TestNominatimProvider:
    """Tests for Nominatim geocoding provider."""

    @pytest.mark.asyncio
    async def test_provider_name(self):
        """Provider returns correct name."""
        provider = NominatimProvider()
        assert provider.provider_name == "nominatim"

    def test_normalize_preserves_city_locality(self):
        """Normalization should keep city locality when stripping 'гр.' prefix."""
        provider = NominatimProvider()
        normalized = provider._normalize_bulgarian_address('гр. София, ул. "Брегалница", №48')
        assert normalized == "Брегалница, 48, София"

    def test_normalize_preserves_village_locality(self):
        """Normalization should keep village locality when stripping 'с.' prefix."""
        provider = NominatimProvider()
        normalized = provider._normalize_bulgarian_address('с. Казичене, ул. "Първа" № 24')
        assert normalized == "Първа 24, Казичене"

    def test_normalize_block_address_removes_entrances(self):
        """Block-style addresses should keep key block info and drop entrance noise."""
        provider = NominatimProvider()
        normalized = provider._normalize_bulgarian_address(
            'гр. София, жк. Обеля - 1, бл.102, вх.А и вх.Г'
        )
        assert normalized == "ж.к. Обеля - 1, бл.102, София"

    def test_normalize_section_marker_and_block_letter(self):
        """Roman section markers should map to numeric district names for OSM lookup."""
        provider = NominatimProvider()
        normalized = provider._normalize_bulgarian_address(
            'гр. София, кв. "Връбница" I ч.  бл. 510 А,Б'
        )
        assert normalized == "ж.к. Връбница 1, бл. 510 А, София"

    def test_normalize_expands_known_street_abbreviation(self):
        """Known local abbreviations should expand to canonical street names."""
        provider = NominatimProvider()
        normalized = provider._normalize_bulgarian_address(
            'гр. София, ул. "Плачк. манастир", №11'
        )
        assert normalized == "Плачковски манастир, 11, София"

    def test_build_candidates_includes_simplified_street_number_variant(self):
        """Fallback candidates should include street+number query when neighborhood query is too specific."""
        provider = NominatimProvider()
        normalized = provider._normalize_bulgarian_address(
            'гр. София, ж. к. "Л. Толстой", ул. "Генерал Жостов" , №1'
        )
        candidates = provider._build_bulgarian_query_candidates(normalized, city="sofia")
        assert "Генерал Жостов 1, София" in candidates

    def test_build_candidates_includes_block_variant_for_numeric_street_encoding(self):
        """Fallback candidates should generate block query for numeric neighborhood encodings."""
        provider = NominatimProvider()
        normalized = provider._normalize_bulgarian_address(
            'гр. София, ж.к. "Дружба 1", ул."5016", №3'
        )
        candidates = provider._build_bulgarian_query_candidates(normalized, city="sofia")
        assert "ж.к. Дружба 1, бл. 3, София" in candidates

    def test_oblast_fallback_specs_include_school_and_locality_queries(self):
        specs = _oblast_fallback_query_specs(
            address="с.Осиковица, община Правец",
            school_name='Основно училище "Любен Каравелов"',
            town_name="Осиковица",
            municipality_name="Правец",
        )
        queries = {query: precision for query, precision, _ in specs}
        assert 'Основно училище Любен Каравелов, Осиковица' in queries
        assert 'Осиковица, Правец' in queries
        assert queries['Осиковица, Правец'] == "locality"

    def test_oblast_fallback_specs_generate_street_query_without_number(self):
        specs = _oblast_fallback_query_specs(
            address='Копривщица, бул."х.Н.Палавеев"№77',
            school_name='Средно училище "Любен Каравелов"',
            town_name="Копривщица",
            municipality_name="Копривщица",
        )
        queries = {query: precision for query, precision, _ in specs}
        assert 'Копривщица хаджи Н Палавеев' in queries or 'Копривщица хаджи Н.Палавеев' in queries or 'Копривщица хаджи Н Палавеев 77' in queries

    def test_city_match_accepts_sofia_and_stolichna(self):
        """City matching should treat Sofia and Stolichna as equivalent."""
        provider = NominatimProvider()
        sofia_result = {"address": {"city": "София"}}
        stolichna_result = {"address": {"municipality": "Столична"}}
        ruse_result = {"address": {"city": "Русе"}}

        assert provider._result_matches_expected_city(sofia_result, "sofia")
        assert provider._result_matches_expected_city(stolichna_result, "sofia")
        assert not provider._result_matches_expected_city(ruse_result, "sofia")

    @pytest.mark.asyncio
    async def test_geocode_with_mock_response(self):
        """Test geocoding with mocked Nominatim API response."""
        provider = NominatimProvider(user_agent="Test/1.0")

        # Mock successful response
        mock_response_data = [
            {
                "lat": "42.6977",
                "lon": "23.3219",
                "display_name": "Sofia, Bulgaria",
            }
        ]

        class MockResponse:
            def __init__(self, json_data, status_code=200):
                self._json_data = json_data
                self.status_code = status_code

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise Exception(f"HTTP {self.status_code}")

            def json(self):
                return self._json_data

        async def mock_get(*args, **kwargs):
            return MockResponse(mock_response_data)

        # Create mock client
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await provider.geocode("бул. Витоша 1, София")

        # Verify result
        assert result.success is True
        assert result.lat == 42.6977
        assert result.lng == 23.3219
        assert result.provider == "nominatim"
        assert "Sofia" in result.formatted_address

    @pytest.mark.asyncio
    async def test_geocode_rejects_wrong_city_and_tries_next_candidate(self):
        """Provider should reject mismatched city hit and continue fallback queries."""
        provider = NominatimProvider(user_agent="Test/1.0")
        provider.MIN_REQUEST_INTERVAL = 0

        class MockResponse:
            def __init__(self, json_data):
                self._json_data = json_data
                self.status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return self._json_data

        async def mock_get(*args, **kwargs):
            query = kwargs["params"]["q"]
            if query == "ж.к. Дружба 1, 5016, 3, София":
                return MockResponse([
                    {
                        "lat": "43.8330064",
                        "lon": "25.9488803",
                        "display_name": "София, Русе",
                        "address": {"city": "Русе"},
                    }
                ])
            if query == "ж.к. Дружба 1, бл. 3, София":
                return MockResponse([
                    {
                        "lat": "42.6690",
                        "lon": "23.4032",
                        "display_name": "ж.к. Дружба 1, София",
                        "address": {"city": "София"},
                    }
                ])
            return MockResponse([])

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await provider.geocode(
                'гр. София, ж.к. "Дружба 1", ул."5016", №3',
                country_code="bg",
                city="sofia",
            )

        assert result.success is True
        assert result.lat == 42.6690
        assert result.lng == 23.4032
        assert "София" in result.formatted_address

    @pytest.mark.asyncio
    async def test_geocode_no_results(self):
        """Test geocoding when no results found."""
        provider = NominatimProvider(user_agent="Test/1.0")

        # Mock empty response
        mock_response_data = []

        class MockResponse:
            def __init__(self, json_data):
                self._json_data = json_data
                self.status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return self._json_data

        async def mock_get(*args, **kwargs):
            return MockResponse(mock_response_data)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await provider.geocode("Invalid Address 999")

        # Verify result
        assert result.success is False
        assert result.error == "No results found"
        assert result.provider == "nominatim"

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Test that rate limiting is enforced."""
        provider = NominatimProvider(user_agent="Test/1.0")

        # Mock response
        mock_response_data = [{"lat": "42.6977", "lon": "23.3219", "display_name": "Sofia"}]

        class MockResponse:
            def __init__(self, json_data):
                self._json_data = json_data
                self.status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return self._json_data

        async def mock_get(*args, **kwargs):
            return MockResponse(mock_response_data)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = mock_get

        import time

        with patch("httpx.AsyncClient", return_value=mock_client):
            start = time.monotonic()  # Use monotonic for more stable timing

            # Make two requests
            await provider.geocode("Address 1")
            await provider.geocode("Address 2")

            elapsed = time.monotonic() - start

            # Should take at least 1 second (rate limit between requests)
            # Allow slight tolerance for timing variations
            assert elapsed >= 0.95  # 1.0s - 50ms tolerance


@pytest.mark.asyncio
class TestGeocodingService:
    """Tests for GeocodingService."""

    async def test_service_respects_provider_setting(self, db_session: AsyncSession):
        """Test that service uses provider from settings."""
        from unittest.mock import patch
        from app.config import Settings

        # Mock settings with nominatim provider
        mock_settings = Settings(
            geocoding_provider="nominatim",
            geocoding_contact_email="test@valid-domain.com",
        )

        with patch("app.services.geocoding.service.get_settings", return_value=mock_settings):
            service = GeocodingService(db=db_session)
            assert service.provider.provider_name == "nominatim"

    async def test_service_raises_on_unknown_provider(self, db_session: AsyncSession):
        """Test that service raises error for unknown provider."""
        from unittest.mock import patch
        from app.config import Settings
        import pytest

        # Mock settings with invalid provider
        mock_settings = Settings(
            geocoding_provider="invalid_provider",
            geocoding_contact_email="test@valid-domain.com",
        )

        with patch("app.services.geocoding.service.get_settings", return_value=mock_settings):
            with pytest.raises(ValueError, match="Unknown geocoding provider"):
                GeocodingService(db=db_session)

    async def test_service_raises_on_placeholder_email(self, db_session: AsyncSession):
        """Test that service raises error if contact email is a placeholder."""
        from unittest.mock import patch
        from app.config import Settings
        import pytest

        # Mock settings with placeholder email
        mock_settings = Settings(
            geocoding_provider="nominatim",
            geocoding_contact_email="your-email@example.com",
        )

        with patch("app.services.geocoding.service.get_settings", return_value=mock_settings):
            with pytest.raises(ValueError, match="GEOCODING_CONTACT_EMAIL must be set"):
                GeocodingService(db=db_session)

    async def test_geocode_location_already_has_coordinates(self, db_session: AsyncSession):
        """Test that geocoding is skipped if location already has coordinates."""
        # Create mock provider
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"

        service = GeocodingService(db=db_session, provider=mock_provider)

        # Create a location with coordinates
        school = School(
            name_i18n={"bg": "Test School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.flush()

        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тестова 1"},
            lat=42.6977,
            lng=23.3219,
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        # Geocode
        result = await service.geocode_location(location)

        # Should return cached result without calling provider
        assert result.success is True
        assert result.provider == "cached"
        assert result.lat == 42.6977
        assert result.lng == 23.3219
        mock_provider.geocode.assert_not_called()

    async def test_geocode_location_success(self, db_session: AsyncSession):
        """Test successful geocoding of a location."""
        # Create mock provider
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.return_value = GeocodingResult(
            lat=42.6977,
            lng=23.3219,
            success=True,
            provider="mock",
        )

        service = GeocodingService(db=db_session, provider=mock_provider)

        # Create a location without coordinates
        school = School(
            name_i18n={"bg": "Test School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.flush()

        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "бул. Витоша 1, София"},
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        # Geocode
        result = await service.geocode_location(location)

        # Verify result
        assert result.success is True
        assert result.lat == 42.6977
        assert result.lng == 23.3219

        # Verify location was updated
        await db_session.refresh(location)
        assert location.lat == 42.6977
        assert location.lng == 23.3219
        assert location.geocode_meta["status"] == "accepted"
        assert location.geocode_meta["provider"] == "mock"

    async def test_geocode_location_failure(self, db_session: AsyncSession):
        """Test geocoding when provider fails."""
        # Create mock provider that returns failure
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.return_value = GeocodingResult(
            success=False,
            error="API error",
            provider="mock",
        )

        service = GeocodingService(db=db_session, provider=mock_provider)

        # Create a location without coordinates
        school = School(
            name_i18n={"bg": "Test School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.flush()

        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "Invalid Address"},
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        # Geocode
        result = await service.geocode_location(location)

        # Verify result
        assert result.success is False
        assert result.error == "API error"

        # Verify location was not updated
        await db_session.rollback()
        await db_session.refresh(location)
        assert location.lat is None
        assert location.lng is None
        assert location.geocode_meta["status"] == "failed"
        assert location.geocode_meta["provider"] == "mock"
        assert location.geocode_meta["rejection_reason"] == "API error"

    async def test_force_deterministic_miss_clears_stale_coordinates_and_records_attempt_metadata(
        self,
        db_session: AsyncSession,
    ):
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.return_value = GeocodingResult(
            success=False,
            error="No results found",
            provider="nominatim",
            method="nominatim_address",
            precision="approximate",
        )
        service = GeocodingService(db=db_session, provider=mock_provider)
        school = School(
            name_i18n={"bg": "Test School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.flush()
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "Unsupported Address"},
            lat=42.7,
            lng=23.3,
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        result = await service.geocode_location(location, force=True)

        assert result.success is False
        await db_session.refresh(location)
        assert location.lat is None
        assert location.lng is None
        assert location.geocode_meta["status"] == "failed"
        assert location.geocode_meta["method"] == "nominatim_address"
        assert location.geocode_meta["precision"] == "approximate"

    async def test_force_transient_failure_preserves_coordinates_and_accepted_metadata(
        self,
        db_session: AsyncSession,
    ):
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.return_value = GeocodingResult(
            success=False,
            error="HTTP 429",
            provider="nominatim",
        )
        service = GeocodingService(db=db_session, provider=mock_provider)
        school = School(
            name_i18n={"bg": "Test School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.flush()
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "Temporarily unavailable address"},
            lat=42.7,
            lng=23.3,
            geocode_meta={
                "status": "accepted",
                "provider": "nominatim",
                "method": "nominatim_address",
                "precision": "exact",
            },
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        result = await service.geocode_location(location, force=True)

        assert result.success is False
        await db_session.refresh(location)
        assert (location.lat, location.lng) == pytest.approx((42.7, 23.3))
        assert location.geocode_meta["status"] == "accepted"
        assert location.geocode_meta["method"] == "nominatim_address"
        assert location.geocode_meta["precision"] == "exact"
        assert location.geocode_meta["last_attempt"]["status"] == "failed"
        assert location.geocode_meta["last_attempt"]["rejection_reason"] == "HTTP 429"

    async def test_geocode_location_rejects_duplicate_geojson_name_match_with_different_address(
        self,
        db_session: AsyncSession,
    ):
        """Approximate GeoJSON name matches must not collapse distinct addresses to one point."""
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.return_value = GeocodingResult(
            lat=42.6977,
            lng=23.3219,
            success=True,
            provider="geojson_bg",
            method="geojson_name_match",
            precision="approximate",
        )

        service = GeocodingService(db=db_session, provider=mock_provider)

        school = School(
            name_i18n={"bg": "Test School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.flush()

        existing_location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Първа 1, София"},
            lat=42.6977,
            lng=23.3219,
            is_primary=True,
        )
        candidate_location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Втора 2, София"},
            is_primary=False,
        )
        db_session.add_all([existing_location, candidate_location])
        await db_session.commit()

        result = await service.geocode_location(candidate_location)

        assert result.success is False
        assert result.error == "duplicate_geojson_name_match_different_address"
        await db_session.refresh(candidate_location)
        assert candidate_location.lat is None
        assert candidate_location.lng is None
        assert candidate_location.geocode_meta["status"] == "rejected"
        assert candidate_location.geocode_meta["rejection_reason"] == result.error

    async def test_geocode_location_rejects_sofia_points_outside_write_bounds(
        self,
        db_session: AsyncSession,
    ):
        """Sofia writes use municipality bounds and store NULL instead of bad points."""
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.return_value = GeocodingResult(
            lat=43.064,
            lng=24.82002,
            success=True,
            provider="nominatim",
            method="nominatim_address",
            precision="exact",
        )

        service = GeocodingService(db=db_session, provider=mock_provider)

        school = School(
            name_i18n={"bg": "Test School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.flush()

        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Шести септември 16, София"},
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        result = await service.geocode_location(location)

        assert result.success is False
        assert result.error == "outside_sofia_write_bounds"
        await db_session.refresh(location)
        assert location.lat is None
        assert location.lng is None
        assert location.geocode_meta["candidate"] == {"lat": 43.064, "lng": 24.82002}
        assert location.geocode_meta["method"] == "nominatim_address"
        assert location.geocode_meta["precision"] == "exact"

    async def test_sofia_labeled_oblast_record_no_longer_bypasses_write_bounds(
        self,
        db_session: AsyncSession,
    ):
        class MockProvider:
            provider_name = "mock"

            def __init__(self):
                self.geocode = AsyncMock(
                    return_value=GeocodingResult(
                        lat=42.97,
                        lng=23.35,
                        success=True,
                        provider="nominatim",
                        method="nominatim_address",
                        precision="exact",
                    )
                )

        mock_provider = MockProvider()
        service = GeocodingService(db=db_session, provider=mock_provider)
        school = School(
            name_i18n={"bg": "Province school"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
            attributes={"moe_region_code": 23},
        )
        db_session.add(school)
        await db_session.flush()
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "гр. Своге, ул. Тест 1"},
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        result = await service.geocode_location(location)

        assert result.success is False
        assert result.error == "outside_sofia_write_bounds"
        await db_session.refresh(location)
        assert location.lat is None
        assert location.lng is None
        assert location.geocode_meta["status"] == "rejected"
        assert location.geocode_meta["method"] == "nominatim_address"
        assert location.geocode_meta["precision"] == "exact"

    async def test_geocode_location_accepts_bankya_within_sofia_municipality_bounds(
        self,
        db_session: AsyncSession,
    ):
        """Bankya is part of Stolichna municipality and should remain visible."""
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.return_value = GeocodingResult(
            lat=42.71125,
            lng=23.14131,
            success=True,
            provider="nominatim",
            method="nominatim_address",
            precision="exact",
        )

        service = GeocodingService(db=db_session, provider=mock_provider)

        school = School(
            name_i18n={"bg": "ДГ №25 Изворче"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="kindergarten",
        )
        db_session.add(school)
        await db_session.flush()

        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'гр. Банкя, ул. "П. Д. Петков", №15'},
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        result = await service.geocode_location(location)

        assert result.success is True
        await db_session.refresh(location)
        assert location.lat == pytest.approx(42.71125)
        assert location.lng == pytest.approx(23.14131)
        assert location.geocode_meta["status"] == "accepted"

    async def test_geocode_all_missing(self, db_session: AsyncSession):
        """Test geocoding all locations without coordinates."""
        # Create mock provider
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.return_value = GeocodingResult(
            lat=42.6977,
            lng=23.3219,
            success=True,
            provider="mock",
        )

        service = GeocodingService(db=db_session, provider=mock_provider)

        # Create school with multiple locations
        school = School(
            name_i18n={"bg": "Test School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.flush()

        # Location 1: no coordinates
        location1 = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тестова 1"},
            is_primary=True,
        )
        db_session.add(location1)

        # Location 2: no coordinates
        location2 = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тестова 2"},
            is_primary=False,
        )
        db_session.add(location2)

        # Location 3: already has coordinates
        location3 = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тестова 3"},
            lat=42.0,
            lng=23.0,
            is_primary=False,
        )
        db_session.add(location3)

        await db_session.commit()

        # Geocode all missing
        summary = await service.geocode_all_missing()

        # Verify summary
        assert summary["total"] == 2  # Only 2 locations without coordinates
        assert summary["success"] == 2
        assert summary["failed"] == 0

        # Verify locations were updated
        await db_session.refresh(location1)
        await db_session.refresh(location2)
        await db_session.refresh(location3)

        assert location1.lat == 42.6977
        assert location1.lng == 23.3219
        assert location2.lat == 42.6977
        assert location2.lng == 23.3219
        assert location3.lat == 42.0  # Unchanged
        assert location3.lng == 23.0  # Unchanged

    async def test_geocode_all_locations_force_refreshes_existing_with_limit(
        self,
        db_session: AsyncSession,
    ):
        """Forced bulk geocoding includes existing coordinates and honors limit."""
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.side_effect = [
            GeocodingResult(
                lat=42.71,
                lng=23.31,
                success=True,
                provider="mock",
                method="nominatim_address",
                precision="exact",
            ),
            GeocodingResult(
                lat=42.72,
                lng=23.32,
                success=True,
                provider="mock",
                method="nominatim_address",
                precision="exact",
            ),
        ]
        service = GeocodingService(db=db_session, provider=mock_provider)

        school = School(
            name_i18n={"bg": "Test School"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="primary",
        )
        db_session.add(school)
        await db_session.flush()

        existing = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тестова 1"},
            lat=42.0,
            lng=23.0,
            is_primary=True,
        )
        missing = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тестова 2"},
            is_primary=False,
        )
        beyond_limit = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тестова 3"},
            lat=41.0,
            lng=22.0,
            is_primary=False,
        )
        db_session.add_all([existing, missing, beyond_limit])
        await db_session.commit()

        summary = await service.geocode_all_locations(force=True, limit=2)

        assert summary == {"total": 2, "success": 2, "failed": 0}
        assert mock_provider.geocode.await_count == 2
        await db_session.refresh(existing)
        await db_session.refresh(missing)
        await db_session.refresh(beyond_limit)
        assert (existing.lat, existing.lng) == pytest.approx((42.71, 23.31))
        assert existing.geocode_meta["status"] == "accepted"
        assert existing.geocode_meta["provider"] == "mock"
        assert existing.geocode_meta["method"] == "nominatim_address"
        assert existing.geocode_meta["precision"] == "exact"
        assert existing.geocode_meta["candidate"] == {"lat": 42.71, "lng": 23.31}
        assert (missing.lat, missing.lng) == pytest.approx((42.72, 23.32))
        assert missing.geocode_meta["status"] == "accepted"
        assert (beyond_limit.lat, beyond_limit.lng) == (41.0, 22.0)
        assert beyond_limit.geocode_meta == {}

    async def test_geocode_all_locations_scopes_schools_and_propagates_country(
        self,
        db_session: AsyncSession,
    ):
        """Bulk geocoding filters by school scope and uses its country code."""
        mock_provider = AsyncMock()
        mock_provider.provider_name = "mock"
        mock_provider.geocode.return_value = GeocodingResult(
            lat=42.71,
            lng=23.31,
            success=True,
            provider="mock",
            method="nominatim_address",
            precision="exact",
        )
        service = GeocodingService(db=db_session, provider=mock_provider)

        schools = [
            School(
                name_i18n={"bg": "Sofia School"},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="primary",
            ),
            School(
                name_i18n={"bg": "Plovdiv School"},
                country_code="bg",
                city="plovdiv",
                school_type="state",
                education_level="primary",
            ),
            School(
                name_i18n={"en": "London School"},
                country_code="gb",
                city="london",
                school_type="state",
                education_level="primary",
            ),
        ]
        db_session.add_all(schools)
        await db_session.flush()
        locations = [
            SchoolLocation(
                school_id=school.id,
                address_i18n={"bg": f"Address {index}"},
                lat=42.0,
                lng=23.0,
                is_primary=True,
            )
            for index, school in enumerate(schools)
        ]
        db_session.add_all(locations)
        await db_session.commit()

        summary = await service.geocode_all_locations(
            force=True,
            country_code="bg",
            city="sofia",
        )

        assert summary == {"total": 1, "success": 1, "failed": 0}
        mock_provider.geocode.assert_awaited_once()
        assert mock_provider.geocode.await_args.kwargs["country_code"] == "bg"
        await db_session.refresh(locations[0])
        await db_session.refresh(locations[1])
        await db_session.refresh(locations[2])
        assert (locations[0].lat, locations[0].lng) == pytest.approx((42.71, 23.31))
        assert (locations[1].lat, locations[1].lng) == (42.0, 23.0)
        assert (locations[2].lat, locations[2].lng) == (42.0, 23.0)

    async def test_merged_kg_branches_skip_geojson_name_lookup(self, db_session: AsyncSession):
        """Merged kg.sofia branch families should bypass GeoJSON name-only matching."""
        class _CompositeLikeProvider:
            provider_name = "composite-mock"

            def __init__(self):
                self.geocode = AsyncMock(return_value=GeocodingResult(
                    lat=42.7,
                    lng=23.3,
                    success=True,
                    provider="composite",
                ))
                self.nominatim_provider = type(
                    "NomProvider",
                    (),
                    {
                        "geocode": AsyncMock(return_value=GeocodingResult(
                            success=False,
                            error="no results",
                            provider="nominatim",
                        ))
                    },
                )()
                self.geojson_provider = type(
                    "GeoProvider",
                    (),
                    {
                        "geocode": AsyncMock(return_value=GeocodingResult(
                            lat=42.71125,
                            lng=23.24131,
                            success=True,
                            provider="geojson_bg",
                        ))
                    },
                )()

        mock_provider = _CompositeLikeProvider()

        service = GeocodingService(db=db_session, provider=mock_provider)

        school = School(
            name_i18n={"bg": "ДГ №25 Изворче"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="kindergarten",
            attributes={
                "kg_sofia_merged_buildings": True,
                "source_refs": {"kg_sofia_bg": {"record_ids": ["174", "274", "497"]}},
            },
        )
        db_session.add(school)
        await db_session.flush()

        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'гр. Банкя, ул. "П. Д. Петков", №15'},
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        result = await service.geocode_location(location)

        # For merged branch families, direct subproviders are used:
        # Nominatim first (address-first), then GeoJSON fallback if needed.
        mock_provider.nominatim_provider.geocode.assert_called_once()
        mock_provider.geojson_provider.geocode.assert_called_once()
        mock_provider.geocode.assert_not_called()
        assert result.success is True

    async def test_sofia_oblast_schools_prefer_nominatim_before_geojson(self, db_session: AsyncSession):
        """Sofia-oblast schools should use address-first geocoding to avoid GeoJSON false positives."""
        class _CompositeLikeProvider:
            provider_name = "composite-mock"

            def __init__(self):
                self.geocode = AsyncMock(return_value=GeocodingResult(
                    lat=0.0,
                    lng=0.0,
                    success=True,
                    provider="composite",
                ))
                self.nominatim_provider = type(
                    "NomProvider",
                    (),
                    {
                        "geocode": AsyncMock(return_value=GeocodingResult(
                            lat=42.744,
                            lng=23.163,
                            success=True,
                            provider="nominatim",
                        ))
                    },
                )()
                self.geojson_provider = type(
                    "GeoProvider",
                    (),
                    {
                        "geocode": AsyncMock(return_value=GeocodingResult(
                            lat=43.80754,
                            lng=26.18329,
                            success=True,
                            provider="geojson_bg",
                        ))
                    },
                )()

        mock_provider = _CompositeLikeProvider()
        service = GeocodingService(db=db_session, provider=mock_provider)

        school = School(
            name_i18n={"bg": 'Професионална гимназия по транспорт "Никола Йонков Вапцаров"'},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="upper_secondary",
            attributes={
                "moe_region_code": 23,
                "moe_town_name": "Сливница",
            },
        )
        db_session.add(school)
        await db_session.flush()

        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": 'Сливница, ул. "Кирил и Методий" № 4'},
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        result = await service.geocode_location(location)

        mock_provider.nominatim_provider.geocode.assert_called_once()
        mock_provider.geojson_provider.geocode.assert_not_called()
        mock_provider.geocode.assert_not_called()
        assert result.success is True
        assert result.provider == "nominatim"

    async def test_sofia_oblast_transient_nominatim_failure_is_not_hidden_by_geojson_miss(
        self,
        db_session: AsyncSession,
    ):
        class _CompositeLikeProvider:
            provider_name = "composite-mock"

            def __init__(self):
                self.geocode = AsyncMock()
                self.nominatim_provider = type(
                    "NomProvider",
                    (),
                    {
                        "geocode": AsyncMock(return_value=GeocodingResult(
                            success=False,
                            error="HTTP 503",
                            provider="nominatim",
                        ))
                    },
                )()
                self.geojson_provider = type(
                    "GeoProvider",
                    (),
                    {
                        "geocode": AsyncMock(return_value=GeocodingResult(
                            success=False,
                            error="No match in GeoJSON index",
                            provider="geojson_bg",
                        ))
                    },
                )()

        mock_provider = _CompositeLikeProvider()
        service = GeocodingService(db=db_session, provider=mock_provider)
        school = School(
            name_i18n={"bg": "Province school"},
            country_code="bg",
            city="svoge",
            school_type="state",
            education_level="primary",
            attributes={"moe_region_code": 23, "moe_town_name": "Своге"},
        )
        db_session.add(school)
        await db_session.flush()
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "гр. Своге, ул. Тест 1"},
            lat=42.96,
            lng=23.35,
            geocode_meta={
                "status": "accepted",
                "provider": "nominatim",
                "method": "nominatim_address",
                "precision": "exact",
            },
            is_primary=True,
        )
        db_session.add(location)
        await db_session.commit()

        result = await service.geocode_location(location, force=True)

        assert result.error == "HTTP 503"
        await db_session.refresh(location)
        assert (location.lat, location.lng) == pytest.approx((42.96, 23.35))
        assert location.geocode_meta["status"] == "accepted"
        assert location.geocode_meta["last_attempt"]["provider"] == "nominatim"
        mock_provider.geojson_provider.geocode.assert_awaited_once()


class TestGeoJSONProvider:
    """Tests for GeoJSON geocoding provider."""

    def test_city_normalization_sofia_variants(self):
        """Test that all Sofia variants map to СТОЛИЧНА."""
        from app.services.geocoding.bg.geojson import GeoJSONProvider
        
        provider = GeoJSONProvider()
        
        # All Sofia variants should map to СТОЛИЧНА (GeoJSON convention)
        sofia_variants = ['sofia', 'SOFIA', 'Sofia', 'СОФИЯ', 'софия', 'СТОЛИЧНА']
        for variant in sofia_variants:
            assert provider._normalize_city(variant) == 'СТОЛИЧНА', \
                f"'{variant}' should normalize to 'СТОЛИЧНА'"
    
    def test_city_normalization_other_cities(self):
        """Test that other cities pass through correctly."""
        from app.services.geocoding.bg.geojson import GeoJSONProvider
        
        provider = GeoJSONProvider()
        
        # Other cities should normalize to uppercase but not map
        test_cases = [
            ('Пловдив', 'ПЛОВДИВ'),
            ('ВАРНА', 'ВАРНА'),
            ('Бургас', 'БУРГАС'),
            ('plovdiv', 'PLOVDIV'),
        ]
        
        for input_city, expected in test_cases:
            assert provider._normalize_city(input_city) == expected, \
                f"'{input_city}' should normalize to '{expected}'"


class TestGeoJSONMatching:
    """Tests for GeoJSON name+city matching logic."""

    @pytest.mark.asyncio
    async def test_exact_name_city_match(self):
        """Test exact (school_name, city) matching."""
        from app.services.geocoding.bg.geojson import GeoJSONProvider
        from unittest.mock import patch
        
        # Mock GeoJSON data with specific school in СТОЛИЧНА
        mock_geojson = {
            'features': [
                {
                    'type': 'Feature',
                    'geometry': {'coordinates': [23.3219, 42.6977]},
                    'properties': {
                        'name': 'ДЕТСКА ГРАДИНА "КАЛИНКА"',
                        'city': 'СТОЛИЧНА',
                        'street': 'УЛ. TEST №1',
                    }
                }
            ]
        }
        
        with patch('pathlib.Path.exists', return_value=True):
            with patch('builtins.open', create=True):
                with patch('json.load', return_value=mock_geojson):
                    provider = GeoJSONProvider()
                    provider._load_index()
                    
                    # Should match with city
                    result = await provider.geocode(
                        address='ул. Test №1',
                        country_code='bg',
                        school_name='ДГ "Калинка"',
                        city='sofia'  # Maps to СТОЛИЧНА
                    )
                    
                    assert result.success
                    assert result.lat == 42.6977
                    assert result.lng == 23.3219

    @pytest.mark.asyncio
    async def test_sofia_match_outside_city_bounds_is_rejected(self):
        """GeoJSON sometimes labels a row as Stolichna but stores bad coordinates."""
        from app.services.geocoding.bg.geojson import GeoJSONProvider
        from unittest.mock import patch

        mock_geojson = {
            'features': [
                {
                    'type': 'Feature',
                    'geometry': {'coordinates': [24.82002, 43.064]},
                    'properties': {
                        'name': '6 ОУ "ГРАФ ИГНАТИЕВ"',
                        'city': 'СТОЛИЧНА',
                        'street': 'УЛ. ШЕСТИ СЕПТЕМВРИ 16',
                    }
                }
            ]
        }

        with patch('pathlib.Path.exists', return_value=True):
            with patch('builtins.open', create=True):
                with patch('json.load', return_value=mock_geojson):
                    provider = GeoJSONProvider()
                    provider._load_index()

                    result = await provider.geocode(
                        address='ул. Шести септември 16',
                        country_code='bg',
                        school_name='6 ОУ "Граф Игнатиев"',
                        city='sofia'
                    )

                    assert not result.success
                    assert "outside expected city bounds" in result.error

    @pytest.mark.asyncio
    async def test_ambiguous_name_rejection(self):
        """Test that ambiguous names (multiple cities) are rejected."""
        from app.services.geocoding.bg.geojson import GeoJSONProvider
        from unittest.mock import patch
        
        # Mock GeoJSON with same school name in different cities
        mock_geojson = {
            'features': [
                {
                    'type': 'Feature',
                    'geometry': {'coordinates': [23.3219, 42.6977]},
                    'properties': {
                        'name': 'ДЕТСКА ГРАДИНА "КАЛИНКА"',
                        'city': 'СТОЛИЧНА',
                        'street': 'УЛ. TEST №1',
                    }
                },
                {
                    'type': 'Feature',
                    'geometry': {'coordinates': [24.7453, 42.1354]},
                    'properties': {
                        'name': 'ДЕТСКА ГРАДИНА "КАЛИНКА"',
                        'city': 'ПЛОВДИВ',
                        'street': 'УЛ. TEST №2',
                    }
                }
            ]
        }
        
        with patch('pathlib.Path.exists', return_value=True):
            with patch('builtins.open', create=True):
                with patch('json.load', return_value=mock_geojson):
                    provider = GeoJSONProvider()
                    provider._load_index()
                    
                    # Should reject ambiguous match (no city provided)
                    result = await provider.geocode(
                        address='ул. Test №1',
                        country_code='bg',
                        school_name='ДГ "Калинка"',
                        city=None  # No city to disambiguate
                    )
                    
                    assert not result.success
                    assert 'ambiguous' in result.error.lower()

    @pytest.mark.asyncio
    async def test_match_by_website_host(self):
        """GeoJSON provider can recover coordinates by website host."""
        from app.services.geocoding.bg.geojson import GeoJSONProvider
        from unittest.mock import patch

        mock_geojson = {
            'features': [
                {
                    'type': 'Feature',
                    'geometry': {'coordinates': [23.25751, 42.65307]},
                    'properties': {
                        'name': 'ЧАСТНО СРЕДНО УЧИЛИЩЕ "УВЕКИНД"',
                        'city': 'СТОЛИЧНА',
                        'street': 'УЛ. ДЕЯН ГЬОРГОВ №4',
                        'url': 'www.uwekind.com',
                    }
                }
            ]
        }

        with patch('pathlib.Path.exists', return_value=True):
            with patch('builtins.open', create=True):
                with patch('json.load', return_value=mock_geojson):
                    provider = GeoJSONProvider()
                    provider._load_index()
                    result = await provider.geocode_by_website("https://uwekind.com")

                    assert result.success
                    assert result.lat == pytest.approx(42.65307)
                    assert result.lng == pytest.approx(23.25751)


class TestCompositeProvider:
    """Tests for composite provider fallback behavior."""

    @pytest.mark.asyncio
    async def test_fallback_to_nominatim(self):
        """Test that composite falls back to Nominatim when GeoJSON fails."""
        from app.services.geocoding.composite import CompositeGeocodingProvider
        from unittest.mock import AsyncMock, patch
        
        # Mock failed GeoJSON result
        mock_geojson_result = AsyncMock(return_value=type('Result', (), {
            'success': False,
            'error': 'No match in GeoJSON index'
        })())
        
        # Mock successful Nominatim result
        mock_nominatim_result = AsyncMock(return_value=type('Result', (), {
            'success': True,
            'lat': 42.6977,
            'lng': 23.3219,
            'provider': 'nominatim'
        })())
        
        provider = CompositeGeocodingProvider()
        
        with patch.object(provider.geojson_provider, 'geocode', mock_geojson_result):
            with patch.object(provider.nominatim_provider, 'geocode', mock_nominatim_result):
                result = await provider.geocode(
                    address='ул. Иван Вазов 15',
                    country_code='bg',
                    school_name='ДГ Тестова',
                    city='sofia'
                )
                
                # Should have tried GeoJSON first
                mock_geojson_result.assert_called_once()
                
                # Should have fallen back to Nominatim
                mock_nominatim_result.assert_called_once()
                
                # Should return Nominatim result
                assert result.success
                assert result.provider == 'nominatim'
