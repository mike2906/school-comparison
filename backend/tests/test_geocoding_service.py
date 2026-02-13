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
