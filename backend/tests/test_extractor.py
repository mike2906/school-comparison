"""Tests for scraper extraction."""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import FieldSource, Pricing, School, SchoolLocation
from app.models.pricing import PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.extraction import (
    ExtractedLanguageFocus,
    ExtractedPrice,
    GeneralInfoExtractionOutput,
    PriceExtractionOutput,
    SummarySourceExtractionOutput,
)
from app.scrapers import extractor as extractor_module
from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG


@pytest.fixture
async def sample_school_for_extraction(db_session):
    school = School(
        name_i18n={"bg": "Тестово Училище", "en": "Test School"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
        website_url="https://test-school.bg",
        scrape_status="navigated",
    )
    db_session.add(school)
    await db_session.flush()

    page1 = SourcePage(
        school_id=school.id,
        source_url="https://test-school.bg/prices",
        page_category="pricing",
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown="Tuition for 2024 is 1000 BGN monthly.",
        content_hash="hash1",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )
    page2 = SourcePage(
        school_id=school.id,
        source_url="https://test-school.bg/about",
        page_category="about",
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown="We teach English and German. We have a pool.",
        content_hash="hash2",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add_all([page1, page2])
    await db_session.commit()
    await db_session.refresh(school)
    return school


@pytest.mark.asyncio
async def test_extract_school_persists_pricing_and_general_info(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    school.attributes = {"website_data_withheld": True}
    await db_session.commit()

    mock_price = PriceExtractionOutput(
        prices=[
            ExtractedPrice(
                category="tuition",
                amount=1000.0,
                currency="BGN",
                period="monthly",
                confidence=0.9,
            )
        ],
        has_pricing_info=True,
    )
    mock_general = GeneralInfoExtractionOutput(
        display_name_i18n={"en": "Fusion School"},
        languages=[ExtractedLanguageFocus(language="English")],
        facilities=["pool"],
        programs=["STEM"],
        extracurricular=[],
        class_size="18 students",
        founded_year="1998",
        accreditations=[],
        summary_source=SummarySourceExtractionOutput(
            teaching_approach=["project-based learning"],
            differentiators=["licensed by the Ministry of Education"],
            has_useful_info=True,
        ),
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 100, 10, 0.001), (mock_general, 150, 20, 0.002)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert result["pricing_count"] == 1
    assert result["general_info_success"] is True
    assert result["validation_status"] == "ok"
    assert result["validation_auto_fixes"] > 0

    pricing_rows = (await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))).scalars().all()
    assert len(pricing_rows) == 1

    await db_session.refresh(school)
    extracted = (school.attributes or {}).get("extracted", {})
    assert extracted.get("facilities") == ["pool"]
    assert extracted.get("programs") == []
    assert extracted.get("class_size") is None
    assert extracted.get("founded_year") is None
    assert extracted.get("summary_source", {}).get("teaching_approach") == []
    data_validation = (school.attributes or {}).get("data_validation", {})
    assert data_validation.get("status") == "ok"
    assert any(
        fix.get("code") == "programs_missing_evidence"
        for fix in data_validation.get("auto_fixes", [])
    )
    assert (school.attributes or {}).get("display_name_i18n") == {
        "bg": "Fusion School",
        "en": "Fusion School",
    }
    assert "website_data_withheld" not in (school.attributes or {})

    field_sources = (
        await db_session.execute(select(FieldSource).where(FieldSource.school_id == school.id))
    ).scalars().all()
    assert any(row.field_key == "attributes.facilities" for row in field_sources)
    assert any(row.field_key == "attributes.summary_source" for row in field_sources)
    assert any(row.field_key == "attributes.display_name_i18n" for row in field_sources)


async def _failed_llm_call(**kwargs):
    """Mimic _run_typed_agent after its retries are exhausted (or the call is refused)."""
    kwargs["llm_stats"].total_calls += 1
    kwargs["llm_stats"].hard_failures += 1
    return None, 0, 0, 0.0


@pytest.mark.asyncio
async def test_extract_school_does_not_promote_fallback_when_llm_call_fails(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction  # never published: no validation report
    school.attributes = {"website_data_withheld": True}
    await db_session.commit()

    mock_price = PriceExtractionOutput(
        prices=[
            ExtractedPrice(
                category="tuition",
                amount=1000.0,
                currency="BGN",
                period="monthly",
                confidence=0.9,
            )
        ],
        has_pricing_info=True,
    )
    calls = iter([(mock_price, 100, 10, 0.001)])

    async def _price_ok_general_fails(**kwargs):
        if kwargs["result_type"] is PriceExtractionOutput:
            return next(calls)
        return await _failed_llm_call(**kwargs)

    with patch("app.scrapers.extractor._run_typed_agent", new=_price_ok_general_fails):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extraction_failed"
    assert "fallback output not promoted" in result["error"]
    assert result["llm_stats"]["hard_failures"] == 1
    assert any("stays withheld" in detail for detail in result["details"])

    await db_session.refresh(school)
    assert school.scrape_status == "extraction_failed"
    assert school.attributes == {"website_data_withheld": True}
    pricing_rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().all()
    assert pricing_rows == []


def _website_field_source(school_id: int, url: str) -> FieldSource:
    from app.models.field_source import SourceConfidence, SourceType

    return FieldSource(
        school_id=school_id,
        category="attributes",
        field_key="attributes.facilities",
        value_text="pool",
        source_type=SourceType.SCRAPED_WEBSITE,
        source_url=url,
        scraped_at=datetime.datetime(2026, 9, 1),
        confidence=SourceConfidence.HIGH,
    )


@pytest.mark.asyncio
async def test_extract_school_does_not_republish_data_from_a_previous_site(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction
    school.attributes = {
        "extracted": {"facilities": ["pool"]},
        "data_validation": {"_schema_version": 1, "status": "ok"},
    }
    # Website discovery moved the school to test-school.bg; the stored data came
    # from another site and nothing withheld it.
    db_session.add(_website_field_source(school.id, "https://old-site.bg/about"))
    await db_session.commit()

    with patch("app.scrapers.extractor._run_typed_agent", new=_failed_llm_call):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extraction_failed"
    await db_session.refresh(school)
    assert school.scrape_status == "extraction_failed"


@pytest.mark.asyncio
async def test_extract_school_keeps_previously_published_data_when_llm_retries_fail(
    db_session,
    sample_school_for_extraction,
):
    from app.utils.website_data import website_data_is_publishable

    school = sample_school_for_extraction
    previous_attributes = {
        "extracted": {"facilities": ["pool"]},
        "data_validation": {"_schema_version": 1, "status": "ok"},
    }
    school.attributes = previous_attributes
    school.summary_i18n = {"bg": "Резюме", "en": "Summary"}
    # Earlier stages of this run moved the status off the published one.
    school.scrape_status = "navigated"
    db_session.add(
        Pricing(
            school_id=school.id,
            category="tuition",
            amount=900.0,
            currency="EUR",
            period="yearly",
            source=PriceSource.SCRAPED_WEBSITE,
            source_url="https://test-school.bg/prices",
        )
    )
    db_session.add(_website_field_source(school.id, "https://www.test-school.bg/about"))
    await db_session.commit()

    with patch("app.scrapers.extractor._run_typed_agent", new=_failed_llm_call):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extraction_failed"
    assert any("Kept previously published" in detail for detail in result["details"])

    await db_session.refresh(school)
    assert school.scrape_status == "summarized"
    assert school.attributes == previous_attributes
    assert school.summary_i18n == {"bg": "Резюме", "en": "Summary"}
    assert website_data_is_publishable(school.attributes, school.scrape_status)
    # The deterministic fallback would have written 1000 BGN monthly from the page.
    pricing_rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().all()
    assert [(row.amount, row.currency) for row in pricing_rows] == [(900.0, "EUR")]


@pytest.mark.asyncio
async def test_pricing_only_success_does_not_republish_withheld_general_data(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction
    school.attributes = {
        "website_data_withheld": True,
        "extracted": {"programs": ["Stale program"]},
        "display_name_i18n": {"bg": "Грешно име"},
    }
    await db_session.commit()

    with (
        patch(
            "app.scrapers.extractor._extract_prices",
            new=AsyncMock(
                return_value={
                    "count": 0,
                    "success": True,
                    "detail": "No pricing information found",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "token_cost_usd": 0.0,
                }
            ),
        ),
        patch(
            "app.scrapers.extractor._extract_general_info",
            new=AsyncMock(
                return_value={
                    "success": False,
                    "detail": "No general-info content found",
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "token_cost_usd": 0.0,
                }
            ),
        ),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    await db_session.refresh(school)
    assert result["status"] == "extracted"
    assert result["general_info_success"] is False
    assert school.attributes["website_data_withheld"] is True
    assert "extracted" in school.attributes
    assert school.attributes["display_name_i18n"] == {"bg": "Грешно име"}
    assert any("remains withheld" in detail for detail in result["details"])


@pytest.mark.asyncio
async def test_extract_school_pricing_drops_oversized_text_fields(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction

    mock_price = PriceExtractionOutput(
        prices=[
            ExtractedPrice(
                category="tuition",
                amount=1000.0,
                currency="BGN",
                period="monthly",
                academic_year="A" * 64,
                plan_name="B" * 140,
                age_group="C" * 80,
                confidence=0.9,
            )
        ],
        has_pricing_info=True,
    )
    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        programs=["stem"],
        class_size="18 students",
        founded_year="2001",
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 100, 10, 0.001), (mock_general, 20, 3, 0.001)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"

    row = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().one()
    assert row.age_group is None
    assert row.plan_name is None
    assert row.academic_year is None


@pytest.mark.asyncio
async def test_extract_school_augments_general_info_with_deterministic_signals(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    llm_general = GeneralInfoExtractionOutput(
        languages=[],
        facilities=["pool"],
        programs=["STEM"],
        extracurricular=[],
        class_size="18 students",
        founded_year="1998",
        accreditations=[],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 10, 2, 0.0), (llm_general, 20, 4, 0.001)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    await db_session.refresh(school)
    extracted = (school.attributes or {}).get("extracted", {})
    languages = extracted.get("languages", [])
    assert any((entry.get("language") or "").lower() == "english" for entry in languages)


@pytest.mark.asyncio
async def test_extract_school_recovers_display_name_with_targeted_capable_fallback(db_session):
    school = School(
        name_i18n={"bg": 'Частна детска градина "Детска къща Монтесори" ООД'},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://www.montessori-bulgaria.com/en",
        scrape_status="navigated",
    )
    db_session.add(school)
    await db_session.flush()

    page = SourcePage(
        school_id=school.id,
        source_url="https://www.montessori-bulgaria.com/en",
        page_category="about",
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown=(
            '[ ![Montessori House](https://www.montessori-bulgaria.com/logo.svg) **Montessori House** ]'
            '(https://www.montessori-bulgaria.com/en "Montessori House")\n'
            "We teach English. We have a pool and STEM activities."
        ),
        content_hash="montessori-house",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(page)
    await db_session.commit()

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        languages=[ExtractedLanguageFocus(language="English")],
        facilities=["pool"],
        programs=["STEM"],
        class_size="18 students",
        has_useful_info=True,
    )
    name_only = extractor_module.DisplayNameOnlyExtractionOutput(
        display_name_i18n={"en": "Montessori House"}
    )

    with (
        patch.object(extractor_module.helpers, "_extract_display_name_i18n_deterministic", return_value=None),
        patch(
            "app.scrapers.extractor._run_typed_agent",
            new=AsyncMock(
                side_effect=[
                    (mock_price, 10, 2, 0.0),
                    (mock_general, 20, 4, 0.001),
                    (name_only, 8, 2, 0.001),
                ]
            ),
        ),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert any("Recovered display name with capable fallback" in detail for detail in result["details"])
    await db_session.refresh(school)
    assert (school.attributes or {}).get("display_name_i18n") == {
        "bg": "Montessori House",
        "en": "Montessori House",
    }


@pytest.mark.asyncio
async def test_extract_school_promotes_repeated_fuller_display_name_from_page_evidence(db_session):
    school = School(
        name_i18n={"bg": 'Частна детска градина "Детска къща Монтесори" ООД'},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://www.montessori-bulgaria.com/en",
        scrape_status="navigated",
    )
    db_session.add(school)
    await db_session.flush()

    pages = [
        SourcePage(
            school_id=school.id,
            source_url="https://www.montessori-bulgaria.com/en/about-us",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown=(
                "# About us\n"
                "Montessori Children’s House (Detska kushta Montessori) helps the children unfold their potential.\n"
                '[ ![Montessori House](https://www.montessori-bulgaria.com/logo.svg) **Montessori House** ]'
                '(https://www.montessori-bulgaria.com/en "Montessori House")\n'
            ),
            content_hash="montessori-about",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
        SourcePage(
            school_id=school.id,
            source_url="https://www.montessori-bulgaria.com/en/admission",
            page_category="admission",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown=(
                "Initial visit to Montessori Children's House of both parents and the child.\n"
            ),
            content_hash="montessori-admission",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
    ]
    db_session.add_all(pages)
    await db_session.commit()

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        display_name_i18n={"en": "Montessori House"},
        languages=[ExtractedLanguageFocus(language="English")],
        facilities=["pool"],
        programs=["STEM"],
        class_size="18 students",
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 10, 2, 0.0), (mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert any("Promoted repeated fuller display name" in detail for detail in result["details"])
    await db_session.refresh(school)
    assert (school.attributes or {}).get("display_name_i18n") == {
        "bg": "Montessori Children’s House",
        "en": "Montessori Children’s House",
    }


@pytest.mark.asyncio
async def test_extract_school_does_not_promote_footer_network_name(db_session):
    school = School(
        name_i18n={"bg": '"ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ" ООД'},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://sofia-school.maplebear.bg/en/",
        scrape_status="navigated",
        attributes={"name_aliases": ["Maple Bear Sofia"]},
    )
    db_session.add(school)
    await db_session.flush()

    pages = [
        SourcePage(
            school_id=school.id,
            source_url="https://sofia-school.maplebear.bg/en/high-school-program/",
            page_category="programs",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="## © 2026 Maple Bear Global Schools Ltd.\n",
            content_hash="maple-program",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
        SourcePage(
            school_id=school.id,
            source_url="https://sofia-school.maplebear.bg/en/contact/",
            page_category="contact",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="## © 2026 Maple Bear Global Schools Ltd.\n",
            content_hash="maple-contact",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
    ]
    db_session.add_all(pages)
    await db_session.commit()

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        display_name_i18n={"en": "Maple Bear Sofia"},
        languages=[ExtractedLanguageFocus(language="English")],
        facilities=["library"],
        programs=["Canadian curriculum"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(
            side_effect=[
                (mock_price, 10, 2, 0.0),
                (mock_general, 20, 4, 0.001),
                (mock_general, 5, 1, 0.0),
            ]
        ),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert not any("Promoted repeated fuller display name" in detail for detail in result["details"])
    await db_session.refresh(school)
    assert (school.attributes or {}).get("display_name_i18n") == {
        "bg": "Maple Bear Sofia",
        "en": "Maple Bear Sofia",
    }


def test_display_name_evidence_requires_domain_alias_and_repeated_identity():
    school = School(
        id=1,
        name_i18n={"bg": 'Частна гимназия "Стив Джобс"'},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="upper_secondary",
        website_url="https://abcschool.bg",
    )
    pages = [
        SourcePage(
            school_id=1,
            source_url="https://abcschool.bg/about-us",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="ABC School helps students grow.\nWelcome to ABC School.",
            content_hash="abc-about",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
        SourcePage(
            school_id=1,
            source_url="https://abcschool.bg/admission",
            page_category="admission",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Admissions at ABC School are open.",
            content_hash="abc-admission",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
    ]

    evidence = extractor_module._build_display_name_evidence(
        {"bg": "ABC School", "en": "ABC School"},
        school=school,
        pages=pages,
    )

    assert evidence == {
        "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
        "status": "corroborated",
    }


def test_display_name_evidence_rejects_uncorroborated_headline():
    school = School(
        id=1,
        name_i18n={"bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"'},
        country_code="bg",
        city="sofia",
        school_type="state",
        education_level="upper_secondary",
        website_url="https://21su.bg",
    )
    pages = [
        SourcePage(
            school_id=1,
            source_url="https://21su.bg/news",
            page_category="news",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="# 21-во училище стана домакин\nНовина за събитие.",
            content_hash="headline",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        )
    ]

    evidence = extractor_module._build_display_name_evidence(
        {"bg": "21-во училище стана домакин"},
        school=school,
        pages=pages,
    )

    assert evidence is None


def test_display_name_evidence_requires_same_repeated_identity():
    school = School(
        id=1,
        name_i18n={"bg": "Частно училище Maple Bear Sofia"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://sofia-school.maplebear.bg/en/",
    )
    pages = [
        SourcePage(
            school_id=1,
            source_url="https://sofia-school.maplebear.bg/en/about-us",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Maple Bear Academy helps children.",
            content_hash="maple-academy-about",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
        SourcePage(
            school_id=1,
            source_url="https://sofia-school.maplebear.bg/en/admission",
            page_category="admission",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Admissions at Maple Bear Academy are open.",
            content_hash="maple-academy",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
    ]

    evidence = extractor_module._build_display_name_evidence(
        {"bg": "Maple Bear Sofia", "en": "Maple Bear Sofia"},
        school=school,
        pages=pages,
    )

    assert evidence is None


def test_display_name_evidence_preserves_comma_qualifiers():
    school = School(
        id=1,
        name_i18n={"bg": "Частно училище Maple Bear Academy Sofia"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://sofia-school.maplebear.bg/en/",
    )
    pages = [
        SourcePage(
            school_id=1,
            source_url="https://sofia-school.maplebear.bg/en/about-us",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Maple Bear Academy, Plovdiv helps children.",
            content_hash="maple-plovdiv-about",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
        SourcePage(
            school_id=1,
            source_url="https://sofia-school.maplebear.bg/en/admission",
            page_category="admission",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Admissions at Maple Bear Academy, Plovdiv are open.",
            content_hash="maple-plovdiv-admission",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
    ]

    evidence = extractor_module._build_display_name_evidence(
        {"bg": "Maple Bear Academy, Sofia", "en": "Maple Bear Academy, Sofia"},
        school=school,
        pages=pages,
    )

    assert evidence is None


def test_display_name_evidence_preserves_qualifiers_outside_quotes():
    school = School(
        id=1,
        name_i18n={"bg": 'ЧДГ "Светлина" София'},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://svetlina-sofia.bg",
    )
    pages = [
        SourcePage(
            school_id=1,
            source_url="https://svetlina-sofia.bg/about-us",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown='ЧДГ "Светлина" Пловдив помага на децата.',
            content_hash="svetlina-plovdiv-about",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
        SourcePage(
            school_id=1,
            source_url="https://svetlina-sofia.bg/admission",
            page_category="admission",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown='Прием в ЧДГ "Светлина" Пловдив.',
            content_hash="svetlina-plovdiv-admission",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
    ]

    evidence = extractor_module._build_display_name_evidence(
        {"bg": 'ЧДГ "Светлина" София'},
        school=school,
        pages=pages,
    )

    assert evidence is None


def test_display_name_evidence_strips_generic_bg_prefixes():
    school = School(
        id=1,
        name_i18n={"bg": "Частно средно училище Дорис Тенеди"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
        website_url="https://doristenedi.bg",
    )
    pages = [
        SourcePage(
            school_id=1,
            source_url="https://doristenedi.bg/about-us",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="# Частно средно училище Дорис Тенеди\nЗа нас.",
            content_hash="doris-about",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
        SourcePage(
            school_id=1,
            source_url="https://doristenedi.bg/admission",
            page_category="admission",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="# Частно средно училище Дорис Тенеди\nПрием.",
            content_hash="doris-admission",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
    ]

    evidence = extractor_module._build_display_name_evidence(
        {"bg": "Дорис Тенеди"},
        school=school,
        pages=pages,
    )

    assert evidence == {
        "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
        "status": "corroborated",
    }


def test_display_name_evidence_canonicalizes_connector_variants():
    school = School(
        id=1,
        name_i18n={"bg": "Частно училище St. George"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
        website_url="https://stgeorgeschool.bg",
    )
    pages = [
        SourcePage(
            school_id=1,
            source_url="https://stgeorgeschool.bg/about-us",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="# St. George School & Preschool\nWe help children grow.",
            content_hash="st-george-about",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
        SourcePage(
            school_id=1,
            source_url="https://stgeorgeschool.bg/admission",
            page_category="admission",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="# St. George School & Preschool\nAdmissions are open.",
            content_hash="st-george-admission",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
    ]

    evidence = extractor_module._build_display_name_evidence(
        {"bg": "St. George School And Preschool", "en": "St. George School And Preschool"},
        school=school,
        pages=pages,
    )

    assert evidence == {
        "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
        "status": "corroborated",
    }


@pytest.mark.parametrize(
    ("website_url", "display_name"),
    [
        ("https://under1roof.bg", "Under 1 Roof"),
        ("https://yourkidsbg.com", "Your Kids"),
        ("https://www.acs.bg", "American College of Sofia"),
        ("https://abckinder.org", "ABC KinderCare Centre"),
    ],
)
def test_display_name_domain_alias_supports_compact_brands_and_acronyms(
    website_url,
    display_name,
):
    assert extractor_module._display_name_has_domain_alias_match(
        {"en": display_name},
        registry_name=None,
        website_url=website_url,
    )


@pytest.mark.parametrize(
    ("website_url", "display_name"),
    [
        # Brand in the registrable domain, generic or locale subdomain in front.
        ("https://school.fusion.bg/", "Fusion School"),
        ("https://en.fusion.bg/", "Fusion School"),
        # Brand in the subdomain on a platform-hosted site.
        ("https://oudoganovo.idwebbg.com/", "OU Doganovo School"),
    ],
)
def test_display_name_domain_alias_matches_brand_in_any_host_label(
    website_url,
    display_name,
):
    """The brand is not always the leftmost host label."""
    assert extractor_module._display_name_has_domain_alias_match(
        {"en": display_name},
        registry_name=None,
        website_url=website_url,
    )


@pytest.mark.parametrize(
    ("website_url", "display_name"),
    [
        ("https://foo.international", "International School"),
        ("https://child.wordpress.com", "Wordpress School"),
        ("https://ou-doganovo.idwebbg.com", "ID Web BG School"),
        ("https://child.webflow.io", "Webflow School"),
        ("https://foo.consulting", "Consulting School"),
        ("https://school.portal.fusion.bg", "Portal School"),
        ("https://abc.portal.fusion.bg", "ABC School"),
    ],
)
def test_display_name_domain_alias_rejects_public_suffix_and_hosting_provider_labels(
    website_url,
    display_name,
):
    assert not extractor_module._display_name_has_domain_alias_match(
        {"en": display_name},
        registry_name=None,
        website_url=website_url,
    )


def test_display_name_domain_alias_keeps_school_subdomain_on_shared_host():
    for website_url, display_name in (
        ("https://ou-doganovo.idwebbg.com", "OU Doganovo School"),
        ("https://child.webflow.io", "Child School"),
    ):
        assert extractor_module._display_name_has_domain_alias_match(
            {"en": display_name},
            registry_name=None,
            website_url=website_url,
        )


def test_display_name_domain_alias_rejects_non_school_acronym_expansion():
    assert not extractor_module._display_name_has_domain_alias_match(
        {"en": "Admissions Calendar Sofia"},
        registry_name=None,
        website_url="https://acs.bg",
    )


@pytest.mark.parametrize(
    ("host_label", "display_name"),
    [
        ("school", "School"),
        ("academy", "Academy"),
        ("kindergarten", "Kindergarten"),
        ("mycollege", "College"),
        ("mypreschool", "Preschool"),
    ],
)
def test_display_name_domain_alias_rejects_generic_hosts(host_label, display_name):
    assert not extractor_module._display_name_has_domain_alias_match(
        {"en": display_name},
        registry_name=None,
        website_url=f"https://{host_label}.bg",
    )


def test_display_name_evidence_accepts_exact_identity_on_official_homepage():
    school = School(
        id=1,
        name_i18n={"bg": 'Частна детска градина "Йор Кидс"'},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://yourkidsbg.com",
    )
    pages = [
        SourcePage(
            school_id=1,
            source_url="https://yourkidsbg.com",
            page_category=None,
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown='Създавайки градина и ясла “Your Kids” ние сбъднахме една мечта.',
            content_hash="your-kids-home",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        )
    ]

    evidence = extractor_module._build_display_name_evidence(
        {"en": "Your Kids"},
        school=school,
        pages=pages,
    )

    assert evidence == {
        "signals": ["website_domain_alias_match", "exact_official_page_identity"],
        "status": "corroborated",
    }


@pytest.mark.parametrize(
    ("website_url", "page_url"),
    [
        ("https://yourkidsbg.com", "https://yourkidsbg.com/en"),
        ("https://yourkidsbg.com/international", "https://yourkidsbg.com/international/"),
    ],
)
def test_display_name_evidence_accepts_exact_identity_on_official_landing_page(
    website_url,
    page_url,
):
    school = School(
        id=1,
        name_i18n={"bg": 'Частна детска градина "Йор Кидс"'},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url=website_url,
    )
    page = SourcePage(
        school_id=1,
        source_url=page_url,
        page_category=None,
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown="Your Kids\nA warm place to learn and grow.",
        content_hash="your-kids-landing",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )

    assert extractor_module._build_display_name_evidence(
        {"en": "Your Kids"},
        school=school,
        pages=[page],
    ) == {
        "signals": ["website_domain_alias_match", "exact_official_page_identity"],
        "status": "corroborated",
    }


def test_display_name_evidence_rejects_composite_page_title_candidate():
    school = School(
        id=1,
        name_i18n={"bg": "Частна детска градина АВСландия"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://abckinder.org",
    )
    page = SourcePage(
        school_id=1,
        source_url="https://abckinder.org",
        page_category=None,
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown=(
            "Admissions and curriculum information.\n\n"
            "## HTML identity signals\nAdmissions | ABC KinderCare Centre"
        ),
        content_hash="abc-split-title",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )

    assert (
        extractor_module._build_display_name_evidence(
            {"en": "Admissions | ABC KinderCare Centre"},
            school=school,
            pages=[page],
        )
        is None
    )


def test_display_name_evidence_preserves_compact_digit_brand_styling():
    school = School(
        id=1,
        name_i18n={"bg": 'Частна детска градина "Под 1 покрив"'},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://under1roof.bg",
    )
    page = SourcePage(
        school_id=1,
        source_url="https://under1roof.bg/about",
        page_category="about",
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown="Under1Roof е частна детска градина.",
        content_hash="under-one-roof",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )

    evidence = extractor_module._build_display_name_evidence(
        {"en": "Under 1 Roof"},
        school=school,
        pages=[page],
    )

    assert evidence == {
        "signals": ["website_domain_alias_match", "exact_official_page_identity"],
        "status": "corroborated",
    }


def test_promote_display_name_accepts_exact_official_candidate_with_domain_match():
    school = School(
        id=541,
        name_i18n={"bg": "Частна детска градина АВСландия"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
        website_url="https://abckinder.org",
    )
    pages = [
        SourcePage(
            school_id=541,
            source_url="https://abckinder.org",
            page_category=None,
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="ABC KinderCare Centre\nWe provide early childhood education.",
            content_hash="abc-home-a",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
        SourcePage(
            school_id=541,
            source_url="https://abckinder.org/",
            page_category=None,
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="ABC KinderCare Centre\nInternational kindergarten in Sofia.",
            content_hash="abc-home-b",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        ),
    ]
    parsed = GeneralInfoExtractionOutput(
        display_name_i18n={"bg": "ABC Landia", "en": "ABC Landia"}
    )

    promoted, note = extractor_module._promote_repeated_display_name_candidate(
        parsed,
        school=school,
        pages=pages,
    )

    assert promoted.display_name_i18n == {
        "bg": "ABC KinderCare Centre",
        "en": "ABC KinderCare Centre",
    }
    assert note == "Promoted repeated fuller display name from page evidence: ABC KinderCare Centre"


def test_display_name_evidence_does_not_invent_pythagoras_from_pitagor_site():
    school = School(
        id=1,
        name_i18n={"bg": 'Частно средно училище "Питагор"'},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="upper_secondary",
        website_url="https://pitagor-school.eu",
    )
    pages = [
        SourcePage(
            school_id=1,
            source_url="https://pitagor-school.eu",
            page_category=None,
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown='Училище по математика „Питагор“',
            content_hash="pitagor-home",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        )
    ]

    assert (
        extractor_module._build_display_name_evidence(
            {"bg": "Питагор", "en": "Pythagoras School"},
            school=school,
            pages=pages,
        )
        is None
    )


@pytest.mark.asyncio
async def test_extract_school_clears_stale_summary_metadata_on_success(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    school.scrape_status = "summarized"
    school.summary_i18n = {
        "bg": {"short": "старо", "long": "старо"},
        "en": {"short": "old", "long": "old"},
    }
    school.attributes = {
        **(school.attributes or {}),
        "summary_generation": {"_schema_version": 1, "input_fingerprint": "old"},
    }
    db_session.add(school)
    await db_session.commit()

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        languages=[ExtractedLanguageFocus(language="English")],
        facilities=["pool"],
        programs=["STEM"],
        class_size="18 students",
        founded_year="2001",
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(
            side_effect=[
                (mock_price, 10, 2, 0.0),
                (mock_general, 20, 4, 0.001),
                (mock_general, 15, 3, 0.001),
            ]
        ),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    await db_session.refresh(school)
    assert school.scrape_status == "extracted"
    assert school.summary_i18n == {}
    assert "summary_generation" not in (school.attributes or {})


def test_extract_contact_info_deterministic_captures_postal_address():
    contact = extractor_module.helpers._extract_contact_info_deterministic(
        """
        ## Контакти
        **Телефон** 02/1234567
        **Адрес**
        гр. София, ул. "Флора Кънева" № 7
        """
    )

    assert contact is not None
    assert contact["address"] == 'гр. София, ул. "Флора Кънева" № 7'


def test_address_is_precise_enough_for_override_requires_street_number():
    assert extractor_module._address_is_precise_enough_for_override('гр. София, ул. "Флора Кънева" № 7')
    assert extractor_module._address_is_precise_enough_for_override("гр. София, ул. Есен 2A")
    assert not extractor_module._address_is_precise_enough_for_override('гр. София, ул. "Иван Вазов", ет. 1, ап. 2')
    assert not extractor_module._address_is_precise_enough_for_override('гр. София, бул. "Джеймс Баучер", офис 3')
    assert not extractor_module._address_is_precise_enough_for_override("бул. Джеймс Баучер офис 3")
    assert not extractor_module._address_is_precise_enough_for_override("ул. Иван Вазов ет. 1 ап. 2")


@pytest.mark.asyncio
async def test_extract_school_replaces_office_like_registry_address_with_website_contact_address(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'бул. "Джеймс Баучер" № 116, ет. 1, ап. 4'},
        lat=42.65,
        lng=23.31,
        location_tags=["coords_source=nominatim"],
        is_primary=True,
    )
    db_session.add(location)

    contact_page = SourcePage(
        school_id=school.id,
        source_url="https://test-school.bg/contact",
        page_category="contact",
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown="""
        ## Контакти
        **Адрес**
        гр. София, ул. "Флора Кънева" № 7
        Coordinates: 42.650340, 23.319464
        **Телефон** 02/1234567
        """,
        content_hash="hash-contact",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(contact_page)
    await db_session.commit()

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 10, 2, 0.0), (mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    await db_session.refresh(location)
    assert location.address_i18n == {"bg": 'гр. София, ул. "Флора Кънева" № 7'}
    assert location.lat == pytest.approx(42.65034)
    assert location.lng == pytest.approx(23.319464)
    assert "address_source=website_contact" in (location.location_tags or [])
    assert "coords_source=website_map_link" in (location.location_tags or [])
    assert location.geocode_meta["status"] == "accepted"
    assert location.geocode_meta["method"] == "website_map_link"
    assert location.geocode_meta["precision"] == "exact"

    field_sources = (
        await db_session.execute(select(FieldSource).where(FieldSource.school_id == school.id))
    ).scalars().all()
    assert any(row.field_key == "locations.primary.address_i18n.bg" for row in field_sources)


@pytest.mark.asyncio
async def test_extract_school_refreshes_coords_from_website_map_link_when_address_already_matches(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'гр. София, ул. "Флора Кънева" № 7'},
        lat=42.0,
        lng=23.0,
        location_tags=["address_source=website_contact"],
        is_primary=True,
    )
    db_session.add(location)

    contact_page = SourcePage(
        school_id=school.id,
        source_url="https://test-school.bg/contact",
        page_category="contact",
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown="""
        ## Контакти
        **Адрес**
        гр. София, ул. "Флора Кънева" № 7
        Coordinates: 42.650340, 23.319464
        **Телефон** 02/1234567
        """,
        content_hash="hash-contact-2",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(contact_page)
    await db_session.commit()

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 10, 2, 0.0), (mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    await db_session.refresh(location)
    assert location.address_i18n == {"bg": 'гр. София, ул. "Флора Кънева" № 7'}
    assert location.lat == pytest.approx(42.65034)
    assert location.lng == pytest.approx(23.319464)
    assert "coords_source=website_map_link" in (location.location_tags or [])
    assert location.geocode_meta["status"] == "accepted"
    assert location.geocode_meta["method"] == "website_map_link"


@pytest.mark.asyncio
async def test_website_map_coordinates_use_write_gate_and_reject_out_of_bounds(db_session):
    school = School(
        name_i18n={"bg": "Тестово училище"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
    )
    db_session.add(school)
    await db_session.flush()
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'гр. София, ул. "Флора Кънева" № 7'},
        lat=42.65,
        lng=23.31,
        is_primary=True,
    )
    db_session.add(location)
    await db_session.commit()

    result = await extractor_module._sync_primary_location_from_contact_address(
        db_session,
        school,
        {
            "address": 'гр. София, ул. "Флора Кънева" № 7',
            "coordinates": {"lat": 41.0, "lng": 24.0},
        },
    )
    await db_session.commit()

    assert result is not None
    await db_session.refresh(location)
    assert location.lat is None
    assert location.lng is None
    assert location.geocode_meta["status"] == "rejected"
    assert location.geocode_meta["method"] == "website_map_link"
    assert location.geocode_meta["rejection_reason"] == "outside_sofia_write_bounds"
    assert "coords_source=website_map_link" not in (location.location_tags or [])


@pytest.mark.asyncio
async def test_website_map_coordinates_do_not_replace_official_point(db_session):
    school = School(
        name_i18n={"bg": "ДГ №4 Слънчо"},
        country_code="bg",
        city="sofia",
        school_type="state",
        education_level="kindergarten",
    )
    db_session.add(school)
    await db_session.flush()
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Ела" № 6'},
        lat=42.6812,
        lng=23.2012,
        location_tags=["source=kg_sofia_bg", OFFICIAL_COORDS_TAG],
        is_primary=True,
    )
    db_session.add(location)
    await db_session.commit()

    await extractor_module._sync_primary_location_from_contact_address(
        db_session,
        school,
        {"address": 'ул. "Ела" № 6', "coordinates": {"lat": 42.70, "lng": 23.28}},
    )
    await db_session.commit()

    await db_session.refresh(location)
    assert (location.lat, location.lng) == (42.6812, 23.2012)
    assert OFFICIAL_COORDS_TAG in location.location_tags
    assert "coords_source=website_map_link" not in location.location_tags


@pytest.mark.asyncio
async def test_website_map_coordinates_do_not_replace_hand_corrected_pin(db_session):
    school = School(
        name_i18n={"bg": "ЧДГ Светлина"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
    )
    db_session.add(school)
    await db_session.flush()
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'ул. "Св. Седмочисленици" № 23'},
        lat=42.674748,
        lng=23.325031,
        geocode_meta={"status": "accepted", "method": "manual_fix"},
        is_primary=True,
    )
    db_session.add(location)
    await db_session.commit()

    await extractor_module._sync_primary_location_from_contact_address(
        db_session,
        school,
        {"address": 'ул. "Св. Седмочисленици" № 23', "coordinates": {"lat": 42.70, "lng": 23.28}},
    )
    await db_session.commit()

    await db_session.refresh(location)
    assert (location.lat, location.lng) == (42.674748, 23.325031)
    assert location.geocode_meta["method"] == "manual_fix"
    assert "coords_source=website_map_link" not in (location.location_tags or [])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_address", "pin", "expected_address"),
    [
        # A different building: the pin was matched to the stored address, so both stay.
        ('ул. "Роза" № 3, ап. 4', {"location_tags": [OFFICIAL_COORDS_TAG]}, 'ул. "Роза" № 3, ап. 4'),
        ('ул. "Роза" № 3, офис 2', {"geocode_meta": {"method": "manual_fix"}}, 'ул. "Роза" № 3, офис 2'),
        # The same building written differently, or no stored address: nothing to contradict.
        ('ул. "Ела" № 6, офис 2', {"geocode_meta": {"method": "manual_fix"}}, 'ул. "Ела" № 6'),
        ("", {"location_tags": [OFFICIAL_COORDS_TAG]}, 'ул. "Ела" № 6'),
        # An ordinary geocoded point is not pinned and follows the address as before.
        ('ул. "Роза" № 3, офис 2', {"geocode_meta": {"method": "nominatim"}}, 'ул. "Ела" № 6'),
    ],
)
async def test_website_address_does_not_replace_a_pinned_locations_other_building(
    db_session, stored_address, pin, expected_address
):
    school = School(
        name_i18n={"bg": "ЧДГ Светлина"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="kindergarten",
    )
    db_session.add(school)
    await db_session.flush()
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": stored_address},
        lat=42.6812,
        lng=23.2012,
        is_primary=True,
        **pin,
    )
    db_session.add(location)
    await db_session.commit()

    await extractor_module._sync_primary_location_from_contact_address(
        db_session, school, {"address": 'ул. "Ела" № 6'}
    )
    await db_session.commit()

    await db_session.refresh(location)
    assert location.address_i18n["bg"] == expected_address
    assert (location.lat, location.lng) == (42.6812, 23.2012)


@pytest.mark.asyncio
async def test_extract_school_does_not_replace_with_office_like_website_contact_address(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": 'гр. София, ул. "Флора Кънева" № 7'},
        lat=42.65,
        lng=23.31,
        location_tags=["coords_source=nominatim"],
        is_primary=True,
    )
    db_session.add(location)

    contact_page = SourcePage(
        school_id=school.id,
        source_url="https://test-school.bg/contact",
        page_category="contact",
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown="""
        ## Контакти
        **Адрес**
        гр. София, ул. "Иван Вазов", ет. 1, ап. 2
        **Телефон** 02/1234567
        """,
        content_hash="hash-contact-office-like",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(contact_page)
    await db_session.commit()

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 10, 2, 0.0), (mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    await db_session.refresh(location)
    assert location.address_i18n == {"bg": 'гр. София, ул. "Флора Кънева" № 7'}
    assert location.lat == pytest.approx(42.65)
    assert location.lng == pytest.approx(23.31)
    assert "address_source=website_contact" not in (location.location_tags or [])


@pytest.mark.asyncio
async def test_extract_school_keeps_existing_coords_when_contact_address_changes_without_map_link(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": "гр. София"},
        lat=42.65,
        lng=23.31,
        location_tags=["coords_source=geojson"],
        is_primary=True,
    )
    db_session.add(location)

    contact_page = SourcePage(
        school_id=school.id,
        source_url="https://test-school.bg/contact",
        page_category="contact",
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown="""
        ## Контакти
        **Адрес**
        гр. София, ул. "Флора Кънева" № 7
        **Телефон** 02/1234567
        """,
        content_hash="hash-contact-keep-existing-coords",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(contact_page)
    await db_session.commit()

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 10, 2, 0.0), (mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    await db_session.refresh(location)
    assert location.address_i18n == {"bg": 'гр. София, ул. "Флора Кънева" № 7'}
    assert location.lat == pytest.approx(42.65)
    assert location.lng == pytest.approx(23.31)
    assert "coords_source=geojson" in (location.location_tags or [])
    assert "address_source=website_contact" in (location.location_tags or [])


@pytest.mark.asyncio
async def test_run_typed_agent_model_retry_recovers(monkeypatch):
    llm_stats = extractor_module.ExtractionLLMStats()

    class FakeAgentResult:
        def __init__(self, payload):
            self.output = payload

        def usage(self):
            return SimpleNamespace(input_tokens=7, output_tokens=3)

    class FakeAgent:
        def __init__(self, **kwargs):
            self._validators = []

        def output_validator(self, fn):
            self._validators.append(fn)
            return fn

        async def run(self, _prompt):
            bad = PriceExtractionOutput(prices=[], has_pricing_info=True)
            for validator in self._validators:
                try:
                    validator(bad)
                except Exception:
                    pass
            good = PriceExtractionOutput(
                prices=[
                    ExtractedPrice(
                        category="tuition",
                        amount=500,
                        currency="BGN",
                        period="monthly",
                        confidence=0.9,
                    )
                ],
                has_pricing_info=True,
            )
            return FakeAgentResult(good)

    monkeypatch.setattr(extractor_module, "Agent", FakeAgent)
    monkeypatch.setattr(extractor_module, "_build_openrouter_model", lambda *_args, **_kwargs: object())

    parsed, input_tokens, output_tokens, token_cost_usd = await extractor_module._run_typed_agent(
        system_prompt="x",
        user_prompt="y",
        result_type=PriceExtractionOutput,
        timeout_seconds=5.0,
        llm_stats=llm_stats,
    )

    assert parsed is not None
    assert parsed.has_pricing_info is True
    assert len(parsed.prices) == 1
    assert llm_stats.model_retries == 1
    assert input_tokens == 7
    assert output_tokens == 3
    assert token_cost_usd == 0.0


@pytest.mark.asyncio
async def test_run_typed_agent_falls_back_from_cheap_to_capable_for_general_info(monkeypatch):
    llm_stats = extractor_module.ExtractionLLMStats()

    class FakeAgentResult:
        def __init__(self, payload):
            self.output = payload

        def usage(self):
            return SimpleNamespace(input_tokens=11, output_tokens=5)

    class FakeAgent:
        def __init__(self, model=None, **kwargs):
            self.model = model

        def output_validator(self, fn):
            return fn

        async def run(self, _prompt):
            if self.model == "cheap":
                raise RuntimeError("cheap tier failed")
            return FakeAgentResult(
                GeneralInfoExtractionOutput(
                    programs=["STEM"],
                    has_useful_info=True,
                )
            )

    monkeypatch.setattr(extractor_module, "Agent", FakeAgent)
    monkeypatch.setattr(extractor_module, "_build_openrouter_model", lambda tier="cheap": tier)

    parsed, input_tokens, output_tokens, token_cost_usd = await extractor_module._run_typed_agent(
        system_prompt="x",
        user_prompt="y",
        result_type=GeneralInfoExtractionOutput,
        timeout_seconds=5.0,
        llm_stats=llm_stats,
    )

    assert parsed is not None
    assert parsed.programs == ["STEM"]
    assert llm_stats.capable_fallback_attempts == 1
    assert llm_stats.capable_fallback_successes == 1
    assert llm_stats.hard_failures == 0
    assert input_tokens == 11
    assert output_tokens == 5
    assert token_cost_usd == 0.0


@pytest.mark.asyncio
async def test_run_typed_agent_retries_timed_out_call_with_longer_timeout(monkeypatch):
    from app.config import get_settings

    llm_stats = extractor_module.ExtractionLLMStats()
    monkeypatch.setattr(get_settings(), "extraction_llm_retry_timeout_seconds", 5.0)
    monkeypatch.setattr(get_settings(), "extraction_llm_timeout_retries", 2)
    delays = iter([1.0, 0.2])  # the retry is slower than the first timeout allows

    class FakeAgentResult:
        output = PriceExtractionOutput(prices=[], has_pricing_info=False)

        def usage(self):
            return SimpleNamespace(input_tokens=4, output_tokens=2)

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def output_validator(self, fn):
            return fn

        async def run(self, _prompt):
            await asyncio.sleep(next(delays))
            return FakeAgentResult()

    monkeypatch.setattr(extractor_module, "Agent", FakeAgent)
    monkeypatch.setattr(extractor_module, "_build_openrouter_model", lambda *_args, **_kwargs: object())

    parsed, input_tokens, _output_tokens, _cost = await extractor_module._run_typed_agent(
        system_prompt="x",
        user_prompt="y",
        result_type=PriceExtractionOutput,
        timeout_seconds=0.05,
        llm_stats=llm_stats,
    )

    assert parsed is not None
    assert input_tokens == 4
    assert llm_stats.timeout_retries == 1
    assert llm_stats.hard_failures == 0


@pytest.mark.asyncio
async def test_run_typed_agent_gives_up_after_timeout_retries(monkeypatch):
    from app.config import get_settings

    llm_stats = extractor_module.ExtractionLLMStats()
    monkeypatch.setattr(get_settings(), "extraction_llm_retry_timeout_seconds", 0.05)
    monkeypatch.setattr(get_settings(), "extraction_llm_timeout_retries", 2)
    runs = 0

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def output_validator(self, fn):
            return fn

        async def run(self, _prompt):
            nonlocal runs
            runs += 1
            await asyncio.sleep(1.0)

    monkeypatch.setattr(extractor_module, "Agent", FakeAgent)
    monkeypatch.setattr(extractor_module, "_build_openrouter_model", lambda *_args, **_kwargs: object())

    parsed, *_ = await extractor_module._run_typed_agent(
        system_prompt="x",
        user_prompt="y",
        result_type=PriceExtractionOutput,
        timeout_seconds=0.05,
        llm_stats=llm_stats,
    )

    assert parsed is None
    assert runs == 3
    assert llm_stats.timeout_retries == 2
    assert llm_stats.hard_failures == 1


@pytest.mark.asyncio
async def test_run_typed_agent_makes_no_call_after_a_hard_failure(monkeypatch):
    llm_stats = extractor_module.ExtractionLLMStats(hard_failures=1)

    class FakeAgent:
        def __init__(self, **kwargs):
            raise AssertionError("no LLM call after a hard failure")

    monkeypatch.setattr(extractor_module, "Agent", FakeAgent)
    monkeypatch.setattr(extractor_module, "_build_openrouter_model", lambda *_args, **_kwargs: object())

    parsed, *_ = await extractor_module._run_typed_agent(
        system_prompt="x",
        user_prompt="y",
        result_type=GeneralInfoExtractionOutput,
        timeout_seconds=5.0,
        llm_stats=llm_stats,
    )

    assert parsed is None
    assert llm_stats.total_calls == 0
    assert llm_stats.hard_failures == 1


def test_extract_openrouter_cost_usd_from_provider_details():
    message_a = SimpleNamespace(provider_details={"cost": 0.001})
    message_b = SimpleNamespace(provider_details={"cost": 0.0025})
    message_c = SimpleNamespace(provider_details={"ignored": True})
    result = SimpleNamespace(
        all_messages=lambda: [message_a, message_b, message_c],
        response=SimpleNamespace(provider_details={"cost": 0.5}),
    )

    cost = extractor_module._extract_openrouter_cost_usd(result)
    assert cost == 0.0035


def test_extract_openrouter_cost_usd_falls_back_to_response_details():
    result = SimpleNamespace(
        all_messages=lambda: [],
        response=SimpleNamespace(provider_details={"cost": "0.004"}),
    )
    cost = extractor_module._extract_openrouter_cost_usd(result)
    assert cost == 0.004


def test_normalize_summary_source_output_drops_generic_fluff():
    normalized = extractor_module.helpers._normalize_summary_source_output(
        SummarySourceExtractionOutput(
            positioning="Innovative school",
            teaching_approach=["project-based learning", "quality education", "project-based learning"],
            community_signals=["parent partnership", "supportive environment"],
            differentiators=["licensed by the Ministry of Education"],
            canonical_tags=["Project-based learning", "Project-based learning"],
        )
    )

    assert normalized.positioning is None
    assert normalized.teaching_approach == ["project-based learning"]
    assert normalized.community_signals == ["parent partnership"]
    assert normalized.differentiators == ["licensed by the Ministry of Education"]
    assert normalized.canonical_tags == ["Project-based learning"]


def test_normalize_summary_source_output_filters_category_noise():
    normalized = extractor_module.helpers._normalize_summary_source_output(
        SummarySourceExtractionOutput(
            positioning="Научете разликите в подхода на двете места",
            teaching_approach=["Waldorf pedagogy", "administration@waldorf.bg"],
            student_experience=["Summer camp", "European Sports Week 2025"],
            community_signals=["Parent partnership", "Media and partners for us"],
            differentiators=["Licensed by the Ministry of Education", "All rights reserved"],
        )
    )

    assert normalized.positioning is None
    assert normalized.teaching_approach == ["Waldorf pedagogy"]
    assert normalized.student_experience == ["Summer camp"]
    assert normalized.community_signals == ["Parent partnership"]
    assert normalized.differentiators == ["Licensed by the Ministry of Education"]
    assert normalized.canonical_tags == ["Waldorf"]


def test_normalize_summary_source_output_derives_canonical_tags_from_languages_and_programs():
    normalized = extractor_module.helpers._normalize_summary_source_output(
        SummarySourceExtractionOutput(
            teaching_approach=["project-based learning"],
        ),
        language_candidates=[ExtractedLanguageFocus(language="German"), ExtractedLanguageFocus(language="English")],
        program_candidates=["Cambridge"],
    )

    assert normalized.canonical_tags == [
        "Cambridge",
        "Project-based learning",
        "German-focused",
    ]


def test_select_pages_for_summary_source_prefers_about_over_news_and_parents():
    school = School(
        name_i18n={"bg": "Тест"},
        website_url="https://example-school.bg",
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
    )
    pages = [
        SourcePage(
            source_url="https://example-school.bg/novini/european-sports-week-2025",
            page_category="news",
            raw_markdown="European Sports Week 2025",
        ),
        SourcePage(
            source_url="https://example-school.bg/za-roditeli/dokumenti",
            page_category="about",
            raw_markdown="Parent documents and forms",
        ),
        SourcePage(
            source_url="https://example-school.bg/zashto-fusion/obrazovatelen-model",
            page_category="about",
            raw_markdown="Fusion educational model and project-based learning",
        ),
    ]

    selected_text, source_urls = extractor_module.helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["about", "programs", "facilities"],
        use_case="general_summary_source",
        include_tokens=extractor_module.GENERAL_INFO_HINT_TOKENS,
    )

    assert "Fusion educational model" in selected_text
    assert source_urls[0] == "https://example-school.bg/zashto-fusion/obrazovatelen-model"
    assert all("novini" not in url for url in source_urls)


def test_select_pages_for_summary_source_filters_operational_pages_when_narrative_exists():
    school = School(
        name_i18n={"bg": "Тест"},
        website_url="https://example-school.bg",
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
    )
    pages = [
        SourcePage(
            source_url="https://example-school.bg/priem",
            page_category="admission",
            raw_markdown="Admission documents, pricing, and working hours.",
        ),
        SourcePage(
            source_url="https://example-school.bg/filosofia",
            page_category="about",
            raw_markdown="Our philosophy uses Montessori methods and project-based learning.",
        ),
    ]

    selected_text, source_urls = extractor_module.helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["about", "programs", "facilities"],
        use_case="general_summary_source",
        include_tokens=extractor_module.GENERAL_INFO_HINT_TOKENS,
    )

    assert "Montessori methods" in selected_text
    assert source_urls == ["https://example-school.bg/filosofia"]


def test_select_pages_for_pricing_prefers_tseni_slug_even_without_pricing_category():
    school = School(
        name_i18n={"bg": "Тест"},
        website_url="https://example-school.bg",
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
    )
    pages = [
        SourcePage(
            source_url="https://example-school.bg/zashto-fusion/programa",
            page_category="programs",
            raw_markdown="Project-based learning and classroom routines.",
        ),
        SourcePage(
            source_url="https://example-school.bg/priem/grafik-i-tseni",
            page_category=None,
            raw_markdown='График и цени\nТакси "Обучение" за 1-4 клас\nТакса "Храна".',
        ),
    ]

    selected_text, source_urls = extractor_module.helpers._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["pricing", "admission", "contact"],
        use_case="pricing",
        include_tokens=("price", "pricing", "fees", "tuition", "такси", "цени"),
    )

    assert 'Такси "Обучение"' in selected_text
    assert source_urls[0] == "https://example-school.bg/priem/grafik-i-tseni"


def test_extract_prices_deterministic_parses_fusion_style_pricing_sections():
    parsed = extractor_module.helpers._extract_prices_deterministic(
        """
        # График и цени
        ## Такси „Обучение“ за Предучилищна – учебна 2025-2026 година
        ### Стандартна такса:
        За 1 вноска: €6650
        На 2 вноски: 2 вноски по €3475
        На 4 вноски: 4 вноски по €1775
        На 9 вноски: 9 вноски по €811
        таксата включва обучение до 17:30 ч., без допълнителни такси за удължен ден
        ## Такси „Обучение“ за 1, 2, 3, 4, 5, 6 и 7 клас – учебна 2025-2026 година
        ### Стандартна такса:
        За 1 вноска: €7150
        На 2 вноски: 2 вноски по €3775
        ## Учебници и всички необходими консумативи – €545
        """
    )

    assert parsed.has_pricing_info is True
    assert len(parsed.prices) == 3
    assert parsed.prices[0].category == "tuition"
    assert parsed.prices[0].amount == 6650
    assert parsed.prices[0].currency == "EUR"
    assert parsed.prices[0].period is None
    assert parsed.prices[0].age_group == "preschool"
    assert parsed.prices[0].academic_year == "2025-2026"
    assert any("2 вноски" in item for item in parsed.prices[0].installments)
    assert parsed.prices[1].age_group == "1-7 клас"
    assert parsed.prices[2].category == "materials"
    assert parsed.prices[2].amount == 545


def test_extract_prices_deterministic_handles_generic_monthly_fee_page():
    parsed = extractor_module.helpers._extract_prices_deterministic(
        """
        ## ТАКСИ
        € 530 | 1037 лв. - целодневно гледане, ежемесечно заплащане
        € 350 | 685 лв. - половин ден с включен обяд
        € 265 | 518 лв. – депозит за запазване на място
        € 510 | 997 лв. - при предплащане за 3 месеца
        € 490 | 958 лв. - при предплащане над 6 месеца
        Допълнителните дейности не са включени в таксата и се заплащат отделно.
        обучение по английски език - € 20 / 39.12 лв. месечна такса
        """
    )

    by_amount = {price.amount: price for price in parsed.prices}

    assert parsed.has_pricing_info is True
    assert by_amount[530].category == "tuition"
    assert by_amount[530].period is None
    assert by_amount[350].period is None
    assert by_amount[265].category == "registration"
    assert by_amount[265].period is None
    assert by_amount[510].category == "tuition"
    assert by_amount[490].category == "tuition"
    assert by_amount[20].category == "extracurricular"
    assert by_amount[20].period == "monthly"


def test_extract_prices_deterministic_resets_after_optional_services_section():
    parsed = extractor_module.helpers._extract_prices_deterministic(
        """
        Такса "Обучение, консумативи и образователни ресурси" за една уч. година
        Първи клас - € 8170
        Такси за учебната 2025/2026 г. УСЛУГИ ПО ЖЕЛАНИЕ НА РОДИТЕЛИТЕ И С ДОПЪЛНИТЕЛНО ЗАПЛАЩАНЕ
        Чуждоезиков курс с преподаватели от UK
        € 300 за 30 уч. часа седмично
        Отбори по математика 2 - 5 клас - € 360 на срок
        """
    )

    tuition_amounts = sorted(price.amount for price in parsed.prices if price.category == "tuition")
    extracurricular_by_amount = {price.amount: price for price in parsed.prices if price.category == "extracurricular"}
    tuition_by_amount = {price.amount: price for price in parsed.prices if price.category == "tuition"}

    assert parsed.has_pricing_info is True
    assert tuition_amounts == [8170]
    assert tuition_by_amount[8170].period == "yearly"
    assert extracurricular_by_amount[300].period is None
    assert extracurricular_by_amount[360].period == "term"


def test_extract_prices_deterministic_keeps_registration_and_optional_services_out_of_tuition():
    parsed = extractor_module.helpers._extract_prices_deterministic(
        """
        School fees
        TUITION FEES FOR THE ACADEMIC 2025/2026 YEAR
        5 - 7 grade | € 8890: Students from Bulgarian schools
        Additional Services
        Registration fee (for all candidates): € 200
        School Transport (optional): € 2100
        Cafeteria Meals (optional): € 345 (per quarter)
        """
    )

    by_amount = {price.amount: price for price in parsed.prices}
    tuition_amounts = sorted(price.amount for price in parsed.prices if price.category == "tuition")

    assert parsed.has_pricing_info is True
    assert tuition_amounts == [8890]
    assert by_amount[200].category == "registration"
    assert by_amount[200].period is None
    assert by_amount[2100].period is None
    assert by_amount[345].category == "food"
    assert by_amount[345].period == "quarter"


def test_extract_prices_deterministic_does_not_turn_installments_into_periods():
    parsed = extractor_module.helpers._extract_prices_deterministic(
        """
        Годишна такса „Обучение“
        Плащане на пълна такса
        7,950€
        Плащане на две вноски
        2×4,094€
        Месечно заплащане
        10×843€
        """
    )

    by_amount = {price.amount: price for price in parsed.prices}

    assert by_amount[7950].period == "yearly"
    assert by_amount[4094].period is None
    assert by_amount[843].period is None


@pytest.mark.parametrize(
    ("line", "expected_period"),
    [
        ("School Transport: €2,100 total, payable in two installments", None),
        ("Tuition in two installments: €4,000 each", None),
        ("Обучение на две вноски: по 4 000 EUR", None),
    ],
)
def test_detect_price_period_does_not_infer_from_installment_plans(
    line,
    expected_period,
):
    assert extractor_module.helpers._detect_price_period(line) == expected_period


@pytest.mark.parametrize(
    ("line", "expected_period"),
    [
        ("Annual tuition fee: EUR 8,000", "yearly"),
        ("Tuition fee: EUR 800 per month", "monthly"),
        ("Tuition fee: EUR 2,000 per term", "term"),
        ("Tuition fee: EUR 4,000 per semester", "semester"),
        ("Meal fee: EUR 1,000 per quarter", "quarter"),
        ("One-time registration fee: EUR 200", "one_time"),
    ],
)
def test_detect_price_period_normalizes_explicit_supported_periods(line, expected_period):
    assert extractor_module.helpers._detect_price_period(line) == expected_period


@pytest.mark.parametrize(
    ("line", "expected_period"),
    [
        ("Annual tuition fee: EUR 8,000, payable quarterly", "yearly"),
        ("Annual tuition fee: EUR 8,000, payable per quarter", "yearly"),
        ("Tuition fee: EUR 8,000, payable per quarter", None),
        ("Quarterly instalment: EUR 2,000", None),
        ("Quarterly installment: EUR 2,000", None),
        ("Quarterly meal fee: EUR 400", "quarter"),
        ("Meal fee: EUR 400 per quarter", "quarter"),
    ],
)
def test_price_period_distinguishes_quarterly_fees_from_payments(line, expected_period):
    assert extractor_module.helpers._detect_price_period(line) == expected_period


@pytest.mark.parametrize(
    ("line", "expected_period"),
    [
        ("Еднократна регистрационна такса: 450 EUR", "one_time"),
        ("Регистрационната такса се заплаща еднократно", "one_time"),
        ("Годишната такса се заплаща еднократно", "yearly"),
        ("Таксата се заплаща еднократно в началото на всяка учебна година", None),
        ("Таксата се заплаща еднократно или на две вноски", None),
    ],
)
def test_bulgarian_one_time_fee_wording_distinguishes_fee_from_payment_plan(
    line, expected_period
):
    assert extractor_module.helpers._detect_explicit_price_period(line) == expected_period


def test_price_period_on_following_line_applies_to_preceding_amount():
    text = "Tuition fee: EUR 8,000\nper year\nRegistration fee: EUR 200"

    parsed = extractor_module.helpers._extract_prices_deterministic(text)
    signals = extractor_module.helpers._iter_price_line_signals(text)
    supported = extractor_module.helpers._filter_supported_prices(
        [ExtractedPrice(category="tuition", amount=8000, currency="EUR", period="yearly", confidence=0.9)],
        text,
    )

    assert [(price.amount, price.period) for price in parsed.prices] == [
        (8000, "yearly"),
        (200, None),
    ]
    assert [(signal["amount"], signal["period"]) for signal in signals] == [
        (8000, "yearly"),
        (200, None),
    ]
    assert len(supported) == 1
    assert supported[0].period == "yearly"


@pytest.mark.parametrize("line", ["Quarterly instalment: EUR 2,000", "Quarterly payment: EUR 2,000"])
def test_quarterly_payment_does_not_inherit_fee_period(line):
    assert extractor_module.helpers._detect_price_period(line, default_period="yearly") is None


def test_annual_heading_survives_payment_cadence_on_amount_line():
    text = "Annual tuition fee\nEUR 8,000 payable quarterly"

    parsed = extractor_module.helpers._extract_prices_deterministic(text)
    signals = extractor_module.helpers._iter_price_line_signals(text)

    assert [(price.amount, price.period) for price in parsed.prices] == [(8000, "yearly")]
    assert [(signal["amount"], signal["period"]) for signal in signals] == [
        (8000, "yearly")
    ]


@pytest.mark.parametrize(
    "heading",
    [
        "Месечнополовин ден",
        "Месечноцелодневно гледане",
        "Месечно половин ден",
        "Месечно целодневно гледане",
        "Такса за месец",
        "Такса обучение на месец",
        "Такса за обучение на месец",
    ],
)
def test_explicit_bulgarian_monthly_headings_set_period(heading):
    text = f"## Такси\n## {heading}\nEUR 409"

    parsed = extractor_module.helpers._extract_prices_deterministic(text)
    signals = extractor_module.helpers._iter_price_line_signals(text)

    assert [(price.amount, price.period) for price in parsed.prices] == [(409, "monthly")]
    assert [(signal["amount"], signal["period"]) for signal in signals] == [
        (409, "monthly")
    ]


@pytest.mark.parametrize(
    "line",
    [
        "Таксите се заплащат месец за месец.",
        "Таксата се заплаща на месец",
        "Таксите се внасят на месец",
    ],
)
def test_bulgarian_payment_cadence_does_not_set_monthly_period(line):
    assert extractor_module.helpers._detect_explicit_price_period(line) is None


def test_price_period_does_not_carry_into_next_tuition_section():
    text = """
    Annual tuition fee
    EUR 8,000
    Tuition fees for international students
    EUR 9,000
    """

    parsed = extractor_module.helpers._extract_prices_deterministic(text)
    signals = extractor_module.helpers._iter_price_line_signals(text)

    assert {price.amount: price.period for price in parsed.prices} == {
        8000: "yearly",
        9000: None,
    }
    assert {signal["amount"]: signal["period"] for signal in signals} == {
        8000: "yearly",
        9000: None,
    }


def test_unrecognised_markdown_heading_ends_price_section():
    text = """
    Annual tuition fee
    EUR 8,000
    # Other charges
    EUR 200
    """

    parsed = extractor_module.helpers._extract_prices_deterministic(text)
    signals = extractor_module.helpers._iter_price_line_signals(text)

    assert [(price.category, price.amount, price.period) for price in parsed.prices] == [
        ("tuition", 8000, "yearly"),
    ]
    assert [(signal["category"], signal["amount"], signal["period"]) for signal in signals] == [
        ("tuition", 8000, "yearly"),
    ]


def test_normalized_price_fields_preserves_unstated_period():
    fields = extractor_module._normalized_price_fields(
        ExtractedPrice(
            category="tuition",
            amount=8000,
            currency="EUR",
            period=None,
            confidence=0.9,
        )
    )

    assert fields is not None
    assert fields["period"] is None


def test_normalized_price_fields_still_rejects_unknown_stated_period():
    fields = extractor_module._normalized_price_fields(
        ExtractedPrice(
            category="tuition",
            amount=8000,
            currency="EUR",
            period="weekly",
            confidence=0.9,
        )
    )

    assert fields is None


def test_dedupe_price_rows_drops_currency_and_installment_duplicates():
    prices = [
        ExtractedPrice(
            category="food", amount=7880, currency="EUR", period="yearly",
            academic_year="2026-2027",
            installments=["8100 € – 2 вноски", "8250 € – 3 вноски"],
            confidence=0.9,
        ),
        ExtractedPrice(category="food", amount=8100, currency="EUR", period="yearly", academic_year="2026-2027", confidence=0.9),
        ExtractedPrice(category="food", amount=8250, currency="EUR", period="yearly", academic_year="2026-2027", confidence=0.9),
        ExtractedPrice(category="food", amount=15411.94, currency="BGN", period="yearly", academic_year="2026-2027", confidence=0.9),
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(prices)
    by_amount = {p.amount: p for p in deduped}

    assert 7880 in by_amount
    assert 8100 not in by_amount
    assert 8250 not in by_amount
    assert 15411.94 not in by_amount


def test_dedupe_price_rows_drops_installment_plan_name_variants():
    # Pythagoras pattern: per-grade-band, three rows with the same age_group —
    # one full-pay row (plan_name=None) plus "2 installments" and "10 installments"
    # variants that hold per-installment amounts rather than annual totals.
    prices = [
        ExtractedPrice(
            category="tuition", amount=7580, currency="EUR", period="yearly",
            age_group="ПГ - 4 .клас", academic_year="2026-2027",
            confidence=0.9,
        ),
        ExtractedPrice(
            category="tuition", amount=3975, currency="EUR", period="yearly",
            age_group="ПГ - 4 .клас", academic_year="2026-2027",
            plan_name="2 installments",
            confidence=0.9,
        ),
        ExtractedPrice(
            category="tuition", amount=827, currency="EUR", period="yearly",
            age_group="ПГ - 4 .клас", academic_year="2026-2027",
            plan_name="10 installments",
            confidence=0.9,
        ),
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(prices)
    amounts = sorted(p.amount for p in deduped)

    assert amounts == [7580]


def test_dedupe_price_rows_preserves_a_sole_total_fee():
    price = ExtractedPrice(
        category="tuition", amount=12000, currency="EUR", period="yearly",
        plan_name="Total Fee", confidence=0.9,
    )

    deduped = extractor_module.helpers._dedupe_price_rows([price])

    assert [(row.amount, row.plan_name) for row in deduped] == [(12000, "Total Fee")]


def test_dedupe_price_rows_drops_total_fee_when_component_sibling_exists():
    prices = [
        ExtractedPrice(
            category="tuition", amount=10000, currency="EUR", period="yearly",
            plan_name="Tuition Fee", confidence=0.9,
        ),
        ExtractedPrice(
            category="tuition", amount=12000, currency="EUR", period="yearly",
            plan_name="Total Fee", confidence=0.9,
        ),
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(prices)

    assert [(row.amount, row.plan_name) for row in deduped] == [(10000, "Tuition Fee")]


def test_filter_supported_prices_drops_unsupported_llm_rows():
    text = """
    # Book a visit
    Enrollment visits are planned in the Spring.
    Ad-hoc visits are allowed if we have confirmed that a free space is available.
    Contact Us
    """
    prices = [
        ExtractedPrice(category="tuition", amount=750, currency="EUR", period="monthly", confidence=0.9),
        ExtractedPrice(category="tuition", amount=400, currency="EUR", period="monthly", confidence=0.9),
    ]

    refined = extractor_module.helpers._filter_supported_prices(prices, text)

    assert refined == []


def test_filter_supported_prices_uses_source_line_to_fix_period_and_category():
    text = """
    ## ТАКСИ
    € 530 | 1037 лв. - целодневно гледане, ежемесечно заплащане
    € 350 | 685 лв. - половин ден с включен обяд
    € 265 | 518 лв. – депозит за запазване на място
    """
    prices = [
        ExtractedPrice(category="tuition", amount=530, currency="EUR", period="yearly", confidence=0.9),
        ExtractedPrice(category="tuition", amount=350, currency="EUR", period="yearly", confidence=0.9),
        ExtractedPrice(category="tuition", amount=265, currency="EUR", period="yearly", confidence=0.9),
    ]

    refined = extractor_module.helpers._filter_supported_prices(prices, text)
    by_amount = {price.amount: price for price in refined}

    assert by_amount[530].period is None
    assert by_amount[350].period is None
    assert by_amount[265].category == "registration"
    assert by_amount[265].period is None


def test_filter_supported_prices_clears_llm_period_without_source_evidence():
    text = "Tuition fee for academic year 2026/2027: EUR 8,000"
    guessed = ExtractedPrice(
        category="tuition",
        amount=8000,
        currency="EUR",
        period="yearly",
        academic_year="2026/2027",
        confidence=0.9,
    )

    refined = extractor_module.helpers._filter_supported_prices([guessed], text)

    assert len(refined) == 1
    assert refined[0].period is None


def test_filter_supported_prices_uses_plan_context_when_amount_repeats():
    text = """
    ## Meals
    Annual meal plan | EUR 2,900
    ## Transport
    Full School Bus Service Fee | EUR 2,900
    """
    prices = [
        ExtractedPrice(
            category="tuition",
            amount=2900,
            currency="EUR",
            period="yearly",
            plan_name="Full School Bus Service Fee",
            confidence=0.9,
        )
    ]

    refined = extractor_module.helpers._filter_supported_prices(prices, text)

    assert len(refined) == 1
    assert refined[0].category == "transport"


def test_filter_supported_prices_uses_category_when_amount_repeats():
    text = """
    ## Tuition
    Monthly fee EUR 500
    ## Transport
    Monthly fee EUR 500
    """
    prices = [
        ExtractedPrice(
            category=category, amount=500, currency="EUR", period="monthly", confidence=0.9,
        )
        for category in ("tuition", "transport")
    ]

    refined = extractor_module.helpers._filter_supported_prices(prices, text)

    assert {price.category for price in refined} == {"tuition", "transport"}


def test_filter_supported_prices_clears_guess_when_category_resolves_repeated_amount():
    text = "Tuition fee EUR 500\nTransport fee EUR 500"
    price = ExtractedPrice(
        category="tuition", amount=500, currency="EUR", period="yearly", confidence=0.9,
    )

    refined = extractor_module.helpers._filter_supported_prices([price], text)

    assert len(refined) == 1
    assert refined[0].category == "tuition"
    assert refined[0].period is None


def test_filter_supported_prices_uses_heading_to_fix_high_confidence_category():
    text = """
    ## Transport
    EUR 160
    """
    price = ExtractedPrice(
        category="tuition", amount=160, currency="EUR", period="monthly", confidence=0.9,
    )

    refined = extractor_module.helpers._filter_supported_prices([price], text)

    assert len(refined) == 1
    assert refined[0].category == "transport"


def test_filter_supported_prices_keeps_tuition_when_transport_is_included():
    text = "Tuition fee including transport EUR 10,000"
    price = ExtractedPrice(
        category="tuition", amount=10000, currency="EUR", period="yearly", confidence=0.9,
    )

    refined = extractor_module.helpers._filter_supported_prices([price], text)

    assert len(refined) == 1
    assert refined[0].category == "tuition"


def test_filter_supported_prices_prefers_food_over_generic_monthly_fee():
    text = "Месечна такса за храна – 100 лв"
    price = ExtractedPrice(
        category="food", amount=100, currency="BGN", period="monthly", confidence=0.9,
    )

    refined = extractor_module.helpers._filter_supported_prices([price], text)

    assert len(refined) == 1
    assert refined[0].category == "food"


def test_dedupe_price_rows_keeps_only_latest_academic_year():
    prices = [
        ExtractedPrice(
            category="tuition", amount=9000, currency="EUR", period="yearly",
            academic_year="2024/2025", confidence=0.9,
        ),
        ExtractedPrice(
            category="tuition", amount=11000, currency="EUR", period="yearly",
            academic_year="2026/2027", confidence=0.9,
        ),
        ExtractedPrice(
            category="registration", amount=500, currency="EUR", period="one_time",
            academic_year="2026/2027", confidence=0.9,
        ),
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(prices)

    assert {(row.amount, row.academic_year) for row in deduped} == {
        (11000, "2026/2027"),
        (500, "2026/2027"),
    }


def test_dedupe_price_rows_drops_yearless_rows_when_explicit_year_exists():
    prices = [
        ExtractedPrice(
            category="tuition", amount=6000, currency="EUR", period="yearly",
            academic_year=None, confidence=0.9,
        ),
        ExtractedPrice(
            category="tuition", amount=6000, currency="EUR", period="yearly",
            academic_year="2026/2027", confidence=0.9,
        ),
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(prices)

    assert [(row.amount, row.academic_year) for row in deduped] == [(6000, "2026/2027")]


def test_dedupe_price_rows_preserves_distinct_yearless_fee_in_same_category():
    prices = [
        ExtractedPrice(
            category="registration", amount=500, currency="EUR", period="one_time",
            confidence=0.9,
        ),
        ExtractedPrice(
            category="registration", amount=1000, currency="EUR", period="one_time",
            academic_year="2026/2027", confidence=0.9,
        ),
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(prices)

    assert {(row.amount, row.academic_year) for row in deduped} == {
        (500, None),
        (1000, "2026/2027"),
    }


def test_dedupe_price_rows_preserves_yearless_fee_in_other_category():
    prices = [
        ExtractedPrice(
            category="tuition", amount=6000, currency="EUR", period="yearly",
            academic_year="2026/2027", confidence=0.9,
        ),
        ExtractedPrice(
            category="registration", amount=500, currency="EUR", period="one_time",
            confidence=0.9,
        ),
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(prices)

    assert {(row.category, row.amount) for row in deduped} == {
        ("tuition", 6000),
        ("registration", 500),
    }


def test_dedupe_price_rows_keeps_latest_year_per_comparable_fee_group():
    prices = [
        ExtractedPrice(
            category="tuition", amount=6000, currency="EUR", period="yearly",
            academic_year="2025/2026", confidence=0.9,
        ),
        ExtractedPrice(
            category="registration", amount=500, currency="EUR", period="one_time",
            academic_year="2026/2027", confidence=0.9,
        ),
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(prices)

    assert {(row.category, row.amount) for row in deduped} == {
        ("tuition", 6000),
        ("registration", 500),
    }


def test_price_signals_reset_semantic_state_between_source_pages():
    text = """
    --- SOURCE: https://school.test/meals ---
    Food fees 2024/2025
    EUR 2,900
    --- SOURCE: https://school.test/bus ---
    EUR 1,885
    """

    signals = extractor_module.helpers._iter_price_line_signals(text)

    assert [signal for signal in signals if signal["amount"] == 1885] == []


def test_filter_supported_prices_uses_academic_year_to_resolve_repeated_amount():
    text = """
    --- SOURCE: https://school.test/fees-2025 ---
    Tuition fees 2025/2026
    Annual tuition EUR 5,000
    --- SOURCE: https://school.test/fees-2026 ---
    Tuition fees 2026/2027
    Annual tuition EUR 5,000
    """
    price = ExtractedPrice(
        category="tuition", amount=5000, currency="EUR", period="yearly",
        academic_year="2026-2027", confidence=0.9,
    )

    refined = extractor_module.helpers._filter_supported_prices([price], text)

    assert len(refined) == 1
    assert refined[0].academic_year == "2026-2027"


def test_filter_supported_prices_scopes_staleness_to_supporting_page():
    text = """
    --- SOURCE: https://school.test/old-fees ---
    Monthly tuition EUR 500
    Payment deadline: September 2023
    --- SOURCE: https://school.test/current-fees ---
    Current monthly tuition EUR 500
    """
    price = ExtractedPrice(
        category="tuition", amount=500, currency="EUR", period="monthly", confidence=0.9,
    )

    refined = extractor_module.helpers._filter_supported_prices([price], text)

    assert len(refined) == 1
    assert refined[0].amount == 500


def test_filter_supported_prices_ignores_unrelated_old_year_on_current_page():
    text = """
    --- SOURCE: https://school.test/current-fees ---
    Archived admissions results for 2023/2024
    ## Current tuition
    Monthly tuition EUR 500
    """
    price = ExtractedPrice(
        category="tuition", amount=500, currency="EUR", period="monthly", confidence=0.9,
    )

    refined = extractor_module.helpers._filter_supported_prices([price], text)

    assert len(refined) == 1
    assert refined[0].amount == 500


@pytest.mark.parametrize(
    ("text", "price"),
    [
        (
            "Annual meal plan | EUR 2,900\nFull School Bus Service Fee | EUR 2,900",
            ExtractedPrice(category="tuition", amount=2900, currency="EUR", period="yearly", confidence=0.9),
        ),
        (
            "English as an Additional Language (EAL) support fee | EUR 1,500",
            ExtractedPrice(
                category="tuition", amount=1500, currency="EUR", period="yearly",
                plan_name="EAL support fee", confidence=0.9,
            ),
        ),
        (
            "Tuition Fee EUR 10,000\nCapital Fee EUR 2,000\nTotal Fee EUR 12,000",
            ExtractedPrice(
                category="tuition", amount=12000, currency="EUR", period="yearly",
                plan_name="Total Fee", confidence=0.9,
            ),
        ),
    ],
)
def test_filter_supported_prices_withholds_ambiguous_or_composite_rows(text, price):
    assert extractor_module.helpers._filter_supported_prices([price], text) == []


def test_filter_supported_prices_withholds_stale_yearless_fee_table():
    text = """
    ## Tuition fees
    Annual tuition EUR 8,000
    Payment deadline: 15 September 2023
    Second payment deadline: 15 January 2024
    """
    assert extractor_module.helpers._yearless_pricing_text_is_stale(text, current_year=2026) is True


def test_filter_supported_prices_supports_bulgarian_euro_fee_table():
    text = """
    Месечна такса – 580 евро
    Допълнителни занимания
    Модерни танци – 25 евро/месец
    Транспорт - 160 евро/месец
    """
    prices = [
        ExtractedPrice(category="tuition", amount=580, currency="EUR", period="monthly", confidence=0.9),
        ExtractedPrice(category="extracurricular", amount=25, currency="EUR", period="monthly", confidence=0.9),
        ExtractedPrice(category="transport", amount=160, currency="EUR", period="monthly", confidence=0.9),
    ]

    refined = extractor_module.helpers._filter_supported_prices(prices, text)

    assert [(row.category, row.amount, row.period) for row in refined] == [
        ("tuition", 580, "monthly"),
        ("extracurricular", 25, "monthly"),
        ("transport", 160, "monthly"),
    ]


def test_supported_prices_keep_only_current_fee_structure_across_categories():
    text = """
    Fee Structure 2024/2025 & 2025/2026
    Food (fruit, lunch, PM snack)
    270 BGN
    Camps
    Judo 60 BGN
    2026/2027
    Annual Tuition Paid in Full
    7,820 EUR
    Monthly paid tuition for families in the region
    680 EUR
    Transportation with school buses from and to Sofia
    250 EUR
    Volleyball Club
    20 EUR
    Second Foreign Language Books
    80 EUR
    """

    parsed = extractor_module.helpers._extract_prices_deterministic(text)
    supported = extractor_module._supported_price_rows(parsed.prices, text)

    assert {(row.amount, row.academic_year) for row in supported} == {
        (7820.0, "2026/2027"),
        (680.0, "2026/2027"),
        (250.0, "2026/2027"),
        (20.0, "2026/2027"),
        (80.0, "2026/2027"),
    }
    by_amount = {row.amount: row for row in supported}
    assert by_amount[680].period is None
    assert by_amount[250].period is None
    assert by_amount[20].period is None
    assert by_amount[80].category == "materials"


@pytest.mark.parametrize(
    ("text", "price"),
    [
        (
            "2026/2027\nTransportation with school buses from and to Sofia\n250 EUR",
            ExtractedPrice(
                category="transport", amount=250, currency="EUR", period="yearly",
                academic_year="2026/2027", confidence=0.9,
            ),
        ),
        (
            "2026/2027\nVolleyball Club\n20 EUR",
            ExtractedPrice(
                category="extracurricular", amount=20, currency="EUR", period="yearly",
                academic_year="2026/2027", confidence=0.9,
            ),
        ),
    ],
)
def test_filter_supported_prices_preserves_unlabeled_service_with_null_period(text, price):
    refined = extractor_module.helpers._filter_supported_prices([price], text)

    assert len(refined) == 1
    assert refined[0].period is None


def test_deterministic_pricing_preserves_bare_transport_with_unstated_period():
    parsed = extractor_module.helpers._extract_prices_deterministic(
        """
        TUITION FEES FOR THE ACADEMIC 2026/2027 YEAR
        Grade 1 tuition fee: 8,000 EUR
        Transportation with school buses from and to Sofia 250 EUR
        """
    )

    assert [(row.category, row.amount, row.period) for row in parsed.prices] == [
        ("tuition", 8000.0, None),
        ("transport", 250.0, None),
    ]


def test_filter_supported_prices_withholds_late_payment_penalties():
    text = "There will be a late fee charge of EUR 250 for payments made after July 1."
    price = ExtractedPrice(
        category="registration", amount=250, currency="EUR", period="one_time",
        confidence=0.9,
    )

    assert extractor_module.helpers._filter_supported_prices([price], text) == []


def test_filter_supported_prices_withholds_yearless_table_with_stale_due_dates():
    text = """
    Учебна такса 5700 eur
    Първа вноска 2850 eur
    Следваща вноска 2850 eur (до 31.01.2024 г.)
    Разсрочено плащане 5950 eur от 01.09.2023 г.
    """
    prices = [
        ExtractedPrice(
            category="tuition", amount=5700, currency="EUR", period="yearly",
            confidence=0.9,
        ),
        ExtractedPrice(
            category="tuition", amount=5950, currency="EUR", period="yearly",
            confidence=0.9,
        ),
    ]

    assert extractor_module.helpers._filter_supported_prices(prices, text) == []


def test_dedupe_price_rows_drops_exact_indistinguishable_duplicates():
    rows = [
        ExtractedPrice(
            category="registration", amount=250, currency="EUR", period="one_time",
            academic_year="2026/2027", plan_name="Application fee", confidence=0.9,
        )
        for _ in range(2)
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(rows)

    assert len(deduped) == 1


def test_price_signals_do_not_treat_inclusion_text_as_section_heading():
    text = """
    Такса "Образователни услуги"
    В таксата не е включена храна
    Предучилищен клас
    8100 €
    Месечна такса – 510 евро
    Храната се заплаща допълнително
    Модерни танци
    25 евро/месец
    """
    prices = [
        ExtractedPrice(category="tuition", amount=8100, currency="EUR", period="yearly", confidence=0.9),
        ExtractedPrice(category="extracurricular", amount=25, currency="EUR", period="monthly", confidence=0.9),
    ]

    refined = extractor_module.helpers._filter_supported_prices(prices, text)

    assert [(row.category, row.amount) for row in refined] == [
        ("tuition", 8100),
        ("extracurricular", 25),
    ]


@pytest.mark.parametrize("amount, period", [(400, "monthly"), (750, None)])
def test_find_supporting_price_source_url_returns_page_with_matching_amount(amount, period):
    school = School(
        name_i18n={"bg": "Тест"},
        website_url="https://example-school.bg",
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
    )
    pages = [
        SourcePage(
            source_url="https://example-school.bg/enroll/book-a-visit",
            page_category="admission",
            raw_markdown="Book a visit and contact us for details.",
        ),
        SourcePage(
            source_url="https://example-school.bg/enroll",
            page_category="admission",
            raw_markdown="## Tuition Costs\nHalf-day Program\n400 €\n/per month\nFull-day Program\n750 €",
        ),
    ]
    price = ExtractedPrice(
        category="tuition", amount=amount, currency="EUR", period=period, confidence=0.9
    )

    source_url = extractor_module.helpers._find_supporting_price_source_url(school, pages, price)

    assert source_url == "https://example-school.bg/enroll"


def test_find_supporting_price_source_url_uses_semantics_when_amount_repeats():
    school = School(
        name_i18n={"bg": "Тест"}, website_url="https://school.test",
        country_code="bg", city="sofia", school_type="private", education_level="primary",
    )
    pages = [
        SourcePage(
            source_url="https://school.test/meals", page_category="pricing",
            raw_markdown="Annual meal plan | EUR 2,900",
        ),
        SourcePage(
            source_url="https://school.test/bus", page_category="pricing",
            raw_markdown="School Bus Fees 2026/2027\nFull School Bus Service Fee | EUR 2,900",
        ),
    ]
    price = ExtractedPrice(
        category="transport", amount=2900, currency="EUR", period=None,
        plan_name="Full School Bus Service Fee", confidence=0.9,
    )

    source_url = extractor_module.helpers._find_supporting_price_source_url(school, pages, price)

    assert source_url == "https://school.test/bus"


def test_extract_prices_deterministic_prefers_yearly_when_mixed_fee_table_line_mentions_monthly_installments():
    parsed = extractor_module.helpers._extract_prices_deterministic(
        """
        #### 02
        #### Годишна такса „Обучение“
        Размерът на годишната такса „Обучение“ е различен в зависимост от избрания начин на плащане – еднократно, на две равни вноски или на равни ежемесечни вноски.
        Клас | Продължителност на обучението | Плащане на пълна такса | Плащане на две вноски | Месечно заплащане*
        1. - 4. клас | 01.09. - 30.06. | 7,950€ | 2×4,094€ | 10×843€
        """
    )

    assert parsed.has_pricing_info is True
    assert parsed.prices[0].amount == 7950
    assert parsed.prices[0].period == "yearly"


@pytest.mark.asyncio
async def test_extract_school_uses_deterministic_pricing_fallback_when_llm_reports_no_pricing(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction
    pricing_page = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id, SourcePage.page_category == "pricing"))
    ).scalar_one()
    pricing_page.source_url = "https://test-school.bg/priem/grafik-i-tseni"
    pricing_page.raw_markdown = """
        # График и цени
        ## Такси „Обучение“ за Предучилищна – учебна 2025-2026 година
        ### Стандартна такса:
        За 1 вноска: €6650
        На 2 вноски: 2 вноски по €3475
        ## Такси „Обучение“ за 1, 2, 3, 4, 5, 6 и 7 клас – учебна 2025-2026 година
        ### Стандартна такса:
        За 1 вноска: €7150
        ## Учебници и всички необходими консумативи – €545
    """
    await db_session.commit()

    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        programs=["STEM"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 10, 2, 0.0), (mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert result["pricing_count"] == 3
    assert any("deterministic fallback" in detail for detail in result["details"])

    pricing_rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id).order_by(Pricing.id))
    ).scalars().all()
    assert [
        (
            row.category.value,
            float(row.amount),
            row.currency,
            row.period.value if row.period else None,
        )
        for row in pricing_rows
    ] == [
        ("tuition", 6650.0, "EUR", None),
        ("tuition", 7150.0, "EUR", None),
        ("materials", 545.0, "EUR", None),
    ]


@pytest.mark.asyncio
async def test_extract_school_discards_unsupported_llm_pricing_rows(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    db_session.add(
        Pricing(
            school_id=school.id,
            category="tuition",
            amount=999,
            currency="EUR",
            period="monthly",
            source=PriceSource.SCRAPED_WEBSITE,
            source_url="https://test-school.bg/old-prices",
            pricing_context={"confidence": 0.9},
        )
    )
    pricing_page = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id, SourcePage.page_category == "pricing"))
    ).scalar_one()
    pricing_page.raw_markdown = "Monthly tuition EUR 750"
    await db_session.commit()

    mock_price = PriceExtractionOutput(
        prices=[
            ExtractedPrice(
                category="tuition",
                amount=750,
                currency="EUR",
                period="monthly",
                confidence=0.9,
            )
        ],
        has_pricing_info=True,
    )
    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        programs=["STEM"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(
            side_effect=[
                (mock_price, 10, 2, 0.0),
                (mock_general, 20, 4, 0.001),
                (None, 0, 0, 0.0),
                (None, 0, 0, 0.0),
            ]
        ),
    ), patch.object(
        extractor_module.helpers,
        "_find_supporting_price_source_url",
        return_value=None,
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert result["pricing_count"] == 0
    assert any("kept existing pricing as history" in detail for detail in result["details"])
    pricing_rows = (await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))).scalars().all()
    # The unsupported row is not written, and the prior row survives as history.
    assert [float(row.amount) for row in pricing_rows] == [999.0]


@pytest.mark.asyncio
async def test_successful_no_price_refresh_preserves_historical_scraped_rows(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction
    db_session.add_all(
        [
            Pricing(
                school_id=school.id,
                category="extracurricular",
                amount=25,
                currency="EUR",
                period="monthly",
                source=PriceSource.SCRAPED_WEBSITE,
                source_url="https://test-school.bg/old-prices",
                pricing_context={"confidence": 0.9},
            ),
            Pricing(
                school_id=school.id,
                category="tuition",
                amount=1000,
                currency="EUR",
                period="yearly",
                source=PriceSource.OFFICIAL,
                source_url="https://official.test/fees",
                pricing_context={"confidence": 1.0},
            ),
        ]
    )
    await db_session.commit()

    no_prices = PriceExtractionOutput(prices=[], has_pricing_info=False)
    pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id))
    ).scalars().all()
    next(page for page in pages if page.page_category == "pricing").raw_markdown = (
        "Contact the school for current pricing."
    )
    await db_session.flush()
    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(return_value=(no_prices, 10, 2, 0.001)),
    ):
        result = await extractor_module._extract_prices(
            db_session,
            school,
            list(pages),
            20.0,
            extractor_module.ExtractionLLMStats(),
        )

    rows = (
        await db_session.execute(
            select(Pricing).where(Pricing.school_id == school.id).order_by(Pricing.id)
        )
    ).scalars().all()
    assert result["success"] is True
    assert result["count"] == 0
    assert "kept existing pricing as history" in result["detail"]
    # Finding no pricing supersedes no year, so both rows survive.
    assert [(row.source, float(row.amount)) for row in rows] == [
        (PriceSource.SCRAPED_WEBSITE, 25.0),
        (PriceSource.OFFICIAL, 1000.0),
    ]


def test_prepare_summary_source_page_text_drops_navigation_and_keeps_narrative_lines():
    prepared = extractor_module.helpers._prepare_summary_source_page_text(
        """
        * [Начало](https://example-school.bg)
        * [Контакти](https://example-school.bg/contact)
        ### Образователен модел
        Fusion educational model with project-based learning and creative practice.
        Technical storage or access for cookies.
        """
    )

    assert "Fusion educational model" in prepared
    assert "Начало" not in prepared
    assert "Technical storage or access" not in prepared


def test_normalize_display_name_i18n_duplicates_single_brand_name_for_bg_and_en():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"en": "Fusion School"},
        "bg",
    )

    assert normalized == {
        "bg": "Fusion School",
        "en": "Fusion School",
    }


def test_normalize_display_name_i18n_keeps_cyrillic_single_value_in_bg_only():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "ЧОУ ПЕТЪР БЕРОН"},
        "bg",
    )

    assert normalized == {
        "bg": "ЧОУ ПЕТЪР БЕРОН",
    }


def test_normalize_display_name_i18n_drops_duplicated_cyrillic_en_value():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "Българско школо", "en": "Българско школо"},
        "bg",
    )

    assert normalized == {
        "bg": "Българско школо",
    }


def test_normalize_display_name_i18n_drops_transliterated_en_value():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "Българско школо", "en": "BALGARSKO SHKOLO"},
        "bg",
    )

    assert normalized == {
        "bg": "Българско школо",
    }


def test_normalize_display_name_i18n_simplifies_generic_bg_label_to_brand_core():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": 'Частно начално училище "БИЗИ"'},
        "bg",
    )

    assert normalized == {
        "bg": "БИЗИ",
    }


def test_normalize_display_name_i18n_simplifies_quoted_brand_but_keeps_location_only_case():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": 'Учебен комплекс “Българско школо”'},
        "bg",
    )
    location_only = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": 'Частна Детска Градина “София”'},
        "bg",
    )

    assert normalized == {
        "bg": "Българско школо",
    }
    assert location_only == {
        "bg": 'Частна Детска Градина “София”',
    }


def test_extract_display_name_i18n_deterministic_accepts_matching_brand_signal():
    text = '[ЧДГ Доверие](https://doverie-bg.net "ЧДГ Доверие")'

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='ЧАСТНА ДЕТСКА ГРАДИНА "ДОВЕРИЕ" ЕООД',
        country_code="bg",
    )

    assert extracted == {
        "bg": "ЧДГ Доверие",
        "en": "ЧДГ Доверие",
    }


def test_extract_display_name_i18n_deterministic_rejects_wrong_site_name():
    text = '![ЧОУ “Азбуки” | София](https://azbuki-school.bg/wp-content/uploads/logo.svg)'

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='ЧАСТНА ДЕТСКА ГРАДИНА МАЛКИ СТЪПКИ - ЛИТЪЛ СТЕПС',
        country_code="bg",
    )

    assert extracted is None


def test_extract_display_name_i18n_deterministic_keeps_explicit_bg_and_en_variants():
    text = (
        '[![Fusion School](https://school.fusion.bg/logo.svg)](https://school.fusion.bg "Fusion School") '
        '[ЧОУ „Фюжън“](https://school.fusion.bg)'
    )

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='ЧАСТНО ОСНОВНО УЧИЛИЩЕ ФЮЖЪН ЕООД',
        country_code="bg",
    )

    assert extracted == {
        "bg": 'ЧОУ „Фюжън“',
        "en": "Fusion School",
    }


def test_extract_display_name_i18n_deterministic_accepts_cross_script_house_brand():
    text = (
        '[ ![Montessori House](https://www.montessori-bulgaria.com/logo.svg) **Montessori House** ]'
        '(https://www.montessori-bulgaria.com/en "Montessori House")'
    )

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='Частна детска градина "Детска къща Монтесори" ООД',
        country_code="bg",
    )

    assert extracted == {
        "bg": "Montessori House",
        "en": "Montessori House",
    }


def test_extract_display_name_i18n_deterministic_strips_section_prefixes():
    text = '[Защо BRITANICA Park School](https://britanica-parkschool.bg/)'

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='ЧАСТНА ДЕТСКА ГРАДИНА БРИТАНИКА ООД',
        country_code="bg",
    )

    assert extracted == {
        "bg": "BRITANICA Park School",
        "en": "BRITANICA Park School",
    }


def test_extract_display_name_i18n_deterministic_strips_about_prefix_when_name_matches():
    text = '[За Веда](https://wedaschule.com/)'

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='ЧАСТНА ДЕТСКА ГРАДИНА ВЕДА ООД',
        country_code="bg",
    )

    assert extracted == {
        "bg": "Веда",
        "en": "Веда",
    }


def test_extract_display_name_i18n_deterministic_rejects_blog_title_noise():
    text = '[Блогът на Увекинд](https://uwekind.com/blog)'

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='ЧАСТНО НАЧАЛНО УЧИЛИЩЕ ЛОЗЕН ЕООД',
        country_code="bg",
    )

    assert extracted is None


def test_extract_display_name_i18n_deterministic_rejects_school_news_host_title():
    text = "# 21-во училище стана домакин"

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"',
        country_code="bg",
        website_url="https://21su.bg",
    )

    assert extracted is None


def test_extract_display_name_i18n_deterministic_rejects_reference_school_noise():
    text = '[Google Reference School](https://espa.bg)'

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='Частно средно училище ЕСПА ЕООД',
        country_code="bg",
    )

    assert extracted is None


def test_extract_display_name_i18n_deterministic_strips_markdown_emphasis():
    text = '[**ЧДГ „Приказка без край“**](https://novigradini.com/pbk/)'

    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text,
        registry_name='ЧАСТНА ДЕТСКА ГРАДИНА ПРИКАЗКА БЕЗ КРАЙ 1',
        country_code="bg",
    )

    assert extracted == {
        "bg": 'ЧДГ „Приказка без край“',
        "en": 'ЧДГ „Приказка без край“',
    }


def test_extract_display_name_i18n_deterministic_ignores_city_only_overlap():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        '[УЧИЛИЩА В СОФИЯ](https://luiskarol.com/)',
        registry_name='"Частно начално училище Луис Карол - гр. София" ЕООД',
        country_code="bg",
    )

    assert extracted is None


def test_extract_display_name_i18n_deterministic_falls_back_to_host_aligned_registry_brand():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        '[УЧИЛИЩА В СОФИЯ](https://luiskarol.com/)',
        registry_name='"Частно начално училище Луис Карол - гр. София" ЕООД',
        country_code="bg",
        website_url="https://luiskarol.com/",
    )

    assert extracted == {
        "bg": "Луис Карол",
        "en": "Luis Karol",
    }


def test_extract_display_name_i18n_deterministic_backfills_en_from_host_when_bg_found():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        '[Луис Карол](https://luiskarol.com/)',
        registry_name='"Частно начално училище Луис Карол - гр. София" ЕООД',
        country_code="bg",
        website_url="https://luiskarol.com/",
    )

    assert extracted == {
        "bg": "Луис Карол",
        "en": "Luis Karol",
    }


def test_merge_display_name_i18n_backfills_missing_locale_from_deterministic():
    merged = extractor_module.helpers._merge_display_name_i18n(
        {"bg": "Луис Карол"},
        {"bg": "Луис Карол", "en": "Luis Karol"},
        "bg",
    )

    assert merged == {
        "bg": "Луис Карол",
    }


def test_normalize_display_name_i18n_rejects_low_quality_headings():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"en": "Our kindergartens", "bg": "Стратегически план на Американския колеж 2027"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_email_contact_labels():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {
            "bg": "Email: school.p.beron@gmail.com",
            "en": "school.p.beron@gmail.com",
        },
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_report_titles():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "Тримесечен отчет на 145. ОУ Симеон Радев за м.Декември"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_news_host_titles():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "21-во училище стана домакин"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_generic_numbered_school_labels():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "131. средно училище"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_generic_numbered_school_abbreviations():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "21. СУ", "en": "21 Secondary School"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_extracts_core_from_teacher_page_heading():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": 'Преподаватели в 123 СУ "Стефан Стамболов"'},
        "bg",
    )

    assert normalized == {"bg": "Стефан Стамболов"}


def test_normalize_display_name_i18n_strips_logo_prefix_and_early_language_prefix():
    logo_name = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "лого Веселата къща"},
        "bg",
    )
    espa_name = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "с ранно чуждоезиково обучение ЕСПА"},
        "bg",
    )

    assert logo_name == {"bg": "Веселата къща"}
    assert espa_name == {"bg": "ЕСПА"}


