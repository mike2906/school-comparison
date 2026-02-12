"""Tests for geocoding service."""
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.nominatim import NominatimProvider
from app.services.geocoding.service import GeocodingService
from app.models import School, SchoolLocation


class TestNominatimProvider:
    """Tests for Nominatim geocoding provider."""

    @pytest.mark.asyncio
    async def test_provider_name(self):
        """Provider returns correct name."""
        provider = NominatimProvider()
        assert provider.provider_name == "nominatim"

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
        await db_session.refresh(location)
        assert location.lat is None
        assert location.lng is None

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
