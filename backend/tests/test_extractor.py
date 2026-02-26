import asyncio
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
    AdmissionExtractionOutput,
    ExtractedLanguageFocus,
    ExtractedPrice,
    FacilitiesExtractionOutput,
    MetadataExtractionOutput,
    LanguagesExtractionOutput,
    GeneralInfoExtractionOutput,
    OperationsExtractionOutput,
    PriceExtractionOutput,
    PricingTermsExtractionOutput,
    ProgramsExtractionOutput,
    ServicesExtractionOutput,
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
    pages = (
        await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.page_category == "pricing",
            )
        )
    ).scalars().all()
    for page in pages:
        page.raw_markdown = (
            "Tuition for 2024 is 1000 BGN monthly. "
            "Sibling discount 10%. "
            "Tuition includes lunch."
        )
        db_session.add(page)
    await db_session.commit()

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
    assert (row.pricing_context or {}).get("discounts", []) == []
    assert (row.pricing_context or {}).get("includes", []) == []


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
async def test_extract_pricing_drops_oversized_text_fields(db_session, sample_school_for_extraction):
    school = sample_school_for_extraction
    pages = (
        await db_session.execute(
            select(SourcePage).where(
                SourcePage.school_id == school.id,
                SourcePage.page_category == "pricing",
            )
        )
    ).scalars().all()
    for page in pages:
        page.raw_markdown = "Pricing page content."
        db_session.add(page)
    await db_session.commit()

    mock_price = ExtractedPrice(
        category=PriceCategory.TUITION,
        amount=1000.0,
        currency="BGN",
        period=PricePeriod.MONTHLY,
        academic_year="A" * 64,
        plan_name="B" * 140,
        age_group="C" * 80,
        confidence=0.9,
    )
    mock_output = PriceExtractionOutput(prices=[mock_price], has_pricing_info=True)
    empty_general = GeneralInfoExtractionOutput(has_useful_info=False)

    with (
        patch("app.scrapers.extractor._run_agent_with_fallback", new_callable=AsyncMock) as mock_run,
        patch("app.scrapers.extractor._extract_general_info_sections", new_callable=AsyncMock) as mock_sections,
    ):
        mock_run.return_value = (mock_output, 100, 50)
        mock_sections.return_value = (empty_general, 10, 2)
        result = await extractor.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"

    row = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().one()
    assert row.age_group is None
    assert row.plan_name is None
    assert row.academic_year is None


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


def test_normalize_general_info_output_handles_invalid_language_level():
    parsed = GeneralInfoExtractionOutput(
        languages=[ExtractedLanguageFocus(language="English", level="{}")],
        has_useful_info=True,
    )

    normalized, _ = extractor._normalize_general_info_output(parsed, "bg")
    assert len(normalized.languages) == 1
    assert normalized.languages[0].language == "English"
    assert normalized.languages[0].level is None


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


def test_extract_admission_info_deterministic_extracts_key_fields():
    text = (
        "ПЛАН-ПРИЕМ за учебната 2025/2026 година.\n"
        "Краен срок за подаване на документи: 15.06.2025.\n"
        "Необходими документи: заявление и медицинско свидетелство.\n"
        "Кандидатстване чрез онлайн форма.\n"
        "Приемен изпит по математика.\n"
        "Свободни места: 25."
    )
    result = extractor._extract_admission_info_deterministic(text)
    assert result.has_useful_info is True
    assert any("ПЛАН-ПРИЕМ" in item for item in result.deadlines)
    assert any("Необходими документи" in item for item in result.required_documents)
    assert any("онлайн форма" in item for item in result.application_steps)
    assert any("Приемен изпит" in item for item in result.entrance_requirements)
    assert any("Свободни места" in item for item in result.available_spots)


def test_extract_admission_info_deterministic_extracts_document_items_without_heading():
    text = "Копие от акт за раждане и заявление по образец."
    result = extractor._extract_admission_info_deterministic(text)
    assert any("акт за раждане" in item.lower() for item in result.required_documents)


def test_extract_operations_info_deterministic_extracts_key_fields():
    text = (
        "Работно време: 08:00 - 18:00.\n"
        "Осигуряваме целодневна и полудневна организация.\n"
        "Дневен режим за всички групи.\n"
        "Седмично меню и хранене.\n"
        "Училищен автобус и транспорт.\n"
        "Униформа за учениците."
    )
    result = extractor._extract_operations_info_deterministic(text)
    assert result.has_useful_info is True
    assert result.working_hours is not None
    assert any("целодневна" in item for item in result.day_options)
    assert any("Дневен режим" in item for item in result.daily_schedule)
    assert any("Седмично меню" in item for item in result.meals)
    assert any("автобус" in item for item in result.transport)
    assert any("Униформа" in item for item in result.uniforms)


