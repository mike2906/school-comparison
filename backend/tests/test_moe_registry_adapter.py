"""Tests for MoeRegistryAdapter."""
import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
from app.scrapers.sources import get_adapter, list_adapters


class TestMoeRegistryAdapterRegistry:
    """Test adapter registration."""

    def test_adapter_registered(self):
        """MoeRegistryAdapter is registered in the adapter registry."""
        adapter_class = get_adapter("moe_registry")
        assert adapter_class == MoeRegistryAdapter

    def test_adapter_metadata(self):
        """Adapter has correct metadata."""
        assert MoeRegistryAdapter.ADAPTER_NAME == "moe_registry"
        assert MoeRegistryAdapter.COUNTRY_CODE == "bg"
        assert MoeRegistryAdapter.CITY is None  # Country-wide
        assert MoeRegistryAdapter.RATE_LIMIT == "2/m"

    def test_adapter_listed(self):
        """Adapter appears in list_adapters()."""
        adapters = list_adapters()
        assert "moe_registry" in adapters
        assert adapters["moe_registry"]["country_code"] == "bg"
        assert adapters["moe_registry"]["city"] == "country-wide"


class TestMoeRegistryAdapterParsing:
    """Test parsing logic."""

    def test_determine_school_type_state(self):
        """Determine school type from text."""
        adapter = MoeRegistryAdapter(db=AsyncMock())

        assert adapter._determine_school_type("държавно училище") == "state"
        assert adapter._determine_school_type("общинско училище") == "state"
        assert adapter._determine_school_type("чуждоезиково училище") == "state"

    def test_determine_school_type_private(self):
        """Determine private school type."""
        adapter = MoeRegistryAdapter(db=AsyncMock())

        assert adapter._determine_school_type("частно училище") == "private"

    def test_determine_school_type_international(self):
        """Determine international school type."""
        adapter = MoeRegistryAdapter(db=AsyncMock())

        assert adapter._determine_school_type("международно училище") == "international"

    def test_determine_education_level_from_name(self):
        """Determine education level from school name."""
        adapter = MoeRegistryAdapter(db=AsyncMock())

        # Primary schools (НУ)
        assert adapter._determine_education_level("НУ Христо Ботев", "") == "primary"
        assert adapter._determine_education_level("Начално училище Иван Вазов", "") == "primary"

        # Lower secondary (ОУ - grades 1-8)
        assert adapter._determine_education_level("ОУ Отец Паисий", "") == "lower_secondary"
        assert adapter._determine_education_level("134 ОУ Иван Вазов", "") == "lower_secondary"
        assert adapter._determine_education_level("Основно училище Христо Ботев", "") == "lower_secondary"

        # Upper secondary (СУ, gymnasiums)
        assert adapter._determine_education_level("СУ Неофит Рилски", "") == "upper_secondary"
        assert adapter._determine_education_level("23 СУ Фредерик Жолио-Кюри", "") == "upper_secondary"
        assert adapter._determine_education_level("ПГМЕТ Джон Атанасов", "") == "upper_secondary"
        assert adapter._determine_education_level("Професионална гимназия по икономика", "") == "upper_secondary"

    def test_extract_city_from_address(self):
        """Extract city from address."""
        adapter = MoeRegistryAdapter(db=AsyncMock())

        # Sofia patterns
        assert adapter._extract_city_from_address("гр. София, ул. Иван Вазов 15") == "sofia"
        assert adapter._extract_city_from_address("ул. Граф Игнатиев 20, София") == "sofia"
        assert adapter._extract_city_from_address("София 1000, бул. Витоша 100") == "sofia"

        # No city
        assert adapter._extract_city_from_address("ул. Неизвестна 1") is None

    def test_extract_district_from_address(self):
        """Extract district from address."""
        adapter = MoeRegistryAdapter(db=AsyncMock())

        # Uses KgSofiaBgAdapter's district mapping
        assert adapter._extract_district_from_address("ул. Иван Вазов 15, Средец") == "Средец"
        assert adapter._extract_district_from_address("бул. Витоша 100, Лозенец, София") == "Лозенец"

        # No district
        assert adapter._extract_district_from_address("ул. Неизвестна 1") is None


