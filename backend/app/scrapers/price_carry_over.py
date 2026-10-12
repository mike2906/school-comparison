"""Periods a re-read fee keeps from the row it replaces."""

from __future__ import annotations

from app.models.field_source import FieldSource
from app.models.pricing import Pricing
from app.models.school import School
from app.models.source_page import SourcePage
from app.scrapers.extractor_helpers import _normalize_academic_year
from app.scrapers.price_evidence import period_fits_category, states_another_unit
from app.utils.display_gating import blocked_pricing_row_ids, pricing_row_is_publishable


def carry_over_periods(
    school: School,
    pages: list[SourcePage],
    superseded: list[Pricing],
    proposed: list[Pricing],
    sources: list[FieldSource],
) -> None:
    """Give a re-read fee with no period the period its published row has.

    The text decides a new row's period, and some pages state it only where the reader
    cannot see it: 171's yearly fees were set by hand from "за учебната 2026 - 2027 г."
    pages above the amounts. The same fee read again keeps that period instead of
    holding the whole re-extraction, unless the page writes another unit after the
    amount (533's "≈ 950 EUR / за всички" was stored as monthly).
    """
    blocked = blocked_pricing_row_ids(school.attributes if isinstance(school.attributes, dict) else None)
    published = [
        row
        for row in superseded
        if row.id not in blocked and row.period is not None and row.amount is not None
        and pricing_row_is_publishable(row)
    ]
    texts = {page.id: page.raw_markdown for page in pages}
    for row, source in zip(proposed, sources, strict=True):
        if row.period is not None or row.amount is None:
            continue
        periods = {old.period for old in published if _same_fee(old, row)}
        if len(periods) != 1:
            continue
        period = periods.pop()
        if not period_fits_category(row.category.value, period.value) or states_another_unit(
            texts.get(row.source_page_id), row.amount
        ):
            continue
        row.period = period
        source.field_key = f"pricing.{row.category.value}.{period.value}"


def _same_fee(old: Pricing, new: Pricing) -> bool:
    """The same fee read again: same page, category, amount, currency and academic year."""
    return (
        old.source_page_id is not None
        and old.source_page_id == new.source_page_id
        and old.category == new.category
        and float(old.amount) == float(new.amount)
        and str(old.currency or "").upper() == str(new.currency or "").upper()
        and _year(old.academic_year) == _year(new.academic_year)
    )


def _year(value: str | None) -> str:
    raw = str(value or "").strip()
    return _normalize_academic_year(raw) or raw if raw else ""