def test_normalize_display_name_i18n_strips_transliterated_generic_en_prefixes():
    german_name = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "Ерих Кестнер", "en": "nemska gimnaziya Erih Kestner"},
        "bg",
    )
    espa_name = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "ЕСПА", "en": "s ranno chuzhdoezikovo obuchenie ESPA"},
        "bg",
    )

    assert german_name == {"bg": "Ерих Кестнер"}
    assert espa_name == {"bg": "ЕСПА"}


def test_normalize_display_name_i18n_rejects_junk_group_and_markdown_values():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "Групи", "en": "![39"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_screenshot_filename_values():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {
            "bg": "Screenshot 2019-01-02 at 17.18.42.png",
            "en": "Screenshot 2019-01-02 at 17.18.42.png",
        },
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_role_and_icon_noise():
    role_noise = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": 'Директор на ЧСУ “ДРУЖБА”'},
        "bg",
    )
    icon_noise = extractor_module.helpers._normalize_display_name_i18n(
        {"en": "language-school-icon"},
        "bg",
    )

    assert role_noise is None
    assert icon_noise is None


def test_normalize_display_name_i18n_rejects_logo_slug_and_portal_noise():
    logo_slug = extractor_module.helpers._normalize_display_name_i18n(
        {"en": "school-logo"},
        "bg",
    )
    portal_noise = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "Към Портал МИЛЕА"},
        "bg",
    )

    assert logo_slug is None
    assert portal_noise is None