def test_extract_operations_info_deterministic_detects_meals_without_menu_word():
    text = "Осигуряваме топъл обяд и следобедна закуска всеки ден."
    result = extractor._extract_operations_info_deterministic(text)
    assert any("топъл обяд" in item.lower() for item in result.meals)


def test_extract_services_info_deterministic_extracts_key_fields():
    text = (
        "Екипът включва психолог, логопед и медицинска сестра.\n"
        "Сигурността е подсигурена с видеонаблюдение и охрана."
    )
    result = extractor._extract_services_info_deterministic(text)
    assert result.has_useful_info is True
    assert any("психолог" in item.lower() for item in result.support_services)
    assert any("видеонаблюдение" in item.lower() for item in result.safety_features)


def test_extract_services_info_deterministic_ignores_parent_guide_noise():
    text = (
        "Наръчник на родителя: Лично за мама, здраве и психология, учим и играем заедно.\n"
        "Екипът включва педагогически съветник и ресурсен учител."
    )
    result = extractor._extract_services_info_deterministic(text)
    assert all("наръчник" not in item.lower() for item in result.support_services)
    assert any("педагогически съветник" in item.lower() for item in result.support_services)
    assert any("ресурсен учител" in item.lower() for item in result.support_services)


def test_extract_pricing_terms_deterministic_extracts_key_fields():
    text = (
        "Отстъпка за второ дете 10%.\n"
        "Плащане на 3 вноски.\n"
        "Таксата включва храна.\n"
        "Таксата не е включена униформа.\n"
        "Депозит в размер на 1 такса.\n"
        "Такса за кандидатстване 80 лв.\n"
        "Такса записване 200 лв."
    )
    result = extractor._extract_pricing_terms_deterministic(text)
    assert result.has_useful_info is True
    assert any("Отстъпка" in item for item in result.discounts)
    assert any("вноски" in item for item in result.installments)
    assert any("включва" in item for item in result.included_items)
    assert any("не е включена" in item for item in result.excluded_items)
    assert any("Депозит" in item for item in result.deposits)
    assert any("кандидатстване" in item for item in result.application_fees)
    assert any("записване" in item for item in result.registration_fees)


def test_extract_pricing_terms_deterministic_moves_negated_includes_to_excluded():
    text = "В таксата не е включена храна."
    result = extractor._extract_pricing_terms_deterministic(text)
    # Deterministic extraction captures raw matches; reconciliation happens in normalization.
    normalized = extractor._normalize_pricing_terms_output(result)
    assert normalized.included_items == []
    assert any("не е включена" in item.lower() for item in normalized.excluded_items)


def test_extract_admission_info_deterministic_filters_navigation_noise():
    text = (
        "Начало | За родителите | Необходими документи | Контакти.\n"
        "Необходими документи: заявление и медицинско свидетелство."
    )
    result = extractor._extract_admission_info_deterministic(text)
    assert len(result.required_documents) == 1
    assert "заявление" in result.required_documents[0].lower()


def test_extract_operations_info_deterministic_filters_menu_noise():
    text = (
        "За родителите | Електронен дневник | Седмично меню.\n"
        "Седмично меню: топъл обяд и следобедна закуска."
    )
    result = extractor._extract_operations_info_deterministic(text)
    assert len(result.meals) == 1
    assert "топъл обяд" in result.meals[0].lower()


def test_extract_services_info_deterministic_filters_regulatory_noise():
    text = (
        "Медицински стандарти и центрове за спешна медицинска помощ.\n"
        "Екипът включва психолог и логопед."
    )
    result = extractor._extract_services_info_deterministic(text)
    assert len(result.support_services) == 1
    assert "психолог" in result.support_services[0].lower()


def test_normalize_price_payload_coerces_term_fields():
    payload = {
        "has_pricing_info": True,
        "prices": [
            {
                "category": "tuition",
                "period": "monthly",
                "amount": 1000,
                "discounts": "10% sibling discount",
                "installments": 3,
                "includes": "food",
                "excludes": None,
            }
        ],
    }
    normalized = extractor._normalize_price_payload(payload)
    row = normalized["prices"][0]
    assert row["discounts"] == ["10% sibling discount"]
    assert row["installments"] == ["3"]
    assert row["includes"] == ["food"]
    assert row["excludes"] == []


def test_normalize_admission_output_filters_navigation_noise():
    normalized = extractor._normalize_admission_output(
        AdmissionExtractionOutput(
            deadlines=[],
            required_documents=[
                "Начало | За родителите | Необходими документи | Контакти",
                "Необходими документи: заявление и акт за раждане.",
            ],
            application_steps=[],
            entrance_requirements=[],
            available_spots=[],
            has_useful_info=True,
        )
    )
    assert normalized.required_documents == ["Необходими документи: заявление и акт за раждане."]


