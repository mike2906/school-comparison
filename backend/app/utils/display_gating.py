"""Field-level display gating (P1.7).

Mirrors the summarizer's ``_blocked_summary_sections`` pattern, but at field
granularity for the public API projection: a display field that carries an
error-level issue (or an actionable spot-check discrepancy) in the current
Stage 6 validation report is withheld from the response, so a parent never sees a
value the validator has already flagged as wrong.

Pricing rows are gated separately by a hard rule: a row with no ``source_url`` or a
confidence below :data:`PRICING_CONFIDENCE_FLOOR` is withheld. The floor is shared
with ``app.services.data_quality`` so the scoreboard metric and this display gate
can never diverge.
"""

from __future__ import annotations

import datetime
import decimal
import re
from typing import Any, Iterator, Mapping

from app.config import get_settings

# Pricing rows below this per-row confidence are withheld from the API. Imported by
# ``app.services.data_quality`` so the "pricing rows failing gates" metric measures
# exactly what the display gate hides.
PRICING_CONFIDENCE_FLOOR = 0.7

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_PRICE_PERIODS = {"monthly", "yearly", "one_time", "quarter", "term", "semester"}

# Spot-check discrepancy kinds that are actionable enough to withhold a field.
# Matches the summarizer's `_ACTIONABLE_DISCREPANCY_KINDS` (omissions are only a
# monitoring signal, not a reason to hide an otherwise-supported value).
_ACTIONABLE_DISCREPANCY_KINDS = {"contradiction", "unsupported"}

# Maps validation-report field paths to the public display fields they feed. A path
# matches a key when it equals the key or is nested under it (``key`` + ``.``).
#
# Each ``attributes.extracted.<key>`` must be reachable — i.e. a spot-check discrepancy
# on it must actually survive to the report. `_normalize_spot_check_output` drops any
# discrepancy outside `validator.SPOT_CHECK_CORE_FIELD_PREFIXES`, so a mapping key that
# is not core can never match and would give false confidence. A drift-guard test
# (`test_display_gating`) fails if a mapped key falls outside that core set, keeping the
# gate's coverage and the spot-check's scope in lockstep.
_FIELD_PATH_DISPLAY_FIELDS: dict[str, tuple[str, ...]] = {
    "attributes.extracted.languages": ("language_focus", "languages_of_instruction"),
    "attributes.extracted.facilities": ("facilities",),
    "attributes.extracted.programs": ("special_programs",),
    "attributes.extracted.accreditations": ("special_programs",),
    "attributes.extracted.extracurricular": ("activities_offered",),
    "attributes.extracted.class_size": ("class_size",),
    "attributes.extracted.admission.deadlines": ("application_deadlines",),
    "attributes.extracted.admission.entrance_requirements": ("entry_requirements",),
    "attributes.extracted.admission.available_spots": ("available_spots",),
    "attributes.extracted.operations.working_hours": ("school_hours",),
    "attributes.extracted.operations.daily_schedule": ("daily_schedule",),
    # Keep these parent mappings after the child paths above: a discrepancy on one
    # child should block only that child, while a whole-section discrepancy blocks
    # every public field fed by the section.
    "attributes.extracted.admission": (
        "application_deadlines",
        "entry_requirements",
        "available_spots",
    ),
    "attributes.extracted.operations": ("school_hours", "daily_schedule"),
}

# Whole-section paths above deliberately block every public child, but an unrelated
# nested discrepancy (e.g. ``operations.transport``) must not inherit that broad block.
_EXACT_ONLY_FIELD_PATHS = {
    "attributes.extracted.admission",
    "attributes.extracted.operations",
}

# Validator pricing paths are `pricing[{row.id}]` / `pricing[{row.id}].{field}`.
_PRICING_ROW_RE = re.compile(r"^pricing\[(\d+)\]")

