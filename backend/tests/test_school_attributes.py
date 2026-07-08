"""Tests for the public projection of `schools.attributes`.

`schools.attributes` is an internal JSONB scratchpad. These tests pin the boundary:
what the API is allowed to serve, and that the merge of `extracted` / `extracted_i18n`
happens server-side rather than in the browser.
"""

import ast
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.school import School, SchoolLocation
from app.schemas.school import (
    SchoolDisplayAttributes,
    SchoolListResponse,
    SchoolLocalizedAttributes,
    SchoolResponse,
)
from app.utils.school_attributes import (
    build_base_attributes,
    build_display_attributes,
    build_filterable_attributes,
    build_localized_attributes,
)

# A realistic slice of what the pipeline actually writes, taken from production rows.
INTERNAL_ATTRIBUTES = {
    "moe_bulstat": "000670625",
    "moe_email": "director@school.bg",
    "moe_region_code": 23,
    "moe_abbreviation": "СУ",
    "moe_registry_active": True,
    "kg_sofia_id": 51,
    "kg_sofia_esri_id": 12345,
    "source_refs": {"kg_sofia_bg": {"record_id": "51"}},
    "website_candidate_url": "https://example.bg",
    "website_candidate_reason": "domain alias match",
    "validated_website_url": "https://example.bg",
    "url_validation_timeout_failures": 0,
    "name_aliases": ["СУ Пример"],
    "_extraction_hashes": {"general_info": "abc123"},
    "data_validation": {"status": "needs_review", "issues": [{"field": "pricing"}]},
    "display_name_i18n": {"bg": "Училище Пример", "en": "Example School"},
    "extracted": {
        "_schema_version": 1,
        "languages": [{"language": "Английски", "level": "intensive"}],
        "facilities": ["Библиотека"],
        "programs": ["Спортна програма"],
        "accreditations": ["ISO 9001"],
        "extracurricular": ["Шахмат"],
        "class_size": "до 16 ученици в клас",
        "founded_year": 1998,
        "admission": {"deadlines": ["30 юни"]},
        "operations": {"meals": ["Обяд"]},
        "services": {"support_services": ["Психолог"]},
        "pricing_terms": {"discounts": ["10% за второ дете"]},
        "summary_source": {"highlights": ["Silver medal"]},
        "contact": {"phone": "+359 2 000 0000"},
    },
    "extracted_i18n": {
        "en": {
            "languages": [{"language": "English", "level": "intensive"}],
            "facilities": ["Library"],
            "programs": ["Sports program"],
            "accreditations": ["ISO 9001"],
            "extracurricular": ["Chess"],
        }
    },
}

INTERNAL_KEY_MARKERS = (
    "extracted",
    "extracted_i18n",
    "data_validation",
    "source_refs",
    "display_name_i18n",
    "moe_",
    "kg_sofia",
    "website_candidate",
    "validated_website_url",
    "url_validation",
    "name_aliases",
    "_extraction_hashes",
    "_schema_version",
    "summary_source",
    "pricing_terms",
)


class TestClassSize:
    """Ported from frontend/src/utils/schoolAttributes.test.js."""

    def test_parses_when_class_context_exists(self):
        base = build_base_attributes({"extracted": {"class_size": "Up to 16 students per class"}})
        assert base["class_size"] == 16

    def test_does_not_parse_unrelated_numeric_text(self):
        base = build_base_attributes({"extracted": {"class_size": "Grades 1-4 program"}})
        assert base["class_size"] is None

    def test_existing_numeric_class_size_wins(self):
        base = build_base_attributes({"class_size": 18, "extracted": {"class_size": "12 students"}})
        assert base["class_size"] == 18

    def test_bulgarian_class_context(self):
        base = build_base_attributes({"extracted": {"class_size": "до 16 ученици в клас"}})
        assert base["class_size"] == 16

    def test_integral_values_stay_int(self):
        base = build_base_attributes({"class_size": "16.0"})
        assert base["class_size"] == 16
        assert isinstance(base["class_size"], int)

    @pytest.mark.parametrize("value", [0, -5, "0 students", None, True, "no cap"])
    def test_rejects_non_sizes(self, value):
        assert build_base_attributes({"class_size": value})["class_size"] is None


