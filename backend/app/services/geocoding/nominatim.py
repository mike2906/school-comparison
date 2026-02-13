"""OpenStreetMap Nominatim geocoding provider."""
import logging
import asyncio
from typing import Optional
import httpx

from app.services.geocoding.base import BaseGeocodingProvider, GeocodingResult

logger = logging.getLogger(__name__)


class NominatimProvider(BaseGeocodingProvider):
    """
    Geocoding provider using OpenStreetMap Nominatim API.

    IMPORTANT: This provider respects Nominatim Usage Policy:
    https://operations.osmfoundation.org/policies/nominatim/

    - Rate limit: Maximum 1 request per second
    - User-Agent: Must include contact information
    - Caching: Results should be cached to avoid redundant requests
    - No heavy usage: Don't use this for bulk geocoding of thousands of addresses
    """

    # Nominatim API endpoint (official OSM instance)
    BASE_URL = "https://nominatim.openstreetmap.org"

    # Rate limit: 1 request per second (OSM policy)
    MIN_REQUEST_INTERVAL = 1.0  # seconds

    def __init__(self, user_agent: str = "SofiaSchoolComparison/1.0 (contact@example.com)"):
        """
        Initialize Nominatim provider.

        Args:
            user_agent: User-Agent string with contact information (REQUIRED by OSM policy)
        """
        self.user_agent = user_agent
        self._last_request_time: Optional[float] = None
        self._rate_limit_lock = asyncio.Lock()  # Ensure thread-safe rate limiting

    @property
    def provider_name(self) -> str:
        return "nominatim"

    async def _enforce_rate_limit(self):
        """
        Enforce rate limit: wait if necessary to ensure 1 second between requests.

        This is REQUIRED by Nominatim Usage Policy.
        Thread-safe via asyncio.Lock to prevent concurrent requests from bypassing rate limit.
        """
        async with self._rate_limit_lock:
            if self._last_request_time is not None:
                import time
                elapsed = time.monotonic() - self._last_request_time
                if elapsed < self.MIN_REQUEST_INTERVAL:
                    wait_time = self.MIN_REQUEST_INTERVAL - elapsed
                    logger.debug(f"Rate limiting: waiting {wait_time:.2f}s before next Nominatim request")
                    await asyncio.sleep(wait_time)

            import time
            self._last_request_time = time.monotonic()

    def _normalize_bulgarian_address(self, address: str) -> str:
        """
        Normalize Bulgarian address for better Nominatim results.

        Removes city prefixes, street abbreviations, and formatting that confuses geocoding.

        Examples:
            'гр. София, ул. "Брегалница", №48' -> 'Брегалница 48, София'
            'район Панчарево, ж. к. Малинова Долина, ул. "Първа" № 24' -> 'Първа 24, Малинова Долина, Панчарево, София'
        """
        import re

        # Remove quotes first (before removing abbreviations)
        logger.debug(f"Before quote removal: {address!r}")
        address = address.replace('"', '').replace('"', '').replace('"', '')
        logger.debug(f"After quote removal: {address!r}")

        # Extract locality prefix (гр./с.) and keep it for re-append later.
        # This avoids dropping crucial disambiguation data for village addresses.
        locality = None
        locality_match = re.match(r'^\s*(гр\.|с\.)\s*([^,]+)\s*,\s*(.+)$', address, flags=re.IGNORECASE)
        if locality_match:
            locality = locality_match.group(2).strip()
            address = locality_match.group(3).strip()

        # Remove district prefix (район X,)
        address = re.sub(r'район\s+([^,]+),\s*', r'\1, ', address)

        # Remove neighborhood prefix (ж. к. X,) but keep the name
        address = re.sub(r'ж\.\s*к\.\s*', '', address)

        # Remove street abbreviations (бул. BEFORE ул. to avoid partial match)
        logger.debug(f"Before abbreviation removal: {address!r}")
        address = re.sub(r'бул\.\s*', '', address)  # Remove "бул." first
        address = re.sub(r'ул\.\s*', '', address)   # Then remove "ул."
        logger.debug(f"After abbreviation removal: {address!r}")

        # Replace № with space
        address = address.replace('№', ' ')

        # Clean up extra spaces and commas
        address = re.sub(r'\s+', ' ', address).strip()
        address = re.sub(r',\s*,', ',', address)

        # Re-append locality if it was stripped from a city/village prefix.
        if locality and locality.lower() not in address.lower():
            address = f"{address}, {locality}" if address else locality

        return address

    async def geocode(self, address: str, country_code: str = "bg", school_name: Optional[str] = None, city: Optional[str] = None) -> GeocodingResult:
        """
        Geocode an address using Nominatim.

        Args:
            address: Full address string (e.g., "ул. Иван Вазов 15, София")
            country_code: ISO country code (default: "bg")
            school_name: Optional school name (not used by Nominatim)
            city: Optional city name (not used by Nominatim - address should be complete)

        Returns:
            GeocodingResult with coordinates or error
        """
        # Normalize Bulgarian addresses for better results
        if country_code == "bg":
            normalized_address = self._normalize_bulgarian_address(address)
            logger.debug(f"Normalized address: '{address}' -> '{normalized_address}'")
        else:
            normalized_address = address

        # Enforce rate limit
        await self._enforce_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/search",
                    params={
                        "q": normalized_address,
                        "format": "json",
                        "countrycodes": country_code,
                        "limit": 1,
                        "addressdetails": 1,
                    },
                    headers={
                        "User-Agent": self.user_agent,
                    },
                )
                response.raise_for_status()

                results = response.json()

                if not results:
                    logger.warning(f"Nominatim: No results found for address: {normalized_address} (original: {address})")
                    return GeocodingResult(
                        success=False,
                        error="No results found",
                        provider=self.provider_name,
                    )

                # Get first (best) result
                result = results[0]
                lat = float(result["lat"])
                lng = float(result["lon"])
                formatted_address = result.get("display_name")

                logger.info(f"Nominatim: Successfully geocoded '{address}' → ({lat}, {lng})")

                return GeocodingResult(
                    lat=lat,
                    lng=lng,
                    success=True,
                    provider=self.provider_name,
                    formatted_address=formatted_address,
                )

        except httpx.HTTPStatusError as e:
            logger.error(f"Nominatim HTTP error for '{address}': {e}")
            return GeocodingResult(
                success=False,
                error=f"HTTP {e.response.status_code}",
                provider=self.provider_name,
            )
        except Exception as e:
            logger.error(f"Nominatim error geocoding '{address}': {e}")
            return GeocodingResult(
                success=False,
                error=str(e),
                provider=self.provider_name,
            )

    async def reverse_geocode(self, lat: float, lng: float) -> GeocodingResult:
        """
        Reverse geocode coordinates to an address.

        Args:
            lat: Latitude
            lng: Longitude

        Returns:
            GeocodingResult with formatted address
        """
        # Enforce rate limit
        await self._enforce_rate_limit()

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.BASE_URL}/reverse",
                    params={
                        "lat": lat,
                        "lon": lng,
                        "format": "json",
                        "addressdetails": 1,
                    },
                    headers={
                        "User-Agent": self.user_agent,
                    },
                )
                response.raise_for_status()

                result = response.json()

                if "error" in result:
                    logger.warning(f"Nominatim: No address found for ({lat}, {lng})")
                    return GeocodingResult(
                        success=False,
                        error=result["error"],
                        provider=self.provider_name,
                    )

                formatted_address = result.get("display_name")

                logger.info(f"Nominatim: Successfully reverse geocoded ({lat}, {lng}) → '{formatted_address}'")

                return GeocodingResult(
                    lat=lat,
                    lng=lng,
                    success=True,
                    provider=self.provider_name,
                    formatted_address=formatted_address,
                )

        except httpx.HTTPStatusError as e:
            logger.error(f"Nominatim HTTP error for ({lat}, {lng}): {e}")
            return GeocodingResult(
                success=False,
                error=f"HTTP {e.response.status_code}",
                provider=self.provider_name,
            )
        except Exception as e:
            logger.error(f"Nominatim error reverse geocoding ({lat}, {lng}): {e}")
            return GeocodingResult(
                success=False,
                error=str(e),
                provider=self.provider_name,
            )
