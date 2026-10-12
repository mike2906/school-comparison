"""Steps of the price extraction in ``extractor._extract_prices``.

Each function is one phase of that orchestrator. This module must not import
``app.scrapers.extractor``; the LLM call stays in the orchestrator so tests that patch
``extractor._run_typed_agent`` still intercept it.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.field_source import FieldSource, SourceConfidence, SourceType
from app.models.pricing import PriceSource, Pricing
from app.models.school import School
from app.models.source_page import SourcePage
from app.schemas.extraction import ExtractedPrice, PageFees, PriceExtractionOutput
from app.scrapers.extraction import fee_lines, llm_results, price_lines, price_rows, text_values
from app.scrapers.page_content import is_template_filler
from app.scrapers.price_evidence import (
    PriceRow,
    label_is_another_institutions,
    page_is_another_institutions,
    replacement_regressions,
)
from app.scrapers.shared_site_check import level_family
from app.utils.display_gating import blocked_pricing_row_ids, pricing_row_is_publishable


def _supported_price_rows(
    prices: list[ExtractedPrice], selected_text: str, *, model_rows: bool = False
) -> list[ExtractedPrice]:
    """Evidence-filter then de-duplicate extracted price rows.

    ``model_rows`` marks rows a model read from the page; the keyword extractor's rows
    (the default) are checked against line and heading keywords instead.

    Shared by the price-extraction path (LLM and deterministic branches) and the
    golden-corpus harness so the filter/dedupe sequence can never drift between them.
    """
    if not prices:
        return []
    supported = (
        fee_lines._filter_model_prices(prices, selected_text)
        if model_rows
        else price_rows._filter_supported_prices(prices, selected_text)
    )
    return price_rows._dedupe_price_rows(supported)


def _normalized_price_fields(extracted: ExtractedPrice) -> dict[str, Any] | None:
    """Coerce one ``ExtractedPrice`` into normalized persisted-row field values.

    Returns ``None`` when the row cannot be persisted (unknown category/period or
    no amount). Shared by ``_extract_prices`` row assembly and the golden-corpus
    harness so coercion stays identical between production and the regression suite.
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
        "plan_name": fee_lines._fee_label(extracted.plan_name, 100),
        "academic_year": text_values._normalize_scalar_text(extracted.academic_year, max_len=20),
        "age_group": fee_lines._fee_label(extracted.age_group, 50),
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
    canonical = price_lines._normalize_academic_year(raw)
    return (True, canonical) if canonical else (False, raw)


# UF45 rule 6: why the last re-extraction's pricing was not written (internal only).
PRICING_HOLD_KEY = "pricing_hold"


def _pricing_replacement_regressions(
    school: School,
    pages: list[SourcePage],
    superseded: list[Pricing],
    proposed: list[Pricing],
    listed_under_a_fee: Iterable[tuple[str, float]] = (),
) -> list[str]:
    """Rule 6 over the rows a re-extraction would replace: only rows the API publishes."""
    blocked = blocked_pricing_row_ids(school.attributes if isinstance(school.attributes, dict) else None)
    published = [
        row for row in superseded if row.id not in blocked and pricing_row_is_publishable(row)
    ]
    if not published:
        return []
    texts = {page.id: page.raw_markdown for page in pages}
    return replacement_regressions(
        [(PriceRow.from_pricing(row), texts.get(row.source_page_id)) for row in published],
        [(PriceRow.from_pricing(row), texts.get(row.source_page_id)) for row in proposed],
        level_family(school.education_level),
        listed_under_a_fee,
    )


# What the model is asked to do with a school's fee pages. The model copies the priced
# lines; it is not asked whose fee a line is, which year counts or what a payment plan's
# total means. Asked to decide those in the same pass, models read the same page
# differently from one run to the next, while the amounts they copied were the same. Code
# decides them below, from the labels.
PRICE_SYSTEM_PROMPT = (
    "You are given the text of a few pages of a school's website, each under a "
    "'--- SOURCE: url ---' line. List every amount of money on them that a family could be "
    "charged: one entry per amount. Copy, do not interpret: keep labels and quotes in the "
    "page's own words and language, and do not leave an amount out because it is for "
    "another age group, another school of the same brand, an earlier year, or a payment "
    "plan. Say what each amount is with the fields provided. Leave out amounts that are "
    "not charged to families (prizes, salaries, budgets, percentages). If the pages state "
    "no such amounts, return an empty list."
)


