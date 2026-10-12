"""Price rows a failed extraction keeps published must still be on their pages (R2b).

Navigation overwrites a ``SourcePage``'s markdown in place, and price rows keep pointing
at the same page. A successful extraction re-validates before it commits; a failed one
leaves the school publishing on the strength of the old report
(``extractor.keep_previous_website_data``, or a prices-only run that changes nothing).
This re-runs the evidence check over the stored rows against the pages' current text and
records an error for each row the text no longer supports, under
``data_validation.kept_price_issues``, so the display gate withholds it.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Pricing, School
from app.models.pricing import PriceSource
from app.models.source_page import SourcePage
from app.schemas.validation import ValidationReport
from app.scrapers.validator import _check_price_evidence
from app.utils.display_gating import KEPT_PRICE_ISSUES_KEY
from app.utils.website_data import website_data_is_publishable

logger = logging.getLogger(__name__)


async def _kept_price_issues(db: AsyncSession, school: School) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(Pricing)
            .where(Pricing.school_id == school.id, Pricing.source == PriceSource.SCRAPED_WEBSITE)
            .order_by(Pricing.id)
        )
    ).scalars().all()
    if not rows:
        return []
    try:
        scratch = ValidationReport(validated_at="", status="ok", issue_counts={"error": 0, "warning": 0})
        await _check_price_evidence(db, scratch, school, list(rows), apply_fixes=False)
        found = [issue.model_dump(mode="json") for issue in scratch.issues if issue.severity == "error"]
        # The evidence check skips a row whose page has no text (validation re-extracts it
        # anyway); here an empty re-crawl is exactly the page no longer showing the price.
        page_ids = {row.source_page_id for row in rows if row.source_page_id is not None}
        pages = dict(
            (
                await db.execute(
                    select(SourcePage.id, SourcePage.raw_markdown).where(
                        SourcePage.id.in_(page_ids), SourcePage.is_valid.is_(True)
                    )
                )
            ).all()
        ) if page_ids else {}
        found += [
            {
                "code": "pricing_current_page_empty",
                "severity": "error",
                "field_path": f"pricing[{row.id}]",
                "message": "the linked page has no text after the re-crawl",
            }
            for row in rows
            if row.source_page_id in pages and not (pages[row.source_page_id] or "").strip()
        ]
    except Exception:
        # Fail closed: the rows can't be shown to be on their pages.
        logger.exception("Kept-price check failed for school %s; withholding its scraped prices", school.id)
        found = [
            {
                "code": "pricing_kept_check_failed",
                "severity": "error",
                "field_path": f"pricing[{row.id}]",
                "message": "the check against the page's current text failed",
            }
            for row in rows
        ]
    issues: dict[tuple[str, str], dict[str, Any]] = {}
    for issue in found:
        issues.setdefault((issue["code"], issue["field_path"]), issue)
    return list(issues.values())


async def withhold_unsupported_kept_prices(db: AsyncSession, school: School) -> None:
    """Replace the school's kept-price issues with a fresh check (does not commit).

    The validation report's own issues, status and dates are left as they are.
    """
    attrs: dict[str, Any] = dict(school.attributes) if isinstance(school.attributes, dict) else {}
    current = attrs.get("data_validation")
    if not isinstance(current, dict):
        return
    issues = await _kept_price_issues(db, school)
    if issues == (current.get(KEPT_PRICE_ISSUES_KEY) or []):
        return
    report = dict(current)
    if issues:
        report[KEPT_PRICE_ISSUES_KEY] = issues
    else:
        report.pop(KEPT_PRICE_ISSUES_KEY, None)
    attrs["data_validation"] = report
    school.attributes = attrs
    flag_modified(school, "attributes")


async def recheck_after_failed_price_refresh(db: AsyncSession, school_id: int) -> None:
    """A prices-only run that changed nothing: re-check the kept rows and commit."""
    school = await db.get(School, school_id, populate_existing=True)
    if school is not None and website_data_is_publishable(school.attributes, school.scrape_status):
        await withhold_unsupported_kept_prices(db, school)
        await db.commit()
