"""
MoeRegistryAdapter - Discover schools and kindergartens from the Ministry of Education registry

This adapter uses the Bulgarian Ministry of Education's official API (ri-api.mon.bg)
to discover all educational institutions (kindergartens, primary schools, secondary schools)
with their official institutional IDs.

For detailed API documentation, field mappings, and examples, see:
backend/docs/MOE_API_DOCUMENTATION.md
"""
import logging
from typing import Optional
import httpx

from app.scrapers.sources.base_adapter import BaseSourceAdapter
from app.schemas.scraping import DiscoveredSchool, DiscoveredLocation
from app.scrapers.sources import register_adapter

logger = logging.getLogger(__name__)


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

    # Ministry of Education API endpoints
    API_BASE_URL = "https://ri-api.mon.bg"
    PUBLIC_REGISTER_URL = f"{API_BASE_URL}/data/get/public-register"
    INSTITUTION_DETAIL_URL = f"{API_BASE_URL}/data/get/institution"
    REGIONS_URL = f"{API_BASE_URL}/data/get/regionMultiple"
    MUNICIPALITIES_URL = f"{API_BASE_URL}/data/get/municipalityMultiple"
    TOWNS_URL = f"{API_BASE_URL}/data/get/townMultiple"

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

    async def discover(self, limit: Optional[int] = None, fetch_details: bool = True) -> list[DiscoveredSchool]:
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

        Returns:
            List of DiscoveredSchool objects

        Raises:
            httpx.HTTPError: If the API is unreachable
            ValueError: If the response format is unexpected
        """
        discovered_schools = []

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
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

                    # Step 3a: Fetch detailed data for this school
                    detail_data = None
                    if fetch_details:
                        detail_data = await self._fetch_institution_detail(client, inst_data)

                    # Step 3b: Parse institution data (with detail data if available)
                    school = self._parse_institution_data(inst_data, detail_data)

                    if school:
                        discovered_schools.append(school)

                    # Small delay to be respectful
                    await self._delay()

                except Exception as e:
                    logger.error(f"Error processing institution {inst_data.get('id')}: {e}")
                    continue

        logger.info(f"Discovered {len(discovered_schools)} schools from MoE registry")
        return discovered_schools

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

            # Determine city (based on region code)
            region_code = data.get("region")
            city = "sofia" if region_code in [self.SOFIA_CITY_REGION, self.SOFIA_OBLAST_REGION] else None

            # Extract location data from detail_data if available
            locations = []
            website_url = None

            if detail_data:
                # Extract website URL
                website_url = (detail_data.get("website") or "").strip() or None

                # Extract primary location
                primary_address = (detail_data.get("settlementAddress") or "").strip()
                phone_number = (detail_data.get("phoneNumber") or "").strip() or None
                email = (detail_data.get("email") or "").strip() or None

                if primary_address:
                    primary_location = DiscoveredLocation(
                        address_i18n={"bg": primary_address},
                        district=None,  # Will need to parse from address or enrich later
                        phone=phone_number,
                        is_primary=True,
                        age_groups=[],
                        shifts={},
                        has_organised_groups={},
                    )
                    locations.append(primary_location)

                # Extract department locations (branches)
                departments = detail_data.get("institutionDepartments", [])
                for dept in departments:
                    dept_address = (dept.get("departmentAddress") or "").strip()
                    if dept_address and dept_address != primary_address:  # Avoid duplicates
                        dept_location = DiscoveredLocation(
                            address_i18n={"bg": dept_address},
                            district=None,
                            phone=None,  # Departments don't have separate phone numbers in API
                            is_primary=False,
                            age_groups=[],
                            shifts={},
                            has_organised_groups={},
                        )
                        locations.append(dept_location)

            # Build attributes dict
            attributes = {
                "moe_region_code": region_code,
                "moe_municipality_code": data.get("municipality"),
                "moe_town_code": data.get("town"),
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
        from app.config import get_settings

        settings = get_settings()
        # Rate limit: 10/min = 6 seconds between requests
        # Use 1 second delay to be respectful but not too slow
        await asyncio.sleep(1.0)  # 1 second between detail requests (~60 schools/min)
