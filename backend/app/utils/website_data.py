"""Publication state for data derived from a school's website."""

from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any

from app.utils.display_gating import display_name_blocked

WEBSITE_DATA_WITHHELD_KEY = "website_data_withheld"
VALIDATION_HISTORY_KEY = "data_validation_history"
VALIDATION_ATTEMPT_KEY = "data_validation_attempt"
WEBSITE_PUBLISHABLE_STATUSES = frozenset({"extracted", "summarized"})
# Scrape statuses reached only after URL validation accepted the stored website as the
# school's own. ``pending`` (not checked yet), ``failed_validate`` and ``no_official_website``
# are left out. Facts and the fees agent's rows do not wait for extraction, so
# ``extraction_failed`` still counts.
SITE_ACCEPTED_STATUSES = frozenset({"validated", "navigated", "extraction_failed", "extracted", "summarized"})

_PRIVATE_WEBSITE_ATTRIBUTE_KEYS = frozenset(
    {
        "data_validation",
        VALIDATION_HISTORY_KEY,
        VALIDATION_ATTEMPT_KEY,
        "display_name_evidence",
        "display_name_i18n",
        "extracted",
        "extracted_i18n",
        "summary_generation",
    }
)


# ``app.utils.school_facts`` and ``app.utils.website_name`` ATTRIBUTE_KEY and PUBLISHED_KEY
# (those modules import this one).
_SCHOOL_FACTS_KEYS = ("school_facts", "school_facts_published", "website_name", "website_name_published")


def _validation_report_identity(report: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    return (
        report.get("_schema_version", report.get("schema_version")),
        report.get("validated_at"),
        report.get("status"),
    )


def prepare_validation_rollover(
    attributes: Any, *, started_at: str | None = None
) -> dict[str, Any]:
    """Retain accepted evidence while withholding replacement data under validation."""
    attrs = dict(attributes) if isinstance(attributes, Mapping) else {}
    current = attrs.get("data_validation")
    attrs[VALIDATION_ATTEMPT_KEY] = {
        "status": "pending",
        "started_at": started_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "replaces_validated_at": current.get("validated_at") if isinstance(current, Mapping) else None,
    }
    attrs[WEBSITE_DATA_WITHHELD_KEY] = True
    return attrs


def promote_validation_report(
    attributes: Any,
    report: Mapping[str, Any],
    *,
    clear_replacement_withholding: bool = True,
) -> dict[str, Any]:
    """Atomically move the old current report to history and install its replacement."""
    attrs = dict(attributes) if isinstance(attributes, Mapping) else {}
    current = attrs.get("data_validation")
    history = [
        dict(item)
        for item in (attrs.get(VALIDATION_HISTORY_KEY) or [])
        if isinstance(item, Mapping)
    ]
    if isinstance(current, Mapping):
        identity = _validation_report_identity(current)
        if not any(_validation_report_identity(item) == identity for item in history):
            history.append(dict(current))
    if history:
        attrs[VALIDATION_HISTORY_KEY] = history
    attrs["data_validation"] = dict(report)
    had_replacement = isinstance(attrs.get(VALIDATION_ATTEMPT_KEY), Mapping)
    attrs.pop(VALIDATION_ATTEMPT_KEY, None)
    if had_replacement and clear_replacement_withholding:
        attrs.pop(WEBSITE_DATA_WITHHELD_KEY, None)
    return attrs


def record_validation_failure(
    attributes: Any, *, error: str, failed_at: str | None = None
) -> dict[str, Any]:
    """Withhold immediately without replacing or deleting the last accepted report."""
    attrs = dict(attributes) if isinstance(attributes, Mapping) else {}
    attempt = attrs.get(VALIDATION_ATTEMPT_KEY)
    started_at = attempt.get("started_at") if isinstance(attempt, Mapping) else None
    attrs[VALIDATION_ATTEMPT_KEY] = {
        "status": "failed",
        "started_at": started_at,
        "failed_at": failed_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "error": str(error),
    }
    attrs[WEBSITE_DATA_WITHHELD_KEY] = True
    return attrs


def website_data_withheld_reasons(attributes: Any, scrape_status: Any) -> list[str]:
    """Why stored website data may not cross the API boundary; empty when it may.

    URL validation can fail before a replacement extraction is available.  A
    persistent marker keeps the old payload withheld across that gap and is cleared
    only after extraction and deterministic validation succeed in one transaction.
    The marker is also set by re-extraction, failed validation and the shared-site check.
    """
    attrs = attributes if isinstance(attributes, Mapping) else {}
    status = str(scrape_status or "").strip().lower()
    report = attrs.get("data_validation")
    version = report.get("_schema_version", report.get("schema_version")) if isinstance(report, Mapping) else None
    try:
        report_is_current = int(version) == 1
    except (TypeError, ValueError):
        report_is_current = False
    reasons = []
    if not report_is_current:
        reasons.append("no_current_report")
    if bool(attrs.get(WEBSITE_DATA_WITHHELD_KEY)):
        reasons.append("withheld_marker")
    if status not in WEBSITE_PUBLISHABLE_STATUSES:
        reasons.append("status_not_publishable")
    return reasons


def agent_fees_publishable(attributes: Any, scrape_status: Any) -> bool:
    """Whether the fees agent's rows may cross the API boundary.

    The agent reads the fee pages itself and Stage 6 checks its rows, so they do not wait
    for the old extraction stage to move the status to ``extracted``: St George (594) has
    stayed ``validated`` with its fee rows never shown. A current Stage 6 report, no
    withholding marker and an accepted website are still required.
    """
    status = str(scrape_status or "").strip().lower()
    reasons = set(website_data_withheld_reasons(attributes, scrape_status)) - {"status_not_publishable"}
    return not reasons and status in SITE_ACCEPTED_STATUSES


def website_data_is_publishable(attributes: Any, scrape_status: Any) -> bool:
    """Return whether stored website data may cross the API boundary."""
    return not website_data_withheld_reasons(attributes, scrape_status)


def attributes_for_publication(attributes: Any, scrape_status: Any) -> dict[str, Any]:
    """Return raw attributes with private website-derived branches removed when withheld.

    School facts and the website name are always removed: they carry their own gate, and
    only ``app.utils.school_facts.publication_attributes`` adds back what may show."""
    attrs = dict(attributes) if isinstance(attributes, Mapping) else {}
    for key in _SCHOOL_FACTS_KEYS:
        attrs.pop(key, None)
    if website_data_is_publishable(attrs, scrape_status):
        # UF45 rule 5: a display name validation flagged (a sibling's name) falls back
        # to the registry name.
        if display_name_blocked(attrs):
            attrs.pop("display_name_i18n", None)
        return attrs
    for key in _PRIVATE_WEBSITE_ATTRIBUTE_KEYS:
        attrs.pop(key, None)
    return attrs
