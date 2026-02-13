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
        assert MoeRegistryAdapter.RATE_LIMIT == "60/m"

    def test_adapter_listed(self):
        """Adapter appears in list_adapters()."""
        adapters = list_adapters()
        assert "moe_registry" in adapters
        assert adapters["moe_registry"]["country_code"] == "bg"
        assert adapters["moe_registry"]["city"] == "country-wide"


class TestMoeRegistryAdapterMappings:
    """Test field mappings."""

    def test_financial_type_mapping_complete(self):
        """All financial types are mapped."""
        assert len(MoeRegistryAdapter.FINANCIAL_TYPE_MAPPING) >= 4
        assert MoeRegistryAdapter.FINANCIAL_TYPE_MAPPING[1] == "state"  # Държавно
        assert MoeRegistryAdapter.FINANCIAL_TYPE_MAPPING[2] == "state"  # Общинско
        assert MoeRegistryAdapter.FINANCIAL_TYPE_MAPPING[3] == "private"  # Частно
        assert MoeRegistryAdapter.FINANCIAL_TYPE_MAPPING[12] == "international"

    def test_detailed_type_mapping_complete(self):
        """All detailed school types are mapped."""
        assert len(MoeRegistryAdapter.DETAILED_TYPE_MAPPING) >= 10
        assert MoeRegistryAdapter.DETAILED_TYPE_MAPPING[121] == "primary"
        assert MoeRegistryAdapter.DETAILED_TYPE_MAPPING[122] == "lower_secondary"
        assert MoeRegistryAdapter.DETAILED_TYPE_MAPPING[125] == "upper_secondary"
        assert MoeRegistryAdapter.DETAILED_TYPE_MAPPING[151] == "kindergarten"


