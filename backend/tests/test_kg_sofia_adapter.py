"""Tests for KgSofiaBgAdapter."""
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.scrapers.sources import get_adapter, list_adapters
from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter


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
        assert "source=kg_sofia_bg" in kg1.locations[0].location_tags
        assert "source_record_id=1" in kg1.locations[0].location_tags
        assert kg1.attributes["kg_sofia_id"] == 1
        assert kg1.attributes["kg_sofia_esri_id"] == 12345
        assert kg1.attributes["source_refs"]["kg_sofia_bg"]["record_id"] == "1"

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
        from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool

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
        from sqlalchemy import String, cast, select
        from sqlalchemy.orm import selectinload

        from app.models import School

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
        from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool

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


class TestKgSofiaBuildingMerges:
    """Tests for safe 'сграда' branch merging."""

    def test_merge_building_variants_into_single_school(self):
        """Explicit '- сграда ...' records should merge into one school with multiple locations."""
        from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool

        adapter = KgSofiaBgAdapter(db=None)

        def _school(name_bg: str, address_bg: str, kg_id: int):
            return DiscoveredSchool(
                name_i18n={"bg": name_bg},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="kindergarten",
                source_url="https://kg.sofia.bg/api/public/kg/type/kinderGarden/all",
                locations=[
                    DiscoveredLocation(
                        address_i18n={"bg": address_bg},
                        district="Банкя",
                        phone="02/967 66 20",
                        is_primary=True,
                        location_tags=[
                            "source=kg_sofia_bg",
                            f"source_record_id={kg_id}",
                            f"source_esri_id={kg_id}",
                        ],
                        age_groups=["first", "second", "third", "preschool"],
                        shifts={},
                        has_organised_groups={},
                    )
                ],
                attributes={
                    "kg_sofia_id": kg_id,
                    "kg_sofia_esri_id": kg_id,
                    "kg_sofia_public_type": "ДГ",
                },
            )

        records = [
            (_school("ДГ №25 Изворче", 'гр. Банкя, ул. "П. Д. Петков", №15', 174), False),
            (_school("ДГ №25 Изворче - сграда 2", "гр. Банкя, ул.Восток-2, №4", 274), True),
            (_school("ДГ №25 Изворче - сграда 3", 'гр. Банкя, ул. "Царибродска", №5', 497), False),
        ]

        merged = adapter._merge_building_branch_records(records)

        assert len(merged) == 1
        merged_school, changed = merged[0]
        assert changed is True
        assert merged_school.name_i18n["bg"] == "ДГ №25 Изворче"
        assert len(merged_school.locations) == 3
        assert sum(1 for location in merged_school.locations if location.is_primary) == 1
        assert merged_school.attributes["kg_sofia_merged_buildings"] is True
        assert merged_school.attributes["kg_sofia_ids"] == ["174", "274", "497"]
        assert merged_school.attributes["source_refs"]["kg_sofia_bg"]["record_ids"] == ["174", "274", "497"]
        flat_tags = [tag for location in merged_school.locations for tag in (location.location_tags or [])]
        assert "source_record_id=174" in flat_tags
        assert "source_record_id=274" in flat_tags
        assert "source_record_id=497" in flat_tags

    def test_does_not_merge_without_building_suffix(self):
        """Records without '- сграда' suffix must remain independent."""
        from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool

        adapter = KgSofiaBgAdapter(db=None)

        def _school(name_bg: str, address_bg: str, kg_id: int):
            return DiscoveredSchool(
                name_i18n={"bg": name_bg},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="kindergarten",
                source_url="https://kg.sofia.bg/api/public/kg/type/kinderGarden/all",
                locations=[
                    DiscoveredLocation(
                        address_i18n={"bg": address_bg},
                        district="Средец",
                        phone="02/123 45 67",
                        is_primary=True,
                        age_groups=[],
                        shifts={},
                        has_organised_groups={},
                    )
                ],
                attributes={"kg_sofia_id": kg_id},
            )

        records = [
            (_school("ДГ №1 Щастливо детство", "ул. Иван Вазов 1", 1), True),
            (_school("ДГ №1 Щастливо детство 2", "ул. Иван Вазов 2", 2), True),
        ]

        merged = adapter._merge_building_branch_records(records)

        assert len(merged) == 2
        assert merged[0][0].name_i18n["bg"] == "ДГ №1 Щастливо детство"
        assert merged[1][0].name_i18n["bg"] == "ДГ №1 Щастливо детство 2"


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
        """Test age groups for secondary school (СУ) serving grades 1-12."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter

        adapter = KgSofiaBgAdapter(db=None)

        # СУ (Средно училище) = grades 1-12
        result = adapter._extract_age_groups('СУ', 'upper_secondary')
        assert result == ['grade_1_4', 'grade_5_7', 'grade_8_12']
    
    def test_extract_age_groups_gymnasium(self):
        """Test age groups for specialized gymnasium (ПГ) serving grades 8-12."""
        from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
        
        adapter = KgSofiaBgAdapter(db=None)
        
        # ПГ (Профилирана гимназия) = grades 8-12 only
        result = adapter._extract_age_groups('ПГ', 'upper_secondary')
        assert result == ['grade_8_12']


def _kg_mock_client(kindergartens: list[dict], schools: list[dict]):
    class MockResponse:
        status_code = 200

        def __init__(self, json_data):
            self._json_data = json_data

        def raise_for_status(self):
            pass

        def json(self):
            return self._json_data

    async def mock_get(url, **kwargs):
        if "kinderGarden" in url:
            return MockResponse({"items": {"kinderGardens": kindergartens}})
        return MockResponse({"items": {"kinderGardens": schools}})

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get = mock_get
    return mock_client


def _kg_record(kg_id: int, name: str, public_type: str = "ДГ", region: str = "средец") -> dict:
    return {
        "id": kg_id,
        "nameStr": name,
        "name": {"publicType": public_type},
        "address": f"ул. Тестова {kg_id}, София",
        "region": region,
        "contacts": [],
    }


class TestKgSofiaImportCompleteness:
    """UF35: a limited first run must not hide the remaining records forever."""

    async def test_full_run_after_limited_run_creates_remaining_kindergartens(
        self, db_session: AsyncSession
    ):
        from sqlalchemy import func, select

        from app.models import School

        kindergartens = [_kg_record(i, f"ДГ №{i} Тест") for i in range(1, 7)]

        with patch("httpx.AsyncClient", return_value=_kg_mock_client(kindergartens, [])):
            first = await KgSofiaBgAdapter(db=db_session).run(limit=2)
        assert first["created"] == 2

        # The limited run stored fingerprints for all six records. Records whose school
        # was never created must still count as changed on the next run.
        with patch("httpx.AsyncClient", return_value=_kg_mock_client(kindergartens, [])):
            second = await KgSofiaBgAdapter(db=db_session).run()
        assert second["created"] == 4
        assert second["updated"] == 0

        total = await db_session.scalar(select(func.count(School.id)))
        assert total == 6

        # A third run with unchanged data does nothing.
        with patch("httpx.AsyncClient", return_value=_kg_mock_client(kindergartens, [])):
            third = await KgSofiaBgAdapter(db=db_session).run()
        assert third == {"created": 0, "updated": 0, "skipped": 0}

    async def test_record_with_known_kg_id_updates_existing_school(self, db_session: AsyncSession):
        """A linked school is matched by kg id, even when its name no longer matches."""
        from sqlalchemy import func, select

        from app.models import School, SchoolLocation

        existing = School(
            country_code="bg",
            name_i18n={"bg": "ДГ №7 Старо име"},
            school_type="state",
            education_level="kindergarten",
            city="sofia",
            scrape_status="summarized",
            attributes={"kg_sofia_id": 7},
        )
        db_session.add(existing)
        await db_session.flush()
        db_session.add(
            SchoolLocation(
                school_id=existing.id,
                address_i18n={"bg": "ул. Тестова 7, София"},
                district="Средец",
                lat=42.69,
                lng=23.32,
                is_primary=True,
            )
        )
        await db_session.commit()

        # No registry page yet, so the record counts as changed and is upserted.
        records = [_kg_record(7, "ДГ №7 Ново име")]
        with patch("httpx.AsyncClient", return_value=_kg_mock_client(records, [])):
            result = await KgSofiaBgAdapter(db=db_session).run()

        assert result == {"created": 0, "updated": 1, "skipped": 0}
        assert await db_session.scalar(select(func.count(School.id))) == 1
        location = (await db_session.execute(select(SchoolLocation))).scalar_one()
        assert (location.lat, location.lng) == (42.69, 23.32)

    async def test_linked_school_gains_new_building_without_losing_existing_location(
        self, db_session: AsyncSession
    ):
        from sqlalchemy import select

        from app.models import School, SchoolLocation
        from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool

        existing = School(
            country_code="bg",
            name_i18n={"bg": "ДГ №7 Тест"},
            school_type="state",
            education_level="kindergarten",
            city="sofia",
            attributes={"kg_sofia_id": 7},
        )
        db_session.add(existing)
        await db_session.flush()
        db_session.add(
            SchoolLocation(
                school_id=existing.id,
                address_i18n={"bg": "ул. Тестова 7, София"},
                district="Средец",
                lat=42.69,
                lng=23.32,
                is_primary=True,
            )
        )
        await db_session.commit()

        def location(address: str) -> DiscoveredLocation:
            return DiscoveredLocation(
                address_i18n={"bg": address},
                district="Средец",
                is_primary=True,
                age_groups=["first"],
                shifts={},
                has_organised_groups={},
            )

        adapter = KgSofiaBgAdapter(db=db_session)
        adapter._school_id_by_kg_id = {"7": existing.id}
        result = await adapter.upsert_schools([
            DiscoveredSchool(
                name_i18n={"bg": "ДГ №7 Тест"},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="kindergarten",
                locations=[location("ул.  Тестова 7, София"), location("ул. Нова 1, София")],
                attributes={"kg_sofia_id": 7, "kg_sofia_ids": ["7", "8"]},
            )
        ])

        assert result["updated"] == 1
        locations = (
            await db_session.execute(
                select(SchoolLocation).order_by(SchoolLocation.id)
            )
        ).scalars().all()
        assert [loc.address_i18n["bg"] for loc in locations] == [
            "ул. Тестова 7, София",
            "ул. Нова 1, София",
        ]
        assert (locations[0].lat, locations[0].lng, locations[0].is_primary) == (42.69, 23.32, True)
        assert locations[1].is_primary is False

    def test_merged_family_keeps_building_that_is_already_its_own_school(self):
        from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool

        def location(record_id: int) -> DiscoveredLocation:
            return DiscoveredLocation(
                address_i18n={"bg": f"ул. Тестова {record_id}"},
                district="Средец",
                is_primary=record_id == 106,
                location_tags=[f"source_record_id={record_id}"],
                age_groups=[],
                shifts={},
                has_organised_groups={},
            )

        adapter = KgSofiaBgAdapter(db=None)
        # 106 is the family's school; 299 ('- сграда 2') was imported as school 120.
        adapter._school_id_by_kg_id = {"106": 119, "299": 120}
        adapter._school_id_by_own_kg_id = {"106": 119, "299": 120}
        family = DiscoveredSchool(
            name_i18n={"bg": "ДГ №16 Приказен свят"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="kindergarten",
            locations=[location(106), location(299), location(500)],
            attributes={"kg_sofia_id": 106, "kg_sofia_ids": ["106", "299", "500"]},
        )

        adapter._drop_locations_owned_by_other_schools(family)

        assert [loc.location_tags for loc in family.locations] == [
            ["source_record_id=106"],
            ["source_record_id=500"],
        ]

    def test_new_family_does_not_match_standalone_building_school(self):
        from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool

        def location(record_id: int) -> DiscoveredLocation:
            return DiscoveredLocation(
                address_i18n={"bg": f"ул. Тестова {record_id}"},
                district="Средец",
                is_primary=record_id == 40,
                location_tags=[f"source_record_id={record_id}"],
                age_groups=[],
                shifts={},
                has_organised_groups={},
            )

        adapter = KgSofiaBgAdapter(db=None)
        # Canonical 40 was never imported; its building 41 is standalone school 500.
        adapter._school_id_by_kg_id = {"41": 500}
        adapter._school_id_by_own_kg_id = {"41": 500}
        family = DiscoveredSchool(
            name_i18n={"bg": "ДГ №40 Тест"},
            country_code="bg",
            city="sofia",
            school_type="state",
            education_level="kindergarten",
            locations=[location(40), location(41)],
            attributes={"kg_sofia_id": 40, "kg_sofia_ids": ["40", "41"]},
        )

        assert adapter._existing_school_id_for(family) is None
        adapter._drop_locations_owned_by_other_schools(family)
        assert [loc.location_tags for loc in family.locations] == [["source_record_id=40"]]

    async def test_linked_school_location_takes_new_age_groups_keeps_coordinates(
        self, db_session: AsyncSession
    ):
        from sqlalchemy import select

        from app.models import School, SchoolLocation, SchoolLocationAgeGroupShift
        from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool

        school = School(
            country_code="bg", name_i18n={"bg": "ДГ №9 Тест"}, school_type="state",
            education_level="kindergarten", city="sofia", attributes={"kg_sofia_id": 9},
        )
        db_session.add(school)
        await db_session.flush()
        row = SchoolLocation(
            school_id=school.id, address_i18n={"bg": "ул. Тестова 9"}, district="Средец",
            phone="02/000", lat=42.7, lng=23.3, is_primary=True,
            location_tags=["address_source=website_contact", "source_record_id=1"],
        )
        db_session.add(row)
        await db_session.flush()
        db_session.add(SchoolLocationAgeGroupShift(location_id=row.id, age_group="first"))
        await db_session.commit()

        adapter = KgSofiaBgAdapter(db=db_session)
        adapter._school_id_by_kg_id = {"9": school.id}
        await adapter.upsert_schools([
            DiscoveredSchool(
                name_i18n={"bg": "ДГ №9 Тест"}, country_code="bg", city="sofia",
                school_type="state", education_level="kindergarten",
                locations=[DiscoveredLocation(
                    address_i18n={"bg": "ул. Тестова 9"}, district="Средец", phone="02/111",
                    is_primary=True, age_groups=["nursery", "first"], shifts={},
                    location_tags=["source=kg_sofia_bg", "source_record_id=9"],
                    has_organised_groups={},
                )],
                attributes={"kg_sofia_id": 9, "kg_sofia_public_type": "ДГ (с яслени групи)"},
            )
        ])

        location = (await db_session.execute(select(SchoolLocation))).scalar_one()
        assert location.id == row.id
        assert (location.lat, location.lng, location.phone) == (42.7, 23.3, "02/111")
        assert location.location_tags == [
            "address_source=website_contact",
            "source=kg_sofia_bg",
            "source_record_id=9",
        ]
        age_groups = (
            await db_session.execute(select(SchoolLocationAgeGroupShift.age_group))
        ).scalars().all()
        assert sorted(age_groups) == ["first", "nursery"]

    async def test_building_school_owns_its_kg_id_over_family_list(self, db_session: AsyncSession):
        from app.models import School

        family = School(
            country_code="bg", name_i18n={"bg": "A"}, school_type="state",
            education_level="kindergarten", city="sofia",
            attributes={"kg_sofia_id": 106, "kg_sofia_ids": ["106", "299"]},
        )
        building = School(
            country_code="bg", name_i18n={"bg": "B"}, school_type="state",
            education_level="kindergarten", city="sofia",
            attributes={"kg_sofia_id": 299},
        )
        # Insert the building first so row order alone would let the family win.
        db_session.add(building)
        await db_session.flush()
        db_session.add(family)
        await db_session.commit()

        adapter = KgSofiaBgAdapter(db=db_session)
        with patch("httpx.AsyncClient", return_value=_kg_mock_client([], [])):
            await adapter.discover()

        assert adapter._school_id_by_kg_id["299"] == building.id
        assert adapter._school_id_by_kg_id["106"] == family.id

    async def test_school_records_never_create_schools(self, db_session: AsyncSession):
        from sqlalchemy import func, select

        from app.models import School

        schools = [_kg_record(100, "33 СУ Тест", public_type="СУ")]
        with patch("httpx.AsyncClient", return_value=_kg_mock_client([], schools)):
            result = await KgSofiaBgAdapter(db=db_session).run()

        assert result["created"] == 0
        assert result["skipped"] == 1
        assert await db_session.scalar(select(func.count(School.id))) == 0

    async def test_nursery_records_create_schools(self, db_session: AsyncSession):
        kindergartens = [_kg_record(5, "СДЯ №5 Тест", public_type="СДЯ")]
        with patch("httpx.AsyncClient", return_value=_kg_mock_client(kindergartens, [])):
            result = await KgSofiaBgAdapter(db=db_session).run()
        assert result["created"] == 1

    async def test_enrichment_keeps_existing_locations_and_scrape_status(
        self, db_session: AsyncSession
    ):
        from sqlalchemy import select

        from app.models import School, SchoolLocation
        from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool

        # ASCII name: SQLite stores Cyrillic JSON as escapes, which breaks name matching in tests.

        existing = School(
            country_code="bg",
            name_i18n={"bg": "DG 31 Lyulin"},
            school_type="state",
            education_level="kindergarten",
            city="sofia",
            scrape_status="summarized",
            attributes={},
        )
        db_session.add(existing)
        await db_session.flush()
        corrected = SchoolLocation(
            school_id=existing.id,
            address_i18n={"bg": "гр. София, ул. 208 № 17 - II м. р."},
            district="Люлин",
            lat=42.7239608,
            lng=23.2546818,
            geocode_meta={"status": "accepted", "manual_fix": "UF33"},
            is_primary=True,
        )
        db_session.add(corrected)
        await db_session.commit()
        corrected_id = corrected.id

        result = await KgSofiaBgAdapter(db=db_session).upsert_schools([
            DiscoveredSchool(
                name_i18n={"bg": "DG 31 Lyulin"},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="kindergarten",
                locations=[
                    DiscoveredLocation(
                        address_i18n={"bg": "гр. София, ул. 208 № 17"},
                        district="Люлин",
                        is_primary=True,
                        age_groups=["first"],
                        shifts={},
                        has_organised_groups={},
                    )
                ],
                attributes={"kg_sofia_id": 83},
            )
        ])

        assert result["updated"] == 1
        await db_session.refresh(existing)
        assert existing.scrape_status == "summarized"
        assert existing.attributes["kg_sofia_id"] == 83
        locations = (
            await db_session.execute(
                select(SchoolLocation).where(SchoolLocation.school_id == existing.id)
            )
        ).scalars().all()
        assert [location.id for location in locations] == [corrected_id]
        assert (locations[0].lat, locations[0].lng) == (42.7239608, 23.2546818)
        assert locations[0].geocode_meta["manual_fix"] == "UF33"
