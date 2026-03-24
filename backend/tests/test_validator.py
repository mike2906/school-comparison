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
            raw_markdown="The school uses project-based learning and works in partnership with parents.",
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
        attributes={"extracted": {"languages": [{"language": "English", "level": None}]}},
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
                        cheap_value={"language": "English", "level": "C2"},
                        capable_value={"language": "English", "level": None},
                        confidence=0.9,
                    )
                ],
                summary="Language level seems unsupported in source text.",
            )
            for validator in self._validators:
                output = validator(output)
            return SimpleNamespace(output=output)

    monkeypatch.setattr(validator_module, "create_agent", lambda **_kwargs: FakeAgent())

    result = await validator_module.run_spot_check_for_school(db_session, school.id, "bg")
    assert result["status"] == "checked"
    assert result["has_discrepancy"] is True

    await db_session.refresh(school)
    payload = (school.attributes or {}).get("data_validation", {}).get("spot_check", {})
    assert payload.get("has_discrepancy") is True
    assert payload.get("model_tier") == "capable"
