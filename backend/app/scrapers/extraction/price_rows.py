"""Price row dedupe (installments, currency variants) and the filter that keeps only rows the pages support."""

from __future__ import annotations

import re
from typing import Any, Optional

from app.schemas.extraction import (
    ExtractedPrice,
)
from app.scrapers.extraction.price_lines import (
    _iter_price_line_signals,
    _normalize_academic_year,
    _parse_price_amount_token,
)
from app.scrapers.extraction.text_values import (
    _to_optional_float,
)

_BGN_EUR_PEG = 1.95583


_INSTALLMENT_TEXT_AMOUNT_RE = re.compile(r"(\d[\d\s.,]*)")


_INSTALLMENT_PLAN_NAME_RE = re.compile(
    r"(?:(\d+)\s*(?:installment|monthly|вноск)|(?:installment|monthly|вноск)\s*(\d+))",
    flags=re.IGNORECASE,
)


def _amounts_in_installment_text(text: str) -> list[float]:
    amounts: list[float] = []
    for match in _INSTALLMENT_TEXT_AMOUNT_RE.finditer(text or ""):
        value = _parse_price_amount_token(match.group(1))
        if value is not None and value >= 1:
            amounts.append(value)
    return amounts


def _is_installment_plan_name(plan_name: Optional[str]) -> bool:
    # "10 installments", "2 вноски", "monthly installment", etc. signal that a
    # row represents a payment-schedule variant rather than a distinct fee.
    # "Плащане на 1 вноска" is the whole fee paid at once, not a schedule.
    if not plan_name:
        return False
    return any(
        (match.group(1) or match.group(2)) != "1"
        for match in _INSTALLMENT_PLAN_NAME_RE.finditer(plan_name)
    )


def _dedupe_installment_variants(prices: list[ExtractedPrice]) -> list[ExtractedPrice]:
    # Drop rows that represent payment-schedule variants of another row:
    # - amount is listed in another row's `installments` strings, OR
    # - plan_name is labelled as "N installments" / "N вноски" while a sibling
    #   row in the same (category, period, academic_year, age_group) has no
    #   such label.
    if not prices:
        return prices

    def group_key(price: ExtractedPrice) -> tuple:
        return (
            (price.category or "").lower(),
            (price.period or "").lower(),
            price.academic_year,
            price.age_group,
        )

    def is_total_fee_row(price: ExtractedPrice) -> bool:
        context = " ".join(
            str(value or "") for value in (price.plan_name, price.notes)
        ).casefold()
        return "total fee" in context

    grouped: dict[tuple, list[ExtractedPrice]] = {}
    for price in prices:
        row_context = " ".join(
            str(value or "") for value in (price.plan_name, price.notes)
        ).casefold()
        if any(
            token in row_context
            for token in ("eal", "learning support", "additional language support")
        ):
            continue
        grouped.setdefault(group_key(price), []).append(price)

    kept: list[ExtractedPrice] = []
    for group in grouped.values():
        if len(group) == 1:
            kept.extend(group)
            continue
        installment_amounts: set[float] = set()
        for row in group:
            for line in row.installments or []:
                installment_amounts.update(_amounts_in_installment_text(line))
        has_non_installment_sibling = any(
            not _is_installment_plan_name(row.plan_name) for row in group
        )
        has_component_sibling = any(not is_total_fee_row(row) for row in group)
        for row in group:
            if has_component_sibling and is_total_fee_row(row):
                continue
            row_amount = _to_optional_float(row.amount)
            if has_non_installment_sibling and _is_installment_plan_name(row.plan_name):
                continue
            if row_amount is None:
                kept.append(row)
                continue
            if row.installments:
                kept.append(row)
                continue
            if installment_amounts and any(
                abs(row_amount - inst) < 0.5 for inst in installment_amounts
            ):
                continue
            kept.append(row)
    return kept


