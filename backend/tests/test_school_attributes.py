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
    SchoolLocationResponse,
    SchoolLocalizedAttributes,
    SchoolResponse,
)
from app.services.school_service import SchoolService
from app.config import get_settings
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
    "display_name_evidence": {
        "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
        "status": "corroborated",
    },
    "extracted": {
        "_schema_version": 1,
        "languages": [{"language": "Английски", "level": "intensive"}],
        "facilities": ["Библиотека"],
        "programs": ["Спортна програма"],
        "accreditations": ["ISO 9001"],
        "extracurricular": ["Шахмат"],
        "class_size": "до 16 ученици в клас",
        "founded_year": 1998,
        "admission": {
            "deadlines": ["30 юни"],
            "entrance_requirements": ["Входящ тест"],
            "available_spots": ["12 свободни места"],
        },
        "operations": {
            "working_hours": "8:00-18:00",
            "daily_schedule": ["Учебни занятия до 15:00"],
            "meals": ["Обяд"],
        },
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
    "display_name_evidence",
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


@pytest.fixture
def website_admission_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "publish_website_admission_fields", True)


@pytest.fixture
def website_dynamic_fields_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "publish_website_dynamic_fields", True)


class TestDynamicWebsiteLaunchScope:
    SCRAPED_DYNAMIC_FIELDS = {
        "extracted": {
            "class_size": "16 students per class",
            "founded_year": 1998,
            "operations": {
                "working_hours": "08:00-18:00",
                "daily_schedule": ["Classes until 15:00"],
            },
        }
    }

    def test_dynamic_website_fields_are_withheld_by_default(self):
        assert get_settings().publish_website_dynamic_fields is False

        base = build_base_attributes(self.SCRAPED_DYNAMIC_FIELDS)
        localized = build_localized_attributes(self.SCRAPED_DYNAMIC_FIELDS, "en")

        assert base["class_size"] is None
        assert base["school_hours"] is None
        assert base["established_year"] is None
        assert localized["daily_schedule"] == []

    def test_curated_top_level_dynamic_fields_remain_publishable(self):
        base = build_base_attributes(
            {
                **self.SCRAPED_DYNAMIC_FIELDS,
                "class_size": 18,
                "school_hours": "07:30-17:30",
                "established_year": 1975,
            }
        )

        assert base["class_size"] == 18
        assert base["school_hours"] == "07:30-17:30"
        assert base["established_year"] == 1975

    def test_flag_restores_dynamic_website_fields(self, website_dynamic_fields_enabled):
        base = build_base_attributes(self.SCRAPED_DYNAMIC_FIELDS)
        localized = build_localized_attributes(self.SCRAPED_DYNAMIC_FIELDS, "en")

        assert base["class_size"] == 16
        assert base["school_hours"] == "08:00-18:00"
        assert base["established_year"] == 1998
        assert localized["daily_schedule"] == ["Classes until 15:00"]

    def test_validator_rejected_founded_year_stays_withheld(
        self, website_dynamic_fields_enabled
    ):
        attributes = {
            **self.SCRAPED_DYNAMIC_FIELDS,
            "data_validation": {
                "status": "needs_review",
                "spot_check": {
                    "discrepancies": [
                        {
                            "field_path": "attributes.extracted.founded_year",
                            "kind": "contradiction",
                        }
                    ]
                },
            },
        }

        assert build_base_attributes(attributes)["established_year"] is None


@pytest.mark.usefixtures("website_dynamic_fields_enabled")
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

    @pytest.mark.parametrize("value", ["5 students", "6 students", "7 students", 5, 3])
    def test_rejects_implausibly_small_sizes(self, value):
        # "5 students" and friends are almost always a teacher:student ratio the extractor
        # mislabelled as class size (P1.10, school 510) — reject below the plausible floor.
        assert build_base_attributes({"extracted": {"class_size": value}})["class_size"] is None

    @pytest.mark.parametrize("value,expected", [("8 students", 8), ("20 students", 20), (24, 24)])
    def test_keeps_plausible_sizes(self, value, expected):
        assert build_base_attributes({"extracted": {"class_size": value}})["class_size"] == expected

    def test_rejects_size_above_ceiling(self):
        # A number this large is total enrolment, not a class size.
        assert build_base_attributes({"extracted": {"class_size": "120 students"}})["class_size"] is None


