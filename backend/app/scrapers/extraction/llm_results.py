"""LLM call results: token usage, error classification, answer scoring and value coercion."""

from __future__ import annotations

from typing import Any, Optional

from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior

from app.models.pricing import PriceCategory, PricePeriod
from app.schemas.extraction import (
    GeneralInfoExtractionOutput,
)
from app.scrapers.extraction.text_values import (
    _count_usable_text_values,
    _is_usable_text,
    _normalize_key,
)
from app.scrapers.extraction_rules import base as rules


def _get_usage(result: Any) -> tuple[int, int]:
    """Extract input/output tokens from pydantic-ai result."""
    usage_obj = result.usage() if callable(getattr(result, "usage", None)) else getattr(result, "usage", None)
    if usage_obj is None:
        return 0, 0

    input_tokens = getattr(usage_obj, "input_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage_obj, "request_tokens", 0)

    output_tokens = getattr(usage_obj, "output_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage_obj, "response_tokens", 0)

    return int(input_tokens or 0), int(output_tokens or 0)


def _is_model_or_provider_error(exc: Exception) -> bool:
    """Allow capable fallback only for model/provider-level failures."""
    if isinstance(exc, (ModelHTTPError, ModelAPIError)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in rules._PROVIDER_ERROR_MARKERS)


def _is_output_validation_error(exc: Exception) -> bool:
    stack = [exc]
    seen: set[int] = set()

    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        message = str(current).lower()
        if any(marker in message for marker in rules._OUTPUT_VALIDATION_ERROR_MARKERS):
            return True
        if isinstance(current, UnexpectedModelBehavior) and "retry" in message and "validation" in message:
            return True

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, Exception):
            stack.append(cause)
        if isinstance(context, Exception):
            stack.append(context)

        nested = getattr(current, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            for child in nested:
                if isinstance(child, Exception):
                    stack.append(child)

    return False


def _score_general_info_output(parsed: GeneralInfoExtractionOutput) -> int:
    """Heuristic quality score for general-info extraction payloads."""
    score = 0
    if any(_is_usable_text(entry.language) for entry in parsed.languages):
        score += 1
    if _count_usable_text_values(parsed.facilities) > 0:
        score += 1
    if _count_usable_text_values(parsed.programs) > 0:
        score += 1
    if _count_usable_text_values(parsed.extracurricular) > 0:
        score += 1
    if _count_usable_text_values(parsed.accreditations) > 0:
        score += 1
    if _is_usable_text(parsed.class_size):
        score += 1
    if _is_usable_text(parsed.founded_year):
        score += 1
    if _count_usable_text_values(parsed.admission.deadlines) > 0:
        score += 1
    if _count_usable_text_values(parsed.admission.required_documents) > 0:
        score += 1
    if _count_usable_text_values(parsed.admission.application_steps) > 0:
        score += 1
    if _count_usable_text_values(parsed.admission.available_spots) > 0:
        score += 1
    if _is_usable_text(parsed.operations.working_hours):
        score += 1
    if _count_usable_text_values(parsed.operations.day_options) > 0:
        score += 1
    if _count_usable_text_values(parsed.operations.meals) > 0:
        score += 1
    if _count_usable_text_values(parsed.services.support_services) > 0:
        score += 1
    if _count_usable_text_values(parsed.services.safety_features) > 0:
        score += 1
    if _count_usable_text_values(parsed.pricing_terms.discounts) > 0:
        score += 1
    if _is_usable_text(parsed.summary_source.positioning):
        score += 1
    if _count_usable_text_values(parsed.summary_source.teaching_approach) > 0:
        score += 1
    if _count_usable_text_values(parsed.summary_source.student_experience) > 0:
        score += 1
    if _count_usable_text_values(parsed.summary_source.community_signals) > 0:
        score += 1
    if _count_usable_text_values(parsed.summary_source.differentiators) > 0:
        score += 1
    if _count_usable_text_values(parsed.summary_source.canonical_tags) > 0:
        score += 1
    return score


def _coerce_price_category(raw: Optional[str | PriceCategory]) -> Optional[PriceCategory]:
    if raw is None:
        return None
    if isinstance(raw, PriceCategory):
        return raw
    if isinstance(raw, str) and "." in raw:
        tail = raw.split(".")[-1]
        normalized_tail = _normalize_key(tail)
        if normalized_tail in rules._CATEGORY_ALIASES:
            return rules._CATEGORY_ALIASES[normalized_tail]
    normalized = _normalize_key(raw)
    return rules._CATEGORY_ALIASES.get(normalized)


def _coerce_price_period(raw: Optional[str | PricePeriod]) -> Optional[PricePeriod]:
    if raw is None:
        return None
    if isinstance(raw, PricePeriod):
        return raw
    if isinstance(raw, str) and "." in raw:
        tail = raw.split(".")[-1]
        normalized_tail = _normalize_key(tail)
        if normalized_tail in rules._PERIOD_ALIASES:
            return rules._PERIOD_ALIASES[normalized_tail]
    normalized = _normalize_key(raw)
    return rules._PERIOD_ALIASES.get(normalized)


def _format_price_value_text(
    amount: Optional[float],
    amount_min: Optional[float],
    amount_max: Optional[float],
    currency: Optional[str],
) -> Optional[str]:
    code = (currency or "BGN")[:3].upper()
    if amount is not None:
        return f"{amount:.2f} {code}"
    if amount_min is not None and amount_max is not None:
        return f"{amount_min:.2f}-{amount_max:.2f} {code}"
    if amount_min is not None:
        return f"from {amount_min:.2f} {code}"
    if amount_max is not None:
        return f"up to {amount_max:.2f} {code}"
    return None
