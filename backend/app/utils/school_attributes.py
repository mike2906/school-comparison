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

DISPLAY_LOCALES: tuple[str, ...] = ("bg", "en")

# Locale-independent display fields, read straight from the top level of `attributes`.
BASE_FIELDS: tuple[str, ...] = (
    "class_size",
    "has_canteen",
    "uniform_required",
    "special_focus",
    "teaching_approach",
)

# Free-text display fields, resolved per locale by merging `extracted` with
# `extracted_i18n[locale]`.
LOCALIZED_FIELDS: tuple[str, ...] = (
    "language_focus",
    "languages_of_instruction",
    "facilities",
    "special_programs",
    "activities_offered",
)

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


def _parse_class_size(value: Any) -> Optional[Union[int, float]]:
    """Parse a class size, refusing bare numbers with no class/group context.

    ``"Up to 16 students per class"`` → 16, but ``"Grades 1-4 program"`` → None.
    """
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return value if value > 0 else None

    if isinstance(value, str):
        normalized = value.replace(",", ".")
        try:
            direct = float(normalized)
        except ValueError:
            direct = None
        if direct is not None:
            if direct <= 0:
                return None
            return int(direct) if direct.is_integer() else direct

        lower = normalized.lower()
        if not any(marker in lower for marker in _CLASS_SIZE_MARKERS):
            return None

        match = _NUMBER_RE.search(normalized)
        if not match:
            return None
        parsed = float(match.group(0))
        if parsed <= 0:
            return None
        return int(parsed) if parsed.is_integer() else parsed

    if isinstance(value, Mapping):
        for key in ("class_size", "average", "size", "max", "value"):
            parsed = _parse_class_size(value.get(key))
            if parsed is not None:
                return parsed

    return None


def _localized_extracted(attributes: Mapping[str, Any], locale: str) -> dict[str, Any]:
    """`extracted` overlaid with this locale's overrides from `extracted_i18n`."""
    extracted = _as_mapping(attributes.get("extracted"))
    extracted_i18n = _as_mapping(attributes.get("extracted_i18n"))
    override = extracted_i18n.get(locale)
    if isinstance(override, Mapping):
        return {**extracted, **override}
    return extracted


def build_localized_attributes(
    attributes: Mapping[str, Any] | None,
    locale: str,
) -> dict[str, Any]:
    """Resolve the free-text display fields for a single locale."""
    attrs = _as_mapping(attributes)
    extracted = _localized_extracted(attrs, locale)

    focus = _normalize_focus_entries(
        _normalize_focus_entries(attrs.get("language_focus"))
        + _normalize_focus_entries(extracted.get("languages"))
    )

    return {
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
    }


def build_base_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """Resolve the locale-independent display fields."""
    attrs = _as_mapping(attributes)
    extracted = _as_mapping(attrs.get("extracted"))

    class_size = _parse_class_size(attrs.get("class_size"))
    if class_size is None:
        class_size = _parse_class_size(extracted.get("class_size"))

    return {
        "class_size": class_size,
        "has_canteen": attrs.get("has_canteen") if isinstance(attrs.get("has_canteen"), bool) else None,
        "uniform_required": attrs.get("uniform_required") if isinstance(attrs.get("uniform_required"), bool) else None,
        "special_focus": _normalize_text(attrs.get("special_focus")),
        "teaching_approach": _merged_list(attrs.get("teaching_approach")),
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
    """
    localized = [build_localized_attributes(attributes, locale) for locale in DISPLAY_LOCALES]
    text_fields = ("languages_of_instruction", "facilities", "special_programs", "activities_offered")

    merged: dict[str, Any] = {
        "teaching_approach": build_base_attributes(attributes)["teaching_approach"],
        "language_focus": _normalize_focus_entries(
            [entry for values in localized for entry in values["language_focus"]]
        ),
    }
    for field in text_fields:
        merged[field] = _dedupe_strings(value for values in localized for value in values[field])

    return merged