@pytest.mark.usefixtures("website_dynamic_fields_enabled")
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

    def test_projects_scraped_admission_and_schedule_fields(self, website_admission_enabled):
        localized = build_localized_attributes(INTERNAL_ATTRIBUTES, "bg")
        assert localized["entry_requirements"] == ["Входящ тест"]
        assert localized["application_deadlines"] == ["30 юни"]
        assert localized["available_spots"] == ["12 свободни места"]
        assert localized["daily_schedule"] == ["Учебни занятия до 15:00"]

    def test_admission_projection_fails_closed_for_stale_and_navigation_content(
        self, website_admission_enabled
    ):
        attributes = {
            "extracted": {
                "admission": {
                    "deadlines": [
                        "Краен срок за подаване на оферта-05.06.2020г. 16:00ч.",
                        "![admission](https://school.test/admission.png)",
                        "Admission for 2025/2026 is",
                        "Крайният срок е 30 юни 2099 г.",
                    ],
                    "entrance_requirements": [
                        "интервютата",
                        "Приемът включва писмен тест и интервю.",
                    ],
                    "available_spots": [
                        "* [Свободни места](https://school.test/admission)",
                        "Свободни места",
                        "Остават 12 свободни места за първи клас.",
                    ],
                }
            }
        }

        localized = build_localized_attributes(attributes, "bg")

        assert localized["application_deadlines"] == ["Крайният срок е 30 юни 2099 г."]
        assert localized["entry_requirements"] == ["Приемът включва писмен тест и интервю."]
        assert localized["available_spots"] == ["Остават 12 свободни места за първи клас."]

    def test_validation_blocks_scraped_admission_and_schedule_fields(
        self, website_admission_enabled
    ):
        attributes = {
            **INTERNAL_ATTRIBUTES,
            "data_validation": {
                "status": "needs_review",
                "spot_check": {
                    "discrepancies": [
                        {
                            "field_path": "attributes.extracted.admission.deadlines",
                            "kind": "unsupported",
                        },
                        {
                            "field_path": "attributes.extracted.operations.daily_schedule",
                            "kind": "contradiction",
                        },
                    ]
                },
            },
        }
        localized = build_localized_attributes(attributes, "bg")
        assert localized["application_deadlines"] == []
        assert localized["daily_schedule"] == []
        assert localized["entry_requirements"] == ["Входящ тест"]

    @pytest.mark.parametrize(
        "field_path,blocked_fields",
        [
            (
                "attributes.extracted.admission",
                {"entry_requirements", "application_deadlines", "available_spots"},
            ),
            (
                "attributes.extracted.operations",
                {"daily_schedule"},
            ),
        ],
    )
    def test_parent_section_discrepancy_blocks_all_projected_children(
        self, field_path, blocked_fields, website_admission_enabled
    ):
        attributes = {
            **INTERNAL_ATTRIBUTES,
            "data_validation": {
                "status": "needs_review",
                "spot_check": {
                    "discrepancies": [
                        {"field_path": field_path, "kind": "unsupported"}
                    ]
                },
            },
        }
        localized = build_localized_attributes(attributes, "bg")
        for field in blocked_fields:
            assert localized[field] == []

        base = build_base_attributes(attributes)
        if field_path.endswith("operations"):
            assert base["school_hours"] is None

    @pytest.mark.parametrize(
        "field_path",
        [
            "attributes.extracted.operations.transport",
            "attributes.extracted.operations.meals",
            "attributes.extracted.admission.required_documents",
            "attributes.extracted.admission.application_steps",
        ],
    )
    def test_unmapped_section_child_does_not_block_unrelated_display_fields(
        self, field_path, website_admission_enabled
    ):
        attributes = {
            **INTERNAL_ATTRIBUTES,
            "data_validation": {
                "status": "needs_review",
                "spot_check": {
                    "discrepancies": [
                        {"field_path": field_path, "kind": "unsupported"}
                    ]
                },
            },
        }
        localized = build_localized_attributes(attributes, "bg")
        assert localized["entry_requirements"] == ["Входящ тест"]
        assert localized["application_deadlines"] == ["30 юни"]
        assert localized["available_spots"] == ["12 свободни места"]
        assert localized["daily_schedule"] == ["Учебни занятия до 15:00"]
        assert build_base_attributes(attributes)["school_hours"] == "8:00-18:00"

    def test_website_admission_fields_are_withheld_by_default(self):
        localized = build_localized_attributes(INTERNAL_ATTRIBUTES, "bg")

        assert localized["entry_requirements"] == []
        assert localized["application_deadlines"] == []
        assert localized["available_spots"] == []

    def test_shared_projection_drops_markdown_and_urls_from_every_localized_field(self):
        localized = build_localized_attributes(
            {
                "extracted": {
                    "facilities": ["Library", "[Pool](https://school.test/pool)"],
                    "programs": ["https://school.test/stem"],
                    "languages": [
                        {"language": "English", "level": "intensive"},
                        {"language": "[German](https://school.test/de)", "level": None},
                    ],
                    "operations": {
                        "daily_schedule": [
                            "Учебни занятия до 15:00",
                            "08:00 - 19:00",
                            "Сесията включва три етапа - задачи, интервю и среща.",
                            "- Самостоятелен Markdown bullet",
                            "* [Дневен режим](https://school324.test/schedule)",
                        ]
                    },
                }
            },
            "bg",
        )

        assert localized["facilities"] == ["Library"]
        assert localized["special_programs"] == []
        assert localized["language_focus"] == [
            {"language": "English", "level": "intensive"}
        ]
        assert localized["daily_schedule"] == [
            "Учебни занятия до 15:00",
            "08:00 - 19:00",
            "Сесията включва три етапа - задачи, интервю и среща.",
        ]

    def test_location_projection_drops_school_367_markdown_address(self):
        location = SchoolLocationResponse.model_validate(
            {
                "id": 897,
                "school_id": 367,
                "age_groups": ["grade_1_4"],
                "address_i18n": {
                    "bg": (
                        'кв."Бенковски", ул."Наука" №2 '
                        "](https://60ousvsvkirilimetodii.com/index.php) Навигация"
                    ),
                    "en": "2 Nauka St, Benkovski",
                },
            }
        )

        payload = location.model_dump(mode="json")

        assert payload["address_i18n"] == {"en": "2 Nauka St, Benkovski"}
        assert payload["resolved_address_i18n"] == {
            "bg": "2 Nauka St, Benkovski",
            "en": "2 Nauka St, Benkovski",
        }
        assert "http" not in str(payload)
        assert "](" not in str(payload)

    def test_scraper_display_fields_are_declared_in_the_allowlist(self):
        projected_scraper_fields = {
            "entry_requirements",
            "application_deadlines",
            "available_spots",
            "daily_schedule",
        }
        assert projected_scraper_fields <= set(SchoolLocalizedAttributes.model_fields)

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

    def test_english_primary_school_backfills_bg_from_en(self):
        # English-primary schools stash every list under `extracted_i18n.en` with an empty
        # primary slot, so a BG viewer used to see nothing (P1.10). Backfill from en.
        attrs = {
            "extracted": {"facilities": [], "programs": [], "languages": []},
            "extracted_i18n": {
                "en": {
                    "facilities": ["Medical care", "Playground"],
                    "programs": ["IB Diploma"],
                    "languages": [{"language": "English", "level": "intensive"}],
                }
            },
        }
        bg = build_localized_attributes(attrs, "bg")
        assert bg["facilities"] == ["Medical care", "Playground"]
        assert bg["special_programs"] == ["IB Diploma"]
        assert [e["language"] for e in bg["language_focus"]] == ["English"]

    def test_backfill_does_not_override_populated_locale(self):
        # A locale that already has its own content is never overwritten by another's.
        attrs = {
            "extracted": {"facilities": ["Библиотека"]},
            "extracted_i18n": {
                "bg": {"facilities": ["Библиотека"]},
                "en": {"facilities": ["Library"]},
            },
        }
        assert build_localized_attributes(attrs, "bg")["facilities"] == ["Библиотека"]
        assert build_localized_attributes(attrs, "en")["facilities"] == ["Library"]


