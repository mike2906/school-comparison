"""Main geocoding service that coordinates geocoding operations."""
import logging
import math
from typing import Optional

from sqlalchemy import String, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import School, SchoolLocation
from app.services.geocoding.base import BaseGeocodingProvider, GeocodingResult
from app.services.geocoding.bg.address_match import same_building
from app.services.geocoding.composite import CompositeGeocodingProvider
from app.services.geocoding.nominatim import (
    AREA_LEVEL_MATCH_ERROR,
    AREA_MISMATCH_ERROR,
    OTHER_INSTITUTION_ERROR,
    NominatimProvider,
)
from app.services.geocoding.write_gate import (
    OFFICIAL_COORDS_TAG,
    apply_geocode_result_to_location,
    location_holding_point,
)

logger = logging.getLogger(__name__)

# A GeoJSON name match for a school with several locations whose register address is not
# this location's. Not terminal: the address tiers may still find the location.
NAME_MATCH_ADDRESS_MISMATCH = "geojson_name_match_address_mismatch"
NAME_MATCH_POINT_TAKEN = "duplicate_geojson_name_match_different_address"
# The location's stored address and the institution's register address name different
# buildings: a point found from the stored address is not written (the location is unchanged).
REGISTER_ADDRESS_CONFLICT = "register_address_conflict"
# A sibling location this close to a name-match point is taken to sit on it.
SIBLING_POINT_RADIUS_M = 50.0

# Failures that routine runs do not retry. Deliberately absent: NAME_MATCH_POINT_TAKEN, which
# only says the GeoJSON tier's point belongs to another location, not that the address
# tiers cannot find this one (UF44).
TERMINAL_GEOCODE_FAILURE_REASONS = frozenset({
    "No address available",
    "No results found",
    "outside_sofia_write_bounds",
    "duplicate_approximate_match_different_address",
    AREA_LEVEL_MATCH_ERROR,
    AREA_MISMATCH_ERROR,
    OTHER_INSTITUTION_ERROR,
})


# Unpinned locations that still list (without a map pin): terminal failures plus a name
# match that was set aside, which a later run may still resolve.
LISTABLE_UNPINNED_REASONS = TERMINAL_GEOCODE_FAILURE_REASONS | {
    NAME_MATCH_POINT_TAKEN,
    NAME_MATCH_ADDRESS_MISMATCH,
}


def nominatim_user_agent(settings) -> str:
    """Build the Nominatim User-Agent, refusing a missing or placeholder contact email."""
    contact_email = settings.geocoding_contact_email
    if not contact_email or "example.com" in contact_email.lower():
        raise ValueError(
            "GEOCODING_CONTACT_EMAIL must be set to a valid email in .env file. "
            "Nominatim requires a real contact email in the User-Agent header. "
            f"Current value: {contact_email}"
        )
    return f"SofiaSchoolComparison/1.0 ({contact_email})"


def _is_deterministic_force_failure(result: GeocodingResult) -> bool:
    """Return whether a failed refresh proves the stored point is unsupported."""
    return result.error in TERMINAL_GEOCODE_FAILURE_REASONS


