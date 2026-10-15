"""scripts/restore_school_prices.py puts one school's website price rows back from a dump."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import FieldSource, Pricing, School
from app.models.country import Country
from app.models.field_source import SourceType
from app.models.pricing import PriceCategory, PriceSource
from scripts.restore_school_prices import copy_blocks, restore

pytestmark = pytest.mark.postgres


def _school(name):
    return School(
        name_i18n={"bg": name, "en": name},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
    )


def _price(school, amount, source=PriceSource.SCRAPED_WEBSITE):
    return Pricing(school_id=school.id, category=PriceCategory.TUITION, amount=amount, currency="EUR", source=source)


def _evidence(school, note):
    return FieldSource(
        school_id=school.id, category="pricing", field_key="pricing", source_type=SourceType.SCRAPED_WEBSITE, notes=note
    )


async def test_one_schools_website_prices_come_back_from_the_dump(async_engine, db_session, tmp_path):
    if not shutil.which("pg_dump"):
        pytest.skip("pg_dump is not installed")
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
    school, other = _school("Re-read"), _school("Untouched")
    db_session.add_all([school, other])
    await db_session.flush()
    good = [_price(school, 8950), _price(school, 9050)]
    official = _price(school, 100, PriceSource.OFFICIAL)
    db_session.add_all([*good, official, _price(other, 500), _evidence(school, "good")])
    await db_session.commit()
    good_ids = sorted(row.id for row in good)

    url = os.environ["POSTGRES_TEST_DATABASE_URL"].replace("postgresql+asyncpg", "postgresql")
    dump = tmp_path / "launch.dump"
    subprocess.run(["pg_dump", "--format=custom", "-f", str(dump), url], check=True)

    # The worse re-read: the good rows and their evidence replaced, and the other school's fee changed.
    for row in good:
        await db_session.delete(row)
    rows = (await db_session.execute(select(FieldSource))).scalars().all()
    for row in rows:
        await db_session.delete(row)
    db_session.add_all([_price(school, 1), _evidence(school, "bad"), _price(other, 600)])
    await db_session.commit()

    blocks = copy_blocks(dump, ["pg_restore"])
    maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    await restore(school.id, blocks, apply=False, session_maker=maker)
    async with maker() as db:
        amounts = (await db.execute(select(Pricing.amount).where(Pricing.school_id == school.id))).scalars().all()
    assert sorted(amounts) == [1, 100]  # a dry run writes nothing

    await restore(school.id, blocks, apply=True, session_maker=maker)
    async with maker() as db:
        rows = (await db.execute(select(Pricing).where(Pricing.school_id == school.id))).scalars().all()
        website = sorted(row.id for row in rows if row.source is PriceSource.SCRAPED_WEBSITE)
        notes = (await db.execute(select(FieldSource.notes).where(FieldSource.school_id == school.id))).scalars().all()
        others = (await db.execute(select(Pricing.amount).where(Pricing.school_id == other.id))).scalars().all()
    assert website == good_ids
    assert any(row.source is PriceSource.OFFICIAL and row.amount == 100 for row in rows)
    assert notes == ["good"]
    assert sorted(others) == [500, 600]
