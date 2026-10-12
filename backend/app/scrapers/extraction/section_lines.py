"""Pick the lines of a page section that match a field's patterns (admission, operations, services, pricing terms)."""

from __future__ import annotations

import re

from app.scrapers.extraction.text_values import (
    _extract_clean_text_candidates,
    _merge_text_values,
    _normalize_text_list,
    _sanitize_label,
)
from app.scrapers.extraction_rules import base as rules


def _collect_matching_lines(
    text: str,
    patterns: tuple[str, ...],
    require_patterns: tuple[str, ...] | None = None,
    exclude_patterns: tuple[str, ...] | None = None,
    max_items: int = 6,
) -> list[str]:
    if not text:
        return []
    lines = [line.strip() for line in re.split(r"[\n\r]+", text) if line.strip()]
    collected: list[str] = []
    seen: set[str] = set()
    for line in lines:
        lowered = line.lower()
        if exclude_patterns and _line_matches_any_pattern(lowered, exclude_patterns):
            continue
        if _is_probable_section_noise_line(lowered):
            continue
        if not any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns):
            continue
        if require_patterns and not _line_matches_any_pattern(lowered, require_patterns):
            continue
        normalized = _sanitize_label(line, max_len=180)
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        collected.append(normalized)
        if len(collected) >= max_items:
            break
    return collected


def _line_matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _normalize_heading_key(text: str) -> str:
    normalized = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text or "")
    normalized = re.sub(r"^\s*[-*#>\d().]+\s*", "", normalized)
    normalized = normalized.strip().strip(":;,.!?")
    normalized = re.sub(r"\s+", " ", normalized).lower()
    return normalized


def _is_heading_only_value(text: str, markers: tuple[str, ...]) -> bool:
    heading = _normalize_heading_key(text)
    if not heading:
        return False
    return heading in {marker.strip().lower() for marker in markers}


def _reconcile_pricing_include_exclude(
    included_items: list[str],
    excluded_items: list[str],
) -> tuple[list[str], list[str]]:
    include_values = _normalize_text_list(included_items)
    exclude_values = _normalize_text_list(excluded_items)

    moved_to_excluded: list[str] = []
    cleaned_includes: list[str] = []
    for value in include_values:
        if _line_matches_any_pattern(value.lower(), rules._PRICING_NEGATED_INCLUDE_PATTERNS):
            moved_to_excluded.append(value)
        else:
            cleaned_includes.append(value)

    merged_excluded = _merge_text_values(exclude_values, moved_to_excluded)
    return cleaned_includes, merged_excluded


def _is_probable_section_noise_line(text: str) -> bool:
    if not text:
        return True

    lowered = text.lower()
    if _line_matches_any_pattern(lowered, rules._SECTION_REGULATORY_NOISE_PATTERNS):
        return True
    if _line_matches_any_pattern(lowered, rules._MENU_BREADCRUMB_NOISE_PATTERNS):
        return True

    nav_hits = sum(
        1
        for pattern in rules._SECTION_NAV_NOISE_PATTERNS
        if re.search(pattern, lowered, flags=re.IGNORECASE)
    )
    if nav_hits >= 2:
        return True

    if nav_hits >= 1 and re.search(r"(?:\||>|»|/).*(?:\||>|»|/)", lowered):
        return True

    return False


def _extract_working_hours_value(text: str) -> str | None:
    if not text:
        return None
    snippets = _collect_matching_lines(
        text,
        rules._OPERATIONS_WORKING_HOURS_PATTERNS,
        exclude_patterns=rules._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        max_items=1,
    )
    if snippets:
        return snippets[0]
    time_match = re.search(r"\b([01]?\d|2[0-3])[:.][0-5]\d\b\s*[-–]\s*\b([01]?\d|2[0-3])[:.][0-5]\d\b", text)
    if not time_match:
        return None
    return _sanitize_label(time_match.group(0), max_len=rules._GENERAL_INFO_ITEM_MAX_LEN)


def _filter_section_values(
    values: list[str],
    require_patterns: tuple[str, ...] | None = None,
    exclude_patterns: tuple[str, ...] | None = None,
    heading_only_markers: tuple[str, ...] | None = None,
) -> list[str]:
    filtered: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        raw_text = str(raw_value or "").strip()
        if not raw_text:
            continue
        lowered_raw = raw_text.lower()
        if exclude_patterns and _line_matches_any_pattern(lowered_raw, exclude_patterns):
            continue
        if _is_probable_section_noise_line(lowered_raw):
            continue

        for candidate in _extract_clean_text_candidates(raw_value):
            label = _sanitize_label(candidate)
            if not label:
                continue
            lowered = label.lower()
            if exclude_patterns and _line_matches_any_pattern(lowered, exclude_patterns):
                continue
            if _is_probable_section_noise_line(lowered):
                continue
            if require_patterns and not _line_matches_any_pattern(lowered, require_patterns):
                continue
            if heading_only_markers and _is_heading_only_value(label, heading_only_markers):
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            filtered.append(label)
            if len(filtered) >= rules._GENERAL_INFO_LIST_MAX_ITEMS:
                return filtered
    return filtered