_ADMISSION_NAV_LABELS = {
    "admission",
    "admissions",
    "прием",
    "кандидатстване",
    "apply",
    "интервюта",
    "интервютата",
    "school life",
    "училищен живот",
    "available places",
    "available spots",
    "свободни места",
}
_ADMISSION_PROCUREMENT_RE = re.compile(
    r"\b(?:оферт(?:а|и|ата)|обществен[аио]\s+поръчк|procurement|tender|bid submission)\b",
    re.IGNORECASE,
)
_ADMISSION_INCOMPLETE_END_RE = re.compile(
    r"\b(?:is|are|on|at|from|to|на|в|от|до|за)\s*[.:,;\-–—]*$",
    re.IGNORECASE,
)
_ADMISSION_MONTH_RE = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|october|"
    r"november|december|януари|февруари|март|април|май|юни|юли|август|"
    r"септември|октомври|ноември|декември)\b",
    re.IGNORECASE,
)


def admission_value_is_semantically_valid(
    field_name: str,
    value: str,
    *,
    today: datetime.date | None = None,
) -> bool:
    """Return whether extracted admission text is safe to show to parents."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return False
    lowered = text.casefold()

    label = re.sub(r"^[\s#*+\-←→]+|[\s:;.!?]+$", "", lowered).strip()
    if label in _ADMISSION_NAV_LABELS:
        return False
    if _ADMISSION_PROCUREMENT_RE.search(text) or _ADMISSION_INCOMPLETE_END_RE.search(text):
        return False

    reference_date = today or datetime.datetime.now(datetime.timezone.utc).date()
    current_cycle_start = reference_date.year if reference_date.month >= 7 else reference_date.year - 1
    for match in re.finditer(r"(?<!\d)(20\d{2})\s*[/\-]\s*(20\d{2}|\d{2})(?!\d)", text):
        if int(match.group(1)) < current_cycle_start:
            return False
    years = [int(year) for year in re.findall(r"(?<!\d)(20\d{2})(?!\d)", text)]
    if years and max(years) < current_cycle_start:
        return False

    if field_name == "deadlines":
        if not (
            re.search(r"(?<!\d)\d{1,2}[./-]\d{1,2}(?:[./-]\d{2,4})?(?!\d)", text)
            or _ADMISSION_MONTH_RE.search(text)
            or re.search(
                r"\b(?:year[- ]round|rolling admissions|целогодишно)\b",
                text,
                re.IGNORECASE,
            )
        ):
            return False
    elif field_name == "available_spots" and not re.search(r"(?<!\d)\d{1,3}(?!\d)", text):
        return False

    return True


def iter_blocking_field_paths(report: Mapping[str, Any] | None) -> Iterator[str]:
    """Yield the ``field_path`` of every entry that should withhold its target.

    That is, error-level issues plus actionable spot-check discrepancies in a
    ``data_validation`` report mapping. Shared by the API display gate and the
    summarizer's section gate so the two can't diverge on what "blocking" means.
    """
    if not isinstance(report, Mapping):
        return

    for issue in report.get("issues", []) or []:
        if isinstance(issue, Mapping) and str(issue.get("severity") or "").lower() == "error":
            yield str(issue.get("field_path") or "")

    spot_check = report.get("spot_check")
    if isinstance(spot_check, Mapping):
        for discrepancy in spot_check.get("discrepancies", []) or []:
            if (
                isinstance(discrepancy, Mapping)
                and str(discrepancy.get("kind") or "").lower() in _ACTIONABLE_DISCREPANCY_KINDS
            ):
                yield str(discrepancy.get("field_path") or "")


def _blocking_field_paths(attributes: Mapping[str, Any] | None) -> Iterator[str]:
    """`iter_blocking_field_paths` over a school's raw ``attributes`` blob."""
    if not isinstance(attributes, Mapping):
        return
    yield from iter_blocking_field_paths(attributes.get("data_validation"))


def _display_fields_for_path(field_path: str) -> tuple[str, ...]:
    path = (field_path or "").strip()
    if not path:
        return ()
    for prefix, fields in _FIELD_PATH_DISPLAY_FIELDS.items():
        if path == prefix:
            return fields
        if prefix not in _EXACT_ONLY_FIELD_PATHS and path.startswith(f"{prefix}."):
            return fields
    return ()


def blocked_display_fields(attributes: Mapping[str, Any] | None) -> set[str]:
    """Display fields to withhold given a school's raw ``attributes`` blob.

    Reads ``attributes.data_validation``: error-level issues and actionable
    spot-check discrepancies whose ``field_path`` feeds a display field block it.
    """
    blocked: set[str] = set()
    for path in _blocking_field_paths(attributes):
        blocked.update(_display_fields_for_path(path))
    return blocked


