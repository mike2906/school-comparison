"""Base adapter for discovery sources (government registries, search engines, etc.)."""
from abc import ABC, abstractmethod
from typing import Optional
from datetime import datetime
import uuid
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.scraping import DiscoveredSchool
from app.models.scrape_log import ScrapeLog, ScrapeType, ScrapeStatus

logger = logging.getLogger(__name__)


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
    SCRAPE_TYPE: Optional[ScrapeType] = None  # Override to set explicit scrape type (REGISTRY or DISCOVERY)
    # If True, existing records are treated as authoritative for core identity/classification
    # fields and this adapter only enriches supplemental fields.
    ENRICHMENT_ONLY: bool = False
    # Optional prefixes that incoming attribute keys must match in ENRICHMENT_ONLY mode.
    # Non-matching keys are ignored to avoid clobbering authoritative source metadata.
    ENRICHMENT_ATTRIBUTE_PREFIXES: tuple[str, ...] = ()

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
        from app.services.geocoding.base import GeocodingResult
        from app.services.geocoding.write_gate import apply_geocode_result_to_location
        from sqlalchemy import select, and_, func, delete
        import logging

        logger = logging.getLogger(__name__)

        created = 0
        updated = 0
        skipped = 0
        touched_school_ids: set[int] = set()

        for disc in discovered_schools:
            try:
                self._ensure_i18n_fallbacks(disc)

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
                    from sqlalchemy import cast, String

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
                    incoming_attributes = self._filter_incoming_attributes(disc.attributes or {})
                    existing_attributes = existing_school.attributes or {}
                    existing_admission_info = existing_school.admission_info or {}

                    # Update existing
                    if self.ENRICHMENT_ONLY:
                        # Enrichment adapters (e.g., kg.sofia) must not overwrite core
                        # identity/classification fields from authoritative registries.
                        existing_school.website_url = existing_school.website_url or disc.website_url
                        existing_school.source_url = existing_school.source_url or disc.source_url
                        existing_school.city = existing_school.city or disc.city
                        if disc.institutional_id and not existing_school.institutional_id:
                            existing_school.institutional_id = disc.institutional_id
                    else:
                        existing_school.name_i18n = disc.name_i18n
                        existing_school.school_type = disc.school_type
                        existing_school.education_level = disc.education_level
                        existing_school.city = disc.city
                        existing_school.website_url = disc.website_url or existing_school.website_url
                        existing_school.source_url = disc.source_url or existing_school.source_url
                        existing_school.institutional_id = disc.institutional_id or existing_school.institutional_id

                    existing_school.attributes = {**existing_attributes, **incoming_attributes}
                    existing_school.admission_info = {**existing_admission_info, **(disc.admission_info or {})}
                    existing_school.scrape_status = "pending"  # Reset to pending for re-scraping

                    school_id = existing_school.id
                    school_for_gate = existing_school
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
                        attributes=disc.attributes or {},
                        admission_info=disc.admission_info or {},
                        scrape_status="pending",
                    )
                    self.db.add(new_school)
                    await self.db.flush()
                    school_id = new_school.id
                    school_for_gate = new_school
                    created += 1
                touched_school_ids.add(school_id)

                # Step 3: Create/update locations
                # If no locations were discovered, keep existing locations intact to avoid wiping data.
                if disc.locations:
                    # For simplicity, delete old locations and recreate
                    # (In production, might want smarter diffing, but locations rarely change)
                    if existing_school:
                        # Remove child shift rows first because raw deletes bypass ORM cascades.
                        location_ids_result = await self.db.execute(
                            select(SchoolLocation.id).where(SchoolLocation.school_id == school_id)
                        )
                        location_ids = [row[0] for row in location_ids_result.all()]
                        if location_ids:
                            await self.db.execute(
                                delete(SchoolLocationAgeGroupShift).where(
                                    SchoolLocationAgeGroupShift.location_id.in_(location_ids)
                                )
                            )
                        await self.db.execute(
                            delete(SchoolLocation).where(SchoolLocation.school_id == school_id)
                        )

                    for loc in disc.locations:
                        new_location = SchoolLocation(
                            school_id=school_id,
                            address_i18n=loc.address_i18n,
                            district=loc.district,
                            lat=loc.lat,
                            lng=loc.lng,
                            phone=loc.phone,
                            location_tags=loc.location_tags,
                            geocode_meta=loc.geocode_meta,
                            is_primary=loc.is_primary,
                        )
                        self.db.add(new_location)
                        await self.db.flush()
                        if new_location.lat is not None and new_location.lng is not None:
                            meta = new_location.geocode_meta or {}
                            await apply_geocode_result_to_location(
                                self.db,
                                new_location,
                                GeocodingResult(
                                    success=True,
                                    lat=new_location.lat,
                                    lng=new_location.lng,
                                    provider=meta.get("provider", ""),
                                    formatted_address=meta.get("formatted_address"),
                                    method=meta.get("method"),
                                    precision=meta.get("precision"),
                                ),
                                school=school_for_gate,
                            )

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

        if touched_school_ids:
            missing_locations_result = await self.db.execute(
                select(School.id)
                .outerjoin(SchoolLocation, School.id == SchoolLocation.school_id)
                .where(School.id.in_(touched_school_ids))
                .group_by(School.id)
                .having(func.count(SchoolLocation.id) == 0)
            )
            missing_ids = [row[0] for row in missing_locations_result.all()]
            for school_id in missing_ids:
                school = await self.db.get(School, school_id)
                name_i18n = school.name_i18n if school else None
                school_name = None
                if isinstance(name_i18n, dict):
                    school_name = name_i18n.get("bg") or name_i18n.get("en")
                if not school_name:
                    school_name = str(name_i18n)
                logger.warning(
                    "Data quality: school has no locations after upsert (id=%s, name=%s, adapter=%s)",
                    school_id,
                    school_name,
                    self.ADAPTER_NAME,
                )

        await self.db.commit()

        logger.info(f"Discovery complete: created={created}, updated={updated}, skipped={skipped}")
        return {"created": created, "updated": updated, "skipped": skipped}

    def _ensure_i18n_fallbacks(self, disc: DiscoveredSchool) -> None:
        """Keep discovery data source-backed; synthetic EN fallbacks are added at display time only."""
        return None

    def _filter_incoming_attributes(self, incoming: dict) -> dict:
        """Filter incoming attributes according to adapter merge policy."""
        if not incoming:
            return {}
        if not self.ENRICHMENT_ONLY:
            return incoming
        if not self.ENRICHMENT_ATTRIBUTE_PREFIXES:
            return incoming
        return {
            key: value
            for key, value in incoming.items()
            if key.startswith(self.ENRICHMENT_ATTRIBUTE_PREFIXES)
        }

    async def run(self, limit: Optional[int] = None, sample_ratio: float = 0.0) -> dict[str, int]:
        """
        Run the full discovery process: discover + upsert.

        This is a convenience method that combines discover() and upsert_schools().
        It also logs the scrape run to scrape_log for observability.

        Args:
            limit: Optional limit on number of schools to discover
            sample_ratio: Optional ratio (0.0-1.0) of unchanged schools to sample for details

        Returns:
            Dict with counts: {"created": N, "updated": M, "skipped": K}
        """
        run_id = str(uuid.uuid4())
        start_time = datetime.now()
        source_url = getattr(self, 'API_BASE_URL', None) or getattr(self, 'KINDERGARTENS_URL', None)

        # Determine scrape type from class attribute or fallback to heuristic
        if self.SCRAPE_TYPE is not None:
            scrape_type = self.SCRAPE_TYPE
        else:
            # Fallback: heuristic based on adapter name
            # Registry adapters (moe_registry) use REGISTRY, others use DISCOVERY
            scrape_type = ScrapeType.REGISTRY if 'registry' in self.ADAPTER_NAME.lower() else ScrapeType.DISCOVERY

        try:
            logger.info(f"Starting {self.ADAPTER_NAME} scrape (run_id={run_id}, limit={limit})")

            # Run discovery and upsert
            discovered = await self.discover(limit=limit, sample_ratio=sample_ratio)
            result = await self.upsert_schools(discovered)

            # Calculate duration
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Log success
            log = ScrapeLog(
                scrape_type=scrape_type,
                status=ScrapeStatus.SUCCESS,
                source_url=source_url,
                run_id=run_id,
                duration_ms=duration_ms,
                scraped_at=datetime.utcnow()
            )
            self.db.add(log)
            await self.db.commit()

            logger.info(f"Completed {self.ADAPTER_NAME} scrape in {duration_ms}ms: {result}")

            return result

        except Exception as e:
            # Calculate duration even on failure
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            # Rollback any failed transaction before logging
            await self.db.rollback()

            # Log failure
            log = ScrapeLog(
                scrape_type=scrape_type,
                status=ScrapeStatus.FAILED,
                source_url=source_url,
                error_message=str(e),
                run_id=run_id,
                duration_ms=duration_ms,
                scraped_at=datetime.utcnow()
            )
            self.db.add(log)
            await self.db.commit()

            logger.error(f"Failed {self.ADAPTER_NAME} scrape after {duration_ms}ms: {e}")
            raise