def test_normalize_display_name_i18n_rejects_submenu_navigation_noise():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "Close submenu (Училището)", "en": "Close submenu (Училището)"},
        "bg",
    )

    assert normalized is None


def test_extract_display_name_i18n_deterministic_falls_back_to_known_aliases():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text="## Our kindergartens",
        registry_name='Частна детска градина АВСландия',
        country_code="bg",
        website_url="https://abckinder.org/",
        known_aliases=["ABClandia", "ABCKinder"],
    )

    assert extracted == {"bg": "ABClandia", "en": "ABClandia"}


def test_extract_alias_display_name_i18n_keeps_bg_and_en_aliases():
    extracted = extractor_module.helpers._extract_alias_display_name_i18n(
        ["Йор Кидс", "Your Kids"],
        "bg",
    )

    assert extracted == {"bg": "Йор Кидс", "en": "Your Kids"}


def test_extract_alias_display_name_i18n_prefers_specific_brand_over_acronym():
    extracted = extractor_module.helpers._extract_alias_display_name_i18n(
        ["American College of Sofia", "ACS"],
        "bg",
    )

    assert extracted == {"bg": "American College of Sofia", "en": "American College of Sofia"}


def test_should_prefer_alias_display_name_handles_cross_script_brand_expansion():
    should_prefer = extractor_module.helpers._should_prefer_alias_display_name(
        {"bg": "ТУТИ 2011"},
        {"bg": "TUTI Kindergarten", "en": "TUTI Kindergarten"},
    )

    assert should_prefer is True