def blocked_pricing_row_ids(attributes: Mapping[str, Any] | None) -> set[int]:
    """Pricing row ids withheld by an error-level issue / actionable discrepancy.

    Stage 6 flags a bad price at ``pricing[{row.id}]`` (e.g. ``negative_price_amount``);
    the display gate must drop that specific row even when it has a source_url and
    is confident enough to clear :func:`passes_pricing_gate`.
    """
    blocked: set[int] = set()
    for path in _blocking_field_paths(attributes):
        match = _PRICING_ROW_RE.match(path.strip())
        if match:
            blocked.add(int(match.group(1)))
    return blocked


def summary_is_publishable(attributes: Mapping[str, Any] | None) -> bool:
    """True only when the current validation report is explicitly ``ok`` (P1.7).

    A whole-school summary is only published for a clean report; a stored summary
    from an earlier run must be withheld once validation regresses to
    ``needs_review``, mirroring the generation-side eligibility rule.

    Missing and malformed reports fail closed. This prevents a stored summary from an
    earlier run being published without evidence that it passed the current validator.
    """
    if not get_settings().publish_summaries:
        return False
    if not isinstance(attributes, Mapping):
        return False
    report = attributes.get("data_validation")
    if not isinstance(report, Mapping):
        return False
    status = str(report.get("status") or "").strip().lower()
    return status == "ok" and not any(_blocking_field_paths(attributes))


def passes_pricing_gate(source_url: Any, pricing_context: Any) -> bool:
    """True when a pricing row is safe to publish (has a source, confident enough)."""
    if not str(source_url or "").strip():
        return False
    confidence = pricing_context.get("confidence") if isinstance(pricing_context, Mapping) else None
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return False
    # The bounds also reject NaN and infinities without coercing arbitrary values.
    return PRICING_CONFIDENCE_FLOOR <= confidence <= 1.0


def _pricing_row_value(row: Any, field: str) -> Any:
    return row.get(field) if isinstance(row, Mapping) else getattr(row, field, None)


def _positive_price(value: Any) -> decimal.Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = decimal.Decimal(str(value))
    except (decimal.InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _valid_price_shape(row: Any) -> bool:
    amount_raw = _pricing_row_value(row, "amount")
    low_raw = _pricing_row_value(row, "amount_min")
    high_raw = _pricing_row_value(row, "amount_max")
    amount = _positive_price(amount_raw)
    low = _positive_price(low_raw)
    high = _positive_price(high_raw)

    if amount_raw is not None:
        return amount is not None and low_raw is None and high_raw is None
    return low is not None and high is not None and low <= high


def _aware_verification_time(value: Any) -> datetime.datetime | None:
    if isinstance(value, datetime.datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _has_human_verification(pricing_context: Any, verification_date: Any) -> bool:
    if not isinstance(pricing_context, Mapping):
        return False
    verification = pricing_context.get("human_verification")
    if not isinstance(verification, Mapping):
        return False
    verified_by = verification.get("verified_by")
    if not isinstance(verified_by, str) or not verified_by.strip():
        return False
    if _aware_verification_time(verification.get("verified_at")) is None:
        return False
    return isinstance(verification_date, (datetime.date, datetime.datetime))


def pricing_row_is_publishable(row: Any) -> bool:
    """Fail-closed launch gate shared by API serialization and the scoreboard."""
    source = _pricing_row_value(row, "source")
    source_value = getattr(source, "value", source)
    source_url = _pricing_row_value(row, "source_url")
    pricing_context = _pricing_row_value(row, "pricing_context")
    currency = _pricing_row_value(row, "currency")
    period = _pricing_row_value(row, "period")
    period_value = getattr(period, "value", period)

    return (
        source_value == "official"
        and passes_pricing_gate(source_url, pricing_context)
        and _has_human_verification(
            pricing_context,
            _pricing_row_value(row, "scraped_at"),
        )
        and _valid_price_shape(row)
        and isinstance(currency, str)
        and _CURRENCY_RE.fullmatch(currency) is not None
        and period_value in _PRICE_PERIODS
    )
