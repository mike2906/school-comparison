"""
KgSofiaBgAdapter - Discover kindergartens and schools from kg.sofia.bg

This adapter uses the Sofia Municipality's API to discover kindergartens and schools
with preparatory groups, including addresses and contact information.

For detailed API documentation, see:
backend/docs/KG_SOFIA_API_DOCUMENTATION.md
"""
import logging
from typing import Optional
import httpx

from app.scrapers.sources.base_adapter import BaseSourceAdapter
from app.schemas.scraping import DiscoveredSchool, DiscoveredLocation
from app.scrapers.sources import register_adapter

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

    # kg.sofia.bg API endpoints
    API_BASE_URL = "https://kg.sofia.bg/api/public"
    KINDERGARTENS_URL = f"{API_BASE_URL}/kg/type/kinderGarden/all"
    SCHOOLS_URL = f"{API_BASE_URL}/kg/type/school/all"
    PREPARATORY_URL = f"{API_BASE_URL}/kg/type/preparative/all"
    REGIONS_URL = f"{API_BASE_URL}/regions/all"

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

    async def discover(self, limit: Optional[int] = None) -> list[DiscoveredSchool]:
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

        Returns:
            List of DiscoveredSchool objects

        Raises:
            httpx.HTTPError: If the API is unreachable
            ValueError: If the response format is unexpected
        """
        discovered_schools = []

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
                school = self._parse_institution(kg, "kindergarten")
                if school:
                    discovered_schools.append(school)

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
                school_obj = self._parse_institution(school, "school")
                if school_obj:
                    discovered_schools.append(school_obj)

            # Apply limit if specified
            if limit:
                discovered_schools = discovered_schools[:limit]

        logger.info(f"Discovered {len(discovered_schools)} institutions from kg.sofia.bg")
        return discovered_schools

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

            # Build location
            location = DiscoveredLocation(
                address_i18n={"bg": address_bg},
                district=district,
                phone=phone,
                is_primary=True,
                age_groups=[],  # Not provided by this API
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
                    "kg_sofia_id": data.get("id"),
                    "kg_sofia_esri_id": esri_id,
                    "kg_sofia_public_type": public_type,
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
