"""Tests for scraper extraction."""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import FieldSource, Pricing, School, SchoolLocation
from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.extraction import (
    ExtractedLanguageFocus,
    GeneralInfoExtractionOutput,
    SummarySourceExtractionOutput,
)
from app.scrapers import extractor as extractor_module
from app.scrapers.extraction import (
    contact_details,
    display_naming,
    page_selection,
    summary_sources,
)
from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG

# Fee pages in these tests name academic years such as 2025/2026; pin the date they are read on.
pytestmark = pytest.mark.usefixtures("frozen_today")


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
async def test_extract_school_leaves_fees_to_the_fees_agent(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    db_session.add(
        Pricing(school_id=school.id, category=PriceCategory.TUITION, amount=700, currency="EUR",
                period=PricePeriod.MONTHLY, source=PriceSource.SCRAPED_WEBSITE)
    )  # fmt: skip
    await db_session.commit()
    general = GeneralInfoExtractionOutput(display_name_i18n={"en": "Fusion School"}, has_useful_info=True)

    with patch("app.scrapers.extractor._run_typed_agent", new=AsyncMock(return_value=(general, 150, 20, 0.002))):
        result = await extractor_module.extract_school(db_session, school.id, "bg")

    assert result["general_info_success"] is True
    rows = (await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))).scalars().all()
    assert [(float(r.amount), r.currency) for r in rows] == [(700.0, "EUR")]


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

    with patch("app.scrapers.extractor._run_typed_agent", new=_failed_llm_call):
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


async def _recrawled_school_with_prices(db_session, school, *, status):
    """A published school whose price page was re-crawled: 900 EUR is gone, 1000 BGN stays."""
    school.attributes = {
        "extracted": {"facilities": ["pool"]},
        "data_validation": {
            "_schema_version": 1,
            "validated_at": "2026-09-01T00:00:00+00:00",
            "status": "ok",
            "issue_counts": {"error": 0, "warning": 0},
            "issues": [],
            "auto_fixes": [],
        },
    }
    school.scrape_status = status
    page = (
        await db_session.execute(select(SourcePage).where(SourcePage.source_url == "https://test-school.bg/prices"))
    ).scalar_one()
    rows = [
        Pricing(
            school_id=school.id,
            category=PriceCategory.TUITION,
            amount=amount,
            currency=currency,
            period=PricePeriod(period),
            source=PriceSource.SCRAPED_WEBSITE,
            source_url=page.source_url,
            source_page_id=page.id,
            pricing_context={"confidence": 1.0},
        )
        for amount, currency, period in ((900.0, "EUR", "yearly"), (1000.0, "BGN", "monthly"))
    ]
    db_session.add_all(rows)
    db_session.add(_website_field_source(school.id, "https://www.test-school.bg/about"))
    await db_session.commit()
    return rows


@pytest.mark.asyncio
async def test_failed_extraction_keeps_only_prices_the_recrawled_page_still_shows(
    db_session, client, sample_school_for_extraction
):
    school = sample_school_for_extraction
    gone, still = await _recrawled_school_with_prices(db_session, school, status="navigated")

    with patch("app.scrapers.extractor._run_typed_agent", new=_failed_llm_call):
        result = await extractor_module.extract_school(db_session, school.id, "bg")
    assert result["status"] == "extraction_failed"

    await db_session.refresh(school)
    assert school.scrape_status == "extracted"
    report = school.attributes["data_validation"]
    assert [issue["field_path"] for issue in report["kept_price_issues"]] == [f"pricing[{gone.id}]"]
    # The report itself is untouched: same run, status, issues.
    assert (report["validated_at"], report["status"], report["issues"]) == (
        "2026-09-01T00:00:00+00:00", "ok", []
    )

    response = await client.get(f"/schools/{school.id}")
    assert [row["id"] for row in response.json()["pricing"]] == [still.id]

    # The page shows the amount again; the next failed run publishes the row again.
    page = (
        await db_session.execute(select(SourcePage).where(SourcePage.source_url == "https://test-school.bg/prices"))
    ).scalar_one()
    page.raw_markdown += " Yearly tuition 900 EUR."
    await db_session.commit()
    with patch("app.scrapers.extractor._run_typed_agent", new=_failed_llm_call):
        await extractor_module.extract_school(db_session, school.id, "bg")
    await db_session.refresh(school)
    assert "kept_price_issues" not in school.attributes["data_validation"]


