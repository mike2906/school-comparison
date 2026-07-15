"""Build the public display payload for ``schools.attributes``.

``schools.attributes`` is an internal JSONB scratchpad: it holds registry codes
(``moe_*``), scrape bookkeeping (``website_candidate_url``, ``validated_website_url``),
provenance (``source_refs``), the raw LLM payload (``extracted``, ``extracted_i18n``)
and the Stage 6 validation report (``data_validation``). None of that belongs on the
wire.

This module is the single place that projects that blob down to an explicit allowlist
of display fields. It is used by the response schemas *and* by ``SchoolService``
filtering, so the API filters and the API payload always agree on what a school's
facilities/programs/languages are.

The projection splits into two parts:

* **base** — locale-independent fields (``class_size``, ``has_canteen``, ...).
* **i18n** — fields whose values are free text and therefore differ per locale
  (``facilities``, ``special_programs``, ...). ``extracted`` holds the primary
  language; ``extracted_i18n[locale]`` holds per-locale overrides.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Optional, Union

from app.config import get_settings
from app.utils.display_gating import (
    admission_value_is_semantically_valid,
    blocked_display_fields,
)
from app.utils.facility_vocabulary import (
    APPROACH_VOCAB,
    FACILITY_VOCAB,
    PROGRAM_VOCAB,
    canonical_tags,
)

DISPLAY_LOCALES: tuple[str, ...] = ("bg", "en")

# Free-text display fields whose values are plain string lists. Resolved per locale by
# merging `extracted` with `extracted_i18n[locale]`. (`language_focus` is localized too
# but carries objects, so it is handled separately.)
TEXT_LIST_FIELDS: tuple[str, ...] = (
    "languages_of_instruction",
    "facilities",
    "special_programs",
    "activities_offered",
)

# Plausible bounds for a class/group size. Below the floor is almost certainly a
# teacher:student ratio or small-group figure; above the ceiling is total enrolment.
_MIN_CLASS_SIZE = 8
_MAX_CLASS_SIZE = 40

# Markers that make a bare number in free text plausibly a class size.
_CLASS_SIZE_MARKERS: tuple[str, ...] = (
    "class",
    "classes",
    "group",
    "groups",
    "student",
    "students",
    "children",
    "клас",
    "класове",
    "група",
    "групи",
    "ученик",
    "ученици",
    "деца",
)

# Keys worth reading when the LLM hands us an object where a string was expected.
_NAMED_VALUE_KEYS: tuple[str, ...] = (
    "name",
    "title",
    "label",
    "value",
    "program",
    "language",
    "facility",
    "accreditation",
)

_SINGLE_QUOTED_PAIR_RE = re.compile(r":\s*'([^']+)'")
_DOUBLE_QUOTED_PAIR_RE = re.compile(r":\s*\"([^\"]+)\"")
_NAMED_PAIR_RE = re.compile(
    r"['\"]?(?:name|title|label|value)['\"]?\s*:\s*['\"]([^'\"]+)['\"]",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_WHITESPACE_RE = re.compile(r"\s+")
_SCHOOL_HOURS_RANGE_RE = re.compile(
    r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)\s*(?:-|–|—|до|to)\s*"
    r"([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)",
    re.IGNORECASE,
)
_MARKDOWN_OR_URL_RE = re.compile(
    r"https?://|\b(?:www\.)?[\w.-]+\.(?:bg|com|org|net|edu)(?:/\S*)?|"
    r"!?\[[^\]]*\]\s*\([^)]*\)|(?:^|\s)#{1,6}\s|"
    r"`{1,3}|\*\*|__|~~|"
    r"(?:^|\s)\*[^*]+\*(?:\s|$)|(?:^|\s)_[^_]+_(?:\s|$)",
    re.IGNORECASE,
)
_MARKDOWN_LIST_MARKER_RE = re.compile(r"^\s*[*+\-]\s+")


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _normalize_text(value: Any) -> Optional[str]:
    if value is None or isinstance(value, Mapping) or isinstance(value, list):
        return None
    text = _WHITESPACE_RE.sub(" ", str(value)).strip()
    return text or None


def _publishable_display_text(value: Any) -> Optional[str]:
    """Normalize plain display text and reject Markdown or bare URLs globally."""
    text = _normalize_text(value)
    if (
        not text
        or _MARKDOWN_OR_URL_RE.search(text)
        or _MARKDOWN_LIST_MARKER_RE.search(str(value))
    ):
        return None
    return text


def _normalize_language_level(value: Any) -> Optional[str]:
    text = _normalize_text(value)
    if not text:
        return None
    return re.sub(r"[\s-]+", "_", text.lower())


def _extract_named_value(value: Any) -> Optional[str]:
    """Coax a display string out of whatever the extractor produced.

    Guards against two failure modes seen in real extractions: an object where a
    string was expected (``{"name": "Library", "source": "..."}``) and a stringified
    object that would otherwise render as ``{'name': 'Library'}`` in the UI.
    """
    if value is None:
        return None

    if isinstance(value, (str, int, float)):
        text = _normalize_text(value)
        if not text:
            return None

        quoted = _SINGLE_QUOTED_PAIR_RE.search(text) or _DOUBLE_QUOTED_PAIR_RE.search(text)
        if quoted:
            return _normalize_text(quoted.group(1))

        named = _NAMED_PAIR_RE.search(text)
        if named:
            return _normalize_text(named.group(1))

        if (text.startswith("{") and text.endswith("}")) or (
            text.startswith("[") and text.endswith("]")
        ):
            return None

        return text

    if not isinstance(value, Mapping):
        return None

    for key in _NAMED_VALUE_KEYS:
        if value.get(key) is not None:
            extracted = _extract_named_value(value[key])
            if extracted:
                return extracted

    for candidate in value.values():
        extracted = _extract_named_value(candidate)
        if extracted:
            return extracted

    return None


def _dedupe_strings(items: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _merged_list(*sources: Any) -> list[str]:
    flattened = (
        _extract_named_value(item) for source in sources for item in _as_list(source)
    )
    return _dedupe_strings(item for item in flattened if item)


def _normalize_focus_entries(language_focus: Any) -> list[dict[str, Optional[str]]]:
    """Normalize `language_focus` into `[{language, level}]`, deduped."""
    normalized: list[dict[str, Optional[str]]] = []

    for item in _as_list(language_focus):
        if item is None:
            continue

        if isinstance(item, str):
            text = _normalize_text(item)
            if not text:
                continue
            if ":" in text:
                language_raw, level_raw = text.split(":", 1)
                language = _normalize_text(language_raw)
                if not language:
                    continue
                normalized.append(
                    {"language": language, "level": _normalize_language_level(level_raw)}
                )
                continue
            normalized.append({"language": text, "level": None})
            continue

        if isinstance(item, Mapping):
            language = _normalize_text(item.get("language")) or _extract_named_value(item)
            if not language:
                continue
            normalized.append(
                {"language": language, "level": _normalize_language_level(item.get("level"))}
            )

    seen: set[str] = set()
    deduped: list[dict[str, Optional[str]]] = []
    for entry in normalized:
        key = f"{entry['language'].lower()}::{entry['level'] or ''}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _plausible_class_size(size: Union[int, float]) -> Optional[Union[int, float]]:
    """Reject class sizes outside a plausible range.

    A "class" of a handful of children is almost always a teacher:student ratio or a
    small-group figure that the extractor conflated with class size (e.g. ``"5 students"``
    → 5, school 510 — P1.10). Values above the ceiling are usually total enrolment. The
    smallest genuinely advertised private class sizes are ~8, so ``_MIN_CLASS_SIZE`` keeps
    those while dropping ratios; ``_MAX_CLASS_SIZE`` covers the largest kindergarten groups.
    """
    if size < _MIN_CLASS_SIZE or size > _MAX_CLASS_SIZE:
        return None
    return size


def _parse_class_size(value: Any) -> Optional[Union[int, float]]:
    """Parse a class size, refusing bare numbers with no class/group context.

    ``"Up to 16 students per class"`` → 16, but ``"Grades 1-4 program"`` → None and
    ``"5 students"`` → None (implausible; see :func:`_plausible_class_size`).
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return _plausible_class_size(value) if value > 0 else None

    if isinstance(value, str):
        normalized = value.replace(",", ".")
        try:
            direct = float(normalized)
        except ValueError:
            direct = None
        if direct is not None:
            if direct <= 0:
                return None
            direct = int(direct) if direct.is_integer() else direct
            return _plausible_class_size(direct)

        lower = normalized.lower()
        if not any(marker in lower for marker in _CLASS_SIZE_MARKERS):
            return None

        match = _NUMBER_RE.search(normalized)
        if not match:
            return None
        parsed = float(match.group(0))
        if parsed <= 0:
            return None
        parsed = int(parsed) if parsed.is_integer() else parsed
        return _plausible_class_size(parsed)

    if isinstance(value, Mapping):
        for key in ("class_size", "average", "size", "max", "value"):
            parsed = _parse_class_size(value.get(key))
            if parsed is not None:
                return parsed

    return None


