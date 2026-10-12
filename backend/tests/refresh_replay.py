"""Replay a recorded pricing answer through `_extract_prices` against stored pages and rows.

Fixtures live in ``tests/refresh_fixtures_20261012/<school_id>/``: the pages the extractor
read, the rows published before the refresh, and the model's recorded ``PageFees`` answer.
No model is called.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Pricing, School
from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.schemas.extraction import PageFees
from app.scrapers import extractor as extractor_module

FIXTURES = Path(__file__).parent / "refresh_fixtures_20261012"


def _load(school_id: int, name: str):
    return json.loads((FIXTURES / str(school_id) / name).read_text(encoding="utf-8"))


def recorded_fees(school_id: int) -> PageFees:
    (path,) = sorted((FIXTURES / str(school_id) / "recordings").glob("*.json"))
    return PageFees.model_validate(json.loads(path.read_text(encoding="utf-8"))["output"])


async def replay(db_session, school_id: int, education_level: str = "upper_secondary"):
    """Returns ``(result, rows)``: `_extract_prices`'s answer and the school's rows after it."""
    school = School(
        id=school_id,
        name_i18n={"bg": f"Училище {school_id}"},
        country_code="bg",
        city="sofia",
        school_type="private",
        education_level=education_level,
        website_url="https://example.bg/",
        scrape_status="navigated",
    )
    db_session.add(school)
    await db_session.flush()
    pages = []
    for page in _load(school_id, "pages.json"):
        pages.append(
            SourcePage(
                id=page["id"],
                school_id=school_id,
                source_url=page["url"],
                page_category=page["page_category"] or "pricing",
                scrape_type=ScrapeType.WEBSITE,
                is_valid=page["is_valid"],
                raw_markdown=page["raw_markdown"],
                content_hash=f"hash-{page['id']}",
                last_scraped_at=datetime.datetime.fromisoformat(page["last_scraped_at"]),
            )
        )
    db_session.add_all(pages)
    await db_session.flush()
    for row in _load(school_id, "pricing_rows.json"):
        db_session.add(
            Pricing(
                id=row["id"],
                school_id=school_id,
                category=PriceCategory(row["category"].lower()),
                amount=row["amount"],
                amount_min=row["amount_min"],
                amount_max=row["amount_max"],
                currency=row["currency"],
                period=PricePeriod(row["period"].lower()) if row["period"] else None,
                plan_name=row["plan_name"],
                age_group=row["age_group"],
                academic_year=row["academic_year"],
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=row["source_url"],
                source_page_id=row.get("source_page_id"),
                pricing_context=row["pricing_context"],
            )
        )
    await db_session.commit()
    await db_session.refresh(school)

    pages = extractor_module._freshest_url_variants(pages)
    answer = AsyncMock(return_value=(recorded_fees(school_id), 0, 0, 0.0))
    with patch("app.scrapers.extractor._run_typed_agent", new=answer):
        result = await extractor_module._extract_prices(
            db_session, school, pages, 60.0, extractor_module.ExtractionLLMStats()
        )
    await db_session.flush()
    rows = (
        (
            await db_session.execute(
                select(Pricing).where(Pricing.school_id == school_id).options(selectinload(Pricing.source_page))
            )
        )
        .scalars()
        .all()
    )
    return result, rows