def test_extract_display_name_i18n_deterministic_prefers_fuller_alias_over_short_brand():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text="![American College Logo](https://example.com/logo.png)",
        registry_name="Американски колеж в София",
        country_code="bg",
        website_url="https://www.acs.bg/bg/",
        known_aliases=["American College of Sofia", "ACS"],
    )

    assert extracted == {"bg": "American College of Sofia", "en": "American College of Sofia"}


def test_extract_display_name_i18n_deterministic_rejects_network_wide_bulgaria_label():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text="![Maarif International Education Bulgaria](https://example.com/logo.png)\n* [Училища Маариф в България](https://example.com)",
        registry_name="ЧАСТНА ДЕТСКА ГРАДИНА МААРИФ СОФИЯ",
        country_code="bg",
        website_url="https://bg.maarifschools.org/page/detska-gradina",
    )

    assert extracted == {"bg": "МААРИФ СОФИЯ", "en": "MAARIF SOFIA"}


def test_extract_display_name_i18n_deterministic_prefers_alias_over_short_maple_bear():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text="## Maple Bear",
        registry_name="ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ",
        country_code="bg",
        website_url="https://sofia-school.maplebear.bg/en/",
        known_aliases=["Maple Bear Sofia", "Maple Bear Sofia School", "Maple Bear"],
    )

    assert extracted == {"bg": "Maple Bear Sofia", "en": "Maple Bear Sofia"}