@pytest.mark.asyncio
async def test_extract_school_augments_general_info_with_deterministic_signals(
    db_session,
    sample_school_for_extraction,
):
    school = sample_school_for_extraction

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
        new=AsyncMock(side_effect=[(llm_general, 20, 4, 0.001)]),
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
        patch.object(display_naming, "_extract_display_name_i18n_deterministic", return_value=None),
        patch(
            "app.scrapers.extractor._run_typed_agent",
            new=AsyncMock(
                side_effect=[
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
        new=AsyncMock(side_effect=[(mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
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
    contact = contact_details._extract_contact_info_deterministic(
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

    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
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

    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
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

    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
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

    mock_general = GeneralInfoExtractionOutput(
        facilities=["pool"],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_general, 20, 4, 0.001), (None, 0, 0, 0.0)]),
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
            bad = GeneralInfoExtractionOutput(has_useful_info=True)
            for validator in self._validators:
                try:
                    validator(bad)
                except Exception:
                    pass
            good = GeneralInfoExtractionOutput(programs=["STEM"], has_useful_info=True)
            return FakeAgentResult(good)

    monkeypatch.setattr(extractor_module, "Agent", FakeAgent)
    monkeypatch.setattr(extractor_module, "_build_openrouter_model", lambda *_args, **_kwargs: object())

    parsed, input_tokens, output_tokens, token_cost_usd = await extractor_module._run_typed_agent(
        system_prompt="x",
        user_prompt="y",
        result_type=GeneralInfoExtractionOutput,
        timeout_seconds=5.0,
        llm_stats=llm_stats,
    )

    assert parsed is not None
    assert parsed.programs == ["STEM"]
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

    llm_stats = extractor_module.ExtractionLLMStats()
    monkeypatch.setattr(get_settings(), "extraction_llm_retry_timeout_seconds", 5.0)
    monkeypatch.setattr(get_settings(), "extraction_llm_timeout_retries", 2)
    delays = iter([1.0, 0.2])  # the retry is slower than the first timeout allows

    class FakeAgentResult:
        output = SummarySourceExtractionOutput(has_useful_info=False)

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
        result_type=SummarySourceExtractionOutput,
        timeout_seconds=0.05,
        llm_stats=llm_stats,
    )

    assert parsed is not None
    assert input_tokens == 4
    assert llm_stats.timeout_retries == 1
    assert llm_stats.hard_failures == 0


@pytest.mark.asyncio
async def test_run_typed_agent_gives_up_after_timeout_retries(monkeypatch):

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
        result_type=SummarySourceExtractionOutput,
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
    normalized = summary_sources._normalize_summary_source_output(
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
    normalized = summary_sources._normalize_summary_source_output(
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
    normalized = summary_sources._normalize_summary_source_output(
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

    selected_text, source_urls = page_selection._select_pages(
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

    selected_text, source_urls = page_selection._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["about", "programs", "facilities"],
        use_case="general_summary_source",
        include_tokens=extractor_module.GENERAL_INFO_HINT_TOKENS,
    )

    assert "Montessori methods" in selected_text
    assert source_urls == ["https://example-school.bg/filosofia"]


def test_prepare_summary_source_page_text_drops_navigation_and_keeps_narrative_lines():
    prepared = summary_sources._prepare_summary_source_page_text(
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
    normalized = display_naming._normalize_display_name_i18n(
        {"en": "Fusion School"},
        "bg",
    )

    assert normalized == {
        "bg": "Fusion School",
        "en": "Fusion School",
    }


def test_normalize_display_name_i18n_keeps_cyrillic_single_value_in_bg_only():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": "ЧОУ ПЕТЪР БЕРОН"},
        "bg",
    )

    assert normalized == {
        "bg": "ЧОУ ПЕТЪР БЕРОН",
    }


def test_normalize_display_name_i18n_drops_duplicated_cyrillic_en_value():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": "Българско школо", "en": "Българско школо"},
        "bg",
    )

    assert normalized == {
        "bg": "Българско школо",
    }


def test_normalize_display_name_i18n_drops_transliterated_en_value():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": "Българско школо", "en": "BALGARSKO SHKOLO"},
        "bg",
    )

    assert normalized == {
        "bg": "Българско школо",
    }


def test_normalize_display_name_i18n_simplifies_generic_bg_label_to_brand_core():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": 'Частно начално училище "БИЗИ"'},
        "bg",
    )

    assert normalized == {
        "bg": "БИЗИ",
    }


def test_normalize_display_name_i18n_simplifies_quoted_brand_but_keeps_location_only_case():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": 'Учебен комплекс “Българско школо”'},
        "bg",
    )
    location_only = display_naming._normalize_display_name_i18n(
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

    extracted = display_naming._extract_display_name_i18n_deterministic(
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

    extracted = display_naming._extract_display_name_i18n_deterministic(
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

    extracted = display_naming._extract_display_name_i18n_deterministic(
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

    extracted = display_naming._extract_display_name_i18n_deterministic(
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

    extracted = display_naming._extract_display_name_i18n_deterministic(
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

    extracted = display_naming._extract_display_name_i18n_deterministic(
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

    extracted = display_naming._extract_display_name_i18n_deterministic(
        text,
        registry_name='ЧАСТНО НАЧАЛНО УЧИЛИЩЕ ЛОЗЕН ЕООД',
        country_code="bg",
    )

    assert extracted is None


def test_extract_display_name_i18n_deterministic_rejects_school_news_host_title():
    text = "# 21-во училище стана домакин"

    extracted = display_naming._extract_display_name_i18n_deterministic(
        text,
        registry_name='21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"',
        country_code="bg",
        website_url="https://21su.bg",
    )

    assert extracted is None


def test_extract_display_name_i18n_deterministic_rejects_reference_school_noise():
    text = '[Google Reference School](https://espa.bg)'

    extracted = display_naming._extract_display_name_i18n_deterministic(
        text,
        registry_name='Частно средно училище ЕСПА ЕООД',
        country_code="bg",
    )

    assert extracted is None


def test_extract_display_name_i18n_deterministic_strips_markdown_emphasis():
    text = '[**ЧДГ „Приказка без край“**](https://novigradini.com/pbk/)'

    extracted = display_naming._extract_display_name_i18n_deterministic(
        text,
        registry_name='ЧАСТНА ДЕТСКА ГРАДИНА ПРИКАЗКА БЕЗ КРАЙ 1',
        country_code="bg",
    )

    assert extracted == {
        "bg": 'ЧДГ „Приказка без край“',
        "en": 'ЧДГ „Приказка без край“',
    }


def test_extract_display_name_i18n_deterministic_ignores_city_only_overlap():
    extracted = display_naming._extract_display_name_i18n_deterministic(
        '[УЧИЛИЩА В СОФИЯ](https://luiskarol.com/)',
        registry_name='"Частно начално училище Луис Карол - гр. София" ЕООД',
        country_code="bg",
    )

    assert extracted is None


def test_extract_display_name_i18n_deterministic_falls_back_to_host_aligned_registry_brand():
    extracted = display_naming._extract_display_name_i18n_deterministic(
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
    extracted = display_naming._extract_display_name_i18n_deterministic(
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
    merged = display_naming._merge_display_name_i18n(
        {"bg": "Луис Карол"},
        {"bg": "Луис Карол", "en": "Luis Karol"},
        "bg",
    )

    assert merged == {
        "bg": "Луис Карол",
    }


def test_normalize_display_name_i18n_rejects_low_quality_headings():
    normalized = display_naming._normalize_display_name_i18n(
        {"en": "Our kindergartens", "bg": "Стратегически план на Американския колеж 2027"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_email_contact_labels():
    normalized = display_naming._normalize_display_name_i18n(
        {
            "bg": "Email: school.p.beron@gmail.com",
            "en": "school.p.beron@gmail.com",
        },
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_report_titles():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": "Тримесечен отчет на 145. ОУ Симеон Радев за м.Декември"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_news_host_titles():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": "21-во училище стана домакин"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_generic_numbered_school_labels():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": "131. средно училище"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_generic_numbered_school_abbreviations():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": "21. СУ", "en": "21 Secondary School"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_extracts_core_from_teacher_page_heading():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": 'Преподаватели в 123 СУ "Стефан Стамболов"'},
        "bg",
    )

    assert normalized == {"bg": "Стефан Стамболов"}


def test_normalize_display_name_i18n_strips_logo_prefix_and_early_language_prefix():
    logo_name = display_naming._normalize_display_name_i18n(
        {"bg": "лого Веселата къща"},
        "bg",
    )
    espa_name = display_naming._normalize_display_name_i18n(
        {"bg": "с ранно чуждоезиково обучение ЕСПА"},
        "bg",
    )

    assert logo_name == {"bg": "Веселата къща"}
    assert espa_name == {"bg": "ЕСПА"}


def test_normalize_display_name_i18n_strips_transliterated_generic_en_prefixes():
    german_name = display_naming._normalize_display_name_i18n(
        {"bg": "Ерих Кестнер", "en": "nemska gimnaziya Erih Kestner"},
        "bg",
    )
    espa_name = display_naming._normalize_display_name_i18n(
        {"bg": "ЕСПА", "en": "s ranno chuzhdoezikovo obuchenie ESPA"},
        "bg",
    )

    assert german_name == {"bg": "Ерих Кестнер"}
    assert espa_name == {"bg": "ЕСПА"}


def test_normalize_display_name_i18n_rejects_junk_group_and_markdown_values():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": "Групи", "en": "![39"},
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_screenshot_filename_values():
    normalized = display_naming._normalize_display_name_i18n(
        {
            "bg": "Screenshot 2019-01-02 at 17.18.42.png",
            "en": "Screenshot 2019-01-02 at 17.18.42.png",
        },
        "bg",
    )

    assert normalized is None


def test_normalize_display_name_i18n_rejects_role_and_icon_noise():
    role_noise = display_naming._normalize_display_name_i18n(
        {"bg": 'Директор на ЧСУ “ДРУЖБА”'},
        "bg",
    )
    icon_noise = display_naming._normalize_display_name_i18n(
        {"en": "language-school-icon"},
        "bg",
    )

    assert role_noise is None
    assert icon_noise is None


def test_normalize_display_name_i18n_rejects_logo_slug_and_portal_noise():
    logo_slug = display_naming._normalize_display_name_i18n(
        {"en": "school-logo"},
        "bg",
    )
    portal_noise = display_naming._normalize_display_name_i18n(
        {"bg": "Към Портал МИЛЕА"},
        "bg",
    )

    assert logo_slug is None
    assert portal_noise is None


def test_normalize_display_name_i18n_rejects_submenu_navigation_noise():
    normalized = display_naming._normalize_display_name_i18n(
        {"bg": "Close submenu (Училището)", "en": "Close submenu (Училището)"},
        "bg",
    )

    assert normalized is None


def test_extract_display_name_i18n_deterministic_falls_back_to_known_aliases():
    extracted = display_naming._extract_display_name_i18n_deterministic(
        text="## Our kindergartens",
        registry_name='Частна детска градина АВСландия',
        country_code="bg",
        website_url="https://abckinder.org/",
        known_aliases=["ABClandia", "ABCKinder"],
    )

    assert extracted == {"bg": "ABClandia", "en": "ABClandia"}


def test_extract_alias_display_name_i18n_keeps_bg_and_en_aliases():
    extracted = display_naming._extract_alias_display_name_i18n(
        ["Йор Кидс", "Your Kids"],
        "bg",
    )

    assert extracted == {"bg": "Йор Кидс", "en": "Your Kids"}


def test_extract_alias_display_name_i18n_prefers_specific_brand_over_acronym():
    extracted = display_naming._extract_alias_display_name_i18n(
        ["American College of Sofia", "ACS"],
        "bg",
    )

    assert extracted == {"bg": "American College of Sofia", "en": "American College of Sofia"}


def test_should_prefer_alias_display_name_handles_cross_script_brand_expansion():
    should_prefer = display_naming._should_prefer_alias_display_name(
        {"bg": "ТУТИ 2011"},
        {"bg": "TUTI Kindergarten", "en": "TUTI Kindergarten"},
    )

    assert should_prefer is True


def test_extract_display_name_i18n_deterministic_prefers_fuller_alias_over_short_brand():
    extracted = display_naming._extract_display_name_i18n_deterministic(
        text="![American College Logo](https://example.com/logo.png)",
        registry_name="Американски колеж в София",
        country_code="bg",
        website_url="https://www.acs.bg/bg/",
        known_aliases=["American College of Sofia", "ACS"],
    )

    assert extracted == {"bg": "American College of Sofia", "en": "American College of Sofia"}


def test_extract_display_name_i18n_deterministic_rejects_network_wide_bulgaria_label():
    extracted = display_naming._extract_display_name_i18n_deterministic(
        text="![Maarif International Education Bulgaria](https://example.com/logo.png)\n* [Училища Маариф в България](https://example.com)",
        registry_name="ЧАСТНА ДЕТСКА ГРАДИНА МААРИФ СОФИЯ",
        country_code="bg",
        website_url="https://bg.maarifschools.org/page/detska-gradina",
    )

    assert extracted == {"bg": "МААРИФ СОФИЯ", "en": "MAARIF SOFIA"}


def test_extract_display_name_i18n_deterministic_prefers_alias_over_short_maple_bear():
    extracted = display_naming._extract_display_name_i18n_deterministic(
        text="## Maple Bear",
        registry_name="ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ",
        country_code="bg",
        website_url="https://sofia-school.maplebear.bg/en/",
        known_aliases=["Maple Bear Sofia", "Maple Bear Sofia School", "Maple Bear"],
    )

    assert extracted == {"bg": "Maple Bear Sofia", "en": "Maple Bear Sofia"}


def test_extract_display_name_i18n_deterministic_rejects_copyright_network_heading():
    extracted = display_naming._extract_display_name_i18n_deterministic(
        text="## © 2026 Maple Bear Global Schools Ltd.",
        registry_name="ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ",
        country_code="bg",
        website_url="https://sofia-school.maplebear.bg/en/",
        known_aliases=["Maple Bear Sofia"],
    )

    assert extracted == {"bg": "Maple Bear Sofia", "en": "Maple Bear Sofia"}


def test_extract_display_name_i18n_deterministic_ignores_screenshot_asset_name():
    extracted = display_naming._extract_display_name_i18n_deterministic(
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
    aliases = display_naming._derive_display_name_seed_aliases(
        registry_name='Частна детска градина АВСландия',
        website_url="https://abckinder.org/",
    )

    assert aliases == ["ABClandia", "ABC Kinder"]


def test_extract_display_name_i18n_deterministic_uses_seed_aliases_from_host():
    extracted = display_naming._extract_display_name_i18n_deterministic(
        text="## Preschool Program",
        registry_name="ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ",
        country_code="bg",
        website_url="https://sofia-school.maplebear.bg/en/",
    )

    assert extracted == {"bg": "Maple Bear Sofia", "en": "Maple Bear Sofia"}


def test_extract_display_name_i18n_deterministic_uses_seed_aliases_for_mixed_script_brand():
    extracted = display_naming._extract_display_name_i18n_deterministic(
        text="## Summer School & Courses",
        registry_name='Частна детска градина АВСландия',
        country_code="bg",
        website_url="https://abckinder.org/",
    )

    assert extracted == {"bg": "ABClandia", "en": "ABClandia"}


def test_extract_display_name_i18n_deterministic_augments_english_college_name_with_city():
    extracted = display_naming._extract_display_name_i18n_deterministic(
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


def _packing_school():
    return School(
        name_i18n={"bg": "Тест"},
        website_url="https://example-school.bg",
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level="primary",
    )


@pytest.mark.asyncio
async def test_extract_school_pins_campus_locations_after_its_commit(db_session, sample_school_for_extraction):
    """UF42(b): validation inside extraction creates campus rows; they are geocoded once committed."""
    school = sample_school_for_extraction
    school_id = school.id
    mock_general = GeneralInfoExtractionOutput(
        languages=[ExtractedLanguageFocus(language="English")], has_useful_info=True
    )
    async def fake_agent(**kwargs):
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


def test_extraction_reads_only_the_freshest_copy_of_a_page():
    """School 593: a page stored as /tuition-fees in July and /tuition-fees/ today."""
    def page(url, day):
        return SimpleNamespace(source_url=url, last_scraped_at=datetime.datetime(2026, day[0], day[1]))

    stale = page("https://izzi.academy/admissions/tuition-fees", (7, 15))
    fresh = page("https://izzi.academy/admissions/tuition-fees/", (10, 8))
    older_slash = page("https://izzi.academy/about-us/", (2, 26))
    newer_bare = page("https://izzi.academy/about-us", (7, 15))
    only = page("https://izzi.academy/", (10, 8))

    kept = extractor_module._freshest_url_variants([stale, fresh, older_slash, newer_bare, only])

    assert sorted(p.source_url for p in kept) == [
        "https://izzi.academy/",
        "https://izzi.academy/about-us",
        "https://izzi.academy/admissions/tuition-fees/",
    ]


@pytest.mark.asyncio
async def test_site_address_replaces_a_superseded_registry_address(db_session, sample_school_for_extraction):
    """634: the shared-site check kept the site although it states no registry address;
    the school then takes the address the site gives for its level, and loses its old
    point so that the new address is geocoded."""
    from app.scrapers.shared_site_check import REGISTRY_ADDRESS_SUPERSEDED, SHARED_SITE_CHECK_KEY

    school = sample_school_for_extraction
    school.education_level = "kindergarten"
    location = SchoolLocation(
        school_id=school.id, address_i18n={"bg": 'ул. "Крум Попов" № 69', "en": "69 Krum Popov St"},
        lat=42.65, lng=23.25, is_primary=True, location_tags=["coords_source=nominatim"], district="Витоша",
    )  # fmt: skip
    db_session.add(location)
    await db_session.flush()
    text = "Лозен парк, ул. Манастирска воденица 71 – детска градина и начално училище\nГимназия: бул. Черни връх 32"

    # Without the check's verdict the registry address stands.
    assert await extractor_module._adopt_stated_address(db_session, school, text) is None

    school.attributes = {SHARED_SITE_CHECK_KEY: {"action": "keep", "reason": REGISTRY_ADDRESS_SUPERSEDED}}
    assert await extractor_module._adopt_stated_address(db_session, school, text) == "ул. Манастирска воденица 71"
    assert location.address_i18n == {"bg": "ул. Манастирска воденица 71"}
    assert location.district is None
    # The old point stays until the new address is geocoded after the commit.
    assert (location.lat, location.lng) == (42.65, 23.25)
    assert location.location_tags == [
        "coords_source=nominatim", "address_source=website_contact", "geocode=address_changed",
    ]  # fmt: skip
    # Already there: nothing more to do.
    assert await extractor_module._adopt_stated_address(db_session, school, text) is None

    # A school with a second registered location is left alone: it may be the new premises.
    location.address_i18n = {"bg": 'ул. "Крум Попов" № 69'}
    db_session.add(SchoolLocation(school_id=school.id, address_i18n={"bg": "с. Лозен, ул. Лозен парк 1"}, is_primary=False))
    await db_session.flush()
    assert await extractor_module._adopt_stated_address(db_session, school, text) is None


@pytest.mark.asyncio
async def test_a_pinned_location_keeps_its_address_when_the_registry_is_superseded(
    db_session, sample_school_for_extraction
):
    from app.scrapers.shared_site_check import REGISTRY_ADDRESS_SUPERSEDED, SHARED_SITE_CHECK_KEY
    from app.services.geocoding.write_gate import OFFICIAL_COORDS_TAG

    school = sample_school_for_extraction
    school.attributes = {SHARED_SITE_CHECK_KEY: {"action": "keep", "reason": REGISTRY_ADDRESS_SUPERSEDED}}
    location = SchoolLocation(
        school_id=school.id, address_i18n={"bg": 'ул. "Крум Попов" № 69'}, lat=42.65, lng=23.25,
        is_primary=True, location_tags=[OFFICIAL_COORDS_TAG],
    )  # fmt: skip
    db_session.add(location)
    await db_session.flush()

    assert await extractor_module._adopt_stated_address(db_session, school, "Адрес: ул. Нова 12") is None
    assert location.address_i18n == {"bg": 'ул. "Крум Попов" № 69'}


@pytest.mark.asyncio
async def test_site_map_point_stands_for_a_readdressed_location(db_session, sample_school_for_extraction):
    """The site's own map point is accepted with the address; geocoding the address
    again afterwards would replace it with a rougher one, so the marker is dropped."""
    school = sample_school_for_extraction
    location = SchoolLocation(
        school_id=school.id, address_i18n={"bg": "ул. Манастирска воденица 71"}, lat=42.65, lng=23.25,
        is_primary=True, location_tags=["address_source=website_contact", "geocode=address_changed"],
    )  # fmt: skip
    db_session.add(location)
    await db_session.flush()
    contact = {"address": "ул. Манастирска воденица 71", "coordinates": {"lat": 42.61, "lng": 23.44}}

    with patch.object(
        extractor_module, "apply_geocode_result_to_location", new=AsyncMock(return_value=SimpleNamespace(success=True))
    ):
        await extractor_module._sync_primary_location_from_contact_address(db_session, school, contact)

    assert "geocode=address_changed" not in location.location_tags
    assert "coords_source=website_map_link" in location.location_tags


def test_a_page_stored_under_two_session_ids_is_read_once():
    """School 511: July's fee page, with the old prices, was read beside October's."""
    def page(url, month):
        return SimpleNamespace(source_url=url, last_scraped_at=datetime.datetime(2026, month, 9))

    pages = [
        page("https://www.arty.bg/bg/pages/charges.html?phpsessid=ee8a6ac484e8", 7),
        page("https://www.arty.bg/bg/pages/charges.html?PHPSESSID=ecf72ad1383c", 10),
        page("https://www.arty.bg/bg/pages/charges.html?PHPSESSID=bbdfa3fbde8c&lang=bg", 10),
        page("https://www.arty.bg/bg/gallery.html?id=7&PHPSESSID=ecf72ad1383c", 10),
        page("https://www.arty.bg/bg/gallery.html?id=8", 7),
    ]

    kept = extractor_module._freshest_url_variants(pages)

    assert sorted(p.source_url for p in kept) == [
        "https://www.arty.bg/bg/gallery.html?id=7&PHPSESSID=ecf72ad1383c",
        "https://www.arty.bg/bg/gallery.html?id=8",
        "https://www.arty.bg/bg/pages/charges.html?PHPSESSID=bbdfa3fbde8c&lang=bg",
        "https://www.arty.bg/bg/pages/charges.html?PHPSESSID=ecf72ad1383c",
    ]