def _dedupe_currency_variants(prices: list[ExtractedPrice]) -> list[ExtractedPrice]:
    # Drop BGN rows that are the currency-converted duplicate of an EUR row
    # in the same logical group. Prefer EUR since Bulgaria adopted euro in 2026.
    if not prices:
        return prices

    def group_key(price: ExtractedPrice) -> tuple:
        return (
            (price.category or "").lower(),
            (price.period or "").lower(),
            price.academic_year,
            price.age_group,
            price.plan_name,
        )

    grouped: dict[tuple, list[ExtractedPrice]] = {}
    for price in prices:
        grouped.setdefault(group_key(price), []).append(price)

    kept: list[ExtractedPrice] = []
    for group in grouped.values():
        if len(group) == 1:
            kept.extend(group)
            continue
        eur_rows = [p for p in group if (p.currency or "").upper() == "EUR"]
        bgn_rows = [p for p in group if (p.currency or "").upper() == "BGN"]
        other_rows = [p for p in group if (p.currency or "").upper() not in {"EUR", "BGN"}]
        kept.extend(other_rows)

        if not eur_rows or not bgn_rows:
            kept.extend(eur_rows)
            kept.extend(bgn_rows)
            continue

        eur_amounts = [_to_optional_float(p.amount) for p in eur_rows]
        bgn_remaining = []
        for bgn in bgn_rows:
            bgn_amount = _to_optional_float(bgn.amount)
            if bgn_amount is None:
                bgn_remaining.append(bgn)
                continue
            converted = bgn_amount / _BGN_EUR_PEG
            matched = any(
                eur_amount is not None and abs(eur_amount - converted) / converted < 0.01
                for eur_amount in eur_amounts
            )
            if matched:
                continue
            bgn_remaining.append(bgn)
        kept.extend(eur_rows)
        kept.extend(bgn_remaining)
    return kept


def _dedupe_price_rows(prices: list[ExtractedPrice]) -> list[ExtractedPrice]:
    rows = _dedupe_currency_variants(_dedupe_installment_variants(prices))

    distinct_rows: list[ExtractedPrice] = []
    seen_rows: set[str] = set()
    for row in rows:
        identity = row.model_dump_json(exclude_none=False)
        if identity in seen_rows:
            continue
        seen_rows.add(identity)
        distinct_rows.append(row)
    rows = distinct_rows

    def comparable_group_key(row: ExtractedPrice) -> tuple[str, str, str]:
        return (
            (row.category or "").casefold(),
            (row.plan_name or "").strip().casefold(),
            (row.age_group or "").strip().casefold(),
        )

    def exact_fee_identity(
        row: ExtractedPrice,
    ) -> tuple[str, str, str, str, float | None, str]:
        return (
            (row.category or "").casefold(),
            (row.period or "").casefold(),
            (row.plan_name or "").strip().casefold(),
            (row.age_group or "").strip().casefold(),
            _to_optional_float(row.amount),
            (row.currency or "").upper(),
        )

    grouped: dict[tuple[str, str, str], list[ExtractedPrice]] = {}
    for row in rows:
        grouped.setdefault(comparable_group_key(row), []).append(row)

    latest_year_by_group: dict[tuple[str, str, str], str] = {}
    for key, group in grouped.items():
        explicit_years = {
            normalized
            for row in group
            if (normalized := _normalize_academic_year(row.academic_year)) is not None
        }
        if explicit_years:
            latest_year_by_group[key] = max(
                explicit_years,
                key=lambda value: tuple(int(part) for part in value.split("/")),
            )

    explicit_fee_identities = {
        exact_fee_identity(row)
        for row in rows
        if _normalize_academic_year(row.academic_year) is not None
    }
    explicit_years = {
        normalized
        for row in rows
        if (normalized := _normalize_academic_year(row.academic_year)) is not None
    }
    latest_explicit_year = (
        max(
            explicit_years,
            key=lambda value: tuple(int(part) for part in value.split("/")),
        )
        if explicit_years
        else None
    )
    latest_start_year = (
        int(latest_explicit_year.split("/", 1)[0]) if latest_explicit_year else None
    )
    # Last year's list beside this year's tuition is superseded as a whole, also its fees
    # the new list does not repeat (596: the 2025/2026 food and transport prices).
    latest_year_has_tuition = any(
        (row.category or "").casefold() == "tuition"
        and _normalize_academic_year(row.academic_year) == latest_explicit_year
        for row in rows
    )
    return [
        row
        for row in rows
        if (
            (year := _normalize_academic_year(row.academic_year)) is None
            or latest_start_year is None
            # Keep the immediately preceding academic year while the newest one states
            # no tuition yet (a page that mixes adjacent schedules). Older fee tables
            # are superseded even when the new table has different categories.
            or int(year.split("/", 1)[0]) >= latest_start_year - (0 if latest_year_has_tuition else 1)
        )
        and (
            (key := comparable_group_key(row)) not in latest_year_by_group
            or year == latest_year_by_group[key]
            or (year is None and exact_fee_identity(row) not in explicit_fee_identities)
        )
    ]