class TestMoeRegistryAdapterSchoolTypeMappings:
    """Test school type and education level mappings."""

    def test_school_type_mapping_complete(self):
        """All school type keywords are mapped."""
        assert len(MoeRegistryAdapter.SCHOOL_TYPE_MAPPING) >= 4
        assert "държавно" in MoeRegistryAdapter.SCHOOL_TYPE_MAPPING
        assert "частно" in MoeRegistryAdapter.SCHOOL_TYPE_MAPPING

    def test_education_level_mapping_complete(self):
        """All education level keywords are mapped."""
        assert len(MoeRegistryAdapter.EDUCATION_LEVEL_MAPPING) >= 5
        assert "начално" in MoeRegistryAdapter.EDUCATION_LEVEL_MAPPING
        assert "гимназия" in MoeRegistryAdapter.EDUCATION_LEVEL_MAPPING


@pytest.mark.asyncio
class TestMoeRegistryAdapterIntegration:
    """Integration tests with mocked HTTP responses."""

    async def test_discover_with_mock_response(self, db_session: AsyncSession):
        """Test discover() with mocked HTTP responses."""
        adapter = MoeRegistryAdapter(db=db_session)

        # Mock HTML response (simplified registry table)
        registry_html = """
        <html>
            <body>
                <table>
                    <tr class="school-row">
                        <td>12345</td>
                        <td>23 СУ Фредерик Жолио-Кюри</td>
                        <td>Държавно средно училище</td>
                        <td>гр. София, ул. Сан Стефано 40, Изгрев</td>
                    </tr>
                    <tr class="school-row">
                        <td>67890</td>
                        <td>134 ОУ Иван Вазов</td>
                        <td>Държавно основно училище</td>
                        <td>гр. София, ул. Граф Игнатиев 20, Средец</td>
                    </tr>
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

        # Create mock client
        mock_get = AsyncMock(return_value=MockResponse(registry_html))

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            schools = await adapter.discover(limit=2)

        # Verify we got schools back
        assert len(schools) == 2

        # Verify first school (СУ - upper secondary)
        school1 = schools[0]
        assert school1.institutional_id == "12345"
        assert "Жолио-Кюри" in school1.name_i18n["bg"]
        assert school1.country_code == "bg"
        assert school1.city == "sofia"
        assert school1.school_type == "state"
        assert school1.education_level == "upper_secondary"
        assert len(school1.locations) == 1
        assert school1.locations[0].district == "Изгрев"

        # Verify second school (ОУ - lower secondary)
        school2 = schools[1]
        assert school2.institutional_id == "67890"
        assert "Иван Вазов" in school2.name_i18n["bg"]
        assert school2.education_level == "lower_secondary"
        assert school2.locations[0].district == "Средец"


@pytest.mark.asyncio
class TestMoeRegistryAdapterUpsert:
    """Test upsert_schools() with institutional_id matching."""

    async def test_upsert_with_institutional_id(self, db_session: AsyncSession):
        """Test that upsert matches by institutional_id (primary key)."""
        from app.schemas.scraping import DiscoveredSchool, DiscoveredLocation

        adapter = MoeRegistryAdapter(db=db_session)

        # Create a discovered school WITH institutional_id
        discovered_schools = [
            DiscoveredSchool(
                institutional_id="TEST-123",
                name_i18n={"bg": "23 СУ Фредерик Жолио-Кюри"},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="upper_secondary",
                source_url="https://mon.bg/schools/TEST-123",
                locations=[
                    DiscoveredLocation(
                        address_i18n={"bg": "ул. Сан Стефано 40, Изгрев"},
                        district="Изгрев",
                        is_primary=True,
                        age_groups=[],
                    )
                ],
            )
        ]

        # Upsert first time (should create)
        result = await adapter.upsert_schools(discovered_schools)
        assert result["created"] == 1

        # Upsert again with same institutional_id (should update, not create)
        discovered_schools[0].name_i18n["bg"] = "23 СУ Фредерик Жолио-Кюри (обновено)"
        result = await adapter.upsert_schools(discovered_schools)
        assert result["created"] == 0
        assert result["updated"] == 1

        # Verify school in DB
        from app.models import School
        from sqlalchemy import select

        db_result = await db_session.execute(
            select(School).where(School.institutional_id == "TEST-123")
        )
        school = db_result.scalar_one_or_none()

        assert school is not None
        assert "(обновено)" in school.name_i18n["bg"]
        assert school.city == "sofia"
        assert school.education_level == "upper_secondary"
