"""School display names: deterministic extraction from pages and the registry, aliases, normalisation and merging."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.scrapers.extraction.text_values import (
    _normalize_scalar_text,
    _text_lang_bucket,
)
from app.scrapers.school_tokens import extract_school_name_tokens
from app.utils.i18n_resolver import is_generic_numbered_display_label
from app.utils.transliteration import transliterate_bulgarian

_DISPLAY_NAME_LOCATION_TOKENS = {"софия", "sofia", "град", "grad", "city"}


_DISPLAY_NAME_GENERIC_PREFIXES = (
    "учебен комплекс",
    "частна детска градина",
    "частно детско заведение",
    "частно основно училище",
    "частно начално училище",
    "частно средно училище",
    "частна профилирана гимназия",
    "частна езикова гимназия",
    "детска градина",
    "kindergarten",
    "private kindergarten",
    "private school",
)


_DISPLAY_NAME_BG_STRIP_PREFIX = re.compile(
    r"""^(?:
        учебен\ комплекс|
        частно\ средно\ училище\ и\ детска\ градина|
        частна\ немска\ гимназия|
        частно\ средно\ училище\ с\ ранно\ чуждоезиково\ обучение|
        частно\ средно\ училище\ с\ немски\ език|
        частн(?:а|о)?\s+(?:детска\ градина|детско\ заведение|основно\ училище|начално\ училище|средно\ училище|училище)
    )\s+""",
    flags=re.IGNORECASE | re.VERBOSE,
)


_DISPLAY_NAME_EN_STRIP_PREFIX = re.compile(
    r"""^(?:
        logo|
        nemska\s+gimnaziya|
        angliyska\s+gimnaziya|
        frenska\s+gimnaziya|
        s\s+ranno\s+chuzhdoezikovo\s+obuchenie|
        s\s+nemski\s+ezik|
        chastna?\s+nemska\s+gimnaziya|
        chastna?\s+angliyska\s+gimnaziya|
        chastna?\s+frenska\s+gimnaziya|
        chastn(?:a|o)?\s+(?:detska\ gradina|detsko\ zavedenie|osnovno\ uchilishte|nachalno\ uchilishte|sredno\ uchilishte|uchilishte|profesionalna\ gimnaziya)
    )\s+""",
    flags=re.IGNORECASE | re.VERBOSE,
)


_DISPLAY_NAME_ABBREV_PREFIX = re.compile(r"^(?:ЧОУ|ЧДГ|ЧСУ|ЦДГ|ДГ|НУ|ОУ|СУ|ПГ)\b", flags=re.IGNORECASE)


_DISPLAY_NAME_ROLE_PREFIXES = (
    "директор на",
    "екип",
    "team",
    "teacher",
    "учители",
)


_HOST_ALIAS_GENERIC_PARTS = {"www", "bg", "com", "org", "net", "eu", "edu"}


_HOST_ALIAS_SPLIT_SUFFIXES = (
    "kindergarten",
    "preschool",
    "college",
    "academy",
    "school",
    "kinder",
    "kids",
    "bear",
)


_ENGLISH_INSTITUTION_SUFFIXES = ("college", "university")


def _extract_display_name_i18n_deterministic(
    text: str,
    registry_name: str | None,
    country_code: str,
    website_url: str | None = None,
    known_aliases: list[str] | None = None,
) -> dict[str, str] | None:
    effective_aliases = list(known_aliases or [])
    for alias in _derive_display_name_seed_aliases(registry_name, website_url):
        if alias and alias.casefold() not in {value.casefold() for value in effective_aliases}:
            effective_aliases.append(alias)

    if not text:
        return _extract_alias_display_name_i18n(effective_aliases, country_code) or _extract_host_aligned_display_name(
            registry_name,
            website_url,
        )

    registry_tokens = _display_name_tokens(registry_name)
    registry_match_tokens = _display_name_match_tokens(registry_name)
    alias_tokens: set[str] = set()
    alias_match_tokens: set[str] = set()
    for alias in effective_aliases:
        alias_tokens.update(_display_name_tokens(alias))
        alias_match_tokens.update(_display_name_match_tokens(alias))
    strict_alias_tokens = {token for token in alias_tokens if len(token) >= 4}
    strict_alias_match_tokens = {token for token in alias_match_tokens if len(token) >= 4}
    school_markers = (
        "училище",
        "гимназ",
        "детска градина",
        "чоу",
        "чдг",
        "school",
        "kindergarten",
        "academy",
        "college",
        "house",
    )
    junk_markers = (
        "управление на съгласието",
        "manage consent",
        "copyright",
        "all rights reserved",
        "cookie",
        "бисквит",
        "skip to content",
        "преглед на настройки",
        "manage options",
        "manage services",
        "vendors",
        "начало",
        "home",
        "контакти",
        "contact",
        "блог",
        "blog",
        "reference school",
        "@school",
    )
    junk_prefix_patterns = (
        r"^(?:discover|why|our|admissions?|curriculum|calendar|school dates|work with us)\b",
        r"^(?:да бъдеш|защо|нашите дейности|мисия и визия|мисия|визия)\b",
    )

    def normalize_candidate(raw: str) -> str | None:
        candidate = _normalize_scalar_text(raw, max_len=160)
        if not candidate:
            return None
        candidate = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", candidate)
        candidate = re.sub(
            r"^(?:лого на|logo of|история на|информация за|екип\s+|лято в\s+|защо\s+|why\s+)",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        candidate = re.sub(r"^(?:за|about)\s+(?=[A-ZА-Я0-9\"“„])", "", candidate, flags=re.IGNORECASE)
        candidate = re.split(r"\s+[-–]\s+", candidate, maxsplit=1)[0]
        candidate = candidate.strip("*_` ")
        candidate = re.sub(r"\s+(?:logo|лого)\b", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s+\|\s+(?:София|Sofia)\b", "", candidate, flags=re.IGNORECASE).strip()
        candidate = candidate.strip(" -|")
        candidate = _refine_display_name_label(candidate)
        if not candidate:
            return None
        if _is_low_quality_display_name(candidate):
            return None
        lowered = candidate.lower()
        if any(marker in lowered for marker in junk_markers):
            return None
        if re.search(r"^(?:©\s*)?\d{4}\b", candidate):
            return None
        if re.search(r"\b(?:ltd|llc|inc|eood|ood|ad)\b", lowered):
            return None
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in junk_prefix_patterns):
            return None
        if "→" in candidate:
            return None
        if lowered in {"за нас", "about us", "preschool"}:
            return None
        if lowered in {"preschool program", "high school program", "summer school & courses"}:
            return None
        if re.search(r"\b(?:е|is|are|was|were)\b", lowered) and len(candidate.split()) > 4:
            return None
        if len(candidate) < 3:
            return None
        candidate_tokens = _display_name_tokens(candidate)
        candidate_match_tokens = _display_name_match_tokens(candidate)
        candidate_lang = _text_lang_bucket(candidate)
        has_school_marker = any(marker in lowered for marker in school_markers)
        if registry_tokens and not (candidate_tokens & registry_tokens):
            alias_hits = len(candidate_tokens & strict_alias_tokens)
            alias_match_hits = len(candidate_match_tokens & strict_alias_match_tokens)
            min_alias_hits = 2 if len(strict_alias_tokens | strict_alias_match_tokens) >= 2 else 1
            registry_match_overlap = bool(candidate_match_tokens & registry_match_tokens)
            if strict_alias_tokens and alias_hits >= min_alias_hits:
                pass
            elif strict_alias_match_tokens and alias_match_hits >= min_alias_hits:
                pass
            elif registry_match_overlap and (candidate_lang != "en" or has_school_marker):
                pass
            elif strict_alias_tokens or strict_alias_match_tokens:
                return None
            elif not (candidate_lang == "en" and has_school_marker):
                return None
        if not registry_tokens and not has_school_marker:
            return None
        return candidate

    snippets = text[:4000]
    patterns = (
        r"!\[([^\]]{3,160})\]\(",
        r"\[([^\]]{3,160})\]\(https?://[^)]+\)",
        r"(?m)^#{1,3}\s+(.{3,160})$",
    )
    seen: set[str] = set()
    by_lang: dict[str, str] = {}
    fallback_en_candidate: str | None = None
    for pattern in patterns:
        for match in re.finditer(pattern, snippets, flags=re.IGNORECASE):
            raw_candidate = match.group(1)
            candidate = normalize_candidate(raw_candidate)
            if not candidate:
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            lang = _text_lang_bucket(candidate)
            if lang == "other":
                lang = "bg" if (country_code or "").lower() == "bg" else "en"
            if lang == "en" and any(marker in candidate.lower() for marker in ("school", "kindergarten", "academy")):
                fallback_en_candidate = fallback_en_candidate or candidate
            by_lang.setdefault(lang, candidate)
            if by_lang.get("bg") and by_lang.get("en"):
                return {"bg": by_lang["bg"], "en": by_lang["en"]}

    alias_display = _extract_alias_display_name_i18n(effective_aliases, country_code)
    if not by_lang:
        return alias_display or _extract_host_aligned_display_name(
            registry_name,
            website_url,
        )
    current_display: dict[str, str] | None = None
    if by_lang.get("bg") and fallback_en_candidate:
        current_display = {"bg": by_lang["bg"], "en": fallback_en_candidate}
    elif by_lang.get("bg") and by_lang.get("en"):
        current_display = {"bg": by_lang["bg"], "en": by_lang["en"]}
    elif by_lang.get("bg"):
        host_aligned = _extract_host_aligned_display_name(registry_name, website_url)
        if host_aligned and host_aligned.get("bg", "").casefold() == by_lang["bg"].casefold():
            current_display = {
                "bg": by_lang["bg"],
                "en": host_aligned.get("en") or by_lang["bg"],
            }
        else:
            current_display = {"bg": by_lang["bg"], "en": by_lang["bg"]}
    elif by_lang.get("en"):
        english_label = _augment_english_display_name(by_lang["en"], registry_name)
        current_display = {"bg": english_label, "en": english_label}

    host_aligned_display = _extract_host_aligned_display_name(registry_name, website_url)
    if _should_prefer_alias_display_name(current_display, alias_display):
        return alias_display
    if _should_prefer_alias_display_name(current_display, host_aligned_display):
        return host_aligned_display
    if current_display:
        return current_display
    return alias_display or host_aligned_display or _extract_host_aligned_display_name(
        registry_name,
        website_url,
    )


def _display_name_tokens(value: str | None) -> set[str]:
    return {
        token
        for token in extract_school_name_tokens(value, limit=10)
        if token and token not in _DISPLAY_NAME_LOCATION_TOKENS
    }


def _normalize_display_name_match_token(token: str | None) -> str:
    raw_token = (token or "").strip()
    if not raw_token:
        return ""
    normalized = transliterate_bulgarian(raw_token) if re.search(r"[А-Яа-я]", raw_token) else raw_token
    normalized = re.sub(r"[^a-z0-9]+", "", normalized.casefold())
    normalized = re.sub(r"(.)\1+", r"\1", normalized)
    return normalized


def _display_name_match_tokens(value: str | None) -> set[str]:
    return {
        normalized
        for token in _display_name_tokens(value)
        if (normalized := _normalize_display_name_match_token(token))
    }


def _extract_display_name_core(text: str | None) -> str | None:
    matches = re.findall(r'[„"“]([^"“”„]+)["”]?', text or "")
    for raw_match in reversed(matches):
        core = _normalize_scalar_text(raw_match, max_len=120)
        if not core:
            continue
        if _display_name_tokens(core):
            return core
    return None


def _is_display_name_en_transliteration(bg_value: str | None, en_value: str | None) -> bool:
    if not bg_value or not en_value or not re.search(r"[А-Яа-я]", bg_value):
        return False

    def normalize(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.lower())

    expected = transliterate_bulgarian(bg_value)
    return bool(expected) and normalize(expected) == normalize(en_value)


def _normalize_display_name_case(value: str | None) -> str | None:
    label = str(value or "").strip()
    if not label:
        return None
    if label[0].isalpha() and label[0].islower():
        return label[0].upper() + label[1:]
    return label


def _refine_display_name_label(value: str | None) -> str | None:
    label = _normalize_scalar_text(value, max_len=200)
    if not label:
        return None
    label = re.sub(r"^(?:лого|logo)\s+", "", label, flags=re.IGNORECASE).strip()
    lowered = label.lower()
    if lowered.startswith(_DISPLAY_NAME_ROLE_PREFIXES):
        return None
    if lowered.endswith("-icon") or lowered.endswith("_icon"):
        return None
    if _DISPLAY_NAME_ABBREV_PREFIX.match(label):
        return label

    quoted_core = _extract_display_name_core(label)
    if quoted_core:
        return quoted_core

    stripped = _DISPLAY_NAME_BG_STRIP_PREFIX.sub("", label).strip(" -,\"'“”„")
    stripped = re.sub(
        r"^(?:с\s+ранно\s+чуждоезиково\s+обучение|с\s+немски\s+език|немска\s+гимназия|английска\s+гимназия|френска\s+гимназия)\s+",
        "",
        stripped,
        flags=re.IGNORECASE,
    ).strip(" -,\"'“”„")
    stripped = re.sub(r"\s+софия\s+\d+$", "", stripped, flags=re.IGNORECASE).strip()
    if stripped and stripped != label and _display_name_tokens(stripped):
        return _normalize_display_name_case(stripped)

    return _normalize_display_name_case(label)


def _refine_display_name_en_label(value: str | None) -> str | None:
    label = _normalize_scalar_text(value, max_len=200)
    if not label:
        return None
    stripped = _DISPLAY_NAME_EN_STRIP_PREFIX.sub("", label).strip(" -,\"'“”„")
    if stripped and stripped != label and _display_name_tokens(stripped):
        return _normalize_display_name_case(stripped)
    return _normalize_display_name_case(label)


def _should_drop_display_name_en(bg_value: str | None, en_value: str | None) -> bool:
    bg_label = str(bg_value or "").strip()
    en_label = str(en_value or "").strip()
    if not bg_label or not en_label:
        return False
    if _is_display_name_en_transliteration(bg_value, en_value):
        return True
    normalized_en = re.sub(r"[^a-z0-9]+", " ", en_label.lower()).strip()
    return bool(normalized_en and _DISPLAY_NAME_EN_STRIP_PREFIX.match(normalized_en))


def _normalize_host_brand_text(website_url: str | None) -> str:
    host = urlparse(website_url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in re.split(r"[.-]+", host) if part]
    filtered = [part for part in parts if part not in {"bg", "com", "org", "net", "eu", "school"}]
    return "".join(filtered)


def _format_host_alias_word(value: str) -> str:
    if not value:
        return ""
    if value.isalpha() and len(value) <= 3:
        return value.upper()
    return value.title()


def _humanize_host_alias_part(value: str) -> str | None:
    compact = re.sub(r"[^a-z0-9]+", "", (value or "").lower())
    if not compact or compact in _HOST_ALIAS_GENERIC_PARTS:
        return None
    words: list[str] | None = None
    for suffix in _HOST_ALIAS_SPLIT_SUFFIXES:
        if compact.endswith(suffix) and len(compact) > len(suffix) + 1:
            prefix = compact[: -len(suffix)]
            if prefix:
                words = [prefix, suffix]
            break
    if words is None:
        if compact.isalpha() and len(compact) <= 3:
            words = [compact]
        else:
            return None
    return " ".join(_format_host_alias_word(word) for word in words if word)


def _extract_host_seed_aliases(website_url: str | None) -> list[str]:
    host = urlparse(website_url or "").netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    parts = [part for part in re.split(r"[.-]+", host) if part]
    if not parts:
        return []

    aliases: list[str] = []
    seen: set[str] = set()
    has_school_part = "school" in parts
    location_label = None
    brand_candidates: list[str] = []
    for part in parts:
        if part in _HOST_ALIAS_GENERIC_PARTS:
            continue
        if part in _DISPLAY_NAME_LOCATION_TOKENS and location_label is None:
            location_label = _format_host_alias_word(part)
            continue
        label = _humanize_host_alias_part(part)
        if label:
            brand_candidates.append(label)

    if not brand_candidates:
        return []

    brand_label = brand_candidates[-1]
    if has_school_part and location_label and location_label.casefold() not in brand_label.casefold():
        aliases.append(f"{brand_label} {location_label}")
    aliases.append(brand_label)

    deduped: list[str] = []
    for alias in aliases:
        key = alias.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(alias)
    return deduped


def _extract_mixed_script_registry_alias(
    registry_name: str | None,
    seed_aliases: list[str],
) -> str | None:
    brand_text = _extract_registry_brand_text(registry_name)
    if not brand_text:
        return None
    match = re.match(r"^([А-Я]{2,4})([а-я].+)$", brand_text)
    if not match:
        return None

    acronym = None
    for alias in seed_aliases:
        token = (alias.split() or [""])[0]
        if token.isalpha() and token.upper() == token and 2 <= len(token) <= 4:
            acronym = token.upper()
            break
    if not acronym:
        return None

    suffix = transliterate_bulgarian(match.group(2)).strip()
    suffix = re.sub(r"(?i)landiya\b", "landia", suffix)
    suffix = suffix.lstrip("- ")
    if not suffix:
        return None
    return f"{acronym}{suffix}"


def _derive_display_name_seed_aliases(
    registry_name: str | None,
    website_url: str | None,
) -> list[str]:
    aliases = _extract_host_seed_aliases(website_url)
    mixed_script_alias = _extract_mixed_script_registry_alias(registry_name, aliases)
    if mixed_script_alias:
        aliases.insert(0, mixed_script_alias)
    deduped: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        label = _normalize_scalar_text(alias, max_len=200)
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(label)
    return deduped


def _extract_registry_city_label(registry_name: str | None) -> str | None:
    candidate = " ".join((registry_name or "").replace("\n", " ").split())
    if not candidate:
        return None
    match = re.search(r"\b(?:в|гр\.?)\s+([A-ZА-Я][A-ZА-Яа-я]+)\b", candidate, flags=re.IGNORECASE)
    if not match:
        return None
    city = match.group(1).strip()
    if not city:
        return None
    city_label = transliterate_bulgarian(city) if re.search(r"[А-Яа-я]", city) else city
    return city_label.title()


def _augment_english_display_name(value: str, registry_name: str | None) -> str:
    label = " ".join((value or "").split()).strip()
    if not label:
        return label
    city_label = _extract_registry_city_label(registry_name)
    if not city_label or city_label.casefold() in label.casefold():
        return label
    if label.lower().endswith(_ENGLISH_INSTITUTION_SUFFIXES):
        return f"{label} of {city_label}"
    return label


def _extract_registry_brand_text(registry_name: str | None) -> str | None:
    candidate = " ".join((registry_name or "").replace("\n", " ").split()).strip(" \"'“”„")
    if not candidate:
        return None

    quoted = re.search(r'[„"“]([^"“”„]+)["”]', candidate)
    if quoted:
        inner = quoted.group(1).strip()
        if inner:
            return inner

    candidate = re.sub(r"\b(?:ЕООД|ООД|ЕАД|АД|СДРУЖЕНИЕ)\b.*$", "", candidate, flags=re.IGNORECASE).strip()
    candidate = re.sub(r"\s*-\s*(?:гр\.?|city)\s+.+$", "", candidate, flags=re.IGNORECASE).strip()
    candidate = re.sub(
        r"^(?:частн(?:а|о)?|държавн(?:а|о)?|начално|основно|средно|профилирана|професионална|езикова|английска|детска|градина|училище|гимназия|чоу|чдг|чсу|чну|\s)+",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" -,\"'“”„")
    return candidate or None


def _extract_host_aligned_display_name(
    registry_name: str | None,
    website_url: str | None,
) -> dict[str, str] | None:
    host_text = _normalize_host_brand_text(website_url)
    brand_text = _extract_registry_brand_text(registry_name)
    brand_tokens = _display_name_tokens(brand_text)
    if not host_text or not brand_text or not brand_tokens:
        return None

    transliterated = transliterate_bulgarian(brand_text).lower().replace(" ", "")
    compact_bg = re.sub(r"[^a-z0-9а-я]+", "", brand_text.lower())
    if len(transliterated) < 4 and len(compact_bg) < 4:
        return None
    if transliterated and transliterated not in host_text and compact_bg not in host_text:
        transliterated_tokens = [
            transliterate_bulgarian(token).lower().replace(" ", "")
            for token in brand_tokens
            if len(token) >= 4
        ]
        if not any(token and token in host_text for token in transliterated_tokens):
            return None

    en_brand = transliterate_bulgarian(brand_text) if re.search(r"[А-Яа-я]", brand_text) else brand_text
    return {"bg": brand_text, "en": en_brand}


def _normalize_display_name_i18n(
    raw_value: dict[str, str] | None,
    country_code: str,
) -> dict[str, str] | None:
    if not isinstance(raw_value, dict):
        return None

    alias_to_lang = {
        "bg": "bg",
        "bulgarian": "bg",
        "bългарски": "bg",
        "en": "en",
        "english": "en",
        "английски": "en",
    }

    cleaned: dict[str, str] = {}
    for raw_key, raw_text in raw_value.items():
        lang_key = alias_to_lang.get(str(raw_key or "").strip().lower())
        if lang_key is None:
            continue
        label = _normalize_scalar_text(raw_text, max_len=200)
        label = _refine_display_name_label(label)
        if lang_key == "en":
            label = _refine_display_name_en_label(label)
        if label and not _is_low_quality_display_name(label):
            cleaned[lang_key] = label

    if not cleaned:
        return None

    if cleaned.get("bg") and cleaned.get("en") and cleaned["bg"] == cleaned["en"] and re.search(r"[А-Яа-я]", cleaned["en"]):
        cleaned.pop("en", None)
    if _should_drop_display_name_en(cleaned.get("bg"), cleaned.get("en")):
        cleaned.pop("en", None)

    if len(cleaned) == 1:
        only_value = next(iter(cleaned.values()))
        bucket = _text_lang_bucket(only_value)
        default_lang = "bg" if (country_code or "").lower() == "bg" else "en"
        if bucket == "other":
            cleaned = {default_lang: only_value}
        elif bucket not in cleaned:
            cleaned = {bucket: only_value}

        # Reuse Latin-brand labels across locales, but avoid persisting fake
        # English by copying Bulgarian/Cyrillic text into the EN slot.
        if bucket in {"bg", "en"} and (bucket == "en" or not re.search(r"[А-Яа-я]", only_value)):
            other_lang = "en" if bucket == "bg" else "bg"
            cleaned.setdefault(other_lang, only_value)

    return cleaned


def _is_low_quality_display_name(value: str | None) -> bool:
    raw_value = (value or "").strip()
    lowered = raw_value.lower()
    if not lowered:
        return True
    if re.search(r"(?i)(?:\bemail\s*:|\b(?:e-?mail|имейл)\b|[\w.+-]+@[\w.-]+\.[a-z]{2,})", raw_value):
        return True
    if is_generic_numbered_display_label(raw_value):
        return True
    if lowered.startswith("към портал "):
        return True
    if lowered.startswith(("close submenu", "open submenu", "затвори подменю", "отвори подменю")):
        return True
    if re.search(r"(?i)\b(?:screenshot|screen shot)\b", raw_value):
        return True
    if re.search(r"(?i)\.(?:png|jpe?g|webp|svg|gif|avif)\b", raw_value):
        return True
    if any(marker in lowered for marker in ("-logo", "_logo", "logo-", "logo_", "-icon", "_icon", "icon-", "icon_")):
        return True
    if lowered in {"our kindergartens", "our schools"}:
        return True
    if lowered in {"групи", "groups"}:
        return True
    if lowered in {"preschool program", "high school program", "summer school & courses"}:
        return True
    if lowered.startswith("на "):
        return True
    # Page copy (testimonials, team/about headings, sentences) is not a school name.
    if len(raw_value.split()) > 12:
        return True
    if re.search(r"[a-zа-я]{3,}[.:]$", lowered):
        return True
    if lowered.startswith(("нашето ", "нашата ", "нашия ", "мили ", "ръководство на ", "екип на ", "our family", "dear ")):
        return True
    if lowered.startswith(("преподаватели в", "teachers at")):
        return True
    if lowered.startswith(("стратегически план", "strategic plan")):
        return True
    if lowered.startswith(("тримесечен отчет", "годишен отчет", "quarterly report", "annual report")):
        return True
    if re.search(r"\b(?:стана|беше|бе)\s+домакин\b", lowered):
        return True
    if re.search(r"(?i)\b(?:hosted|hosts?|became\s+host)\b", raw_value):
        return True
    if raw_value.startswith("!["):
        return True
    if re.fullmatch(r"\d{1,3}", raw_value):
        return True
    if lowered.endswith((" в българия", " in bulgaria")):
        return True
    if "international education bulgaria" in lowered:
        return True
    if re.match(r"^\d+\s+(?:години|years)\b", lowered):
        return True
    if any(marker in lowered for marker in ("магазин", "shop", "store")):
        return True
    if len(raw_value) <= 4 and raw_value.isupper() and raw_value.isalpha() and not re.search(r"[А-Яа-я]", raw_value):
        return True
    return False


def _display_name_specificity_score(value: str | None) -> int:
    label = (value or "").strip()
    if not label:
        return -1000
    if _is_low_quality_display_name(label):
        return -500
    lowered = label.lower()
    tokens = _display_name_tokens(label)
    score = min(len(label), 80) + len(tokens) * 12
    for prefix in _DISPLAY_NAME_GENERIC_PREFIXES:
        if lowered.startswith(prefix):
            score -= 18
            break
    score -= sum(
        10 for token in ("school", "kindergarten", "academy", "kinder", "училище", "градина", "гимназия")
        if token in lowered
    )
    return score


def _extract_alias_display_name_i18n(
    known_aliases: list[str] | None,
    country_code: str,
) -> dict[str, str] | None:
    selected: dict[str, str] = {}
    selected_scores: dict[str, int] = {}
    neutral_candidates: list[tuple[int, str]] = []
    for alias in known_aliases or []:
        label = _normalize_scalar_text(alias, max_len=200)
        if not label or _is_low_quality_display_name(label):
            continue
        bucket = _text_lang_bucket(label)
        score = _display_name_specificity_score(label)
        if bucket in {"bg", "en"}:
            if score > selected_scores.get(bucket, -1000):
                selected[bucket] = label
                selected_scores[bucket] = score
            continue
        neutral_candidates.append((score, label))

    neutral_candidates.sort(key=lambda item: item[0], reverse=True)
    if not selected and neutral_candidates:
        return _normalize_display_name_i18n({"en": neutral_candidates[0][1]}, country_code)
    if "bg" not in selected and "en" not in selected and neutral_candidates:
        return _normalize_display_name_i18n({"en": neutral_candidates[0][1]}, country_code)
    if "bg" not in selected and "en" in selected:
        return _normalize_display_name_i18n({"en": selected["en"]}, country_code)
    if "en" not in selected and "bg" in selected:
        return _normalize_display_name_i18n({"bg": selected["bg"]}, country_code)
    if not selected:
        return None
    return _normalize_display_name_i18n(selected, country_code)


def _should_prefer_alias_display_name(
    current_value: dict[str, str] | None,
    alias_value: dict[str, str] | None,
) -> bool:
    if not current_value or not alias_value:
        return False

    for lang in ("en", "bg"):
        current_label = current_value.get(lang)
        alias_label = alias_value.get(lang)
        if not current_label or not alias_label:
            continue
        current_lower = current_label.casefold()
        alias_lower = alias_label.casefold()
        if any(token in current_lower for token in ("sofia", "софия")) and not any(
            token in alias_lower for token in ("sofia", "софия")
        ):
            continue
        current_tokens = _display_name_tokens(current_label)
        alias_tokens = _display_name_tokens(alias_label)
        current_match_tokens = _display_name_match_tokens(current_label)
        alias_match_tokens = _display_name_match_tokens(alias_label)
        alias_has_school_marker = bool(
            re.search(r"(?i)\b(?:school|kindergarten|academy|college|house|училище|детска\s+градина|детска\s+къща|гимназия)\b", alias_label)
        )
        if (
            current_match_tokens
            and alias_match_tokens
            and current_match_tokens & alias_match_tokens
            and re.search(r"\b20\d{2}\b", current_label)
            and alias_has_school_marker
        ):
            return True
        if _display_name_specificity_score(alias_label) <= _display_name_specificity_score(current_label):
            continue
        if not current_tokens:
            return True
        if current_tokens.issubset(alias_tokens):
            return True
        if current_match_tokens and current_match_tokens.issubset(alias_match_tokens):
            return True
        if (
            current_match_tokens
            and alias_match_tokens
            and current_match_tokens & alias_match_tokens
            and len(alias_match_tokens) >= len(current_match_tokens)
        ):
            return True
        if current_label.casefold() in alias_label.casefold() and len(alias_label) > len(current_label):
            return True
    return False


def _merge_display_name_i18n(
    llm_value: dict[str, str] | None,
    deterministic_value: dict[str, str] | None,
    country_code: str,
) -> dict[str, str] | None:
    alias_to_lang = {
        "bg": "bg",
        "bulgarian": "bg",
        "bългарски": "bg",
        "en": "en",
        "english": "en",
        "английски": "en",
    }
    explicit_llm_langs = {
        alias_to_lang.get(str(raw_key or "").strip().lower())
        for raw_key in (llm_value or {})
    }
    explicit_llm_langs.discard(None)

    normalized_llm = _normalize_display_name_i18n(llm_value, country_code)
    normalized_deterministic = _normalize_display_name_i18n(deterministic_value, country_code)
    if not normalized_llm:
        return normalized_deterministic
    if not normalized_deterministic:
        return normalized_llm

    merged = dict(normalized_llm)
    for lang, value in normalized_deterministic.items():
        should_replace_placeholder = (
            lang == "en"
            and normalized_llm.get("bg")
            and normalized_llm.get("en") == normalized_llm.get("bg")
            and normalized_deterministic.get("en")
            and normalized_deterministic.get("en") != normalized_llm.get("en")
        )
        if lang not in explicit_llm_langs or should_replace_placeholder:
            merged[lang] = value
    return merged