@pytest.mark.asyncio
class TestMoeRegistryAdapterIntegration:
    """Integration tests with mocked API responses."""

    async def test_discover_with_mock_api_response(self, db_session: AsyncSession):
        """Test discover() with mocked API JSON responses."""
        adapter = MoeRegistryAdapter(db=db_session)

        # Mock API response for public-register endpoint
        public_register_response = {
            "status": 1,
            "data": {
                "publicInstitutions": [
                    {
                        "id": 2200015,
                        "instid": 2200015,
                        "name": '"ЧАСТНО ОСНОВНО УЧИЛИЩЕ "Проф. д-р Васил Златарски" ЕООД',
                        "region": 22,
                        "municipality": 220,
                        "town": 68134,
                        "instType": 1,
                        "detailedSchoolType": 122,
                        "financialSchoolType": 3,
                        "transformType": 5,
                        "instKind": 3,
                        "formName": "institution",
                        "procID": 9642,
                    },
                    {
                        "id": 2200016,
                        "instid": 2200016,
                        "name": '"ЧАСТНА ДЕТСКА ГРАДИНА СЛЪНЧЕВ АНГЕЛ" ЕООД',
                        "region": 22,
                        "municipality": 220,
                        "town": 68134,
                        "instType": 2,
                        "detailedSchoolType": 151,
                        "financialSchoolType": 3,
                        "transformType": 5,
                        "instKind": 7,
                        "formName": "institution",
                        "procID": 9643,
                    },
                ]
            },
        }

        # Mock API response for institution detail endpoint
        detail_response_school = {
            "status": 1,
            "data": [
                {
                    "riInstitutionID": 9642,
                    "codeNEISPUO": 2200015,
                    "name": '"ЧАСТНО ОСНОВНО УЧИЛИЩЕ "Проф. д-р Васил Златарски" ЕООД',
                    "abbreviation": 'ЧАСТНО ОСНОВНО УЧИЛИЩЕ "Проф. д-р Васил Златарски"',
                    "bulstat": "204959296",
                    "settlementAddress": 'район Студетски, бул. "Св. Климент Охридски" № 49',
                    "settlementPostCode": 1756,
                    "headFirstName": "Цветанка",
                    "headLastName": "Кардашева",
                    "staffDirector": "Цветанка Стефанова Кардашева",
                    "email": "info@zlatarskischool.org",
                    "website": "zlatarskischool.org",
                    "phoneNumber": "02/8766767",
                    "institutionDepartments": [
                        {
                            "id": 14186,
                            "departmentAddress": 'район Студетски, бул. "Св. Климент Охридски" № 49',
                            "departmentPostalCode": 1756,
                        }
                    ],
                }
            ],
        }

        detail_response_kindergarten = {
            "status": 1,
            "data": [
                {
                    "riInstitutionID": 9643,
                    "codeNEISPUO": 2200016,
                    "name": '"ЧАСТНА ДЕТСКА ГРАДИНА СЛЪНЧЕВ АНГЕЛ" ЕООД',
                    "abbreviation": 'ЧАСТНА ДЕТСКА ГРАДИНА "СЛЪНЧЕВ АНГЕЛ"',
                    "bulstat": "204679814",
                    "settlementAddress": 'район Витоша, ж. к. Симеоново, ул. "Шумако" № 29 (нов № 47)',
                    "settlementPostCode": 1434,
                    "headFirstName": "Михаела",
                    "headLastName": "Михайлова-Ботева",
                    "staffDirector": "Михаела Иванова Михайлова-Ботева",
                    "email": "office@sunnyangel.bg",
                    "website": "",
                    "phoneNumber": "0889828647",
                    "institutionDepartments": [],
                }
            ],
        }

        # Mock httpx responses
        class MockResponse:
            def __init__(self, json_data):
                self._json_data = json_data
                self.status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return self._json_data

        # Track which detail request is being made
        detail_call_count = [0]

        async def mock_post(url, **kwargs):
            if "public-register" in url:
                return MockResponse(public_register_response)
            elif "institution" in url:
                # Return detail for first school, then second
                detail_call_count[0] += 1
                if detail_call_count[0] == 1:
                    return MockResponse(detail_response_school)
                else:
                    return MockResponse(detail_response_kindergarten)

        # Create mock client
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = mock_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            schools = await adapter.discover(limit=2, fetch_details=True)

        # Verify we got schools back
        assert len(schools) == 2

        # Verify first school (ОУ - lower secondary)
        school1 = schools[0]
        assert school1.institutional_id == "2200015"
        assert "Златарски" in school1.name_i18n["bg"]
        assert school1.country_code == "bg"
        assert school1.city == "sofia"
        assert school1.school_type == "private"
        assert school1.education_level == "lower_secondary"
        assert school1.website_url == "zlatarskischool.org"
        assert len(school1.locations) == 1
        assert "Климент Охридски" in school1.locations[0].address_i18n["bg"]
        assert school1.locations[0].phone == "02/8766767"
        assert school1.attributes["moe_email"] == "info@zlatarskischool.org"

        # Verify second school (ДГ - kindergarten)
        school2 = schools[1]
        assert school2.institutional_id == "2200016"
        assert "СЛЪНЧЕВ АНГЕЛ" in school2.name_i18n["bg"]
        assert school2.education_level == "kindergarten"
        assert school2.school_type == "private"
        assert len(school2.locations) == 1
        assert "Шумако" in school2.locations[0].address_i18n["bg"]
        assert school2.locations[0].phone == "0889828647"

    async def test_discover_without_details(self, db_session: AsyncSession):
        """Test discover() without fetching detail data (fast mode)."""
        adapter = MoeRegistryAdapter(db=db_session)

        # Mock only public-register response
        public_register_response = {
            "status": 1,
            "data": {
                "publicInstitutions": [
                    {
                        "id": 2200015,
                        "instid": 2200015,
                        "name": "Test School",
                        "region": 22,
                        "municipality": 220,
                        "town": 68134,
                        "instType": 1,
                        "detailedSchoolType": 122,
                        "financialSchoolType": 1,
                        "transformType": 5,
                        "instKind": 3,
                        "formName": "institution",
                        "procID": 9642,
                    }
                ]
            },
        }

        class MockResponse:
            def __init__(self, json_data):
                self._json_data = json_data
                self.status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return self._json_data

        async def mock_post(url, **kwargs):
            return MockResponse(public_register_response)

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post = mock_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            schools = await adapter.discover(limit=1, fetch_details=False)

        # Should get basic data without addresses
        assert len(schools) == 1
        school = schools[0]
        assert school.institutional_id == "2200015"
        assert school.name_i18n["bg"] == "Test School"
        assert school.locations == []  # No location data in fast mode
        assert school.website_url is None


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
                name_i18n={"bg": "Test School"},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="upper_secondary",
                source_url="https://ri-api.mon.bg/data/get/public-register",
                locations=[
                    DiscoveredLocation(
                        address_i18n={"bg": "Test Address"},
                        district="Средец",
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
        discovered_schools[0].name_i18n["bg"] = "Test School (updated)"
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
        assert "(updated)" in school.name_i18n["bg"]
        assert school.city == "sofia"
        assert school.education_level == "upper_secondary"


class TestMoeRegistryAgeGroupExtraction:
    """Tests for age group extraction based on detailed type."""

    def test_age_groups_primary_school(self):
        """Test age groups for primary school (type 121)."""
        from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
        
        # Type 121 = начално (grades 1-4)
        result = MoeRegistryAdapter._get_age_groups_for_detailed_type(121, 'primary')
        assert result == ['grade_1_4']
    
    def test_age_groups_basic_school(self):
        """Test age groups for basic school (type 122) serving grades 1-8."""
        from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
        
        # Type 122 = основно (grades 1-8)
        result = MoeRegistryAdapter._get_age_groups_for_detailed_type(122, 'lower_secondary')
        assert result == ['grade_1_4', 'grade_5_7']
    
    def test_age_groups_united_school(self):
        """Test age groups for united school (type 123) serving grades 1-12."""
        from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
        
        # Type 123 = обединено (grades 1-12)
        result = MoeRegistryAdapter._get_age_groups_for_detailed_type(123, 'upper_secondary')
        assert result == ['grade_1_4', 'grade_5_7', 'grade_8_12']
    
    def test_age_groups_secondary_school(self):
        """Test age groups for secondary school (type 124) serving grades 5-12."""
        from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
        
        # Type 124 = средно (grades 5-12, NOT 1-12)
        result = MoeRegistryAdapter._get_age_groups_for_detailed_type(124, 'upper_secondary')
        assert result == ['grade_5_7', 'grade_8_12']
    
    def test_age_groups_specialized_gymnasium(self):
        """Test age groups for specialized gymnasium (type 125) serving grades 8-12."""
        from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
        
        # Type 125 = профилирана гимназия (grades 8-12)
        result = MoeRegistryAdapter._get_age_groups_for_detailed_type(125, 'upper_secondary')
        assert result == ['grade_8_12']
    
    def test_age_groups_vocational_gymnasium(self):
        """Test age groups for vocational gymnasium (type 126) serving grades 8-12."""
        from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
        
        # Type 126 = професионална гимназия
        result = MoeRegistryAdapter._get_age_groups_for_detailed_type(126, 'upper_secondary')
        assert result == ['grade_8_12']
    
    def test_age_groups_kindergarten(self):
        """Test age groups for kindergarten (type 151)."""
        from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
        
        # Type 151 = детска градина (conservative: no nursery by default)
        result = MoeRegistryAdapter._get_age_groups_for_detailed_type(151, 'kindergarten')
        assert result == ['first', 'second', 'third', 'preschool']
    
    def test_age_groups_fallback_to_education_level(self):
        """Test fallback to education level when detailed type is unknown."""
        from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter
        
        # Unknown type code should fall back to education level mapping
        result = MoeRegistryAdapter._get_age_groups_for_detailed_type(999, 'primary')
        assert result == ['grade_1_4']
        
        result = MoeRegistryAdapter._get_age_groups_for_detailed_type(None, 'kindergarten')
        assert result == ['first', 'second', 'third', 'preschool']
