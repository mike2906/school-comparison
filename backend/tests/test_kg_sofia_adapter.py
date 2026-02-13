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
        assert KgSofiaBgAdapter.RATE_LIMIT == "10/m"

    def test_adapter_listed(self):
        """Adapter appears in list_adapters()."""
        adapters = list_adapters()
        assert "kg_sofia_bg" in adapters
        assert adapters["kg_sofia_bg"]["country_code"] == "bg"
        assert adapters["kg_sofia_bg"]["city"] == "sofia"


class TestKgSofiaBgAdapterMappings:
    """Test field mappings."""

    def test_district_mapping_complete(self):
        """All Sofia districts are mapped."""
        assert len(KgSofiaBgAdapter.DISTRICT_MAPPING) >= 20
        assert KgSofiaBgAdapter.DISTRICT_MAPPING["средец"] == "Средец"
        assert KgSofiaBgAdapter.DISTRICT_MAPPING["лозенец"] == "Лозенец"
        assert KgSofiaBgAdapter.DISTRICT_MAPPING["студентски"] == "Студентски град"

    def test_type_mapping_complete(self):
        """All institution types are mapped."""
        assert len(KgSofiaBgAdapter.TYPE_MAPPING) >= 8
        assert KgSofiaBgAdapter.TYPE_MAPPING["ДГ"] == "kindergarten"
        assert KgSofiaBgAdapter.TYPE_MAPPING["СУ"] == "upper_secondary"
        assert KgSofiaBgAdapter.TYPE_MAPPING["ОУ"] == "lower_secondary"
        assert KgSofiaBgAdapter.TYPE_MAPPING["СДЯ"] == "kindergarten"


