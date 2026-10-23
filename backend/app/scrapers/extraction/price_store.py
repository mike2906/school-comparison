"""Storing a school's fee rows: field coercion, which stored rows a read replaces, the
rule-6 hold, and the write. Used by the fees agent (``stages/fees_agent.py``)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.field_source import FieldSource, SourceType
from app.models.pricing import PriceSource, Pricing
from app.models.school import School
from app.models.source_page import SourcePage
from app.schemas.extraction import ExtractedPrice
from app.scrapers.extraction import llm_results, text_values
from app.scrapers.price_evidence import PriceRow, replacement_regressions
from app.scrapers.shared_site_check import level_family
from app.utils.academic_year import normalize_academic_year
from app.utils.display_gating import blocked_pricing_row_ids, pricing_row_is_publishable

# UF45 rule 6: why the last re-read's fees were not written (internal only).
PRICING_HOLD_KEY = "pricing_hold"


def fee_label(text: str | None, max_len: int, *, cut: bool = False) -> str | None:
    """A fee label kept whole; one over the length is cut when asked, else dropped.

    The general text cleaner keeps the first of a comma-separated list, which cut
    "Детска Ясла ,,Йор Кидс“ / Целодневен престой" down to its heading (542).
    """
    label = " ".join(str(text or "").split())
    if cut and len(label) > max_len:
        label = label[:max_len].rsplit(" ", 1)[0].strip(" /")
    return text_values._sanitize_label(label, max_len=max_len)


def normalized_price_fields(extracted: ExtractedPrice) -> dict[str, Any] | None:
    """Coerce one ``ExtractedPrice`` into normalized persisted-row field values.

    Returns ``None`` when the row cannot be persisted (unknown category/period or
    no amount).
    """
    category = llm_results._coerce_price_category(extracted.category)
    period = llm_results._coerce_price_period(extracted.period)
    amount = text_values._to_optional_float(extracted.amount)
    amount_min = text_values._to_optional_float(extracted.amount_min)
    amount_max = text_values._to_optional_float(extracted.amount_max)
    if category is None or (extracted.period is not None and period is None):
        return None
    if amount is None and amount_min is None and amount_max is None:
        return None
    return {
        "category": category,
        "period": period,
        "amount": amount,
        "amount_min": amount_min,
        "amount_max": amount_max,
        "currency": (extracted.currency or "BGN")[:3].upper(),
        "plan_name": fee_label(extracted.plan_name, 100),
        "academic_year": text_values._normalize_scalar_text(extracted.academic_year, max_len=20),
        "age_group": fee_label(extracted.age_group, 50),
    }


def _retention_year_key(value: str | None) -> tuple[bool, str]:
    """Bucket a stored academic year for replacement.

    Canonical years match across spellings, so a stored "2026-2027" is superseded by an
    incoming "2026/2027". A blank year forms the undated bucket. A non-blank year that
    does not normalize keeps its own bucket rather than collapsing into the undated one,
    so an unrecognised value is never deleted as collateral.
    """
    raw = str(value or "").strip()
    if not raw:
        return (False, "")
    canonical = normalize_academic_year(raw)
    return (True, canonical) if canonical else (False, raw)


def pricing_replacement_regressions(
    school: School,
    pages: list[SourcePage],
    superseded: list[Pricing],
    proposed: list[Pricing],
    listed_under_a_fee: Iterable[tuple[str, float]] = (),
) -> list[str]:
    """Rule 6 over the rows a re-read would replace: only rows the API publishes."""
    blocked = blocked_pricing_row_ids(school.attributes if isinstance(school.attributes, dict) else None)
    published = [row for row in superseded if row.id not in blocked and pricing_row_is_publishable(row)]
    if not published:
        return []
    texts = {page.id: page.raw_markdown for page in pages}
    return replacement_regressions(
        [(PriceRow.from_pricing(row), texts.get(row.source_page_id)) for row in published],
        [(PriceRow.from_pricing(row), texts.get(row.source_page_id)) for row in proposed],
        level_family(school.education_level),
        listed_under_a_fee,
    )


async def find_superseded_rows(db: AsyncSession, school: School, pricing_rows: list[Pricing]) -> list[Pricing]:
    """The stored rows this run replaces.

    Supersede only the academic years this run actually produced, so earlier years
    survive as fee history. Rows the school still publishes undated are replaced by
    the undated rows of this run.
    """
    written_years = {_retention_year_key(row.academic_year) for row in pricing_rows}
    existing_result = await db.execute(
        select(Pricing)
        .where(
            Pricing.school_id == school.id,
            Pricing.source == PriceSource.SCRAPED_WEBSITE,
        )
        .options(selectinload(Pricing.source_page).load_only(SourcePage.id, SourcePage.is_valid))
    )
    # An undated row whose fee this run reads with a year from the same page is that fee,
    # not history: 610's undated "7400" (filed in lev) beside this year's 7400 EUR. The
    # currency is not compared, since a wrong one is what such a row can carry.
    dated_fees = {
        (row.category, float(row.amount), row.source_page_id)
        for row in pricing_rows
        if row.amount is not None and row.source_page_id is not None and _retention_year_key(row.academic_year)[0]
    }
    return [
        existing
        for existing in existing_result.scalars().all()
        if _retention_year_key(existing.academic_year) in written_years
        or (
            not str(existing.academic_year or "").strip()
            and existing.amount is not None
            and (existing.category, float(existing.amount), existing.source_page_id) in dated_fees
        )
    ]


def update_pricing_hold(school: School, regressions: list[str], proposed_rows: int) -> bool:
    """Record or clear the review hold; True when the re-read is held.

    UF45 rule 6: a re-read that would lose what the published rows show is held
    for review; the published rows stay and the proposal is not written.
    """
    attrs = dict(school.attributes) if isinstance(school.attributes, dict) else {}
    if regressions:
        attrs[PRICING_HOLD_KEY] = {
            "held_at": text_values._utcnow_naive().isoformat(),
            "reasons": regressions,
            "proposed_rows": proposed_rows,
        }
        school.attributes = attrs
        flag_modified(school, "attributes")
        return True
    if PRICING_HOLD_KEY in attrs:
        attrs.pop(PRICING_HOLD_KEY)
        school.attributes = attrs
        flag_modified(school, "attributes")
    return False


async def write_price_rows(
    db: AsyncSession,
    school: School,
    superseded_ids: list[int],
    pricing_rows: list[Pricing],
    source_rows: list[FieldSource],
) -> None:
    if superseded_ids:
        await db.execute(delete(Pricing).where(Pricing.id.in_(superseded_ids)))
    await db.execute(
        delete(FieldSource).where(
            FieldSource.school_id == school.id,
            FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
            FieldSource.category == "pricing",
        )
    )
    for row in pricing_rows:
        db.add(row)
    for row in source_rows:
        db.add(row)