def test_extract_display_name_i18n_deterministic_rejects_copyright_network_heading():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text="## © 2026 Maple Bear Global Schools Ltd.",
        registry_name="ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ",
        country_code="bg",
        website_url="https://sofia-school.maplebear.bg/en/",
        known_aliases=["Maple Bear Sofia"],
    )

    assert extracted == {"bg": "Maple Bear Sofia", "en": "Maple Bear Sofia"}


def test_extract_display_name_i18n_deterministic_ignores_screenshot_asset_name():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text=(
            "![Screenshot 2019-01-02 at 17.18.42.png](https://static.wixstatic.com/media/foo.png)\n"
            "### Частна детска градина Албертино\n"
        ),
        registry_name='"Частна детска градина Албертино 01.09." ЕООД',
        country_code="bg",
        website_url="https://www.albertino.bg",
    )

    assert extracted == {"bg": "Албертино 01.09.", "en": "Albertino 01.09."}


def test_derive_display_name_seed_aliases_from_host_and_registry():
    aliases = extractor_module.helpers._derive_display_name_seed_aliases(
        registry_name='Частна детска градина АВСландия',
        website_url="https://abckinder.org/",
    )

    assert aliases == ["ABClandia", "ABC Kinder"]


def test_extract_display_name_i18n_deterministic_uses_seed_aliases_from_host():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text="## Preschool Program",
        registry_name="ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ",
        country_code="bg",
        website_url="https://sofia-school.maplebear.bg/en/",
    )

    assert extracted == {"bg": "Maple Bear Sofia", "en": "Maple Bear Sofia"}


