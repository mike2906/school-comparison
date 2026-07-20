"""Tests for Stage 6 data validation."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import FieldSource, Pricing, School
from app.models.field_source import SourceType
from app.models.pricing import PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.utils.website_data import prepare_validation_rollover, website_data_is_publishable
from app.schemas.validation import SpotCheckDiscrepancy, SpotCheckOutput
from app.scrapers import validator as validator_module


def test_normalize_summary_source_clears_narrative_fields_without_source_text():
    normalized = validator_module._normalize_summary_source(
        {
            "positioning": "Licensed private school for preschool through grade 7.",
            "teaching_approach": ["project-based learning"],
            "community_signals": ["partnership with parents"],
            "canonical_tags": ["Project-based learning"],
            "has_useful_info": True,
        },
        "",
        languages=[{"language": "English"}],
        programs=["Cambridge"],
    )

    assert normalized["positioning"] is None
    assert normalized["teaching_approach"] == []
    assert normalized["community_signals"] == []
    assert normalized["canonical_tags"] == ["Cambridge"]
    assert normalized["has_useful_info"] is True


@pytest.mark.parametrize(
    "source_text",
    [
        "Учениците от 9 клас участваха в благотворителната инициатива.",
        "Nine students won prizes in the regional competition.",
        (
            "исторически проект в 9. клас, който до 9 ноември се занимаваше "
            "самостоятелно с отделни аспекти"
        ),
    ],
)
def test_class_size_evidence_rejects_grade_and_news_counts(source_text):
    assert validator_module._source_mentions_class_size(source_text, "9 students") is False


@pytest.mark.parametrize(
    "source_text,class_size",
    [
        ("Класовете са с до 16 ученици.", "16"),
        ("До 16 ученици в клас.", "16 students"),
        ("The maximum class size is 18 students.", "18"),
        ("Classes have no more than 20 children.", "20"),
    ],
)
def test_class_size_evidence_requires_size_relationship(source_text, class_size):
    assert validator_module._source_mentions_class_size(source_text, class_size) is True


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("deadlines", "Краен срок за подаване на оферта-05.06.2020г. 16:00ч."),
        ("available_spots", "* [Свободни места](https://school.test/admission)"),
        ("available_spots", "Свободни места"),
        ("entrance_requirements", "интервютата"),
        ("entrance_requirements", "училищен живот"),
        ("deadlines", "![admission](https://school.test/admission.png)"),
        ("deadlines", "Admission for 2025/2026 is"),
        ("deadlines", "ВТОРАТА приемна сесия за учебната 2026/27 година ще се проведе на"),
    ],
)
def test_admission_semantics_reject_pilot_garbage(field_name, value):
    assert (
        validator_module._admission_value_is_semantically_valid(
            field_name,
            value,
            today=datetime.date(2026, 7, 14),
        )
        is False
    )


@pytest.mark.parametrize(
    "field_name,value",
    [
        ("deadlines", "Крайният срок е 30 юни 2027 г."),
        ("deadlines", "Applications are accepted year-round."),
        ("available_spots", "Остават 12 свободни места за първи клас."),
        ("entrance_requirements", "Приемът включва писмен тест и интервю."),
    ],
)
def test_admission_semantics_keeps_complete_current_facts(field_name, value):
    assert validator_module._admission_value_is_semantically_valid(
        field_name,
        value,
        today=datetime.date(2026, 7, 14),
    )


@pytest.mark.asyncio
async def test_validate_school_data_applies_safe_fixes_and_persists_report(db_session):
    school = School(
        name_i18n={"bg": "Валидатор Тест"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "extracted": {
                "languages": [
                    {"language": "English", "level": ""},
                    {"language": "english", "level": None},
                    {"language": "German", "level": "intensive"},
                ],
                "facilities": [" pool ", "Pool", ""],
                "programs": ["STEM", "stem", " "],
                "extracurricular": [],
                "accreditations": [],
                "founded_year": "3020",
                "summary_source": {
                    "positioning": "Innovative school",
                    "teaching_approach": ["project-based learning", "quality education"],
                    "community_signals": ["parent partnership", "supportive environment"],
                    "canonical_tags": ["Project-based learning"],
                    "has_useful_info": True,
                },
                "admission": {"has_useful_info": True, "deadlines": ["April"]},
                "operations": {
                    "working_hours": "08:00-17:00",
                    "daily_schedule": ["Morning lessons", "Invented midnight classes"],
                    "has_useful_info": True,
                },
            }
        },
        admission_info={"website_extracted": {"old": True}},
    )
    db_session.add(school)
    await db_session.flush()

    row1 = Pricing(
        school_id=school.id,
        category="tuition",
        amount=450,
        amount_min=500,
        amount_max=300,
        currency=" bgn ",
        period="monthly",
        source=PriceSource.SCRAPED_WEBSITE,
        source_url="https://example.test/pricing",
    )
    row2 = Pricing(
        school_id=school.id,
        category="tuition",
        amount=450,
        amount_min=500,
        amount_max=300,
        currency=" bgn ",
        period="monthly",
        source=PriceSource.SCRAPED_WEBSITE,
        source_url="https://example.test/pricing",
    )
    db_session.add_all([row1, row2])
    db_session.add(
        FieldSource(
            school_id=school.id,
            category="general_info",
            field_key="attributes.languages",
            source_type=SourceType.SCRAPED_WEBSITE,
        )
    )
    db_session.add(
        SourcePage(
            school_id=school.id,
            source_url="https://example.test/about",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown=(
                "The school uses project-based learning and works in partnership with parents. "
                "Working hours: 08:00-17:00. Morning lessons begin after arrival."
            ),
            content_hash="hash-validator-summary-source",
            last_scraped_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    await db_session.commit()
    duplicate_row_id = row2.id

    result = await validator_module.validate_school_data(
        db=db_session,
        school_id=school.id,
        country_code="bg",
        run_spot_check=False,
    )

    assert result["status"] == "ok"
    assert result["auto_fixes"] > 0

    pricing_rows = (await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))).scalars().all()
    assert len(pricing_rows) == 1
    assert pricing_rows[0].currency == "BGN"
    assert float(pricing_rows[0].amount_min) == 300
    assert float(pricing_rows[0].amount_max) == 500

    await db_session.refresh(school)
    extracted = (school.attributes or {}).get("extracted", {})
    assert extracted.get("founded_year") is None
    assert extracted.get("facilities") == ["pool"]
    assert extracted.get("summary_source", {}).get("positioning") is None
    assert extracted.get("summary_source", {}).get("teaching_approach") == ["project-based learning"]
    assert extracted.get("summary_source", {}).get("community_signals") == ["parent partnership"]
    assert extracted.get("summary_source", {}).get("canonical_tags") == ["Project-based learning"]
    assert extracted.get("admission", {}).get("deadlines") == []
    assert extracted.get("operations", {}).get("working_hours") == "08:00-17:00"
    assert extracted.get("operations", {}).get("daily_schedule") == ["Morning lessons"]
    assert school.admission_info.get("website_extracted") is None

    data_validation = (school.attributes or {}).get("data_validation", {})
    assert data_validation.get("_schema_version") == 1
    assert data_validation.get("auto_fixes")
    for fix in data_validation["auto_fixes"]:
        assert "original_value" in fix
        assert "fixed_value" in fix
    duplicate_row_fixes = [
        fix
        for fix in data_validation["auto_fixes"]
        if (fix.get("field_path") or "").startswith(f"pricing[{duplicate_row_id}]")
    ]
    assert duplicate_row_fixes
    assert all(fix.get("code") == "duplicate_pricing_row_removed" for fix in duplicate_row_fixes)


@pytest.mark.asyncio
async def test_validate_school_data_sets_needs_review_on_errors(db_session):
    school = School(
        name_i18n={"bg": "Грешка Тест"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()

    db_session.add(
        Pricing(
            school_id=school.id,
            category="tuition",
            amount=-1,
            currency="BGN",
            period="monthly",
            source=PriceSource.SCRAPED_WEBSITE,
            source_url="https://example.test/pricing",
        )
    )
    await db_session.commit()

    result = await validator_module.validate_school_data(db_session, school.id, "bg")

    assert result["status"] == "needs_review"
    assert int(result["issue_counts"]["error"]) >= 1

    await db_session.refresh(school)
    report = (school.attributes or {}).get("data_validation", {})
    assert report.get("status") == "needs_review"
    assert any(issue.get("code") == "negative_price_amount" for issue in report.get("issues", []))


@pytest.mark.asyncio
async def test_deterministic_revalidation_preserves_prior_actionable_spot_check(db_session):
    spot_check = {
        "has_discrepancy": True,
        "discrepancies": [
            {
                "field_path": "attributes.extracted.class_size",
                "kind": "unsupported",
                "issue": "Unsupported class-size claim",
                "evidence": "No class-size claim appears in the reviewed pages.",
                "cheap_value": "9 students",
                "capable_value": None,
                "confidence": 0.95,
            }
        ],
        "checked_at": "2026-07-14T09:00:00+00:00",
    }
    school = School(
        name_i18n={"bg": "Пазене на спот проверка"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "extracted": {"class_size": "9 students"},
            "data_validation": {
                "_schema_version": 1,
                "validated_at": "2026-07-14T08:00:00+00:00",
                "status": "ok",
                "issue_counts": {"error": 0, "warning": 0},
                "issues": [],
                "auto_fixes": [],
                "spot_check": spot_check,
            },
        },
    )
    db_session.add(school)
    await db_session.commit()

    result = await validator_module.validate_school_data(db_session, school.id, "bg")

    assert result["status"] == "ok"
    await db_session.refresh(school)
    assert school.attributes["data_validation"]["spot_check"] == spot_check


@pytest.mark.asyncio
async def test_validation_rollover_promotes_success_and_preserves_audit_history(db_session):
    prior = {
        "_schema_version": 1,
        "validated_at": "2026-07-19T08:00:00+00:00",
        "status": "ok",
        "issue_counts": {"error": 0, "warning": 0},
        "issues": [],
        "auto_fixes": [],
    }
    school = School(
        name_i18n={"bg": "Успешна подмяна"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes=prepare_validation_rollover(
            {"extracted": {"facilities": ["library"]}, "data_validation": prior},
            started_at="2026-07-20T08:00:00+00:00",
        ),
    )
    db_session.add(school)
    await db_session.commit()

    result = await validator_module.validate_school_data(db_session, school.id, "bg")

    assert result["status"] == "ok"
    await db_session.refresh(school)
    assert school.attributes["data_validation"]["validated_at"] != prior["validated_at"]
    assert school.attributes["data_validation_history"] == [prior]
    assert "data_validation_attempt" not in school.attributes
    assert "website_data_withheld" not in school.attributes
    assert website_data_is_publishable(school.attributes, school.scrape_status) is True


@pytest.mark.asyncio
async def test_validation_rollover_explicit_failure_withholds_and_retains_current_report(
    db_session, monkeypatch
):
    prior = {
        "_schema_version": 1,
        "validated_at": "2026-07-19T08:00:00+00:00",
        "status": "ok",
        "issue_counts": {"error": 0, "warning": 0},
        "issues": [],
        "auto_fixes": [],
    }
    school = School(
        name_i18n={"bg": "Неуспешна подмяна"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes=prepare_validation_rollover(
            {"extracted": {"languages": [{"language": "English"}]}, "data_validation": prior},
            started_at="2026-07-20T08:00:00+00:00",
        ),
    )
    db_session.add(school)
    await db_session.commit()
    monkeypatch.setattr(
        validator_module,
        "_normalize_extracted_languages",
        lambda _value: (_ for _ in ()).throw(RuntimeError("deterministic validation crashed")),
    )

    result = await validator_module.validate_school_data(db_session, school.id, "bg")

    assert result["status"] == "validation_failed"
    await db_session.refresh(school)
    assert school.attributes["data_validation"] == prior
    assert school.attributes["data_validation_attempt"]["status"] == "failed"
    assert school.attributes["website_data_withheld"] is True
    assert website_data_is_publishable(school.attributes, school.scrape_status) is False


def test_validation_rollover_interruption_retains_accepted_report_and_history():
    prior = {
        "_schema_version": 1,
        "validated_at": "2026-07-19T08:00:00+00:00",
        "status": "ok",
    }
    attributes = prepare_validation_rollover(
        {"data_validation": prior, "data_validation_history": [{**prior, "validated_at": "2026-07-18T08:00:00Z"}]},
        started_at="2026-07-20T08:00:00Z",
    )

    assert attributes["data_validation"] == prior
    assert len(attributes["data_validation_history"]) == 1
    assert attributes["data_validation_attempt"]["status"] == "pending"
    assert website_data_is_publishable(attributes, "extracted") is False


@pytest.mark.asyncio
async def test_validation_removes_semantically_invalid_admission_values(db_session):
    invalid_values = {
        "deadlines": [
            "Краен срок за подаване на оферта-05.06.2020г. 16:00ч.",
            "![admission](https://school.test/admission.png)",
            "Admission for 2025/2026 is",
            "ВТОРАТА приемна сесия за учебната 2026/27 година ще се проведе на",
        ],
        "entrance_requirements": ["интервютата", "Приемът включва писмен тест и интервю."],
        "available_spots": [
            "* [Свободни места](https://school.test/admission)",
            "Остават 12 свободни места за първи клас.",
        ],
    }
    source_text = "\n".join(value for values in invalid_values.values() for value in values)
    school = School(
        name_i18n={"bg": "Семантика на прием"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={"extracted": {"admission": {**invalid_values, "has_useful_info": True}}},
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add(
        SourcePage(
            school_id=school.id,
            source_url="https://school.test/admission",
            page_category="admission",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown=source_text,
            content_hash="admission-semantics",
            last_scraped_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    await db_session.commit()

    result = await validator_module.validate_school_data(db_session, school.id, "bg")

    assert result["status"] == "ok"
    await db_session.refresh(school)
    admission = school.attributes["extracted"]["admission"]
    assert admission["deadlines"] == []
    assert admission["entrance_requirements"] == ["Приемът включва писмен тест и интервю."]
    assert admission["available_spots"] == ["Остават 12 свободни места за първи клас."]
    fixes = school.attributes["data_validation"]["auto_fixes"]
    assert any(fix["code"] == "admission_deadlines_semantically_invalid" for fix in fixes)


@pytest.mark.asyncio
async def test_validate_school_data_clears_stale_summary_after_auto_fixes(db_session):
    school = School(
        name_i18n={"bg": "Summary Reset"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="summarized",
        summary_i18n={"bg": {"short": "старо", "long": "старо"}, "en": {"short": "old", "long": "old"}},
        attributes={
            "summary_generation": {"_schema_version": 1, "input_fingerprint": "old"},
            "extracted": {
                "languages": [{"language": "English", "level": ""}],
                "facilities": [" pool ", "Pool"],
                "programs": ["STEM"],
            },
        },
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add(
        FieldSource(
            school_id=school.id,
            category="general_info",
            field_key="attributes.languages",
            source_type=SourceType.SCRAPED_WEBSITE,
        )
    )
    await db_session.commit()

    result = await validator_module.validate_school_data(db_session, school.id, "bg")

    assert result["status"] == "ok"
    await db_session.refresh(school)
    assert school.summary_i18n == {}
    assert school.scrape_status == "extracted"
    assert "summary_generation" not in (school.attributes or {})


@pytest.mark.asyncio
async def test_validate_school_data_rolls_back_all_changes_on_error(db_session, monkeypatch):
    school = School(
        name_i18n={"bg": "Rollback Тест"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={"extracted": {"languages": [{"language": "English"}]}},
    )
    db_session.add(school)
    await db_session.flush()

    db_session.add(
        Pricing(
            school_id=school.id,
            category="tuition",
            amount=100,
            currency=" bgn ",
            period="monthly",
            source=PriceSource.SCRAPED_WEBSITE,
            source_url="https://example.test/pricing",
        )
    )
    await db_session.commit()
    school_id = school.id

    monkeypatch.setattr(
        validator_module,
        "_normalize_extracted_languages",
        lambda _value: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = await validator_module.validate_school_data(db_session, school_id, "bg")
    assert result["status"] == "validation_failed"

    pricing_row = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school_id))
    ).scalars().one()
    assert pricing_row.currency == " bgn "

    school = (await db_session.execute(select(School).where(School.id == school_id))).scalars().one()
    assert "data_validation" not in (school.attributes or {})


@pytest.mark.asyncio
async def test_run_spot_check_for_school_persists_payload(db_session, monkeypatch):
    school = School(
        name_i18n={"bg": "Spotcheck Тест"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
        attributes={
            "extracted": {"languages": [{"language": "English", "level": None}]},
            "data_validation": {
                "_schema_version": 1,
                "validated_at": "2026-07-13T09:00:00+00:00",
                "status": "ok",
                "issue_counts": {"error": 0, "warning": 0},
                "issues": [],
                "auto_fixes": [],
                "spot_check": {
                    "has_discrepancy": True,
                    "discrepancies": [
                        {
                            "field_path": "attributes.extracted.facilities",
                            "kind": "unsupported",
                            "issue": "Old finding",
                            "evidence": "Old evidence",
                        }
                    ],
                },
            },
        },
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add(
        SourcePage(
            school_id=school.id,
            source_url="https://example.test/about",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="English language classes are available.",
            content_hash="hash-spotcheck",
            last_scraped_at=datetime.datetime.now(datetime.timezone.utc),
        )
    )
    await db_session.commit()

    class FakeAgent:
        def __init__(self):
            self._validators = []

        def output_validator(self, fn):
            self._validators.append(fn)
            return fn

        async def run(self, _prompt):
            output = SpotCheckOutput(
                has_discrepancy=True,
                discrepancies=[
                    SpotCheckDiscrepancy(
                        field_path="attributes.extracted.languages",
                        issue="unsupported_language_level",
                        evidence="English language classes are available, but no C2 level is stated.",
                        cheap_value={"language": "English", "level": "C2"},
                        capable_value=None,
                        confidence=0.9,
                    )
                ],
                summary="Language level seems unsupported in source text.",
            )
            for validator in self._validators:
                output = validator(output)
            return SimpleNamespace(
                output=output,
                usage=lambda: SimpleNamespace(input_tokens=321, output_tokens=45),
                all_messages=lambda: [SimpleNamespace(provider_details={"cost": 0.0123})],
            )

    monkeypatch.setattr(validator_module, "create_agent", lambda **_kwargs: FakeAgent())

    result = await validator_module.run_spot_check_for_school(db_session, school.id, "bg")
    assert result["status"] == "checked"
    assert result["has_discrepancy"] is True

    await db_session.refresh(school)
    payload = (school.attributes or {}).get("data_validation", {}).get("spot_check", {})
    assert payload.get("has_discrepancy") is True
    assert payload.get("model_tier") == "capable"
    assert payload.get("input_tokens") == 321
    assert payload.get("output_tokens") == 45
    assert payload.get("token_cost_usd") == 0.0123
    assert [item["field_path"] for item in payload["discrepancies"]] == [
        "attributes.extracted.languages"
    ]
    assert result["input_tokens"] == 321
    assert result["output_tokens"] == 45
    assert result["token_cost_usd"] == 0.0123


def test_normalize_spot_check_output_derives_direction_and_requires_evidence():
    parsed = SpotCheckOutput(
        has_discrepancy=True,
        discrepancies=[
            SpotCheckDiscrepancy(
                field_path="programs",
                kind="unsupported",
                issue="High school programme is missing from extraction",
                cheap_value=None,
                capable_value="High School",
                evidence="High School Programme",
            ),
            SpotCheckDiscrepancy(
                field_path="class_size",
                kind="omission",
                issue="The extracted class size is not supported",
                cheap_value="12 students",
                capable_value=None,
                evidence="No class-size claim appears in the reviewed school pages.",
            ),
            SpotCheckDiscrepancy(
                field_path="facilities",
                kind="unsupported",
                issue="Claim is not supported",
                cheap_value=["pool"],
                capable_value=None,
                evidence=None,
            ),
        ],
    )

    normalized = validator_module._normalize_spot_check_output(parsed)

    assert [(item.field_path, item.kind) for item in normalized.discrepancies] == [
        ("attributes.extracted.programs", "omission"),
        ("attributes.extracted.class_size", "unsupported"),
    ]
    assert normalized.has_discrepancy is True


def test_build_spot_check_context_balances_categories_and_deduplicates():
    pages = [
        SimpleNamespace(id=1, source_url="https://school.test/", page_category="homepage", raw_markdown="HOME " * 1000),
        SimpleNamespace(id=2, source_url="https://school.test/admission", page_category="admission", raw_markdown="ADMISSION " * 500),
        SimpleNamespace(id=3, source_url="https://school.test/programs", page_category="programs", raw_markdown="PROGRAMS " * 500),
        SimpleNamespace(id=4, source_url="https://school.test/facilities", page_category="facilities", raw_markdown="FACILITIES " * 500),
        SimpleNamespace(id=5, source_url="https://school.test/programs#copy", page_category="programs", raw_markdown="PROGRAMS " * 500),
    ]

    context = validator_module._build_spot_check_context(pages, max_chars=3000)

    assert "[admission] https://school.test/admission" in context
    assert "[programs] https://school.test/programs" in context
    assert "[facilities] https://school.test/facilities" in context
    assert context.count("PROGRAMS") < 500
    assert len(context) <= 3000


def test_filter_spot_check_evidence_requires_source_quote_for_omission_and_contradiction():
    parsed = SpotCheckOutput(
        has_discrepancy=True,
        discrepancies=[
            SpotCheckDiscrepancy(
                field_path="programs",
                kind="omission",
                issue="Programme omitted",
                cheap_value=None,
                capable_value="IB Diploma",
                evidence="IB Diploma Programme",
            ),
            SpotCheckDiscrepancy(
                field_path="facilities",
                kind="contradiction",
                issue="Facility conflicts",
                cheap_value="pool",
                capable_value="gym",
                evidence="A swimming pool is available",
            ),
        ],
    )

    filtered = validator_module._filter_spot_check_evidence(
        parsed,
        "The school offers the IB Diploma Programme for grades 11 and 12.",
    )

    assert [item.field_path for item in filtered.discrepancies] == ["programs"]
    assert filtered.has_discrepancy is False
