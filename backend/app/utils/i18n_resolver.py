from __future__ import annotations

import re
from typing import Any, Mapping

from app.utils.transliteration import transliterate_bulgarian


_NAME_EN_REPLACEMENTS = (
    (re.compile(r"\bD-r\b", flags=re.IGNORECASE), "Dr."),
    (re.compile(r"\bSv\.(?=\s|$)", flags=re.IGNORECASE), "St."),
)
_SCHOOL_ABBREVIATION_PREFIX = re.compile(r"^(?:ЧОУ|ЧДГ|ЧСУ|ЦДГ|ДГ|НУ|ОУ|СУ|ПГ)\s+", flags=re.IGNORECASE)
_GENERIC_BG_NAME_MARKERS = {
    "частно",
    "частна",
    "основно",
    "начално",
    "средно",
    "езиково",
    "профилирано",
    "училище",
    "детска",
    "градина",
    "еоод",
    "оод",
}
_GENERIC_BG_PREFIX_RE = re.compile(
    r"^(?:частн(?:а|о)?|основно|начално|средно|езиково|профилирано|училище|детска|градина|учебен|комплекс)\b",
    flags=re.IGNORECASE,
)


def derive_english_name(bg_name: str | None) -> str | None:
    """Derive an English display fallback from Bulgarian without persisting it."""
    if not bg_name:
        return None

    resolved = transliterate_bulgarian(bg_name).strip()
    if not resolved:
        return None
    if resolved.isupper() and any(char.isalpha() for char in resolved):
        resolved = resolved.title()

    for pattern, replacement in _NAME_EN_REPLACEMENTS:
        resolved = pattern.sub(replacement, resolved)

    resolved = re.sub(r"\s+", " ", resolved).strip()
    return resolved or None


def _contains_cyrillic(text: str | None) -> bool:
    return bool(text and re.search(r"[А-Яа-я]", text))


def _clean_i18n_map(value: Mapping[str, Any] | None) -> dict[str, str]:
    return {
        str(lang): str(text)
        for lang, text in dict(value or {}).items()
        if str(lang).strip() and str(text).strip()
    }


def _extract_quoted_core_name(text: str | None) -> str | None:
    matches = re.findall(r'[„"“]([^"“”„]+)["”]', text or "")
    for raw_match in reversed(matches):
        core = re.sub(r"\s+", " ", raw_match).strip()
        lowered_tokens = {token.casefold() for token in core.split()}
        if core and not lowered_tokens.issubset(_GENERIC_BG_NAME_MARKERS):
            return core
    return None


def _extract_named_core(text: str | None) -> str | None:
    normalized = re.sub(r"\s+", " ", text or "").strip(' "„“”')
    if not normalized:
        return None

    quoted = _extract_quoted_core_name(normalized)
    if quoted:
        return quoted

    honorific_match = re.search(
        r"(Д-Р|Д-р|д-р|Св\.|СВ\.|св\.)\s+[A-ZА-Я][A-Za-zА-Яа-я-]+(?:\s+[A-ZА-Я][A-Za-zА-Яа-я-]+){0,3}",
        normalized,
    )
    if honorific_match:
        return honorific_match.group(0)

    abbreviated = _SCHOOL_ABBREVIATION_PREFIX.sub("", normalized).strip()
    if abbreviated and abbreviated != normalized:
        return abbreviated

    return None


def _prefer_display_english(display_name: Mapping[str, Any] | None) -> str | None:
    display = _clean_i18n_map(display_name)
    display_bg = display.get("bg")
    display_en = display.get("en")

    if display_en:
        # Ignore duplicated Cyrillic placeholders like {"bg":"ЧОУ ...", "en":"ЧОУ ..."}.
        if display_en == display_bg and _contains_cyrillic(display_en):
            return None
        return display_en

    if display_bg and not _contains_cyrillic(display_bg):
        return display_bg

    return None


def _is_generic_bg_label(text: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").strip(' "„“”')
    if not normalized:
        return True
    return bool(_GENERIC_BG_PREFIX_RE.match(normalized))


def resolve_name_i18n(
    name_i18n: Mapping[str, Any] | None,
    attributes: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve the best user-facing name while preserving source-backed stored fields."""
    raw_name = _clean_i18n_map(name_i18n)
    raw_attributes = dict(attributes or {})
    display_name = raw_attributes.get("display_name_i18n")
    display = _clean_i18n_map(display_name if isinstance(display_name, Mapping) else None)

    resolved: dict[str, str] = {}
    bg_name = display.get("bg") or display.get("en") or raw_name.get("bg") or raw_name.get("en")
    if bg_name:
        resolved["bg"] = bg_name

    en_name = _prefer_display_english(display)
    if not en_name:
        en_name = raw_name.get("en")
    if not en_name:
        derived_source = raw_name.get("bg")
        if display.get("bg"):
            display_bg = display.get("bg")
            display_core = _extract_named_core(display_bg)
            raw_core = _extract_named_core(raw_name.get("bg"))
            if raw_core and _is_generic_bg_label(raw_core):
                raw_core = None
            derived_source = (
                raw_core
                or display_core
                or (display_bg if display_bg and not _is_generic_bg_label(display_bg) else None)
                or derived_source
            )
        en_name = derive_english_name(derived_source)
    if en_name:
        resolved["en"] = en_name

    return resolved
