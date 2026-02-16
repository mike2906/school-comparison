"""Main geocoding service that coordinates geocoding operations."""
import logging
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolLocation, School
from app.services.geocoding.base import BaseGeocodingProvider, GeocodingResult
from app.services.geocoding.nominatim import NominatimProvider
from app.services.geocoding.composite import CompositeGeocodingProvider
from app.config import get_settings

logger = logging.getLogger(__name__)


class GeocodingService:
    """
    Service for geocoding school locations.

    This service:
    - Uses a configurable provider (default: Nominatim)
    - Checks database cache before making API calls
    - Updates database with results
    - Respects rate limits and usage policies
    """

    def __init__(
        self,
        db: AsyncSession,
        provider: Optional[BaseGeocodingProvider] = None,
    ):
        """
        Initialize geocoding service.

        Args:
            db: Database session
            provider: Geocoding provider (default: based on GEOCODING_PROVIDER setting)
        """
        self.db = db
        self.settings = get_settings()

        # Use provided provider or create one based on settings
        if provider is None:
            provider_name = self.settings.geocoding_provider.lower()

            # Validate contact email is not a placeholder (required for Nominatim and Composite)
            contact_email = self.settings.geocoding_contact_email
            if not contact_email or "example.com" in contact_email.lower():
                raise ValueError(
                    "GEOCODING_CONTACT_EMAIL must be set to a valid email in .env file. "
                    "Nominatim requires a real contact email in the User-Agent header. "
                    f"Current value: {contact_email}"
                )
            user_agent = f"SofiaSchoolComparison/1.0 ({contact_email})"

            if provider_name == "composite":
                # Composite provider (GeoJSON + Nominatim fallback) - RECOMMENDED
                self.provider = CompositeGeocodingProvider(user_agent=user_agent)
            elif provider_name == "nominatim":
                # Nominatim only (no GeoJSON optimization)
                self.provider = NominatimProvider(user_agent=user_agent)
            # TODO: Add other providers when implemented
            # elif provider_name == "google":
            #     self.provider = GoogleMapsProvider(api_key=self.settings.google_maps_api_key)
            # elif provider_name == "mapbox":
            #     self.provider = MapboxProvider(api_key=self.settings.mapbox_api_key)
            else:
                raise ValueError(
                    f"Unknown geocoding provider: {provider_name}. "
                    f"Supported providers: composite, nominatim"
                )
        else:
            self.provider = provider

        logger.info(f"GeocodingService initialized with provider: {self.provider.provider_name}")

    async def geocode_location(
        self,
        location: SchoolLocation,
        force: bool = False,
        country_code: str = "bg",
    ) -> GeocodingResult:
        """
        Geocode a school location.

        Args:
            location: SchoolLocation object to geocode
            force: If True, re-geocode even if coordinates already exist
            country_code: ISO country code (default: "bg")

        Returns:
            GeocodingResult with status and coordinates
        """
        # Check if already geocoded (unless force=True)
        if not force and location.lat is not None and location.lng is not None:
            logger.debug(
                f"Location {location.id} already has coordinates ({location.lat}, {location.lng}), skipping"
            )
            return GeocodingResult(
                lat=location.lat,
                lng=location.lng,
                success=True,
                provider="cached",
            )

        # Get address from i18n dict (prefer Bulgarian for Bulgaria)
        address = location.address_i18n.get("bg") or location.address_i18n.get("en")
        if not address:
            logger.error(f"Location {location.id} has no address in address_i18n")
            return GeocodingResult(
                success=False,
                error="No address available",
            )

        # Fetch school data for GeoJSON matching (if provider supports it)
        school_name = None
        city = None
        if hasattr(self.provider, 'geojson_provider'):  # Composite provider
            school_result = await self.db.execute(
                select(School).where(School.id == location.school_id)
            )
            school = school_result.scalar_one_or_none()
            if school:
                school_name = school.name_i18n.get("bg") or school.name_i18n.get("en")
                city = school.city  # Use city from database instead of parsing address

                # For merged branch families (kg.sofia "сграда"), name-only GeoJSON
                # lookups can collapse multiple branches to one point. Force
                # address-first fallback by skipping GeoJSON in these cases.
                attrs = school.attributes or {}
                source_refs = attrs.get("source_refs") if isinstance(attrs, dict) else {}
                kg_ref = source_refs.get("kg_sofia_bg") if isinstance(source_refs, dict) else {}
                kg_record_ids = kg_ref.get("record_ids") if isinstance(kg_ref, dict) else []
                is_merged_branch_school = bool(attrs.get("kg_sofia_merged_buildings")) or (
                    isinstance(kg_record_ids, list) and len(kg_record_ids) > 1
                )
                if is_merged_branch_school:
                    school_name = None

        # Geocode using provider
        logger.info(f"Geocoding location {location.id}: {address} (school: {school_name}, city: {city})")
        result = None

        # Special handling for merged kg.sofia branch families:
        # 1) Try address-first geocoding (Nominatim) for per-branch precision
        # 2) If that fails, fall back to GeoJSON using school name
        if (
            school_name is None
            and hasattr(self.provider, "nominatim_provider")
            and hasattr(self.provider, "geojson_provider")
        ):
            result = await self.provider.nominatim_provider.geocode(
                address=address,
                country_code=country_code,
                city=city,
            )
            if not result.success:
                school_result = await self.db.execute(
                    select(School).where(School.id == location.school_id)
                )
                school = school_result.scalar_one_or_none()
                fallback_school_name = None
                if school:
                    fallback_school_name = school.name_i18n.get("bg") or school.name_i18n.get("en")
                if fallback_school_name:
                    result = await self.provider.geojson_provider.geocode(
                        address=address,
                        country_code=country_code,
                        school_name=fallback_school_name,
                        city=city,
                    )

        if result is None:
            result = await self.provider.geocode(
                address,
                country_code=country_code,
                school_name=school_name,
                city=city,
            )

        # Update database if successful
        if result.success and result.lat is not None and result.lng is not None:
            location.lat = result.lat
            location.lng = result.lng
            # TODO: Consider batching commits for better performance when geocoding many locations
            await self.db.commit()
            logger.info(f"Updated location {location.id} with coordinates ({result.lat}, {result.lng})")
        else:
            logger.warning(f"Failed to geocode location {location.id}: {result.error}")

        return result

    async def geocode_school_locations(
        self,
        school_id: int,
        force: bool = False,
    ) -> dict[int, GeocodingResult]:
        """
        Geocode all locations for a school.

        Args:
            school_id: School ID
            force: If True, re-geocode even if coordinates already exist

        Returns:
            Dict mapping location_id to GeocodingResult
        """
        # Fetch all locations for this school
        result = await self.db.execute(
            select(SchoolLocation).where(SchoolLocation.school_id == school_id)
        )
        locations = result.scalars().all()

        if not locations:
            logger.warning(f"School {school_id} has no locations to geocode")
            return {}

        # Geocode each location
        results = {}
        for location in locations:
            geocode_result = await self.geocode_location(location, force=force)
            results[location.id] = geocode_result

        return results

    async def geocode_all_missing(self, limit: Optional[int] = None) -> dict:
        """
        Geocode all school locations that don't have coordinates yet.

        Args:
            limit: Maximum number of locations to geocode (for rate limiting)

        Returns:
            Summary dict with counts
        """
        # Find locations without coordinates
        result = await self.db.execute(
            select(SchoolLocation).where(
                (SchoolLocation.lat.is_(None)) | (SchoolLocation.lng.is_(None))
            )
        )
        locations = result.scalars().all()

        if not locations:
            logger.info("All locations already have coordinates")
            return {"total": 0, "success": 0, "failed": 0}

        # Apply limit if specified
        if limit:
            locations = locations[:limit]

        logger.info(f"Found {len(locations)} locations without coordinates")

        # Geocode each location
        success_count = 0
        failed_count = 0

        for location in locations:
            geocode_result = await self.geocode_location(location, force=False)
            if geocode_result.success:
                success_count += 1
            else:
                failed_count += 1

        logger.info(
            f"Geocoding complete: {success_count} success, {failed_count} failed out of {len(locations)} total"
        )

        return {
            "total": len(locations),
            "success": success_count,
            "failed": failed_count,
        }
