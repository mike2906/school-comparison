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

import re
from typing import Any, Iterator, Mapping

# Pricing rows below this per-row confidence are withheld from the API. Imported by
# ``app.services.data_quality`` so the "pricing rows failing gates" metric measures
# exactly what the display gate hides.
PRICING_CONFIDENCE_FLOOR = 0.7

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
    """False when the current validation report is present but not ``ok`` (P1.7).

    A whole-school summary is only published for a clean report; a stored summary
    from an earlier run must be withheld once validation regresses to
    ``needs_review``, mirroring the generation-side eligibility rule.

    Deliberately *lenient* when there is no report (or no status): summaries are only
    ever generated off an ``ok`` report, so a summary without a current report is an
    already-vetted artifact — withholding it would blank legitimate content rather
    than protect against a known-bad one. The gate hides only on an explicit non-``ok``
    status.
    """
    if not isinstance(attributes, Mapping):
        return True
    report = attributes.get("data_validation")
    if not isinstance(report, Mapping):
        return True
    status = str(report.get("status") or "").strip().lower()
    return status in ("", "ok")


def passes_pricing_gate(source_url: Any, pricing_context: Any) -> bool:
    """True when a pricing row is safe to publish (has a source, confident enough)."""
    if not str(source_url or "").strip():
        return False
    confidence = pricing_context.get("confidence") if isinstance(pricing_context, Mapping) else None
    if (
        isinstance(confidence, (int, float))
        and not isinstance(confidence, bool)
        and confidence < PRICING_CONFIDENCE_FLOOR
    ):
        return False
    return True
