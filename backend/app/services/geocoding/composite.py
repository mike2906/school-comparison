"""Composite geocoding provider with fallback strategy.

Tries multiple providers in order:
1. GeoJSON lookup (instant, no rate limits)
2. Nominatim API (rate-limited fallback)
"""
import logging
from typing import Optional

from app.services.geocoding.base import BaseGeocodingProvider, GeocodingResult
from app.services.geocoding.bg import GeoJSONProvider
from app.services.geocoding.nominatim import NominatimProvider

logger = logging.getLogger(__name__)


class CompositeGeocodingProvider(BaseGeocodingProvider):
    """
    Composite geocoding provider that tries multiple sources in order.

    Strategy:
    1. Try GeoJSON lookup (instant, no API calls) - for Bulgarian schools only
    2. Fall back to Nominatim API if no match found

    This maximizes coverage while minimizing API usage and rate limiting.
    """

    def __init__(
        self,
        user_agent: str = "SofiaSchoolComparison/1.0 (contact@example.com)",
        geojson_path: Optional[str] = None,
    ):
        """
        Initialize composite provider.

        Args:
            user_agent: User-Agent for Nominatim requests
            geojson_path: Path to GeoJSON file (optional)
        """
        self.geojson_provider = GeoJSONProvider(geojson_path=geojson_path)
        self.nominatim_provider = NominatimProvider(user_agent=user_agent)

    @property
    def provider_name(self) -> str:
        return "composite"

    async def geocode(
        self,
        address: str,
        country_code: str = "bg",
        school_name: Optional[str] = None,
        city: Optional[str] = None,
    ) -> GeocodingResult:
        """
        Geocode with fallback strategy.

        Process:
        1. If country is "bg" and school_name provided, try GeoJSON lookup
        2. If no match, fall back to Nominatim with address

        Args:
            address: Full address string
            country_code: ISO country code (default: "bg")
            school_name: School name for GeoJSON matching (optional)
            city: City name for GeoJSON matching (optional)

        Returns:
            GeocodingResult from first successful provider
        """
        # Try GeoJSON first (if Bulgarian school with name)
        if country_code == "bg" and school_name:
            logger.debug(f"Trying GeoJSON lookup for '{school_name}' in '{city}'")
            result = await self.geojson_provider.geocode(
                address=address,
                country_code=country_code,
                school_name=school_name,
                city=city,
            )
            if result.success:
                logger.info(f"✓ GeoJSON match: '{school_name}' → ({result.lat}, {result.lng})")
                return result
            else:
                logger.debug(f"GeoJSON miss: {result.error}")

        # Fall back to Nominatim
        logger.debug(f"Falling back to Nominatim for '{address}'")
        result = await self.nominatim_provider.geocode(
            address=address,
            country_code=country_code,
            city=city,
        )

        if result.success:
            logger.info(f"✓ Nominatim match: '{address}' → ({result.lat}, {result.lng})")
        else:
            logger.warning(f"✗ All providers failed for '{address}' (school: {school_name})")

        return result

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodingResult:
        """
        Reverse geocode coordinates.

        Only Nominatim supports reverse geocoding.
        """
        return await self.nominatim_provider.reverse_geocode(lat=lat, lng=lng)
