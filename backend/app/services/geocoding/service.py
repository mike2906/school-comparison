"""Main geocoding service that coordinates geocoding operations."""
import logging
from typing import Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchoolLocation, School
from app.services.geocoding.base import BaseGeocodingProvider, GeocodingResult
from app.services.geocoding.nominatim import NominatimProvider
from app.services.geocoding.composite import CompositeGeocodingProvider
from app.services.geocoding.write_gate import apply_geocode_result_to_location
from app.config import get_settings

logger = logging.getLogger(__name__)

_DETERMINISTIC_FORCE_FAILURES = {
    "No address available",
    "No results found",
}


def _is_deterministic_force_failure(result: GeocodingResult) -> bool:
    """Return whether a failed refresh proves the stored point is unsupported."""
    return result.error in _DETERMINISTIC_FORCE_FAILURES


def _preferred_geocoding_city(school: School | None) -> Optional[str]:
    if school is None:
        return None

    attrs = school.attributes or {}
    if attrs.get("moe_region_code") == 23:
        return (
            attrs.get("moe_town_name")
            or attrs.get("moe_municipality_name")
            or attrs.get("moe_region_name")
            or school.city
        )
    return school.city


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
                method=(location.geocode_meta or {}).get("method"),
                precision=(location.geocode_meta or {}).get("precision"),
            )

        had_coordinates = location.lat is not None and location.lng is not None
        previous_geocode_meta = dict(location.geocode_meta or {})

        # Get address from i18n dict (prefer Bulgarian for Bulgaria)
        address = location.address_i18n.get("bg") or location.address_i18n.get("en")
        if not address:
            logger.error(f"Location {location.id} has no address in address_i18n")
            result = GeocodingResult(
                success=False,
                error="No address available",
            )
            if force:
                location.lat = None
                location.lng = None
            await apply_geocode_result_to_location(self.db, location, result)
            await self.db.commit()
            return result

        # Fetch school data for GeoJSON matching and locality-aware city hints.
        school_name = None
        fallback_school_name = None
        city = None
        school_result = await self.db.execute(
            select(School).where(School.id == location.school_id)
        )
        school = school_result.scalar_one_or_none()
        if school:
            school_name = school.name_i18n.get("bg") or school.name_i18n.get("en")
            fallback_school_name = school_name
            city = _preferred_geocoding_city(school)

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

        # Special handling for merged kg.sofia branch families and Sofia-oblast
        # schools: use address-first geocoding before name-based GeoJSON lookup.
        # This avoids collapsing distinct branch/town records onto an unrelated
        # school point when the legal name is common across Bulgaria.
        prefer_address_first = (
            school is not None
            and (
                school_name is None
                or (school.attributes or {}).get("moe_region_code") == 23
            )
            and hasattr(self.provider, "nominatim_provider")
            and hasattr(self.provider, "geojson_provider")
        )
        if (
            prefer_address_first
        ):
            result = await self.provider.nominatim_provider.geocode(
                address=address,
                country_code=country_code,
                city=city,
            )
            if not result.success and fallback_school_name:
                geojson_result = await self.provider.geojson_provider.geocode(
                    address=address,
                    country_code=country_code,
                    school_name=fallback_school_name,
                    city=city,
                )
                if geojson_result.success:
                    result = geojson_result

        if result is None:
            result = await self.provider.geocode(
                address,
                country_code=country_code,
                school_name=school_name,
                city=city,
            )

        # Update database if successful
        if result.success and result.lat is not None and result.lng is not None:
            result = await apply_geocode_result_to_location(
                self.db,
                location,
                result,
                school=school,
            )
            # TODO: Consider batching commits for better performance when geocoding many locations
            await self.db.commit()
            if result.success:
                logger.info(f"Updated location {location.id} with coordinates ({result.lat}, {result.lng})")
        else:
            clear_stale_coordinates = force and _is_deterministic_force_failure(result)
            if clear_stale_coordinates:
                location.lat = None
                location.lng = None
            await apply_geocode_result_to_location(
                self.db,
                location,
                result,
                school=school,
            )
            if force and had_coordinates and not clear_stale_coordinates:
                # Preserve the last accepted evidence across transient provider
                # failures, while retaining the failed refresh for diagnostics.
                failed_attempt = dict(location.geocode_meta or {})
                location.geocode_meta = {
                    **previous_geocode_meta,
                    "last_attempt": failed_attempt,
                }
            await self.db.commit()
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

    async def geocode_all_locations(
        self,
        *,
        force: bool = False,
        limit: Optional[int] = None,
        country_code: Optional[str] = None,
        city: Optional[str] = None,
    ) -> dict:
        """Geocode locations in bulk, optionally refreshing existing coordinates.

        Args:
            force: If True, include locations that already have coordinates and
                send every selected location through the provider and write gate.
            limit: Maximum number of locations to geocode.
            country_code: Include only schools in this country when provided.
            city: Include only schools in this city when provided.

        Returns:
            Summary dict with counts.
        """
        query = (
            select(SchoolLocation, School.country_code)
            .join(School, School.id == SchoolLocation.school_id)
            .order_by(SchoolLocation.id)
        )
        if not force:
            query = query.where(
                (SchoolLocation.lat.is_(None)) | (SchoolLocation.lng.is_(None))
            )
        if country_code is not None:
            query = query.where(func.lower(School.country_code) == country_code.strip().lower())
        if city is not None:
            query = query.where(func.lower(School.city) == city.strip().lower())
        if limit is not None:
            query = query.limit(limit)

        result = await self.db.execute(query)
        locations = result.all()

        if not locations:
            logger.info(
                "No school locations require geocoding"
                if not force
                else "No school locations found"
            )
            return {"total": 0, "success": 0, "failed": 0}

        logger.info(
            "Found %s locations to %s",
            len(locations),
            "re-geocode" if force else "geocode",
        )

        success_count = 0
        failed_count = 0
        for location, school_country_code in locations:
            geocode_result = await self.geocode_location(
                location,
                force=force,
                country_code=school_country_code,
            )
            if geocode_result.success:
                success_count += 1
            else:
                failed_count += 1

        logger.info(
            "Geocoding complete: %s success, %s failed out of %s total",
            success_count,
            failed_count,
            len(locations),
        )

        return {
            "total": len(locations),
            "success": success_count,
            "failed": failed_count,
        }

    async def geocode_all_missing(self, limit: Optional[int] = None) -> dict:
        """
        Geocode all school locations that don't have coordinates yet.

        Args:
            limit: Maximum number of locations to geocode (for rate limiting)

        Returns:
            Summary dict with counts
        """
        return await self.geocode_all_locations(force=False, limit=limit)
