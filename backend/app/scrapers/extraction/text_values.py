"""Low-level text helpers shared by the extraction modules: label cleanup, text lists, language buckets."""

from __future__ import annotations

import ast
import datetime
import json
import re
from typing import Any, Optional
from urllib.parse import urlparse

from app.schemas.extraction import (
    ExtractedLanguageFocus,
)
from app.scrapers.extraction_rules import base as rules


def _merge_text_values(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in list(primary) + list(secondary):
        normalized = str(value or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(normalized)
    return merged


def _normalize_text_list(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()

    for value in values:
        for candidate in _extract_clean_text_candidates(value):
            label = _sanitize_label(candidate)
            if not label:
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(label)
            if len(cleaned) >= rules._GENERAL_INFO_LIST_MAX_ITEMS:
                return cleaned
    return cleaned


def _normalize_scalar_text(raw_value: Any, max_len: int) -> str | None:
    candidates = _extract_clean_text_candidates(raw_value)
    if not candidates:
        return None
    for candidate in candidates:
        label = _sanitize_label(candidate, max_len=max_len)
        if label:
            return label
    return None


def _extract_clean_text_candidates(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []

    if isinstance(raw_value, (dict, list, tuple)):
        values = _flatten_structured_values(raw_value)
        return [value for value in values if value]

    text = str(raw_value).strip()
    if not text:
        return []

    structured = _try_parse_structured_text(text)
    if structured is not None:
        values = _flatten_structured_values(structured)
        return [value for value in values if value]

    # Split common multi-value separators.
    fragments = re.split(r"[;\n|]+", text)
    # A comma between digits is a decimal or thousands mark ("7774,42 лв."), not a separator.
    if len(fragments) == 1 and "," in text and len(text) < 120:
        fragments = [part.strip() for part in re.split(r"(?<!\d),|,(?!\d)", text)]
    return [fragment.strip() for fragment in fragments if fragment.strip()]


def _try_parse_structured_text(text: str) -> Any | None:
    candidate = text.strip()
    if not candidate:
        return None
    if not ((candidate.startswith("{") and candidate.endswith("}")) or (candidate.startswith("[") and candidate.endswith("]"))):
        return None

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    try:
        return ast.literal_eval(candidate)
    except (ValueError, SyntaxError):
        return None


def _flatten_structured_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, dict):
        preferred: list[str] = []
        for key in rules._GENERAL_INFO_TEXT_KEYS:
            if key in value:
                preferred.extend(_flatten_structured_values(value.get(key)))
        if preferred:
            return preferred

        collected: list[str] = []
        for nested in value.values():
            collected.extend(_flatten_structured_values(nested))
        return collected
    if isinstance(value, (list, tuple, set)):
        collected: list[str] = []
        for nested in value:
            collected.extend(_flatten_structured_values(nested))
        return collected
    text = str(value).strip()
    return [text] if text else []


def _sanitize_label(raw_text: str, max_len: int | None = None) -> str | None:
    if max_len is None:
        max_len = rules._GENERAL_INFO_ITEM_MAX_LEN

    text = str(raw_text).replace("\x00", "").strip()
    if not text:
        return None

    text = re.sub(r"^[\"'`]+|[\"'`]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;|")
    if not text:
        return None

    lowered = text.lower()
    if lowered in rules._GENERAL_INFO_JUNK_VALUES:
        return None
    if (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]")):
        return None
    if len(text) > max_len:
        return None

    # Drop values that are mostly punctuation or URLs.
    if re.fullmatch(r"[\W_]+", text):
        return None
    if text.lower().startswith(("http://", "https://", "www.")):
        return None
    return text


def _pick_primary_text_lang(country_code: str, values: list[str]) -> str:
    default = "bg" if (country_code or "").lower() == "bg" else "en"
    counts = {"bg": 0, "en": 0}
    for value in values:
        bucket = _text_lang_bucket(value)
        if bucket in counts:
            counts[bucket] += 1
    if counts[default] > 0:
        return default
    if counts["bg"] == counts["en"]:
        return default
    return "bg" if counts["bg"] > counts["en"] else "en"


def _split_text_by_lang(values: list[str], primary_lang: str) -> dict[str, list[str]]:
    by_lang = {"bg": [], "en": [], "other": []}
    for value in values:
        by_lang[_text_lang_bucket(value)].append(value)

    primary_values = by_lang[primary_lang] + by_lang["other"]
    secondary_lang = "en" if primary_lang == "bg" else "bg"
    secondary_values = by_lang[secondary_lang]
    return {"primary": primary_values, "bg": by_lang["bg"], "en": by_lang["en"], "secondary": secondary_values}


def _split_language_entries(
    values: list[ExtractedLanguageFocus],
    primary_lang: str,
) -> dict[str, list[ExtractedLanguageFocus]]:
    by_lang = {"bg": [], "en": [], "other": []}
    for value in values:
        by_lang[_text_lang_bucket(value.language)].append(value)

    secondary_lang = "en" if primary_lang == "bg" else "bg"
    return {
        "primary": by_lang[primary_lang] + by_lang["other"],
        "bg": by_lang["bg"],
        "en": by_lang["en"],
        "secondary": by_lang[secondary_lang],
    }


def _text_lang_bucket(text: str) -> str:
    sample = (text or "").strip()
    if not sample:
        return "other"
    cyrillic_count = len(re.findall(r"[А-Яа-я]", sample))
    latin_count = len(re.findall(r"[A-Za-z]", sample))
    if cyrillic_count and not latin_count:
        return "bg"
    if latin_count and not cyrillic_count:
        return "en"
    if cyrillic_count > latin_count:
        return "bg"
    if latin_count > cyrillic_count:
        return "en"
    return "other"


def _count_usable_text_values(values: list[str]) -> int:
    return sum(1 for value in values if _is_usable_text(value))


def _is_usable_text(value: Any) -> bool:
    if value is None:
        return False
    return _sanitize_label(str(value)) is not None


def _normalize_key(raw: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", (raw or "").strip().lower())


def _to_optional_float(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)

    text = str(raw).strip().replace("\xa0", " ")
    if not text:
        return None

    # Keep digits + separators, then normalize decimal separator.
    cleaned = re.sub(r"[^0-9,.-]", "", text).replace(",", ".")
    if cleaned.count(".") > 1:
        # If we got thousands separators mixed in, keep last dot as decimal.
        head, tail = cleaned.rsplit(".", 1)
        cleaned = head.replace(".", "") + "." + tail

    try:
        return float(cleaned)
    except ValueError:
        return None


def _canonical_host(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        host = (urlparse(url).hostname or "").lower().strip()
    except ValueError:
        return None
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _host_matches(page_host: Optional[str], school_host: Optional[str]) -> bool:
    if not page_host or not school_host:
        return False
    return page_host == school_host or page_host.endswith(f".{school_host}")


def _utcnow_naive() -> datetime.datetime:
    """Return UTC as naive datetime for columns declared without timezone."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