class TestLocalizedProjection:
    def test_prefers_extracted_i18n_for_locale(self):
        localized = build_localized_attributes(INTERNAL_ATTRIBUTES, "en")
        assert localized["facilities"] == ["Library"]
        assert localized["activities_offered"] == ["Chess"]
        assert localized["special_programs"] == ["Sports program", "ISO 9001"]

    def test_falls_back_to_primary_extracted_when_locale_missing(self):
        localized = build_localized_attributes(INTERNAL_ATTRIBUTES, "bg")
        assert localized["facilities"] == ["Библиотека"]
        assert localized["activities_offered"] == ["Шахмат"]

    def test_language_focus_is_structured(self):
        localized = build_localized_attributes(INTERNAL_ATTRIBUTES, "en")
        assert localized["language_focus"] == [{"language": "English", "level": "intensive"}]
        assert localized["languages_of_instruction"] == ["English"]

    def test_merges_top_level_with_extracted(self):
        localized = build_localized_attributes(
            {"facilities": ["cafeteria"], "extracted": {"facilities": ["Library"]}},
            "bg",
        )
        assert localized["facilities"] == ["cafeteria", "Library"]

    def test_dedupes_case_insensitively(self):
        localized = build_localized_attributes(
            {"facilities": ["Library"], "extracted": {"facilities": ["library", "Library "]}},
            "bg",
        )
        assert localized["facilities"] == ["Library"]

    def test_string_language_focus_splits_on_colon(self):
        localized = build_localized_attributes({"language_focus": ["English:Early Foreign"]}, "bg")
        assert localized["language_focus"] == [
            {"language": "English", "level": "early_foreign"}
        ]

    def test_rejects_stringified_objects(self):
        localized = build_localized_attributes(
            {"extracted": {"facilities": ["{'name': 'Library'}", "[1, 2]"]}}, "bg"
        )
        assert localized["facilities"] == ["Library"]

    def test_extracts_named_value_from_objects(self):
        localized = build_localized_attributes(
            {"extracted": {"facilities": [{"name": "Gym", "source": "page-3"}]}}, "bg"
        )
        assert localized["facilities"] == ["Gym"]

    def test_handles_missing_attributes(self):
        base, localized = build_display_attributes(None)
        assert base["class_size"] is None
        assert localized["bg"]["facilities"] == []
        assert localized["en"]["facilities"] == []


class TestSeededDisplayFields:
    """Fields only `scripts/seed_data.py` writes, but `SchoolDetailPage` renders.

    No scraped school has these, so the allowlist silently dropped them at first and
    the demo/seed UI lost three tiles. Keep them pinned.
    """

    SEEDED = {
        "teacher_student_ratio": "1:12",
        "school_hours": "8:00-17:00",
        "established_year": 1975,
    }

    def test_seeded_display_fields_survive_the_allowlist(self):
        base = build_base_attributes(self.SEEDED)
        assert base["teacher_student_ratio"] == "1:12"
        assert base["school_hours"] == "8:00-17:00"
        assert base["established_year"] == 1975

    @pytest.mark.parametrize("value", ["", "  ", None])
    def test_blank_ratio_is_dropped(self, value):
        assert build_base_attributes({"teacher_student_ratio": value})["teacher_student_ratio"] is None

    @pytest.mark.parametrize("value", ["not a year", 75, 12345, True, None, 3000])
    def test_implausible_established_year_is_dropped(self, value):
        assert build_base_attributes({"established_year": value})["established_year"] is None

    def test_numeric_string_year_is_coerced(self):
        assert build_base_attributes({"established_year": "1975"})["established_year"] == 1975

    def test_founded_year_is_not_silently_promoted(self):
        # extracted.founded_year is the scraped analogue; surfacing it would be a
        # behaviour change, not a port. See P1.10.
        base = build_base_attributes({"extracted": {"founded_year": 1998}})
        assert base["established_year"] is None


