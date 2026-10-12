"""The T6 offline extraction diff: dump reads stored pages, compare flags any change."""

from __future__ import annotations

import datetime
import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import School
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from scripts import extraction_diff


@pytest.mark.asyncio
async def test_dump_then_compare(db_session, async_engine, tmp_path, monkeypatch):
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
    db_session.add(
        SourcePage(
            school_id=school.id,
            source_url="https://test-school.bg/prices",
            page_category="pricing",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Такса за обучение: 1000 лв. на месец.",
            content_hash="hash1",
            last_scraped_at=datetime.datetime.now(datetime.UTC),
        )
    )
    # An older copy of the same page: extract_school reads only the freshest one.
    db_session.add(
        SourcePage(
            school_id=school.id,
            source_url="https://test-school.bg/prices/",
            page_category="pricing",
            scrape_type=ScrapeType.WEBSITE,
            is_valid=True,
            raw_markdown="Такса за обучение: 700 лв. на месец.",
            content_hash="hash0",
            last_scraped_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=90),
        )
    )
    await db_session.commit()

    seen_urls = []
    real_run = extraction_diff.run_deterministic_extraction

    def spy(school, pages):
        seen_urls.extend(page.source_url for page in pages)
        return real_run(school, pages)

    monkeypatch.setattr(extraction_diff, "run_deterministic_extraction", spy)

    before = tmp_path / "before.jsonl"
    maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)
    await extraction_diff.dump(before, session_maker=maker)

    lines = before.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["school_id"] == school.id
    assert set(row["result"]) == {"pricing", "general_info", "display_name_evidence"}
    assert seen_urls == ["https://test-school.bg/prices"]

    assert extraction_diff.compare(before, before) == 0

    changed = dict(row, result=dict(row["result"], pricing=[{"amount": "1"}]))
    after = tmp_path / "after.jsonl"
    after.write_text(json.dumps(changed) + "\n", encoding="utf-8")
    assert extraction_diff.compare(before, after) == 1
