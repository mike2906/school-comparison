"""Stage 6 pricing-row checks: currency, amounts, ranges, source URLs and duplicates."""

from __future__ import annotations

import datetime
import json
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pricing import Pricing
from app.schemas.validation import ValidationReport
from app.scrapers.validation.report_entries import _add_fix, _add_issue, _to_json_value
from app.utils.display_gating import OFFER_ENDS_KEY


def _pricing_signature(row: Pricing) -> tuple[Any, ...]:
    context_json = json.dumps(_to_json_value(row.pricing_context or {}), sort_keys=True, ensure_ascii=True)
    return (
        row.school_id,
        row.age_group,
        row.category.value if hasattr(row.category, "value") else row.category,
        row.academic_year,
        row.amount,
        row.amount_min,
        row.amount_max,
        row.currency,
        row.period.value if hasattr(row.period, "value") else row.period,
        row.plan_name,
        row.source.value if hasattr(row.source, "value") else row.source,
        row.source_url,
        context_json,
    )


def _normalize_pricing_currency(report: ValidationReport, row: Pricing, row_prefix: str) -> None:
    normalized_currency = (row.currency or "").strip().upper()
    if len(normalized_currency) != 3 or not normalized_currency.isalpha():
        normalized_currency = "BGN"
    if row.currency != normalized_currency:
        _add_fix(
            report,
            code="pricing_currency_normalized",
            field_path=f"{row_prefix}.currency",
            original_value=row.currency,
            fixed_value=normalized_currency,
            reason="Currency codes must be 3-letter uppercase alphabetic values.",
        )
        row.currency = normalized_currency


def _check_pricing_amounts(report: ValidationReport, row: Pricing, row_prefix: str) -> None:
    for field_name in ("amount", "amount_min", "amount_max"):
        raw_value = getattr(row, field_name)
        if raw_value is None:
            continue
        try:
            numeric_value = float(raw_value)
        except (TypeError, ValueError):
            _add_issue(
                report,
                code="invalid_numeric_price_value",
                severity="error",
                field_path=f"{row_prefix}.{field_name}",
                message=f"{field_name} must be numeric when present.",
            )
            continue
        if numeric_value < 0:
            _add_issue(
                report,
                code="negative_price_amount",
                severity="error",
                field_path=f"{row_prefix}.{field_name}",
                message=f"{field_name} cannot be negative.",
            )


def _fix_swapped_pricing_range(report: ValidationReport, row: Pricing, row_prefix: str) -> None:
    if row.amount_min is not None and row.amount_max is not None:
        try:
            amount_min = float(row.amount_min)
            amount_max = float(row.amount_max)
        except (TypeError, ValueError):
            amount_min = None
            amount_max = None
        if amount_min is not None and amount_max is not None and amount_min > amount_max:
            original = {"amount_min": row.amount_min, "amount_max": row.amount_max}
            row.amount_min, row.amount_max = row.amount_max, row.amount_min
            _add_fix(
                report,
                code="pricing_range_swapped",
                field_path=row_prefix,
                original_value=original,
                fixed_value={"amount_min": row.amount_min, "amount_max": row.amount_max},
                reason="amount_min was greater than amount_max.",
            )


def _check_amount_within_range(report: ValidationReport, row: Pricing, row_prefix: str) -> None:
    if row.amount is not None and row.amount_min is not None and row.amount_max is not None:
        try:
            amount = float(row.amount)
            amount_min = float(row.amount_min)
            amount_max = float(row.amount_max)
        except (TypeError, ValueError):
            amount = None
            amount_min = None
            amount_max = None
        if amount is not None and amount_min is not None and amount_max is not None:
            if amount < amount_min or amount > amount_max:
                _add_issue(
                    report,
                    code="amount_outside_range",
                    severity="warning",
                    field_path=f"{row_prefix}.amount",
                    message="amount is outside the row's amount_min/amount_max range.",
                )


def _check_pricing_source_url(report: ValidationReport, row: Pricing, row_prefix: str) -> None:
    if not (row.source_url or "").strip():
        _add_issue(
            report,
            code="missing_pricing_source_url",
            severity="warning",
            field_path=f"{row_prefix}.source_url",
            message="Scraped pricing row is missing source_url.",
        )


def _check_pricing_rows(report: ValidationReport, pricing_rows: list[Pricing]) -> None:
    for row in pricing_rows:
        row_prefix = f"pricing[{row.id}]"
        _normalize_pricing_currency(report, row, row_prefix)
        _check_pricing_amounts(report, row, row_prefix)
        _fix_swapped_pricing_range(report, row, row_prefix)
        _check_amount_within_range(report, row, row_prefix)
        _check_pricing_source_url(report, row, row_prefix)


async def _remove_duplicate_pricing_rows(
    db: AsyncSession, report: ValidationReport, pricing_rows: list[Pricing]
) -> None:
    seen_signatures: dict[tuple[Any, ...], int] = {}
    duplicate_ids: list[tuple[int, int]] = []
    for row in pricing_rows:
        signature = _pricing_signature(row)
        existing = seen_signatures.get(signature)
        if existing is None:
            seen_signatures[signature] = row.id
            continue
        duplicate_ids.append((row.id, existing))

    if duplicate_ids:
        duplicate_id_set = {dup_id for dup_id, _ in duplicate_ids}
        duplicate_prefixes = tuple(f"pricing[{dup_id}]" for dup_id in duplicate_id_set)
        # Keep report trace focused on surviving rows plus explicit duplicate-removal entries.
        report.auto_fixes = [
            fix for fix in report.auto_fixes if not fix.field_path.startswith(duplicate_prefixes)
        ]
        report.issues = [
            issue for issue in report.issues if not issue.field_path.startswith(duplicate_prefixes)
        ]
        report.issue_counts = {
            severity: sum(1 for issue in report.issues if issue.severity == severity)
            for severity in ("error", "warning")
        }
        await db.execute(
            delete(Pricing).where(Pricing.id.in_([dup_id for dup_id, _ in duplicate_ids]))
        )
        for duplicate_id, keep_id in duplicate_ids:
            _add_fix(
                report,
                code="duplicate_pricing_row_removed",
                field_path=f"pricing[{duplicate_id}]",
                original_value={"id": duplicate_id, "duplicate_of": keep_id},
                fixed_value=None,
                reason="Removed exact duplicate scraped pricing row.",
            )


def store_offer_end(row: Pricing, offer_ends: datetime.date | None) -> None:
    """Keep the row's offer end date in step with its page, so the gate can expire it."""
    context = dict(row.pricing_context) if isinstance(row.pricing_context, dict) else {}
    stored = context.get(OFFER_ENDS_KEY)
    wanted = offer_ends.isoformat() if offer_ends else None
    if stored == wanted:
        return
    if wanted:
        context[OFFER_ENDS_KEY] = wanted
    else:
        context.pop(OFFER_ENDS_KEY, None)
    row.pricing_context = context