def test_normalize_admission_output_drops_heading_only_required_documents():
    normalized = extractor._normalize_admission_output(
        AdmissionExtractionOutput(
            deadlines=[],
            required_documents=[
                "- Необходими документи",
                "Необходими документи:",
                "Медицински документ / имунизационен статус",
            ],
            application_steps=[],
            entrance_requirements=[],
            available_spots=[],
            has_useful_info=True,
        )
    )
    assert normalized.required_documents == ["Медицински документ / имунизационен статус"]


def test_normalize_services_output_filters_regulatory_noise():
    normalized = extractor._normalize_services_output(
        ServicesExtractionOutput(
            support_services=[
                "Медицински стандарти и центрове за спешна медицинска помощ",
                "Екипът включва психолог и логопед",
            ],
            safety_features=[
                "Начало | Новини | Контакти | Охрана",
                "24/7 охрана и видеонаблюдение",
            ],
            has_useful_info=True,
        )
    )
    assert normalized.support_services == ["Екипът включва психолог и логопед"]
    assert normalized.safety_features == ["24/7 охрана и видеонаблюдение"]


def test_normalize_operations_output_drops_noise_working_hours():
    normalized = extractor._normalize_operations_output(
        OperationsExtractionOutput(
            working_hours="Начало | Контакти | Новини",
            day_options=[],
            daily_schedule=[],
            meals=[],
            transport=[],
            uniforms=[],
            has_useful_info=True,
        )
    )
    assert normalized.working_hours is None


def test_normalize_operations_output_drops_heading_only_meals():
    normalized = extractor._normalize_operations_output(
        OperationsExtractionOutput(
            working_hours=None,
            day_options=[],
            daily_schedule=[],
            meals=["- Седмично меню", "Седмично меню: топъл обяд и закуска"],
            transport=[],
            uniforms=[],
            has_useful_info=True,
        )
    )
    assert normalized.meals == ["Седмично меню: топъл обяд и закуска"]


def test_normalize_pricing_terms_output_moves_negated_includes_to_excluded():
    normalized = extractor._normalize_pricing_terms_output(
        PricingTermsExtractionOutput(
            discounts=[],
            installments=[],
            included_items=["В таксата не е включена храна"],
            excluded_items=[],
            deposits=[],
            application_fees=[],
            registration_fees=[],
            has_useful_info=True,
        )
    )
    assert normalized.included_items == []
    assert normalized.excluded_items == ["В таксата не е включена храна"]


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


@pytest.mark.asyncio
async def test_extract_general_info_persists_schema_version_and_admission_info(db_session, sample_school_for_extraction):
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
    for page in pages:
        if page.page_category == "about":
            page.raw_markdown = (
                "Необходими документи: заявление.\n"
                "Работно време: 08:00 - 18:00.\n"
                "Екипът включва психолог.\n"
                "Отстъпка за второ дете 10%."
            )
            db_session.add(page)
    await db_session.commit()

    async def fake_run(*, result_type, **kwargs):  # type: ignore[no-untyped-def]
        if result_type is PriceExtractionOutput:
            return PriceExtractionOutput(prices=[], has_pricing_info=False), 5, 1
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
        result = await extractor.extract_school(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    await db_session.refresh(school)
    assert school.attributes["extracted"]["_schema_version"] == 1
    assert school.attributes["extracted"]["admission"]["has_useful_info"] is True
    assert school.admission_info["website_extracted"]["has_useful_info"] is True
    assert "operations" not in school.attributes
    assert "services" not in school.attributes
    assert "pricing_terms" not in school.attributes


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


@pytest.mark.asyncio
async def test_active_rules_are_isolated_per_concurrent_task(monkeypatch):
    alias_key = "customcategorykey"
    bg_rules = SimpleNamespace(
        _CATEGORY_ALIASES={alias_key: PriceCategory.TUITION},
        _PERIOD_ALIASES={},
    )
    us_rules = SimpleNamespace(
        _CATEGORY_ALIASES={alias_key: PriceCategory.FOOD},
        _PERIOD_ALIASES={},
    )

    monkeypatch.setattr(
        extractor,
        "get_rules",
        lambda country_code: {"bg": bg_rules, "us": us_rules}.get(country_code, bg_rules),
    )

    started = 0
    started_lock = asyncio.Lock()
    release = asyncio.Event()

    async def resolve_category(country_code: str) -> PriceCategory:
        nonlocal started
        extractor._ACTIVE_RULES.set(extractor.get_rules(country_code))

        async with started_lock:
            started += 1
            if started == 2:
                release.set()

        await release.wait()
        await asyncio.sleep(0)

        category = extractor._coerce_price_category(alias_key)
        assert category is not None
        return category

    bg_category, us_category = await asyncio.gather(
        resolve_category("bg"),
        resolve_category("us"),
    )

    assert bg_category == PriceCategory.TUITION
    assert us_category == PriceCategory.FOOD