def geocode_failure_is_terminal(meta: object) -> bool:
    """Return whether a null coordinate has durable failure evidence.

    Routine runs leave these accepted terminal states alone. An operator may still
    deliberately retry one with ``force=True`` after the underlying data changes.
    """
    if not isinstance(meta, dict):
        return False
    reason = meta.get("rejection_reason")
    return (
        meta.get("status") in {"failed", "rejected"}
        and isinstance(meta.get("provider"), str)
        and bool(meta["provider"].strip())
        and isinstance(reason, str)
        and reason.strip() in TERMINAL_GEOCODE_FAILURE_REASONS
    )


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    dy = (lat2 - lat1) * 111_320
    dx = (lng2 - lng1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def _location_address(location: SchoolLocation) -> str:
    address_i18n = location.address_i18n or {}
    return address_i18n.get("bg") or address_i18n.get("en") or ""


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
            user_agent = nominatim_user_agent(self.settings)

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
        # Official points (Sofia Municipality) are never replaced by a geocoder guess.
        has_official_point = OFFICIAL_COORDS_TAG in (location.location_tags or [])
        # Check if already geocoded (unless force=True)
        if (
            (not force or has_official_point)
            and location.lat is not None
            and location.lng is not None
        ):
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

        if not force and geocode_failure_is_terminal(location.geocode_meta):
            meta = location.geocode_meta or {}
            logger.debug(
                "Location %s has terminal geocode failure evidence; skipping automatic retry",
                location.id,
            )
            return GeocodingResult(
                success=False,
                error=meta["rejection_reason"],
                provider=meta["provider"],
                method=meta.get("method"),
                precision=meta.get("precision"),
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
                provider="local_validation",
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
        result = await self._official_point_at_same_address(location, school, address, country_code)
        if result is not None and await self._register_address_conflict(
            location, address, fallback_school_name, city, country_code
        ):
            # The stored address may be out of date; neither move nor create a pin from it.
            logger.warning(
                "Location %s: stored address disagrees with the register; pin left as is",
                location.id,
            )
            return GeocodingResult(
                success=False,
                error=REGISTER_ADDRESS_CONFLICT,
                provider=result.provider,
                method=result.method,
            )

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
        if result is None and prefer_address_first:
            result = await self.provider.nominatim_provider.geocode(
                address=address,
                country_code=country_code,
                city=city,
                district=location.district,
                institution_name=fallback_school_name,
            )
            if not result.success and fallback_school_name:
                geojson_result = await self.provider.geojson_provider.geocode(
                    address=address,
                    country_code=country_code,
                    school_name=fallback_school_name,
                    city=city,
                )
                if geojson_result.success and not await self._name_match_conflict(
                    location, address, geojson_result
                ):
                    result = geojson_result

        if result is None:
            result = await self.provider.geocode(
                address,
                country_code=country_code,
                school_name=school_name,
                city=city,
                district=location.district,
                institution_name=fallback_school_name,
            )
            conflict = await self._name_match_conflict(location, address, result)
            if conflict:
                nominatim = getattr(self.provider, "nominatim_provider", None)
                if nominatim is not None:
                    # The name match is another building's point; try the address itself.
                    result = await nominatim.geocode(
                        address=address,
                        country_code=country_code,
                        city=city,
                        district=location.district,
                        institution_name=fallback_school_name,
                    )
                    if result.success:
                        result.method = "nominatim_fallback"
                else:
                    result = GeocodingResult(
                        success=False,
                        error=conflict,
                        provider=result.provider,
                        formatted_address=result.formatted_address,
                        method=result.method,
                        precision=result.precision,
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

    async def _official_point_at_same_address(
        self,
        location: SchoolLocation,
        school: School | None,
        address: str,
        country_code: str,
    ) -> Optional[GeocodingResult]:
        """The official point of a building this location shares, if exactly one.

        Schools often rent space in another institution's building ("… на ПГЕХ"). Its
        official point is reused only when the addresses give the same street and house
        number (not on name similarity), and every such official point is the same one.
        """
        if school is None or country_code != "bg":
            return None
        rows = await self.db.execute(
            select(SchoolLocation)
            .join(School, School.id == SchoolLocation.school_id)
            .where(
                SchoolLocation.id != location.id,
                SchoolLocation.lat.is_not(None),
                SchoolLocation.lng.is_not(None),
                School.country_code == school.country_code,
                School.city == school.city,
                cast(SchoolLocation.location_tags, String).like(f"%{OFFICIAL_COORDS_TAG}%"),
            )
        )
        hosts = [
            host for host in rows.scalars()
            if OFFICIAL_COORDS_TAG in (host.location_tags or [])
            and same_building(address, _location_address(host))
        ]
        if len({(host.lat, host.lng) for host in hosts}) != 1:
            return None
        host = hosts[0]
        logger.info("Location %s shares the building of official point %s", location.id, host.id)
        return GeocodingResult(
            lat=host.lat,
            lng=host.lng,
            success=True,
            provider="official_point",
            formatted_address=_location_address(host),
            method="official_point_same_address",
            precision="exact",
        )

    async def _register_address_conflict(
        self,
        location: SchoolLocation,
        address: str,
        school_name: Optional[str],
        city: Optional[str],
        country_code: str,
    ) -> bool:
        """Whether the register gives the institution an address none of its locations has.

        Then the stored address may be out of date (location 1174: stored Стара планина 13,
        register Раковски 20), and a point found from it is not trusted. No register record,
        or a register address matching this or a sibling location, is no conflict.
        """
        geojson = getattr(self.provider, "geojson_provider", None)
        if geojson is None or not school_name:
            return False
        record = await geojson.geocode(
            address=address, country_code=country_code, school_name=school_name, city=city
        )
        register_address = record.formatted_address if record.success else None
        if not isinstance(register_address, str) or not register_address:
            return False
        siblings = (await self.db.execute(
            select(SchoolLocation).where(SchoolLocation.school_id == location.school_id)
        )).scalars()
        addresses = {address} | {_location_address(sibling) for sibling in siblings}
        return not any(
            same_building(known, register_address, allow_unnumbered=True) for known in addresses
        )

    async def _name_match_conflict(
        self,
        location: SchoolLocation,
        address: str,
        result: GeocodingResult,
    ) -> Optional[str]:
        """Why a GeoJSON name match's point is not this location's, or None if it may be.

        A name identifies the institution, not the building: every location of a school
        with several would get the register's one point (ДГ №149's second building, UF41).
        So for such a school the register address must be this location's address, and no
        sibling may already sit near the point. For a school with one location the register
        point stands (its address is often the newer one: the municipality agrees with it
        for several state schools whose stored address is out of date). Nobody else may
        already hold the exact point at a different address.
        """
        if not result.success or result.method != "geojson_name_match":
            return None
        if result.lat is None or result.lng is None:
            return None
        if await location_holding_point(self.db, location, result.lat, result.lng):
            return NAME_MATCH_POINT_TAKEN
        siblings = [
            sibling for sibling in (await self.db.execute(
                select(SchoolLocation).where(
                    SchoolLocation.school_id == location.school_id,
                    SchoolLocation.id != location.id,
                )
            )).scalars()
            # Duplicate rows of this address are the same building, not a sibling.
            if not same_building(address, _location_address(sibling), allow_unnumbered=True)
        ]
        if not siblings:
            return None
        if not same_building(address, result.formatted_address or "", allow_unnumbered=True):
            return NAME_MATCH_ADDRESS_MISMATCH
        for sibling in siblings:
            if (
                sibling.lat is not None
                and sibling.lng is not None
                and _distance_m(sibling.lat, sibling.lng, result.lat, result.lng) <= SIBLING_POINT_RADIUS_M
            ):
                return NAME_MATCH_POINT_TAKEN
        return None

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
        if limit is not None and force:
            query = query.limit(limit)

        result = await self.db.execute(query)
        locations = result.all()
        if not force:
            locations = [
                row
                for row in locations
                if not geocode_failure_is_terminal(row[0].geocode_meta)
            ]
            if limit is not None:
                locations = locations[:limit]

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


async def same_school_shared_points(
    db: AsyncSession,
    *,
    country_code: Optional[str] = None,
    city: Optional[str] = None,
    radius_m: float = SIBLING_POINT_RADIUS_M,
) -> list[tuple[SchoolLocation, SchoolLocation, float]]:
    """Pairs of one school's pinned locations on (nearly) one point at different addresses.

    One of each pair almost certainly has the other building's point (the failure the
    name-match guard prevents). Duplicate rows of one address and pairs of official
    building points are not reported.
    """
    query = (
        select(SchoolLocation)
        .join(School, School.id == SchoolLocation.school_id)
        .where(SchoolLocation.lat.is_not(None), SchoolLocation.lng.is_not(None))
        .order_by(SchoolLocation.school_id, SchoolLocation.id)
    )
    if country_code is not None:
        query = query.where(func.lower(School.country_code) == country_code.strip().lower())
    if city is not None:
        query = query.where(func.lower(School.city) == city.strip().lower())
    by_school: dict[int, list[SchoolLocation]] = {}
    for location in (await db.execute(query)).scalars():
        by_school.setdefault(location.school_id, []).append(location)
    pairs = []
    for locations in by_school.values():
        for i, first in enumerate(locations):
            for second in locations[i + 1:]:
                if all(OFFICIAL_COORDS_TAG in (loc.location_tags or []) for loc in (first, second)):
                    continue  # two official building points (across a street) are both right
                distance = _distance_m(first.lat, first.lng, second.lat, second.lng)
                if distance <= radius_m and not same_building(
                    _location_address(first), _location_address(second), allow_unnumbered=True
                ):
                    pairs.append((first, second, distance))
    return pairs
