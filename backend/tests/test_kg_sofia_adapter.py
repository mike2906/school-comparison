"""Tests for KgSofiaBgAdapter."""
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
from app.scrapers.sources import get_adapter, list_adapters


class TestKgSofiaBgAdapterRegistry:
    """Test adapter registration."""

    def test_adapter_registered(self):
        """KgSofiaBgAdapter is registered in the adapter registry."""
        adapter_class = get_adapter("kg_sofia_bg")
        assert adapter_class == KgSofiaBgAdapter

    def test_adapter_metadata(self):
        """Adapter has correct metadata."""
        assert KgSofiaBgAdapter.ADAPTER_NAME == "kg_sofia_bg"
        assert KgSofiaBgAdapter.COUNTRY_CODE == "bg"
        assert KgSofiaBgAdapter.CITY == "sofia"
        assert KgSofiaBgAdapter.RATE_LIMIT == "2/m"

    def test_adapter_listed(self):
        """Adapter appears in list_adapters()."""
        adapters = list_adapters()
        assert "kg_sofia_bg" in adapters
        assert adapters["kg_sofia_bg"]["country_code"] == "bg"
        assert adapters["kg_sofia_bg"]["city"] == "sofia"


class TestKgSofiaBgAdapterParsing:
    """Test HTML parsing logic."""

    def test_extract_district_from_address(self):
        """Extract district name from address string."""
        adapter = KgSofiaBgAdapter(db=AsyncMock())

        # Test known districts
        assert adapter._extract_district_from_address("ул. Иван Вазов 15, Средец") == "Средец"
        assert adapter._extract_district_from_address("бул. Витоша 100, Лозенец, София") == "Лозенец"
        assert adapter._extract_district_from_address("ж.к. Люлин 5") == "Люлин"

        # Test case-insensitive matching
        assert adapter._extract_district_from_address("КРАСНО СЕЛО") == "Красно село"

        # Test no match
        assert adapter._extract_district_from_address("ул. Неизвестна 1") is None

    def test_age_group_mapping(self):
        """Age group mapping is defined."""
        assert "яслена" in KgSofiaBgAdapter.AGE_GROUP_MAPPING
        assert KgSofiaBgAdapter.AGE_GROUP_MAPPING["яслена"] == "nursery"
        assert KgSofiaBgAdapter.AGE_GROUP_MAPPING["първа"] == "first"
        assert KgSofiaBgAdapter.AGE_GROUP_MAPPING["предучилищна"] == "preschool"

    def test_extract_name(self):
        """Extract kindergarten name from HTML."""
        from bs4 import BeautifulSoup

        adapter = KgSofiaBgAdapter(db=AsyncMock())

        # Test with h1 tag
        html = "<html><body><h1>Детска градина №1 Щастливо детство</h1></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        name = adapter._extract_name(soup)
        assert name == "ДГ №1 Щастливо детство"

        # Test with div.kg-name
        html = '<html><body><div class="kg-name">ДГ №5</div></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        name = adapter._extract_name(soup)
        assert name == "ДГ №5"

        # Test fallback to title
        html = "<html><head><title>ДГ Слънчице</title></head><body></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        name = adapter._extract_name(soup)
        assert name == "ДГ Слънчице"


@pytest.mark.asyncio
class TestKgSofiaBgAdapterIntegration:
    """Integration tests with mocked HTTP responses."""

    async def test_discover_with_mock_response(self, db_session: AsyncSession):
        """Test discover() with mocked HTTP responses."""
        adapter = KgSofiaBgAdapter(db=db_session)

        # Mock HTML responses
        list_page_html = """
        <html>
            <body>
                <a href="/web/kg/1">ДГ №1</a>
                <a href="/web/kg/2">ДГ №2</a>
            </body>
        </html>
        """

        detail_page_html = """
        <html>
            <body>
                <h1>Детска градина №1 Щастливо детство</h1>
                <div class="address">ул. Иван Вазов 15, Средец, София</div>
                <p>Телефон: 02/987-6543</p>
                <table>
                    <tr><th>Възрастова група</th></tr>
                    <tr><td>Първа група</td></tr>
                    <tr><td>Втора група</td></tr>
                </table>
            </body>
        </html>
        """

        # Mock httpx responses
        class MockResponse:
            def __init__(self, content):
                self.content = content.encode()
                self.status_code = 200

            def raise_for_status(self):
                pass

        # Create mock client that works as async context manager
        mock_get = AsyncMock(
            side_effect=[
                MockResponse(list_page_html),  # List page
                MockResponse(detail_page_html),  # First detail page
                MockResponse(detail_page_html),  # Second detail page
            ]
        )

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            schools = await adapter.discover(limit=2)

        # Verify we got schools back
        assert len(schools) > 0

        # Verify first school structure
        school = schools[0]
        assert school.name_i18n["bg"] == "ДГ №1 Щастливо детство"
        assert school.country_code == "bg"
        assert school.city == "sofia"
        assert school.school_type == "state"
        assert school.education_level == "kindergarten"
        assert len(school.locations) == 1

        # Verify location
        location = school.locations[0]
        assert "Иван Вазов" in location.address_i18n["bg"]
        assert location.district == "Средец"
        assert location.phone == "02/987-6543"
        assert location.is_primary is True

        # Verify age groups
        assert "first" in location.age_groups
        assert "second" in location.age_groups


@pytest.mark.asyncio
class TestKgSofiaBgAdapterUpsert:
    """Test upsert_schools() integration."""

    async def test_upsert_creates_new_school(self, db_session: AsyncSession):
        """Test that upsert creates a new school in the database."""
        from app.schemas.scraping import DiscoveredSchool, DiscoveredLocation

        adapter = KgSofiaBgAdapter(db=db_session)

        # Create a discovered school
        discovered_schools = [
            DiscoveredSchool(
                name_i18n={"bg": "ДГ №99 Тестова"},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="kindergarten",
                source_url="https://kg.sofia.bg/test/99",
                locations=[
                    DiscoveredLocation(
                        address_i18n={"bg": "ул. Тестова 1, Средец"},
                        district="Средец",
                        phone="02/123-4567",
                        is_primary=True,
                        age_groups=["first", "second"],
                        shifts={"first": "morning", "second": "full_day"},
                        has_organised_groups={"first": True, "second": True},
                    )
                ],
            )
        ]

        # Upsert
        result = await adapter.upsert_schools(discovered_schools)

        # Verify counts
        assert result["created"] == 1
        assert result["updated"] == 0
        assert result["skipped"] == 0

        # Verify school was created in DB
        from app.models import School, SchoolLocation
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        # Query by source_url with eager loading of locations
        db_result = await db_session.execute(
            select(School).options(selectinload(School.locations)).where(School.source_url == "https://kg.sofia.bg/test/99")
        )
        school = db_result.scalar_one_or_none()

        assert school is not None
        assert school.name_i18n["bg"] == "ДГ №99 Тестова"
        assert school.city == "sofia"
        assert school.school_type == "state"
        assert len(school.locations) == 1
        assert school.locations[0].district == "Средец"
