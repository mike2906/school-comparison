"""Tests for Stage 7 summarization."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai import summariser as ai_summariser
from app.models import Pricing, School, SchoolLocation
from app.models.pricing import PriceSource
from app.schemas.llm_outputs import SchoolSummaryStrict
from app.scrapers import summarizer as summarizer_module
from tasks import scrape_tasks


def _validation_payload(status: str = "ok", issues: list[dict] | None = None, spot_check: dict | None = None) -> dict:
    return {
        "_schema_version": 1,
        "validated_at": "2026-03-11T00:00:00+00:00",
        "status": status,
        "issue_counts": {"error": 0, "warning": 0},
        "issues": issues or [],
        "auto_fixes": [],
        "spot_check": spot_check,
    }


@pytest.mark.asyncio
async def test_prepare_summary_candidate_rejects_needs_review(db_session):
    # P1.7: a whole-school summary is only published for a clean (`ok`) report; a
    # `needs_review` report still carries error-level issues.
    school = School(
        name_i18n={"bg": "Сумиране Тест", "en": "Summary Test"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        attributes={
            "extracted": {
                "languages": [{"language": "English", "level": None}],
                "programs": ["STEM"],
            },
            "data_validation": _validation_payload(
                status="needs_review",
                issues=[
                    {
                        "code": "negative_price_amount",
                        "severity": "error",
                        "field_path": "pricing[99].amount",
                        "message": "bad",
                    }
                ],
            ),
        },
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)

    assert prepared.summary_input is None
    assert prepared.validation_status == "needs_review"


@pytest.mark.asyncio
async def test_prepare_summary_candidate_drops_pricing_flagged_by_spot_check(db_session):
    # An `ok` report can still carry an actionable spot-check discrepancy; the
    # affected section is dropped while other sections still summarise.
    school = School(
        name_i18n={"bg": "Сумиране Тест", "en": "Summary Test"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        attributes={
            "extracted": {
                "languages": [{"language": "English", "level": None}],
                "programs": ["STEM"],
                "pricing_terms": {"discounts": ["Sibling discount"]},
            },
            "data_validation": _validation_payload(
                status="ok",
                spot_check={
                    "has_discrepancy": True,
                    "discrepancies": [
                        {
                            "field_path": "attributes.extracted.pricing_terms",
                            "kind": "contradiction",
                            "issue": "price mismatch",
                        }
                    ],
                },
            ),
        },
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)

    assert prepared.summary_input is not None
    assert prepared.validation_status == "ok"
    assert prepared.summary_input.offering is not None
    assert prepared.summary_input.pricing is None


@pytest.mark.asyncio
async def test_prepare_summary_candidate_ignores_low_quality_display_name(db_session):
    school = School(
        name_i18n={"bg": '145 Основно училище "Симеон Радев"'},
        country_code="bg",
        school_type="state",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "display_name_i18n": {"bg": "Тримесечен отчет на 145. ОУ Симеон Радев за м.Декември"},
            "extracted": {"programs": ["STEM"]},
            "data_validation": _validation_payload(),
        },
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)

    assert prepared.summary_input is not None
    assert prepared.summary_input.identity.display_name_i18n == {}
    assert prepared.summary_input.identity.name_i18n["bg"] == '145 Основно училище "Симеон Радев"'


@pytest.mark.asyncio
async def test_prepare_summary_candidate_only_exposes_corroborated_display_name(db_session):
    school = School(
        name_i18n={"bg": 'Частно училище "Истинско име"'},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "display_name_i18n": {"bg": "Прием след 7. клас", "en": "Admissions News"},
            "extracted": {"programs": ["STEM"]},
            "data_validation": _validation_payload(),
        },
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)

    assert prepared.summary_input is not None
    assert prepared.summary_input.identity.display_name_i18n == {}
    assert prepared.summary_input.identity.name_i18n["bg"] == 'Частно училище "Истинско име"'


@pytest.mark.asyncio
async def test_prepare_summary_candidate_derives_locality_from_primary_location(db_session):
    school = School(
        name_i18n={"bg": "Космос"},
        country_code="bg",
        school_type="private",
        education_level="upper_secondary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "extracted": {"programs": ["IB"]},
            "data_validation": _validation_payload(),
        },
    )
    school.locations.append(
        SchoolLocation(
            address_i18n={"bg": "с. Осоица 2121"},
            is_primary=True,
        )
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)

    assert prepared.summary_input is not None
    assert prepared.summary_input.identity.city is None
    assert prepared.summary_input.identity.locality_i18n == {"bg": "Осоица", "en": "Osoitsa"}
    fallback = ai_summariser._build_fallback_summary(prepared.summary_input)
    assert "в Осоица" in fallback["bg"]["short"]
    assert "in Osoitsa" in fallback["en"]["short"]


@pytest.mark.asyncio
async def test_prepare_summary_candidate_ignores_generic_numbered_display_name(db_session):
    school = School(
        name_i18n={"bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"'},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "display_name_i18n": {"bg": "21. СУ"},
            "extracted": {"programs": ["STEM"]},
            "data_validation": _validation_payload(),
        },
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)

    assert prepared.summary_input is not None
    assert prepared.summary_input.identity.display_name_i18n == {}
    assert prepared.summary_input.identity.name_i18n == {
        "bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"',
        "en": '21 Secondary School "Hristo Botev"',
    }


@pytest.mark.asyncio
async def test_prepare_summary_candidate_drops_display_name_if_any_locale_is_low_quality(db_session):
    school = School(
        name_i18n={"bg": "Професионална гимназия по телекомуникации"},
        country_code="bg",
        school_type="state",
        education_level="upper_secondary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "display_name_i18n": {"bg": "на Професионална гимназия по телекомуникации", "en": "Cisco academy"},
            "extracted": {"programs": ["Networks"]},
            "data_validation": _validation_payload(),
        },
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)

    assert prepared.summary_input is not None
    assert prepared.summary_input.identity.display_name_i18n == {}
    assert prepared.summary_input.identity.name_i18n["bg"] == "Професионална гимназия по телекомуникации"


@pytest.mark.asyncio
async def test_prepare_summary_candidate_includes_summary_source_narrative(db_session):
    school = School(
        name_i18n={"bg": "Фюжън"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "extracted": {
                "programs": ["STEM"],
                "summary_source": {
                    "positioning": "Licensed private school for preschool through grade 7.",
                    "teaching_approach": ["project-based learning", "emotional intelligence"],
                    "student_experience": ["creative and movement-based activities"],
                    "community_signals": ["partnership with parents"],
                    "differentiators": ["author-developed educational model"],
                    "canonical_tags": ["Project-based learning", "Fusion educational model"],
                    "has_useful_info": True,
                },
            },
            "data_validation": _validation_payload(),
        },
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)

    assert prepared.summary_input is not None
    assert prepared.summary_input.offering is not None
    assert prepared.summary_input.offering.positioning == "Licensed private school for preschool through grade 7."
    assert prepared.summary_input.offering.teaching_approach == [
        "project-based learning",
        "emotional intelligence",
    ]
    assert prepared.summary_input.offering.canonical_tags == [
        "Project-based learning",
        "Fusion educational model",
    ]


@pytest.mark.asyncio
async def test_prepare_summary_candidate_derives_name_based_canonical_tags(db_session):
    school = School(
        name_i18n={"bg": '"Частна немска гимназия Ерих Кестнер" ООД'},
        country_code="bg",
        school_type="private",
        education_level="upper_secondary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "display_name_i18n": {"bg": "Ерих Кестнер", "en": "Erih Kestner"},
            "extracted": {
                "programs": [],
                "summary_source": {
                    "has_useful_info": False,
                },
            },
            "data_validation": _validation_payload(),
        },
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)

    assert prepared.summary_input is not None
    assert prepared.summary_input.offering is not None
    assert "German-focused" in prepared.summary_input.offering.canonical_tags


@pytest.mark.asyncio
async def test_summarize_school_persists_summary_and_status(db_session):
    school = School(
        name_i18n={"bg": "Частно училище Тест", "en": "Private Test School"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "extracted": {
                "languages": [{"language": "English", "level": None}],
                "programs": ["STEM"],
                "facilities": ["pool"],
            },
            "data_validation": _validation_payload(),
        },
    )
    db_session.add(school)
    await db_session.commit()

    fake_result = {
        "summary_i18n": {
            "bg": {"short": "Кратко описание.", "long": "По-дълго описание за училището."},
            "en": {"short": "Short description.", "long": "Longer description for the school."},
        },
        "input_tokens": 100,
        "output_tokens": 25,
        "token_cost_usd": 0.001,
        "model_tier": "cheap",
        "model": "openrouter/google/gemini-2.5-flash-lite",
    }

    with patch("app.scrapers.summarizer.generate_school_summary", new=AsyncMock(return_value=fake_result)):
        result = await summarizer_module.summarize_school(db_session, school.id, "bg")

    assert result["status"] == "summarized"
    await db_session.refresh(school)
    assert school.scrape_status == "summarized"
    assert school.summary_i18n["bg"]["short"] == "Кратко описание."
    metadata = (school.attributes or {}).get("summary_generation", {})
    assert metadata.get("_schema_version") == summarizer_module.SUMMARY_GENERATION_SCHEMA_VERSION
    assert metadata.get("input_fingerprint")


@pytest.mark.asyncio
async def test_summarize_school_skips_when_summary_is_current(db_session):
    school = School(
        name_i18n={"bg": "Актуално училище", "en": "Current School"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        attributes={
            "extracted": {
                "languages": [{"language": "English", "level": None}],
                "programs": ["STEM"],
            },
            "data_validation": _validation_payload(),
        },
    )
    db_session.add(school)
    await db_session.commit()

    school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == school.id)
        )
    ).scalar_one()
    prepared = summarizer_module.prepare_summary_candidate(school)
    school.summary_i18n = {
        "bg": {"short": "Текущо.", "long": "Текущо описание."},
        "en": {"short": "Current.", "long": "Current description."},
    }
    school.attributes = {
        **(school.attributes or {}),
        "summary_generation": {
            "_schema_version": summarizer_module.SUMMARY_GENERATION_SCHEMA_VERSION,
            "input_fingerprint": prepared.fingerprint,
        },
    }
    db_session.add(school)
    await db_session.commit()

    with patch("app.scrapers.summarizer.generate_school_summary", new=AsyncMock()) as summarize_mock:
        result = await summarizer_module.summarize_school(db_session, school.id, "bg")

    assert result["status"] == "skipped"
    summarize_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_schools_requiring_summary_includes_stale_summarized_and_extracted(db_session):
    summarized_stale = School(
        name_i18n={"bg": "Старо резюме"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        summary_i18n={"bg": {"short": "old", "long": "old"}, "en": {"short": "old", "long": "old"}},
        attributes={
            "extracted": {"programs": ["STEM"]},
            "data_validation": _validation_payload(),
            "summary_generation": {"_schema_version": 1, "input_fingerprint": "stale"},
        },
    )
    summarized_fresh = School(
        name_i18n={"bg": "Свежо резюме"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        attributes={
            "extracted": {"programs": ["Arts"]},
            "data_validation": _validation_payload(),
        },
    )
    extracted_stale = School(
        name_i18n={"bg": "Ново училище"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "extracted": {"facilities": ["lab"]},
            "data_validation": _validation_payload(),
        },
    )
    db_session.add_all([summarized_stale, summarized_fresh, extracted_stale])
    await db_session.commit()

    summarized_fresh = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == summarized_fresh.id)
        )
    ).scalar_one()
    fresh_prepared = summarizer_module.prepare_summary_candidate(summarized_fresh)
    summarized_fresh.summary_i18n = {
        "bg": {"short": "fresh", "long": "fresh"},
        "en": {"short": "fresh", "long": "fresh"},
    }
    summarized_fresh.attributes = {
        **(summarized_fresh.attributes or {}),
        "summary_generation": {
            "_schema_version": summarizer_module.SUMMARY_GENERATION_SCHEMA_VERSION,
            "input_fingerprint": fresh_prepared.fingerprint,
        },
    }
    db_session.add(summarized_fresh)
    await db_session.commit()

    schools = await summarizer_module.get_schools_requiring_summary(db_session, country_code="bg", city="sofia")
    school_ids = {school.id for school in schools}

    assert summarized_stale.id in school_ids
    assert extracted_stale.id in school_ids
    assert summarized_fresh.id not in school_ids


@pytest.mark.asyncio
async def test_get_schools_requiring_summary_applies_limit_after_eligibility_filter(db_session):
    ineligible = School(
        name_i18n={"bg": "Без валидация"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={"extracted": {"programs": ["STEM"]}},
    )
    eligible = School(
        name_i18n={"bg": "С валидно Stage 6"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "extracted": {"programs": ["Arts"]},
            "data_validation": _validation_payload(),
        },
    )
    db_session.add_all([ineligible, eligible])
    await db_session.commit()

    schools = await summarizer_module.get_schools_requiring_summary(
        db_session,
        country_code="bg",
        city="sofia",
        limit=1,
    )

    assert len(schools) == 1
    assert schools[0].id == eligible.id


@pytest.mark.asyncio
async def test_task_selectors_include_summarized_school_states(db_session):
    summarized_for_validation = School(
        name_i18n={"bg": "Сумирано училище"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        attributes={
            "extracted": {"programs": ["STEM"]},
        },
    )
    summarized_for_summarization = School(
        name_i18n={"bg": "Сумирано за Stage 7"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        attributes={
            "extracted": {"programs": ["STEM"]},
            "data_validation": _validation_payload(),
        },
    )
    fresh_summary_school = School(
        name_i18n={"bg": "Свежо сумирано"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        summary_i18n={"bg": {"short": "fresh", "long": "fresh"}, "en": {"short": "fresh", "long": "fresh"}},
        attributes={
            "extracted": {"programs": ["Arts"]},
            "data_validation": _validation_payload(),
        },
    )
    db_session.add_all([summarized_for_validation, summarized_for_summarization, fresh_summary_school])
    await db_session.commit()

    summarized_for_summarization = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == summarized_for_summarization.id)
        )
    ).scalar_one()
    stale_prepared = summarizer_module.prepare_summary_candidate(summarized_for_summarization)
    summarized_for_summarization.attributes = {
        **(summarized_for_summarization.attributes or {}),
        "summary_generation": {
            "_schema_version": 1,
            "input_fingerprint": f"{stale_prepared.fingerprint}-stale",
        },
    }
    db_session.add(summarized_for_summarization)

    fresh_summary_school = (
        await db_session.execute(
            select(School).options(
                selectinload(School.locations),
                selectinload(School.pricing),
                selectinload(School.exam_results),
            ).where(School.id == fresh_summary_school.id)
        )
    ).scalar_one()
    fresh_prepared = summarizer_module.prepare_summary_candidate(fresh_summary_school)
    fresh_summary_school.attributes = {
        **(fresh_summary_school.attributes or {}),
        "summary_generation": {
            "_schema_version": summarizer_module.SUMMARY_GENERATION_SCHEMA_VERSION,
            "input_fingerprint": fresh_prepared.fingerprint,
        },
    }
    db_session.add(fresh_summary_school)
    await db_session.commit()

    class SessionCtx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    with patch("app.database.async_session_maker", return_value=SessionCtx()):
        validation_ids = await scrape_tasks._get_schools_for_validation("bg", "sofia", None, False)
        summarization_ids = await scrape_tasks._get_schools_for_summarization("bg", "sofia", None)

    assert summarized_for_validation.id in validation_ids
    assert summarized_for_summarization.id in summarization_ids
    assert fresh_summary_school.id not in summarization_ids


def test_fallback_summary_prefers_raw_bg_and_clean_en_name():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "АМЕРИКАНСКИ КОЛЕЖ В СОФИЯ"},
            display_name_i18n={"bg": "American College of Sofia", "en": "American College of Sofia"},
            school_type="private",
            education_level="upper_secondary",
            city="sofia",
        )
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == "АМЕРИКАНСКИ КОЛЕЖ В СОФИЯ е частна гимназия в София."
    assert result["en"]["short"] == "American College of Sofia is a private high school in Sofia."


def test_fallback_summary_uses_summary_source_narrative_in_long_text():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Фюжън", "en": "Fusion"},
            school_type="private",
            education_level="primary",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            positioning="Licensed private school for preschool through grade 7.",
            teaching_approach=["project-based learning", "emotional intelligence"],
            differentiators=["author-developed educational model"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert "Licensed private school for preschool through grade 7." in result["en"]["long"]
    assert "project-based learning" in result["en"]["long"]


def test_fallback_summary_drops_extracted_fragments_that_fail_validation():
    # School 577: "водеща детска дейност" (a pedagogy term) trips the promotional-word block.
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "НЕМО - Бояна", "en": "NEMO - Boyana"},
            school_type="private",
            education_level="kindergarten",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            teaching_approach=["като водеща детска дейност и като обучителен метод"],
        ),
        operations=ai_summariser.SummaryOperations(support_services=["психолог", "Логопед"]),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert ai_summariser.validate_summary_i18n(result) == result
    assert "водеща" not in result["bg"]["long"]
    assert "психолог" in result["bg"]["long"]


def test_fallback_summary_short_skips_focus_values_that_fail_validation():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Детска градина Слънце", "en": "Sun Kindergarten"},
            school_type="private",
            education_level="kindergarten",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            teaching_approach=["водещ метод на обучение", "игрово обучение"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert ai_summariser.validate_summary_i18n(result) == result
    assert "с акцент върху игрово обучение" in result["bg"]["short"]


def test_fallback_summary_derives_shorter_en_name_and_article():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": 'Френско училище "Виктор Юго" в София'},
            display_name_i18n={},
            school_type="international",
            education_level="primary",
            city="sofia",
        )
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == 'Френско училище "Виктор Юго" в София е международно училище в София.'
    assert result["en"]["short"] == "Viktor Yugo is an international school in Sofia."


def test_fallback_summary_uses_acronym_for_generic_bg_name():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": '"Частно средно училище с ранно чуждоезиково обучение ЕСПА" ЕООД'},
            display_name_i18n={"bg": "с ранно чуждоезиково обучение ЕСПА"},
            school_type="private",
            education_level="upper_secondary",
            city="sofia",
        )
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["en"]["short"] == "ESPA is a private high school in Sofia with early foreign language education."


def test_fallback_summary_prefers_resolved_en_name_over_short_bg_acronym():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": 'Основно училище "Д-р Петър Берон"', "en": "Dr. Petar Beron"},
            display_name_i18n={"bg": 'ОУ "Д-р Петър Берон'},
            school_type="state",
            education_level="primary",
            city="sofia",
        )
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["en"]["short"] == "Dr. Petar Beron is a state school in Sofia."


def test_fallback_summary_does_not_use_numeric_prefix_as_en_name():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": '124 ОСНОВНО УЧИЛИЩЕ "ВАСИЛ ЛЕВСКИ"', "en": '124 OU "Vasil Levski'},
            display_name_i18n={"bg": '124 ОУ "Васил Левски'},
            school_type="state",
            education_level="primary",
            city="sofia",
        )
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["en"]["short"] == "Vasil Levski is a state school in Sofia."


def test_fallback_summary_short_uses_montessori_differentiator():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Берон", "en": "Beron"},
            school_type="private",
            education_level="primary",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            programs=["Montessori"],
            teaching_approach=["individual approach"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == "Берон е частно училище в София с Монтесори подход."
    assert result["en"]["short"] == "Beron is a private school in Sofia with a Montessori approach."


def test_fallback_summary_short_uses_language_focus_when_no_named_program():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Фюжън", "en": "Fusion School"},
            school_type="private",
            education_level="primary",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            languages=["German (intensive)", "English"],
            student_experience=["creative activities"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == "Фюжън е частно училище в София с фокус върху немски и английски език."
    assert result["en"]["short"] == "Fusion School is a private school in Sofia with German and English language focus."


def test_fallback_summary_short_translates_bulgarian_language_labels_for_english():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Свети Наум", "en": "Sveti Naum"},
            school_type="private",
            education_level="upper_secondary",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            languages=["английски", "немски"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["en"]["short"] == "Sveti Naum is a private high school in Sofia with English and German language focus."


def test_fallback_summary_short_uses_canonical_tag_when_languages_are_sparse():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Веда", "en": "Veda"},
            school_type="private",
            education_level="primary",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            canonical_tags=["Early foreign language education", "German-focused"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == "Веда е частно училище в София с ранно чуждоезиково обучение."
    assert result["en"]["short"] == "Veda is a private school in Sofia with early foreign language education."


def test_fallback_summary_short_uses_programming_and_robotics_pattern():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Стив Джобс", "en": "Steve Jobs School"},
            school_type="private",
            education_level="upper_secondary",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            programs=["Програмиране и изкуствен интелект", "Роботика"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == "Стив Джобс е частна гимназия в София с фокус върху програмиране и роботика."
    assert result["en"]["short"] == "Steve Jobs School is a private high school in Sofia with a focus on programming and robotics."


def test_fallback_summary_short_uses_german_focus_from_identity_name():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Частна немска гимназия Ерих Кестнер", "en": "Erich Kastner German School"},
            school_type="private",
            education_level="upper_secondary",
            city="sofia",
        )
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == "Частна немска гимназия Ерих Кестнер е частна гимназия в София с фокус върху немски език."
    assert result["en"]["short"] == "Erich Kastner German School is a private high school in Sofia with German language focus."


def test_fallback_summary_short_skips_license_style_differentiators():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Звездичка", "en": "Zvezdichka"},
            school_type="private",
            education_level="kindergarten",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            teaching_approach=["Учене чрез преживяване и игри"],
            differentiators=["Лиценз РД 14-163/14.10.2004 година"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == "Звездичка е частна детска градина в София с акцент върху Учене чрез преживяване и игри."
    assert result["en"]["short"] == "Zvezdichka is a private kindergarten in Sofia."


def test_fallback_summary_short_skips_generic_program_labels():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Звездичка", "en": "Zvezdichka"},
            school_type="private",
            education_level="kindergarten",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            programs=["образователни направления"],
            teaching_approach=["Личностен и индивидуален подход към всяко дете"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == "Звездичка е частна детска градина в София с акцент върху Личностен и индивидуален подход към всяко дете."
    assert result["en"]["short"] == "Zvezdichka is a private kindergarten in Sofia."


def test_choose_short_summary_keeps_fallback_when_it_has_specific_focus():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Фюжън", "en": "Fusion School"},
            school_type="private",
            education_level="primary",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            positioning="Fusion School implements the Fusion educational model.",
        ),
    )

    chosen = ai_summariser._choose_short_summary(
        "Fusion School offers an individualized learning environment.",
        "Fusion School is a private school in Sofia with the Fusion educational model.",
        summary_input,
        "en",
    )

    assert chosen == "Fusion School is a private school in Sofia with the Fusion educational model."


def test_choose_short_summary_uses_llm_when_fallback_is_generic():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "Звездичка", "en": "Zvezdichka"},
            school_type="private",
            education_level="kindergarten",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            teaching_approach=["Личностен и индивидуален подход към всяко дете"],
        ),
    )

    chosen = ai_summariser._choose_short_summary(
        "Zvezdichka Kindergarten offers an individual approach and educational programs.",
        "Zvezdichka is a private kindergarten in Sofia.",
        summary_input,
        "en",
    )

    assert chosen == "Zvezdichka Kindergarten offers an individual approach and educational programs."


def test_choose_short_summary_rejects_llm_text_without_real_signal():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "ЕСПА", "en": "ESPA"},
            school_type="private",
            education_level="upper_secondary",
            city="sofia",
        )
    )

    chosen = ai_summariser._choose_short_summary(
        "Private school s ranno chuzhdoezikovo obuchenie ESPA in Sofia.",
        "ESPA is a private high school in Sofia.",
        summary_input,
        "en",
    )

    assert chosen == "ESPA is a private high school in Sofia."


def test_fallback_summary_short_skips_mission_and_staff_phrases():
    summary_input = ai_summariser.SummaryInput(
        identity=ai_summariser.SummaryIdentity(
            name_i18n={"bg": "ЕСПА", "en": "ESPA"},
            school_type="private",
            education_level="upper_secondary",
            city="sofia",
        ),
        offering=ai_summariser.SummaryOffering(
            teaching_approach=["които преподават с мисия!", "Методически ръководител – Учебна работа"],
        ),
    )

    result = ai_summariser._build_fallback_summary(summary_input)

    assert result["bg"]["short"] == "ЕСПА е частна гимназия в София."


def test_clean_summary_fragment_strips_markdown_and_bullets():
    assert summarizer_module._clean_summary_fragment("**Психолог**") == "Психолог"
    assert summarizer_module._clean_summary_fragment("– Работа с логопед и психолог") == "Работа с логопед и психолог"


def test_clean_narrative_fragment_drops_admin_and_event_noise():
    assert summarizer_module._clean_narrative_fragment("Научете повече Приемам") == ""
    assert summarizer_module._clean_narrative_fragment("Европейската седмица на спорта 2025") == ""
    assert summarizer_module._clean_narrative_fragment("Проектно базирано обучение") == "Проектно базирано обучение"


def test_school_summary_strict_parses_stringified_summary_i18n():
    parsed = SchoolSummaryStrict.model_validate(
        {
            "summary_i18n": '{"bg":{"short":"Кратко.","long":"Дълго."},"en":{"short":"Short.","long":"Long."}}'
        }
    )

    assert parsed.summary_i18n.bg.short == "Кратко."
    assert parsed.summary_i18n.en.long == "Long."


def test_school_summary_strict_parses_stringified_summary_i18n_from_json():
    parsed = SchoolSummaryStrict.model_validate_json(
        '{"summary_i18n":"{\\"bg\\":{\\"short\\":\\"Кратко.\\",\\"long\\":\\"Дълго.\\"},\\"en\\":{\\"short\\":\\"Short.\\",\\"long\\":\\"Long.\\"}}"}'
    )

    assert parsed.summary_i18n.bg.long == "Дълго."
    assert parsed.summary_i18n.en.short == "Short."


def test_school_summary_strict_rejects_malformed_stringified_summary_i18n():
    with pytest.raises(ValidationError):
        SchoolSummaryStrict.model_validate(
            {
                "summary_i18n": '{"bg":{"short":"Кратко.","long":"Дълго."},"en":{"short":"Short.","long":"Long."}'
            }
        )


def test_validate_summary_payload_rejects_missing_data_filler():
    summary = SchoolSummaryStrict.model_validate(
        {
            "summary_i18n": {
                "bg": {"short": "Кратко.", "long": "Няма информация за извънкласни дейности."},
                "en": {"short": "Short.", "long": "There is no information available about extracurricular activities."},
            }
        }
    )

    with pytest.raises(ai_summariser.ModelRetry):
        ai_summariser._validate_summary_payload(summary)


@pytest.mark.parametrize(
    ("bg_text", "en_text"),
    [
        (
            "Училището не предлага транспорт.",
            "The school does not offer transport.",
        ),
        (
            "Родителите хвалят изключителната среда.",
            "Parents praise its exceptional learning environment.",
        ),
        (
            "В момента училището е водещо в града.",
            "The school is currently a leading choice in the city.",
        ),
        (
            "Подробностите за приема не са уточнени.",
            "Admissions details are not specified.",
        ),
        (
            "Две трети от учениците са международни.",
            "Two-thirds of the students are international.",
        ),
        (
            "Методите вдъхновяват любов към ученето.",
            "Its methods inspire a love of learning.",
        ),
    ],
)
def test_validate_summary_payload_rejects_unsupported_or_unsafe_semantic_claims(bg_text, en_text):
    summary = SchoolSummaryStrict.model_validate(
        {
            "summary_i18n": {
                "bg": {"short": bg_text, "long": bg_text},
                "en": {"short": en_text, "long": en_text},
            }
        }
    )

    with pytest.raises(ai_summariser.ModelRetry):
        ai_summariser._validate_summary_payload(summary)


def test_validate_summary_payload_rejects_exact_counts_and_times():
    summary = SchoolSummaryStrict.model_validate(
        {
            "summary_i18n": {
                "bg": {"short": "Кратко.", "long": "Работи от 8:00 до 18:00 и има 5 ученика в клас."},
                "en": {"short": "Short.", "long": "It runs from 8:00 to 18:00 and has 5 students per class."},
            }
        }
    )

    with pytest.raises(ai_summariser.ModelRetry):
        ai_summariser._validate_summary_payload(summary)


@pytest.mark.asyncio
async def test_summarize_school_fails_closed_when_generated_summary_is_semantically_invalid(db_session):
    school = School(
        name_i18n={"bg": "Затворено резюме", "en": "Closed Summary"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        summary_i18n={
            "bg": {"short": "Старо.", "long": "Старо резюме."},
            "en": {"short": "Old.", "long": "Old summary."},
        },
        attributes={
            "extracted": {"programs": ["STEM"]},
            "data_validation": _validation_payload(),
            "summary_generation": {"_schema_version": 1, "input_fingerprint": "stale"},
        },
    )
    db_session.add(school)
    await db_session.commit()

    invalid_result = {
        "summary_i18n": {
            "bg": {
                "short": "Училището не предлага транспорт.",
                "long": "Училището не предлага транспорт.",
            },
            "en": {
                "short": "The school does not offer transport.",
                "long": "The school does not offer transport.",
            },
        },
        "input_tokens": 100,
        "output_tokens": 25,
        "token_cost_usd": 0.001,
        "model_tier": "capable",
        "model": "test-model",
        "generation_mode": "llm",
    }

    with patch("app.scrapers.summarizer.generate_school_summary", new=AsyncMock(return_value=invalid_result)):
        result = await summarizer_module.summarize_school(db_session, school.id, "bg")

    assert result["status"] == "summary_failed"
    await db_session.refresh(school)
    assert school.summary_i18n == {}
    assert school.scrape_status == "extracted"
    metadata = (school.attributes or {}).get("summary_generation", {})
    assert metadata.get("last_error")
    assert "generated_at" not in metadata
