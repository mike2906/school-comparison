"""Tests for scraper extraction v2."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import FieldSource, Pricing, School
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.extraction import (
    ExtractedLanguageFocus,
    ExtractedPrice,
    GeneralInfoExtractionOutput,
    PriceExtractionOutput,
)
from app.scrapers.v2 import extractor as extractor_v2


@pytest.fixture
async def sample_school_for_extraction_v2(db_session):
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
async def test_extract_school_v2_persists_pricing_and_general_info(db_session, sample_school_for_extraction_v2):
    school = sample_school_for_extraction_v2

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
        languages=[ExtractedLanguageFocus(language="English")],
        facilities=["pool"],
        programs=["STEM"],
        extracurricular=[],
        class_size="18 students",
        founded_year="1998",
        accreditations=[],
        has_useful_info=True,
    )

    with patch(
        "app.scrapers.v2.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 100, 10, 0.001), (mock_general, 150, 20, 0.002)]),
    ):
        result = await extractor_v2.extract_school_v2(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert result["pricing_count"] == 1
    assert result["general_info_success"] is True

    pricing_rows = (await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))).scalars().all()
    assert len(pricing_rows) == 1

    await db_session.refresh(school)
    extracted = (school.attributes or {}).get("extracted", {})
    assert extracted.get("facilities") == ["pool"]

    field_sources = (
        await db_session.execute(select(FieldSource).where(FieldSource.school_id == school.id))
    ).scalars().all()
    assert any(row.field_key == "attributes.facilities" for row in field_sources)


@pytest.mark.asyncio
async def test_extract_school_v2_uses_deterministic_fallback_when_general_llm_fails(
    db_session,
    sample_school_for_extraction_v2,
):
    school = sample_school_for_extraction_v2

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
        "app.scrapers.v2.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 100, 10, 0.001), (None, 0, 0, 0.0)]),
    ):
        result = await extractor_v2.extract_school_v2(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    assert result["general_info_success"] is True
    assert any("deterministic fallback" in detail for detail in result["details"])

    await db_session.refresh(school)
    extracted = (school.attributes or {}).get("extracted", {})
    assert extracted.get("languages"), "deterministic fallback should preserve at least detected languages"


@pytest.mark.asyncio
async def test_extract_school_v2_pricing_drops_oversized_text_fields(
    db_session,
    sample_school_for_extraction_v2,
):
    school = sample_school_for_extraction_v2

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
        "app.scrapers.v2.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 100, 10, 0.001), (mock_general, 20, 3, 0.001)]),
    ):
        result = await extractor_v2.extract_school_v2(db_session, school.id, "bg")

    assert result["status"] == "extracted"

    row = (
        await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))
    ).scalars().one()
    assert row.age_group is None
    assert row.plan_name is None
    assert row.academic_year is None


@pytest.mark.asyncio
async def test_extract_school_v2_augments_general_info_with_deterministic_signals(
    db_session,
    sample_school_for_extraction_v2,
):
    school = sample_school_for_extraction_v2

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
        "app.scrapers.v2.extractor._run_typed_agent",
        new=AsyncMock(side_effect=[(mock_price, 10, 2, 0.0), (llm_general, 20, 4, 0.001)]),
    ):
        result = await extractor_v2.extract_school_v2(db_session, school.id, "bg")

    assert result["status"] == "extracted"
    await db_session.refresh(school)
    extracted = (school.attributes or {}).get("extracted", {})
    languages = extracted.get("languages", [])
    assert any((entry.get("language") or "").lower() == "english" for entry in languages)


@pytest.mark.asyncio
async def test_run_typed_agent_model_retry_recovers(monkeypatch):
    llm_stats = extractor_v2.ExtractionV2LLMStats()

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
                prices=[ExtractedPrice(category="tuition", amount=500, currency="BGN", period="monthly")],
                has_pricing_info=True,
            )
            return FakeAgentResult(good)

    monkeypatch.setattr(extractor_v2, "Agent", FakeAgent)
    monkeypatch.setattr(extractor_v2, "_build_openrouter_model", lambda: object())

    parsed, input_tokens, output_tokens, token_cost_usd = await extractor_v2._run_typed_agent(
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


def test_extract_openrouter_cost_usd_from_provider_details():
    message_a = SimpleNamespace(provider_details={"cost": 0.001})
    message_b = SimpleNamespace(provider_details={"cost": 0.0025})
    message_c = SimpleNamespace(provider_details={"ignored": True})
    result = SimpleNamespace(
        all_messages=lambda: [message_a, message_b, message_c],
        response=SimpleNamespace(provider_details={"cost": 0.5}),
    )

    cost = extractor_v2._extract_openrouter_cost_usd(result)
    assert cost == 0.0035


def test_extract_openrouter_cost_usd_falls_back_to_response_details():
    result = SimpleNamespace(
        all_messages=lambda: [],
        response=SimpleNamespace(provider_details={"cost": "0.004"}),
    )
    cost = extractor_v2._extract_openrouter_cost_usd(result)
    assert cost == 0.004


def test_build_openrouter_model_settings_defaults(monkeypatch):
    monkeypatch.setattr(
        extractor_v2,
        "get_settings",
        lambda: SimpleNamespace(
            extraction_temperature=0.1,
            extraction_openrouter_models="",
            extraction_openrouter_provider_order="",
            extraction_openrouter_provider_allow_fallbacks=True,
            extraction_openrouter_provider_sort="",
        ),
    )
    monkeypatch.setattr(extractor_v2, "get_model", lambda _tier: "openrouter/google/gemini-2.5-flash-lite")

    model_settings = extractor_v2._build_openrouter_model_settings()

    assert model_settings["temperature"] == 0.1
    assert model_settings["openrouter_usage"] == {"include": True}
    assert "openrouter_models" not in model_settings
    assert "openrouter_provider" not in model_settings


def test_build_openrouter_model_settings_with_routing(monkeypatch):
    monkeypatch.setattr(
        extractor_v2,
        "get_settings",
        lambda: SimpleNamespace(
            extraction_temperature=0.0,
            extraction_openrouter_models="openrouter/openai/gpt-4o-mini, google/gemini-2.5-flash-lite,anthropic/claude-3.5-haiku",
            extraction_openrouter_provider_order="openai, anthropic",
            extraction_openrouter_provider_allow_fallbacks=False,
            extraction_openrouter_provider_sort="latency",
        ),
    )
    monkeypatch.setattr(extractor_v2, "get_model", lambda _tier: "openrouter/google/gemini-2.5-flash-lite")

    model_settings = extractor_v2._build_openrouter_model_settings()

    assert model_settings["openrouter_models"] == [
        "openai/gpt-4o-mini",
        "anthropic/claude-3.5-haiku",
    ]
    assert model_settings["openrouter_provider"] == {
        "order": ["openai", "anthropic"],
        "allow_fallbacks": False,
        "sort": "latency",
    }
