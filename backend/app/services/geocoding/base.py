"""Base geocoding provider interface."""
from abc import ABC, abstractmethod
from typing import Literal, Optional
from dataclasses import dataclass


GeocodingMethod = Literal[
    "geojson_name_match",
    "nominatim_address",
    "nominatim_fallback",
    "website_map_link",
]
GeocodingPrecision = Literal["exact", "approximate"]


@dataclass
class GeocodingResult:
    """Result of a geocoding operation."""

    lat: Optional[float] = None
    lng: Optional[float] = None
    success: bool = False
    error: Optional[str] = None
    provider: str = ""
    formatted_address: Optional[str] = None  # Normalized address from provider
    method: Optional[GeocodingMethod] = None
    precision: Optional[GeocodingPrecision] = None


class BaseGeocodingProvider(ABC):
    """
    Abstract base class for geocoding providers.

    This allows us to swap between OSM Nominatim, Google Maps, Mapbox, etc.
    without changing the calling code.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the name of this provider (e.g., 'nominatim', 'google')."""
        pass

    @abstractmethod
    async def geocode(
        self,
        address: str,
        country_code: str = "bg",
        school_name: Optional[str] = None,
        city: Optional[str] = None
    ) -> GeocodingResult:
        """
        Convert an address to lat/lng coordinates.

        Args:
            address: Full address string (e.g., "ул. Иван Вазов 15, София")
            country_code: ISO 3166-1 alpha-2 country code (default: "bg" for Bulgaria)
            school_name: Optional school name for providers that can use it for matching (e.g., GeoJSON)
            city: Optional city name for disambiguation (e.g., "София", "Пловдив")

        Returns:
            GeocodingResult with coordinates or error information
        """
        pass

    @abstractmethod
    async def reverse_geocode(self, lat: float, lng: float) -> GeocodingResult:
        """
        Convert coordinates to an address (optional, for future use).

        Args:
            lat: Latitude
            lng: Longitude

        Returns:
            GeocodingResult with formatted address
        """
        pass
