"""
KgSofiaBgAdapter - Discover kindergartens and schools from kg.sofia.bg

This adapter uses the Sofia Municipality's API to discover kindergartens and schools
with preparatory groups, including addresses and contact information.

For detailed API documentation, see:
backend/docs/KG_SOFIA_API_DOCUMENTATION.md
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional
import httpx

from app.scrapers.sources.base_adapter import BaseSourceAdapter
from app.schemas.scraping import DiscoveredSchool, DiscoveredLocation
from app.scrapers.sources import register_adapter
from app.scrapers.base import BaseScraper
from app.models.scrape_log import ScrapeType

logger = logging.getLogger(__name__)


@register_adapter
class KgSofiaBgAdapter(BaseSourceAdapter):
    """
    Discover kindergartens and schools with preparatory groups from kg.sofia.bg.

    The Sofia municipality maintains a public API with all municipal kindergartens
    and schools, including addresses, phone numbers, and district information.

    Data extracted:
    - School/kindergarten name (Bulgarian)
    - Full address string
    - District/region name
    - Phone numbers
    - ESRI GIS ID

    Note: This API does NOT provide institutional IDs - must match with MoE data by name.
    """

    # Adapter metadata
    ADAPTER_NAME = "kg_sofia_bg"
    COUNTRY_CODE = "bg"
    CITY = "sofia"
    DESCRIPTION = "Sofia kindergartens and schools from kg.sofia.bg API"
    RATE_LIMIT = "10/m"
    SCRAPE_TYPE = ScrapeType.REGISTRY
    ENRICHMENT_ONLY = True
    ENRICHMENT_ATTRIBUTE_PREFIXES = ("kg_sofia_",)

    # kg.sofia.bg API endpoints
    API_BASE_URL = "https://kg.sofia.bg/api/public"
    KINDERGARTENS_URL = f"{API_BASE_URL}/kg/type/kinderGarden/all"
    SCHOOLS_URL = f"{API_BASE_URL}/kg/type/school/all"
    PREPARATORY_URL = f"{API_BASE_URL}/kg/type/preparative/all"
    REGIONS_URL = f"{API_BASE_URL}/regions/all"
    REGISTRY_SOURCE_PREFIX = "kg://"
    BUILDING_SUFFIX_RE = re.compile(r"^(?P<base>.+?)\s*-\s*сграда\b.*$", re.IGNORECASE)

    # District name normalization (same as before)
    DISTRICT_MAPPING = {
        "студентски": "Студентски град",
        "изгрев": "Изгрев",
        "лозенец": "Лозенец",
        "средец": "Средец",
        "оборище": "Оборище",
        "триадица": "Триадица",
        "красно село": "Красно село",
        "витоша": "Витоша",
        "овча купел": "Овча купел",
        "люлин": "Люлин",
        "възраждане": "Възраждане",
        "илинден": "Илинден",
        "надежда": "Надежда",
        "искър": "Искър",
        "подуяне": "Подуяне",
        "слатина": "Слатина",
        "сердика": "Сердика",
        "връбница": "Връбница",
        "красна поляна": "Красна поляна",
        "младост": "Младост",
        "панчарево": "Панчарево",
        "нови искър": "Нови Искър",
        "банкя": "Банкя",
        "кремиковци": "Кремиковци",
    }

    # Age group mapping from publicType (for kindergartens)
    AGE_GROUP_MAPPING = {
        'ДГ (с яслени групи)': ['nursery', 'first', 'second', 'third', 'preschool'],
        'ДГ': ['first', 'second', 'third', 'preschool'],
        'СДЯ': ['nursery'],  # Самостоятелна детска ясла - Nursery only
        'ДГ (с логопедични групи)': ['first', 'second', 'third', 'preschool'],
    }

    # School type age group mapping (for multi-grade schools)
    SCHOOL_TYPE_AGE_GROUPS = {
        'НУ': ['grade_1_4'],  # Начално училище (grades 1-4)
        'ОУ': ['grade_1_4', 'grade_5_7'],  # Основно училище (grades 1-8; no dedicated grade-8 bucket)
        'ОбУ': ['grade_1_4', 'grade_5_7', 'grade_8_12'],  # Обединено училище (grades 1-12)
        'СУ': ['grade_1_4', 'grade_5_7', 'grade_8_12'],  # Средно училище (grades 1-12)
        'СЕУ': ['grade_1_4', 'grade_5_7', 'grade_8_12'],  # Средно езиково училище (grades 1-12)
        'ПГ': ['grade_8_12'],  # Профилирана гимназия (grades 8-12)
    }

    # Institution type mapping (from publicType field)
    TYPE_MAPPING = {
        "ДГ": "kindergarten",
        "ДГ (с яслени групи)": "kindergarten",
        "СДЯ": "kindergarten",  # Самостоятелна детска ясла (Independent Nursery) - maps to kindergarten level
        "СУ": "upper_secondary",
        "СЕУ": "upper_secondary",  # Средно езиково училище
        "ОУ": "lower_secondary",
        "ОбУ": "upper_secondary",  # Обединено училище (grades 1-12)
        "НУ": "primary",
        "ПГ": "upper_secondary",
    }

    async def discover(self, limit: Optional[int] = None, sample_ratio: float = 0.0) -> list[DiscoveredSchool]:
        """
        Discover kindergartens and schools from kg.sofia.bg API.

        Process:
        1. Fetch kindergartens from /kg/type/kinderGarden/all
        2. Fetch schools from /kg/type/school/all
        3. Parse addresses, phone numbers, districts
        4. Return DiscoveredSchool objects

        Note: The API returns all data in a single request (no pagination).

        Args:
            limit: Optional limit on number of schools to discover (for testing)
            sample_ratio: Optional ratio (0.0-1.0) of unchanged schools to sample for details (unused for kg.sofia.bg)

        Returns:
            List of DiscoveredSchool objects

        Raises:
            httpx.HTTPError: If the API is unreachable
            ValueError: If the response format is unexpected
        """
        from sqlalchemy import select
        from app.models import School, SourcePage, ScrapeType

        parsed_records: list[tuple[DiscoveredSchool, bool]] = []
        seen_kg_ids: set[str] = set()
        now = datetime.now(timezone.utc)

        # Preload registry pages for quick lookup
        pages_result = await self.db.execute(
            select(SourcePage).where(
                SourcePage.scrape_type == ScrapeType.REGISTRY,
                SourcePage.source_url.like(f"{self.REGISTRY_SOURCE_PREFIX}%"),
            )
        )
        existing_pages = {page.source_url: page for page in pages_result.scalars().all()}

        # Preload kg_sofia_id -> school_id map
        schools_result = await self.db.execute(
            select(School.id, School.attributes).where(
                School.country_code == "bg",
                School.city == "sofia",
            )
        )
        school_id_by_kg_id: dict[str, int] = {}
        for school_id, attrs in schools_result.all():
            if not attrs:
                continue
            kg_ids = attrs.get("kg_sofia_ids")
            if isinstance(kg_ids, list):
                for grouped_kg_id in kg_ids:
                    if grouped_kg_id is not None:
                        school_id_by_kg_id[str(grouped_kg_id)] = school_id
            kg_id = attrs.get("kg_sofia_id")
            if kg_id is not None:
                school_id_by_kg_id[str(kg_id)] = school_id
        self._school_id_by_kg_id = school_id_by_kg_id

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # Fetch kindergartens
            logger.info(f"Fetching kindergartens from {self.KINDERGARTENS_URL}")
            kg_response = await client.get(
                self.KINDERGARTENS_URL,
                params={
                    "filterType": "by_region",
                    "kgType": 0,  # All types
                    "regionId": 0,  # All regions
                },
            )
            kg_response.raise_for_status()
            kg_data = kg_response.json()

            # Extract kindergartens
            kindergartens = kg_data.get("items", {}).get("kinderGardens", [])
            logger.info(f"Found {len(kindergartens)} kindergartens")

            # Parse kindergartens
            for kg in kindergartens:
                school = await self._process_registry_record(
                    kg,
                    record_type="kinderGarden",
                    default_type="kindergarten",
                    existing_pages=existing_pages,
                    school_id_by_kg_id=school_id_by_kg_id,
                    seen_kg_ids=seen_kg_ids,
                    seen_at=now,
                )
                if school:
                    parsed_records.append(school)

            # Fetch schools with preparatory groups
            logger.info(f"Fetching schools from {self.SCHOOLS_URL}")
            school_response = await client.get(
                self.SCHOOLS_URL,
                params={
                    "filterType": "by_region",
                    "kgType": 0,
                    "regionId": 0,
                },
            )
            school_response.raise_for_status()
            school_data = school_response.json()

            # Extract schools (note: still called "kinderGardens" in response)
            schools = school_data.get("items", {}).get("kinderGardens", [])
            logger.info(f"Found {len(schools)} schools")

            # Parse schools
            for school in schools:
                school_obj = await self._process_registry_record(
                    school,
                    record_type="school",
                    default_type="school",
                    existing_pages=existing_pages,
                    school_id_by_kg_id=school_id_by_kg_id,
                    seen_kg_ids=seen_kg_ids,
                    seen_at=now,
                )
                if school_obj:
                    parsed_records.append(school_obj)

            merged_records = self._merge_building_branch_records(parsed_records)
            discovered_schools = [school for school, changed in merged_records if changed]

            # Apply limit if specified
            if limit:
                discovered_schools = discovered_schools[:limit]

        # Update active/inactive flags for existing schools (avoid mass inactivation on empty results)
        if seen_kg_ids:
            await self._update_active_flags(seen_kg_ids, now)
        else:
            logger.warning("kg.sofia list returned no records; skipping active/inactive updates")

        logger.info(f"Discovered {len(discovered_schools)} institutions from kg.sofia.bg")
        return discovered_schools

    async def _process_registry_record(
        self,
        data: dict,
        record_type: str,
        default_type: str,
        existing_pages: dict[str, "SourcePage"],
        school_id_by_kg_id: dict[str, int],
        seen_kg_ids: set[str],
        seen_at: datetime,
    ) -> Optional[tuple[DiscoveredSchool, bool]]:
        from app.models import SourcePage, ScrapeType

        kg_id = data.get("id")
        if kg_id is None:
            logger.warning("kg.sofia record missing id, skipping")
            return None

        kg_id_str = str(kg_id)
        seen_kg_ids.add(kg_id_str)

        source_url = self._registry_source_url(record_type, kg_id_str)
        page_hash = self._hash_record({"_source": record_type, "data": data})
        source_page = existing_pages.get(source_url)
        school_id = school_id_by_kg_id.get(kg_id_str)
        # A record with no linked school is always processed: an earlier limited run may
        # have stored its fingerprint without ever creating the school.
        changed = (
            source_page is None
            or source_page.content_hash != page_hash
            or school_id is None
        )
        self._upsert_registry_page(
            source_page=source_page,
            source_url=source_url,
            page_hash=page_hash,
            school_id=school_id,
            changed=changed,
            seen_at=seen_at,
        )

        school = self._parse_institution(data, default_type)
        if school:
            school.attributes["kg_sofia_active"] = True
            school.attributes["kg_sofia_last_seen_at"] = seen_at.isoformat()
            return school, changed
        return None

    def _existing_school_id_for(self, disc: DiscoveredSchool) -> Optional[int]:
        """Match by kg.sofia record id: the adapter's names carry no MoE id and may drift."""
        attrs = disc.attributes or {}
        kg_ids = [attrs.get("kg_sofia_id"), *(attrs.get("kg_sofia_ids") or [])]
        mapping = getattr(self, "_school_id_by_kg_id", {}) or {}
        for kg_id in kg_ids:
            if kg_id is not None and str(kg_id) in mapping:
                return mapping[str(kg_id)]
        return None

    def _may_create_school(self, disc: DiscoveredSchool) -> bool:
        """Only kindergartens and nurseries are created from kg.sofia.bg.

        kg.sofia school records (state schools with preparatory groups) carry no MoE
        institutional id and use different names than the MoE register, so creating
        them would duplicate MoE schools. They may only enrich a matched school.
        """
        return disc.education_level == "kindergarten"

    def _registry_source_url(self, record_type: str, kg_id: str) -> str:
        return f"{self.REGISTRY_SOURCE_PREFIX}{record_type}/{kg_id}"

    def _hash_record(self, payload: dict) -> str:
        return BaseScraper.compute_hash(
            json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        )

    def _upsert_registry_page(
        self,
        source_page: Optional["SourcePage"],
        source_url: str,
        page_hash: str,
        school_id: Optional[int],
        changed: bool,
        seen_at: datetime,
    ) -> None:
        from app.models import SourcePage, ScrapeType

        if source_page:
            source_page.last_scraped_at = seen_at
            source_page.scrape_count = (source_page.scrape_count or 0) + 1
            if changed:
                source_page.content_hash = page_hash
                source_page.last_changed_at = seen_at
            if school_id and source_page.school_id != school_id:
                source_page.school_id = school_id
        else:
            new_page = SourcePage(
                school_id=school_id,
                scrape_type=ScrapeType.REGISTRY,
                source_url=source_url,
                content_hash=page_hash,
                last_scraped_at=seen_at,
                last_changed_at=seen_at,
                scrape_count=1,
            )
            self.db.add(new_page)

    async def _update_active_flags(self, seen_kg_ids: set[str], seen_at: datetime) -> None:
        from sqlalchemy import select
        from app.models import School

        now_iso = seen_at.isoformat()
        result = await self.db.execute(
            select(School).where(
                School.country_code == "bg",
                School.city == "sofia",
            )
        )
        for school in result.scalars().all():
            attrs = school.attributes or {}
            id_candidates: list[str] = []
            kg_ids = attrs.get("kg_sofia_ids")
            if isinstance(kg_ids, list):
                for kg_id in kg_ids:
                    if kg_id is not None:
                        id_candidates.append(str(kg_id))
            single_kg_id = attrs.get("kg_sofia_id")
            if single_kg_id is not None:
                id_candidates.append(str(single_kg_id))
            if not id_candidates:
                continue
            active = any(kg_id in seen_kg_ids for kg_id in id_candidates)
            if active:
                attrs["kg_sofia_active"] = True
                attrs["kg_sofia_last_seen_at"] = now_iso
                attrs.pop("kg_sofia_inactive_since", None)
            else:
                attrs["kg_sofia_active"] = False
                attrs.setdefault("kg_sofia_inactive_since", now_iso)
            school.attributes = attrs

    def _merge_building_branch_records(
        self,
        records: list[tuple[DiscoveredSchool, bool]],
    ) -> list[tuple[DiscoveredSchool, bool]]:
        """
        Merge explicit '- сграда ...' branch entries into one institution with multiple locations.

        Safety constraints:
        - Merge only when at least one record has explicit 'сграда' suffix.
        - Require same normalized base name, district, phone, and education level.
        - Keep non-branch records untouched.
        """
        if not records:
            return []

        family_candidates: set[tuple[str, str, str, str]] = set()
        for school, _changed in records:
            name_bg = (school.name_i18n or {}).get("bg", "").strip()
            base_name = self._extract_building_base_name(name_bg)
            if not base_name:
                continue
            primary_location = self._get_primary_location(school)
            family_candidates.add(
                (
                    self._normalize_name_for_grouping(base_name),
                    (primary_location.district or "").strip().lower(),
                    self._normalize_phone(primary_location.phone),
                    school.education_level,
                )
            )

        grouped: dict[tuple[str, ...], list[tuple[DiscoveredSchool, bool]]] = {}
        for index, (school, changed) in enumerate(records):
            primary_location = self._get_primary_location(school)
            name_bg = (school.name_i18n or {}).get("bg", "").strip()
            base_name = self._extract_building_base_name(name_bg)

            if base_name:
                key = (
                    "family",
                    self._normalize_name_for_grouping(base_name),
                    (primary_location.district or "").strip().lower(),
                    self._normalize_phone(primary_location.phone),
                    school.education_level,
                )
            else:
                candidate_key = (
                    self._normalize_name_for_grouping(name_bg),
                    (primary_location.district or "").strip().lower(),
                    self._normalize_phone(primary_location.phone),
                    school.education_level,
                )
                if candidate_key in family_candidates:
                    key = ("family", *candidate_key)
                else:
                    key = ("single", str(index))

            grouped.setdefault(key, []).append((school, changed))

        merged_records: list[tuple[DiscoveredSchool, bool]] = []
        for key, group in grouped.items():
            if key[0] != "family" or len(group) == 1:
                merged_records.extend(group)
                continue

            merged_records.append(self._merge_school_group(group))

        return merged_records

    def _merge_school_group(
        self,
        group: list[tuple[DiscoveredSchool, bool]],
    ) -> tuple[DiscoveredSchool, bool]:
        """Merge one grouped family into a single DiscoveredSchool record."""
        schools = [school for school, _changed in group]
        group_changed = any(changed for _school, changed in group)

        canonical = self._pick_canonical_school(schools)
        merged_school = canonical.model_copy(deep=True)

        # Prefer unsuffixed institution name when available.
        preferred_name = (merged_school.name_i18n or {}).get("bg", "")
        for school in schools:
            name_bg = (school.name_i18n or {}).get("bg", "")
            if name_bg and not self._extract_building_base_name(name_bg):
                preferred_name = name_bg
                break
        if preferred_name:
            merged_school.name_i18n = {**(merged_school.name_i18n or {}), "bg": preferred_name}

        preferred_primary = self._location_signature(self._get_primary_location(canonical))
        merged_locations: list[DiscoveredLocation] = []
        signature_to_index: dict[tuple[str, str, str], int] = {}

        for school in schools:
            for location in school.locations:
                signature = self._location_signature(location)
                if signature in signature_to_index:
                    existing = merged_locations[signature_to_index[signature]]
                    existing_tags = set(existing.location_tags or [])
                    incoming_tags = set(location.location_tags or [])
                    existing.location_tags = sorted(existing_tags | incoming_tags)
                    existing.age_groups = sorted(set(existing.age_groups or []) | set(location.age_groups or []))
                    continue
                copied = location.model_copy(deep=True)
                copied.is_primary = signature == preferred_primary
                copied.location_tags = sorted(set(copied.location_tags or []))
                copied.age_groups = sorted(set(copied.age_groups or []))
                signature_to_index[signature] = len(merged_locations)
                merged_locations.append(copied)

        if merged_locations and not any(location.is_primary for location in merged_locations):
            merged_locations[0].is_primary = True
        merged_school.locations = merged_locations

        canonical_attrs = merged_school.attributes or {}
        kg_ids: set[str] = set()
        esri_ids: set[str] = set()
        for school in schools:
            attrs = school.attributes or {}
            kg_id = attrs.get("kg_sofia_id")
            if kg_id is not None:
                kg_ids.add(str(kg_id))
            grouped_kg_ids = attrs.get("kg_sofia_ids")
            if isinstance(grouped_kg_ids, list):
                for grouped_kg_id in grouped_kg_ids:
                    if grouped_kg_id is not None:
                        kg_ids.add(str(grouped_kg_id))
            esri_id = attrs.get("kg_sofia_esri_id")
            if esri_id is not None:
                esri_ids.add(str(esri_id))

        primary_kg_id = canonical_attrs.get("kg_sofia_id")
        if primary_kg_id is None and kg_ids:
            primary_kg_id = sorted(kg_ids)[0]

        canonical_attrs["kg_sofia_id"] = primary_kg_id
        canonical_attrs["kg_sofia_ids"] = sorted(kg_ids)
        if esri_ids:
            canonical_attrs["kg_sofia_esri_ids"] = sorted(esri_ids)
        canonical_attrs["kg_sofia_merged_buildings"] = True
        source_refs = canonical_attrs.get("source_refs")
        if not isinstance(source_refs, dict):
            source_refs = {}
        kg_ref = source_refs.get(self.ADAPTER_NAME)
        if not isinstance(kg_ref, dict):
            kg_ref = {}
        kg_ref["record_id"] = str(primary_kg_id) if primary_kg_id is not None else None
        kg_ref["record_ids"] = sorted(kg_ids)
        kg_ref["esri_ids"] = sorted(esri_ids)
        source_refs[self.ADAPTER_NAME] = kg_ref
        canonical_attrs["source_refs"] = source_refs
        merged_school.attributes = canonical_attrs

        return merged_school, group_changed

    def _pick_canonical_school(self, schools: list[DiscoveredSchool]) -> DiscoveredSchool:
        """Pick canonical school record for merged attributes and metadata."""
        unsuffixed = [
            school
            for school in schools
            if not self._extract_building_base_name((school.name_i18n or {}).get("bg", ""))
        ]
        if unsuffixed:
            return unsuffixed[0]
        return schools[0]

    def _get_primary_location(self, school: DiscoveredSchool) -> DiscoveredLocation:
        """Return primary location, falling back to first available location."""
        return next((location for location in school.locations if location.is_primary), school.locations[0])

    def _extract_building_base_name(self, name_bg: str) -> Optional[str]:
        """Extract base school name from '- сграда ...' branch names."""
        if not name_bg:
            return None
        match = self.BUILDING_SUFFIX_RE.match(name_bg.strip())
        if not match:
            return None
        return match.group("base").strip()

    def _normalize_name_for_grouping(self, name: str) -> str:
        """Normalize BG name for deterministic grouping comparisons."""
        normalized = name.upper().strip()
        normalized = re.sub(r'\s*\(.*?\)\s*', ' ', normalized)
        normalized = normalized.replace('"', '').replace('„', '').replace('“', '')
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        return normalized

    def _normalize_phone(self, phone: Optional[str]) -> str:
        """Normalize phone for grouping key comparisons."""
        if not phone:
            return ""
        return re.sub(r"[^\d+]", "", phone)

    def _location_signature(self, location: DiscoveredLocation) -> tuple[str, str, str]:
        """Location signature used to deduplicate merged locations."""
        address_bg = (location.address_i18n or {}).get("bg", "").strip().lower()
        district = (location.district or "").strip().lower()
        phone = self._normalize_phone(location.phone)
        return address_bg, district, phone

    def _parse_institution(
        self, data: dict, default_type: str = "kindergarten"
    ) -> Optional[DiscoveredSchool]:
        """
        Parse institution data from kg.sofia.bg API.

        API Response Fields:
        - id: Internal database ID
        - nameStr: Full name (use this)
        - name.publicType: Institution type (ДГ, СУ, ОУ, etc.)
        - address: Full address string
        - region: District/region name
        - contacts[]: Contact information (phone, email, etc.)
        - esriId: ESRI GIS ID

        Args:
            data: Dict with institution data from API
            default_type: Default education level if type parsing fails

        Returns:
            DiscoveredSchool object or None if parsing fails
        """
        try:
            # Extract name
            name_bg = data.get("nameStr", "").strip()
            if not name_bg:
                logger.warning(f"Institution {data.get('id')} has no name, skipping")
                return None

            # Determine education level from publicType
            public_type = data.get("name", {}).get("publicType", "")
            education_level = self._determine_education_level(public_type, name_bg)

            # Determine school type
            # NOTE: kg.sofia.bg is the Sofia Municipality portal - ALL institutions are municipal (classified as "state" in our schema)
            # Private schools are not listed here - they come from MoE API
            school_type = "state"

            # Extract address
            address_bg = data.get("address", "").strip()
            if not address_bg:
                logger.warning(f"Institution {name_bg} has no address, skipping")
                return None

            # Extract district
            district = data.get("region", "").strip()
            if district:
                district = self.DISTRICT_MAPPING.get(district.lower(), district)

            # Extract phone from contacts array
            phone = self._extract_phone(data.get("contacts", []))

            # Extract ESRI ID
            esri_id = data.get("esriId")
            source_record_id = data.get("id")

            # Extract age groups from publicType
            age_groups = self._extract_age_groups(public_type, education_level)

            location_tags = [
                f"source={self.ADAPTER_NAME}",
                f"source_record_id={source_record_id}",
            ]
            if esri_id is not None:
                location_tags.append(f"source_esri_id={esri_id}")

            # Build location
            location = DiscoveredLocation(
                address_i18n={"bg": address_bg},
                district=district,
                phone=phone,
                is_primary=True,
                location_tags=location_tags,
                age_groups=age_groups,
                shifts={},  # Not provided by this API
                has_organised_groups={},  # Not provided by this API
            )

            # Build DiscoveredSchool
            return DiscoveredSchool(
                institutional_id=None,  # Not provided - must match with MoE data
                name_i18n={"bg": name_bg},
                country_code="bg",
                city="sofia",
                school_type=school_type,
                education_level=education_level,
                source_url=self.KINDERGARTENS_URL if education_level == "kindergarten" else self.SCHOOLS_URL,
                locations=[location],
                attributes={
                    "kg_sofia_id": source_record_id,
                    "kg_sofia_esri_id": esri_id,
                    "kg_sofia_public_type": public_type,
                    "source_refs": {
                        self.ADAPTER_NAME: {
                            "record_id": str(source_record_id),
                            "record_ids": [str(source_record_id)],
                            "esri_id": str(esri_id) if esri_id is not None else None,
                            "esri_ids": [str(esri_id)] if esri_id is not None else [],
                            "public_type": public_type,
                        }
                    },
                },
            )

        except Exception as e:
            logger.error(f"Error parsing institution data: {e}")
            return None

    def _determine_education_level(self, public_type: str, name: str) -> str:
        """
        Determine education level from publicType or school name.

        Args:
            public_type: Institution type from API (e.g., "ДГ", "СУ", "ОУ")
            name: School name (fallback for parsing)

        Returns:
            Education level: "kindergarten", "primary", "lower_secondary", "upper_secondary"
        """
        # Try exact match first
        if public_type in self.TYPE_MAPPING:
            return self.TYPE_MAPPING[public_type]

        # Try partial match
        for key, level in self.TYPE_MAPPING.items():
            if key in public_type:
                return level

        # Fallback: parse from name
        name_lower = name.lower()

        if "дг" in name_lower or "детска градина" in name_lower:
            return "kindergarten"
        if "су" in name_lower or "средно" in name_lower or "гимназия" in name_lower:
            return "upper_secondary"
        if "оу" in name_lower or "основно" in name_lower:
            return "lower_secondary"
        if "ну" in name_lower or "начално" in name_lower:
            return "primary"

        # Default
        logger.warning(f"Could not determine education level for '{name}', defaulting to kindergarten")
        return "kindergarten"

    def _extract_age_groups(self, public_type: str, education_level: str) -> list[str]:
        """
        Extract age groups from publicType (for kindergartens) or derive from education level.

        Args:
            public_type: Institution type from API (e.g., "ДГ (с яслени групи)", "СУ")
            education_level: Determined education level

        Returns:
            List of age group codes
        """
        # For kindergartens, try to get from publicType (most precise)
        if education_level == "kindergarten":
            # Try exact match first
            if public_type in self.AGE_GROUP_MAPPING:
                return self.AGE_GROUP_MAPPING[public_type]

            # Default for kindergartens: assume no nursery (conservative)
            return ['first', 'second', 'third', 'preschool']

        # For schools, try publicType mapping first (more accurate for multi-grade schools)
        if public_type in self.SCHOOL_TYPE_AGE_GROUPS:
            return self.SCHOOL_TYPE_AGE_GROUPS[public_type]

        # Fall back to education level defaults
        education_level_defaults = {
            'primary': ['grade_1_4'],
            'lower_secondary': ['grade_5_7'],
            'upper_secondary': ['grade_8_12'],
        }

        return education_level_defaults.get(education_level, [])

    def _extract_phone(self, contacts: list) -> Optional[str]:
        """
        Extract phone number from contacts array.

        Args:
            contacts: List of contact objects from API

        Returns:
            Phone number string or None
        """
        for contact in contacts:
            kind = contact.get("kindCommunication", {}).get("label", "")
            if kind == "phone":
                phone = contact.get("fieldValue", "").strip()
                if phone:
                    return phone
        return None

    async def _delay(self):
        """Small delay between requests (not really needed since API returns all data at once)."""
        import asyncio

        await asyncio.sleep(0.1)
