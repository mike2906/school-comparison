"""Tests for API endpoints."""
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.exam_results import ExamResult
from app.models.field_source import FieldSource, SourceType
from app.models.pricing import Pricing, PriceSource
from app.models.school import School, SchoolLocation, SchoolLocationAgeGroupShift

PRICING_VERIFIED_AT = datetime(2026, 7, 16, 9, 0)


def _validation_report(issues=None, spot_check=None, status="needs_review"):
    return {
        "_schema_version": 1,
        "validated_at": "2026-03-11T00:00:00+00:00",
        "status": status,
        "issue_counts": {"error": len(issues or []), "warning": 0},
        "issues": issues or [],
        "auto_fixes": [],
        "spot_check": spot_check,
    }


def _verified_pricing_context(confidence=1.0, **extra):
    return {
        "confidence": confidence,
        "human_verification": {
            "verified_by": "test-curator",
            "verified_at": "2026-07-16T09:00:00Z",
        },
        **extra,
    }


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
        assert "exam_results" in school
        assert "field_sources" not in school

    @pytest.mark.asyncio
    async def test_list_schools_serializes_exam_results_used_by_school_cards(
        self,
        seeded_db,
        seeded_client,
    ):
        school = (
            await seeded_db.execute(
                select(School).where(School.education_level == "primary")
            )
        ).scalar_one()
        seeded_db.add(
            ExamResult(
                school_id=school.id,
                year=2025,
                exam_type="nvo_4",
                subject="math",
                metric="average_score",
                value=78.25,
                source_url="https://example.edu/nvo-2025",
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools")

        assert response.status_code == 200
        payload = next(item for item in response.json() if item["id"] == school.id)
        assert len(payload["exam_results"]) == 1
        assert payload["exam_results"][0] == {
            "id": payload["exam_results"][0]["id"],
            "school_id": school.id,
            "year": 2025,
            "exam_type": "nvo_4",
            "subject": "math",
            "metric": "average_score",
            "value": 78.25,
            "source_url": "https://example.edu/nvo-2025",
        }

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
        assert "exam_results" in school
        assert "field_sources" in school

    @pytest.mark.asyncio
    async def test_json_backed_response_fields_use_positive_allowlists(self, seeded_db, seeded_client):
        school = School(
            name_i18n={"bg": "Публично училище", "en": "Public School"},
            country_code="bg",
            city="sofia",
            school_type="private",
            education_level="primary",
            website_url="https://public-school.bg",
            scrape_status="extracted",
            attributes={"data_validation": _validation_report(status="ok")},
            admission_info={
                "system": "interview",
                "status": "accepting",
                "website_extracted": {"has_useful_info": True, "deadlines": ["Internal raw value"]},
                "internal_note": "must not ship",
            },
        )
        seeded_db.add(school)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocation(
                school_id=school.id,
                address_i18n={"bg": "София"},
                lat=42.7,
                lng=23.3,
                is_primary=True,
            )
        )
        seeded_db.add(
            Pricing(
                school_id=school.id,
                category="tuition",
                amount=500,
                currency="BGN",
                period="monthly",
                source=PriceSource.OFFICIAL,
                scraped_at=PRICING_VERIFIED_AT,
                source_url="https://public-school.bg/fees",
                pricing_context=_verified_pricing_context(
                    0.9,
                    includes=["Books"],
                    internal_prompt_trace="must not ship",
                ),
            )
        )
        seeded_db.add(
            FieldSource(
                school_id=school.id,
                category="general_info",
                field_key="attributes.admission",
                field_path="attributes.extracted.admission",
                value_text="Admissions",
                value_json={"raw": "must not ship"},
                source_type=SourceType.SCRAPED_WEBSITE,
                source_url="https://public-school.bg/admissions",
                notes="internal note",
            )
        )
        await seeded_db.commit()

        list_response = await seeded_client.get("/schools")
        listed = next(row for row in list_response.json() if row["id"] == school.id)
        assert listed["admission_info"]["system"] == "interview"
        assert listed["admission_info"]["status"] == "accepting"
        assert "website_extracted" not in listed["admission_info"]
        assert "internal_note" not in listed["admission_info"]

        detail_response = await seeded_client.get(f"/schools/{school.id}")
        detail = detail_response.json()
        assert "website_extracted" not in detail["admission_info"]
        assert "internal_note" not in detail["admission_info"]
        assert "internal_prompt_trace" not in detail["pricing"][0]["pricing_context"]
        assert detail["pricing"][0]["pricing_context"]["includes"] == ["Books"]
        source = detail["field_sources"][0]
        assert set(source) == {
            "source_type",
            "source_url",
            "last_verified",
            "confidence",
        }

    @pytest.mark.asyncio
    async def test_unpublishable_website_state_withholds_all_scraped_payloads(
        self, seeded_db, seeded_client
    ):
        school = School(
            name_i18n={"bg": "Регистърно име", "en": "Registry Name"},
            country_code="bg",
            city="sofia",
            school_type="private",
            education_level="primary",
            website_url=None,
            scrape_status="no_official_website",
            summary_i18n={
                "bg": {"short": "Старо", "long": "Старо резюме"},
                "en": {"short": "Old", "long": "Old summary"},
            },
            attributes={
                "display_name_i18n": {"bg": "Старо име", "en": "Old name"},
                "display_name_evidence": {"status": "corroborated"},
                "extracted": {"facilities": ["Old pool"]},
                "data_validation": _validation_report(status="ok"),
            },
            admission_info={"status": "accepting", "website_extracted": {"deadlines": ["Old"]}},
        )
        seeded_db.add(school)
        await seeded_db.flush()
        seeded_db.add_all(
            [
                Pricing(
                    school_id=school.id,
                    category="tuition",
                    amount=500,
                    currency="BGN",
                    period="monthly",
                    source=PriceSource.SCRAPED_WEBSITE,
                    source_url="https://old.bg/fees",
                    pricing_context={"confidence": 0.9},
                ),
                Pricing(
                    school_id=school.id,
                    category="activities",
                    amount=50,
                    currency="BGN",
                    period="monthly",
                    source=PriceSource.OFFICIAL,
                    scraped_at=PRICING_VERIFIED_AT,
                    source_url="https://registry.bg/fees",
                    pricing_context=_verified_pricing_context(),
                ),
                FieldSource(
                    school_id=school.id,
                    category="general_info",
                    field_key="attributes.facilities",
                    source_type=SourceType.SCRAPED_WEBSITE,
                    source_url="https://old.bg",
                ),
                FieldSource(
                    school_id=school.id,
                    category="admission",
                    field_key="admission_info",
                    source_type=SourceType.GOVERNMENT,
                    source_url="https://registry.bg",
                ),
            ]
        )
        await seeded_db.commit()

        response = await seeded_client.get(f"/schools/{school.id}")
        payload = response.json()
        assert payload["resolved_name_i18n"] == school.name_i18n
        assert payload["attributes_i18n"]["bg"]["facilities"] == []
        assert payload["summary_i18n"] is None
        assert [row["source"] for row in payload["pricing"]] == ["official"]
        assert [row["source_type"] for row in payload["field_sources"]] == ["government"]
        assert payload["admission_info"]["status"] == "accepting"
        assert "website_extracted" not in payload["admission_info"]

    @pytest.mark.asyncio
    async def test_rejected_school_529_value_cannot_escape_through_provenance(
        self, seeded_db, seeded_client
    ):
        school = School(
            id=529,
            name_i18n={"bg": "Училище 529", "en": "School 529"},
            country_code="bg",
            city="sofia",
            school_type="private",
            education_level="primary",
            scrape_status="extracted",
            attributes={
                "extracted": {"class_size": "9 students"},
                "data_validation": _validation_report(
                    issues=[
                        {
                            "code": "unsupported_class_size",
                            "severity": "error",
                            "field_path": "attributes.extracted.class_size",
                            "message": "unsupported",
                        }
                    ]
                ),
            },
        )
        seeded_db.add(school)
        await seeded_db.flush()
        seeded_db.add(
            FieldSource(
                school_id=school.id,
                category="general_info",
                field_key="class_size",
                field_path="attributes.extracted.class_size",
                value_text="9 students",
                value_json={"class_size": 9},
                source_type=SourceType.SCRAPED_WEBSITE,
                source_url="https://school529.test/about",
            )
        )
        await seeded_db.commit()

        responses = [
            await seeded_client.get(f"/schools/{school.id}"),
            await seeded_client.get(f"/compare?ids={school.id}"),
        ]
        for response in responses:
            assert response.status_code == 200
            assert "9 students" not in response.text
            assert "value_text" not in response.text

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
                assert "geocode_meta" not in location

    @pytest.mark.asyncio
    async def test_location_tags_serialize_semantic_only(self, seeded_db, seeded_client):
        """P1.8: provenance/coords tags are withheld; semantic focus tags still render."""
        school = School(
            name_i18n={"bg": "Училище с етикети", "en": "Tagged School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
        )
        seeded_db.add(school)
        await seeded_db.flush()
        location = SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тест 1, София", "en": "1 Test St, Sofia"},
            lat=42.7,
            lng=23.3,
            is_primary=True,
            location_tags=[
                "source=moe_registry",
                "source_esri_id=123",
                "coords_source=nominatim_approximate",
                "science_focus",
            ],
        )
        seeded_db.add(location)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocationAgeGroupShift(
                location_id=location.id, age_group="grade_1_4", shift="morning"
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get(f"/schools/{school.id}")
        assert response.status_code == 200
        tags = response.json()["locations"][0]["location_tags"]
        assert tags == ["science_focus"]
        assert not any("source" in tag or "coords" in tag for tag in tags)

    @pytest.mark.asyncio
    async def test_list_keeps_terminally_unresolved_school(self, seeded_db, seeded_client):
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
            geocode_meta={
                "status": "failed",
                "provider": "nominatim",
                "rejection_reason": "No results found",
            },
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
        schools = {school["id"]: school for school in response.json()}
        assert hidden_school.id in schools
        assert schools[hidden_school.id]["locations"][0]["lat"] is None
        assert schools[hidden_school.id]["locations"][0]["lng"] is None

    @pytest.mark.asyncio
    async def test_list_still_excludes_unresolved_school_without_failure_evidence(
        self,
        seeded_db,
        seeded_client,
    ):
        pending_school = School(
            name_i18n={"bg": "Непроверено училище", "en": "Pending School"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
        )
        seeded_db.add(pending_school)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocation(
                school_id=pending_school.id,
                address_i18n={"bg": "ул. Непроверена 1, София"},
                lat=None,
                lng=None,
                geocode_meta={"status": "failed", "provider": "nominatim"},
                is_primary=True,
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools")

        assert response.status_code == 200
        assert pending_school.id not in {school["id"] for school in response.json()}

    @pytest.mark.asyncio
    async def test_list_excludes_transiently_unresolved_school(
        self,
        seeded_db,
        seeded_client,
    ):
        school = School(
            name_i18n={"bg": "Временно недостъпно училище"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
        )
        seeded_db.add(school)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocation(
                school_id=school.id,
                address_i18n={"bg": "ул. Временна 503"},
                geocode_meta={
                    "status": "failed",
                    "provider": "nominatim",
                    "rejection_reason": "HTTP 503",
                },
                is_primary=True,
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools")

        assert response.status_code == 200
        assert school.id not in {item["id"] for item in response.json()}

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
    async def test_age_group_filter_keeps_terminally_unresolved_matching_location(self, seeded_db, seeded_client):
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
            geocode_meta={
                "status": "failed",
                "provider": "nominatim",
                "rejection_reason": "No results found",
            },
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
        assert school.id in ids

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
        """Search matches corroborated branded display names stored in attributes."""
        school = (
            await seeded_db.execute(
                select(School).where(School.school_type == "private")
            )
        ).scalar_one()
        school.attributes = {
            "data_validation": {"_schema_version": 1, "status": "ok"},
            "display_name_i18n": {
                "bg": "Fusion School",
                "en": "Fusion School",
            },
            "display_name_evidence": {
                "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
                "status": "corroborated",
            },
        }
        school.scrape_status = "extracted"
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Fusion")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        # display_name_i18n is internal; the resolved name is what reaches the client.
        assert data[0]["resolved_name_i18n"]["en"] == "Fusion School"
        assert "display_name_i18n" not in data[0]["attributes"]

    @pytest.mark.asyncio
    async def test_search_ignores_withheld_display_name(self, seeded_db, seeded_client):
        school = (
            await seeded_db.execute(select(School).where(School.school_type == "private"))
        ).scalar_one()
        school.scrape_status = "extracted"
        school.attributes = {
            "website_data_withheld": True,
            "display_name_i18n": {"bg": "Hidden Fusion", "en": "Hidden Fusion"},
            "display_name_evidence": {
                "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
                "status": "corroborated",
            },
        }
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Hidden%20Fusion")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_advanced_filter_ignores_withheld_extracted_values(self, seeded_db, seeded_client):
        school = (
            await seeded_db.execute(select(School).where(School.school_type == "private"))
        ).scalar_one()
        school.scrape_status = "extracted"
        school.attributes = {
            "website_data_withheld": True,
            "extracted": {"programs": ["Montessori"]},
        }
        await seeded_db.commit()

        response = await seeded_client.get("/schools?teaching_approach=montessori")

        assert response.status_code == 200
        assert all(row["id"] != school.id for row in response.json())

    @pytest.mark.asyncio
    async def test_search_ignores_uncorroborated_display_name_i18n(self, seeded_db, seeded_client):
        """Uncorroborated internal display names should not affect public search."""
        school = (
            await seeded_db.execute(
                select(School).where(School.school_type == "private")
            )
        ).scalar_one()
        school.attributes = {
            "display_name_i18n": {
                "bg": "Admissions Headline",
                "en": "Admissions Headline",
            }
        }
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Admissions%20Headline")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_search_ignores_corroborated_non_identity_display_name(
        self, seeded_db, seeded_client
    ):
        """Hidden prose must not affect search after the identity gate rejects it."""
        school = (
            await seeded_db.execute(select(School).where(School.school_type == "private"))
        ).scalar_one()
        school.scrape_status = "extracted"
        school.attributes = {
            "display_name_i18n": {
                "bg": "Our Values At Hidden Academy",
                "en": "Our Values At Hidden Academy",
            },
            "display_name_evidence": {
                "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
                "status": "corroborated",
            },
        }
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Our%20Values%20At")

        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_search_requires_exact_display_evidence_signals(self, seeded_db, seeded_client):
        """Malformed lookalike evidence values must not unlock internal display-name search."""
        school = (
            await seeded_db.execute(
                select(School).where(School.school_type == "private")
            )
        ).scalar_one()
        school.attributes = {
            "display_name_i18n": {
                "bg": "Hidden Brand",
                "en": "Hidden Brand",
            },
            "display_name_evidence": {
                "signals": ["not_website_domain_alias_match", "not_repeated_on_page_identity"],
                "status": "uncorroborated",
            },
        }
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Hidden%20Brand")
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_search_matches_resolved_english_name_fallback(self, seeded_db, seeded_client):
        """Search includes derived English names visible in the API response."""
        school = School(
            name_i18n={"bg": "Д-р Петър Берон"},
            country_code="bg",
            school_type="state",
            education_level="primary",
            city="sofia",
            scrape_status="extracted",
            attributes={},
        )
        seeded_db.add(school)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocation(
                school_id=school.id,
                address_i18n={"bg": "ул. Рила 1, София"},
                lat=42.7,
                lng=23.3,
                is_primary=True,
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Petar%20Beron")
        assert response.status_code == 200
        data = response.json()

        assert any(item["id"] == school.id for item in data)

    @pytest.mark.asyncio
    async def test_search_blank_normalized_query_returns_empty(self, seeded_client):
        """Whitespace-only terms satisfy min length but should not match everything."""
        response = await seeded_client.get("/schools/search?q=%20%20")
        assert response.status_code == 200
        assert response.json() == []

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
    async def test_search_keeps_terminally_unresolved_school(self, seeded_db, seeded_client):
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
                geocode_meta={
                    "status": "failed",
                    "provider": "nominatim",
                    "rejection_reason": "No results found",
                },
                is_primary=True,
            )
        )
        await seeded_db.commit()

        response = await seeded_client.get("/schools/search?q=Invisible")
        assert response.status_code == 200
        assert [school["id"] for school in response.json()] == [hidden_school.id]

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
    async def test_counts_include_terminally_unresolved_locations(self, seeded_db, seeded_client):
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
            geocode_meta={
                "status": "failed",
                "provider": "nominatim",
                "rejection_reason": "No results found",
            },
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
        assert data["first"] == 3

    @pytest.mark.asyncio
    async def test_counts_include_terminally_unresolved_matching_age_group(self, seeded_db, seeded_client):
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
            geocode_meta={
                "status": "rejected",
                "provider": "geojson_bg",
                "rejection_reason": "duplicate_geojson_name_match_different_address",
            },
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
        assert data["grade_1_4"] == 2

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


class TestDisplayGating:
    """P1.7: validator-rejected fields and ungated pricing never reach the API."""

    @pytest.mark.asyncio
    async def test_pricing_rows_are_curated_only_across_parent_endpoints(
        self, seeded_db, seeded_client
    ):
        school = School(
            name_i18n={"bg": "Ценово училище", "en": "Pricing School"},
            country_code="bg",
            school_type="private",
            education_level="primary",
            city="sofia",
            scrape_status="extracted",
            attributes={},
        )
        seeded_db.add(school)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocation(
                school_id=school.id,
                address_i18n={"bg": "София", "en": "Sofia"},
                lat=42.72,
                lng=23.32,
                is_primary=True,
            )
        )
        seeded_db.add_all(
            [
                Pricing(
                    school_id=school.id,
                    category="tuition",
                    amount=500,
                    currency="BGN",
                    period="monthly",
                    source=PriceSource.SCRAPED_WEBSITE,
                    source_url="https://example.com/fees",
                    pricing_context={"confidence": 0.9},
                ),
                Pricing(
                    school_id=school.id,
                    category="activities",
                    amount=50,
                    currency="BGN",
                    period="monthly",
                    source=PriceSource.OFFICIAL,
                    scraped_at=PRICING_VERIFIED_AT,
                    source_url="https://example.com/verified-fees",
                    pricing_context=_verified_pricing_context(),
                ),
                # No source_url → withheld.
                Pricing(
                    school_id=school.id,
                    category="food",
                    amount=100,
                    currency="BGN",
                    period="monthly",
                    source=PriceSource.SCRAPED_WEBSITE,
                    source_url=None,
                    pricing_context={"confidence": 0.9},
                ),
                # Confidence below the floor → withheld.
                Pricing(
                    school_id=school.id,
                    category="transport",
                    amount=80,
                    currency="BGN",
                    period="monthly",
                    source=PriceSource.SCRAPED_WEBSITE,
                    source_url="https://example.com/fees",
                    pricing_context={"confidence": 0.4},
                ),
                # A numeric-looking string must not be coerced through the gate.
                Pricing(
                    school_id=school.id,
                    category="registration",
                    amount=50,
                    currency="BGN",
                    period="monthly",
                    source=PriceSource.SCRAPED_WEBSITE,
                    source_url="https://example.com/fees",
                    pricing_context={"confidence": "0.9"},
                ),
            ]
        )
        await seeded_db.commit()

        responses = [
            await seeded_client.get(f"/schools/{school.id}"),
            await seeded_client.get("/schools"),
            await seeded_client.get(f"/compare?ids={school.id}"),
        ]
        for response in responses:
            assert response.status_code == 200
            payload = response.json()
            school_payload = payload if isinstance(payload, dict) else next(
                row for row in payload if row["id"] == school.id
            )
            assert [row["category"] for row in school_payload["pricing"]] == ["activities"]
            assert school_payload["pricing"][0]["source"] == "official"

    @pytest.mark.asyncio
    async def test_pricing_row_with_validation_error_is_hidden(self, seeded_db, seeded_client):
        school = School(
            name_i18n={"bg": "Ценово училище 2", "en": "Pricing School 2"},
            country_code="bg",
            school_type="private",
            education_level="primary",
            city="sofia",
            scrape_status="extracted",
            attributes={},
        )
        seeded_db.add(school)
        await seeded_db.flush()
        good = Pricing(
            school_id=school.id,
            category="tuition",
            amount=500,
            currency="BGN",
            period="monthly",
            source=PriceSource.OFFICIAL,
            scraped_at=PRICING_VERIFIED_AT,
            source_url="https://example.com/fees",
            pricing_context=_verified_pricing_context(0.9),
        )
        # Clears the source/confidence gate, but Stage 6 flagged it as a bad price.
        flagged = Pricing(
            school_id=school.id,
            category="food",
            amount=-10,
            currency="BGN",
            period="monthly",
            source=PriceSource.OFFICIAL,
            scraped_at=PRICING_VERIFIED_AT,
            source_url="https://example.com/fees",
            pricing_context=_verified_pricing_context(0.9),
        )
        seeded_db.add_all([good, flagged])
        await seeded_db.flush()
        school.attributes = {
            "data_validation": _validation_report(
                issues=[
                    {
                        "code": "negative_price_amount",
                        "severity": "error",
                        "field_path": f"pricing[{flagged.id}].amount",
                        "message": "negative amount",
                    }
                ],
            )
        }
        await seeded_db.commit()

        response = await seeded_client.get(f"/schools/{school.id}")
        assert response.status_code == 200
        pricing = response.json()["pricing"]
        assert [row["category"] for row in pricing] == ["tuition"]

    @pytest.mark.asyncio
    async def test_stored_summary_hidden_when_validation_not_ok(
        self, seeded_db, seeded_client, monkeypatch
    ):
        from app.config import get_settings

        monkeypatch.setattr(get_settings(), "publish_summaries", True)
        summary = {"bg": {"short": "кратко", "long": "дълго"}}
        unvalidated = School(
            name_i18n={"bg": "Резюме без отчет", "en": "Summary Without Report"},
            country_code="bg",
            school_type="private",
            education_level="primary",
            city="sofia",
            scrape_status="summarized",
            summary_i18n=summary,
            attributes={},
        )
        needs_review = School(
            name_i18n={"bg": "Резюме А", "en": "Summary A"},
            country_code="bg",
            school_type="private",
            education_level="primary",
            city="sofia",
            scrape_status="summarized",
            summary_i18n=summary,
            attributes={"data_validation": _validation_report(status="needs_review")},
        )
        clean = School(
            name_i18n={"bg": "Резюме Б", "en": "Summary B"},
            country_code="bg",
            school_type="private",
            education_level="primary",
            city="sofia",
            scrape_status="summarized",
            summary_i18n=summary,
            attributes={"data_validation": _validation_report(status="ok", issues=[])},
        )
        seeded_db.add_all([unvalidated, needs_review, clean])
        await seeded_db.commit()

        no_report = (await seeded_client.get(f"/schools/{unvalidated.id}")).json()
        assert no_report["summary_i18n"] is None

        hidden = (await seeded_client.get(f"/schools/{needs_review.id}")).json()
        assert hidden["summary_i18n"] is None

        shown = (await seeded_client.get(f"/schools/{clean.id}")).json()
        assert shown["summary_i18n"] == summary

    @pytest.mark.asyncio
    async def test_launch_flag_withholds_clean_summaries_from_all_endpoints(
        self, seeded_db, seeded_client
    ):
        summary = {"bg": {"short": "кратко", "long": "дълго"}}
        school = School(
            name_i18n={"bg": "Резюме", "en": "Summary"},
            country_code="bg",
            school_type="private",
            education_level="primary",
            city="sofia",
            scrape_status="summarized",
            summary_i18n=summary,
            attributes={"data_validation": _validation_report(status="ok")},
        )
        seeded_db.add(school)
        await seeded_db.flush()
        seeded_db.add(
            SchoolLocation(
                school_id=school.id,
                address_i18n={"bg": "София", "en": "Sofia"},
                lat=42.71,
                lng=23.31,
                is_primary=True,
            )
        )
        await seeded_db.commit()

        responses = [
            await seeded_client.get("/schools"),
            await seeded_client.get(f"/schools/{school.id}"),
            await seeded_client.get(f"/compare?ids={school.id}"),
        ]
        for response in responses:
            assert response.status_code == 200
            payload = response.json()
            school_payload = payload if isinstance(payload, dict) else next(
                row for row in payload if row["id"] == school.id
            )
            assert school_payload.get("summary_i18n") is None

    @pytest.mark.asyncio
    async def test_website_admission_flag_preserves_curated_admission_info(
        self, seeded_db, seeded_client, monkeypatch
    ):
        from app.config import get_settings

        school = School(
            name_i18n={"bg": "Прием", "en": "Admission"},
            country_code="bg",
            school_type="private",
            education_level="primary",
            city="sofia",
            scrape_status="extracted",
            admission_info={"status": "accepting", "requirements": "Official interview"},
            attributes={
                "extracted": {
                    "admission": {
                        "entrance_requirements": ["Website test"],
                        "deadlines": ["30 юни"],
                        "available_spots": ["12 свободни места"],
                        "application_steps": ["Internal extra child"],
                    }
                },
                "data_validation": _validation_report(status="ok"),
            },
        )
        seeded_db.add(school)
        await seeded_db.commit()

        hidden = (await seeded_client.get(f"/schools/{school.id}")).json()
        assert hidden["admission_info"] == {
            "status": "accepting",
            "requirements": "Official interview",
        }
        for locale in ("bg", "en"):
            assert hidden["attributes_i18n"][locale]["entry_requirements"] == []
            assert hidden["attributes_i18n"][locale]["application_deadlines"] == []
            assert hidden["attributes_i18n"][locale]["available_spots"] == []
        assert "Internal extra child" not in str(hidden)

        monkeypatch.setattr(get_settings(), "publish_website_admission_fields", True)
        shown = (await seeded_client.get(f"/schools/{school.id}")).json()
        for locale in ("bg", "en"):
            assert shown["attributes_i18n"][locale]["entry_requirements"] == ["Website test"]
            assert shown["attributes_i18n"][locale]["application_deadlines"] == ["30 юни"]
            assert shown["attributes_i18n"][locale]["available_spots"] == ["12 свободни места"]
        assert shown["admission_info"] == hidden["admission_info"]

    @pytest.mark.asyncio
    async def test_display_field_hidden_when_validation_error(self, seeded_db, seeded_client):
        school = School(
            name_i18n={"bg": "Тест атрибути", "en": "Attr School"},
            country_code="bg",
            school_type="private",
            education_level="primary",
            city="sofia",
            scrape_status="extracted",
            attributes={
                "extracted": {
                    "facilities": ["Library", "Gym"],
                    "programs": ["STEM"],
                },
                "data_validation": _validation_report(
                    issues=[
                        {
                            "code": "facilities_unsupported",
                            "severity": "error",
                            "field_path": "attributes.extracted.facilities",
                            "message": "not supported by source",
                        }
                    ],
                ),
            },
        )
        seeded_db.add(school)
        await seeded_db.commit()

        response = await seeded_client.get(f"/schools/{school.id}")
        assert response.status_code == 200
        attributes_i18n = response.json()["attributes_i18n"]
        # Flagged field is withheld in every locale...
        assert attributes_i18n["bg"]["facilities"] == []
        assert attributes_i18n["en"]["facilities"] == []
        # ...while an unaffected field still comes through.
        assert attributes_i18n["bg"]["special_programs"] == ["STEM"]
