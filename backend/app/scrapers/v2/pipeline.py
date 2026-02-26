"""Thin orchestration helpers for scraper v2."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.scrapers.v2.extractor import extract_school_v2
from app.scrapers.v2.navigator import navigate_school_v2


async def run_school_pipeline_v2(
    db: AsyncSession,
    school_id: int,
    country_code: str = "bg",
) -> dict[str, Any]:
    """Run validate->navigate->extract equivalent for v2 stage scope.

    Stage 2/3 remain unchanged externally; this helper only orchestrates
    Stage 4+5 v2 steps for one school.
    """
    nav = await navigate_school_v2(db=db, school_id=school_id, country_code=country_code)
    if not nav.get("success"):
        return {
            "school_id": school_id,
            "navigate": nav,
            "extract": None,
            "success": False,
        }

    ext = await extract_school_v2(db=db, school_id=school_id, country_code=country_code)
    return {
        "school_id": school_id,
        "navigate": nav,
        "extract": ext,
        "success": bool(ext.get("status") == "extracted"),
    }
