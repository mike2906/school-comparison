"""Thin orchestration helpers for scraper stages."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .extractor import extract_school
from .navigator import navigate_school


async def run_school_pipeline(
    db: AsyncSession,
    school_id: int,
    country_code: str = "bg",
) -> dict[str, Any]:
    """Run validate->navigate->extract equivalent for canonical stage scope."""
    nav = await navigate_school(db=db, school_id=school_id, country_code=country_code)
    if not nav.get("success"):
        return {
            "school_id": school_id,
            "navigate": nav,
            "extract": None,
            "success": False,
        }

    ext = await extract_school(db=db, school_id=school_id, country_code=country_code)
    return {
        "school_id": school_id,
        "navigate": nav,
        "extract": ext,
        "success": bool(ext.get("status") == "extracted"),
    }
