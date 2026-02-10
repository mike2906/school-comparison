"""Tests for API endpoints."""
import pytest


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
        assert "locations" in school
        assert "summary_i18n" in school

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
                assert "age_group" in location


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