def _price_result(
    success: bool,
    count: int,
    detail: str,
    input_tokens: int,
    output_tokens: int,
    token_cost_usd: float,
    *,
    held: bool = False,
) -> dict[str, Any]:
    """The result dict of a price extraction that got as far as an LLM answer."""
    result: dict[str, Any] = {"success": success, "count": count}
    if held:
        result["held"] = True
    result["detail"] = detail
    result["input_tokens"] = input_tokens
    result["output_tokens"] = output_tokens
    result["token_cost_usd"] = token_cost_usd
    return result


def _fee_pages_of_this_school(pages: list[SourcePage], scope: Any, family: Any) -> list[SourcePage]:
    """Drop pages that are not fee pages of this school.

    Not fee pages of this school: a site template's sample text (590's "$1,800/mo"
    between lines of lorem ipsum), and a sibling school's section of a shared site.
    """
    return [
        page
        for page in pages
        if not is_template_filler(page.raw_markdown)
        and not page_is_another_institutions(page.source_url, scope.grades, family, scope.shares_site)
    ]


def _fees_from_model_answer(parsed: Any) -> tuple[Any, list[tuple[str, float]]]:
    """Convert the model's ``PageFees`` answer into price rows.

    Returns the converted answer (or ``parsed`` unchanged when it is not a ``PageFees``)
    and the amounts the model read as a discounted price or a payment plan's part: listed
    under a fee, not stored as one, so a re-extraction that leaves them out has not lost
    them.
    """
    listed_under_a_fee: list[tuple[str, float]] = []
    if isinstance(parsed, PageFees):
        listed_under_a_fee = [
            (fee_lines._FEE_KIND_CATEGORY.get(line.kind, "other"), line.amount)
            for line in parsed.lines
            if line.role in ("discounted", "plan")
        ]
        prices = fee_lines._prices_from_fee_lines(parsed.lines)
        parsed = PriceExtractionOutput(prices=prices, has_pricing_info=bool(prices))
    return parsed, listed_under_a_fee


def _supported_prices_of_this_school(
    prices: list[ExtractedPrice],
    selected_text: str,
    scope: Any,
    family: Any,
    *,
    model_rows: bool,
) -> list[ExtractedPrice]:
    rows = _supported_price_rows(prices, selected_text, model_rows=model_rows)
    # A sibling kindergarten's or school's fee on a shared site is not this one's.
    return [
        row
        for row in rows
        if not label_is_another_institutions(
            " / ".join(part for part in (row.plan_name, row.age_group) if part),
            family,
            set(scope.grades),
            scope.own_name,
            scope.shares_site,
        )
    ]


def _apply_evidence_filters(
    parsed: PriceExtractionOutput,
    deterministic_pricing: PriceExtractionOutput,
    used_deterministic_pricing: bool,
    selected_text: str,
    scope: Any,
    family: Any,
) -> tuple[PriceExtractionOutput, bool]:
    """Keep only rows the page text bears out; fall back to the keyword reading if allowed.

    Returns the filtered answer and whether it is the deterministic reading.
    """
    # The keyword extractor stands in only when the model call gave no answer. A model
    # that answers "no fees for this institution" is usually right (526: the page a
    # kindergarten shares lists only the school's fees), and when its rows are not borne
    # out by the text the keyword reading is no better.
    model_gave_rows = not used_deterministic_pricing
    supported_prices = _supported_prices_of_this_school(
        parsed.prices, selected_text, scope, family, model_rows=not used_deterministic_pricing
    )
    if supported_prices:
        parsed = parsed.model_copy(update={"prices": supported_prices, "has_pricing_info": True})
    elif deterministic_pricing.has_pricing_info and not model_gave_rows:
        deterministic_supported_prices = _supported_prices_of_this_school(
            deterministic_pricing.prices, selected_text, scope, family, model_rows=False
        )
        if deterministic_supported_prices:
            parsed = deterministic_pricing.model_copy(
                update={"prices": deterministic_supported_prices, "has_pricing_info": True}
            )
            used_deterministic_pricing = True
        else:
            parsed = parsed.model_copy(update={"prices": [], "has_pricing_info": False})
    else:
        parsed = parsed.model_copy(update={"prices": [], "has_pricing_info": False})
    return parsed, used_deterministic_pricing