def _localized_extracted(attributes: Mapping[str, Any], locale: str) -> dict[str, Any]:
    """`extracted` overlaid with this locale's overrides from `extracted_i18n`.

    English-primary schools (and any single-locale site) stash every free-text list
    under one locale's override — e.g. all facilities land in ``extracted_i18n.en`` with
    an empty primary ``extracted.facilities`` — so the requested locale's slot is empty
    and a BG viewer sees a blank section (P1.10). After applying this locale's override
    we backfill any field it left empty from another locale's override, so a viewer sees
    the extracted facts (in whatever language they exist) rather than nothing. Fields the
    requested locale already populated are never touched, so healthy per-locale content
    is never cross-contaminated.
    """
    extracted = _as_mapping(attributes.get("extracted"))
    extracted_i18n = _as_mapping(attributes.get("extracted_i18n"))
    merged = {**extracted, **_as_mapping(extracted_i18n.get(locale))}
    for other_locale in DISPLAY_LOCALES:
        if other_locale == locale:
            continue
        for key, value in _as_mapping(extracted_i18n.get(other_locale)).items():
            if not merged.get(key):
                merged[key] = value
    return merged


def build_localized_attributes(
    attributes: Mapping[str, Any] | None,
    locale: str,
) -> dict[str, Any]:
    """Resolve the free-text display fields for a single locale.

    Fields flagged by the current validation report (P1.7) are emitted empty so a
    validator-rejected value never reaches the browser.
    """
    attrs = _as_mapping(attributes)
    extracted = _localized_extracted(attrs, locale)
    blocked = blocked_display_fields(attrs)
    admission = _as_mapping(extracted.get("admission"))
    operations = _as_mapping(extracted.get("operations"))

    focus = _normalize_focus_entries(
        _normalize_focus_entries(attrs.get("language_focus"))
        + _normalize_focus_entries(extracted.get("languages"))
    )

    def admission_values(field_name: str) -> list[str]:
        return [
            value
            for value in _merged_list(admission.get(field_name))
            if admission_value_is_semantically_valid(field_name, value)
        ]

    if not get_settings().publish_website_admission_fields:
        admission = {}

    resolved = {
        "language_focus": focus,
        "languages_of_instruction": _merged_list(
            attrs.get("languages_of_instruction"),
            extracted.get("languages"),
        ),
        "facilities": _merged_list(attrs.get("facilities"), extracted.get("facilities")),
        "special_programs": _merged_list(
            attrs.get("special_programs"),
            extracted.get("programs"),
            extracted.get("accreditations"),
        ),
        "activities_offered": _merged_list(
            attrs.get("activities_offered"),
            extracted.get("extracurricular"),
        ),
        "entry_requirements": admission_values("entrance_requirements"),
        "application_deadlines": admission_values("deadlines"),
        "available_spots": admission_values("available_spots"),
        "daily_schedule": _merged_list(operations.get("daily_schedule")),
    }
    for field in blocked:
        if field in resolved:
            resolved[field] = []
    resolved["language_focus"] = [
        entry
        for entry in resolved["language_focus"]
        if _publishable_display_text(entry.get("language"))
        and (
            entry.get("level") is None
            or _publishable_display_text(entry.get("level"))
        )
    ]
    for field, values in resolved.items():
        if field == "language_focus":
            continue
        resolved[field] = [
            text
            for value in values
            if (text := _publishable_display_text(value)) is not None
        ]
    return resolved


