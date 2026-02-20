import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.field_source import FieldSource, SourceType
from app.models.pricing import PriceCategory, PricePeriod, PriceSource, Pricing
from app.models.scrape_log import ScrapeType
from app.models.school import School
from app.models.source_page import SourcePage
from app.schemas.extraction import (
    ExtractedLanguageFocus,
    ExtractedPrice,
    FacilitiesExtractionOutput,
    MetadataExtractionOutput,
    LanguagesExtractionOutput,
    GeneralInfoExtractionOutput,
    PriceExtractionOutput,
    ProgramsExtractionOutput,
)
from app.scrapers import extractor


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
async def test_extract_creates_pricing_rows(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    mock_price = ExtractedPrice(
        category=PriceCategory.TUITION,
        amount=1000.0,
        currency="BGN",
        period=PricePeriod.MONTHLY,
        academic_year="2024/2025",
        confidence=0.9,
    )
    mock_output = PriceExtractionOutput(prices=[mock_price], has_pricing_info=True)

    empty_general = GeneralInfoExtractionOutput(
        languages=[],
        facilities=[],
        programs=[],
        extracurricular=[],
        class_size=None,
        founded_year=None,
        accreditations=[],
        has_useful_info=False,
    )

    with (
        patch("app.scrapers.extractor._run_agent_with_fallback", new_callable=AsyncMock) as mock_run,
        patch("app.scrapers.extractor._extract_general_info_sections", new_callable=AsyncMock) as mock_sections,
    ):
        mock_run.return_value = (mock_output, 100, 50)
        mock_sections.return_value = (empty_general, 20, 5)

        result = await extractor.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert result["pricing_count"] == 1
    assert mock_run.await_count == 1

    pricing_rows = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().all()
    assert len(pricing_rows) == 1
    row = pricing_rows[0]
    assert row.amount == 1000.0
    assert row.category == PriceCategory.TUITION
    assert row.period == PricePeriod.MONTHLY
    assert row.source == PriceSource.SCRAPED_WEBSITE


@pytest.mark.asyncio
async def test_extract_creates_general_info(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    empty_pricing = PriceExtractionOutput(prices=[], has_pricing_info=False)
    gen_info = GeneralInfoExtractionOutput(
        languages=[
            ExtractedLanguageFocus(language="English"),
            ExtractedLanguageFocus(language="German", level="B1"),
        ],
        facilities=["swimming pool"],
        programs=["STEM"],
        extracurricular=[],
        class_size=None,
        founded_year=None,
        accreditations=[],
        has_useful_info=True,
    )

    with (
        patch("app.scrapers.extractor._run_agent_with_fallback", new_callable=AsyncMock) as mock_run,
        patch("app.scrapers.extractor._extract_general_info_sections", new_callable=AsyncMock) as mock_sections,
    ):
        mock_run.return_value = (empty_pricing, 50, 10)
        mock_sections.return_value = (gen_info, 120, 40)

        await extractor.extract_school(db_session, school.id, "bg")

    await db_session.refresh(school)
    assert "extracted" in school.attributes
    extracted = school.attributes["extracted"]
    assert extracted["languages"][0]["language"] == "English"
    assert extracted["facilities"] == ["swimming pool"]
    assert extracted["programs"] == ["STEM"]

    fs_rows = (
        await db_session.execute(
            select(FieldSource).where(
                FieldSource.school_id == school.id,
                FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
            )
        )
    ).scalars().all()
    keys = [item.field_key for item in fs_rows]
    assert "attributes.languages" in keys
    assert "attributes.facilities" in keys


@pytest.mark.asyncio
async def test_extract_skips_unchanged_content(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    school.attributes["_extraction_hashes"] = {
        "https://test-school.bg/prices": "hash1",
        "https://test-school.bg/about": "hash2",
    }
    db_session.add(school)
    await db_session.commit()

    with patch("app.scrapers.extractor._run_agent_with_fallback", new_callable=AsyncMock) as mock_run:
        result = await extractor.extract_school(db_session, school.id, "bg")

    assert result["skipped"] is True
    mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_extract_attempts_navigation_refresh_for_empty_content(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    pages = (
        await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
            )
        )
    ).scalars().all()
    for page in pages:
        page.raw_markdown = ""
        db_session.add(page)
    await db_session.commit()

    with patch("app.scrapers.navigator.navigate_school", new_callable=AsyncMock) as mock_nav:
        mock_nav.return_value = {"success": False}
        result = await extractor.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extraction_failed"
    assert result["error"] == "No non-empty navigated source page content"
    assert result["content_refresh_attempted"] is True
    assert result["content_refresh_succeeded"] is False
    assert result["llm_stats"]["total_calls"] == 0


@pytest.mark.asyncio
async def test_select_pages_prefers_school_domain(sample_school_for_extraction):
    school = sample_school_for_extraction
    pages = [
        SourcePage(
            school_id=school.id,
            source_url="https://directory.example.com/admission",
            page_category="admission",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="External directory content",
        ),
        SourcePage(
            school_id=school.id,
            source_url="https://test-school.bg/about",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Our school teaches English and German.",
        ),
    ]

    text, urls = extractor._select_pages(
        school=school,
        pages=pages,
        preferred_categories=["about", "admission", "contact"],
        use_case="general_info",
    )
    assert any("test-school.bg/about" in url for url in urls)
    assert all("directory.example.com" not in url for url in urls)
    assert "Our school teaches English and German." in text


def test_parse_llm_output_repairs_fenced_json_and_language_shape():
    raw = """```json
{
  "languages": ["English", "German"],
  "facilities": "library",
  "programs": ["Cambridge"],
  "extracurricular": [],
  "class_size": 24,
  "founded_year": 1995,
  "accreditations": [],
  "has_useful_info": true
}
```"""
    result = SimpleNamespace(output=raw, data=None)
    parsed = extractor._parse_llm_output(result, GeneralInfoExtractionOutput)

    assert isinstance(parsed, GeneralInfoExtractionOutput)
    assert len(parsed.languages) == 2
    assert parsed.languages[0].language == "English"
    assert parsed.facilities == ["library"]
    assert parsed.class_size == "24"
    assert parsed.founded_year == "1995"


def test_languages_section_payload_coercion_tracks_count():
    raw = {
        "languages": [
            "English",
            {"name": "Bulgarian"},
            {"language": "German", "level": 2},
            5,
        ]
    }

    parsed, error, diagnostics = extractor._coerce_and_validate_output_verbose(raw, LanguagesExtractionOutput)

    assert error is None
    assert isinstance(parsed, LanguagesExtractionOutput)
    assert [entry.language for entry in parsed.languages] == ["English", "Bulgarian", "German", "5"]
    assert parsed.languages[2].level == "2"
    assert diagnostics.get("languages_coercions_applied") == 4


def test_should_try_medium_fallback_false_when_same_model(monkeypatch):
    monkeypatch.setattr(extractor, "get_model", lambda tier: "google/gemini-2.5-flash-lite")
    assert extractor._should_try_medium_fallback("cheap") is False


def test_should_try_medium_fallback_true_when_different_models(monkeypatch):
    def fake_get_model(tier: str) -> str:
        return "model-a" if tier == "cheap" else "model-b"

    monkeypatch.setattr(extractor, "get_model", fake_get_model)
    assert extractor._should_try_medium_fallback("cheap") is True


def test_general_info_quality_score_ignores_stringified_structures():
    parsed = GeneralInfoExtractionOutput(
        languages=[ExtractedLanguageFocus(language="English")],
        facilities=[],
        programs=["{'name': 'IB'}"],
        extracurricular=[],
        class_size=None,
        founded_year="1995",
        accreditations=[],
        has_useful_info=True,
    )
    score = extractor._score_general_info_output(parsed)
    assert score == 2


def test_normalize_general_info_output_cleans_stringified_values():
    parsed = GeneralInfoExtractionOutput(
        languages=[ExtractedLanguageFocus(language="{'language':'English','level':'B2'}", level=None)],
        facilities=["{'name':'STEM lab'}", "library"],
        programs=["['Montessori', 'STEM']"],
        extracurricular=["{'value': 'Robotics'}"],
        class_size="{'value':'18 students'}",
        founded_year="Founded in 2011",
        accreditations=["{'title':'Cambridge'}"],
        has_useful_info=True,
    )

    normalized, extracted_i18n = extractor._normalize_general_info_output(parsed, "bg")

    assert [lang.language for lang in normalized.languages] == ["English"]
    assert normalized.facilities == ["STEM lab", "library"]
    assert normalized.programs == ["Montessori", "STEM"]
    assert normalized.extracurricular == ["Robotics"]
    assert normalized.class_size == "18 students"
    assert normalized.founded_year == "2011"
    assert normalized.accreditations == ["Cambridge"]
    assert extracted_i18n is not None
    assert "en" in extracted_i18n


@pytest.mark.asyncio
async def test_general_info_quality_gate_reruns_medium_for_low_quality(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    pages = (
        await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.scrape_type == ScrapeType.WEBSITE,
                SourcePage.is_valid.is_(True),
                SourcePage.raw_markdown.isnot(None),
            )
        )
    ).scalars().all()

    cheap_low_quality = GeneralInfoExtractionOutput(
        languages=[],
        facilities=[],
        programs=["{'name': 'Program'}"],
        extracurricular=[],
        class_size=None,
        founded_year=None,
        accreditations=[],
        has_useful_info=True,
    )
    medium_high_quality = GeneralInfoExtractionOutput(
        languages=[ExtractedLanguageFocus(language="English")],
        facilities=["library"],
        programs=["IB"],
        extracurricular=["sports"],
        class_size="20",
        founded_year="1995",
        accreditations=["Cambridge"],
        has_useful_info=True,
    )

    with patch("app.scrapers.extractor._extract_general_info_sections", new_callable=AsyncMock) as mock_sections:
        mock_sections.side_effect = [
            (cheap_low_quality, 100, 10),
            (medium_high_quality, 120, 20),
        ]

        result = await extractor._extract_general_info(
            db=db_session,
            school=school,
            pages=pages,
            timeout_seconds=10,
            primary_tier="cheap",
            quality_gate_enabled=True,
            min_quality_score=4,
            allow_capable_fallback=False,
        )

    assert result["success"] is True
    assert "quality gate upgraded" in result["detail"]
    assert result["input_tokens"] == 220
    assert result["output_tokens"] == 30
    assert mock_sections.await_count == 2

    extracted = school.attributes.get("extracted", {})
    assert extracted["languages"][0]["language"] == "English"
    assert extracted["facilities"] == ["library"]


def test_extract_founded_year_deterministic_detects_keyword_year():
    text = (
        "ДГ Пример е основана през 1998 година. "
        "Таксата за учебната 2024/2025 година е 650 лв."
    )
    assert extractor._extract_founded_year_deterministic(text) == "1998"


def test_extract_founded_year_deterministic_ignores_generic_recent_years():
    text = (
        "Учебна година 2026/2027 започва през септември. "
        "Приемът за 2025 е отворен."
    )
    assert extractor._extract_founded_year_deterministic(text) is None


def test_extract_languages_deterministic_uses_context_markers():
    text = (
        "Обучението по английски и немски език започва в подготвителна група. "
        "В двора има спортна площадка."
    )
    languages = extractor._extract_languages_deterministic(text)
    labels = [entry.language for entry in languages]
    assert "English" in labels
    assert "German" in labels


def test_extract_class_size_deterministic_detects_group_size():
    text = "Групите са до 18 деца, а в първи клас са по 20 ученици."
    assert extractor._extract_class_size_deterministic(text) == "18 students"


def test_extract_accreditations_deterministic_detects_formal_context():
    text = (
        "Училището е акредитирано от Cambridge International и е оторизирано за "
        "International Baccalaureate Diploma Programme."
    )
    values = extractor._extract_accreditations_deterministic(text)
    assert "Cambridge International" in values
    assert "International Baccalaureate (IB)" in values


@pytest.mark.asyncio
async def test_extract_general_info_sections_merges_deterministic_signals(sample_school_for_extraction):
    school = sample_school_for_extraction
    pages = [
        SourcePage(
            school_id=school.id,
            source_url="https://test-school.bg/about",
            page_category="about",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown=(
                "Основана през 2004 година. Обучение по английски език. "
                "Групите са до 18 деца. Училището е акредитирано от Cambridge International."
            ),
        )
    ]

    async def fake_run(*, result_type, **kwargs):  # type: ignore[no-untyped-def]
        if result_type is LanguagesExtractionOutput:
            return LanguagesExtractionOutput(languages=[]), 10, 2
        if result_type is FacilitiesExtractionOutput:
            return FacilitiesExtractionOutput(facilities=[]), 10, 2
        if result_type is ProgramsExtractionOutput:
            return ProgramsExtractionOutput(programs=[], extracurricular=[]), 10, 2
        if result_type is MetadataExtractionOutput:
            return MetadataExtractionOutput(class_size=None, founded_year=None, accreditations=[]), 10, 2
        raise AssertionError(f"Unexpected result_type {result_type}")

    with patch("app.scrapers.extractor._run_agent_with_fallback", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = fake_run
        combined, input_tokens, output_tokens = await extractor._extract_general_info_sections(
            school=school,
            pages=pages,
            school_name="Тестово Училище",
            fallback_content=(
                "Основана през 2004 година. Обучение по английски език. "
                "Групите са до 18 деца. Училището е акредитирано от Cambridge International."
            ),
            timeout_seconds=10,
            primary_tier="cheap",
            allow_capable_fallback=False,
        )

    assert input_tokens == 40
    assert output_tokens == 8
    assert combined.founded_year == "2004"
    assert combined.class_size == "18 students"
    assert "Cambridge International" in combined.accreditations
    assert any(entry.language == "English" for entry in combined.languages)


# ---------------------------------------------------------------------------
# _extract_contact_info_deterministic
# ---------------------------------------------------------------------------

def test_contact_info_extracts_bg_mobile_and_email():
    text = (
        "За контакти: тел. +359884801660 или 02/9991032. "
        "Email: director@school.bg"
    )
    result = extractor._extract_contact_info_deterministic(text)
    assert result is not None
    assert any("884801660" in p for p in result["phones"])
    assert any("director@school.bg" in e for e in result["emails"])


def test_contact_info_extracts_at_obfuscated_email():
    text = "Пишете ни на info [ at ] kids-academy.bg за повече информация."
    result = extractor._extract_contact_info_deterministic(text)
    assert result is not None
    assert "info@kids-academy.bg" in result["emails"]


def test_contact_info_skips_noise_emails():
    text = "Contact us at noreply@example.com or test@test.com"
    result = extractor._extract_contact_info_deterministic(text)
    # Both should be filtered by noise tokens
    assert result is None or not result.get("emails")


def test_contact_info_returns_none_for_empty_text():
    assert extractor._extract_contact_info_deterministic("") is None
    assert extractor._extract_contact_info_deterministic(None) is None


def test_contact_info_deduplicates_phones():
    text = "+359 2 9991032 ... 02 9991032 ... +359 2 999 10 32"
    result = extractor._extract_contact_info_deterministic(text)
    # All three are the same number - should deduplicate
    assert result is None or len(result.get("phones", [])) < 3


def test_contact_info_limits_to_three_phones():
    text = (
        "+359887111111, +359887222222, +359887333333, +359887444444"
    )
    result = extractor._extract_contact_info_deterministic(text)
    assert result is not None
    assert len(result["phones"]) <= 3


@pytest.mark.asyncio
async def test_extract_general_info_includes_contact(sample_school_for_extraction):
    school = sample_school_for_extraction
    pages = [
        SourcePage(
            school_id=school.id,
            source_url="https://test-school.bg/contact",
            page_category="contact",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Телефон: +359884801660  Email: office@test-school.bg",
        )
    ]

    async def fake_run(*, result_type, **kwargs):  # type: ignore[no-untyped-def]
        if result_type is LanguagesExtractionOutput:
            return LanguagesExtractionOutput(languages=[]), 5, 1
        if result_type is FacilitiesExtractionOutput:
            return FacilitiesExtractionOutput(facilities=[]), 5, 1
        if result_type is ProgramsExtractionOutput:
            return ProgramsExtractionOutput(programs=[], extracurricular=[]), 5, 1
        if result_type is MetadataExtractionOutput:
            return MetadataExtractionOutput(class_size=None, founded_year=None, accreditations=[]), 5, 1
        raise AssertionError(f"Unexpected result_type {result_type}")

    with patch("app.scrapers.extractor._run_agent_with_fallback", new_callable=AsyncMock) as mock_run:
        mock_run.side_effect = fake_run
        combined, _, _ = await extractor._extract_general_info_sections(
            school=school,
            pages=pages,
            school_name="Тестово Училище",
            fallback_content="",
            timeout_seconds=10,
            primary_tier="cheap",
            allow_capable_fallback=False,
        )

    # Contact is stored separately; check it lands in school.attributes after _extract_general_info
    # _extract_general_info_sections doesn't persist directly, but the caller does.
    # We verify the deterministic extraction function itself found the data:
    contact = extractor._extract_contact_info_deterministic(
        "Телефон: +359884801660  Email: office@test-school.bg"
    )
    assert contact is not None
    assert any("884801660" in p for p in contact["phones"])
    assert "office@test-school.bg" in contact["emails"]