async def _clear_pricing_evidence(db: AsyncSession, school: School) -> None:
    await db.execute(
        delete(FieldSource).where(
            FieldSource.school_id == school.id,
            FieldSource.source_type == SourceType.SCRAPED_WEBSITE,
            FieldSource.category == "pricing",
        )
    )


def _build_price_rows(
    school: School,
    pages: list[SourcePage],
    parsed: PriceExtractionOutput,
    used_deterministic_pricing: bool,
) -> tuple[list[Pricing], list[FieldSource]]:
    """Turn the accepted price answer into ``Pricing`` and ``FieldSource`` rows."""
    pricing_rows: list[Pricing] = []
    source_rows: list[FieldSource] = []
    seen_fees: set[tuple[Any, Any, Any]] = set()

    for extracted in parsed.prices:
        fields = _normalized_price_fields(extracted)
        if fields is None:
            continue
        category = fields["category"]
        period = fields["period"]
        amount = fields["amount"]
        amount_min = fields["amount_min"]
        amount_max = fields["amount_max"]

        normalized_discounts = text_values._normalize_text_list(extracted.discounts)
        normalized_installments = text_values._normalize_text_list(extracted.installments)
        normalized_includes = text_values._normalize_text_list(extracted.includes)
        normalized_excludes = text_values._normalize_text_list(extracted.excludes)
        row_source_url = fee_lines._find_supporting_price_source_url(
            school, pages, extracted, model_rows=not used_deterministic_pricing
        )
        if not row_source_url:
            continue
        supporting_page = next(
            (page for page in pages if page.source_url == row_source_url),
            None,
        )
        # The same fee copied from a second page (the site's other language, an older
        # copy) resolves to the first page that states the amount; there its label is
        # not to be found, unlike that of a second band with the same price.
        fee_key = (category, amount, fields["currency"])
        if fee_key in seen_fees and not fee_lines._label_is_on_page(
            fields["plan_name"], supporting_page.raw_markdown if supporting_page else None
        ):
            continue
        seen_fees.add(fee_key)
        if (
            not fields["academic_year"]
            and supporting_page is not None
            and price_lines._yearless_pricing_text_is_stale(supporting_page.raw_markdown or "")
        ):
            continue

        pricing_rows.append(
            Pricing(
                school_id=school.id,
                category=category,
                amount=amount,
                amount_min=amount_min,
                amount_max=amount_max,
                currency=fields["currency"],
                period=period,
                plan_name=fields["plan_name"],
                academic_year=fields["academic_year"],
                age_group=fields["age_group"],
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=row_source_url,
                source_page_id=supporting_page.id if supporting_page else None,
                pricing_context={
                    "notes": extracted.notes,
                    "confidence": extracted.confidence,
                    "discounts": normalized_discounts,
                    "installments": normalized_installments,
                    "includes": normalized_includes,
                    "excludes": normalized_excludes,
                },
            )
        )

        source_rows.append(
            FieldSource(
                school_id=school.id,
                category="pricing",
                field_key=f"pricing.{category.value}.{period.value if period else 'unstated'}",
                value_text=llm_results._format_price_value_text(
                    amount,
                    amount_min,
                    amount_max,
                    extracted.currency,
                ),
                source_type=SourceType.SCRAPED_WEBSITE,
                source_url=row_source_url,
                scraped_at=text_values._utcnow_naive(),
                confidence=SourceConfidence.HIGH if (extracted.confidence or 0) >= 0.8 else SourceConfidence.MEDIUM,
                confidence_score=extracted.confidence,
            )
        )

    return pricing_rows, source_rows


async def _find_superseded_rows(
    db: AsyncSession, school: School, pricing_rows: list[Pricing]
) -> list[Pricing]:
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


def _update_pricing_hold(school: School, regressions: list[str], proposed_rows: int) -> bool:
    """Record or clear the review hold; True when the re-extraction is held.

    UF45 rule 6: a re-extraction that would lose what the published rows show is held
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


async def _write_price_rows(
    db: AsyncSession,
    school: School,
    superseded_ids: list[int],
    pricing_rows: list[Pricing],
    source_rows: list[FieldSource],
) -> None:
    if superseded_ids:
        await db.execute(delete(Pricing).where(Pricing.id.in_(superseded_ids)))
    await _clear_pricing_evidence(db, school)

    for row in pricing_rows:
        db.add(row)
    for row in source_rows:
        db.add(row)
