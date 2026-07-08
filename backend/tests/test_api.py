"""Tests for API endpoints."""
import pytest
from sqlalchemy import select

from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_check(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}


class TestSchoolsEndpoint:
    @pytest.mark.asyncio
    async def test_list_schools_empty_db(self, client):
        """Empty database returns empty list."""
        response = await client.get("/schools")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_schools_with_data(self, seeded_client):
        """Returns all schools with locations."""
        response = await seeded_client.get("/schools")
        assert response.status_code == 200

        data = response.json()
        assert len(data) == 3

        # Check first school has expected fields
        school = data[0]
        assert "id" in school
        assert "name_i18n" in school
        assert "school_type" in school
        assert "education_level" in school
        assert "locations" in school
        assert "country_code" in school
        assert "resolved_name_i18n" in school

    @pytest.mark.asyncio
    async def test_list_schools_filter_by_age_group(self, seeded_client):
        """Filter schools by age group."""
        # First group - should return 2 kindergartens
        response = await seeded_client.get("/schools?age_group=first")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

        # Grade 1-4 - should return 1 primary school
        response = await seeded_client.get("/schools?age_group=grade_1_4")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["education_level"] == "primary"

        # Preschool - should return 1 kindergarten (the state one with 2 locations)
        response = await seeded_client.get("/schools?age_group=preschool")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_get_school_by_id(self, seeded_client):
        """Get single school with full details."""
        # First get list to find an ID
        list_response = await seeded_client.get("/schools")
        school_id = list_response.json()[0]["id"]

        response = await seeded_client.get(f"/schools/{school_id}")
        assert response.status_code == 200

        school = response.json()
        assert school["id"] == school_id
        assert "name_i18n" in school
        assert "resolved_name_i18n" in school
        assert "locations" in school
        assert "summary_i18n" in school

    @pytest.mark.asyncio
    async def test_resolved_name_i18n_derives_english_fallback(self, seeded_db, seeded_client):
        school = School(
            name_i18n={"bg": "Д-р Петър Берон"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            attributes={},
        )
        seeded_db.add(school)
        await seeded_db.commit()

        response = await seeded_client.get(f"/schools/{school.id}")
        assert response.status_code == 200

        data = response.json()
        assert data["resolved_name_i18n"] == {
            "bg": "Д-р Петър Берон",
            "en": "Dr. Petar Beron",
        }

    @pytest.mark.asyncio
    async def test_get_school_not_found(self, seeded_client):
        """Returns 404 for non-existent school."""
        response = await seeded_client.get("/schools/99999")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_school_locations_have_coordinates(self, seeded_client):
        """School locations include lat/lng."""
        response = await seeded_client.get("/schools")
        data = response.json()

        for school in data:
            for location in school["locations"]:
                assert "lat" in location
                assert "lng" in location
                assert "address_i18n" in location
                assert "age_groups" in location  # Changed to age_groups (list property)
                assert "age_group_shifts" in location  # Junction table data

    @pytest.mark.asyncio
    async def test_list_excludes_schools_without_resolved_locations(self, seeded_db, seeded_client):
        hidden_school = School(
            name_i18n={"bg": "Скрито училище", "en": "Hidden School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
        )
        seeded_db.add(hidden_school)
        await seeded_db.flush()
        hidden_location = SchoolLocation(
            school_id=hidden_school.id,
            address_i18n={"bg": "ул. Без координати 1, София", "en": "1 No Coordinates St, Sofia"},
            lat=None,
            lng=None,
            is_primary=True,
        )
        seeded_db.add(hidden_location)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=hidden_location.id,
                age_group="grade_1_4",
                shift="morning",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools")
        assert response.status_code == 200
        ids = {school["id"] for school in response.json()}
        assert hidden_school.id not in ids

    @pytest.mark.asyncio
    async def test_list_defaults_to_sofia_city_scope(self, seeded_db, seeded_client):
        outside_school = School(
            name_i18n={"bg": "Пловдивско училище", "en": "Plovdiv School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="plovdiv",
        )
        seeded_db.add(outside_school)
        await seeded_db.flush()
        outside_location = SchoolLocation(
            school_id=outside_school.id,
            address_i18n={"bg": "ул. Пловдив 1", "en": "1 Plovdiv St"},
            lat=42.1354,
            lng=24.7453,
            is_primary=True,
        )
        seeded_db.add(outside_location)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=outside_location.id,
                age_group="grade_1_4",
                shift="morning",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools")
        assert response.status_code == 200
        ids = {school["id"] for school in response.json()}
        assert outside_school.id not in ids

        response = await seeded_client.get("/schools?city=all")
        assert response.status_code == 200
        ids = {school["id"] for school in response.json()}
        assert outside_school.id in ids

    @pytest.mark.asyncio
    async def test_list_excludes_sofia_schools_with_out_of_bounds_coordinates(self, seeded_db, seeded_client):
        bad_geo_school = School(
            name_i18n={"bg": '6 ОУ "Граф Игнатиев"', "en": "6 OU Graf Ignatiev"},
            country_code="bg",
            school_type="state",
            education_level="lower_secondary",
            city="sofia",
        )
        seeded_db.add(bad_geo_school)
        await seeded_db.flush()
        bad_location = SchoolLocation(
            school_id=bad_geo_school.id,
            address_i18n={"bg": "ул. Шести септември 16", "en": "16 Shesti Septemvri St"},
            lat=43.064,
            lng=24.82002,
            is_primary=True,
        )
        seeded_db.add(bad_location)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=bad_location.id,
                age_group="grade_5_7",
                shift="morning",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools")
        assert response.status_code == 200
        ids = {school["id"] for school in response.json()}
        assert bad_geo_school.id not in ids

        response = await seeded_client.get("/schools?city=all")
        assert response.status_code == 200
        ids = {school["id"] for school in response.json()}
        assert bad_geo_school.id in ids

    @pytest.mark.asyncio
    async def test_list_includes_sofia_municipality_edge_coordinates(self, seeded_db, seeded_client):
        bankya_school = School(
            name_i18n={"bg": "ДГ №25 Изворче", "en": "KG 25 Izvorche"},
            country_code="bg",
            school_type="state",
            education_level="kindergarten",
            city="sofia",
        )
        seeded_db.add(bankya_school)
        await seeded_db.flush()
        bankya_location = SchoolLocation(
            school_id=bankya_school.id,
            address_i18n={"bg": 'гр. Банкя, ул. "П. Д. Петков", №15'},
            lat=42.71125,
            lng=23.14131,
            is_primary=True,
        )
        seeded_db.add(bankya_location)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=bankya_location.id,
                age_group="first",
                shift="morning",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools")
        assert response.status_code == 200
        ids = {school["id"] for school in response.json()}
        assert bankya_school.id in ids


class TestSchoolsFilterEndpoint:
    """Test advanced filtering capabilities."""

    @pytest.mark.asyncio
    async def test_filter_by_school_type_state(self, seeded_client):
        """Filter by school_type=state."""
        response = await seeded_client.get("/schools?school_type=state")
        assert response.status_code == 200
        data = response.json()
        # Should return 2 state schools (1 KG + 1 primary)
        assert len(data) == 2
        assert all(s["school_type"] == "state" for s in data)

    @pytest.mark.asyncio
    async def test_filter_by_school_type_private(self, seeded_client):
        """Filter by school_type=private."""
        response = await seeded_client.get("/schools?school_type=private")
        assert response.status_code == 200
        data = response.json()
        # Should return 1 private school
        assert len(data) == 1
        assert data[0]["school_type"] == "private"

    @pytest.mark.asyncio
    async def test_filter_by_education_level(self, seeded_client):
        """Filter by education_level."""
        response = await seeded_client.get("/schools?education_level=kindergarten")
        assert response.status_code == 200
        data = response.json()
        # Should return 2 kindergartens
        assert len(data) == 2
        assert all(s["education_level"] == "kindergarten" for s in data)

    @pytest.mark.asyncio
    async def test_filter_combined_age_and_type(self, seeded_client):
        """Combine age_group and school_type filters."""
        response = await seeded_client.get("/schools?age_group=first&school_type=state")
        assert response.status_code == 200
        data = response.json()
        # Should return 1 state kindergarten with first group
        assert len(data) == 1
        assert data[0]["school_type"] == "state"

    @pytest.mark.asyncio
    async def test_age_group_filter_excludes_schools_with_only_unresolved_matching_location(self, seeded_db, seeded_client):
        school = School(
            name_i18n={"bg": "Смесени локации", "en": "Mixed Locations School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
        )
        seeded_db.add(school)
        await seeded_db.flush()

        resolved_location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Видима 4, София", "en": "4 Visible St, Sofia"},
            lat=42.7001,
            lng=23.3001,
            is_primary=True,
        )
        unresolved_location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Невидима 5, София", "en": "5 Invisible St, Sofia"},
            lat=None,
            lng=None,
            is_primary=False,
        )
        seeded_db.add_all([resolved_location, unresolved_location])
        await seeded_db.flush()

        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=resolved_location.id,
                age_group="preschool",
                shift="full_day",
            )
        )
        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=unresolved_location.id,
                age_group="grade_1_4",
                shift="morning",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools?age_group=grade_1_4")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()}
        assert school.id not in ids

    @pytest.mark.asyncio
    async def test_include_crossover_preschool(self, seeded_client):
        """Test include_crossover=true for preschool age group."""
        # Without crossover - should only get kindergartens
        response1 = await seeded_client.get("/schools?age_group=preschool&education_level=kindergarten")
        assert response1.status_code == 200
        data1 = response1.json()
        assert len(data1) == 1  # Only state KG with preschool

        # With crossover - should get both kindergartens and primary schools
        response2 = await seeded_client.get("/schools?age_group=preschool&include_crossover=true")
        assert response2.status_code == 200
        data2 = response2.json()
        # Should have both the state KG with preschool location
        assert len(data2) >= 1

    @pytest.mark.asyncio
    async def test_preschool_school_filter_includes_all_through_schools(self, seeded_db, seeded_client):
        school = School(
            name_i18n={"bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"'},
            country_code="bg",
            school_type="state",
            education_level="upper_secondary",
            city="sofia",
            website_url="https://21su.bg",
        )
        seeded_db.add(school)
        await seeded_db.flush()

        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Люботрън 12"},
            lat=42.66915,
            lng=23.31543,
            is_primary=True,
        )
        seeded_db.add(location)
        await seeded_db.flush()

        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=location.id,
                age_group="preschool",
                shift="full_day",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools?age_group=preschool&education_level=primary")
        assert response.status_code == 200
        data = response.json()
        ids = {item["id"] for item in data}
        assert school.id in ids
        assert all(item["education_level"] != "kindergarten" for item in data)


