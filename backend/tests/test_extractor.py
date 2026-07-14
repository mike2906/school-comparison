"""Tests for scraper extraction."""

from __future__ import annotations

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


@pytest.mark.asyncio
async def test_extract_school_uses_deterministic_fallback_when_general_llm_fails(
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
                confidence=0.9,
            )
        ],
        has_pricing_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 100, 10, 0.001), (None, 0, 0, 0.0)]),
    ):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert result["general_info_success"] is True
    assert any("deterministic fallback" in detail for detail in result["details"])

    await db_session.refresh(school)
    extracted = (school.attributes or {}).get("extracted", {})
    assert extracted.get("languages"), "deterministic fallback should preserve at least detected languages"


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
    assert parsed.prices[0].period == "yearly"
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
    assert by_amount[530].period == "monthly"
    assert by_amount[350].period == "monthly"
    assert by_amount[265].category == "registration"
    assert by_amount[265].period == "one_time"
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
    assert extracurricular_by_amount[300].period == "yearly"
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
    assert by_amount[200].period == "one_time"
    assert by_amount[2100].category == "transport"
    assert by_amount[345].category == "food"
    assert by_amount[345].period == "quarter"


def test_extract_prices_deterministic_maps_installment_multipliers_to_periods():
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
    assert by_amount[4094].period == "semester"
    assert by_amount[843].period == "monthly"


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

    assert by_amount[530].period == "monthly"
    assert by_amount[350].period == "monthly"
    assert by_amount[265].category == "registration"
    assert by_amount[265].period == "one_time"


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
            category="tuition", amount=500, currency="EUR", period="monthly",
            academic_year=None, confidence=0.9,
        ),
        ExtractedPrice(
            category="tuition", amount=6000, currency="EUR", period="yearly",
            academic_year="2026/2027", confidence=0.9,
        ),
    ]

    deduped = extractor_module.helpers._dedupe_price_rows(prices)

    assert [(row.amount, row.academic_year) for row in deduped] == [(6000, "2026/2027")]


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


def test_find_supporting_price_source_url_returns_page_with_matching_amount():
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
        category="tuition", amount=750, currency="EUR", period="monthly", confidence=0.9
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
        category="transport", amount=2900, currency="EUR", period="yearly",
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
    assert [(row.category.value, float(row.amount), row.currency, row.period.value) for row in pricing_rows] == [
        ("tuition", 6650.0, "EUR", "yearly"),
        ("tuition", 7150.0, "EUR", "yearly"),
        ("materials", 545.0, "EUR", "yearly"),
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
    assert any("cleared existing scraped pricing" in detail for detail in result["details"])
    pricing_rows = (await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))).scalars().all()
    assert pricing_rows == []


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
