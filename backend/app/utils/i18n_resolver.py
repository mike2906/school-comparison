from __future__ import annotations

import re
from typing import Any, Mapping

from app.utils.transliteration import transliterate_address, transliterate_bulgarian


_NAME_EN_REPLACEMENTS = (
    (re.compile(r"\bD-r\b", flags=re.IGNORECASE), "Dr."),
    (re.compile(r"\bSv\.(?=\s|$)", flags=re.IGNORECASE), "St."),
)
_INSTITUTION_EN_REPLACEMENTS = (
    (re.compile(r"\bChastna Detska Gradina\b", flags=re.IGNORECASE), "Private Kindergarten"),
    (re.compile(r"\bDetska Gradina\b", flags=re.IGNORECASE), "Kindergarten"),
    (re.compile(r"\bChastno Nachalno Uchilishte\b", flags=re.IGNORECASE), "Private Primary School"),
    (re.compile(r"\bNachalno Uchilishte\b", flags=re.IGNORECASE), "Primary School"),
    (re.compile(r"\bChastno Osnovno Uchilishte\b", flags=re.IGNORECASE), "Private Primary School"),
    (re.compile(r"\bOsnovno Uchilishte\b", flags=re.IGNORECASE), "Primary School"),
    (re.compile(r"\bObedineno Uchilishte\b", flags=re.IGNORECASE), "Unified School"),
    (re.compile(r"\bChastno Sredno Uchilishte\b", flags=re.IGNORECASE), "Private Secondary School"),
    (re.compile(r"\bSredno Uchilishte\b", flags=re.IGNORECASE), "Secondary School"),
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
_GENERIC_BG_NAME_STRIP_PREFIX = re.compile(
    r"""^(?:
        частна?\s+немска\s+гимназия|
        частна?\s+английска\s+гимназия|
        частна?\s+френска\s+гимназия|
        частн(?:а|о)?\s+(?:детска\ градина|детско\ заведение|основно\ училище|начално\ училище|средно\ училище|училище|професионална\ гимназия)|
        средно\ училище|
        основно\ училище|
        детска\ градина|
        гимназия
    )\s+""",
    flags=re.IGNORECASE | re.VERBOSE,
)
_LEGAL_ENTITY_SUFFIX_RE = re.compile(
    r"""[\s,"'„“”-]*(?:
        еоод|оод|ад|ет|еад|кд|сд|сдружение|
        eood|ood|ad|ead|ltd|llc|inc|association
    )\.?$""",
    flags=re.IGNORECASE | re.VERBOSE,
)
_GENERIC_NUMBERED_BG_DISPLAY_RE = re.compile(
    r"""^(?:№\s*)?\d+\.?\s*(?:
        ОУ|ОбУ|СУ|НУ|ПГ|ППМГ|ДГ|ЦДГ|ЧОУ|ЧСУ|ЧДГ|
        основно\ училище|
        начално\ училище|
        средно\ училище|
        обединено\ училище|
        детска\ градина|
        гимназия
    )\.?$""",
    flags=re.IGNORECASE | re.VERBOSE,
)
_GENERIC_NUMBERED_EN_DISPLAY_RE = re.compile(
    r"""^(?:No\.\s*)?\d+\.?\s*(?:
        SU|OU|NU|PG|PPMG|DG|TSDG|CHOU|CHSU|CHDG|
        primary\ school|
        secondary\ school|
        unified\ school|
        kindergarten|
        gymnasium|
        high\ school
    )\.?$""",
    flags=re.IGNORECASE | re.VERBOSE,
)


def derive_english_name(bg_name: str | None) -> str | None:
    """Derive an English display fallback from Bulgarian without persisting it."""
    if not bg_name:
        return None

    resolved = transliterate_bulgarian(bg_name).strip()
    if not resolved:
        return None
    preserve_uppercase_acronym = bool(
        re.fullmatch(r"[А-Я]{3,6}", re.sub(r"\s+", "", bg_name or ""))
    )
    if not preserve_uppercase_acronym and resolved.isupper() and any(char.isalpha() for char in resolved):
        resolved = resolved.title()

    for pattern, replacement in _NAME_EN_REPLACEMENTS:
        resolved = pattern.sub(replacement, resolved)
    for pattern, replacement in _INSTITUTION_EN_REPLACEMENTS:
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


def _clean_core_candidate(text: str | None) -> str | None:
    normalized = re.sub(r"\s+", " ", text or "").strip(' "„“”')
    if not normalized:
        return None
    cleaned = _LEGAL_ENTITY_SUFFIX_RE.sub("", normalized).strip(' "„“”,-')
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _extract_legal_fallback_name(text: str | None) -> str | None:
    normalized = re.sub(r"\s+", " ", text or "").strip(' "„“”')
    if not normalized or not _LEGAL_ENTITY_SUFFIX_RE.search(normalized):
        return None

    core = _extract_named_core(normalized)
    if core and not _is_generic_bg_label(core):
        return core

    return _clean_core_candidate(normalized)


def _extract_named_core(text: str | None) -> str | None:
    normalized = re.sub(r"\s+", " ", text or "").strip(' "„“”')
    if not normalized:
        return None

    quoted = _extract_quoted_core_name(normalized)
    if quoted:
        return _clean_core_candidate(quoted)

    honorific_match = re.search(
        r"(Д-Р|Д-р|д-р|Св\.|СВ\.|св\.)\s+[A-ZА-Я][A-Za-zА-Яа-я-]+(?:\s+[A-ZА-Я][A-Za-zА-Яа-я-]+){0,3}",
        normalized,
    )
    if honorific_match:
        return _clean_core_candidate(honorific_match.group(0))

    stripped = _GENERIC_BG_NAME_STRIP_PREFIX.sub("", normalized).strip(' "„“”')
    stripped = re.sub(
        r"^(?:с\s+ранно\s+чуждоезиково\s+обучение|с\s+немски\s+език|немска\s+гимназия|английска\s+гимназия|френска\s+гимназия)\s+",
        "",
        stripped,
        flags=re.IGNORECASE,
    ).strip(' "„“”')
    stripped = _clean_core_candidate(stripped)
    if stripped and stripped != normalized and not _is_generic_bg_label(stripped):
        return stripped

    abbreviated = _clean_core_candidate(_SCHOOL_ABBREVIATION_PREFIX.sub("", normalized).strip())
    if abbreviated and abbreviated != normalized and not _is_generic_bg_label(abbreviated):
        return abbreviated

    cleaned = _clean_core_candidate(normalized)
    if cleaned and cleaned != normalized and not _is_generic_bg_label(cleaned):
        return cleaned
    if cleaned and not _is_generic_bg_label(cleaned):
        return cleaned

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


def _prefer_raw_english_name(raw_name: Mapping[str, str] | None) -> str | None:
    cleaned = _clean_i18n_map(raw_name)
    raw_en = cleaned.get("en")
    if not raw_en or _contains_cyrillic(raw_en):
        return None
    if _LEGAL_ENTITY_SUFFIX_RE.search(raw_en):
        return None
    return raw_en


def _is_generic_bg_label(text: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").strip(' "„“”')
    if not normalized:
        return True
    return bool(_GENERIC_BG_PREFIX_RE.match(normalized))


def _contains_honorific(text: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return False
    return bool(re.search(r"\b(?:Д-Р|Д-р|д-р|Св\.|СВ\.|св\.)\b", normalized))


def is_generic_numbered_display_label(text: str | None) -> bool:
    normalized = re.sub(r"\s+", " ", text or "").strip(' "„“”')
    if not normalized:
        return False
    return bool(
        _GENERIC_NUMBERED_BG_DISPLAY_RE.fullmatch(normalized)
        or _GENERIC_NUMBERED_EN_DISPLAY_RE.fullmatch(normalized)
    )


_DISPLAY_NAME_CORROBORATION_SIGNALS = frozenset(
    {
        "website_domain_alias_match",
        "repeated_on_page_identity",
        "exact_official_page_identity",
    }
)


def _has_corroborated_display_name(attributes: Mapping[str, Any]) -> bool:
    evidence = attributes.get("display_name_evidence")
    if not isinstance(evidence, Mapping):
        return False

    raw_signals = evidence.get("signals")
    if not isinstance(raw_signals, list):
        return False

    signals = {
        str(signal)
        for signal in raw_signals
        if str(signal) in _DISPLAY_NAME_CORROBORATION_SIGNALS
    }
    return len(signals) >= 2


def _filter_display_name_i18n(display_name: Mapping[str, Any] | None) -> dict[str, str]:
    display = _clean_i18n_map(display_name)
    return {
        lang: value
        for lang, value in display.items()
        if _is_identity_display_name(value)
    }


def _is_identity_display_name(value: str | None) -> bool:
    """Reject corroborated page labels that are still prose rather than identities."""
    normalized = re.sub(r"\s+", " ", value or "").strip()
    lowered = normalized.casefold()
    if not normalized or is_generic_numbered_display_label(normalized):
        return False
    if len(normalized) > 80:
        return False
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", normalized, flags=re.IGNORECASE):
        return False
    if any(
        marker in lowered
        for marker in ("©", "all rights reserved", "всички права запазени")
    ):
        return False
    return not lowered.startswith(
        (
            "приемът ",
            "ръководство на ",
            "нашето семейство включва ",
            "our values at ",
            "да бъдеш преподавател ",
        )
    )


def resolve_display_name_i18n(attributes: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Return only display names that clear the public corroboration gate."""
    raw_attributes = dict(attributes or {})
    display_name = raw_attributes.get("display_name_i18n")
    if not _has_corroborated_display_name(raw_attributes):
        return {}
    return _filter_display_name_i18n(
        display_name if isinstance(display_name, Mapping) else None
    )


def resolve_name_i18n(
    name_i18n: Mapping[str, Any] | None,
    attributes: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Resolve the best user-facing name while preserving source-backed stored fields."""
    raw_name = _clean_i18n_map(name_i18n)
    raw_attributes = dict(attributes or {})
    display = resolve_display_name_i18n(raw_attributes)

    resolved: dict[str, str] = {}
    raw_primary_name = raw_name.get("bg") or raw_name.get("en")
    cleaned_raw_fallback = _extract_legal_fallback_name(raw_primary_name)

    bg_name = display.get("bg") or display.get("en") or cleaned_raw_fallback or raw_primary_name
    if bg_name:
        resolved["bg"] = bg_name

    en_name = _prefer_display_english(display)
    if not en_name and not display:
        en_name = _prefer_raw_english_name(raw_name)
    if not en_name:
        derived_source = raw_name.get("bg") or raw_name.get("en")
        if display.get("bg"):
            display_bg = display.get("bg")
            display_core = _extract_named_core(display_bg)
            raw_core = _extract_named_core(raw_name.get("bg"))
            if raw_core and _is_generic_bg_label(raw_core):
                raw_core = None
            if (
                raw_core
                and display_core
                and _contains_honorific(raw_core)
                and not _contains_honorific(display_core)
            ):
                preferred_core = raw_core
            else:
                preferred_core = display_core or raw_core
            derived_source = (
                preferred_core
                or display_bg
                or derived_source
            )
        elif cleaned_raw_fallback:
            derived_source = cleaned_raw_fallback
        en_name = derive_english_name(derived_source)
    if en_name:
        resolved["en"] = en_name

    return resolved


def resolve_address_i18n(address_i18n: Mapping[str, Any] | None) -> dict[str, str]:
    """Resolve the best display address without persisting synthetic EN fields."""
    raw_address = _clean_i18n_map(address_i18n)
    if not raw_address:
        return {}

    resolved: dict[str, str] = {}
    bg_address = raw_address.get("bg") or raw_address.get("en")
    if bg_address:
        resolved["bg"] = bg_address

    en_address = raw_address.get("en")
    if not en_address and raw_address.get("bg"):
        en_address = transliterate_address(raw_address["bg"]).strip() or None
    if en_address:
        resolved["en"] = en_address

    return resolved
