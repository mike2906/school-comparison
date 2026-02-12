"""Base adapter for discovery sources (government registries, search engines, etc.)."""
from abc import ABC, abstractmethod
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.scraping import DiscoveredSchool


class BaseSourceAdapter(ABC):
    """
    Abstract base class for discovery source adapters.

    Each adapter is responsible for discovering schools from a specific source
    (e.g., kg.sofia.bg, MoE registry, web search) and returning structured data.

    Adapters are locale-specific (e.g., KgSofiaBgAdapter for Sofia kindergartens),
    but share common patterns defined here.
    """

    # Adapter metadata (override in subclasses)
    ADAPTER_NAME: str = "base"
    COUNTRY_CODE: str = "bg"
    CITY: Optional[str] = None  # e.g., "sofia" - None means country-wide
    DESCRIPTION: str = "Base adapter (override in subclass)"
    RATE_LIMIT: str = "2/m"  # Celery rate limit (e.g., "2/m" = 2 requests per minute)

    def __init__(self, db: AsyncSession):
        """
        Initialize the adapter.

        Args:
            db: AsyncSession for database operations (creating/updating schools)
        """
        self.db = db

    @abstractmethod
    async def discover(self, limit: Optional[int] = None, sample_ratio: float = 0.0) -> list[DiscoveredSchool]:
        """
        Discover schools from this source.

        This is the main entry point for each adapter. It should:
        1. Fetch data from the source (HTTP requests, API calls, etc.)
        2. Parse the data (HTML, JSON, etc.)
        3. Return a list of DiscoveredSchool objects

        Args:
            limit: Optional limit on number of schools to discover (for testing)
            sample_ratio: Optional ratio (0.0-1.0) of unchanged schools to sample for details

        Returns:
            List of DiscoveredSchool objects

        Raises:
            httpx.HTTPError: If the source is unreachable
            ValueError: If the data format is invalid
        """
        pass

    async def upsert_schools(self, discovered_schools: list[DiscoveredSchool]) -> dict[str, int]:
        """
        Create or update schools in the database based on discovered data.

        Uses tiered matching strategy:
        1. Primary key: institutional_id (when available)
        2. Fallback: (country_code, name_i18n[default_language], city, primary_location.district)

        Args:
            discovered_schools: List of DiscoveredSchool objects from discover()

        Returns:
            Dict with counts: {"created": N, "updated": M, "skipped": K}
        """
        from app.models import School, SchoolLocation, SchoolLocationAgeGroupShift
        from sqlalchemy import select, and_
        from sqlalchemy.dialects.postgresql import insert
        import logging

        logger = logging.getLogger(__name__)

        created = 0
        updated = 0
        skipped = 0

        for disc in discovered_schools:
            try:
                # Step 1: Try to find existing school
                existing_school = None

                if disc.institutional_id:
                    # Primary match: institutional_id
                    result = await self.db.execute(
                        select(School).where(
                            and_(
                                School.country_code == disc.country_code,
                                School.institutional_id == disc.institutional_id,
                            )
                        )
                    )
                    existing_school = result.scalar_one_or_none()

                if not existing_school:
                    # Fallback match: name + city + district
                    # Get default language name for matching
                    default_lang = disc.country_code  # Assume country code is default language
                    name_to_match = disc.name_i18n.get(default_lang) or disc.name_i18n.get("bg")

                    if not name_to_match:
                        logger.warning(f"No name found for matching: {disc.name_i18n}")
                        skipped += 1
                        continue

                    # Get district from primary location
                    primary_location = next((loc for loc in disc.locations if loc.is_primary), None)
                    district = primary_location.district if primary_location else None

                    # Try to find by name, city, and district
                    # Use JSON operations to match name in name_i18n
                    from sqlalchemy import cast, String, func

                    result = await self.db.execute(
                        select(School)
                        .join(SchoolLocation, School.id == SchoolLocation.school_id)
                        .where(
                            and_(
                                School.country_code == disc.country_code,
                                cast(School.name_i18n[default_lang], String) == name_to_match,
                                School.city == disc.city,
                                SchoolLocation.district == district,
                                SchoolLocation.is_primary == True,
                            )
                        )
                    )
                    existing_school = result.scalar_one_or_none()

                    if not existing_school and not disc.institutional_id:
                        logger.warning(
                            f"Fallback match for '{name_to_match}' (no institutional_id) - duplicate risk"
                        )

                # Step 2: Create or update school
                if existing_school:
                    # Update existing
                    existing_school.name_i18n = disc.name_i18n
                    existing_school.school_type = disc.school_type
                    existing_school.education_level = disc.education_level
                    existing_school.city = disc.city
                    existing_school.website_url = disc.website_url or existing_school.website_url
                    existing_school.source_url = disc.source_url or existing_school.source_url
                    existing_school.institutional_id = disc.institutional_id or existing_school.institutional_id
                    existing_school.attributes = {**existing_school.attributes, **disc.attributes}
                    existing_school.admission_info = {**existing_school.admission_info, **disc.admission_info}
                    existing_school.scrape_status = "pending"  # Reset to pending for re-scraping

                    school_id = existing_school.id
                    updated += 1
                else:
                    # Create new
                    new_school = School(
                        country_code=disc.country_code,
                        name_i18n=disc.name_i18n,
                        school_type=disc.school_type,
                        education_level=disc.education_level,
                        city=disc.city,
                        website_url=disc.website_url,
                        source_url=disc.source_url,
                        institutional_id=disc.institutional_id,
                        attributes=disc.attributes,
                        admission_info=disc.admission_info,
                        scrape_status="pending",
                    )
                    self.db.add(new_school)
                    await self.db.flush()
                    school_id = new_school.id
                    created += 1

                # Step 3: Create/update locations
                # If no locations were discovered, keep existing locations intact to avoid wiping data.
                if disc.locations:
                    # For simplicity, delete old locations and recreate
                    # (In production, might want smarter diffing, but locations rarely change)
                    if existing_school:
                        await self.db.execute(
                            SchoolLocation.__table__.delete().where(SchoolLocation.school_id == school_id)
                        )

                    for loc in disc.locations:
                        new_location = SchoolLocation(
                            school_id=school_id,
                            address_i18n=loc.address_i18n,
                            district=loc.district,
                            lat=loc.lat,
                            lng=loc.lng,
                            phone=loc.phone,
                            is_primary=loc.is_primary,
                        )
                        self.db.add(new_location)
                        await self.db.flush()

                        # Add age group shifts
                        for age_group in loc.age_groups:
                            shift = SchoolLocationAgeGroupShift(
                                location_id=new_location.id,
                                age_group=age_group,
                                shift=loc.shifts.get(age_group),
                                has_organised_groups=loc.has_organised_groups.get(age_group),
                            )
                            self.db.add(shift)

            except Exception as e:
                logger.error(f"Error upserting school {disc.name_i18n}: {e}")
                skipped += 1
                continue

        await self.db.commit()

        logger.info(f"Discovery complete: created={created}, updated={updated}, skipped={skipped}")
        return {"created": created, "updated": updated, "skipped": skipped}

    async def run(self, limit: Optional[int] = None, sample_ratio: float = 0.0) -> dict[str, int]:
        """
        Run the full discovery process: discover + upsert.

        This is a convenience method that combines discover() and upsert_schools().

        Args:
            limit: Optional limit on number of schools to discover
            sample_ratio: Optional ratio (0.0-1.0) of unchanged schools to sample for details

        Returns:
            Dict with counts: {"created": N, "updated": M, "skipped": K}
        """
        discovered = await self.discover(limit=limit, sample_ratio=sample_ratio)
        return await self.upsert_schools(discovered)