class TestFilterableProjection:
    def test_unions_locales_so_either_language_matches(self):
        filterable = build_filterable_attributes(INTERNAL_ATTRIBUTES)
        assert filterable["facilities"] == ["Библиотека", "Library"]
        assert {entry["language"] for entry in filterable["language_focus"]} == {
            "Английски",
            "English",
        }

    def test_preserves_canonical_top_level_values(self):
        filterable = build_filterable_attributes({"facilities": ["cafeteria"], "teaching_approach": ["montessori"]})
        assert filterable["facilities"] == ["cafeteria"]
        assert filterable["teaching_approach"] == ["montessori"]


class TestAllowlistCoversWrittenFields:
    """The allowlist must not silently drop fields the app actually writes.

    `seed_data.py` is the reference dataset (AGENTS.md), so every attribute key it
    writes has to survive the projection. This is the check that was missing when the
    allowlist first landed and quietly dropped three SchoolDetailPage tiles.
    """

    @staticmethod
    def _seed_attribute_keys() -> set[str]:
        source = (Path(__file__).resolve().parents[1] / "scripts" / "seed_data.py").read_text()
        keys: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.keyword) and node.arg == "attributes":
                if isinstance(node.value, ast.Dict):
                    keys.update(
                        key.value for key in node.value.keys if isinstance(key, ast.Constant)
                    )
        return keys

    def test_every_seeded_attribute_key_is_served(self):
        seed_keys = self._seed_attribute_keys()
        assert seed_keys, "failed to parse seed_data.py — did the fixture shape change?"

        served = set(SchoolDisplayAttributes.model_fields) | set(
            SchoolLocalizedAttributes.model_fields
        )
        assert seed_keys <= served, f"allowlist drops seeded fields: {sorted(seed_keys - served)}"

    def test_projection_output_matches_the_schemas(self):
        base, localized = build_display_attributes({})
        assert set(base) == set(SchoolDisplayAttributes.model_fields)
        for values in localized.values():
            assert set(values) == set(SchoolLocalizedAttributes.model_fields)


class TestSerializationAllowlist:
    def _leaked(self, payload: str) -> list[str]:
        return [marker for marker in INTERNAL_KEY_MARKERS if f'"{marker}' in payload]

    async def _school_with_attributes(self, db, attributes: dict) -> School:
        school = (await db.execute(select(School))).scalars().first()
        school.attributes = attributes
        await db.commit()
        return (
            await db.execute(
                select(School)
                .where(School.id == school.id)
                .options(
                    selectinload(School.locations).selectinload(SchoolLocation.age_group_shifts),
                    selectinload(School.pricing),
                    selectinload(School.exam_results),
                    selectinload(School.field_sources),
                )
            )
        ).scalar_one()

    @pytest.mark.asyncio
    async def test_list_and_detail_responses_ship_no_internal_keys(self, seeded_db):
        school = await self._school_with_attributes(seeded_db, INTERNAL_ATTRIBUTES)

        for model in (SchoolListResponse, SchoolResponse):
            payload = model.model_validate(school).model_dump_json()
            assert self._leaked(payload) == [], f"{model.__name__} leaked internal keys"

    @pytest.mark.asyncio
    async def test_api_serves_merged_attributes(self, seeded_db, seeded_client):
        school = (await seeded_db.execute(select(School))).scalars().first()
        school.attributes = INTERNAL_ATTRIBUTES
        await seeded_db.commit()

        response = await seeded_client.get(f"/schools/{school.id}")
        assert response.status_code == 200
        data = response.json()

        assert self._leaked(response.text) == []
        assert data["attributes"]["class_size"] == 16
        assert data["attributes_i18n"]["bg"]["facilities"] == ["Библиотека"]
        assert data["attributes_i18n"]["en"]["facilities"] == ["Library"]
        # display_name_i18n stays internal but still drives the resolved name.
        assert data["resolved_name_i18n"]["en"] == "Example School"

    @pytest.mark.asyncio
    async def test_unknown_attribute_keys_cannot_be_added_by_accident(self, seeded_db):
        school = await self._school_with_attributes(seeded_db, {"some_future_internal_key": "secret"})

        payload = SchoolListResponse.model_validate(school).model_dump()
        assert "some_future_internal_key" not in payload["attributes"]