def _bool_or_none(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _year_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= 2999 else None


def _scraped_school_hours(value: Any) -> Optional[str]:
    """Return a plausible daytime operating-hours range from scraped text.

    Golden fixtures contain false captures such as a heading with no value,
    ``18:00-7:00`` (reversed), and one-hour contact/arrival windows. Schools are not
    overnight businesses, so require an explicit forward range lasting 4–14 hours.
    The matched range is returned without surrounding Markdown heading noise.
    """
    text = _normalize_text(value)
    if not text:
        return None
    match = _SCHOOL_HOURS_RANGE_RE.search(text)
    if not match:
        return None
    start = int(match.group(1)) * 60 + int(match.group(2))
    end = int(match.group(3)) * 60 + int(match.group(4))
    duration = end - start
    if duration < 4 * 60 or duration > 14 * 60:
        return None
    return match.group(0)


def compute_filter_tags(attributes: Mapping[str, Any] | None) -> dict[str, list[str]]:
    """Map the free-text display fields onto the advanced filter's controlled vocabulary.

    Locale-independent canonical tags (``cafeteria``, ``sports_program`` …) derived from
    the (validation-gated) free text, so the browser can count/select filters and the
    ``/schools`` matcher agrees. The free text itself stays in the localized projection
    for display. See ``app.utils.facility_vocabulary`` (P1.9).
    """
    attrs = _as_mapping(attributes)
    localized = [build_localized_attributes(attrs, locale) for locale in DISPLAY_LOCALES]
    blocked = blocked_display_fields(attrs)

    def _union(field: str) -> list[str]:
        return _dedupe_strings(value for values in localized for value in values[field])

    def _nested(*path: str) -> list[str]:
        """Free text from a nested `extracted`/`extracted_i18n[locale]` list, per locale.

        Scraped rows store transport/meals and pedagogy phrases outside the projected
        lists (`extracted.operations.{transport,meals}`,
        `extracted.summary_source.teaching_approach`), so seed the mapper from them too.
        """
        collected: list[Any] = []
        for locale in DISPLAY_LOCALES:
            node = _localized_extracted(attrs, locale)
            for key in path[:-1]:
                node = _as_mapping(node.get(key))
            collected.append(node.get(path[-1]))
        return _merged_list(*collected)

    facilities_text = _union("facilities")
    programs_text = _union("special_programs")
    activities_text = _union("activities_offered")
    # The extractor often records an approach only in `summary_source` — sometimes in a
    # dedicated `teaching_approach` list, but also as distilled `canonical_tags` ("IB",
    # "Montessori") or in the `positioning`/`differentiators` marketing prose. The
    # approach vocabulary is narrow and distinctive, so mining that prose is low-risk.
    approach_text = (
        _merged_list(attrs.get("teaching_approach"))
        + _nested("summary_source", "teaching_approach")
        + _nested("summary_source", "canonical_tags")
        + _nested("summary_source", "positioning")
        + _nested("summary_source", "differentiators")
    )

    # Nested extraction sources feed the same canonical tag as their sibling display
    # field, so they are gated the same way (P1.7): transport → facilities, meals →
    # special_programs. Pedagogy → teaching_approach, which has no display-field gate.
    if "facilities" not in blocked:
        facilities_text = facilities_text + _nested("operations", "transport")
    meals_text = [] if "special_programs" in blocked else _nested("operations", "meals")

    return {
        "facilities": canonical_tags(facilities_text, FACILITY_VOCAB),
        # Programs and extracurricular both feed the "special programs" filter (a sports
        # club is a sports program); Montessori/IB live in programs, so they feed
        # teaching_approach as well.
        "special_programs": canonical_tags(
            programs_text + activities_text + meals_text, PROGRAM_VOCAB
        ),
        "teaching_approach": canonical_tags(programs_text + approach_text, APPROACH_VOCAB),
    }


def build_base_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """Resolve the locale-independent display fields.

    Fields flagged by the current validation report (P1.7) are emitted as ``None``.
    """
    attrs = _as_mapping(attributes)
    extracted = _as_mapping(attrs.get("extracted"))
    operations = _as_mapping(extracted.get("operations"))
    blocked = blocked_display_fields(attrs)

    class_size = _parse_class_size(attrs.get("class_size"))
    if class_size is None:
        class_size = _parse_class_size(extracted.get("class_size"))
    if "class_size" in blocked:
        class_size = None

    school_hours = _normalize_text(attrs.get("school_hours")) or _scraped_school_hours(
        operations.get("working_hours")
    )
    if "school_hours" in blocked:
        school_hours = None

    return {
        "class_size": class_size,
        "has_canteen": _bool_or_none(attrs.get("has_canteen")),
        "uniform_required": _bool_or_none(attrs.get("uniform_required")),
        "special_focus": _normalize_text(attrs.get("special_focus")),
        "teaching_approach": _merged_list(attrs.get("teaching_approach")),
        # Rendered by SchoolDetailPage; currently only ever written by seed_data.py.
        # `extracted.founded_year` is the scraped equivalent of established_year but is
        # deliberately not mapped here — surfacing it is a behaviour change, not a port.
        "teacher_student_ratio": _normalize_text(attrs.get("teacher_student_ratio")),
        "school_hours": school_hours,
        "established_year": _year_or_none(attrs.get("established_year")),
        # Canonical advanced-filter tags (P1.9), consumed by the frontend filter counts.
        "filter_tags": compute_filter_tags(attrs),
    }


def build_display_attributes(
    attributes: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Project raw `attributes` into `(base, {locale: localized})` for the API."""
    return (
        build_base_attributes(attributes),
        {locale: build_localized_attributes(attributes, locale) for locale in DISPLAY_LOCALES},
    )


def build_filterable_attributes(
    attributes: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Flatten the display projection into the shape filters match against.

    Filter values are language-agnostic option keys, but the underlying free text
    is per-locale, so we match against the union of every locale's values.

    ``facilities`` / ``special_programs`` / ``teaching_approach`` are the advanced-filter
    categories that offer a controlled vocabulary (``cafeteria``, ``sports_program`` …).
    The extractor emits free text for them, so here we map that free text onto the
    canonical tags (P1.9) — the display projection keeps the free text intact. The
    ``/schools`` matcher and ``/schools/filters`` both read this projection, so they
    agree on the vocabulary.
    """
    localized = [build_localized_attributes(attributes, locale) for locale in DISPLAY_LOCALES]

    def _union(field: str) -> list[str]:
        return _dedupe_strings(value for values in localized for value in values[field])

    return {
        "language_focus": _normalize_focus_entries(
            [entry for values in localized for entry in values["language_focus"]]
        ),
        # Free-text lists kept for any caller that wants the raw union; the three
        # controlled-vocabulary fields (facilities/special_programs/teaching_approach)
        # come from the shared canonical projection so options, counts, and the matcher
        # can never diverge.
        "languages_of_instruction": _union("languages_of_instruction"),
        "activities_offered": _union("activities_offered"),
        **compute_filter_tags(attributes),
    }