@pytest.mark.asyncio
class TestKgSofiaBgAdapterIntegration:
    """Integration tests with mocked API responses."""

    async def test_discover_with_mock_api_response(self, db_session: AsyncSession):
        """Test discover() with mocked kg.sofia.bg API JSON responses."""
        adapter = KgSofiaBgAdapter(db=db_session)

        # Mock API response for kindergartens endpoint
        kg_response = {
            "items": {
                "kinderGardens": [
                    {
                        "id": 1,
                        "nameStr": "ДГ №1 Щастливо детство",
                        "name": {
                            "publicType": "ДГ"
                        },
                        "address": "ул. Иван Вазов 15, София",
                        "region": "средец",
                        "contacts": [
                            {
                                "kindCommunication": {"label": "phone"},
                                "fieldValue": "02/987-6543"
                            }
                        ],
                        "esriId": 12345
                    },
                    {
                        "id": 2,
                        "nameStr": "ДГ №5 Слънчице",
                        "name": {
                            "publicType": "ДГ (с яслени групи)"
                        },
                        "address": "бул. Витоша 100, София",
                        "region": "лозенец",
                        "contacts": [
                            {
                                "kindCommunication": {"label": "email"},
                                "fieldValue": "dg5@sofia.bg"
                            },
                            {
                                "kindCommunication": {"label": "phone"},
                                "fieldValue": "02/123-4567"
                            }
                        ],
                        "esriId": 12346
                    }
                ]
            }
        }

        # Mock API response for schools endpoint
        school_response = {
            "items": {
                "kinderGardens": [
                    {
                        "id": 100,
                        "nameStr": "СУ Христо Ботев",
                        "name": {
                            "publicType": "СУ"
                        },
                        "address": "ул. Граф Игнатиев 2, София",
                        "region": "оборище",
                        "contacts": [
                            {
                                "kindCommunication": {"label": "phone"},
                                "fieldValue": "02/555-1234"
                            }
                        ],
                        "esriId": 20001
                    }
                ]
            }
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

        # Track which endpoint is being called
        call_count = [0]

        async def mock_get(url, **kwargs):
            call_count[0] += 1
            if "kinderGarden" in url:
                return MockResponse(kg_response)
            elif "school" in url:
                return MockResponse(school_response)
            return MockResponse({"items": {"kinderGardens": []}})

        # Create mock client
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            schools = await adapter.discover(limit=None)

        # Verify we got schools back
        assert len(schools) == 3  # 2 kindergartens + 1 school

        # Verify first kindergarten
        kg1 = schools[0]
        assert kg1.name_i18n["bg"] == "ДГ №1 Щастливо детство"
        assert kg1.country_code == "bg"
        assert kg1.city == "sofia"
        assert kg1.school_type == "state"
        assert kg1.education_level == "kindergarten"
        assert len(kg1.locations) == 1
        assert "Иван Вазов" in kg1.locations[0].address_i18n["bg"]
        assert kg1.locations[0].district == "Средец"
        assert kg1.locations[0].phone == "02/987-6543"
        assert kg1.attributes["kg_sofia_id"] == 1
        assert kg1.attributes["kg_sofia_esri_id"] == 12345

        # Verify second kindergarten (with nursery groups)
        kg2 = schools[1]
        assert kg2.name_i18n["bg"] == "ДГ №5 Слънчице"
        assert kg2.education_level == "kindergarten"
        assert kg2.locations[0].district == "Лозенец"
        assert kg2.locations[0].phone == "02/123-4567"
        assert kg2.attributes["kg_sofia_public_type"] == "ДГ (с яслени групи)"

        # Verify school
        school = schools[2]
        assert school.name_i18n["bg"] == "СУ Христо Ботев"
        assert school.education_level == "upper_secondary"
        assert school.locations[0].district == "Оборище"
        assert school.locations[0].phone == "02/555-1234"

    async def test_discover_with_limit(self, db_session: AsyncSession):
        """Test discover() respects limit parameter."""
        adapter = KgSofiaBgAdapter(db=db_session)

        # Mock API response with multiple items
        kg_response = {
            "items": {
                "kinderGardens": [
                    {
                        "id": i,
                        "nameStr": f"ДГ №{i}",
                        "name": {"publicType": "ДГ"},
                        "address": f"ул. Test {i}, София",
                        "region": "средец",
                        "contacts": []
                    }
                    for i in range(1, 11)  # 10 kindergartens
                ]
            }
        }

        school_response = {
            "items": {
                "kinderGardens": [
                    {
                        "id": 100 + i,
                        "nameStr": f"СУ №{i}",
                        "name": {"publicType": "СУ"},
                        "address": f"ул. School {i}, София",
                        "region": "лозенец",
                        "contacts": []
                    }
                    for i in range(1, 6)  # 5 schools
                ]
            }
        }

        class MockResponse:
            def __init__(self, json_data):
                self._json_data = json_data
                self.status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return self._json_data

        async def mock_get(url, **kwargs):
            if "kinderGarden" in url:
                return MockResponse(kg_response)
            elif "school" in url:
                return MockResponse(school_response)
            return MockResponse({"items": {"kinderGardens": []}})

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            schools = await adapter.discover(limit=5)

        # Should only return 5 schools even though API returned 15 total
        assert len(schools) == 5

    async def test_discover_handles_missing_phone(self, db_session: AsyncSession):
        """Test that discover handles institutions without phone numbers."""
        adapter = KgSofiaBgAdapter(db=db_session)

        # Mock API response with no phone contacts
        kg_response = {
            "items": {
                "kinderGardens": [
                    {
                        "id": 1,
                        "nameStr": "ДГ без телефон",
                        "name": {"publicType": "ДГ"},
                        "address": "ул. Test 1, София",
                        "region": "средец",
                        "contacts": [
                            {
                                "kindCommunication": {"label": "email"},
                                "fieldValue": "test@example.com"
                            }
                        ]
                    }
                ]
            }
        }

        class MockResponse:
            def __init__(self, json_data):
                self._json_data = json_data
                self.status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return self._json_data

        async def mock_get(url, **kwargs):
            if "kinderGarden" in url:
                return MockResponse(kg_response)
            return MockResponse({"items": {"kinderGardens": []}})

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = mock_get

        with patch("httpx.AsyncClient", return_value=mock_client):
            schools = await adapter.discover(limit=1)

        assert len(schools) == 1
        assert schools[0].locations[0].phone is None


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
                source_url="https://kg.sofia.bg/api/public/kg/type/kinderGarden/all",
                locations=[
                    DiscoveredLocation(
                        address_i18n={"bg": "ул. Тестова 1, София"},
                        district="Средец",
                        phone="02/123-4567",
                        is_primary=True,
                        age_groups=[],  # kg.sofia.bg doesn't provide age groups
                        shifts={},
                        has_organised_groups={},
                    )
                ],
                attributes={
                    "kg_sofia_id": 99,
                    "kg_sofia_esri_id": 99999,
                    "kg_sofia_public_type": "ДГ"
                }
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
        from sqlalchemy import select, cast, String
        from sqlalchemy.orm import selectinload

        # Query by kg_sofia_id in attributes (unique identifier)
        # Use cast(JSON, String) for cross-DB compatibility (SQLite vs PostgreSQL)
        db_result = await db_session.execute(
            select(School)
            .options(selectinload(School.locations))
            .where(cast(School.attributes, String).ilike('%"kg_sofia_id": 99%'))
        )
        school = db_result.scalar_one_or_none()

        assert school is not None
        assert school.name_i18n["bg"] == "ДГ №99 Тестова"
        assert school.city == "sofia"
        assert school.school_type == "state"
        assert len(school.locations) == 1
        assert school.locations[0].district == "Средец"
        assert school.attributes["kg_sofia_id"] == 99

    async def test_upsert_preserves_moe_core_fields_on_existing_school(self, db_session: AsyncSession):
        """kg.sofia enrichment must not overwrite core fields sourced from MoE."""
        from sqlalchemy import select
        from app.models import School, SchoolLocation
        from app.schemas.scraping import DiscoveredSchool, DiscoveredLocation

        existing = School(
            country_code="bg",
            name_i18n={"bg": "ДГ №200 Тест"},
            school_type="state",
            education_level="kindergarten",
            city="sofia",
            source_url="moe://public-register/2200200",
            website_url="https://moe-school.example",
            institutional_id="2200200",
            attributes={
                "moe_email": "office@moe-school.example",
                "moe_registry_active": True,
            },
            admission_info={"system": "points"},
        )
        db_session.add(existing)
        await db_session.flush()

        db_session.add(
            SchoolLocation(
                school_id=existing.id,
                address_i18n={"bg": "ул. Тестова 1, София"},
                district="Средец",
                is_primary=True,
            )
        )
        await db_session.commit()

        adapter = KgSofiaBgAdapter(db=db_session)
        discovered_schools = [
            DiscoveredSchool(
                institutional_id="2200200",
                name_i18n={"bg": "ДГ №200 Тест"},
                country_code="bg",
                city="sofia",
                school_type="private",  # Must not overwrite existing core classification
                education_level="upper_secondary",  # Must not overwrite existing level
                source_url="https://kg.sofia.bg/api/public/kg/type/kinderGarden/all",
                website_url="https://kg-sofia.example",
                locations=[
                    DiscoveredLocation(
                        address_i18n={"bg": "ул. Тестова 2, София"},
                        district="Средец",
                        phone="02/123-0000",
                        is_primary=True,
                        age_groups=[],
                        shifts={},
                        has_organised_groups={},
                    )
                ],
                attributes={
                    "kg_sofia_id": 200,
                    "kg_sofia_public_type": "ДГ",
                    "moe_email": "bad-overwrite@example.com",  # Must be ignored by enrichment filter
                },
            )
        ]

        result = await adapter.upsert_schools(discovered_schools)
        assert result["updated"] == 1
        assert result["created"] == 0

        refreshed = (
            await db_session.execute(
                select(School).where(School.id == existing.id)
            )
        ).scalar_one()

        # Core identity/classification fields remain authoritative.
        assert refreshed.institutional_id == "2200200"
        assert refreshed.school_type == "state"
        assert refreshed.education_level == "kindergarten"
        assert refreshed.website_url == "https://moe-school.example"
        assert refreshed.source_url == "moe://public-register/2200200"

        # MoE attributes are preserved; kg.sofia attributes are added.
        assert refreshed.attributes["moe_email"] == "office@moe-school.example"
        assert refreshed.attributes["kg_sofia_id"] == 200


class TestKgSofiaAgeGroupExtraction:
    """Tests for age group extraction logic."""

    def test_extract_age_groups_kindergarten_with_nursery(self):
        """Test age groups for kindergarten with nursery (яслени групи)."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
        
        adapter = KgSofiaBgAdapter(db=None)
        
        # ДГ (с яслени групи) should include nursery + all kindergarten groups
        result = adapter._extract_age_groups('ДГ (с яслени групи)', 'kindergarten')
        assert result == ['nursery', 'first', 'second', 'third', 'preschool']
    
    def test_extract_age_groups_kindergarten_standard(self):
        """Test age groups for standard kindergarten."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
        
        adapter = KgSofiaBgAdapter(db=None)
        
        # Standard ДГ (no nursery)
        result = adapter._extract_age_groups('ДГ', 'kindergarten')
        assert result == ['first', 'second', 'third', 'preschool']
    
    def test_extract_age_groups_nursery_only(self):
        """Test age groups for standalone nursery (СДЯ)."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
        
        adapter = KgSofiaBgAdapter(db=None)
        
        # СДЯ (Самостоятелна детска ясла) = nursery only
        result = adapter._extract_age_groups('СДЯ', 'kindergarten')
        assert result == ['nursery']
    
    def test_extract_age_groups_primary_school(self):
        """Test age groups for primary school (НУ)."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
        
        adapter = KgSofiaBgAdapter(db=None)
        
        # НУ (Начално училище) = grades 1-4 only
        result = adapter._extract_age_groups('НУ', 'primary')
        assert result == ['grade_1_4']
    
    def test_extract_age_groups_basic_school(self):
        """Test age groups for basic school (ОУ) serving grades 1-8."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
        
        adapter = KgSofiaBgAdapter(db=None)
        
        # ОУ (Основно училище) = grades 1-8
        result = adapter._extract_age_groups('ОУ', 'lower_secondary')
        assert result == ['grade_1_4', 'grade_5_7']
    
    def test_extract_age_groups_united_school(self):
        """Test age groups for united school (ОбУ) serving grades 1-12."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
        
        adapter = KgSofiaBgAdapter(db=None)
        
        # ОбУ (Обединено училище) = grades 1-12
        result = adapter._extract_age_groups('ОбУ', 'upper_secondary')
        assert result == ['grade_1_4', 'grade_5_7', 'grade_8_12']
    
    def test_extract_age_groups_secondary_school(self):
        """Test age groups for secondary school (СУ) serving grades 5-12."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
        
        adapter = KgSofiaBgAdapter(db=None)
        
        # СУ (Средно училище) = grades 5-12 (NOT 1-12)
        result = adapter._extract_age_groups('СУ', 'upper_secondary')
        assert result == ['grade_5_7', 'grade_8_12']
    
    def test_extract_age_groups_gymnasium(self):
        """Test age groups for specialized gymnasium (ПГ) serving grades 8-12."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
        
        adapter = KgSofiaBgAdapter(db=None)
        
        # ПГ (Профилирана гимназия) = grades 8-12 only
        result = adapter._extract_age_groups('ПГ', 'upper_secondary')
        assert result == ['grade_8_12']
