"""Refresh one school's pricing evidence from the live website in the isolated DB.

This is intentionally an operational tool for the 2026 pricing review, not a general
pipeline feature. It refuses to run outside the named isolated database, bypasses the
Crawl4AI cache, and replaces existing website evidence only after a conservative
completeness check succeeds.

Run from ``backend/``::

    uv run python scripts/refresh_pricing_evidence.py --school-id 171
"""

from __future__ import annotations

import argparse
import asyncio
import json
from math import ceil
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.models import School, ScrapeType, SourcePage
from app.scrapers.navigator import WebsiteNavigator, _persist_navigation_result
from app.scrapers.url_validator import URLValidator

ISOLATED_DATABASE_NAME = "sofia_schools_pricing_refresh_20260915"
MIN_PAGE_RETENTION_RATIO = 0.80
GOOGLE_SITES_HOST = "sites.google.com"
GOOGLE_SITES_TENANT_PREFIXES = {"site", "view"}


class RefreshRejected(RuntimeError):
    """The live result was unsafe to use as replacement evidence."""


def required_live_page_count(existing_count: int) -> int:
    """Return the conservative minimum useful-page count for a replacement crawl."""
    if existing_count <= 1:
        return 1
    return max(2, ceil(existing_count * MIN_PAGE_RETENTION_RATIO))


def _site_identity(url: str) -> tuple[str, ...] | None:
    """Return the conservative site identity used by this operational refresh."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None

    if host != GOOGLE_SITES_HOST:
        return (host,)

    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0] not in GOOGLE_SITES_TENANT_PREFIXES:
        return None
    return (host, path_parts[0], path_parts[1])


def _has_same_site_identity(url: str, canonical_site_url: str) -> bool:
    identity = _site_identity(url)
    return identity is not None and identity == _site_identity(canonical_site_url)


def _stored_page_is_useful(page: SourcePage, navigator: WebsiteNavigator) -> bool:
    markdown = (page.raw_markdown or "").strip()
    return bool(
        page.is_valid
        and markdown
        and not navigator._is_bot_challenge_url(page.source_url)
        and not navigator._is_bot_protection_content(markdown)
    )


async def _current_database_name(db: AsyncSession) -> str:
    result = await db.execute(text("SELECT current_database()"))
    return str(result.scalar_one())


async def refresh_school_pricing_evidence(
    db: AsyncSession,
    *,
    school_id: int,
) -> dict[str, Any]:
    """Fetch, validate, and atomically replace one school's website evidence."""
    database_name = await _current_database_name(db)
    if database_name != ISOLATED_DATABASE_NAME:
        raise RefreshRejected(
            "Refusing to refresh outside the isolated database: "
            f"expected {ISOLATED_DATABASE_NAME!r}, got {database_name!r}"
        )

    school_result = await db.execute(select(School).where(School.id == school_id))
    school = school_result.scalar_one_or_none()
    if school is None:
        raise RefreshRejected(f"School {school_id} was not found")
    if not school.website_url:
        raise RefreshRejected(f"School {school_id} has no website URL")

    validator = URLValidator(country_code=school.country_code)
    normalized_url = validator.normalize_url(school.website_url)
    if not normalized_url:
        raise RefreshRejected(f"School {school_id} has an invalid website URL")

    navigator = WebsiteNavigator(
        country_code=school.country_code,
        bypass_cache=True,
    )
    existing_result = await db.execute(
        select(SourcePage).where(
            SourcePage.school_id == school_id,
            SourcePage.scrape_type == ScrapeType.WEBSITE,
        )
    )
    existing_pages = existing_result.scalars().all()
    useful_existing = [
        page for page in existing_pages if _stored_page_is_useful(page, navigator)
    ]

    final_url, live_pages = await navigator.discover_pages(normalized_url)
    cache_hits = sum(
        (page.cache_status or "").lower().startswith("hit") for page in live_pages
    )
    if cache_hits:
        raise RefreshRejected(
            f"Live crawl unexpectedly reported {cache_hits} cache hit(s)"
        )

    if not _has_same_site_identity(final_url, normalized_url):
        raise RefreshRejected(
            f"Live crawl redirected off the school site: {final_url}"
        )

    useful_live = [
        page
        for page in live_pages
        if navigator._is_extractable_page_content(page)
        and _has_same_site_identity(page.url, normalized_url)
    ]
    required_count = required_live_page_count(len(useful_existing))
    if len(useful_live) < required_count:
        raise RefreshRejected(
            "Live crawl appears incomplete: "
            f"{len(useful_live)} useful page(s), requires at least {required_count} "
            f"from a baseline of {len(useful_existing)}"
        )

    baseline_has_pricing = any(
        page.page_category == "pricing" for page in useful_existing
    )
    live_has_pricing = any(page.category == "pricing" for page in useful_live)
    if baseline_has_pricing and not live_has_pricing:
        raise RefreshRejected(
            "Live crawl lost the pricing page present in the existing evidence"
        )

    await db.execute(
        delete(SourcePage).where(
            SourcePage.school_id == school_id,
            SourcePage.scrape_type == ScrapeType.WEBSITE,
        )
    )
    result = await _persist_navigation_result(
        db=db,
        school=school,
        normalized_url=normalized_url,
        final_url=final_url,
        pages=useful_live,
        validator=validator,
        navigator=navigator,
    )
    if not result.get("success"):
        raise RefreshRejected(
            f"Navigation persistence failed: {result.get('reason', 'unknown reason')}"
        )

    return {
        **result,
        "database": database_name,
        "baseline_useful_pages": len(useful_existing),
        "minimum_required_pages": required_count,
        "fresh_useful_pages": len(useful_live),
    }


async def _run(school_id: int) -> int:
    async with async_session_maker() as db:
        try:
            result = await refresh_school_pricing_evidence(
                db,
                school_id=school_id,
            )
        except Exception as exc:
            await db.rollback()
            print(json.dumps({"success": False, "reason": str(exc)}, ensure_ascii=False))
            return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--school-id", type=int, required=True)
    args = parser.parse_args()
    return asyncio.run(_run(args.school_id))


if __name__ == "__main__":
    raise SystemExit(main())
