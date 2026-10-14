"""School facts at the publish boundary: the topics, and which stored facts may be shown.

The school-facts stage stores ``attributes["school_facts"]``; only facts that passed its
checks are stored. This drops anything malformed or from another schema version, so the API
never serialises the stored block itself.
"""

from __future__ import annotations

import datetime
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

# Other labels a fact can carry, summed up per school in ``at_a_glance``.
ADMISSION_TYPES = ("entrance_exam", "interview", "trial_day", "open_entry")
PLACES = ("full", "waiting_list")
CURRICULA = ("bulgarian_state", "ib", "cambridge", "british", "american", "canadian", "french", "german")

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
        hours = _int_in(item.get("hours_per_week"), 1, 30)
        if language and all(row["language"] != language for row in out):
            out.append({"language": language, "level": level, "hours_per_week": hours})
    return out


def _int_in(value: Any, low: int, high: int) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else None


def _date(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _bilingual(value: Any, limit: int) -> list[dict[str, str]]:
    """``[{"bg", "en"}]`` names (exam subjects, bus areas), short and deduped."""
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        names = {lang: item.get(lang).strip() for lang in ("bg", "en") if isinstance(item.get(lang), str)}
        if len(names) == 2 and all(0 < len(name) <= 60 for name in names.values()) and names not in out:
            out.append(names)
    return out[:limit]


def _labels(fact: Mapping[str, Any]) -> dict[str, Any]:
    """A stored fact's labels, cleaned; anything malformed is dropped."""

    def listed(key: str, allowed: tuple[str, ...]) -> list[str]:
        return [value for value in dict.fromkeys(_as_list(fact.get(key))) if value in allowed]

    return {
        "tags": [tag for tag in dict.fromkeys(_as_list(fact.get("tags"))) if tag in TAG_GROUPS],
        "languages": _languages(fact.get("languages")),
        "max_class_size": _class_size(fact.get("max_class_size")),
        "instruction_languages": [
            name
            for name in dict.fromkeys(canonical_language(v) for v in _as_list(fact.get("instruction_languages")))
            if name
        ],
        "admission": listed("admission", ADMISSION_TYPES),
        "admission_grade": _int_in(fact.get("admission_grade"), 0, 12),
        "exam_subjects": _bilingual(fact.get("exam_subjects"), 8),
        "places": fact.get("places") if fact.get("places") in PLACES else None,
        "highest_grade": _int_in(fact.get("highest_grade"), 1, 12),
        "curricula": listed("curricula", CURRICULA),
        "diploma_recognised": fact.get("diploma_recognised")
        if isinstance(fact.get("diploma_recognised"), bool)
        else None,
        "applications_open": _date(fact.get("applications_open")),
        "applications_close": _date(fact.get("applications_close")),
        "bus_areas": _bilingual(fact.get("bus_areas"), 20),
        "special_needs_support": True if fact.get("special_needs_support") is True else None,
    }


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def at_a_glance(facts: list[Mapping[str, Any]], today: Optional[datetime.date] = None) -> dict[str, Any]:
    """The school's labels summed up from its shown facts (any rank), key facts first.

    One value fields take the first fact that has one; lists are joined. An application
    window whose closing date has passed is left out."""
    today = today or datetime.date.today()

    def first(key: str) -> Any:
        return next((fact[key] for fact in facts if fact.get(key) is not None), None)

    bus_areas: list[dict[str, str]] = []
    for fact in facts:
        bus_areas += [area for area in fact.get("bus_areas") or [] if area not in bus_areas]
    admission = []
    for fact in facts:
        if fact.get("admission") or fact.get("exam_subjects"):
            row = {
                "grade": fact.get("admission_grade"),
                "types": fact.get("admission") or [],
                "subjects": fact.get("exam_subjects") or [],
            }
            if row not in admission:
                admission.append(row)
    places = []
    for fact in facts:
        if fact.get("places") and {"grade": fact.get("admission_grade"), "status": fact["places"]} not in places:
            places.append({"grade": fact.get("admission_grade"), "status": fact["places"]})
    window = next(
        (
            {"open": f.get("applications_open"), "close": f.get("applications_close")}
            for f in facts
            if f.get("applications_open") or f.get("applications_close")
        ),
        None,
    )
    if window and window["close"] and datetime.date.fromisoformat(window["close"]) < today:
        window = None
    languages: list[dict[str, Any]] = []
    for fact in facts:
        for item in fact.get("languages") or []:
            if all(row["language"] != item["language"] for row in languages):
                languages.append(item)
    return {
        "admission": admission,
        "places": places,
        "highest_grade": first("highest_grade"),
        "curricula": [c for c in CURRICULA if c in {v for f in facts for v in f.get("curricula") or []}],
        "diploma_recognised": first("diploma_recognised"),
        "applications": window,
        "instruction_languages": list(dict.fromkeys(v for f in facts for v in f.get("instruction_languages") or [])),
        "languages": languages,
        "class_size": first("max_class_size"),
        "bus_areas": bus_areas,
        "special_needs_support": first("special_needs_support"),
    }


def _class_size(value: Any) -> Optional[int]:
    return _int_in(value, 1, 60)


def publishable_school_facts(attributes: Any, website_url: Optional[str]) -> Optional[dict[str, Any]]:
    """``{"read_at", "facts": [...], "at_a_glance": {...}}``, or None when none can show.

    Each fact is ``{"topic", "text_i18n", "source_url", "rank"}`` plus its labels
    (``_labels``), never the quote, in stored order (most useful first). Only the first
    ``MAX_KEY_FACTS`` key facts, ``MAX_KEY_PER_TOPIC`` a topic, stay ``key``; later ones
    show as ``more``.

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
        facts.append(
            {
                "topic": topic,
                "text_i18n": {"bg": text["bg"], "en": text["en"]},
                "source_url": url,
                "rank": rank,
                **_labels(fact),
            }
        )
    if not facts:
        return None
    return {"read_at": str(block.get("read_at") or ""), "facts": facts, "at_a_glance": at_a_glance(facts)}


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