_PRICE_MATCH_STOPWORDS = {
    "annual",
    "fee",
    "fees",
    "full",
    "plan",
    "service",
    "the",
    "for",
    "school",
    "такса",
    "такси",
    "годишна",
    "за",
    "на",
}


def _price_signal_match_score(price: ExtractedPrice, signal: dict[str, Any]) -> int:
    context = " ".join(
        str(value or "")
        for value in (price.plan_name, price.notes, price.category)
    ).casefold()
    line = str(signal.get("line") or "").casefold()
    context_tokens = {
        token for token in re.findall(r"[^\W\d_]{3,}", context) if token not in _PRICE_MATCH_STOPWORDS
    }
    line_tokens = set(re.findall(r"[^\W\d_]{3,}", line))
    score = 3 * len(context_tokens & line_tokens)
    if price.category == signal.get("category"):
        score += 2
    return score


def _filter_supported_prices(prices: list[ExtractedPrice], text: str) -> list[ExtractedPrice]:
    if not prices or not text:
        return []

    signals = _iter_price_line_signals(text)
    if not signals:
        return []

    refined: list[ExtractedPrice] = []
    for price in prices:
        row_context = " ".join(
            str(value or "") for value in (price.plan_name, price.notes)
        ).strip().casefold()
        amount = _to_optional_float(price.amount)
        if amount is None:
            continue

        currency = (price.currency or "BGN")[:3].upper()
        candidates = [
            signal
            for signal in signals
            if signal["currency"] == currency and abs(float(signal["amount"]) - amount) < 0.01
        ]
        if not candidates:
            continue
        academic_year = _normalize_academic_year(price.academic_year)
        if academic_year:
            candidates = [
                signal
                for signal in candidates
                if _normalize_academic_year(signal.get("academic_year")) == academic_year
            ]
        else:
            candidates = [
                signal for signal in candidates if not signal.get("source_is_stale", False)
            ]
        if not candidates:
            continue
        candidate_semantics = {
            (signal.get("category"), signal.get("period"), signal.get("academic_year"))
            for signal in candidates
        }
        if not row_context and len(candidate_semantics) > 1:
            # Deterministic/low-confidence rows do not carry enough context to
            # safely disambiguate repeated amounts from composite source lines.
            if float(price.confidence or 0.0) < 0.8:
                continue
            semantic_matches = [
                signal
                for signal in candidates
                if signal.get("category") == price.category
            ]
            if len({(signal.get("category"), signal.get("period"), signal.get("academic_year")) for signal in semantic_matches}) > 1:
                semantic_matches = [
                    signal for signal in semantic_matches if signal.get("period") == price.period
                ]
            matched_semantics = {
                (signal.get("category"), signal.get("period"), signal.get("academic_year"))
                for signal in semantic_matches
            }
            if len(matched_semantics) != 1:
                continue
            candidates = semantic_matches
        scored = [(_price_signal_match_score(price, signal), signal) for signal in candidates]
        best_score = max(score for score, _signal in scored)
        best_signals = [signal for score, signal in scored if score == best_score]
        best_semantics = {
            (signal.get("category"), signal.get("period"), signal.get("academic_year"))
            for signal in best_signals
        }
        if len(best_semantics) != 1:
            continue
        supporting_signal = best_signals[0]
        if "total fee" in str(supporting_signal.get("section_heading") or "").casefold():
            continue

        normalized = price.model_copy(deep=True)
        if supporting_signal.get("category"):
            normalized.category = supporting_signal["category"]
        # Evidence is authoritative in both directions: an explicit supported
        # period corrects the model, and absent period evidence clears a guess.
        normalized.period = supporting_signal.get("period")
        if supporting_signal.get("academic_year") and not normalized.academic_year:
            normalized.academic_year = supporting_signal["academic_year"]
        if supporting_signal.get("age_group") and not normalized.age_group:
            normalized.age_group = supporting_signal["age_group"]
        refined.append(normalized)

    return refined