@pytest.mark.usefixtures("website_dynamic_fields_enabled")
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

    def test_scraped_working_hours_fill_school_hours(self):
        base = build_base_attributes(INTERNAL_ATTRIBUTES)
        assert base["school_hours"] == "8:00-18:00"

    @pytest.mark.parametrize(
        "value",
        [
            "### What are the working hours?",
            "18:00-7:00",
            "7.30 – 8.30",
            "10:00 - 12:00",
        ],
    )
    def test_implausible_scraped_working_hours_are_dropped(self, value):
        base = build_base_attributes({"extracted": {"operations": {"working_hours": value}}})
        assert base["school_hours"] is None

    def test_markdown_noise_is_removed_from_scraped_working_hours(self):
        base = build_base_attributes(
            {"extracted": {"operations": {"working_hours": "###### РАБОТНО ВРЕМЕ: 09:00 - 17:00ч."}}}
        )
        assert base["school_hours"] == "09:00 - 17:00"

    def test_seeded_school_hours_override_scraped_hours(self):
        base = build_base_attributes(
            {**INTERNAL_ATTRIBUTES, "school_hours": "7:30-17:30"}
        )
        assert base["school_hours"] == "7:30-17:30"

    def test_validation_blocks_scraped_working_hours(self):
        attributes = {
            **INTERNAL_ATTRIBUTES,
            "data_validation": {
                "status": "needs_review",
                "spot_check": {
                    "discrepancies": [
                        {
                            "field_path": "attributes.extracted.operations.working_hours",
                            "kind": "unsupported",
                        }
                    ]
                },
            },
        }
        assert build_base_attributes(attributes)["school_hours"] is None

    @pytest.mark.parametrize("value", ["", "  ", None])
    def test_blank_ratio_is_dropped(self, value):
        assert build_base_attributes({"teacher_student_ratio": value})["teacher_student_ratio"] is None

    @pytest.mark.parametrize("value", ["not a year", 75, 12345, True, None, 3000])
    def test_implausible_established_year_is_dropped(self, value):
        assert build_base_attributes({"established_year": value})["established_year"] is None

    def test_numeric_string_year_is_coerced(self):
        assert build_base_attributes({"established_year": "1975"})["established_year"] == 1975

    def test_flag_restores_scraped_founded_year(self):
        base = build_base_attributes({"extracted": {"founded_year": 1998}})
        assert base["established_year"] == 1998

    def test_filter_tags_are_served_and_canonical(self):
        # The frontend counts advanced-filter checkboxes against these locale-independent
        # canonical tags (P1.9); free text in either locale collapses onto them.
        base = build_base_attributes(INTERNAL_ATTRIBUTES)
        assert base["filter_tags"] == {
            "facilities": ["library"],
            # "Обяд" (lunch) under operations.meals maps to meals_provided (P1.9 nested).
            "special_programs": ["meals_provided", "sports_program"],
            "teaching_approach": [],
        }

    def test_filter_tags_include_nested_extraction_sources(self):
        # Scraped rows store transport/meals under `operations` and pedagogy under
        # `summary_source`, outside the projected lists. These still feed the filter tags.
        base = build_base_attributes(
            {
                "extracted": {
                    "operations": {
                        "transport": ["Buses: 213", "автобус №150"],
                        "meals": ["Столово хранене", "Cafeteria Meals (optional)"],
                    },
                    "summary_source": {"teaching_approach": ["проектно-базирано обучение"]},
                }
            }
        )
        assert base["filter_tags"]["facilities"] == ["transportation"]
        assert base["filter_tags"]["special_programs"] == ["meals_provided"]
        assert base["filter_tags"]["teaching_approach"] == ["project_based"]

    def test_approach_tags_from_summary_source_prose(self):
        # The extractor often records an approach only as summary_source canonical_tags
        # or positioning/differentiators prose, with teaching_approach left empty.
        base = build_base_attributes(
            {
                "extracted": {
                    "summary_source": {
                        "teaching_approach": [],
                        "canonical_tags": ["IB", "Cambridge"],
                        "positioning": "IB programme by International Baccalaureate",
                        "differentiators": ["Montessori-inspired classrooms"],
                    }
                }
            }
        )
        assert base["filter_tags"]["teaching_approach"] == ["ib_program", "montessori"]

    def test_nested_sources_respect_validation_gating(self):
        # A validation error on facilities/programs blocks the nested transport/meals too.
        attrs = {
            "extracted": {
                "operations": {"transport": ["Buses: 213"], "meals": ["Столово хранене"]},
            },
            "data_validation": {
                "status": "ok",
                "issues": [],
                "spot_check": {
                    "discrepancies": [
                        {"field_path": "attributes.extracted.facilities", "kind": "contradiction"},
                        {"field_path": "attributes.extracted.programs", "kind": "contradiction"},
                    ]
                },
            },
        }
        tags = build_base_attributes(attrs)["filter_tags"]
        assert tags["facilities"] == []
        assert tags["special_programs"] == []


