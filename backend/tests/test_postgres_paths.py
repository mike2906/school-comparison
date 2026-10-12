"""DB-facing paths run on PostgreSQL (Lane D R3a).

The rest of the suite runs on in-memory SQLite. These tests cover the queries and
writes whose behaviour depends on the engine: JSON path predicates, JSON text matching,
transaction rollback and JSON persistence. They need ``POSTGRES_TEST_DATABASE_URL``
pointing at a database migrated to head (see ``tests/conftest.py``); CI's Backend job
provides one.
"""

from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Pricing, School
from app.models.country import Country
from app.models.field_source import FieldSource, SourceConfidence, SourceType
from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.models.school import SchoolLocation, SchoolLocationAgeGroupShift
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool
from app.scrapers import extractor as extractor_module
from app.scrapers import validator as validator_module
from app.scrapers.sources.base_adapter import BaseSourceAdapter

pytestmark = pytest.mark.postgres


async def _fresh_read(async_engine, model, row_id):
    """Read a row back on a new connection, so nothing comes from the session's cache."""
    async with async_sessionmaker(async_engine, class_=AsyncSession)() as session:
        return await session.get(model, row_id)


async def _country(db_session):
    db_session.add(
        Country(
            code="bg",
            name_i18n={"bg": "България", "en": "Bulgaria"},
            education_config={},
            map_config={},
            supported_languages=["bg", "en"],
            default_language="bg",
            default_currency="EUR",
        )
    )
    await db_session.flush()


def _school(name, **kwargs):
    return School(
        name_i18n={"bg": name, "en": name},
        country_code="bg",
        school_type=kwargs.pop("school_type", "state"),
        education_level=kwargs.pop("education_level", "primary"),
        city=kwargs.pop("city", "sofia"),
        **kwargs,
    )


async def _with_location(db_session, school, *, lat, lng, geocode_meta=None):
    db_session.add(school)
    await db_session.flush()
    location = SchoolLocation(
        school_id=school.id,
        address_i18n={"bg": f"адрес {school.id}"},
        lat=lat,
        lng=lng,
        geocode_meta=geocode_meta or {},
        is_primary=True,
    )
    db_session.add(location)
    await db_session.flush()
    db_session.add(SchoolLocationAgeGroupShift(location_id=location.id, age_group="grade_1_4", shift="morning"))
    return school


async def test_list_endpoint_location_and_city_clauses(seeded_db, client):
    """`SchoolService`'s JSON path predicates (`geocode_meta["status"].as_string()`)."""
    seeded = {school.id for school in (await seeded_db.execute(select(School))).scalars()}
    terminal = await _with_location(
        seeded_db,
        _school("Без координати"),
        lat=None,
        lng=None,
        geocode_meta={"status": "failed", "provider": "nominatim", "rejection_reason": "No results found"},
    )
    # Unresolved without durable failure evidence: not listable.
    await _with_location(seeded_db, _school("Непроверено"), lat=None, lng=None, geocode_meta={"status": "pending"})
    await _with_location(
        seeded_db,
        _school("Отхвърлено без причина"),
        lat=None,
        lng=None,
        geocode_meta={"status": "rejected", "provider": "nominatim", "rejection_reason": "  "},
    )
    # A Sofia school pinned in Plovdiv: the bounds clause, not the city one, drops it.
    misplaced = await _with_location(seeded_db, _school("Грешна точка"), lat=42.1354, lng=24.7453)
    # A Plovdiv school: the city clause drops it.
    plovdiv = await _with_location(seeded_db, _school("Пловдивско", city="plovdiv"), lat=42.1354, lng=24.7453)
    await seeded_db.commit()

    sofia = await client.get("/schools", params={"city": "sofia"})
    assert sofia.status_code == 200
    assert {school["id"] for school in sofia.json()} == seeded | {terminal.id}

    country_wide = await client.get("/schools", params={"city": "all"})
    assert {school["id"] for school in country_wide.json()} == seeded | {terminal.id, misplaced.id, plovdiv.id}

    # Same clauses through the counts query: the seeded primary school plus the terminal one.
    counts = await client.get("/schools/counts", params={"city": "sofia"})
    assert counts.json()["grade_1_4"] == 2


class _Adapter(BaseSourceAdapter):
    ADAPTER_NAME = "postgres-test"
    COUNTRY_CODE = "bg"
    CITY = "sofia"

    async def discover(self, limit=None, sample_ratio=0.0):
        return []


