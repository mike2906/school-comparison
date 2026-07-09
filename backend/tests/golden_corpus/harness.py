"""Fixture builders for the golden-fixture corpus (P1.5).

Turns a committed ``case.json`` + ``pageNN.md`` fixture into transient
(un-persisted) ORM objects and runs the shared deterministic extraction path
(``app.scrapers.deterministic.run_deterministic_extraction``). The extraction
logic itself lives in the app and is exercised by production; this module only
constructs the inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.school import School
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.scrapers.deterministic import run_deterministic_extraction

__all__ = ["FixturePage", "build_school", "build_pages", "run_deterministic_extraction"]


@dataclass
class FixturePage:
    """Lightweight stand-in for a persisted ``SourcePage`` row."""

    id: int
    source_url: str
    page_category: str | None
    raw_markdown: str


def build_school(case: dict[str, Any]) -> School:
    """Build a transient (un-persisted) School from fixture metadata."""
    attributes: dict[str, Any] = {}
    if case.get("name_aliases"):
        attributes["name_aliases"] = list(case["name_aliases"])
    return School(
        id=case["school_id"],
        name_i18n=case.get("name_i18n") or {},
        school_type=case.get("school_type"),
        country_code=case.get("country_code") or "bg",
        website_url=case.get("website_url"),
        attributes=attributes,
    )


def build_pages(pages: list[FixturePage]) -> list[SourcePage]:
    """Build transient SourcePage rows the deterministic helpers can read."""
    return [
        SourcePage(
            id=page.id,
            scrape_type=ScrapeType.WEBSITE,
            source_url=page.source_url,
            content_hash="0" * 64,
            page_category=page.page_category,
            raw_markdown=page.raw_markdown,
            is_valid=True,
        )
        for page in pages
    ]
