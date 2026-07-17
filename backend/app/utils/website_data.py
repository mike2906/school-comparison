"""Publication state for data derived from a school's website."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


WEBSITE_DATA_WITHHELD_KEY = "website_data_withheld"
WEBSITE_PUBLISHABLE_STATUSES = frozenset({"extracted", "summarized"})

_PRIVATE_WEBSITE_ATTRIBUTE_KEYS = frozenset(
    {
        "data_validation",
        "display_name_evidence",
        "display_name_i18n",
        "extracted",
        "extracted_i18n",
        "summary_generation",
    }
)


def website_data_is_publishable(attributes: Any, scrape_status: Any) -> bool:
    """Return whether stored website-derived data may cross the API boundary.

    URL validation can fail before a replacement extraction is available.  A
    persistent marker keeps the old payload withheld across that gap and is cleared
    only after extraction and deterministic validation succeed in one transaction.
    """
    attrs = attributes if isinstance(attributes, Mapping) else {}
    status = str(scrape_status or "").strip().lower()
    report = attrs.get("data_validation")
    if not isinstance(report, Mapping):
        return False
    version = report.get("_schema_version", report.get("schema_version"))
    try:
        report_is_current = int(version) == 1
    except (TypeError, ValueError):
        report_is_current = False
    return (
        status in WEBSITE_PUBLISHABLE_STATUSES
        and not bool(attrs.get(WEBSITE_DATA_WITHHELD_KEY))
        and report_is_current
    )


def attributes_for_publication(attributes: Any, scrape_status: Any) -> dict[str, Any]:
    """Return raw attributes with private website-derived branches removed when withheld."""
    attrs = dict(attributes) if isinstance(attributes, Mapping) else {}
    if website_data_is_publishable(attrs, scrape_status):
        return attrs
    for key in _PRIVATE_WEBSITE_ATTRIBUTE_KEYS:
        attrs.pop(key, None)
    return attrs
