"""Tests for scraper extraction."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models import FieldSource, Pricing, School, SchoolLocation
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

    pricing_rows = (await db_session.execute(select(Pricing).where(Pricing.school_id == school.id))).scalars().all()
    assert len(pricing_rows) == 1

    await db_session.refresh(school)
    extracted = (school.attributes or {}).get("extracted", {})
    assert extracted.get("facilities") == ["pool"]
    assert extracted.get("summary_source", {}).get("teaching_approach") == ["project-based learning"]
    assert (school.attributes or {}).get("display_name_i18n") == {
        "bg": "Fusion School",
        "en": "Fusion School",
    }

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
        new=AsyncMock(side_effect=[(mock_price, 10, 2, 0.0), (mock_general, 20, 4, 0.001)]),
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
                prices=[ExtractedPrice(category="tuition", amount=500, currency="BGN", period="monthly")],
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


def test_normalize_display_name_i18n_rejects_generic_numbered_school_labels():
    normalized = extractor_module.helpers._normalize_display_name_i18n(
        {"bg": "131. средно училище"},
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