class TestFilterableProjection:
    def test_unions_locales_and_maps_to_canonical_vocabulary(self):
        # Free-text facilities/programs in either locale collapse onto the controlled
        # vocabulary the advanced filter offers (P1.9): "Библиотека"/"Library" → library,
        # "Спортна програма"/"Sports program" → sports_program.
        filterable = build_filterable_attributes(INTERNAL_ATTRIBUTES)
        assert filterable["facilities"] == ["library"]
        assert filterable["special_programs"] == ["meals_provided", "sports_program"]
        assert {entry["language"] for entry in filterable["language_focus"]} == {
            "Английски",
            "English",
        }

    def test_preserves_canonical_top_level_values(self):
        filterable = build_filterable_attributes({"facilities": ["cafeteria"], "teaching_approach": ["montessori"]})
        assert filterable["facilities"] == ["cafeteria"]
        assert filterable["teaching_approach"] == ["montessori"]

    @pytest.mark.asyncio
    async def test_language_filter_options_match_filter_normalization(self, seeded_db):
        school = (await seeded_db.execute(select(School))).scalars().first()
        school.attributes = {
            "language_focus": [
                {"language": "English", "level": "Mother tongue"},
                "German:Early Foreign",
            ]
        }
        await seeded_db.commit()

        service = SchoolService(seeded_db)

        filters = await service.get_available_filters()
        assert "English:mother_tongue" in filters["language_focus_pairs"]
        assert "German:early_foreign" in filters["language_focus_pairs"]
        assert "Mother tongue" not in filters["language_focus_levels"]
        assert "German:Early Foreign" not in filters["language_focus_pairs"]

        for language_focus in ("English:mother_tongue", "German:early_foreign"):
            schools = await service.list_schools_filtered(language_focus=[language_focus])
            assert school.id in {matched.id for matched in schools}

    @pytest.mark.asyncio
    async def test_free_text_facilities_surface_as_canonical_options(self, seeded_db):
        """A scraped school's free text becomes canonical filter options that match.

        This is the P1.9 regression: options and the matcher share the projection, so
        a free-text "библиотека" both shows up as the `library` option and matches the
        `facilities=library` filter — the two used to never intersect.
        """
        school = (await seeded_db.execute(select(School))).scalars().first()
        school.attributes = {
            "extracted": {
                "facilities": ["библиотека", "физкултурен салон"],
                "programs": ["Футбол", "Montessori"],
            }
        }
        school.scrape_status = "extracted"
        await seeded_db.commit()

        service = SchoolService(seeded_db)

        filters = await service.get_available_filters()
        assert "library" in filters["facilities"]
        assert "sports_facilities" in filters["facilities"]
        assert "sports_program" in filters["special_programs"]
        assert "montessori" in filters["teaching_approach"]
        # Only canonical vocabulary is ever emitted — never the raw free text.
        assert "библиотека" not in filters["facilities"]
        assert all(":" not in option for option in filters["facilities"])

        matched = await service.list_schools_filtered(facilities=["library"])
        assert school.id in {s.id for s in matched}
        matched = await service.list_schools_filtered(special_programs=["sports_program"])
        assert school.id in {s.id for s in matched}


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
        school.scrape_status = "extracted"
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
        school.scrape_status = "extracted"
        await seeded_db.commit()

        response = await seeded_client.get(f"/schools/{school.id}")
        assert response.status_code == 200
        data = response.json()

        assert self._leaked(response.text) == []
        assert data["attributes"]["class_size"] is None
        assert data["attributes"]["school_hours"] is None
        assert data["attributes"]["established_year"] is None
        assert data["attributes_i18n"]["bg"]["daily_schedule"] == []
        assert data["attributes_i18n"]["bg"]["facilities"] == ["Библиотека"]
        assert data["attributes_i18n"]["en"]["facilities"] == ["Library"]
        # Canonical advanced-filter tags ship alongside the free text (P1.9), mapped
        # from it: "Библиотека"/"Library" → library, "Спортна програма" → sports_program.
        # These are what the frontend counts checkboxes against.
        assert data["attributes"]["filter_tags"] == {
            "facilities": ["library"],
            "special_programs": ["meals_provided", "sports_program"],
            "teaching_approach": [],
        }
        # display_name_i18n stays internal but still drives the resolved name.
        assert data["resolved_name_i18n"]["en"] == "Example School"

    @pytest.mark.asyncio
    async def test_unknown_attribute_keys_cannot_be_added_by_accident(self, seeded_db):
        school = await self._school_with_attributes(seeded_db, {"some_future_internal_key": "secret"})

        payload = SchoolListResponse.model_validate(school).model_dump()
        assert "some_future_internal_key" not in payload["attributes"]