def test_extract_display_name_i18n_deterministic_uses_seed_aliases_for_mixed_script_brand():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text="## Summer School & Courses",
        registry_name='Частна детска градина АВСландия',
        country_code="bg",
        website_url="https://abckinder.org/",
    )

    assert extracted == {"bg": "ABClandia", "en": "ABClandia"}


def test_extract_display_name_i18n_deterministic_augments_english_college_name_with_city():
    extracted = extractor_module.helpers._extract_display_name_i18n_deterministic(
        text="![American College Logo](https://www.acs.bg/media/img/acs_logo.png)",
        registry_name="АМЕРИКАНСКИ КОЛЕЖ В СОФИЯ",
        country_code="bg",
        website_url="https://www.acs.bg/bg/",
    )

    assert extracted == {"bg": "American College of Sofia", "en": "American College of Sofia"}


def test_build_openrouter_model_settings_defaults(monkeypatch):
    monkeypatch.setattr(
        extractor_module,
        "get_settings",
        lambda: SimpleNamespace(
            extraction_temperature=0.1,
            extraction_openrouter_models="",
            extraction_openrouter_provider_order="",
            extraction_openrouter_provider_allow_fallbacks=True,
            extraction_openrouter_provider_sort="",
        ),
    )
    monkeypatch.setattr(extractor_module, "get_model", lambda _tier: "openrouter/google/gemini-2.5-flash-lite")

    model_settings = extractor_module._build_openrouter_model_settings()

    assert model_settings["temperature"] == 0.1
    assert model_settings["openrouter_usage"] == {"include": True}
    assert "openrouter_models" not in model_settings
    assert "openrouter_provider" not in model_settings