async def test_adapter_falls_back_to_name_i18n_match(db_session):
    """`School.name_i18n["bg"].as_string() == name` matches unquoted text on Postgres."""
    await _country(db_session)
    name = 'ДГ № 5 „Слънце" \\ "Звезда"'
    existing = _school(name, education_level="kindergarten")
    db_session.add(existing)
    await db_session.flush()
    db_session.add(
        SchoolLocation(school_id=existing.id, address_i18n={"bg": "ул. Тест 1"}, district="Лозенец", is_primary=True)
    )
    await db_session.commit()

    result = await _Adapter(db=db_session).upsert_schools(
        [
            DiscoveredSchool(
                name_i18n={"bg": name},
                country_code="bg",
                city="sofia",
                school_type="state",
                education_level="kindergarten",
                locations=[DiscoveredLocation(address_i18n={"bg": "ул. Тест 1"}, district="Лозенец")],
            )
        ]
    )

    assert (result["created"], result["updated"]) == (0, 1)
    assert len((await db_session.execute(select(School))).scalars().all()) == 1


async def _published_school_with_pages(db_session):
    await _country(db_session)
    school = _school(
        "Частно училище",
        school_type="private",
        website_url="https://pg-test.bg",
        scrape_status="navigated",
        attributes={
            "extracted": {"facilities": ["pool"]},
            "data_validation": {
                "_schema_version": 1,
                "validated_at": "2026-09-01T00:00:00+00:00",
                "status": "ok",
                "issue_counts": {"error": 0, "warning": 0},
                "issues": [],
                "auto_fixes": [],
            },
        },
    )
    db_session.add(school)
    await db_session.flush()
    page = SourcePage(
        school_id=school.id,
        source_url="https://pg-test.bg/prices",
        page_category="pricing",
        scrape_type=ScrapeType.WEBSITE,
        is_valid=True,
        raw_markdown="Годишна такса 1-4 клас: 7500 € годишно.",
        content_hash="pg-hash",
        last_scraped_at=datetime.datetime.now(datetime.UTC),
    )
    db_session.add(page)
    await db_session.flush()
    db_session.add(
        FieldSource(
            school_id=school.id,
            category="attributes",
            field_key="attributes.facilities",
            value_text="pool",
            source_type=SourceType.SCRAPED_WEBSITE,
            source_url="https://pg-test.bg/about",
            scraped_at=datetime.datetime(2026, 9, 1),
            confidence=SourceConfidence.HIGH,
        )
    )
    row = Pricing(
        school_id=school.id,
        category=PriceCategory.TUITION,
        amount=7500,
        currency="EUR",
        period=None,
        plan_name="Годишна такса 1-4 клас",
        source=PriceSource.SCRAPED_WEBSITE,
        source_url=page.source_url,
        source_page_id=page.id,
        pricing_context={"confidence": 1.0},
    )
    db_session.add(row)
    await db_session.commit()
    return school, row


async def _failed_llm_call(**kwargs):
    kwargs["llm_stats"].total_calls += 1
    kwargs["llm_stats"].hard_failures += 1
    return None, 0, 0, 0.0


async def test_failed_extraction_rolls_back_and_keeps_published_data(db_session, async_engine):
    school, row = await _published_school_with_pages(db_session)
    school_id, row_id = school.id, row.id  # the rollback expires the session's objects
    before = (await _fresh_read(async_engine, School, school_id)).attributes

    real_extract_prices = extractor_module._extract_prices
    written = []

    async def _spy_extract_prices(*args, **kwargs):
        price_result = await real_extract_prices(*args, **kwargs)
        written.append(price_result.get("count", 0))
        return price_result

    with (
        patch("app.scrapers.extractor._run_typed_agent", new=_failed_llm_call),
        patch("app.scrapers.extractor._extract_prices", new=_spy_extract_prices),
    ):
        result = await extractor_module.extract_school(db_session, school_id, "bg")
    assert result["status"] == "extraction_failed"
    assert written and written[0] > 0, "the fallback must write rows for the rollback to undo"

    stored = await _fresh_read(async_engine, School, school_id)
    assert stored.scrape_status == "extracted"
    assert stored.attributes == before
    async with async_sessionmaker(async_engine, class_=AsyncSession)() as session:
        rows = (await session.execute(select(Pricing).where(Pricing.school_id == school_id))).scalars().all()
    # The deterministic fallback's rows were rolled back; the published row is untouched.
    assert [(r.id, r.amount, r.period) for r in rows] == [(row_id, 7500, None)]


async def test_validation_report_persists(db_session, async_engine):
    school, row = await _published_school_with_pages(db_session)
    school.scrape_status = "extracted"
    await db_session.commit()

    result = await validator_module.validate_school_data(db_session, school.id, "bg")
    assert result["status"] in {"ok", "needs_review"}

    stored = await _fresh_read(async_engine, School, school.id)
    report = stored.attributes["data_validation"]
    assert report["_schema_version"] == 1
    assert report["validated_at"] != "2026-09-01T00:00:00+00:00"
    # The replaced report went to history, as JSON.
    assert [old["validated_at"] for old in stored.attributes["data_validation_history"]] == [
        "2026-09-01T00:00:00+00:00"
    ]
    # The period the page states was filled in on the row.
    stored_row = await _fresh_read(async_engine, Pricing, row.id)
    assert stored_row.period == PricePeriod.YEARLY
