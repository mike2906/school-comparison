"""Base adapter for discovery sources (government registries, search engines, etc.)."""
import logging
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_log import ScrapeLog, ScrapeStatus, ScrapeType
from app.schemas.scraping import DiscoveredSchool
from app.services.geocoding.bg.address_match import same_building

logger = logging.getLogger(__name__)


def _address_key(address_i18n: Optional[dict]) -> str:
    """Normalized address used to recognise a location that already exists.

    Punctuation and spacing are ignored: kg.sofia.bg writes one building as
    "ул. Балша, №6-8" in one record and "ул. Балша, № 6-8," in another.
    """
    address = (address_i18n or {}).get("bg") or (address_i18n or {}).get("en") or ""
    return " ".join(re.findall(r"\w+", address.casefold()))


def _is_pinned_location(location, address_i18n: Optional[dict]) -> bool:
    """Whether a source address is this pinned location.

    A hand correction may have replaced the source's address with the real one; the
    address it replaced is kept in ``geocode_meta.manual_fix.previous``.
    """
    known = {_address_key(location.address_i18n)}
    meta = location.geocode_meta if isinstance(location.geocode_meta, dict) else {}
    manual_fix = meta.get("manual_fix")
    previous = manual_fix.get("previous") if isinstance(manual_fix, dict) else None
    if isinstance(previous, dict):
        known.add(_address_key(previous.get("address_i18n")))
    if _address_key(address_i18n) in known:
        return True
    return same_building(
        (location.address_i18n or {}).get("bg") or "", (address_i18n or {}).get("bg") or ""
    )


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
    RATE_LIMIT: str = "2/m"  # Informational only; nothing enforces it (e.g., "2/m" = 2 requests per minute)
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
        import logging

        from sqlalchemy import and_, delete, func, select

        from app.models import School, SchoolLocation, SchoolLocationAgeGroupShift
        from app.services.geocoding.base import GeocodingResult
        from app.services.geocoding.write_gate import apply_geocode_result_to_location, has_pinned_point

        logger = logging.getLogger(__name__)

        created = 0
        updated = 0
        skipped = 0
        touched_school_ids: set[int] = set()

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

                matched_by_source_id = False
                if not existing_school:
                    # Adapter-specific match (e.g. a source record id stored in attributes)
                    source_match_id = self._existing_school_id_for(disc)
                    if source_match_id is not None:
                        existing_school = await self.db.get(School, source_match_id)
                        # Only a school this source created (no registry id) is "owned" by it.
                        matched_by_source_id = (
                            existing_school is not None and not existing_school.institutional_id
                        )

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
                    result = await self.db.execute(
                        select(School)
                        .join(SchoolLocation, School.id == SchoolLocation.school_id)
                        .where(
                            and_(
                                School.country_code == disc.country_code,
                                # as_string() yields the unquoted text on Postgres and
                                # SQLite; cast(..., String) kept the JSON quotes/escapes
                                # and never matched.
                                School.name_i18n[default_lang].as_string() == name_to_match,
                                School.city == disc.city,
                                SchoolLocation.district == district,
                                SchoolLocation.is_primary == True,  # noqa: E712  (SQL expression)
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
                        from app.services.identity_curation import merge_authoritative_name_i18n

                        # Authoritative sources commonly provide only their native
                        # locale. Preserve independently curated canonical locales,
                        # while incoming values still win when supplied.
                        existing_school.name_i18n = merge_authoritative_name_i18n(
                            existing_school,
                            disc.name_i18n,
                        )
                        existing_school.school_type = disc.school_type
                        existing_school.education_level = disc.education_level
                        existing_school.city = disc.city or existing_school.city
                        existing_school.website_url = disc.website_url or existing_school.website_url
                        existing_school.source_url = disc.source_url or existing_school.source_url
                        existing_school.institutional_id = disc.institutional_id or existing_school.institutional_id

                    existing_school.attributes = {**existing_attributes, **incoming_attributes}
                    existing_school.admission_info = {**existing_admission_info, **(disc.admission_info or {})}
                    if not self.ENRICHMENT_ONLY:
                        # Reset to pending for re-scraping. Enrichment adapters only add
                        # their own attributes, which never need a website re-scrape (and a
                        # pending status would withhold the school's published website data).
                        existing_school.scrape_status = "pending"

                    school_id = existing_school.id
                    school_for_gate = existing_school
                    updated += 1
                else:
                    if not self._may_create_school(disc):
                        logger.info(
                            "%s: no existing school for %s; this record type only enriches",
                            self.ADAPTER_NAME,
                            disc.name_i18n,
                        )
                        skipped += 1
                        continue
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
                locations_to_add = list(disc.locations or [])
                keep_existing_locations = False
                if existing_school and self.ENRICHMENT_ONLY:
                    # Enrichment adapters never delete or recreate an existing school's
                    # locations: they may carry corrected coordinates
                    # (geocode_meta.manual_fix) or come from the authoritative registry.
                    existing_locations = (
                        await self.db.execute(
                            select(SchoolLocation).where(SchoolLocation.school_id == school_id)
                        )
                    ).scalars().all()
                    if existing_locations:
                        keep_existing_locations = True
                        if matched_by_source_id:
                            # A school this source already owns keeps its rows (and their
                            # coordinates) but takes the source's contact and age-group data,
                            # and may gain a location (e.g. a new kindergarten building).
                            known = {_address_key(loc.address_i18n): loc for loc in existing_locations}
                            for incoming in locations_to_add:
                                row = known.get(_address_key(incoming.address_i18n))
                                if row is not None:
                                    await self._sync_source_location(row, incoming)
                            locations_to_add = [
                                loc.model_copy(update={"is_primary": False})
                                for loc in locations_to_add
                                if _address_key(loc.address_i18n) not in known
                            ]
                        else:
                            locations_to_add = []
                if locations_to_add:
                    # For simplicity, delete old locations and recreate
                    # (In production, might want smarter diffing, but locations rarely change)
                    if existing_school and not keep_existing_locations:
                        existing_locations = (
                            await self.db.execute(
                                select(SchoolLocation).where(SchoolLocation.school_id == school_id)
                            )
                        ).scalars().all()
                        # Official and hand-corrected points are not recreated: the rows
                        # stay, and a source location that is one of them only refreshes
                        # its source fields.
                        pinned = [loc for loc in existing_locations if has_pinned_point(loc)]
                        location_ids = [loc.id for loc in existing_locations if loc not in pinned]
                        if location_ids:
                            # Remove child shift rows first because raw deletes bypass ORM cascades.
                            await self.db.execute(
                                delete(SchoolLocationAgeGroupShift).where(
                                    SchoolLocationAgeGroupShift.location_id.in_(location_ids)
                                )
                            )
                            await self.db.execute(
                                delete(SchoolLocation).where(SchoolLocation.id.in_(location_ids))
                            )
                        if pinned:
                            new_locations = []
                            unmatched = list(pinned)
                            for incoming in locations_to_add:
                                row = next(
                                    (loc for loc in unmatched if _is_pinned_location(loc, incoming.address_i18n)),
                                    None,
                                )
                                if row is None:
                                    new_locations.append(incoming)
                                    continue
                                # One row is one source location: a second address on the
                                # same street and number is its own building.
                                unmatched.remove(row)
                                # The source's coords_source tag describes its own geocode,
                                # not the pinned point.
                                source_tags = [
                                    tag
                                    for tag in incoming.location_tags or []
                                    if not tag.startswith("coords_source=")
                                ]
                                await self._sync_source_location(
                                    row, incoming.model_copy(update={"location_tags": source_tags})
                                )
                            if any(loc.is_primary for loc in pinned):
                                if new_locations and any(loc.is_primary for loc in unmatched):
                                    logger.warning(
                                        "%s: school %s keeps a pinned primary location whose address "
                                        "the source no longer lists; review it against the new one",
                                        self.ADAPTER_NAME,
                                        school_id,
                                    )
                                new_locations = [
                                    loc.model_copy(update={"is_primary": False}) for loc in new_locations
                                ]
                            locations_to_add = new_locations

                    for loc in locations_to_add:
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

    async def _sync_source_location(self, row, incoming) -> None:
        """Update a source-owned location's source fields; never its coordinates."""
        from sqlalchemy import select

        from app.models import SchoolLocationAgeGroupShift

        row.district = incoming.district or row.district
        row.phone = incoming.phone or row.phone
        if incoming.location_tags:
            # Replace only this source's own tags; other stages keep markers here
            # (e.g. address_source=website_contact, coords_cleared=...).
            source_prefixes = tuple(
                {tag.split("=", 1)[0] + "=" for tag in incoming.location_tags if "=" in tag}
            )
            kept_tags = [
                tag for tag in row.location_tags or [] if not tag.startswith(source_prefixes)
            ]
            row.location_tags = sorted(set(kept_tags) | set(incoming.location_tags))
        shifts = (
            await self.db.execute(
                select(SchoolLocationAgeGroupShift).where(
                    SchoolLocationAgeGroupShift.location_id == row.id
                )
            )
        ).scalars().all()
        wanted = set(incoming.age_groups or [])
        if not wanted:
            return
        # The registry now states these age groups itself: they are no longer only the
        # website's claim, so a later website withhold must not delete them (UF42b).
        from app.scrapers.campus_sync import AGE_GROUP_TAG_PREFIX

        confirmed = {AGE_GROUP_TAG_PREFIX + age_group for age_group in wanted}
        if confirmed & set(row.location_tags or []):
            row.location_tags = [tag for tag in row.location_tags if tag not in confirmed]
        existing = {shift.age_group: shift for shift in shifts}
        for age_group, shift in existing.items():
            if age_group not in wanted:
                await self.db.delete(shift)
                dropped = AGE_GROUP_TAG_PREFIX + age_group
                if dropped in (row.location_tags or []):
                    row.location_tags = [tag for tag in row.location_tags if tag != dropped]
                continue
            # Take values the source states; keep stored ones it does not provide.
            if age_group in incoming.shifts:
                shift.shift = incoming.shifts[age_group]
            if age_group in incoming.has_organised_groups:
                shift.has_organised_groups = incoming.has_organised_groups[age_group]
        for age_group in sorted(wanted - set(existing)):
            self.db.add(
                SchoolLocationAgeGroupShift(
                    location_id=row.id,
                    age_group=age_group,
                    shift=incoming.shifts.get(age_group),
                    has_organised_groups=incoming.has_organised_groups.get(age_group),
                )
            )

    def _existing_school_id_for(self, disc: DiscoveredSchool) -> Optional[int]:
        """School id this record is already linked to by source id (override per adapter)."""
        return None

    def _may_create_school(self, disc: DiscoveredSchool) -> bool:
        """Whether an unmatched discovered record may create a new school (override to restrict)."""
        return True

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
