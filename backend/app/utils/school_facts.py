"""School facts at the publish boundary: the topics, and which stored facts may be shown.

The school-facts stage stores ``attributes["school_facts"]``; only facts that passed its
checks are stored. This drops anything malformed or from another schema version, so the API
never serialises the stored block itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import urlparse

from app.utils.display_lists import canonical_language
from app.utils.facility_vocabulary import APPROACH_VOCAB, FACILITY_VOCAB, PROGRAM_VOCAB
from app.utils.website_data import attributes_for_publication, website_data_withheld_reasons

SCHEMA_VERSION = 1
ATTRIBUTE_KEY = "school_facts"
# Where ``publication_attributes`` puts the facts that may show, for the display and filter
# projections; ``attributes_for_publication`` removes both keys from anything else.
PUBLISHED_KEY = "school_facts_published"

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


# A fact can name the advanced-filter tags its quote states; each feeds the filter group
# whose vocabulary has it (``compute_filter_tags``).
TAG_GROUPS = {
    **{tag: "facilities" for tag in FACILITY_VOCAB},
    **{tag: "special_programs" for tag in PROGRAM_VOCAB},
    **{tag: "teaching_approach" for tag in APPROACH_VOCAB},
}
FACT_TAGS = tuple(TAG_GROUPS)
# The language-focus levels the search filter offers (frontend ``advancedFilterOptions``).
LANGUAGE_LEVELS = ("immersion", "bilingual", "enrichment")

# How useful the checker found a fact for choosing a school. ``key`` facts are shown first
# (``MAX_KEY_FACTS`` at most, ``MAX_KEY_PER_TOPIC`` a topic; the rest shown as ``more``);
# ``minor`` facts are not shown but their tags still feed the filters.
RANKS = ("key", "more", "minor")
MAX_KEY_FACTS = 8
MAX_KEY_PER_TOPIC = 2

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


def _languages(value: Any) -> list[dict[str, Optional[str]]]:
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        language = canonical_language(item.get("language"))
        level = item.get("level") if item.get("level") in LANGUAGE_LEVELS else None
        if language and {"language": language, "level": level} not in out:
            out.append({"language": language, "level": level})
    return out


def _class_size(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 60 else None


def publishable_school_facts(attributes: Any, website_url: Optional[str]) -> Optional[dict[str, Any]]:
    """``{"read_at", "facts": [...]}``, or None when none can show.

    Each fact is ``{"topic", "text_i18n", "source_url", "rank", "tags", "languages",
    "max_class_size"}`` (never the quote), in stored order (most useful first). Only the
    first ``MAX_KEY_FACTS`` key facts, ``MAX_KEY_PER_TOPIC`` a topic, stay ``key``; later
    ones show as ``more``.

    Facts show only while ``website_url`` is still the site they were read from: a school
    whose site was found wrong, replaced or removed stops showing them until it is re-read."""
    block = attributes.get(ATTRIBUTE_KEY) if isinstance(attributes, Mapping) else None
    if not isinstance(block, Mapping) or block.get("_schema_version") != SCHEMA_VERSION:
        return None
    if not _site(website_url) or _site(block.get("website_url")) != _site(website_url):
        return None
    facts = []
    key_per_topic: dict[str, int] = {}
    for fact in block.get("facts") or []:
        if not isinstance(fact, Mapping):
            continue
        text = fact.get("text_i18n")
        url = fact.get("source_url")
        if not (
            fact.get("topic") in TOPICS
            and isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and isinstance(text, Mapping)
            and all(isinstance(text.get(lang), str) and text[lang].strip() for lang in ("bg", "en"))
        ):
            continue
        topic = fact["topic"]
        rank = fact.get("rank") if fact.get("rank") in RANKS else "key"
        if rank == "key":
            if key_per_topic.get(topic, 0) >= MAX_KEY_PER_TOPIC or sum(key_per_topic.values()) >= MAX_KEY_FACTS:
                rank = "more"
            else:
                key_per_topic[topic] = key_per_topic.get(topic, 0) + 1
        tags = fact.get("tags") if isinstance(fact.get("tags"), list) else []
        facts.append(
            {
                "topic": topic,
                "text_i18n": {"bg": text["bg"], "en": text["en"]},
                "source_url": url,
                "rank": rank,
                "tags": [tag for tag in dict.fromkeys(tags) if tag in TAG_GROUPS],
                "languages": _languages(fact.get("languages")),
                "max_class_size": _class_size(fact.get("max_class_size")),
            }
        )
    if not facts:
        return None
    return {"read_at": str(block.get("read_at") or ""), "facts": facts}


def shown_school_facts(attributes: Any, scrape_status: Any, website_url: Optional[str]) -> Optional[dict[str, Any]]:
    """The facts that may cross the API boundary (``publishable_school_facts``), or None.

    Facts carry their own page checks, so they do not wait for a Stage 6 report; a
    website-data withholding marker (failed URL check, shared site, re-extraction) still
    hides them, and so does a website URL validation has not accepted."""
    if "withheld_marker" in website_data_withheld_reasons(attributes, scrape_status):
        return None
    if str(scrape_status or "").strip().lower() not in SITE_ACCEPTED_STATUSES:
        return None
    return publishable_school_facts(attributes, website_url)


def publication_attributes(attributes: Any, scrape_status: Any, website_url: Optional[str]) -> dict[str, Any]:
    """``attributes_for_publication``, plus the shown facts under ``PUBLISHED_KEY`` so the
    display and filter projections can use their tags, languages and class size."""
    attrs = attributes_for_publication(attributes, scrape_status)
    facts = shown_school_facts(attributes, scrape_status, website_url)
    if facts:
        attrs[PUBLISHED_KEY] = facts
    return attrs