def test_build_openrouter_model_settings_with_routing(monkeypatch):
    monkeypatch.setattr(
        extractor_module,
        "get_settings",
        lambda: SimpleNamespace(
            extraction_temperature=0.0,
            extraction_openrouter_models="openrouter/openai/gpt-4o-mini, google/gemini-2.5-flash-lite,anthropic/claude-3.5-haiku",
            extraction_openrouter_provider_order="openai, anthropic",
            extraction_openrouter_provider_allow_fallbacks=False,
            extraction_openrouter_provider_sort="latency",
        ),
    )
    monkeypatch.setattr(extractor_module, "get_model", lambda _tier: "openrouter/google/gemini-2.5-flash-lite")

    model_settings = extractor_module._build_openrouter_model_settings()

    assert model_settings["openrouter_models"] == [
        "openai/gpt-4o-mini",
        "anthropic/claude-3.5-haiku",
    ]
    assert model_settings["openrouter_provider"] == {
        "order": ["openai", "anthropic"],
        "allow_fallbacks": False,
        "sort": "latency",
    }


@pytest.mark.asyncio
async def test_extracted_pricing_links_to_its_evidence_page(
    db_session,
    sample_school_for_extraction,
):
    """Each new row records the page it came from, which is what makes it publishable."""
    school = sample_school_for_extraction
    priced = PriceExtractionOutput(
        prices=[
            ExtractedPrice(
                category="tuition",
                amount=1000.0,
                currency="BGN",
                period="monthly",
                confidence=0.9,
            )
        ],
        has_pricing_info=True,
    )
    pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id))
    ).scalars().all()
    pricing_page = next(page for page in pages if page.page_category == "pricing")

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(return_value=(priced, 10, 2, 0.001)),
    ):
        result = await extractor_module._extract_prices(
            db_session,
            school,
            list(pages),
            20.0,
            extractor_module.ExtractionLLMStats(),
        )

    assert result["success"] is True
    rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().all()
    assert [row.source_page_id for row in rows] == [pricing_page.id]


@pytest.mark.asyncio
async def test_extraction_replaces_only_the_academic_year_it_writes(
    db_session,
    sample_school_for_extraction,
):
    """Fee history survives a refresh: only the written year is superseded."""
    school = sample_school_for_extraction
    db_session.add_all(
        [
            Pricing(
                school_id=school.id,
                category="tuition",
                amount=800,
                currency="BGN",
                period="monthly",
                academic_year="2024-2025",
                source=PriceSource.SCRAPED_WEBSITE,
                source_url="https://test-school.bg/prices",
                pricing_context={"confidence": 0.9},
            ),
            Pricing(
                school_id=school.id,
                category="tuition",
                amount=900,
                currency="BGN",
                period="monthly",
                # Same logical year as the incoming row, spelled differently.
                academic_year="2026-2027",
                source=PriceSource.SCRAPED_WEBSITE,
                source_url="https://test-school.bg/prices",
                pricing_context={"confidence": 0.9},
            ),
        ]
    )
    await db_session.commit()

    priced = PriceExtractionOutput(
        prices=[
            ExtractedPrice(
                category="tuition",
                amount=1000.0,
                currency="BGN",
                period="monthly",
                academic_year="2026/2027",
                confidence=0.9,
            )
        ],
        has_pricing_info=True,
    )
    pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id))
    ).scalars().all()
    next(page for page in pages if page.page_category == "pricing").raw_markdown = (
        "Tuition for 2026/2027 is 1000 BGN monthly."
    )
    await db_session.flush()

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(return_value=(priced, 10, 2, 0.001)),
    ):
        result = await extractor_module._extract_prices(
            db_session,
            school,
            list(pages),
            20.0,
            extractor_module.ExtractionLLMStats(),
        )

    assert result["success"] is True
    rows = (
        await db_session.execute(
            select(Pricing).where(Pricing.school_id == school.id).order_by(Pricing.id)
        )
    ).scalars().all()

    # The earlier year is untouched history; the matching year was replaced once,
    # even though it was stored with a different spelling.
    assert sorted((row.academic_year, float(row.amount)) for row in rows) == [
        ("2024-2025", 800.0),
        ("2026/2027", 1000.0),
    ]


@pytest.mark.asyncio
async def test_extraction_preserves_rows_whose_year_is_unrecognised(
    db_session,
    sample_school_for_extraction,
):
    """An unparseable year must not be deleted as if it were an undated row."""
    school = sample_school_for_extraction
    db_session.add(
        Pricing(
            school_id=school.id,
            category="tuition",
            amount=700,
            currency="BGN",
            period="monthly",
            # Normalizes to None, but it is not the undated bucket.
            academic_year="2022-2027",
            source=PriceSource.SCRAPED_WEBSITE,
            source_url="https://test-school.bg/prices",
            pricing_context={"confidence": 0.9},
        )
    )
    await db_session.commit()

    priced = PriceExtractionOutput(
        prices=[
            ExtractedPrice(
                category="tuition",
                amount=1000.0,
                currency="BGN",
                period="monthly",
                confidence=0.9,
            )
        ],
        has_pricing_info=True,
    )
    pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id))
    ).scalars().all()

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(return_value=(priced, 10, 2, 0.001)),
    ):
        result = await extractor_module._extract_prices(
            db_session,
            school,
            list(pages),
            20.0,
            extractor_module.ExtractionLLMStats(),
        )

    assert result["success"] is True
    rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().all()
    # The undated incoming row does not supersede the unrecognised-year row.
    assert sorted((row.academic_year or "", float(row.amount)) for row in rows) == [
        ("", 1000.0),
        ("2022-2027", 700.0),
    ]


def _historical_scraped_price(school, page, amount=650):
    """A 2025-26 fee row backed by a still-valid evidence page."""
    return Pricing(
        school_id=school.id,
        category="tuition",
        amount=amount,
        currency="BGN",
        period="monthly",
        academic_year="2025/2026",
        source=PriceSource.SCRAPED_WEBSITE,
        source_url=page.source_url,
        source_page_id=page.id,
        pricing_context={"confidence": 0.9},
    )


@pytest.mark.asyncio
async def test_no_pricing_found_preserves_historical_rows_and_evidence_links(
    db_session,
    sample_school_for_extraction,
):
    """A later crawl finding no 2026-27 fees must not erase the 2025-26 history.

    Finding nothing supersedes no academic year, so neither the rows nor the links
    that make them publishable may be touched.
    """
    school = sample_school_for_extraction
    pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id))
    ).scalars().all()
    pricing_page = next(page for page in pages if page.page_category == "pricing")
    db_session.add(_historical_scraped_price(school, pricing_page))
    pricing_page.raw_markdown = "Contact the school for current pricing."
    await db_session.commit()

    no_prices = PriceExtractionOutput(prices=[], has_pricing_info=False)
    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(return_value=(no_prices, 10, 2, 0.001)),
    ):
        result = await extractor_module._extract_prices(
            db_session,
            school,
            list(pages),
            20.0,
            extractor_module.ExtractionLLMStats(),
        )

    assert result["success"] is True
    assert result["count"] == 0
    rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].academic_year == "2025/2026"
    # The evidence link survives, so the row stays publishable as history.
    assert rows[0].source_page_id == pricing_page.id


@pytest.mark.asyncio
async def test_provider_failure_still_leaves_pricing_untouched(
    db_session,
    sample_school_for_extraction,
):
    """Unchanged behaviour: a failed LLM call is not an authoritative result."""
    school = sample_school_for_extraction
    pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id))
    ).scalars().all()
    pricing_page = next(page for page in pages if page.page_category == "pricing")
    db_session.add(_historical_scraped_price(school, pricing_page))
    pricing_page.raw_markdown = "No prices listed here."
    await db_session.commit()

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(return_value=(None, 0, 0, 0.0)),
    ):
        result = await extractor_module._extract_prices(
            db_session,
            school,
            list(pages),
            20.0,
            extractor_module.ExtractionLLMStats(),
        )

    assert result["success"] is False
    rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().all()
    assert [(row.academic_year, row.source_page_id) for row in rows] == [
        ("2025/2026", pricing_page.id)
    ]


@pytest.mark.asyncio
async def test_newer_year_extraction_keeps_the_previous_year_alongside_it(
    db_session,
    sample_school_for_extraction,
):
    """The school publishes 2026-27: last year's fee stays as history beside it."""
    school = sample_school_for_extraction
    pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id))
    ).scalars().all()
    pricing_page = next(page for page in pages if page.page_category == "pricing")
    db_session.add(_historical_scraped_price(school, pricing_page))
    pricing_page.raw_markdown = "Tuition for 2026/2027 is 1000 BGN monthly."
    await db_session.commit()

    priced = PriceExtractionOutput(
        prices=[
            ExtractedPrice(
                category="tuition",
                amount=1000.0,
                currency="BGN",
                period="monthly",
                academic_year="2026/2027",
                confidence=0.9,
            )
        ],
        has_pricing_info=True,
    )
    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(return_value=(priced, 10, 2, 0.001)),
    ):
        result = await extractor_module._extract_prices(
            db_session,
            school,
            list(pages),
            20.0,
            extractor_module.ExtractionLLMStats(),
        )

    assert result["success"] is True
    rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().all()
    assert sorted((row.academic_year, float(row.amount)) for row in rows) == [
        ("2025/2026", 650.0),
        ("2026/2027", 1000.0),
    ]


def _packing_school():
    return School(
        name_i18n={"bg": "Тест"},
        website_url="https://example-school.bg",
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
    )


def _select_pricing_pages(pages):
    return extractor_module.helpers._select_pages(
        school=_packing_school(),
        pages=pages,
        preferred_categories=["pricing", "admission", "contact"],
        use_case="pricing",
        include_tokens=("price", "pricing", "fees", "tuition", "такси", "цени"),
    )


def test_oversized_page_cannot_starve_a_smaller_pricing_page(monkeypatch):
    """The school 302 shape: a 15k mis-categorised privacy page hid the real fee page.

    Its privacy policy was categorised `pricing` and filled the entire content budget,
    so the 1.5k page carrying "680 euro" never reached the model.
    """
    settings = extractor_module.helpers.get_settings()
    monkeypatch.setattr(settings, "extraction_max_content_chars", 15000, raising=False)

    fee_text = (
        "Прием и такса\n"
        + ("Общи условия за прием и необходими документи. " * 12)
        + "\nМесечна такса за яслена група за учебната 2025/2026г. е 680 euro\n"
        + "Месечна такса за детска градина 650 euro\n"
    )
    pages = [
        # Ranked ahead of the fee page and large enough to consume the whole budget.
        SourcePage(
            source_url="https://example-school.bg/content/pravila-za-zashtita-danni.php",
            page_category="pricing",
            raw_markdown="Правила за защита на личните данни. Такси. " + ("х" * 15000),
        ),
        SourcePage(
            source_url="https://example-school.bg/content/taksa-za-detska-gradina.php",
            page_category="pricing",
            raw_markdown=fee_text,
        ),
    ]

    selected_text, source_urls = _select_pricing_pages(pages)

    assert "680 euro" in selected_text
    assert "650 euro" in selected_text
    assert "https://example-school.bg/content/taksa-za-detska-gradina.php" in source_urls
    # Budget still respected (the "\n\n" joins between parts are not counted against it).
    assert len(selected_text) <= 15000 + 4


def test_single_long_pricing_page_keeps_effectively_the_whole_budget(monkeypatch):
    """Guard: the fair-budget rule must not gut a school whose fees really are one long page."""
    settings = extractor_module.helpers.get_settings()
    monkeypatch.setattr(settings, "extraction_max_content_chars", 15000, raising=False)

    long_fees = "Такси за обучение\n" + ("Клас и такса за учебната 2026/2027 година. " * 340)
    pages = [
        SourcePage(
            source_url="https://example-school.bg/taksi",
            page_category="pricing",
            raw_markdown=long_fees,
        )
    ]

    selected_text, source_urls = _select_pricing_pages(pages)

    assert source_urls == ["https://example-school.bg/taksi"]
    # Nothing is queued behind it, so no room is held back.
    assert len(selected_text) >= 14000


def test_long_pricing_page_keeps_most_of_the_budget_beside_small_pages(monkeypatch):
    """A long genuine fee page loses only the modest reserve held for the pages behind it."""
    settings = extractor_module.helpers.get_settings()
    monkeypatch.setattr(settings, "extraction_max_content_chars", 15000, raising=False)

    pages = [
        SourcePage(
            source_url="https://example-school.bg/taksi",
            page_category="pricing",
            raw_markdown="Такси за обучение\n" + ("Такса за учебната 2026/2027 година. " * 500),
        ),
        SourcePage(
            source_url="https://example-school.bg/contact",
            page_category="contact",
            raw_markdown="Контакти: 0888 123 456",
        ),
    ]

    selected_text, source_urls = _select_pricing_pages(pages)

    assert "https://example-school.bg/taksi" in source_urls
    # Only the small queued page's own size is reserved, not a fixed quarter share.
    assert len(selected_text) >= 14000


