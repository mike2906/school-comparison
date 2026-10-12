"""Writing a Stage 6 validation result, or a validation failure, back to the school."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models.school import School
from app.schemas.validation import ValidationReport
from app.scrapers.campus_sync import apply_campus_sync, remove_website_campus_data
from app.scrapers.summary_pipeline import clear_summary_state
from app.scrapers.validation.report_entries import _now_iso
from app.utils.website_data import promote_validation_report, record_validation_failure

logger = logging.getLogger(__name__)


async def _persist_validation_report(
    db: AsyncSession,
    report: ValidationReport,
    school: School,
    attrs: dict[str, Any],
    admission_info: dict[str, Any],
    prior_validation: dict[str, Any],
) -> None:
    report.status = "needs_review" if report.issue_counts.get("error", 0) > 0 else "ok"
    report.validated_at = _now_iso()

    validation_payload = report.model_dump(mode="json", by_alias=True)
    # An unsampled deterministic rerun is not evidence that a capable-model
    # finding was resolved. Preserve the prior audit (and therefore its
    # actionable publish gates); only a new explicit spot check supersedes it.
    prior_spot_check = prior_validation.get("spot_check")
    if isinstance(prior_spot_check, dict):
        validation_payload["spot_check"] = prior_spot_check
    attrs = promote_validation_report(attrs, validation_payload)

    school.attributes = attrs
    school.admission_info = admission_info
    # UF42(b): campuses and stated age groups are created only as the marker
    # clears, in this transaction (apply_campus_sync re-checks the site).
    await apply_campus_sync(db, school)
    if report.auto_fixes:
        clear_summary_state(school, downgrade_status=True)
    school.updated_at = datetime.datetime.now(datetime.timezone.utc)
    flag_modified(school, "attributes")
    flag_modified(school, "admission_info")
    db.add(school)


async def _persist_validation_failure(
    db: AsyncSession, school_id: int, country_code: str, exc: Exception
) -> None:
    if db.in_transaction():
        await db.rollback()
    try:
        failed_school = await db.get(School, school_id)
        if failed_school is not None and failed_school.country_code == country_code:
            failed_school.attributes = record_validation_failure(
                failed_school.attributes,
                error=str(exc),
            )
            # The marker is set again: website-derived campuses stop publishing.
            await remove_website_campus_data(db, school_id)
            failed_school.updated_at = datetime.datetime.now(datetime.timezone.utc)
            flag_modified(failed_school, "attributes")
            db.add(failed_school)
            await db.commit()
    except Exception:
        logger.exception("Failed to persist validation-failure withholding for school %s", school_id)
        if db.in_transaction():
            await db.rollback()
