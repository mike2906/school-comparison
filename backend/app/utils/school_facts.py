"""School facts at the publish boundary: the topics, and which stored facts may be shown.

The school-facts stage stores ``attributes["school_facts"]``; only facts that passed its
checks are stored. This drops anything malformed or from another schema version, so the API
never serialises the stored block itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import urlparse

SCHEMA_VERSION = 1
ATTRIBUTE_KEY = "school_facts"

# Each topic has a row on the compare page and a label in the frontend's i18n
# (`schoolFacts.topics.<topic>`). Order is display order.
TOPICS = (
    "curriculum",
    "languages",
    "school_day",
    "class_size",
    "meals",
    "transport",
    "activities",
    "facilities",
    "support",
    "admissions",
)


# Scrape statuses reached only after URL validation accepted the stored website as the
# school's own. ``pending`` (not checked yet), ``failed_validate`` and ``no_official_website``
# are left out. Facts do not wait for extraction, so ``extraction_failed`` still counts.
SITE_ACCEPTED_STATUSES = frozenset({"validated", "navigated", "extraction_failed", "extracted", "summarized"})


def _site(url: Any) -> str:
    """Host without www, and path, for telling a moved website from the same one."""
    if not isinstance(url, str):
        return ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return f"{host}{parsed.path.rstrip('/').lower()}" if host else ""


def publishable_school_facts(attributes: Any, website_url: Optional[str]) -> Optional[dict[str, Any]]:
    """``{"read_at", "facts": [{"topic", "text_i18n", "source_url"}]}``, or None when none can show.

    Facts show only while ``website_url`` is still the site they were read from: a school
    whose site was found wrong, replaced or removed stops showing them until it is re-read."""
    block = attributes.get(ATTRIBUTE_KEY) if isinstance(attributes, Mapping) else None
    if not isinstance(block, Mapping) or block.get("_schema_version") != SCHEMA_VERSION:
        return None
    if not _site(website_url) or _site(block.get("website_url")) != _site(website_url):
        return None
    facts = []
    for fact in block.get("facts") or []:
        if not isinstance(fact, Mapping):
            continue
        text = fact.get("text_i18n")
        url = fact.get("source_url")
        if (
            fact.get("topic") in TOPICS
            and isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and isinstance(text, Mapping)
            and all(isinstance(text.get(lang), str) and text[lang].strip() for lang in ("bg", "en"))
        ):
            facts.append({"topic": fact["topic"], "text_i18n": {"bg": text["bg"], "en": text["en"]}, "source_url": url})
    if not facts:
        return None
    return {"read_at": str(block.get("read_at") or ""), "facts": facts}