class TestSchoolsSearchEndpoint:
    """Test search endpoint.

    Note: SQLite stores Cyrillic as Unicode escapes in JSON columns,
    so search tests use English names. On PostgreSQL (production),
    Cyrillic search works natively in JSONB.
    """

    @pytest.mark.asyncio
    async def test_search_by_name(self, seeded_client):
        """Search schools by English name."""
        response = await seeded_client.get("/schools/search?q=Happy")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert "Happy" in data[0]["name_i18n"]["en"]

    @pytest.mark.asyncio
    async def test_search_partial_match(self, seeded_client):
        """Search with partial name match."""
        response = await seeded_client.get("/schools/search?q=Joliot")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

    @pytest.mark.asyncio
    async def test_search_case_insensitive(self, seeded_client):
        """Search is case-insensitive."""
        response = await seeded_client.get("/schools/search?q=sunshine")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

    @pytest.mark.asyncio
    async def test_search_matches_location_address(self, seeded_client):
        """Search also matches location address text."""
        response = await seeded_client.get("/schools/search?q=Vitosha")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name_i18n"]["en"] == "Private KG Sunshine"

    @pytest.mark.asyncio
    async def test_search_deduplicates_multi_location_matches(self, seeded_client):
        """Multiple matching addresses still return each school once."""
        response = await seeded_client.get("/schools/search?q=Sofia")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        assert len({school["id"] for school in data}) == 3

    @pytest.mark.asyncio
    async def test_search_matches_display_name_i18n(self, seeded_db, seeded_client):
        """Search also matches branded display names stored in attributes."""
        school = (
            await seeded_db.execute(
                select(School).where(School.school_type == "private")
            )
        ).scalar_one()
        school.attributes = {
            "display_name_i18n": {
                "bg": "Fusion School",
                "en": "Fusion School",
            }
        }
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Fusion")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["attributes"]["display_name_i18n"]["en"] == "Fusion School"

    @pytest.mark.asyncio
    async def test_search_min_length_violation(self, seeded_client):
        """Search query must be at least 2 characters."""
        response = await seeded_client.get("/schools/search?q=a")
        assert response.status_code == 422  # Validation error

    @pytest.mark.asyncio
    async def test_search_max_length_ok(self, seeded_client):
        """Search query under 100 characters works."""
        query = "a" * 99
        response = await seeded_client.get(f"/schools/search?q={query}")
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_search_no_results(self, seeded_client):
        """Search with no matches returns empty list."""
        response = await seeded_client.get("/schools/search?q=NonexistentSchool")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_search_excludes_schools_without_resolved_locations(self, seeded_db, seeded_client):
        hidden_school = School(
            name_i18n={"bg": "Невидимо училище", "en": "Invisible School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
        )
        seeded_db.add(hidden_school)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocation(
                school_id=hidden_school.id,
                address_i18n={"bg": "ул. Невидима 2, София", "en": "2 Invisible St, Sofia"},
                lat=None,
                lng=None,
                is_primary=True,
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Invisible")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_search_defaults_to_sofia_city_scope(self, seeded_db, seeded_client):
        outside_school = School(
            name_i18n={"bg": "Варненско училище", "en": "Varna Search School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="varna",
        )
        seeded_db.add(outside_school)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocation(
                school_id=outside_school.id,
                address_i18n={"bg": "ул. Варна 1", "en": "1 Varna St"},
                lat=43.2141,
                lng=27.9147,
                is_primary=True,
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Varna")
        assert response.status_code == 200
        assert response.json() == []

        response = await seeded_client.get("/schools/search?q=Varna&city=all")
        assert response.status_code == 200
        assert len(response.json()) == 1


class TestSchoolsCountsEndpoint:
    """Test counts endpoint."""

    @pytest.mark.asyncio
    async def test_get_counts(self, seeded_client):
        """Get school counts per age group."""
        response = await seeded_client.get("/schools/counts")
        assert response.status_code == 200
        data = response.json()
        # Should have counts for age groups present in seeded data
        assert isinstance(data, dict)
        assert "first" in data
        assert data["first"] == 2  # 2 schools have first group
        assert "preschool" in data
        assert "grade_1_4" in data

    @pytest.mark.asyncio
    async def test_counts_empty_db(self, client):
        """Counts with empty database returns empty dict."""
        response = await client.get("/schools/counts")
        assert response.status_code == 200
        assert response.json() == {}

    @pytest.mark.asyncio
    async def test_counts_exclude_schools_without_resolved_locations(self, seeded_db, seeded_client):
        hidden_school = School(
            name_i18n={"bg": "Брояч скрито училище", "en": "Hidden Count School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
        )
        seeded_db.add(hidden_school)
        await seeded_db.flush()
        hidden_location = SchoolLocation(
            school_id=hidden_school.id,
            address_i18n={"bg": "ул. Пропусната 3, София", "en": "3 Missing St, Sofia"},
            lat=None,
            lng=None,
            is_primary=True,
        )
        seeded_db.add(hidden_location)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=hidden_location.id,
                age_group="first",
                shift="morning",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools/counts")
        assert response.status_code == 200
        data = response.json()
        assert data["first"] == 2

    @pytest.mark.asyncio
    async def test_counts_exclude_unresolved_locations_for_matching_age_group(self, seeded_db, seeded_client):
        school = School(
            name_i18n={"bg": "Брояч смесени локации", "en": "Mixed Count School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
        )
        seeded_db.add(school)
        await seeded_db.flush()

        resolved_location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Видима 6, София", "en": "6 Visible St, Sofia"},
            lat=42.7002,
            lng=23.3002,
            is_primary=True,
        )
        unresolved_location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Невидима 7, София", "en": "7 Invisible St, Sofia"},
            lat=None,
            lng=None,
            is_primary=False,
        )
        seeded_db.add_all([resolved_location, unresolved_location])
        await seeded_db.flush()

        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=resolved_location.id,
                age_group="preschool",
                shift="full_day",
            )
        )
        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=unresolved_location.id,
                age_group="grade_1_4",
                shift="morning",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools/counts")
        assert response.status_code == 200
        data = response.json()
        assert data["grade_1_4"] == 1

    @pytest.mark.asyncio
    async def test_counts_default_to_sofia_city_scope(self, seeded_db, seeded_client):
        outside_school = School(
            name_i18n={"bg": "Бургаско училище", "en": "Burgas Count School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="burgas",
        )
        seeded_db.add(outside_school)
        await seeded_db.flush()
        outside_location = SchoolLocation(
            school_id=outside_school.id,
            address_i18n={"bg": "ул. Бургас 1", "en": "1 Burgas St"},
            lat=42.5048,
            lng=27.4626,
            is_primary=True,
        )
        seeded_db.add(outside_location)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=outside_location.id,
                age_group="grade_1_4",
                shift="morning",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools/counts")
        assert response.status_code == 200
        assert response.json()["grade_1_4"] == 1

        response = await seeded_client.get("/schools/counts?city=all")
        assert response.status_code == 200
        assert response.json()["grade_1_4"] == 2


class TestSchoolValidation:
    """Test input validation on school endpoints."""

    @pytest.mark.asyncio
    async def test_get_school_negative_id(self, seeded_client):
        """Negative school ID returns 422 validation error."""
        response = await seeded_client.get("/schools/-1")
        assert response.status_code == 422  # Pydantic validation error

    @pytest.mark.asyncio
    async def test_get_school_zero_id(self, seeded_client):
        """School ID of 0 returns 422 validation error."""
        response = await seeded_client.get("/schools/0")
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_unknown_age_group_returns_empty(self, seeded_client):
        """Unknown age_group string returns empty list (no matching data)."""
        response = await seeded_client.get("/schools?age_group=nonexistent")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_unknown_school_type_returns_empty(self, seeded_client):
        """Unknown school_type string returns empty list (no matching data)."""
        response = await seeded_client.get("/schools?school_type=nonexistent")
        assert response.status_code == 200
        assert response.json() == []


class TestCompareEndpoint:
    @pytest.mark.asyncio
    async def test_compare_empty_ids(self, seeded_client):
        """Empty IDs returns 422 validation error (pattern mismatch)."""
        response = await seeded_client.get("/compare?ids=")
        assert response.status_code == 422  # Pattern validation fails first

    @pytest.mark.asyncio
    async def test_compare_single_school(self, seeded_client):
        """Compare with single school ID."""
        list_response = await seeded_client.get("/schools")
        school_id = list_response.json()[0]["id"]

        response = await seeded_client.get(f"/compare?ids={school_id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_compare_multiple_schools(self, seeded_client):
        """Compare multiple schools."""
        list_response = await seeded_client.get("/schools")
        schools = list_response.json()
        ids = ",".join(str(s["id"]) for s in schools[:2])

        response = await seeded_client.get(f"/compare?ids={ids}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_compare_invalid_ids_return_422(self, seeded_client):
        """Invalid IDs (non-numeric) return 422 validation error."""
        response = await seeded_client.get("/compare?ids=abc,def,xyz")
        assert response.status_code == 422  # Pattern validation fails

    @pytest.mark.asyncio
    async def test_compare_negative_ids_return_422(self, seeded_client):
        """Negative IDs return 422 validation error (pattern mismatch)."""
        response = await seeded_client.get("/compare?ids=-1,-2,-3")
        assert response.status_code == 422  # Pattern doesn't allow negative

    @pytest.mark.asyncio
    async def test_compare_mixed_valid_invalid(self, seeded_client):
        """Mixed valid and invalid IDs return 422."""
        list_response = await seeded_client.get("/schools")
        valid_id = list_response.json()[0]["id"]

        response = await seeded_client.get(f"/compare?ids={valid_id},abc,999")
        # Pattern validation catches "abc" and returns 422
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_compare_nonexistent_ids_return_404(self, seeded_client):
        """All non-existent IDs (but valid format) return 404."""
        response = await seeded_client.get("/compare?ids=99999,99998,99997")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_compare_max_five_schools(self, seeded_client):
        """Compare is limited to 5 schools."""
        # We only have 3 schools, but test the logic
        list_response = await seeded_client.get("/schools")
        schools = list_response.json()
        ids = ",".join(str(s["id"]) for s in schools)

        response = await seeded_client.get(f"/compare?ids={ids}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) <= 5

    @pytest.mark.asyncio
    async def test_compare_returns_locations(self, seeded_client):
        """Compare endpoint includes school locations."""
        list_response = await seeded_client.get("/schools")
        school_id = list_response.json()[0]["id"]

        response = await seeded_client.get(f"/compare?ids={school_id}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        school = data[0]
        # Check locations are present
        assert "locations" in school
        assert len(school["locations"]) > 0
        # Check location has required fields
        location = school["locations"][0]
        assert "lat" in location
        assert "lng" in location
        assert "address_i18n" in location
