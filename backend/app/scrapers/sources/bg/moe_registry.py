"""
MoeRegistryAdapter - Discover schools and kindergartens from the Ministry of Education registry

This adapter uses the Bulgarian Ministry of Education's official API (ri-api.mon.bg)
to discover all educational institutions (kindergartens, primary schools, secondary schools)
with their official institutional IDs.

For detailed API documentation, field mappings, and examples, see:
backend/docs/MOE_API_DOCUMENTATION.md
"""
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import httpx

from app.config import get_settings
from app.models.scrape_log import ScrapeType
from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool
from app.scrapers.base import BaseScraper
from app.scrapers.sources import register_adapter
from app.scrapers.sources.base_adapter import BaseSourceAdapter
from app.services.geocoding.base import GeocodingResult
from app.services.geocoding.bg import GeoJSONProvider, city_storage_value
from app.services.geocoding.nominatim import NominatimProvider

if TYPE_CHECKING:
    from app.models.source_page import SourcePage

logger = logging.getLogger(__name__)
_LOCALITY_MARKER_RE = re.compile(r"\b(?:гр\.?|с\.?|село|район|кв\.|ж\.к\.)\b", flags=re.IGNORECASE)


@register_adapter
class MoeRegistryAdapter(BaseSourceAdapter):
    """
    Discover schools and kindergartens from the Ministry of Education registry API.

    The Bulgarian Ministry of Education maintains a public API at ri-api.mon.bg
    with all registered educational institutions including their institutional codes (Код по НЕИСПУО).

    Data extracted:
    - Institutional ID (instid) - CRITICAL for idempotency
    - School name (Bulgarian)
    - School type (state/private/international)
    - Education level (kindergarten/primary/lower_secondary/upper_secondary)
    - Region, Municipality, Town codes
    """

    # Adapter metadata
    ADAPTER_NAME = "moe_registry"
    COUNTRY_CODE = "bg"
    CITY = None  # Can filter by region/municipality
    DESCRIPTION = "Bulgarian schools and kindergartens from Ministry of Education API (ri-api.mon.bg)"
    RATE_LIMIT = "60/m"  # 1 second between detail requests
    SCRAPE_TYPE = ScrapeType.REGISTRY

    # Ministry of Education API endpoints
    API_BASE_URL = "https://ri-api.mon.bg"
    PUBLIC_REGISTER_URL = f"{API_BASE_URL}/data/get/public-register"
    INSTITUTION_DETAIL_URL = f"{API_BASE_URL}/data/get/institution"
    REGIONS_URL = f"{API_BASE_URL}/data/get/regionMultiple"
    MUNICIPALITIES_URL = f"{API_BASE_URL}/data/get/municipalityMultiple"
    TOWNS_URL = f"{API_BASE_URL}/data/get/townMultiple"
    REGISTRY_SOURCE_PREFIX = "moe://public-register/"

    # Sofia region codes (город София + област София)
    SOFIA_CITY_REGION = 22  # София-град
    SOFIA_OBLAST_REGION = 23  # София област

    # Institution type mapping (instType field)
    INST_TYPE_MAPPING = {
        1: "school",  # Училище
        2: "kindergarten",  # Детска градина
        # 3-5 are support centers, not regular schools
    }

    # Financial/ownership type mapping (financialSchoolType field)
    FINANCIAL_TYPE_MAPPING = {
        1: "state",  # Държавно
        2: "state",  # Общинско (municipal = state-run)
        3: "private",  # Частно
        11: "state",  # Духовно (religious schools are state-funded)
        12: "international",  # По силата на международен договор
    }

    # Detailed school type mapping (detailedSchoolType field)
    # Maps to education_level in our schema
    DETAILED_TYPE_MAPPING = {
        111: "upper_secondary",  # духовно
        112: "upper_secondary",  # по изкуствата
        113: "upper_secondary",  # по културата
        114: "upper_secondary",  # спортно
        121: "primary",  # начално (grades 1-4)
        122: "lower_secondary",  # основно (grades 1-8)
        123: "upper_secondary",  # обединено (grades 1-12)
        124: "upper_secondary",  # средно (grades 1-12)
        125: "upper_secondary",  # профилирана гимназия (grades 8-12)
        126: "upper_secondary",  # професионална гимназия (vocational)
        131: "lower_secondary",  # за обучение и подкрепа на ученици с увреден слух
        132: "lower_secondary",  # за обучение и подкрепа на ученици с нарушено зрение
        133: "lower_secondary",  # възпитателно училище - интернат
        134: "lower_secondary",  # социално-педагогически интернат
        141: "lower_secondary",  # към местата за лишаване от свобода
        151: "kindergarten",  # детска градина
        # 161-174 are support centers, observatories, dormitories (not schools)
        181: "upper_secondary",  # училище, функциониращо по силата на международен договор
    }

    # Age group defaults derived from education level
    # (MoE API doesn't provide explicit age group data)
    EDUCATION_LEVEL_AGE_GROUPS = {
        'kindergarten': ['first', 'second', 'third', 'preschool'],  # Conservative (no nursery by default)
        'primary': ['grade_1_4'],
        'lower_secondary': ['grade_5_7'],
        'upper_secondary': ['grade_8_12'],
    }

    # Detailed school type → age groups mapping (for multi-grade schools)
    # Overrides EDUCATION_LEVEL_AGE_GROUPS when detailed type is known
    # Uses detailedSchoolType codes from MoE API
    DETAILED_TYPE_AGE_GROUPS = {
        121: ['grade_1_4'],  # начално (grades 1-4)
        122: ['grade_1_4', 'grade_5_7'],  # основно (grades 1-8; no dedicated grade-8 bucket)
        123: ['grade_1_4', 'grade_5_7', 'grade_8_12'],  # обединено (grades 1-12)
        124: ['grade_1_4', 'grade_5_7', 'grade_8_12'],  # средно (grades 1-12)
        125: ['grade_8_12'],  # профилирана гимназия (grades 8-12)
        126: ['grade_8_12'],  # професионална гимназия (grades 8-12)
        151: ['first', 'second', 'third', 'preschool'],  # детска градина
    }

    def __init__(self, db):
        super().__init__(db)
        self._geojson_provider = GeoJSONProvider()
        self._nominatim_provider: Optional[NominatimProvider] = None
        self._region_labels: dict[int, str] = {}
        self._municipality_labels: dict[int, str] = {}
        self._town_labels: dict[int, str] = {}

        settings = get_settings()
        contact_email = settings.geocoding_contact_email
        if contact_email and "example.com" not in contact_email.lower():
            user_agent = f"SofiaSchoolComparison/1.0 ({contact_email})"
            self._nominatim_provider = NominatimProvider(user_agent=user_agent)
        else:
            logger.warning(
                "MoE registry geocoding will skip Nominatim because GEOCODING_CONTACT_EMAIL is not configured"
            )

    @staticmethod
    def _get_age_groups_for_detailed_type(detailed_type: Optional[int], education_level: str) -> list[str]:
        """
        Derive age groups from detailed school type or education level.

        Args:
            detailed_type: MoE detailedSchoolType code (e.g., 122 for основно)
            education_level: Fallback education level if detailed type not recognized

        Returns:
            List of age group codes
        """
        # Try detailed type mapping first (more accurate for multi-grade schools)
        if detailed_type and detailed_type in MoeRegistryAdapter.DETAILED_TYPE_AGE_GROUPS:
            return MoeRegistryAdapter.DETAILED_TYPE_AGE_GROUPS[detailed_type]

        # Fall back to education level mapping
        return MoeRegistryAdapter.EDUCATION_LEVEL_AGE_GROUPS.get(education_level, [])

    @staticmethod
    def _get_age_groups_for_education_level(education_level: str) -> list[str]:
        """
        Derive reasonable age group defaults from education level.

        DEPRECATED: Use _get_age_groups_for_detailed_type() instead for better accuracy.

        Args:
            education_level: Education level code

        Returns:
            List of age group codes
        """
        return MoeRegistryAdapter.EDUCATION_LEVEL_AGE_GROUPS.get(education_level, [])

    @staticmethod
    def _extract_code_label_map(payload: dict | None) -> dict[int, str]:
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return {}

        resolved: dict[int, str] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            label = item.get("label")
            if isinstance(code, int) and isinstance(label, str) and label.strip():
                resolved[code] = label.strip()
        return resolved

    async def _load_reference_labels(self, client: httpx.AsyncClient) -> None:
        if self._region_labels and self._municipality_labels and self._town_labels:
            return

        endpoints = (
            ("_region_labels", self.REGIONS_URL),
            ("_municipality_labels", self.MUNICIPALITIES_URL),
            ("_town_labels", self.TOWNS_URL),
        )
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
        }

        for attr_name, url in endpoints:
            try:
                response = await client.post(url, json={}, headers=headers)
                response.raise_for_status()
                setattr(self, attr_name, self._extract_code_label_map(response.json()))
            except Exception as exc:
                logger.warning("Failed to load MoE lookup labels from %s: %s", url, exc)
                setattr(self, attr_name, {})

    @staticmethod
    def _preferred_geocoding_city(city: Optional[str], attributes: Optional[dict]) -> Optional[str]:
        attrs = attributes or {}
        region_code = attrs.get("moe_region_code")
        if region_code == MoeRegistryAdapter.SOFIA_OBLAST_REGION:
            return (
                attrs.get("moe_town_name")
                or attrs.get("moe_municipality_name")
                or attrs.get("moe_region_name")
                or city
            )
        return city

    @classmethod
    def _derive_record_city(
        cls,
        *,
        region_code: Optional[int],
        municipality_name: Optional[str],
        town_name: Optional[str],
    ) -> Optional[str]:
        """Derive city from this registry record, never from the import scope."""
        settlement_city = city_storage_value(town_name)
        municipality_city = city_storage_value(municipality_name)
        # Region 22 is Sofia-city (Stolichna municipality), including Bankya
        # and its other settlements. Keep it in Sofia scope even if the
        # municipality lookup failed while the independent town lookup worked.
        if region_code == cls.SOFIA_CITY_REGION:
            return "sofia"
        # Bankya and other settlements inside Stolichna municipality remain in
        # Sofia scope when the municipality label is available.
        if municipality_city == "sofia":
            return "sofia"
        if settlement_city:
            return settlement_city
        if municipality_city:
            return municipality_city
        # Missing lookup labels must not cause Sofia-oblast rows to inherit the
        # Sofia batch scope.
        return None

    @staticmethod
    def _address_with_locality_hint(address: str, locality_name: Optional[str]) -> str:
        normalized_address = (address or "").strip()
        locality = (locality_name or "").strip()
        if not normalized_address or not locality:
            return normalized_address
        if locality.casefold() in normalized_address.casefold():
            return normalized_address
        if _LOCALITY_MARKER_RE.search(normalized_address):
            return normalized_address
        return f"{locality}, {normalized_address}"

    def _filter_incoming_attributes(self, incoming: dict) -> dict:
        """Preserve last-known MoE labels across partial lookup failures."""
        filtered = super()._filter_incoming_attributes(incoming)
        lookup_label_keys = {
            "moe_region_name",
            "moe_municipality_name",
            "moe_town_name",
        }
        return {
            key: value
            for key, value in filtered.items()
            if value is not None or key not in lookup_label_keys
        }

    async def discover(
        self,
        limit: Optional[int] = None,
        fetch_details: bool = True,
        sample_ratio: float = 0.0,
    ) -> list[DiscoveredSchool]:
        """
        Discover schools from the Ministry of Education API.

        Process:
        1. Call the public-register API endpoint with Sofia region filters
        2. For each school, fetch detailed data (addresses, phone, email, website)
        3. Parse the JSON response
        4. Map API fields to our DiscoveredSchool schema
        5. Return list of schools

        Args:
            limit: Optional limit on number of schools to discover (for testing)
            fetch_details: Whether to fetch detailed data for each school (default: True)
            sample_ratio: Optional ratio (0.0-1.0) of unchanged schools to sample for details

        Returns:
            List of DiscoveredSchool objects

        Raises:
            httpx.HTTPError: If the API is unreachable
            ValueError: If the response format is unexpected
        """
        from sqlalchemy import select

        from app.models import School, ScrapeType, SourcePage

        discovered_schools = []
        seen_instids: set[str] = set()
        now = datetime.now(timezone.utc)

        # Preload registry pages for quick lookup
        pages_result = await self.db.execute(
            select(SourcePage).where(
                SourcePage.scrape_type == ScrapeType.REGISTRY,
                SourcePage.source_url.like(f"{self.REGISTRY_SOURCE_PREFIX}%"),
            )
        )
        existing_pages = {page.source_url: page for page in pages_result.scalars().all()}

        # Preload instid -> existing school metadata map
        schools_result = await self.db.execute(
            select(School.id, School.institutional_id, School.website_url).where(
                School.country_code == "bg",
                School.city == "sofia",
                School.institutional_id.isnot(None),
            )
        )
        school_meta_by_instid = {
            str(instid): {"school_id": school_id, "website_url": website_url}
            for school_id, instid, website_url in schools_result.all()
            if instid
        }

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            await self._load_reference_labels(client)

            # Step 1: Fetch schools from Sofia city and Sofia region
            logger.info(f"Fetching schools from MoE API: {self.PUBLIC_REGISTER_URL}")

            # Request body - filter for Sofia regions and active institutions only
            request_body = {
                "region": [self.SOFIA_CITY_REGION, self.SOFIA_OBLAST_REGION],
                "isRIActive": 1,  # Only active institutions
            }

            response = await client.post(
                self.PUBLIC_REGISTER_URL,
                json=request_body,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()

            # Step 2: Parse response
            data = response.json()

            if data.get("status") != 1:
                raise ValueError(f"API returned error status: {data}")

            institutions = data.get("data", {}).get("publicInstitutions", [])
            logger.info(f"Found {len(institutions)} institutions from API")

            if limit:
                institutions = institutions[:limit]

            # Step 3: Process each institution
            for idx, inst_data in enumerate(institutions, 1):
                try:
                    logger.info(f"Processing institution {idx}/{len(institutions)}: {inst_data.get('name')}")

                    instid = inst_data.get("instid")
                    if instid is None:
                        logger.warning("Institution has no instid, skipping")
                        continue
                    instid_str = str(instid)
                    seen_instids.add(instid_str)

                    source_url = self._registry_source_url(instid_str)
                    page_hash = self._hash_record(inst_data)
                    source_page = existing_pages.get(source_url)
                    changed = source_page is None or source_page.content_hash != page_hash
                    sampled = False

                    if not changed and sample_ratio > 0:
                        sampled = self._should_sample(instid_str, sample_ratio, now)

                    school_meta = school_meta_by_instid.get(instid_str) or {}
                    school_id = school_meta.get("school_id")
                    self._upsert_registry_page(
                        source_page=source_page,
                        source_url=source_url,
                        page_hash=page_hash,
                        school_id=school_id,
                        changed=changed,
                        seen_at=now,
                    )

                    # Step 3a: Fetch detailed data for this school
                    detail_data = None
                    if fetch_details and (changed or sampled):
                        detail_data = await self._fetch_institution_detail(client, inst_data)

                    # Step 3b: Parse institution data (with detail data if available)
                    school = None
                    if changed or sampled:
                        school = self._parse_institution_data(inst_data, detail_data)

                    if school:
                        existing_website = school_meta.get("website_url")
                        if not school.website_url and existing_website:
                            school.website_url = existing_website

                        if fetch_details and school.locations:
                            await self._populate_location_coordinates(school)

                        if fetch_details and not school.locations:
                            fallback_location = await self._recover_location_from_geojson(
                                school=school,
                                inst_data=inst_data,
                                detail_data=detail_data,
                            )
                            if fallback_location:
                                school.locations = [fallback_location]
                                school.attributes["missing_location_data"] = False
                                school.attributes["moe_location_fallback"] = "geojson"
                            else:
                                school.attributes["missing_location_data"] = True
                                school.attributes["missing_location_reason"] = "no_address_and_geojson_miss"
                                logger.warning(
                                    "No location recovered for instid=%s (%s)",
                                    school.institutional_id,
                                    school.name_i18n.get("bg"),
                                )
                        school.attributes["moe_registry_active"] = True
                        school.attributes["moe_registry_last_seen_at"] = now.isoformat()
                        discovered_schools.append(school)

                    # Small delay to be respectful
                    if fetch_details and (changed or sampled):
                        await self._delay()

                except Exception as e:
                    logger.error(f"Error processing institution {inst_data.get('id')}: {e}")
                    continue

        # Update active/inactive flags for existing schools (avoid mass inactivation on empty results)
        if seen_instids:
            await self._update_active_flags(seen_instids, now)
        else:
            logger.warning("MoE public-register returned no records; skipping active/inactive updates")

        logger.info(f"Discovered {len(discovered_schools)} schools from MoE registry")
        return discovered_schools

    async def _populate_location_coordinates(self, school: DiscoveredSchool) -> None:
        """Populate coordinates for discovered locations when address data is available."""
        for location in school.locations:
            if location.lat is not None and location.lng is not None:
                continue

            result, provider_tag = await self._geocode_discovered_location(school, location)
            if not result.success or result.lat is None or result.lng is None:
                logger.warning(
                    "Failed to geocode discovered location for instid=%s (%s): %s",
                    school.institutional_id,
                    school.name_i18n.get("bg"),
                    result.error,
                )
                continue

            location.lat = result.lat
            location.lng = result.lng
            location.geocode_meta = {
                "status": "accepted",
                "provider": result.provider,
                "method": result.method,
                "precision": result.precision,
                "formatted_address": result.formatted_address,
            }
            location.geocode_meta = {
                key: value for key, value in location.geocode_meta.items() if value is not None
            }

            if provider_tag and provider_tag not in location.location_tags:
                location.location_tags.append(provider_tag)

    async def _geocode_discovered_location(
        self,
        school: DiscoveredSchool,
        location: DiscoveredLocation,
    ) -> tuple[GeocodingResult, Optional[str]]:
        """Geocode a discovered location using address-first lookup with GeoJSON fallback."""
        address = (location.address_i18n or {}).get("bg") or (location.address_i18n or {}).get("en") or ""
        school_name = (school.name_i18n or {}).get("bg") or (school.name_i18n or {}).get("en")
        geocoding_city = self._preferred_geocoding_city(school.city, school.attributes)

        if self._nominatim_provider and address:
            result = await self._nominatim_provider.geocode(
                address=address,
                country_code=school.country_code,
                city=geocoding_city,
                institution_name=school_name,
            )
            if result.success and result.lat is not None and result.lng is not None:
                return result, "coords_source=nominatim"

        if school_name:
            result = await self._geojson_provider.geocode(
                address=address,
                country_code=school.country_code,
                school_name=school_name,
                city=geocoding_city,
            )
            if result.success and result.lat is not None and result.lng is not None:
                return result, "coords_source=geojson"

        if school.website_url and location.is_primary:
            result = await self._geojson_provider.geocode_by_website(school.website_url)
            if result.success and result.lat is not None and result.lng is not None:
                return result, "coords_source=geojson_website"

        return GeocodingResult(
            success=False,
            provider="moe_registry_geocoding",
            error="No geocoding match for discovered location",
        ), None

    async def _recover_location_from_geojson(
        self,
        school: DiscoveredSchool,
        inst_data: dict,
        detail_data: Optional[dict],
    ) -> Optional[DiscoveredLocation]:
        """Recover a missing location using deterministic GeoJSON school-name matching."""
        school_name_bg = (school.name_i18n or {}).get("bg")
        if not school_name_bg:
            return None

        address_hint = ""
        phone_number = None
        if detail_data:
            address_hint = (detail_data.get("settlementAddress") or "").strip()
            phone_number = (detail_data.get("phoneNumber") or "").strip() or None

        geo_result = await self._geojson_provider.geocode(
            address=address_hint,
            country_code=school.country_code,
            school_name=school_name_bg,
            city=self._preferred_geocoding_city(school.city, school.attributes),
        )
        if not geo_result.success or geo_result.lat is None or geo_result.lng is None:
            if school.website_url:
                geo_result = await self._geojson_provider.geocode_by_website(school.website_url)
            if not geo_result.success or geo_result.lat is None or geo_result.lng is None:
                return None

        detailed_type = inst_data.get("detailedSchoolType")
        age_groups = self._get_age_groups_for_detailed_type(detailed_type, school.education_level)
        recovered_address = (geo_result.formatted_address or address_hint or "София").strip()

        logger.info(
            "Recovered missing location via GeoJSON for instid=%s (%s) -> (%s, %s)",
            school.institutional_id,
            school_name_bg,
            geo_result.lat,
            geo_result.lng,
        )

        return DiscoveredLocation(
            address_i18n={"bg": recovered_address},
            district=None,
            lat=geo_result.lat,
            lng=geo_result.lng,
            phone=phone_number,
            is_primary=True,
            location_tags=["source=moe_registry", "location_recovered=geojson"],
            geocode_meta={
                "status": "accepted",
                "provider": geo_result.provider,
                "method": geo_result.method,
                "precision": geo_result.precision,
                "formatted_address": geo_result.formatted_address,
            },
            age_groups=age_groups,
            shifts={},
            has_organised_groups={},
        )

    def _registry_source_url(self, instid: str) -> str:
        return f"{self.REGISTRY_SOURCE_PREFIX}{instid}"

    def _hash_record(self, data: dict) -> str:
        return BaseScraper.compute_hash(
            json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        )

    def _should_sample(self, key: str, sample_ratio: float, now: datetime) -> bool:
        if sample_ratio <= 0:
            return False
        iso_year, iso_week, _ = now.isocalendar()
        token = f"{key}:{iso_year}-W{iso_week}"
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) / 0xFFFFFFFF
        return bucket < sample_ratio

    def _upsert_registry_page(
        self,
        source_page: Optional["SourcePage"],
        source_url: str,
        page_hash: str,
        school_id: Optional[int],
        changed: bool,
        seen_at: datetime,
    ) -> None:
        from app.models import ScrapeType, SourcePage

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

    async def _update_active_flags(self, seen_instids: set[str], seen_at: datetime) -> None:
        from sqlalchemy import select

        from app.models import School

        now_iso = seen_at.isoformat()
        result = await self.db.execute(
            select(School).where(
                School.country_code == "bg",
                School.institutional_id.isnot(None),
            )
        )
        for school in result.scalars().all():
            attrs = dict(school.attributes or {})
            region_code = attrs.get("moe_region_code")
            # The Sofia registry batch covers both Sofia-city and
            # Sofia-oblast. Relabeled province rows must remain in this active
            # reconciliation, while future imports for other regions must not.
            if region_code not in {22, 23, "22", "23"} and (school.city or "").casefold() != "sofia":
                continue
            instid = school.institutional_id
            if not instid:
                continue
            active = str(instid) in seen_instids
            if active:
                attrs["moe_registry_active"] = True
                attrs["moe_registry_last_seen_at"] = now_iso
                attrs.pop("moe_registry_inactive_since", None)
            else:
                attrs["moe_registry_active"] = False
                attrs.setdefault("moe_registry_inactive_since", now_iso)
            school.attributes = attrs

    async def _fetch_institution_detail(self, client: httpx.AsyncClient, inst_data: dict) -> Optional[dict]:
        """
        Fetch detailed data for a specific institution.

        The detail endpoint provides addresses, phone numbers, emails, websites,
        director information, and multiple locations.

        Args:
            client: HTTP client
            inst_data: Basic institution data from public-register (contains instid and procID)

        Returns:
            Dict with detailed institution data, or None if fetch fails
        """
        try:
            instid = inst_data.get("instid")
            proc_id = inst_data.get("procID")

            if not instid or not proc_id:
                logger.warning(f"Missing instid or procID for institution {inst_data.get('name')}")
                return None

            # Fetch detailed data
            response = await client.post(
                self.INSTITUTION_DETAIL_URL,
                json={"instid": str(instid), "procID": proc_id},
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()

            data = response.json()

            if data.get("status") != 1:
                logger.warning(f"Detail API returned status {data.get('status')} for institution {instid}")
                return None

            detail_list = data.get("data", [])
            if not detail_list:
                logger.warning(f"No detail data returned for institution {instid}")
                return None

            # Return first item (should only be one)
            return detail_list[0]

        except Exception as e:
            logger.error(f"Error fetching detail for institution {inst_data.get('instid')}: {e}")
            return None

    def _parse_institution_data(self, data: dict, detail_data: Optional[dict] = None) -> Optional[DiscoveredSchool]:
        """
        Parse raw institution data from the API into a DiscoveredSchool object.

        API Response Fields (from public-register):
        - id: Internal database ID
        - instid: Institutional ID (Код по НЕИСПУО) - USE THIS for idempotency
        - name: School name in Bulgarian
        - region: Region code (22 = Sofia city, 23 = Sofia oblast)
        - municipality: Municipality code
        - town: Town/settlement code
        - instType: 1=school, 2=kindergarten
        - detailedSchoolType: Specific school type (121=primary, 122=basic, etc.)
        - financialSchoolType: 1=state, 2=municipal, 3=private, etc.
        - transformType: Transformation status (not relevant for us)
        - instKind: Institution category (not relevant for us)

        Detail Data Fields (from /data/get/institution):
        - settlementAddress: Full address
        - phoneNumber: Phone number
        - email: Email address
        - website: Website URL
        - institutionDepartments: Array of department locations

        Args:
            data: Dict with raw institution data from public-register API
            detail_data: Optional dict with detailed data from institution API

        Returns:
            DiscoveredSchool object or None if not a regular school/kindergarten
        """
        try:
            # Extract institutional ID (CRITICAL for idempotency)
            instid = data.get("instid")
            if instid is None:
                logger.warning("Institution has no instid, skipping")
                return None
            institutional_id = str(instid)

            # Extract name
            name_bg = data.get("name", "").strip()
            if not name_bg:
                logger.warning(f"Institution {institutional_id} has no name, skipping")
                return None

            # Determine institution type (school vs kindergarten)
            inst_type = data.get("instType")
            if inst_type not in self.INST_TYPE_MAPPING:
                logger.debug(f"Skipping non-school institution type {inst_type}: {name_bg}")
                return None

            inst_category = self.INST_TYPE_MAPPING[inst_type]

            # Determine school type (state/private/international)
            financial_type = data.get("financialSchoolType")
            school_type = self.FINANCIAL_TYPE_MAPPING.get(financial_type, "state")

            # Determine education level from detailed type
            detailed_type = data.get("detailedSchoolType")
            education_level = self.DETAILED_TYPE_MAPPING.get(detailed_type)

            if not education_level:
                logger.warning(f"Unknown detailedSchoolType {detailed_type} for {name_bg}, skipping")
                return None

            # If instType says kindergarten, ensure education_level matches
            if inst_category == "kindergarten":
                education_level = "kindergarten"

            # Derive city from the record's settlement/municipality labels. The
            # Sofia import scope includes both Sofia-city and Sofia-oblast, so it
            # must never be used as the persisted city value.
            region_code = data.get("region")
            municipality_code = data.get("municipality")
            town_code = data.get("town")
            region_name = self._region_labels.get(region_code) if isinstance(region_code, int) else None
            municipality_name = (
                self._municipality_labels.get(municipality_code) if isinstance(municipality_code, int) else None
            )
            town_name = self._town_labels.get(town_code) if isinstance(town_code, int) else None
            city = self._derive_record_city(
                region_code=region_code,
                municipality_name=municipality_name,
                town_name=town_name,
            )

            # Extract location data from detail_data if available
            locations = []
            website_url = None

            if detail_data:
                # Extract website URL
                website_url = (detail_data.get("website") or "").strip() or None

                # Extract primary location
                primary_address = self._address_with_locality_hint(
                    (detail_data.get("settlementAddress") or "").strip(),
                    town_name,
                )
                phone_number = (detail_data.get("phoneNumber") or "").strip() or None

                if primary_address:
                    primary_location = DiscoveredLocation(
                        address_i18n={"bg": primary_address},
                        district=None,  # Will need to parse from address or enrich later
                        phone=phone_number,
                        is_primary=True,
                        age_groups=self._get_age_groups_for_detailed_type(detailed_type, education_level),
                        shifts={},
                        has_organised_groups={},
                    )
                    locations.append(primary_location)

                # Extract department locations (branches)
                departments = detail_data.get("institutionDepartments", [])
                for dept in departments:
                    dept_address = self._address_with_locality_hint(
                        (dept.get("departmentAddress") or "").strip(),
                        town_name,
                    )
                    if dept_address and dept_address != primary_address:  # Avoid duplicates
                        dept_location = DiscoveredLocation(
                            address_i18n={"bg": dept_address},
                            district=None,
                            phone=None,  # Departments don't have separate phone numbers in API
                            is_primary=False,
                            age_groups=self._get_age_groups_for_detailed_type(detailed_type, education_level),
                            shifts={},
                            has_organised_groups={},
                        )
                        locations.append(dept_location)

            # Build attributes dict
            attributes = {
                "moe_region_code": region_code,
                "moe_region_name": region_name,
                "moe_municipality_code": municipality_code,
                "moe_municipality_name": municipality_name,
                "moe_town_code": town_code,
                "moe_town_name": town_name,
                "moe_detailed_type": detailed_type,
                "moe_inst_type": inst_type,
                "moe_financial_type": financial_type,
            }

            # Add detail data attributes if available
            if detail_data:
                attributes.update({
                    "moe_bulstat": detail_data.get("bulstat"),
                    "moe_abbreviation": detail_data.get("abbreviation"),
                    "moe_director_name": detail_data.get("staffDirector"),
                    "moe_email": detail_data.get("email"),
                })

            # Build DiscoveredSchool
            return DiscoveredSchool(
                institutional_id=institutional_id,
                name_i18n={"bg": name_bg},
                country_code="bg",
                city=city,
                school_type=school_type,
                education_level=education_level,
                source_url=self.PUBLIC_REGISTER_URL,  # Provenance: API endpoint, not per-school URL (MoE doesn't have those)
                website_url=website_url,
                locations=locations,
                attributes=attributes,
            )

        except Exception as e:
            logger.error(f"Error parsing institution data: {e}")
            return None

    async def _delay(self):
        """Small delay between requests to be respectful to the API."""
        import asyncio

        # Rate limit: 10/min = 6 seconds between requests
        # Use 1 second delay to be respectful but not too slow
        await asyncio.sleep(1.0)  # 1 second between detail requests (~60 schools/min)
