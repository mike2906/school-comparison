"""
MoeRegistryAdapter - Discover schools from the Ministry of Education registry

This adapter scrapes the Bulgarian Ministry of Education's school registry
to discover primary and secondary schools with their official institutional IDs.
"""
import logging
from typing import Optional
import httpx
from bs4 import BeautifulSoup

from app.scrapers.sources.base_adapter import BaseSourceAdapter
from app.schemas.scraping import DiscoveredSchool, DiscoveredLocation
from app.scrapers.sources import register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class MoeRegistryAdapter(BaseSourceAdapter):
    """
    Discover primary and secondary schools from the Ministry of Education registry.

    The Bulgarian Ministry of Education maintains a public registry of all
    registered schools with their institutional codes (Код по НЕИСПУО).

    Data extracted:
    - Institutional ID (Код по НЕИСПУО) - CRITICAL for idempotency
    - School name (Bulgarian)
    - School type (state/private/international)
    - Education level (primary/lower_secondary/upper_secondary)
    - Address
    - District (when available)
    - Phone
    """

    # Adapter metadata
    ADAPTER_NAME = "moe_registry"
    COUNTRY_CODE = "bg"
    CITY = None  # Country-wide registry, can filter by city in post-processing
    DESCRIPTION = "Bulgarian schools from Ministry of Education registry"
    RATE_LIMIT = "2/m"  # Respect government server

    # Ministry of Education registry endpoints
    BASE_URL = "https://admin.mon.bg"  # Placeholder - actual URL may differ
    SCHOOLS_SEARCH_URL = f"{BASE_URL}/schools/search"  # Placeholder

    # School type mapping (Bulgarian terms → our schema)
    SCHOOL_TYPE_MAPPING = {
        "държавно": "state",
        "общинско": "state",
        "частно": "private",
        "международно": "international",
        "чуждоезиково": "state",  # Foreign language schools are state-run
    }

    # Education level mapping
    EDUCATION_LEVEL_MAPPING = {
        "начално": "primary",
        "прогимназия": "lower_secondary",
        "основно": "lower_secondary",  # ОУ covers grades 1-8
        "гимназия": "upper_secondary",
        "средно": "upper_secondary",
        "професионална": "upper_secondary",
    }

    async def discover(self, limit: Optional[int] = None) -> list[DiscoveredSchool]:
        """
        Discover schools from the Ministry of Education registry.

        Process:
        1. Search/list schools in the registry (may need pagination)
        2. For each school, extract institutional ID and basic info
        3. Optionally fetch detail pages for full information
        4. Return DiscoveredSchool objects

        Args:
            limit: Optional limit on number of schools to discover (for testing)

        Returns:
            List of DiscoveredSchool objects

        Raises:
            httpx.HTTPError: If the registry is unreachable
            ValueError: If the data format is unexpected
        """
        discovered_schools = []

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # Step 1: Fetch school list/search page
            logger.info(f"Fetching school registry from {self.SCHOOLS_SEARCH_URL}")

            # The actual implementation will depend on how the MoE registry works:
            # Option A: Single page with all schools (unlikely)
            # Option B: Paginated list
            # Option C: Search form that needs to be submitted
            # Option D: Downloadable CSV/Excel file
            # Option E: API endpoint (best case)

            # Placeholder: Assume we can get a list of schools
            schools_data = await self._fetch_school_list(client)

            if limit:
                schools_data = schools_data[:limit]

            logger.info(f"Found {len(schools_data)} schools to process")

            # Step 2: Process each school
            for idx, school_data in enumerate(schools_data, 1):
                try:
                    logger.info(f"Processing school {idx}/{len(schools_data)}")

                    # Parse school data
                    school = self._parse_school_data(school_data)

                    if school:
                        discovered_schools.append(school)

                    # Rate limiting
                    await self._delay()

                except Exception as e:
                    logger.error(f"Error processing school: {e}")
                    continue

        logger.info(f"Discovered {len(discovered_schools)} schools from MoE registry")
        return discovered_schools

    async def _fetch_school_list(self, client: httpx.AsyncClient) -> list[dict]:
        """
        Fetch the list of schools from the registry.

        This is a placeholder - the actual implementation depends on how
        the MoE registry exposes data.

        Args:
            client: HTTP client

        Returns:
            List of school data dictionaries

        Note:
            Real implementation options:
            1. Scrape HTML list pages with pagination
            2. Submit search forms to get results
            3. Download CSV/Excel and parse
            4. Call an API endpoint (if available)
            5. Scrape regional/district pages separately
        """
        schools = []

        # Placeholder: Assume we're scraping an HTML list
        response = await client.get(self.SCHOOLS_SEARCH_URL)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, "html.parser")

        # Extract school entries from the page
        # Common patterns:
        # - Table with rows for each school
        # - List items with school info
        # - Search results with links to detail pages

        # Example: Find all school rows in a table
        for row in soup.find_all("tr", class_="school-row"):  # Placeholder selector
            school_data = self._extract_school_from_row(row)
            if school_data:
                schools.append(school_data)

        return schools

    def _extract_school_from_row(self, row) -> Optional[dict]:
        """
        Extract school data from a table row or list item.

        Args:
            row: BeautifulSoup element (tr, li, div, etc.)

        Returns:
            Dict with school data or None if parsing fails

        Note:
            This is a placeholder - the actual structure depends on the HTML.
        """
        try:
            # Example extraction (adjust selectors based on actual HTML)
            cells = row.find_all("td")

            if len(cells) < 4:
                return None

            return {
                "institutional_id": cells[0].get_text(strip=True),
                "name": cells[1].get_text(strip=True),
                "type": cells[2].get_text(strip=True),
                "address": cells[3].get_text(strip=True),
            }

        except Exception as e:
            logger.error(f"Error extracting school from row: {e}")
            return None

    def _parse_school_data(self, data: dict) -> Optional[DiscoveredSchool]:
        """
        Parse raw school data into a DiscoveredSchool object.

        Args:
            data: Dict with raw school data from the registry

        Returns:
            DiscoveredSchool object or None if parsing fails
        """
        try:
            # Extract institutional ID (CRITICAL for idempotency)
            institutional_id = data.get("institutional_id")

            # Extract name
            name_bg = data.get("name", "").strip()
            if not name_bg:
                logger.warning("School has no name, skipping")
                return None

            # Determine school type
            type_text = data.get("type", "").lower()
            school_type = self._determine_school_type(type_text)

            # Determine education level
            education_level = self._determine_education_level(name_bg, type_text)

            # Extract address
            address_bg = data.get("address", "").strip()

            # Try to extract city and district from address
            city = self._extract_city_from_address(address_bg)
            district = self._extract_district_from_address(address_bg)

            # Build location
            locations = []
            if address_bg:
                location = DiscoveredLocation(
                    address_i18n={"bg": address_bg},
                    district=district,
                    phone=data.get("phone"),
                    is_primary=True,
                    age_groups=[],  # Will be populated later or from other sources
                )
                locations.append(location)

            # Build school
            return DiscoveredSchool(
                institutional_id=institutional_id,
                name_i18n={"bg": name_bg},
                country_code="bg",
                city=city,
                school_type=school_type,
                education_level=education_level,
                source_url=data.get("detail_url"),
                locations=locations,
                attributes=data.get("attributes", {}),
            )

        except Exception as e:
            logger.error(f"Error parsing school data: {e}")
            return None

    def _determine_school_type(self, type_text: str) -> str:
        """
        Determine school type from registry text.

        Args:
            type_text: School type text from registry

        Returns:
            School type: "state", "private", or "international"
        """
        for keyword, school_type in self.SCHOOL_TYPE_MAPPING.items():
            if keyword in type_text:
                return school_type

        # Default to state if unclear
        logger.warning(f"Unknown school type '{type_text}', defaulting to 'state'")
        return "state"

    def _determine_education_level(self, name: str, type_text: str) -> str:
        """
        Determine education level from school name and type.

        Args:
            name: School name
            type_text: School type text

        Returns:
            Education level: "primary", "lower_secondary", or "upper_secondary"

        Note:
            Bulgarian school naming conventions:
            - ОУ (Основно училище) = grades 1-8 → lower_secondary
            - СУ (Средно училище) = grades 8-12 → upper_secondary
            - НУ (Начално училище) = grades 1-4 → primary
            - ПГ (Професионална гимназия) = vocational high school → upper_secondary
            - ПГМЕТ, ПГЕЕ, etc. = specialized vocational schools → upper_secondary
        """
        name_lower = name.lower()
        type_lower = type_text.lower()

        # Check name abbreviations
        if " оу " in name_lower or name_lower.startswith("оу ") or "основно" in name_lower:
            return "lower_secondary"

        if " су " in name_lower or name_lower.startswith("су ") or "средно" in name_lower:
            return "upper_secondary"

        if " ну " in name_lower or name_lower.startswith("ну ") or "начално" in name_lower:
            return "primary"

        if "пг" in name_lower or "гимназия" in name_lower or "професионална" in name_lower:
            return "upper_secondary"

        # Check type text for keywords
        for keyword, level in self.EDUCATION_LEVEL_MAPPING.items():
            if keyword in type_lower:
                return level

        # Default based on common patterns
        logger.warning(f"Could not determine education level for '{name}', defaulting to 'lower_secondary'")
        return "lower_secondary"

    def _extract_city_from_address(self, address: str) -> Optional[str]:
        """
        Extract city name from address.

        Args:
            address: Full address string

        Returns:
            City name (lowercase) or None

        Note:
            Common patterns in Bulgarian addresses:
            - "гр. София" or "град София"
            - "София, ул. ..."
            - Address ending with ", София"
        """
        if not address:
            return None

        address_lower = address.lower()

        # Check for Sofia
        if "софия" in address_lower:
            return "sofia"

        # Could add other cities here
        # "пловдив" → "plovdiv"
        # "варна" → "varna"
        # etc.

        return None

    def _extract_district_from_address(self, address: str) -> Optional[str]:
        """
        Extract district from address.

        Args:
            address: Full address string

        Returns:
            District name or None

        Note:
            Uses the same district mapping as KgSofiaBgAdapter
        """
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter

        if not address:
            return None

        address_lower = address.lower()

        for district_key, district_name in KgSofiaBgAdapter.DISTRICT_MAPPING.items():
            if district_key in address_lower:
                return district_name

        return None

    async def _delay(self):
        """Small delay between requests to avoid overwhelming the server."""
        import asyncio
        from app.config import get_settings

        settings = get_settings()
        await asyncio.sleep(settings.scrape_delay_seconds)