def test_complete_short_fee_page_is_not_dropped_as_a_fragment(monkeypatch):
    """A whole page under the fragment floor is evidence, not a fragment.

    The 500-char minimum exists to reject truncated scraps. Applying it to a complete
    page drops terse fee pages ("Tuition EUR 680") behind an oversized page, recreating
    the starvation this packing rule exists to prevent.
    """
    settings = extractor_module.helpers.get_settings()
    monkeypatch.setattr(settings, "extraction_max_content_chars", 15000, raising=False)

    pages = [
        # Carries the strong pricing slug, so it ranks ahead of the terse fee page.
        SourcePage(
            source_url="https://example-school.bg/taksi-i-ceni",
            page_category="pricing",
            raw_markdown="Такси и цени. Правила за защита на личните данни. " + ("х" * 15000),
        ),
        SourcePage(
            source_url="https://example-school.bg/info",
            page_category=None,
            raw_markdown="Tuition EUR 680",
        ),
    ]

    selected_text, source_urls = _select_pricing_pages(pages)

    assert "https://example-school.bg/info" in source_urls
    assert "Tuition EUR 680" in selected_text
    # Included whole, not clipped, despite being far below the fragment floor.
    assert selected_text.rstrip().endswith("Tuition EUR 680")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Целодневна – 500евро", "Целодневна – 500 евро"),
        ("Такса храна - 88евро.", "Такса храна - 88 евро."),
        ("Месечна такса 1200лева", "Месечна такса 1200 лева"),
        ("1 200ЕВРО", "1 200 ЕВРО"),
    ],
)
def test_glued_bulgarian_currency_word_is_spaced_for_the_prompt(raw, expected):
    assert extractor_module.helpers._space_glued_currency_words(raw) == expected


@pytest.mark.parametrize(
    "unchanged",
    [
        # Already spaced.
        "Целодневна – 500 евро",
        "Месечна такса 1200 лева",
        # Abbreviations and Latin forms already work glued; deliberately left alone.
        "Месечно половин ден 888лв",
        "500eur",
        "500euro",
        "500bgn",
        # The currency word is only the start of a longer word.
        "5евроклуб",
        "3левашки",
        # Not attached to a number at all.
        "Европа 2026",
        "",
    ],
)
def test_prompt_normalization_leaves_other_text_alone(unchanged):
    assert extractor_module.helpers._space_glued_currency_words(unchanged) == unchanged


@pytest.mark.asyncio
async def test_only_the_llm_prompt_sees_spaced_currency_words(
    db_session,
    sample_school_for_extraction,
):
    """School 615: the model misses "500евро" but reads "500 евро".

    Deterministic parsing and the evidence filter must keep the original text, because
    they deliberately do not treat full Bulgarian currency words as currencies.
    """
    school = sample_school_for_extraction
    pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id))
    ).scalars().all()
    next(page for page in pages if page.page_category == "pricing").raw_markdown = (
        "# ТАКСИ\n#### Целодневна – 500евро\nМесечна такса\n#### Такса храна - 88евро\n"
    )
    await db_session.flush()

    # Return a row so the evidence filter actually runs; with no prices it is skipped and
    # the check below would pass without testing anything.
    llm = AsyncMock(
        return_value=(
            PriceExtractionOutput(
                prices=[
                    ExtractedPrice(
                        category="tuition", amount=500.0, currency="EUR",
                        period="monthly", confidence=0.9,
                    )
                ],
                has_pricing_info=True,
            ),
            1, 1, 0.0,
        )
    )
    helpers = extractor_module.helpers
    with patch("app.scrapers.extractor._run_typed_agent", new=llm), patch.object(
        helpers, "_extract_prices_deterministic", wraps=helpers._extract_prices_deterministic
    ) as deterministic, patch.object(
        helpers, "_filter_supported_prices", wraps=helpers._filter_supported_prices
    ) as support:
        await extractor_module._extract_prices(
            db_session, school, list(pages), 20.0, extractor_module.ExtractionLLMStats()
        )

    prompt = llm.call_args.kwargs.get("user_prompt") or llm.call_args.args[1]
    assert "500 евро" in prompt and "88 евро" in prompt
    assert "500евро" not in prompt

    deterministic_text = deterministic.call_args.args[0]
    assert "500евро" in deterministic_text
    assert "500 евро" not in deterministic_text

    assert support.call_count >= 1
    for call in support.call_args_list:
        assert "500евро" in call.args[1]
        assert "500 евро" not in call.args[1]


@pytest.mark.asyncio
async def test_extract_school_pins_campus_locations_after_its_commit(db_session, sample_school_for_extraction):
    """UF42(b): validation inside extraction creates campus rows; they are geocoded once committed."""
    school = sample_school_for_extraction
    school_id = school.id
    mock_price = PriceExtractionOutput(prices=[], has_pricing_info=False)
    mock_general = GeneralInfoExtractionOutput(
        languages=[ExtractedLanguageFocus(language="English")], has_useful_info=True
    )
    async def fake_agent(**kwargs):
        if kwargs["result_type"] is PriceExtractionOutput:
            return mock_price, 1, 1, 0.0
        if kwargs["result_type"] is GeneralInfoExtractionOutput:
            return mock_general, 1, 1, 0.0
        return None, 0, 0, 0.0

    with (
        patch("app.scrapers.extractor._run_typed_agent", new=AsyncMock(side_effect=fake_agent)),
        patch("app.scrapers.campus_sync.geocode_campus_locations", new=AsyncMock()) as geocode,
    ):
        result = await extractor_module.extract_school(db_session, school_id, "bg")

    assert result["status"] == "extracted"
    geocode.assert_awaited_once()
    assert geocode.await_args.args[1] == school_id


# UF45 rule 6: a re-extraction that loses what the published rows show is held.

_HOLD_PAGE = (
    "ТАКСИ\n€ 530 | 1037 лв.\n- целодневно гледане, ежемесечно заплащане\n"
    "€ 350 | 685 лв.\n- половин ден с включен обяд\n"
    "€ 265 | 518 лв.\n– депозит за запазване на място\n"
)


async def _published_rows_and_page(db_session, school, rows):
    pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == school.id))
    ).scalars().all()
    pricing_page = next(page for page in pages if page.page_category == "pricing")
    pricing_page.raw_markdown = _HOLD_PAGE
    for category, amount, period in rows:
        db_session.add(
            Pricing(
                school_id=school.id,
                category=category,
                amount=amount,
                currency="EUR",
                period=period,
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=pricing_page.source_url,
                source_page_id=pricing_page.id,
                pricing_context={"confidence": 1.0},
            )
        )
    await db_session.commit()
    return list(pages)


async def _run_prices(db_session, school, pages, prices):
    output = PriceExtractionOutput(
        prices=[
            ExtractedPrice(category=category, amount=amount, currency="EUR", period=period, confidence=1.0)
            for category, amount, period in prices
        ],
        has_pricing_info=True,
    )
    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(return_value=(output, 10, 2, 0.001)),
    ):
        return await extractor_module._extract_prices(
            db_session, school, pages, 20.0, extractor_module.ExtractionLLMStats()
        )


async def _stored(db_session, school):
    rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().all()
    return sorted(
        (row.category.value, float(row.amount), row.period.value if row.period else None) for row in rows
    )


@pytest.mark.asyncio
async def test_reextraction_dropping_a_fee_the_page_still_shows_is_held(
    db_session, sample_school_for_extraction
):
    school = sample_school_for_extraction
    published = [("tuition", 530, "monthly"), ("registration", 265, "one_time")]
    pages = await _published_rows_and_page(db_session, school, published)

    result = await _run_prices(db_session, school, pages, [("tuition", 530.0, "monthly")])

    assert result["held"] is True
    assert await _stored(db_session, school) == sorted(
        [("tuition", 530.0, "monthly"), ("registration", 265.0, "one_time")]
    )
    hold = school.attributes[extractor_module.PRICING_HOLD_KEY]
    assert hold["reasons"] == ["drops REGISTRATION 265.00, which the page still shows"]


@pytest.mark.asyncio
async def test_reextraction_losing_a_period_the_page_does_not_state_is_held(
    db_session, sample_school_for_extraction
):
    school = sample_school_for_extraction
    pages = await _published_rows_and_page(db_session, school, [("tuition", 350, "monthly")])

    result = await _run_prices(db_session, school, pages, [("tuition", 350.0, None)])

    assert result["held"] is True
    assert await _stored(db_session, school) == [("tuition", 350.0, "monthly")]


@pytest.mark.asyncio
async def test_reextraction_losing_a_period_the_page_states_is_written(
    db_session, sample_school_for_extraction
):
    """Validation fills "ежемесечно" back in, so this is not a regression."""
    school = sample_school_for_extraction
    school.attributes = {extractor_module.PRICING_HOLD_KEY: {"reasons": ["earlier"]}}
    pages = await _published_rows_and_page(db_session, school, [("tuition", 530, "monthly")])

    result = await _run_prices(db_session, school, pages, [("tuition", 530.0, None)])

    assert result.get("held") is None
    assert await _stored(db_session, school) == [("tuition", 530.0, None)]
    # A replacement that goes through clears the earlier hold.
    assert extractor_module.PRICING_HOLD_KEY not in school.attributes


@pytest.mark.asyncio
async def test_reextraction_is_not_held_for_a_fee_the_page_no_longer_shows(
    db_session, sample_school_for_extraction
):
    """525's €560 became €530: the old amount is gone from the page."""
    school = sample_school_for_extraction
    pages = await _published_rows_and_page(db_session, school, [("tuition", 560, "monthly")])

    result = await _run_prices(db_session, school, pages, [("tuition", 530.0, "monthly")])

    assert result.get("held") is None
    assert [row[:2] for row in await _stored(db_session, school)] == [("tuition", 530.0)]


def test_pricing_selection_prefers_a_page_that_states_prices_over_preferred_categories():
    """The school 568 shape: an uncategorised fee.html lost all four slots to
    admission/contact/home pages, so its fee table never reached the model."""
    pages = [
        SourcePage(source_url="https://example-school.bg/admission.html", page_category="admission", raw_markdown="Прием на ученици. " * 20),
        SourcePage(source_url="https://example-school.bg/contact.html", page_category="contact", raw_markdown="Контакти и адрес. " * 20),
        SourcePage(source_url="https://example-school.bg", page_category="about", raw_markdown="Начало. " * 20),
        SourcePage(source_url="https://example-school.bg/apply.html", page_category="admission", raw_markdown="Кандидатстване. " * 20),
        SourcePage(source_url="https://example-school.bg/team.html", page_category="contact", raw_markdown="Екип и телефон. " * 20),
        SourcePage(
            source_url="https://example-school.bg/fee.html",
            page_category=None,
            raw_markdown="Годишна такса за учебната 2026/2027 г.\nПГ | EUR 8950 | EUR 9300\nVIII - XII клас | EUR 6700 | EUR 7050",
        ),
    ]  # fmt: skip

    selected_text, source_urls = _select_pricing_pages(pages)

    assert source_urls[0] == "https://example-school.bg/fee.html"
    assert "EUR 6700" in selected_text


def test_pricing_selection_counts_url_spellings_of_one_page_once():
    """`http://host` and `http://host/` (or an escaped and a decoded path) are one page;
    the newest crawl is the one kept."""
    old = datetime.datetime(2026, 2, 1, tzinfo=datetime.UTC)
    new = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    pages = [
        SourcePage(source_url="http://example-school.bg/", page_category="contact", raw_markdown="Начало старо", last_scraped_at=old),
        SourcePage(source_url="https://www.example-school.bg", page_category="contact", raw_markdown="Начало ново", last_scraped_at=new),
        SourcePage(source_url="https://example-school.bg/%D1%82%D0%B0%D0%BA%D1%81%D0%B8", page_category="pricing", raw_markdown="Такса 500 евро", last_scraped_at=old),
        SourcePage(source_url="https://example-school.bg/такси/", page_category="pricing", raw_markdown="Такса 600 евро", last_scraped_at=new),
    ]  # fmt: skip

    selected_text, source_urls = _select_pricing_pages(pages)

    assert source_urls == ["https://example-school.bg/такси/", "https://www.example-school.bg"]
    assert "600 евро" in selected_text and "500 евро" not in selected_text
    assert "Начало старо" not in selected_text


def test_long_pricing_page_is_cut_to_its_fee_section_not_its_head(monkeypatch):
    settings = extractor_module.helpers.get_settings()
    monkeypatch.setattr(settings, "extraction_max_content_chars", 4000, raising=False)
    fees = "Такса обучение I - IV клас 7000 евро\nТакса обучение V - VII клас 7500 евро\n"
    pages = [
        SourcePage(
            source_url="https://example-school.bg/taksi",
            page_category="pricing",
            raw_markdown=("Меню и навигация. " * 500) + fees + ("Общи условия. " * 500),
        )
    ]

    selected_text, _ = _select_pricing_pages(pages)

    assert selected_text.startswith("--- SOURCE: https://example-school.bg/taksi ---\n")
    assert "7000 евро" in selected_text and "7500 евро" in selected_text
    assert len(selected_text) <= 4000


def _model_price(**fields):
    return ExtractedPrice(currency="EUR", confidence=0.9, **fields)


def test_model_row_keeps_its_category_under_an_unrelated_heading():
    """School 404: the admission-steps heading above the fee list carried "registration"
    onto every tuition line, and the heading outranked the model."""
    text = """
    --- SOURCE: https://school.test/priem-i-taksi ---
    Необходими документи за записване и регистрация
    Договор и заплащане
    За ученици от 1 до 3 клас: 6 750 евро / година / девет вноски
    За ученици от 4 до 7 клас: 7 500 евро / година / десет вноски
    """
    rows = [
        _model_price(category="tuition", amount=6750, period="yearly", plan_name="Ученици от 1 до 3 клас"),
        _model_price(category="tuition", amount=7500, period="yearly", plan_name="Ученици от 4 до 7 клас"),
    ]  # fmt: skip

    refined = extractor_module.helpers._filter_supported_prices(rows, text, model_rows=True)

    assert [(row.category, row.amount, row.period) for row in refined] == [
        ("tuition", 6750, "yearly"),
        ("tuition", 7500, "yearly"),
    ]
    # The keyword extractor's rows still take the heading's category.
    by_keywords = extractor_module.helpers._filter_supported_prices(rows, text)
    assert {row.category for row in by_keywords} == {"registration"}


def test_model_row_is_supported_by_a_price_line_with_no_fee_word():
    """School 529: "€ 6.540" stands under a level name; no line near it says what fee."""
    text = """
    --- SOURCE: https://school.test/uchebni-taksi ---
    Правилник за учебната 2026/2027 година
    Предучилищна
    € 6.540
    Гимназия
    € 6.740
    """
    rows = [
        _model_price(category="tuition", amount=6540, period="yearly", academic_year="2026/2027", plan_name="Предучилищна"),
        _model_price(category="tuition", amount=6740, period="yearly", academic_year="2026/2027", plan_name="Гимназия"),
        _model_price(category="tuition", amount=9999, period="yearly", plan_name="Гимназия"),
    ]  # fmt: skip

    refined = extractor_module.helpers._filter_supported_prices(rows, text, model_rows=True)

    # Both amounts on the page are kept; the period the page does not state is cleared;
    # an amount that is not on the page is still dropped.
    assert [(row.category, row.amount, row.period) for row in refined] == [
        ("tuition", 6540, None),
        ("tuition", 6740, None),
    ]
    assert extractor_module.helpers._filter_supported_prices(rows, text) == []


def test_model_row_category_is_still_corrected_by_its_own_line():
    text = "--- SOURCE: https://school.test/fees ---\nSchool bus service EUR 1,200 per year"
    row = _model_price(category="tuition", amount=1200, period="yearly")

    refined = extractor_module.helpers._filter_supported_prices([row], text, model_rows=True)

    assert [(r.category, r.amount) for r in refined] == [("transport", 1200)]


def test_model_row_for_a_total_fee_line_is_withheld():
    text = "--- SOURCE: https://school.test/fees ---\nTuition Fee EUR 10,000\nCapital Fee EUR 2,000\nTotal Fee EUR 12,000"
    row = _model_price(category="tuition", amount=12000, period="yearly", plan_name="Total Fee")

    assert extractor_module.helpers._filter_supported_prices([row], text, model_rows=True) == []


def test_pricing_selection_keeps_the_priced_spelling_from_one_crawl():
    """Two spellings stamped by the same crawl: the copy that states the prices is kept."""
    now = datetime.datetime(2026, 10, 1, tzinfo=datetime.UTC)
    pages = [
        SourcePage(source_url="https://example-school.bg/taksi", page_category="pricing", raw_markdown="Прием и такси. Стъпки при кандидатстване.", last_scraped_at=now),
        SourcePage(source_url="https://example-school.bg/taksi/", page_category="pricing", raw_markdown="Такса 6 750 евро / година", last_scraped_at=now),
    ]  # fmt: skip

    selected_text, source_urls = _select_pricing_pages(pages)

    assert source_urls == ["https://example-school.bg/taksi/"]
    assert "6 750 евро" in selected_text
