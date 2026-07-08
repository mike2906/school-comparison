"""Tests for country API endpoints."""
import pytest

from app.models.country import Country


@pytest.fixture
def bg_country():
    """Bulgaria country config for testing."""
    return Country(
        code="bg",
        name_i18n={"bg": "България", "en": "Bulgaria"},
        education_config={
            "age_groups": [
                {"key": "nursery", "label_i18n": {"bg": "Ясла", "en": "Nursery"}, "min_diff": 0, "max_diff": 1, "category": "kindergarten"},
                {"key": "first", "label_i18n": {"bg": "Първа група", "en": "First group"}, "min_diff": 3, "max_diff": 3, "category": "kindergarten"},
            ],
            "education_levels": [
                {"key": "nursery", "label_i18n": {"bg": "Детска ясла", "en": "Nursery"}},
                {"key": "kindergarten", "label_i18n": {"bg": "Детска градина", "en": "Kindergarten"}},
            ],
            "school_types": [
                {"key": "state", "label_i18n": {"bg": "Държавно", "en": "State"}},
                {"key": "private", "label_i18n": {"bg": "Частно", "en": "Private"}},
            ],
            "shifts": [
                {"key": "morning", "label_i18n": {"bg": "Сутрешна смяна", "en": "Morning shift"}},
            ],
            "exam_types": [
                {"key": "nvo_7", "label_i18n": {"bg": "НВО 7 клас", "en": "NVO Grade 7"}},
            ],
            "exam_subjects": [
                {"key": "bulgarian", "label_i18n": {"bg": "Български", "en": "Bulgarian"}},
                {"key": "math", "label_i18n": {"bg": "Математика", "en": "Mathematics"}},
            ],
            "admission_systems": {"kindergarten_state": "points", "gymnasium": "nvo_score"},
            "grade_scale": {"min": 2, "max": 6},
            "grade_to_points_table": {"6.00": 50, "5.50": 39},
            "age_calculation_method": "enrollment_year_minus_birth_year",
        },
        map_config={
            "center": [42.6977, 23.3219],
            "bounds": [[42.50, 23.10], [42.86, 23.60]],
            "default_zoom": 12,
            "geocoding": {"country_codes": "bg", "city_suffix": ", София, България"},
        },
        supported_languages=["bg", "en"],
        default_language="bg",
        default_currency="BGN",
    )


class TestCountriesEndpoint:
    @pytest.mark.asyncio
    async def test_list_countries_empty(self, client):
        """Empty database returns empty list."""
        response = await client.get("/countries")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_countries_with_data(self, db_session, client, bg_country):
        """Returns countries when seeded."""
        db_session.add(bg_country)
        await db_session.commit()

        response = await client.get("/countries")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["code"] == "bg"
        assert data[0]["name_i18n"]["en"] == "Bulgaria"
        assert data[0]["default_currency"] == "BGN"
        # List endpoint should NOT include education_config (it's not in CountryListResponse)
        assert "education_config" not in data[0]

    @pytest.mark.asyncio
    async def test_get_country_not_found(self, client):
        """Returns 404 for non-existent country."""
        response = await client.get("/countries/xx")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_country_with_data(self, db_session, client, bg_country):
        """Returns full country config."""
        db_session.add(bg_country)
        await db_session.commit()

        response = await client.get("/countries/bg")
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == "bg"
        assert data["name_i18n"]["bg"] == "България"
        assert "education_config" in data
        assert "map_config" in data
        assert data["default_language"] == "bg"
        assert data["default_currency"] == "BGN"
        assert data["supported_languages"] == ["bg", "en"]

    @pytest.mark.asyncio
    async def test_get_country_education_config_structure(self, db_session, client, bg_country):
        """Education config has expected structure."""
        db_session.add(bg_country)
        await db_session.commit()

        response = await client.get("/countries/bg")
        config = response.json()["education_config"]
        assert "age_groups" in config
        assert "education_levels" in config
        assert "school_types" in config
        assert "shifts" in config
        assert "exam_types" in config
        assert "exam_subjects" in config
        assert "admission_systems" in config
        assert "grade_scale" in config
        assert "grade_to_points_table" in config
        assert "age_calculation_method" in config

    @pytest.mark.asyncio
    async def test_get_country_map_config_structure(self, db_session, client, bg_country):
        """Map config has expected structure."""
        db_session.add(bg_country)
        await db_session.commit()

        response = await client.get("/countries/bg")
        map_config = response.json()["map_config"]
        assert "center" in map_config
        assert "bounds" in map_config
        assert "default_zoom" in map_config
        assert "geocoding" in map_config
        assert len(map_config["center"]) == 2

    @pytest.mark.asyncio
    async def test_get_country_case_insensitive(self, db_session, client, bg_country):
        """Country code lookup is case-insensitive."""
        db_session.add(bg_country)
        await db_session.commit()

        response = await client.get("/countries/BG")
        assert response.status_code == 200
        assert response.json()["code"] == "bg"
