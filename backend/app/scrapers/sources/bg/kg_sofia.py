"""
KgSofiaBgAdapter - Discover kindergartens from kg.sofia.bg

This adapter scrapes the official Sofia kindergarten portal (kg.sofia.bg)
to discover state kindergartens with their locations, age groups, and admission data.
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
class KgSofiaBgAdapter(BaseSourceAdapter):
    """
    Discover state kindergartens from kg.sofia.bg.

    The Sofia municipality maintains a public registry of all state kindergartens
    with enrollment information, locations, and admission thresholds.

    Data extracted:
    - School name (Bulgarian)
    - Address(es) with districts
    - Age groups offered (nursery, first, second, third, preschool)
    - Phone numbers
    - Historical admission points thresholds (stored in admission_info)
    """

    # Adapter metadata
    ADAPTER_NAME = "kg_sofia_bg"
    COUNTRY_CODE = "bg"
    CITY = "sofia"
    DESCRIPTION = "Sofia state kindergartens from kg.sofia.bg"
    RATE_LIMIT = "2/m"  # Respect government server

    # kg.sofia.bg endpoints
    BASE_URL = "https://kg.sofia.bg"
    KINDERGARTENS_LIST_URL = f"{BASE_URL}/web/guest/83"  # Main kindergarten list page

    # Age group mapping (kg.sofia.bg terminology → our schema)
    AGE_GROUP_MAPPING = {
        "яслена": "nursery",
        "първа": "first",
        "втора": "second",
        "трета": "third",
        "предучилищна": "preschool",
    }

    # District name normalization (Cyrillic → standardized)
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

    async def discover(self, limit: Optional[int] = None) -> list[DiscoveredSchool]:
        """
        Discover kindergartens from kg.sofia.bg.

        Process:
        1. Fetch the main kindergarten list page
        2. Extract links to individual kindergarten detail pages
        3. For each kindergarten, fetch its detail page
        4. Parse: name, address, district, age groups, admission thresholds
        5. Return DiscoveredSchool objects

        Args:
            limit: Optional limit on number of kindergartens to discover (for testing)

        Returns:
            List of DiscoveredSchool objects

        Raises:
            httpx.HTTPError: If kg.sofia.bg is unreachable
            ValueError: If the page structure is unexpected
        """
        discovered_schools = []

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            # Step 1: Fetch kindergarten list page
            logger.info(f"Fetching kindergarten list from {self.KINDERGARTENS_LIST_URL}")
            response = await client.get(self.KINDERGARTENS_LIST_URL)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, "html.parser")

            # Step 2: Extract kindergarten detail page links
            # NOTE: This selector is a placeholder - will need to be adjusted based on actual HTML structure
            kg_links = self._extract_kindergarten_links(soup)

            if limit:
                kg_links = kg_links[:limit]

            logger.info(f"Found {len(kg_links)} kindergartens to process")

            # Step 3: Process each kindergarten
            for idx, (kg_id, kg_url) in enumerate(kg_links, 1):
                try:
                    logger.info(f"Processing kindergarten {idx}/{len(kg_links)}: {kg_url}")

                    # Fetch detail page
                    detail_response = await client.get(kg_url)
                    detail_response.raise_for_status()

                    detail_soup = BeautifulSoup(detail_response.content, "html.parser")

                    # Parse kindergarten data
                    school_data = self._parse_kindergarten_page(kg_id, kg_url, detail_soup)

                    if school_data:
                        discovered_schools.append(school_data)

                    # Rate limiting (handled by Celery task-level rate limit, but add small delay)
                    await self._delay()

                except Exception as e:
                    logger.error(f"Error processing kindergarten {kg_url}: {e}")
                    continue

        logger.info(f"Discovered {len(discovered_schools)} kindergartens from kg.sofia.bg")
        return discovered_schools

    def _extract_kindergarten_links(self, soup: BeautifulSoup) -> list[tuple[str, str]]:
        """
        Extract kindergarten detail page links from the list page.

        Args:
            soup: Parsed HTML of the kindergarten list page

        Returns:
            List of (kindergarten_id, detail_url) tuples

        Note:
            This is a placeholder implementation. The actual selectors will depend
            on the HTML structure of kg.sofia.bg. Common patterns:
            - Links in a table with class="kg-list"
            - Links with href containing "/kindergarten/" or "kg_id="
            - JSON data embedded in a script tag
        """
        links = []

        # Placeholder: Look for links that might be kindergarten detail pages
        # Real implementation will need to inspect the actual HTML structure
        for link in soup.find_all("a", href=True):
            href = link["href"]
            # Example pattern: href="/web/kg/123" or "?kg_id=123"
            if "/kg/" in href or "kg_id=" in href:
                full_url = href if href.startswith("http") else f"{self.BASE_URL}{href}"
                # Extract ID from URL (rough heuristic)
                kg_id = href.split("/")[-1] if "/" in href else href.split("=")[-1]
                links.append((kg_id, full_url))

        # Fallback: If no links found, log a warning
        if not links:
            logger.warning("No kindergarten links found - HTML structure may have changed")

        return links

    def _parse_kindergarten_page(
        self, kg_id: str, source_url: str, soup: BeautifulSoup
    ) -> Optional[DiscoveredSchool]:
        """
        Parse a kindergarten detail page.

        Args:
            kg_id: Kindergarten ID from the URL
            source_url: URL of the detail page
            soup: Parsed HTML of the detail page

        Returns:
            DiscoveredSchool object or None if parsing fails

        Note:
            This is a placeholder implementation. The actual parsing logic will depend
            on the HTML structure of kg.sofia.bg kindergarten detail pages.

            Common data locations:
            - Name: <h1> or <div class="kg-name">
            - Address: <div class="kg-address"> or in a "Контакти" section
            - District: Often part of the address or in metadata
            - Age groups: Table or list with "Възрастови групи"
            - Admission points: Table with historical thresholds per year/group
        """
        try:
            # Extract name
            name_bg = self._extract_name(soup)
            if not name_bg:
                logger.warning(f"Could not extract name for kindergarten {kg_id}")
                return None

            # Extract location data
            location_data = self._extract_location(soup)

            # Extract age groups (may be per-location or school-wide)
            age_groups_data = self._extract_age_groups(soup)

            # Extract admission info (historical thresholds)
            admission_info = self._extract_admission_info(soup)

            # Build DiscoveredLocation
            locations = []
            if location_data:
                discovered_location = DiscoveredLocation(
                    address_i18n={"bg": location_data["address"]},
                    district=location_data.get("district"),
                    phone=location_data.get("phone"),
                    is_primary=True,
                    age_groups=age_groups_data.get("age_groups", []),
                    shifts=age_groups_data.get("shifts", {}),
                    has_organised_groups=age_groups_data.get("organised_groups", {}),
                )
                locations.append(discovered_location)

            # Build DiscoveredSchool
            return DiscoveredSchool(
                institutional_id=None,  # kg.sofia.bg may not provide MoE institutional ID
                name_i18n={"bg": name_bg},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="kindergarten",
                source_url=source_url,
                locations=locations,
                admission_info=admission_info,
            )

        except Exception as e:
            logger.error(f"Error parsing kindergarten page {kg_id}: {e}")
            return None

    def _extract_name(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract kindergarten name from the page."""
        # Try common selectors
        name_tag = soup.find("h1") or soup.find("div", class_="kg-name") or soup.find("title")

        if name_tag:
            name = name_tag.get_text(strip=True)
            # Clean up common prefixes/suffixes
            name = name.replace("Детска градина", "ДГ").strip()
            return name

        return None

    def _extract_location(self, soup: BeautifulSoup) -> Optional[dict]:
        """Extract address, district, phone from the page."""
        location = {}

        # Try to find address
        # Common patterns: <div class="address">, <span>Адрес:</span>, etc.
        address_tag = soup.find("div", class_="address") or soup.find(string=lambda t: t and "адрес" in t.lower())

        if address_tag:
            if isinstance(address_tag, str):
                # Found text containing "адрес", get the next sibling or parent
                parent = address_tag.parent
                address_text = parent.get_text(strip=True) if parent else ""
            else:
                address_text = address_tag.get_text(strip=True)

            location["address"] = address_text

            # Try to extract district from address
            district = self._extract_district_from_address(address_text)
            if district:
                location["district"] = district

        # Try to find phone
        phone_tag = soup.find(string=lambda t: t and "телефон" in t.lower())
        if phone_tag:
            parent = phone_tag.parent
            phone_text = parent.get_text(strip=True) if parent else ""
            # Extract phone number pattern (e.g., 02/123-4567)
            import re
            phone_match = re.search(r"0\d{1}/\d{3}-?\d{4}", phone_text)
            if phone_match:
                location["phone"] = phone_match.group(0)

        return location if location else None

    def _extract_district_from_address(self, address: str) -> Optional[str]:
        """
        Extract district name from address string.

        Looks for known Sofia district names in the address.
        """
        address_lower = address.lower()
        for district_key, district_name in self.DISTRICT_MAPPING.items():
            if district_key in address_lower:
                return district_name
        return None

    def _extract_age_groups(self, soup: BeautifulSoup) -> dict:
        """
        Extract age groups and shift information.

        Returns:
            Dict with:
            - age_groups: list[str]
            - shifts: dict[str, str]
            - organised_groups: dict[str, bool]
        """
        result = {"age_groups": [], "shifts": {}, "organised_groups": {}}

        # Look for age group table or list
        # Common patterns: table with "Възрастова група" column
        age_group_section = soup.find(string=lambda t: t and "възрастов" in t.lower())

        if age_group_section:
            # Find parent table or list
            parent = age_group_section.find_parent(["table", "ul", "div"])
            if parent:
                # Extract age group text
                for row in parent.find_all("tr"):
                    cells = row.find_all(["td", "th"])
                    if len(cells) >= 1:
                        cell_text = cells[0].get_text(strip=True).lower()
                        # Map Bulgarian age group names to our schema
                        for bg_name, schema_name in self.AGE_GROUP_MAPPING.items():
                            if bg_name in cell_text:
                                result["age_groups"].append(schema_name)
                                # Default to full_day for state kindergartens
                                result["shifts"][schema_name] = "full_day"
                                # Most state KGs have organised groups (after-school care)
                                result["organised_groups"][schema_name] = True

        return result

    def _extract_admission_info(self, soup: BeautifulSoup) -> dict:
        """
        Extract admission points thresholds from historical data.

        kg.sofia.bg often publishes historical admission thresholds showing
        the minimum points needed to get into each age group in past years.

        Returns:
            Dict with historical_thresholds structure
        """
        admission_info = {"system": "points", "historical_thresholds": []}

        # Look for admission threshold tables
        # Common patterns: table with "Прием" or "Бал" in the header
        threshold_section = soup.find(string=lambda t: t and ("прием" in t.lower() or "бал" in t.lower()))

        if threshold_section:
            # This would parse the threshold table
            # Format: year, age_group, rounds: [{round, last_admitted_points, admitted_count}]
            # Placeholder for now - actual parsing depends on HTML structure
            pass

        return admission_info

    async def _delay(self):
        """Small delay between requests to avoid overwhelming the server."""
        import asyncio
        from app.config import get_settings

        settings = get_settings()
        await asyncio.sleep(settings.scrape_delay_seconds)
