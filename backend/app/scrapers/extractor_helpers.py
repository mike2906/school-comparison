"""Shared helper functions for extraction normalization and deterministic parsing."""

from __future__ import annotations

import ast
import contextvars
import datetime
import json
import re
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError, UnexpectedModelBehavior

from app.config import get_settings
from app.models.pricing import PriceCategory, PricePeriod
from app.models.school import School
from app.models.source_page import SourcePage
from app.schemas.extraction import (
    AdmissionExtractionOutput,
    ExtractedLanguageFocus,
    ExtractedPrice,
    GeneralInfoExtractionOutput,
    OperationsExtractionOutput,
    PriceExtractionOutput,
    PricingTermsExtractionOutput,
    ServicesExtractionOutput,
    SummarySourceExtractionOutput,
)
from app.scrapers.extraction_rules import get_rules
from app.scrapers.fee_pages import page_key
from app.scrapers.price_evidence import (
    _NUMBER_ONLY_LINE_RE,
    _gap,
    _line_of,
    amount_spans,
    currency_price_starts,
    label_spans,
    normalize_text,
    occurrence_currency,
    period_families,
    stated_period,
)
from app.scrapers.school_tokens import extract_school_name_tokens
from app.utils.academic_year import normalize_academic_year
from app.utils.i18n_resolver import is_generic_numbered_display_label
from app.utils.transliteration import transliterate_bulgarian

_ACTIVE_RULES: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "extraction_rules_module",
    default=get_rules(None),  # noqa: B039  (a module, not a mutable container)
)

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
_SUMMARY_SOURCE_MAX_ITEMS = 5
_SUMMARY_SOURCE_GENERIC_EXACT = {
    "качествено образование",
    "quality education",
    "иновативно училище",
    "innovative school",
    "модерно училище",
    "modern school",
    "подкрепяща среда",
    "supportive environment",
    "приятелска среда",
    "friendly environment",
    "сигурна среда",
    "safe environment",
    "зона за родители",
    "parents zone",
    "специализирани преподаватели",
    "teachers of sports and arts",
}
_SUMMARY_SOURCE_NAV_EXACT = {
    "начало",
    "контакти",
    "за родители",
    "академия",
    "transport",
    "транспорт",
    "методика",
    "образователен модел",
}
_SUMMARY_SOURCE_ALLOWED_SINGLE_TOKENS = {"montessori", "waldorf", "stem", "ib", "cambridge"}
_SUMMARY_SOURCE_GENERIC_PATTERNS = (
    r"^(?:иновативн(?:о|а)?|модерн(?:о|а)?|креативн(?:о|а)?|вдъхновяващ(?:о|а)?|подкрепящ(?:а|о)|"
    r"приятелск(?:а|о)|безопасн(?:а|о)|качествен(?:а|о)|цялостн(?:о|а))\s+"
    r"(?:училище|детска градина|среда|образование|общност|подход|програма)$",
    r"^(?:innovative|modern|creative|inspiring|supportive|friendly|safe|quality|holistic)\s+"
    r"(?:school|kindergarten|environment|education|community|approach|program)$",
    r"^(?:подготовка|preparing)\b.+\b(?:предизвикателствата|challenges)\b",
    r"^(?:well-rounded individuals|цялостни личности)$",
    r"^(?:©|copyright|\(c\)).*",
    r"^(?:all rights reserved|всички права запазени)\.?$",
    r".*\b(?:best education|най-доброто обучение)\b.*",
    r".*\b(?:най-голямо богатство|deserve the best|заслужават най-доброто)\b.*",
    r".*\b(?:епидемичн|epidemic|pandemic|temporarily suspended|временно прекратени)\b.*",
    r".*\b(?:recommendations how to choose|препоръки как да изберете)\b.*",
    r".*\b(?:week|седмица|ден|day|games|игри|event|събитие)\b.*\b20\d{2}(?:/\d{2,4})?\b.*",
    r".*\b(?:техническото съхранение или достъп|technical storage or access)\b.*",
    r".*\b(?:мисия и изкуство|mission and art)\b.*",
    r".*\b(?:динам(?:ичн|ic).+образователна среда|dynamic.+educational environment)\b.*",
)
_SUMMARY_SOURCE_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "teaching_approach": (
        "approach",
        "method",
        "model",
        "teaching",
        "learning",
        "learning through",
        "experiential",
        "pedagog",
        "project-based",
        "project based",
        "montessori",
        "waldorf",
        "democratic",
        "emotional intelligence",
        "critical thinking",
        "individual approach",
        "индивидуален подход",
        "проектно",
        "емоционална интелигентност",
        "мислене",
        "учене чрез",
        "обучение",
        "преподав",
        "метод",
        "подход",
        "модел",
        "педагог",
        "демократич",
    ),
    "student_experience": (
        "creative",
        "movement",
        "arts",
        "sport",
        "full-day",
        "full day",
        "daily",
        "hands-on",
        "activities",
        "summer",
        "camp",
        "meals",
        "snacks",
        "transport",
        "celebrat",
        "clubs",
        "заним",
        "творч",
        "движ",
        "спорт",
        "целоднев",
        "ежеднев",
        "дейност",
        "практическ",
        "лятна",
        "лагер",
        "хран",
        "закуск",
        "транспорт",
        "празник",
        "клуб",
    ),
    "community_signals": (
        "community",
        "parent",
        "family",
        "supportive",
        "environment",
        "partnership",
        "общност",
        "родител",
        "семей",
        "подкрепящ",
        "среда",
        "партньор",
    ),
    "differentiators": (
        "licensed",
        "license",
        "accredit",
        "author",
        "fusion",
        "synthesis",
        "international",
        "bilingual",
        "cambridge",
        "ib",
        "stem",
        "лиценз",
        "акредитац",
        "авторск",
        "синтез",
        "международ",
        "двуезич",
        "cambridge",
        "ib",
        "stem",
    ),
    "positioning": (
        "serves",
        "for children",
        "from preschool",
        "through",
        "licensed",
        "private",
        "state",
        "international",
        "приема",
        "за деца",
        "от предучилищна",
        "лиценз",
        "частн",
        "държавн",
        "международ",
    ),
}
_SUMMARY_SOURCE_PAGE_GOOD_URL_TOKENS = (
    "about",
    "za-nas",
    "program",
    "curriculum",
    "mission",
    "vision",
    "philosophy",
    "model",
    "method",
    "pedagog",
    "obuchenie",
    "obrazovatelen-model",
    "metodika",
    "approach",
    "waldorf",
    "montessori",
    "fusion",
)
_SUMMARY_SOURCE_PAGE_BAD_URL_TOKENS = (
    "news",
    "novini",
    "blog",
    "event",
    "calendar",
    "parents",
    "roditeli",
    "document",
    "docs",
    "policy",
    "gdpr",
    "contact",
    "team",
    "staff",
    "cookies",
    "admission",
    "priem",
    "pricing",
    "fees",
    "gallery",
    "vacancy",
    "konkurs",
)
_SUMMARY_SOURCE_PAGE_BAD_TEXT_TOKENS = (
    "правилник",
    "cookie",
    "cookies",
    "приемам",
    "всички права запазени",
    "all rights reserved",
    "новини",
    "news",
    "technical storage or access",
    "техническото съхранение или достъп",
    "зона за родители",
)
_SUMMARY_SOURCE_PAGE_NARRATIVE_TEXT_TOKENS = (
    "мисия",
    "визия",
    "философ",
    "подход",
    "метод",
    "модел",
    "педагог",
    "обучение",
    "учене",
    "езиков",
    "чуждоезиков",
    "монте",
    "валдорф",
    "cambridge",
    "ib",
    "fusion",
    "project-based",
    "project based",
    "language",
    "curriculum",
    "philosophy",
    "mission",
    "vision",
    "approach",
    "method",
    "model",
    "pedagog",
    "bilingual",
)
_SUMMARY_SOURCE_PAGE_OPERATIONAL_URL_TOKENS = (
    "admission",
    "priem",
    "pricing",
    "fees",
    "menu",
    "transport",
    "contact",
    "documents",
    "docs",
    "policy",
    "rules",
)
_SUMMARY_SOURCE_PAGE_OPERATIONAL_TEXT_TOKENS = (
    "прием",
    "записване",
    "такса",
    "цени",
    "меню",
    "транспорт",
    "работно време",
    "контакти",
    "документи",
    "phone",
    "email",
    "admission",
    "apply",
    "fees",
    "pricing",
    "working hours",
    "menu",
    "transport",
    "contact",
    "documents",
)
_SUMMARY_SOURCE_PAGE_NEWS_PATTERNS = (
    r"\b20\d{2}(?:/\d{2,4})?\b",
    r"\b(?:новин|news|archive|calendar|event|събит)\b",
)
_SUMMARY_SOURCE_CANONICAL_TAG_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Montessori", ("montessori", "монтесори")),
    ("Waldorf", ("waldorf", "валдорф")),
    ("Reggio Emilia", ("reggio emilia", "reggio-emilia")),
    ("IB", ("international baccalaureate", " ib ", "ib programme", "ib program", "ib diploma")),
    ("Cambridge", ("cambridge", "кембридж")),
    ("A-Level", ("a-level", "a level", "alevel")),
    ("Fusion educational model", ("fusion educational model", "fusion model", "образователния модел fusion", "модел fusion")),
    ("Project-based learning", ("project-based", "project based", "проектно базирано")),
    ("STEM-focused", (" stem ", "steam", "stem-focused", "stem focus", "stem програма")),
    ("Bilingual", ("bilingual", "двуезич")),
    ("Early foreign language education", ("early foreign language", "ранно чуждоезиково", "early language education")),
    ("German-focused", ("german-focused", "deutsch", "немски език", "германски")),
    ("French-focused", ("french-focused", "френски език", "francais")),
    ("Spanish-focused", ("spanish-focused", "испански език", "espanol")),
)

def _rules() -> Any:
    return _ACTIVE_RULES.get()

def _extract_languages_deterministic(text: str) -> list[ExtractedLanguageFocus]:
    if not text:
        return []
    seen: set[str] = set()
    results: list[ExtractedLanguageFocus] = []
    fragments = re.split(r"[\n\r.!?;]+", text)
    max_items = 8

    for fragment in fragments:
        lowered_fragment = fragment.lower().strip()
        if not lowered_fragment:
            continue
        has_context = any(marker in lowered_fragment for marker in _rules()._LANGUAGE_CONTEXT_MARKERS)
        for label, variants in _rules()._DETERMINISTIC_LANGUAGE_PATTERNS:
            if label.lower() in seen:
                continue
            for variant in variants:
                if not re.search(rf"(?<![\w-]){re.escape(variant)}(?![\w-])", lowered_fragment):
                    continue
                if not has_context and not any(marker in lowered_fragment for marker in ("english", "английски")):
                    # Keep false positives low by requiring language-ish context for most hits.
                    break
                seen.add(label.lower())
                results.append(ExtractedLanguageFocus(language=label, level=None))
                break
            if len(results) >= max_items:
                return results
    return results

def _extract_founded_year_deterministic(text: str) -> str | None:
    if not text:
        return None
    current_year = datetime.datetime.now(datetime.UTC).year
    patterns = (
        r"(?:основан[ао]?|създаден[ао]?|учреден[ао]?|established|founded)\D{0,24}((?:19|20)\d{2})",
        r"((?:19|20)\d{2})\D{0,24}(?:основан[ао]?|създаден[ао]?|учреден[ао]?|established|founded)",
    )
    candidates: list[int] = []
    lowered = text.lower()
    for pattern in patterns:
        for match in re.finditer(pattern, lowered, flags=re.IGNORECASE):
            year_text = next((group for group in match.groups() if group), None)
            if not year_text:
                continue
            year = int(year_text)
            if 1850 <= year <= current_year:
                candidates.append(year)
    if not candidates:
        return None
    return str(min(candidates))

def _extract_class_size_deterministic(text: str) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    patterns = (
        r"(?:клас(?:ове)?|груп(?:а|и)|class(?:es)?|group(?:s)?)\D{0,25}(?:до|up to|по|of|с)?\D{0,8}(\d{1,2})\D{0,12}(?:деца|ученици|students|children)?",
        r"(?:до|up to|around|около)?\D{0,6}(\d{1,2})\D{0,12}(?:деца|ученици|students|children)\D{0,15}(?:в|по|per)?\D{0,8}(?:клас|груп|class|group)?",
    )
    candidates: list[int] = []
    for pattern in patterns:
        for match in re.finditer(pattern, lowered, flags=re.IGNORECASE):
            num_text = next((group for group in match.groups() if group), None)
            if not num_text:
                continue
            value = int(num_text)
            if 5 <= value <= 40:
                candidates.append(value)
    if not candidates:
        return None
    return f"{min(candidates)} students"

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

def _extract_accreditations_deterministic(text: str) -> list[str]:
    if not text:
        return []
    fragments = [fragment.strip().lower() for fragment in re.split(r"[\n\r.!?;]+", text) if fragment.strip()]
    found: list[str] = []
    seen: set[str] = set()

    for fragment in fragments:
        has_context = any(marker in fragment for marker in _rules()._ACCREDITATION_CONTEXT_MARKERS)
        if not has_context:
            continue
        for label, keywords in _rules()._ACCREDITATION_KEYWORDS:
            if label in seen:
                continue
            if any(keyword in fragment for keyword in keywords):
                seen.add(label)
                found.append(label)
    return found

def _extract_admission_info_deterministic(text: str) -> AdmissionExtractionOutput:
    deadlines = _collect_matching_lines(
        text,
        _rules()._ADMISSION_DEADLINE_PATTERNS,
        require_patterns=_rules()._ADMISSION_DEADLINE_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    required_documents = _collect_matching_lines(
        text,
        _rules()._ADMISSION_DOCUMENT_PATTERNS,
        require_patterns=_rules()._ADMISSION_REQUIRED_DOCUMENTS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    application_steps = _collect_matching_lines(
        text,
        _rules()._ADMISSION_STEPS_PATTERNS,
        require_patterns=_rules()._ADMISSION_APPLICATION_STEPS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    entrance_requirements = _collect_matching_lines(
        text,
        _rules()._ADMISSION_ENTRANCE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    available_spots = _collect_matching_lines(
        text,
        _rules()._ADMISSION_SPOTS_PATTERNS,
        require_patterns=_rules()._ADMISSION_AVAILABLE_SPOTS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )

    parsed = AdmissionExtractionOutput(
        deadlines=deadlines,
        required_documents=required_documents,
        application_steps=application_steps,
        entrance_requirements=entrance_requirements,
        available_spots=available_spots,
        has_useful_info=False,
    )
    parsed.has_useful_info = any(
        (
            parsed.deadlines,
            parsed.required_documents,
            parsed.application_steps,
            parsed.entrance_requirements,
            parsed.available_spots,
        )
    )
    return parsed

def _extract_operations_info_deterministic(text: str) -> OperationsExtractionOutput:
    working_hours = _extract_working_hours_value(text)
    day_options = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_DAY_OPTIONS_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    daily_schedule = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_DAILY_SCHEDULE_PATTERNS,
        require_patterns=_rules()._OPERATIONS_DAILY_SCHEDULE_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    meals = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_MEALS_PATTERNS,
        require_patterns=_rules()._OPERATIONS_MEALS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    transport = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_TRANSPORT_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    uniforms = _collect_matching_lines(
        text,
        _rules()._OPERATIONS_UNIFORMS_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )

    parsed = OperationsExtractionOutput(
        working_hours=working_hours,
        day_options=day_options,
        daily_schedule=daily_schedule,
        meals=meals,
        transport=transport,
        uniforms=uniforms,
        has_useful_info=False,
    )
    parsed.has_useful_info = any(
        (
            parsed.working_hours,
            parsed.day_options,
            parsed.daily_schedule,
            parsed.meals,
            parsed.transport,
            parsed.uniforms,
        )
    )
    return parsed

def _extract_services_info_deterministic(text: str) -> ServicesExtractionOutput:
    support_services = _collect_matching_lines(
        text,
        _rules()._SERVICES_SUPPORT_PATTERNS,
        require_patterns=_rules()._SERVICES_SUPPORT_REQUIRE_PATTERNS,
        exclude_patterns=(*_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS, *_rules()._SERVICES_SUPPORT_EXCLUDE_PATTERNS),
    )
    safety_features = _collect_matching_lines(
        text,
        _rules()._SERVICES_SAFETY_PATTERNS,
        require_patterns=_rules()._SERVICES_SAFETY_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    parsed = ServicesExtractionOutput(
        support_services=support_services,
        safety_features=safety_features,
        has_useful_info=False,
    )
    parsed.has_useful_info = any((parsed.support_services, parsed.safety_features))
    return parsed

def _extract_pricing_terms_deterministic(text: str) -> PricingTermsExtractionOutput:
    discounts = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_DISCOUNT_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_DISCOUNTS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    installments = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_INSTALLMENTS_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_INSTALLMENTS_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    included_items = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_INCLUDED_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_INCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    excluded_items = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_EXCLUDED_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_EXCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    deposits = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_DEPOSIT_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_DEPOSIT_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    application_fees = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_APPLICATION_FEE_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_APPLICATION_FEE_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    registration_fees = _collect_matching_lines(
        text,
        _rules()._PRICING_TERMS_REGISTRATION_FEE_PATTERNS,
        require_patterns=_rules()._PRICING_TERMS_REGISTRATION_FEE_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    parsed = PricingTermsExtractionOutput(
        discounts=discounts,
        installments=installments,
        included_items=included_items,
        excluded_items=excluded_items,
        deposits=deposits,
        application_fees=application_fees,
        registration_fees=registration_fees,
        has_useful_info=False,
    )
    parsed.has_useful_info = any(
        (
            parsed.discounts,
            parsed.installments,
            parsed.included_items,
            parsed.excluded_items,
            parsed.deposits,
            parsed.application_fees,
            parsed.registration_fees,
        )
    )
    return parsed

_PRICE_LINE_AMOUNT_RE = re.compile(
    r"(?:(€|eur|euro|евро|лв\.?|лева?|bgn)\s*([\d][\d\s.,]*))|(?:([\d][\d\s.,]*)\s*(€|eur|euro|евро|лв\.?|лева?|bgn))",
    flags=re.IGNORECASE,
)
_PRICE_LINE_AMOUNT_LATIN_RE = re.compile(
    r"(?:(€|eur|euro|лв\.?|bgn)\s*([\d][\d\s.,]*))|(?:([\d][\d\s.,]*)\s*(€|eur|euro|лв\.?|bgn))",
    flags=re.IGNORECASE,
)
_OPTIONAL_PRICING_SECTION_TOKENS = (
    "other charges",
    "other fees",
    "услуги по желание",
    "по желание на родителите",
    "допълнително заплащане",
    "допълнителни услуги",
    "допълнителни такси",
    "additional services",
    "additional fees",
    "additional charges",
    "optional services",
)


def _clean_price_line(raw: str) -> str:
    line = re.sub(r"\[(.*?)\]\([^)]*\)", r"\1", raw or "")
    line = re.sub(r"^[#>*\-\s`_]+", "", line)
    line = line.replace("**", "").replace("__", "")
    line = re.sub(r"\s+", " ", line).strip(" -:\t")
    return line.strip()


def _parse_price_amount_token(raw: str) -> float | None:
    token = re.sub(r"[^0-9,.\s]", "", str(raw or "")).replace("\xa0", " ").strip()
    if not token:
        return None

    token = re.sub(r"\s+", "", token)
    if not token:
        return None

    if "," in token and "." in token:
        if token.rfind(",") > token.rfind("."):
            token = token.replace(".", "").replace(",", ".")
        else:
            token = token.replace(",", "")
    elif token.count(",") > 1:
        token = token.replace(",", "")
    elif token.count(".") > 1:
        token = token.replace(".", "")
    elif "," in token:
        head, tail = token.split(",", 1)
        token = head + tail if len(tail) == 3 else f"{head}.{tail}"
    elif "." in token:
        head, tail = token.split(".", 1)
        token = head + tail if len(tail) == 3 else f"{head}.{tail}"

    try:
        return float(token)
    except ValueError:
        return None


# A full Bulgarian currency word written directly against its amount ("500евро",
# "1200лева"), and not itself the start of a longer word.
_GLUED_BG_CURRENCY_WORD_RE = re.compile(r"(\d)(евро|лева)(?![^\W\d_])", re.IGNORECASE)


def _space_glued_currency_words(text: str) -> str:
    """Separate a glued Bulgarian currency word from its amount, for the LLM prompt only.

    The model reliably misses "500евро" yet reliably reads "500 евро". Only the full words
    are rewritten: abbreviations such as "лв" already work glued, so they are left alone.
    Callers must keep using the original text for deterministic parsing and evidence
    checks, which deliberately do not treat these words as currencies.
    """
    return _GLUED_BG_CURRENCY_WORD_RE.sub(r"\1 \2", text or "")


def _extract_price_amount_currency(
    value: str, *, allow_cyrillic_currency_words: bool = True
) -> tuple[float | None, str | None]:
    pattern = (
        _PRICE_LINE_AMOUNT_RE
        if allow_cyrillic_currency_words
        else _PRICE_LINE_AMOUNT_LATIN_RE
    )
    match = pattern.search(value or "")
    if not match:
        return None, None
    currency_token = match.group(1) or match.group(4) or ""
    amount_token = match.group(2) or match.group(3) or ""
    amount = _parse_price_amount_token(amount_token)
    if amount is None:
        return None, None
    currency_normalized = currency_token.lower().strip(". ")
    if currency_normalized in {"€", "eur", "euro", "евро"}:
        return amount, "EUR"
    if currency_normalized in {"лв", "лев", "лева", "bgn"}:
        return amount, "BGN"
    return amount, None


def _detect_price_category(value: str, *, allow_generic_heading: bool = False) -> str | None:
    lowered = value.lower()
    if any(token in lowered for token in ("late fee", "late-payment", "late payment", "просроч")):
        return None
    if "обучение по " in lowered:
        return "extracurricular"
    if "обуч" in lowered or "образователни услуги" in lowered or "tuition" in lowered:
        return "tuition"
    if any(
        token in lowered
        for token in ("school bus", "bus service", "автобус", "транспорт", "transport")
    ):
        return "transport"
    if any(
        token in lowered
        for token in (
            "образователни ресурси",
            "учебниц",
            "консуматив",
            "materials",
            "language books",
            "foreign language books",
        )
    ):
        return "materials"
    if any(token in lowered for token in ("храна", "food", "meal", "meals", "lunch", "snack", "закуска", "обяд")):
        return "food"
    if re.search(r"\b(?:месечна|годишна)\s+такса\b", lowered):
        return "tuition"
    if "униформ" in lowered or "uniform" in lowered:
        return "uniforms"
    if any(
        token in lowered
        for token in (
            "регистрац",
            "registration",
            "admission",
            "enrollment",
            "кандидатств",
            "application fee",
            "deposit",
            "депозит",
            "capital fee",
            "новопостъп",
        )
    ):
        return "registration"
    if "лагер" in lowered or "camp" in lowered:
        return "camp"
    if any(
        token in lowered
        for token in (
            "извънклас",
            "extracurricular",
            "допълнителни дейности",
            "additional activities",
            "курс",
            "course",
            "урок",
            "уроци",
            "lesson",
            "lessons",
            "занимания",
            "training",
            "trainings",
            "отбор",
            "teams",
            "музикален инструмент",
            "танц",
            "плуване",
            "тенис",
            "футбол",
            "балет",
            "гимнастика",
            "volleyball",
            "english lessons",
        )
    ):
        return "extracurricular"
    if "удължен" in lowered or "extended day" in lowered:
        return "extended_day"
    if allow_generic_heading and any(token in lowered for token in _OPTIONAL_PRICING_SECTION_TOKENS):
        return None
    if allow_generic_heading and re.search(r"\b(такси|fees?)\b", lowered):
        return "tuition"
    return None


def _is_price_category_heading(value: str) -> bool:
    lowered = value.casefold()
    return not any(
        token in lowered
        for token in (
            "включ",
            "изключ",
            "не е",
            "не e",
            "заплаща",
            "included",
            "excluded",
            "does not include",
        )
    )


def _starts_new_price_section(value: str) -> bool:
    """Identify a new fee section, excluding table labels and explanatory prose."""
    return value.lstrip().startswith("#") or bool(
        re.match(
            r"^(?:tuition|school|food|meal|transport)\s+fees?\s+for\b|"
            r"^такс\w*\s+за\s+(?:обучение|храна|транспорт)\b",
            value,
            flags=re.IGNORECASE,
        )
    )


def _detect_price_age_group(value: str) -> str | None:
    lowered = value.lower()
    if "предучилищ" in lowered:
        return "preschool"
    range_match = re.search(r"(\d+\s*[-–]\s*\d+\s*клас)", value, flags=re.IGNORECASE)
    if range_match:
        return range_match.group(1)
    multi_grade_match = re.search(r"(\d(?:\s*,\s*\d+)+(?:\s*и\s*\d+)?\s*клас)", value, flags=re.IGNORECASE)
    if multi_grade_match:
        digits = [int(part) for part in re.findall(r"\d+", multi_grade_match.group(1))]
        if digits:
            return f"{min(digits)}-{max(digits)} клас"
    single_grade_match = re.search(r"(\d+\s*клас)", value, flags=re.IGNORECASE)
    if single_grade_match:
        return single_grade_match.group(1)
    return None


_INSTALLMENT_MULTIPLIER_RE = re.compile(
    r"\b\d{1,2}\s*[×xXхХ]\s*[\d.,\s]*\s*(?:€|eur|euro|лв|bgn)",
    flags=re.IGNORECASE,
)
_PAYMENT_SCHEDULE_FREQUENCY_RE = re.compile(
    r"\b(?:monthly|quarterly)\s+(?:paid|payment|instalments?|installments?)|"
    r"\b(?:paid|payable)\s+(?:monthly|quarterly)\b|"
    r"\b(?:instalments?|installments?|вноск\w*)\b|"
    r"\bежемесечн\w*\s+(?:плащ\w*|вноск\w*)\b",
    flags=re.IGNORECASE,
)


def _detect_explicit_price_period(value: str) -> str | None:
    lowered = value.casefold()
    if re.search(
        r"\b(?:one[- ]time|single)\b.{0,30}\b(?:fee|charge|deposit)\b|"
        r"\b(?:fee|charge|deposit)\b.{0,30}\bone[ -]time\b|"
        r"\bеднократн\w*(?:\s+\w+){0,2}\s+такс\w*\b",
        lowered,
    ):
        return "one_time"
    if re.search(
        r"\b(?:per|each)\s+semester\b|\bsemester(?:ly)?\s+(?:fee|tuition)\b|"
        r"\b(?:на|за)\s+(?:един\s+)?семестър\b|\bсеместриална\s+такса\b",
        lowered,
    ):
        return "semester"
    if (
        re.search(r"\b(?:per|each)\s+quarter\b", lowered)
        and not re.search(r"\b(?:paid|payable)\s+(?:per|each)\s+quarter\b", lowered)
    ) or re.search(
        r"\bquarterly\s+(?:(?:tuition|school|meal|food|transport)\s+)?(?:fee|charge)\b|"
        r"\b(?:на|за)\s+(?:едно\s+)?тримесечие\b|\bтримесечна\s+такса\b",
        lowered,
    ):
        return "quarter"
    if re.search(
        r"\b(?:per|each)\s+term\b|\bterm(?:ly)?\s+(?:fee|tuition)\b|"
        r"\b(?:на|за)\s+(?:един\s+)?(?:учебен\s+)?срок\b|\bтакса\s+за\s+срок\b",
        lowered,
    ):
        return "term"
    if (
        re.search(r"\b(?:annual|yearly)\s+(?:(?:tuition|school)\s+)?fee\b", lowered)
        or re.search(r"\bannual\s+tuition\b", lowered)
        or re.search(r"\bгодишн\w*\s+(?:учебн\w*\s+)?такс\w*\b", lowered)
        or re.search(r"\bper\s+(?:(?:academic|school)\s+)?year\b", lowered)
        or re.search(r"\bfor\s+(?:one|the\s+(?:whole|entire))\s+(?:academic|school)\s+year\b", lowered)
        or re.search(r"\bза\s+(?:една|цялата)\s+уч(?:ебна|\.)?\s*година\b", lowered)
        or re.search(r"\bна\s+година\b", lowered)
        or re.search(r"/\s*(?:per\s+)?(?:year|година)\b", lowered)
    ):
        return "yearly"
    if (
        re.search(r"\bmonthly\s+(?:tuition|fee)\b", lowered)
        or re.search(r"\b(?:tuition|fee)\b.{0,30}\bper\s+month\b", lowered)
        or re.search(r"\bмесечн\w*\s+такс\w*\b", lowered)
        or re.search(r"\bмесечно\s*(?:половин|целодневно)", lowered)
        or re.search(r"\bтакс\w*\b(?:(?!плащ|внас).){0,30}\bна\s+месец\b", lowered)
        or re.search(r"\bтакс\w*\s+за\s+месец\b", lowered)
        or re.search(r"/\s*(?:per\s+)?(?:month|месец)\b", lowered)
    ):
        return "monthly"
    if (
        re.search(r"\bсе\s+заплаща\s+еднократно\b", lowered)
        and not re.search(r"\bили\b.{0,40}\bвноск\w*\b", lowered)
        and not re.search(r"\bвсяка\s+учебна\s+година\b", lowered)
    ):
        return "one_time"
    return None


def _detect_price_period(
    value: str,
    default_period: str | None = None,
) -> str | None:
    explicit = _detect_explicit_price_period(value)
    if explicit is not None:
        return explicit
    if _PAYMENT_SCHEDULE_FREQUENCY_RE.search(value):
        if (
            default_period is not None
            and re.search(r"\b(?:paid|payable)\s+(?:monthly|quarterly)\b", value, re.IGNORECASE)
            and not re.search(r"\b(?:instalments?|installments?|payments?|вноск\w*)\b", value, re.IGNORECASE)
        ):
            return default_period
        return None
    # Callers only supply a default for an explicit period heading in the current
    # fee section. Academic years, fee categories, amounts, and installment counts
    # are deliberately not period evidence.
    return default_period


def _section_period_applies_to_price_line(
    value: str,
    section_heading: str | None,
    payment_plan_heading: bool,
) -> bool:
    if not payment_plan_heading:
        return True
    # A mixed table can explicitly label its first amount as the full fee while
    # also showing installment alternatives. The section's stated period applies
    # to that total, but not to standalone installment amounts.
    return (
        len(_PRICE_LINE_AMOUNT_RE.findall(value)) > 1
        and any(
            token in (section_heading or "").casefold()
            for token in ("full fee", "пълна такса")
        )
    )


def _is_penalty_price_line(value: str) -> bool:
    lowered = (value or "").casefold()
    return any(
        token in lowered
        for token in (
            "late fee",
            "late-payment",
            "late payment",
            "surcharge for any payments made after",
            "такса за просроч",
            "неустойк",
        )
    )


def _period_on_following_line(lines: list[str], line_index: int) -> str | None:
    if line_index + 1 >= len(lines):
        return None
    following = lines[line_index + 1]
    if _extract_price_amount_currency(following)[0] is not None:
        return None
    if _detect_price_category(following, allow_generic_heading=True) is not None:
        return None
    return _detect_explicit_price_period(following)


def _iter_price_line_signals(text: str) -> list[dict[str, Any]]:
    lines = [_clean_price_line(raw) for raw in re.split(r"[\n\r]+", text) if _clean_price_line(raw)]
    if not lines:
        return []

    signals: list[dict[str, Any]] = []
    current_category: str | None = None
    current_age_group: str | None = None
    current_academic_year: str | None = None
    current_period: str | None = None
    current_payment_plan = False
    current_heading: str | None = None
    current_source_url: str | None = None
    source_staleness = _pricing_source_staleness(text)

    for line_index, line in enumerate(lines):
        lowered = line.lower()
        if lowered.startswith("source:"):
            current_source_url = re.sub(
                r"\s*---\s*$", "", line.split(":", 1)[1]
            ).strip() or None
            current_category = None
            current_age_group = None
            current_academic_year = None
            current_period = None
            current_payment_plan = False
            current_heading = None
            continue
        line_amount, line_currency = _extract_price_amount_currency(line)
        academic_year_match = re.search(r"(20\d{2}\s*[-/]\s*20\d{2})", line)
        if academic_year_match:
            current_academic_year = academic_year_match.group(1).replace(" ", "")

        if line_amount is None and any(token in lowered for token in _OPTIONAL_PRICING_SECTION_TOKENS):
            current_category = None
            current_age_group = None
            current_period = None
            current_payment_plan = False
            current_heading = None
            continue

        if line_amount is None and current_period == "monthly" and lowered.startswith("еднократно плащане"):
            current_period = None

        line_category = _detect_price_category(line, allow_generic_heading=True)
        if line_category is not None and line_amount is None and _is_price_category_heading(line):
            if line_category != current_category or _starts_new_price_section(line):
                current_period = None
                current_payment_plan = False
            current_category = line_category
            current_heading = line
            detected_age_group = _detect_price_age_group(line)
            if detected_age_group is not None:
                current_age_group = detected_age_group

        explicit_period = _detect_explicit_price_period(line)
        if explicit_period is not None and line_amount is None:
            previous_amount = (
                _extract_price_amount_currency(lines[line_index - 1])[0]
                if line_index > 0 else None
            )
            if previous_amount is None or line_category is not None:
                current_period = explicit_period
                current_payment_plan = False
        elif (
            line_amount is None
            and line_category is not None
            and _PAYMENT_SCHEDULE_FREQUENCY_RE.search(line)
        ):
            current_payment_plan = True

        installment_match = re.search(r"\b(?:на|за)\s*(\d+)\s*вноск", lowered)
        if installment_match and int(installment_match.group(1)) != 1:
            continue

        if line_amount is None:
            continue

        previous_line = lines[line_index - 1] if line_index > 0 else ""
        previous_amount, _ = _extract_price_amount_currency(previous_line)
        semantic_line = (
            f"{previous_line} {line}"
            if previous_line and previous_amount is None and len(previous_line) <= 120
            else line
        )
        if _is_penalty_price_line(semantic_line):
            continue
        row_category = _detect_price_category(semantic_line) or current_category
        if row_category is None:
            continue

        row_age_group = _detect_price_age_group(line) or current_age_group
        row_year = academic_year_match.group(1).replace(" ", "") if academic_year_match else current_academic_year
        row_period = _detect_price_period(
            line,
            default_period=(
                current_period
                if row_category == current_category
                and _section_period_applies_to_price_line(
                    line, current_heading, current_payment_plan
                )
                and not (
                    _INSTALLMENT_MULTIPLIER_RE.search(line)
                    and len(_PRICE_LINE_AMOUNT_RE.findall(line)) == 1
                )
                else None
            ),
        )
        if row_period is None:
            row_period = _period_on_following_line(lines, line_index)
        evidence_line = " ".join(part for part in (current_heading, semantic_line) if part)
        signals.append(
            {
                "line": evidence_line,
                "amount": line_amount,
                "currency": line_currency or "BGN",
                "category": row_category,
                "period": row_period,
                "academic_year": row_year,
                "age_group": row_age_group,
                "section_heading": current_heading,
                "source_url": current_source_url,
                "source_is_stale": source_staleness.get(
                    current_source_url, source_staleness.get(None, False)
                ),
            }
        )

    return signals


def _extract_prices_deterministic(text: str) -> PriceExtractionOutput:
    if not text:
        return PriceExtractionOutput()

    lines = [_clean_price_line(raw) for raw in re.split(r"[\n\r]+", text) if _clean_price_line(raw)]
    if not lines:
        return PriceExtractionOutput()

    prices: list[ExtractedPrice] = []
    current_category: str | None = None
    current_age_group: str | None = None
    current_academic_year: str | None = None
    current_period: str | None = None
    current_payment_plan = False
    current_plan_name: str | None = None
    current_heading: str | None = None
    current_includes: list[str] = []
    active_price: ExtractedPrice | None = None
    seen: set[tuple[str, float, str, str | None, str | None]] = set()

    for line_index, line in enumerate(lines):
        lowered = line.lower()
        if lowered.startswith("source:"):
            current_category = None
            current_age_group = None
            current_academic_year = None
            current_period = None
            current_payment_plan = False
            current_plan_name = None
            current_heading = None
            current_includes = []
            active_price = None
            continue
        line_amount, line_currency = _extract_price_amount_currency(
            line, allow_cyrillic_currency_words=False
        )
        academic_year_match = re.search(r"(20\d{2}\s*[-/]\s*20\d{2})", line)
        if academic_year_match:
            current_academic_year = academic_year_match.group(1).replace(" ", "")

        if line_amount is None and any(token in lowered for token in _OPTIONAL_PRICING_SECTION_TOKENS):
            current_category = None
            current_age_group = None
            current_period = None
            current_payment_plan = False
            current_plan_name = None
            current_heading = None
            current_includes = []
            active_price = None
            continue

        if line_amount is None and current_period == "monthly" and lowered.startswith("еднократно плащане"):
            current_period = None

        line_category = _detect_price_category(line, allow_generic_heading=True)
        if line_category is not None and line_amount is None:
            if line_category != current_category or _starts_new_price_section(line):
                current_period = None
                current_payment_plan = False
            current_category = line_category
            current_heading = line
            detected_age_group = _detect_price_age_group(line)
            if detected_age_group is not None:
                current_age_group = detected_age_group
            current_plan_name = "Standard" if "стандарт" in lowered or "standard" in lowered else current_plan_name
            current_includes = []
            active_price = None

        explicit_period = _detect_explicit_price_period(line)
        if explicit_period is not None and line_amount is None:
            previous_amount = (
                _extract_price_amount_currency(lines[line_index - 1])[0]
                if line_index > 0 else None
            )
            if previous_amount is None or line_category is not None:
                current_period = explicit_period
                current_payment_plan = False
        elif (
            line_amount is None
            and line_category is not None
            and _PAYMENT_SCHEDULE_FREQUENCY_RE.search(line)
        ):
            current_payment_plan = True

        if "включва" in lowered and current_category is not None:
            current_includes.append(line)
            if active_price is not None and line not in active_price.includes:
                active_price.includes.append(line)
            continue

        installment_match = re.search(r"\b(?:на|за)\s*(\d+)\s*вноск", lowered)
        if installment_match:
            installment_count = int(installment_match.group(1))
            if installment_count != 1:
                if active_price is not None and line not in active_price.installments:
                    active_price.installments.append(line)
                continue

        if line_amount is None:
            continue

        if _is_penalty_price_line(line):
            continue

        row_category = _detect_price_category(line) or current_category
        if row_category is None:
            continue

        row_age_group = _detect_price_age_group(line) or current_age_group
        row_year = academic_year_match.group(1).replace(" ", "") if academic_year_match else current_academic_year
        row_plan_name = "Standard" if "стандарт" in lowered or "standard" in lowered else current_plan_name
        row_period = _detect_price_period(
            line,
            default_period=(
                current_period
                if row_category == current_category
                and _section_period_applies_to_price_line(
                    line, current_heading, current_payment_plan
                )
                and not (
                    _INSTALLMENT_MULTIPLIER_RE.search(line)
                    and len(_PRICE_LINE_AMOUNT_RE.findall(line)) == 1
                )
                else None
            ),
        )
        if row_period is None:
            row_period = _period_on_following_line(lines, line_index)
        signature = (row_category, line_amount, line_currency or "BGN", row_age_group, row_year)
        if signature in seen:
            continue
        seen.add(signature)

        price = ExtractedPrice(
            category=row_category,
            amount=line_amount,
            currency=line_currency or "BGN",
            period=row_period,
            plan_name=row_plan_name,
            academic_year=row_year,
            age_group=row_age_group,
            includes=list(current_includes),
            confidence=0.7,
        )
        prices.append(price)
        active_price = price

    return PriceExtractionOutput(
        prices=prices,
        has_pricing_info=bool(prices),
        confidence_notes="Deterministic fallback parsed structured pricing lines.",
    )


_BGN_EUR_PEG = 1.95583
_INSTALLMENT_TEXT_AMOUNT_RE = re.compile(r"(\d[\d\s.,]*)")
_INSTALLMENT_PLAN_NAME_RE = re.compile(
    r"(?:(\d+)\s*(?:installment|monthly|вноск)|(?:installment|monthly|вноск)\s*(\d+))",
    flags=re.IGNORECASE,
)


def _amounts_in_installment_text(text: str) -> list[float]:
    amounts: list[float] = []
    for match in _INSTALLMENT_TEXT_AMOUNT_RE.finditer(text or ""):
        value = _parse_price_amount_token(match.group(1))
        if value is not None and value >= 1:
            amounts.append(value)
    return amounts


def _is_installment_plan_name(plan_name: Optional[str]) -> bool:
    # "10 installments", "2 вноски", "monthly installment", etc. signal that a
    # row represents a payment-schedule variant rather than a distinct fee.
    if not plan_name:
        return False
    return bool(_INSTALLMENT_PLAN_NAME_RE.search(plan_name))


def _dedupe_installment_variants(prices: list[ExtractedPrice]) -> list[ExtractedPrice]:
    # Drop rows that represent payment-schedule variants of another row:
    # - amount is listed in another row's `installments` strings, OR
    # - plan_name is labelled as "N installments" / "N вноски" while a sibling
    #   row in the same (category, period, academic_year, age_group) has no
    #   such label.
    if not prices:
        return prices

    def group_key(price: ExtractedPrice) -> tuple:
        return (
            (price.category or "").lower(),
            (price.period or "").lower(),
            price.academic_year,
            price.age_group,
        )

    def is_total_fee_row(price: ExtractedPrice) -> bool:
        context = " ".join(
            str(value or "") for value in (price.plan_name, price.notes)
        ).casefold()
        return "total fee" in context

    grouped: dict[tuple, list[ExtractedPrice]] = {}
    for price in prices:
        row_context = " ".join(
            str(value or "") for value in (price.plan_name, price.notes)
        ).casefold()
        if any(
            token in row_context
            for token in ("eal", "learning support", "additional language support")
        ):
            continue
        grouped.setdefault(group_key(price), []).append(price)

    kept: list[ExtractedPrice] = []
    for group in grouped.values():
        if len(group) == 1:
            kept.extend(group)
            continue
        installment_amounts: set[float] = set()
        for row in group:
            for line in row.installments or []:
                installment_amounts.update(_amounts_in_installment_text(line))
        has_non_installment_sibling = any(
            not _is_installment_plan_name(row.plan_name) for row in group
        )
        has_component_sibling = any(not is_total_fee_row(row) for row in group)
        for row in group:
            if has_component_sibling and is_total_fee_row(row):
                continue
            row_amount = _to_optional_float(row.amount)
            if has_non_installment_sibling and _is_installment_plan_name(row.plan_name):
                continue
            if row_amount is None:
                kept.append(row)
                continue
            if row.installments:
                kept.append(row)
                continue
            if installment_amounts and any(
                abs(row_amount - inst) < 0.5 for inst in installment_amounts
            ):
                continue
            kept.append(row)
    return kept


def _dedupe_currency_variants(prices: list[ExtractedPrice]) -> list[ExtractedPrice]:
    # Drop BGN rows that are the currency-converted duplicate of an EUR row
    # in the same logical group. Prefer EUR since Bulgaria adopted euro in 2026.
    if not prices:
        return prices

    def group_key(price: ExtractedPrice) -> tuple:
        return (
            (price.category or "").lower(),
            (price.period or "").lower(),
            price.academic_year,
            price.age_group,
            price.plan_name,
        )

    grouped: dict[tuple, list[ExtractedPrice]] = {}
    for price in prices:
        grouped.setdefault(group_key(price), []).append(price)

    kept: list[ExtractedPrice] = []
    for group in grouped.values():
        if len(group) == 1:
            kept.extend(group)
            continue
        eur_rows = [p for p in group if (p.currency or "").upper() == "EUR"]
        bgn_rows = [p for p in group if (p.currency or "").upper() == "BGN"]
        other_rows = [p for p in group if (p.currency or "").upper() not in {"EUR", "BGN"}]
        kept.extend(other_rows)

        if not eur_rows or not bgn_rows:
            kept.extend(eur_rows)
            kept.extend(bgn_rows)
            continue

        eur_amounts = [_to_optional_float(p.amount) for p in eur_rows]
        bgn_remaining = []
        for bgn in bgn_rows:
            bgn_amount = _to_optional_float(bgn.amount)
            if bgn_amount is None:
                bgn_remaining.append(bgn)
                continue
            converted = bgn_amount / _BGN_EUR_PEG
            matched = any(
                eur_amount is not None and abs(eur_amount - converted) / converted < 0.01
                for eur_amount in eur_amounts
            )
            if matched:
                continue
            bgn_remaining.append(bgn)
        kept.extend(eur_rows)
        kept.extend(bgn_remaining)
    return kept


def _dedupe_price_rows(prices: list[ExtractedPrice]) -> list[ExtractedPrice]:
    rows = _dedupe_currency_variants(_dedupe_installment_variants(prices))

    distinct_rows: list[ExtractedPrice] = []
    seen_rows: set[str] = set()
    for row in rows:
        identity = row.model_dump_json(exclude_none=False)
        if identity in seen_rows:
            continue
        seen_rows.add(identity)
        distinct_rows.append(row)
    rows = distinct_rows

    def comparable_group_key(row: ExtractedPrice) -> tuple[str, str, str]:
        return (
            (row.category or "").casefold(),
            (row.plan_name or "").strip().casefold(),
            (row.age_group or "").strip().casefold(),
        )

    def exact_fee_identity(
        row: ExtractedPrice,
    ) -> tuple[str, str, str, str, float | None, str]:
        return (
            (row.category or "").casefold(),
            (row.period or "").casefold(),
            (row.plan_name or "").strip().casefold(),
            (row.age_group or "").strip().casefold(),
            _to_optional_float(row.amount),
            (row.currency or "").upper(),
        )

    grouped: dict[tuple[str, str, str], list[ExtractedPrice]] = {}
    for row in rows:
        grouped.setdefault(comparable_group_key(row), []).append(row)

    latest_year_by_group: dict[tuple[str, str, str], str] = {}
    for key, group in grouped.items():
        explicit_years = {
            normalized
            for row in group
            if (normalized := _normalize_academic_year(row.academic_year)) is not None
        }
        if explicit_years:
            latest_year_by_group[key] = max(
                explicit_years,
                key=lambda value: tuple(int(part) for part in value.split("/")),
            )

    explicit_fee_identities = {
        exact_fee_identity(row)
        for row in rows
        if _normalize_academic_year(row.academic_year) is not None
    }
    explicit_years = {
        normalized
        for row in rows
        if (normalized := _normalize_academic_year(row.academic_year)) is not None
    }
    latest_explicit_year = (
        max(
            explicit_years,
            key=lambda value: tuple(int(part) for part in value.split("/")),
        )
        if explicit_years
        else None
    )
    latest_start_year = (
        int(latest_explicit_year.split("/", 1)[0]) if latest_explicit_year else None
    )
    return [
        row
        for row in rows
        if (
            (year := _normalize_academic_year(row.academic_year)) is None
            or latest_start_year is None
            # Keep the immediately preceding/current academic year when a
            # page legitimately mixes adjacent schedules. Older fee tables
            # are superseded even when the new table has different categories.
            or int(year.split("/", 1)[0]) >= latest_start_year - 1
        )
        and (
            (key := comparable_group_key(row)) not in latest_year_by_group
            or year == latest_year_by_group[key]
            or (year is None and exact_fee_identity(row) not in explicit_fee_identities)
        )
    ]


def _normalize_academic_year(value: str | None) -> str | None:
    """Delegate to the shared canonical normalizer (single implementation)."""
    return normalize_academic_year(value)


_PRICE_MATCH_STOPWORDS = {
    "annual",
    "fee",
    "fees",
    "full",
    "plan",
    "service",
    "the",
    "for",
    "school",
    "такса",
    "такси",
    "годишна",
    "за",
    "на",
}


def _price_signal_match_score(price: ExtractedPrice, signal: dict[str, Any]) -> int:
    context = " ".join(
        str(value or "")
        for value in (price.plan_name, price.notes, price.category)
    ).casefold()
    line = str(signal.get("line") or "").casefold()
    context_tokens = {
        token for token in re.findall(r"[^\W\d_]{3,}", context) if token not in _PRICE_MATCH_STOPWORDS
    }
    line_tokens = set(re.findall(r"[^\W\d_]{3,}", line))
    score = 3 * len(context_tokens & line_tokens)
    if price.category == signal.get("category"):
        score += 2
    return score


def _yearless_pricing_text_is_stale(text: str, *, current_year: int | None = None) -> bool:
    """Detect clearly dated legacy fee tables without treating general history as pricing dates."""
    current_year = current_year or datetime.datetime.now(datetime.timezone.utc).year
    raw_lines = re.split(r"[\n\r]+", text or "")
    academic_year_ends: list[int] = []
    pricing_tokens = (
        "tuition",
        "fee",
        "fees",
        "price",
        "pricing",
        "такс",
        "цена",
        "цени",
        "плащ",
    )
    for index, raw_line in enumerate(raw_lines):
        year_matches = list(
            re.finditer(r"(20\d{2})\s*[-/]\s*(20\d{2})", raw_line)
        )
        if not year_matches:
            continue
        lowered = raw_line.casefold()
        directly_pricing_related = any(token in lowered for token in pricing_tokens)
        remaining = re.sub(
            r"(20\d{2})\s*[-/]\s*(20\d{2})", "", lowered
        )
        remaining = re.sub(r"[^\w\s]+", " ", remaining).strip()
        is_academic_year_heading = remaining in {
            "",
            "academic year",
            "school year",
            "учебна година",
            "учебната година",
        }
        nearby_lines = (
            raw_lines[max(0, index - 1) : index]
            + raw_lines[index + 1 : index + 2]
        )
        nearby_pricing_related = any(
            any(token in nearby.casefold() for token in pricing_tokens)
            for nearby in nearby_lines
        )
        if directly_pricing_related or (is_academic_year_heading and nearby_pricing_related):
            academic_year_ends.extend(int(match.group(2)) for match in year_matches)
    if academic_year_ends:
        return max(academic_year_ends) < current_year

    deadline_years: list[int] = []
    for raw_line in raw_lines:
        lowered = raw_line.casefold()
        if not any(token in lowered for token in ("deadline", "payment", "плащ", "краен срок")):
            continue
        deadline_years.extend(int(year) for year in re.findall(r"\b(20\d{2})\b", raw_line))
    return bool(deadline_years) and max(deadline_years) <= current_year - 2


def _pricing_source_staleness(text: str) -> dict[str | None, bool]:
    """Return staleness per source block, falling back to the whole text for one page."""
    source_headers = list(
        re.finditer(
            r"^\s*---\s*SOURCE:\s*(.*?)\s*---\s*$",
            text or "",
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )
    if not source_headers:
        return {None: _yearless_pricing_text_is_stale(text)}

    staleness: dict[str | None, bool] = {}
    for index, header in enumerate(source_headers):
        block_end = (
            source_headers[index + 1].start()
            if index + 1 < len(source_headers)
            else len(text)
        )
        source_url = header.group(1).strip() or None
        staleness[source_url] = _yearless_pricing_text_is_stale(
            text[header.end() : block_end]
        )
    return staleness


def _filter_supported_prices(prices: list[ExtractedPrice], text: str) -> list[ExtractedPrice]:
    if not prices or not text:
        return []

    signals = _iter_price_line_signals(text)
    if not signals:
        return []

    refined: list[ExtractedPrice] = []
    for price in prices:
        row_context = " ".join(
            str(value or "") for value in (price.plan_name, price.notes)
        ).strip().casefold()
        amount = _to_optional_float(price.amount)
        if amount is None:
            continue

        currency = (price.currency or "BGN")[:3].upper()
        candidates = [
            signal
            for signal in signals
            if signal["currency"] == currency and abs(float(signal["amount"]) - amount) < 0.01
        ]
        if not candidates:
            continue
        academic_year = _normalize_academic_year(price.academic_year)
        if academic_year:
            candidates = [
                signal
                for signal in candidates
                if _normalize_academic_year(signal.get("academic_year")) == academic_year
            ]
        else:
            candidates = [
                signal for signal in candidates if not signal.get("source_is_stale", False)
            ]
        if not candidates:
            continue
        candidate_semantics = {
            (signal.get("category"), signal.get("period"), signal.get("academic_year"))
            for signal in candidates
        }
        if not row_context and len(candidate_semantics) > 1:
            # Deterministic/low-confidence rows do not carry enough context to
            # safely disambiguate repeated amounts from composite source lines.
            if float(price.confidence or 0.0) < 0.8:
                continue
            semantic_matches = [
                signal
                for signal in candidates
                if signal.get("category") == price.category
            ]
            if len({(signal.get("category"), signal.get("period"), signal.get("academic_year")) for signal in semantic_matches}) > 1:
                semantic_matches = [
                    signal for signal in semantic_matches if signal.get("period") == price.period
                ]
            matched_semantics = {
                (signal.get("category"), signal.get("period"), signal.get("academic_year"))
                for signal in semantic_matches
            }
            if len(matched_semantics) != 1:
                continue
            candidates = semantic_matches
        scored = [(_price_signal_match_score(price, signal), signal) for signal in candidates]
        best_score = max(score for score, _signal in scored)
        best_signals = [signal for score, signal in scored if score == best_score]
        best_semantics = {
            (signal.get("category"), signal.get("period"), signal.get("academic_year"))
            for signal in best_signals
        }
        if len(best_semantics) != 1:
            continue
        supporting_signal = best_signals[0]
        if "total fee" in str(supporting_signal.get("section_heading") or "").casefold():
            continue

        normalized = price.model_copy(deep=True)
        if supporting_signal.get("category"):
            normalized.category = supporting_signal["category"]
        # Evidence is authoritative in both directions: an explicit supported
        # period corrects the model, and absent period evidence clears a guess.
        normalized.period = supporting_signal.get("period")
        if supporting_signal.get("academic_year") and not normalized.academic_year:
            normalized.academic_year = supporting_signal["academic_year"]
        if supporting_signal.get("age_group") and not normalized.age_group:
            normalized.age_group = supporting_signal["age_group"]
        refined.append(normalized)

    return refined


def _source_blocks(text: str) -> list[tuple[str | None, str]]:
    """(source URL, text) per ``--- SOURCE ---`` block; one block for unmarked text."""
    headers = list(
        re.finditer(r"^\s*---\s*SOURCE:\s*(.*?)\s*---\s*$", text or "", flags=re.IGNORECASE | re.MULTILINE)
    )
    if not headers:
        return [(None, text or "")]
    blocks = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        blocks.append((header.group(1).strip() or None, text[header.end() : end]))
    return blocks


_PAGE_ACADEMIC_YEAR_RE = re.compile(r"20\d{2}\s*[-/–]\s*(?:20)?\d{2}")
_FEE_WORDS = r"такс|\bцен[аи]\b|\bfees?\b|tuition|\bprices?\b"
# An academic year on a line that is about fees ("Такси за учебната 2026/2027 г.").
_FEE_YEAR_LINE_RE = re.compile(
    rf"^.*(?:{_FEE_WORDS}).*{_PAGE_ACADEMIC_YEAR_RE.pattern}"
    rf"|^.*{_PAGE_ACADEMIC_YEAR_RE.pattern}.*(?:{_FEE_WORDS})",
    re.MULTILINE,
)
_CURRENCY_OR_FEE_RE = re.compile(rf"€|\beur|евро|лв|\bbgn|лева|{_FEE_WORDS}")


def _filter_model_prices(prices: list[ExtractedPrice], text: str) -> list[ExtractedPrice]:
    """Keep a model's price rows that the page text bears out, corrected by that text.

    The model read the whole page, so its category, plan and age group stand. The text
    decides the rest, by the same reading Stage 6 uses to gate publication
    (``price_evidence``): the amount must be on a page in the row's currency; the period
    is whatever is written next to the amount (or in its table's header row), or none;
    the academic year is kept only when that page names it, and taken from the page when
    the model gave none and the page names exactly one, on a line about fees. A yearless
    row needs a page that is not a dated old fee list. Rows for late-payment penalties, a "total fee" sum and per-day/week/hour
    amounts are dropped.

    The keyword extractor's rows go through :func:`_filter_supported_prices` instead:
    they have no reading of their own, so there the line and heading keywords decide.
    """
    if not prices or not text:
        return []
    staleness = _pricing_source_staleness(text)
    blocks = [
        (normalize_text(block), staleness.get(url, staleness.get(None, False)))
        for url, block in _source_blocks(text)
    ]

    refined: list[ExtractedPrice] = []
    for price in prices:
        amount = _to_optional_float(price.amount)
        if amount is None:
            continue
        currency = (price.currency or "BGN")[:3].upper()
        year = _normalize_academic_year(price.academic_year)
        label = price.plan_name or price.age_group or price.notes
        supported: list[tuple[bool, str | None]] = []  # (page names the year, period)
        for block, stale in blocks:
            spans = [
                span
                for span in amount_spans(block, amount)
                if _amount_is_a_fee_in(block, span, currency)
            ]
            if not spans:
                continue
            labels = label_spans(block, label)
            if labels:
                spans = [min(spans, key=lambda span: min(_gap(span, found) for found in labels))]
            stated = [_period_written_for(block, span) for span in spans]
            if all(unrepresentable for _, unrepresentable in stated):
                continue
            families = [found for found, _ in stated]
            agreed = families[0] if all(found == families[0] for found in families) else set()
            period = next(iter(agreed)).lower() if len(agreed) == 1 else None
            page_years = {
                _normalize_academic_year(found) for found in _PAGE_ACADEMIC_YEAR_RE.findall(block)
            } - {None}
            if year in page_years:
                page_year = price.academic_year
            elif not year and len(page_years) == 1 and _FEE_YEAR_LINE_RE.search(block):
                # The model left the year out; the page names exactly one, with its fees.
                page_year = next(iter(page_years))
            else:
                page_year = None
            if page_year is None and stale:
                continue
            supported.append((page_year, period))
        if not supported:
            continue
        # A page that names the year is the better witness.
        page_year, period = max(supported, key=lambda item: item[0] is not None)
        normalized = price.model_copy(deep=True)
        normalized.period = _period_fitting(str(price.category or "").lower(), period)
        normalized.academic_year = page_year
        refined.append(normalized)
    return refined


def _period_fitting(category: str, period: str | None) -> str | None:
    """Drop a period the category cannot have; the wording meant something else.

    "Еднократно плащане" beside a tuition fee is the fee paid in one instalment (517's
    "ГОДИШНА ТАКСА" came out one_time), and a recurring word beside a registration fee
    belongs to a neighbouring fee on the line (151's came out yearly).
    """
    if category == "tuition" and period == "one_time":
        return None
    if category == "registration" and period not in (None, "one_time"):
        return None
    return period


def _table_header(block: str, span: tuple[int, int]) -> str | None:
    """The first row of the pipe table the amount sits in, when that row holds no price."""
    header = None
    for line in reversed(block[: span[0]].splitlines()[:-1]):
        if "|" not in line:
            break
        header = line
    return None if header is None or currency_price_starts(header) else header


def _period_written_for(block: str, span: tuple[int, int]) -> tuple[set[str], bool]:
    """Period wording for one amount; for a table cell with none, its column header's."""
    families, unrepresentable = stated_period(block, span)
    line = _line_of(block, span)
    if families or unrepresentable or "|" not in line:
        return families, unrepresentable
    header = _table_header(block, span)
    if header is None:
        return families, unrepresentable
    cell = _header_cell(block, span, header)
    return period_families(cell) if cell is not None else (families, unrepresentable)


def _header_cell(block: str, span: tuple[int, int], header: str) -> str | None:
    """The header cell above the amount's own column, when the two rows line up."""
    line = _line_of(block, span)
    line_start = block.rfind("\n", 0, span[0]) + 1
    cells = header.split("|")
    if len(cells) != line.count("|") + 1:
        return None
    return cells[line[: span[0] - line_start].count("|")]


def _currencies_named(text: str) -> set[str]:
    return {
        code
        for code, pattern in (("EUR", r"€|\beur|евро"), ("BGN", r"лв|\bbgn|лева"))
        if re.search(pattern, text)
    }


def _amount_is_a_fee_in(block: str, span: tuple[int, int], currency: str) -> bool:
    """The occurrence is a price in ``currency`` and not a penalty or a sum of fees."""
    line = _line_of(block, span)
    written = occurrence_currency(block, span)
    bare = written is None and (
        _NUMBER_ONLY_LINE_RE.match(line) is not None
        or ("|" in line and _bare_cell_is_a_price_in(block, span, currency))
    )
    if written != currency and not bare:
        return False
    return not _is_penalty_price_line(line) and "total fee" not in line


def _bare_cell_is_a_price_in(block: str, span: tuple[int, int], currency: str) -> bool:
    """A table cell holding a number with no currency of its own is a price in ``currency``.

    Its column header decides when it names a currency ("Plan | EUR | BGN"). Otherwise
    the table must be about fees and name no currency but the row's: a class-size table
    is not a price list, and a two-currency table with unnamed columns is not evidence
    for either.
    """
    line = _line_of(block, span)
    header = _table_header(block, span) or ""
    cell = _header_cell(block, span, header) if header else None
    in_column = _currencies_named(cell or "")
    if in_column:
        return currency in in_column
    table = f"{line}\n{header}"
    return _currencies_named(table) <= {currency} and _CURRENCY_OR_FEE_RE.search(table) is not None


def _find_supporting_price_source_url(
    school: School,
    pages: list[SourcePage],
    price: ExtractedPrice,
    *,
    model_rows: bool = False,
) -> str | None:
    amount = _to_optional_float(price.amount)
    if amount is None:
        return None

    school_host = _canonical_host(school.website_url)
    candidate_pages = [
        page
        for page in pages
        if page.raw_markdown and _host_matches(_canonical_host(page.source_url), school_host)
    ] or [page for page in pages if page.raw_markdown]
    category_priority = {"pricing": 0, "admission": 1, "contact": 2}
    candidate_pages.sort(
        key=lambda page: (
            category_priority.get((page.page_category or "").lower(), 9),
            len(page.source_url or ""),
        )
    )

    for page in candidate_pages:
        page_text = page.raw_markdown or ""
        page_context = f"--- SOURCE: {page.source_url} ---\n{page_text}"
        supported = (
            _filter_model_prices([price], page_context)
            if model_rows
            else _filter_supported_prices([price], page_context)
        )
        if any(
            row.category == price.category
            and row.period == price.period
            and (
                not price.academic_year
                or _normalize_academic_year(row.academic_year)
                == _normalize_academic_year(price.academic_year)
            )
            for row in supported
        ):
            return page.source_url
    return None

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
        if _line_matches_any_pattern(value.lower(), _rules()._PRICING_NEGATED_INCLUDE_PATTERNS):
            moved_to_excluded.append(value)
        else:
            cleaned_includes.append(value)

    merged_excluded = _merge_text_values(exclude_values, moved_to_excluded)
    return cleaned_includes, merged_excluded

def _is_probable_section_noise_line(text: str) -> bool:
    if not text:
        return True

    lowered = text.lower()
    if _line_matches_any_pattern(lowered, _rules()._SECTION_REGULATORY_NOISE_PATTERNS):
        return True
    if _line_matches_any_pattern(lowered, _rules()._MENU_BREADCRUMB_NOISE_PATTERNS):
        return True

    nav_hits = sum(
        1
        for pattern in _rules()._SECTION_NAV_NOISE_PATTERNS
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
        _rules()._OPERATIONS_WORKING_HOURS_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        max_items=1,
    )
    if snippets:
        return snippets[0]
    time_match = re.search(r"\b([01]?\d|2[0-3])[:.][0-5]\d\b\s*[-–]\s*\b([01]?\d|2[0-3])[:.][0-5]\d\b", text)
    if not time_match:
        return None
    return _sanitize_label(time_match.group(0), max_len=_rules()._GENERAL_INFO_ITEM_MAX_LEN)

def _extract_contact_info_deterministic(text: str) -> dict[str, Any] | None:
    """Extract phone numbers, email addresses, and postal addresses from page text.

    This is deterministic (no LLM) and processes all page content. Returns None
    when nothing useful is found.
    """
    if not text:
        return None

    contact: dict[str, Any] = {}

    # Extract phone numbers
    phones: list[str] = []
    seen_phones: set[str] = set()
    for match in _rules()._PHONE_PATTERN.finditer(text):
        raw = match.group(0).strip()
        # Normalize: collapse internal whitespace and separators
        normalized = re.sub(r"[\s./\-]+", "", raw)
        if normalized in seen_phones:
            continue
        if len(normalized) < 9:  # Too short to be a real phone
            continue
        seen_phones.add(normalized)
        phones.append(raw.strip())
        if len(phones) >= 3:  # Max 3 phone numbers per school
            break

    if phones:
        contact["phones"] = phones

    # Extract email addresses
    emails: list[str] = []
    seen_emails: set[str] = set()
    for match in _rules()._EMAIL_PATTERN.finditer(text):
        raw = match.group(0).strip()
        # Normalize [at] / [ a t ] → @, collapse spaces around @
        normalized = re.sub(r"\s*\[\s*a\s*t\s*\]\s*", "@", raw, flags=re.IGNORECASE)
        normalized = re.sub(r"\s+", "", normalized).lower()
        # Filter obvious placeholder/noise emails
        if any(noise in normalized for noise in _rules()._CONTACT_NOISE_TOKENS):
            continue
        if normalized in seen_emails:
            continue
        seen_emails.add(normalized)
        emails.append(normalized)
        if len(emails) >= 3:  # Max 3 email addresses
            break

    if emails:
        contact["emails"] = emails

    addresses = _extract_contact_address_candidates(text)
    if addresses:
        contact["address"] = addresses[0]
        if len(addresses) > 1:
            contact["addresses"] = addresses

    coordinates = _extract_contact_coordinates(text)
    if coordinates:
        contact["coordinates"] = coordinates

    return contact if contact else None


_CONTACT_ADDRESS_LABEL_RE = re.compile(r"(?:^|[\s>*_-])(?:address|адрес)\b[:\s-]*", flags=re.IGNORECASE)
_CONTACT_ADDRESS_STREET_MARKERS = (
    "ул.",
    "бул.",
    "ж.к.",
    "жк.",
    "кв.",
    "пл.",
    "гр.",
    "street",
    "st.",
    "boulevard",
    "blvd",
    "road",
    "rd.",
    "avenue",
    "ave.",
)
_CONTACT_ADDRESS_NOISE_MARKERS = (
    "all rights reserved",
    "cookie",
    "бисквит",
    "privacy",
    "политика",
    "общи условия",
    "designed by",
    "телефон",
    "email",
    "e-mail",
    "турнир",
    "шампион",
    "училища",
)


def _extract_contact_address_candidates(text: str) -> list[str]:
    if not text:
        return []

    lines = [line.strip() for line in text.splitlines()]
    candidates: list[tuple[int, str]] = []

    def add_candidate(raw_value: str, score_bonus: int = 0) -> None:
        normalized = _normalize_contact_address_candidate(raw_value)
        if not _looks_like_contact_address(normalized):
            return
        score = _contact_address_score(normalized) + score_bonus
        candidates.append((score, normalized))

    for idx, line in enumerate(lines):
        if not line:
            continue
        normalized_line = _normalize_contact_address_candidate(line)
        if _looks_like_contact_address(normalized_line):
            add_candidate(normalized_line)

        if not _CONTACT_ADDRESS_LABEL_RE.search(line):
            continue

        inline = _CONTACT_ADDRESS_LABEL_RE.sub("", line).strip(" -:|")
        if inline:
            add_candidate(inline, score_bonus=3)

        trailing_lines: list[str] = []
        for offset in range(1, 4):
            next_idx = idx + offset
            if next_idx >= len(lines):
                break
            next_line = lines[next_idx].strip()
            if not next_line or _is_probable_section_noise_line(next_line):
                continue
            if re.match(r"^#{1,6}\s", next_line):
                break
            if _CONTACT_ADDRESS_LABEL_RE.search(next_line):
                break
            trailing_lines.append(next_line)
            if _looks_like_contact_address(next_line):
                break
        if trailing_lines:
            add_candidate(", ".join(trailing_lines), score_bonus=4)

    unique: list[str] = []
    seen: set[str] = set()
    for _score, candidate in sorted(candidates, key=lambda item: (-item[0], len(item[1]))):
        key = re.sub(r"\s+", " ", candidate.casefold()).strip(" ,")
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
        if len(unique) >= 3:
            break
    return unique


def _normalize_contact_address_candidate(raw_value: str) -> str:
    candidate = _sanitize_label(str(raw_value or "").strip(), max_len=240)
    if not candidate:
        return ""
    candidate = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", candidate)
    candidate = candidate.replace("location_on", " ")
    candidate = re.sub(r"[*_#`]+", " ", candidate)
    candidate = _CONTACT_ADDRESS_LABEL_RE.sub("", candidate).strip(" -:|")
    lowered = candidate.casefold()
    marker_positions = [lowered.find(marker) for marker in _CONTACT_ADDRESS_STREET_MARKERS if marker in lowered]
    if marker_positions:
        first_marker = min(pos for pos in marker_positions if pos >= 0)
        if first_marker > 0:
            candidate = candidate[first_marker:].strip(" ,")
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,")
    return candidate


def _contact_address_score(value: str) -> int:
    lowered = (value or "").casefold()
    if not lowered:
        return 0

    score = 0
    if any(marker in lowered for marker in _CONTACT_ADDRESS_STREET_MARKERS):
        score += 4
    if re.search(r"[№#]\s*\d+", value) or re.search(r"\b\d+[A-Za-zА-Яа-я]?\b", value):
        score += 2
    if any(marker in lowered for marker in ("ет.", "ап.", "офис", "floor", "suite")):
        score += 1
    if _CONTACT_ADDRESS_LABEL_RE.search(value):
        score += 2
    return score


def _looks_like_contact_address(value: str | None) -> bool:
    candidate = (value or "").strip()
    lowered = candidate.casefold()
    if not candidate or len(candidate) < 8:
        return False
    if any(marker in lowered for marker in _CONTACT_ADDRESS_NOISE_MARKERS):
        return False
    if _is_probable_section_noise_line(candidate):
        return False
    if len(candidate.split()) < 2:
        return False
    if len(candidate.split()) > 16:
        return False
    if not any(marker in lowered for marker in _CONTACT_ADDRESS_STREET_MARKERS) and not re.search(r"[№#]\s*\d+", candidate):
        return False
    return _contact_address_score(candidate) >= 4


def _extract_contact_coordinates(text: str) -> dict[str, float] | None:
    if not text:
        return None
    match = re.search(
        r"(?:coordinates|координати)\s*:\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    lat = float(match.group(1))
    lng = float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    return {"lat": lat, "lng": lng}

def _merge_language_candidates(
    llm_languages: list[ExtractedLanguageFocus],
    deterministic_languages: list[ExtractedLanguageFocus],
) -> list[ExtractedLanguageFocus]:
    merged: list[ExtractedLanguageFocus] = []
    seen: set[tuple[str, str]] = set()
    for candidate in list(llm_languages) + list(deterministic_languages):
        key = (
            (candidate.language or "").strip().lower(),
            (candidate.level or "").strip().lower(),
        )
        if not key[0] or key in seen:
            continue
        seen.add(key)
        merged.append(candidate)
    return merged

def _merge_founded_year(llm_year: str | None, deterministic_year: str | None) -> str | None:
    if deterministic_year is None:
        return llm_year
    if llm_year is None:
        return deterministic_year
    llm_match = re.search(r"(?:19|20)\d{2}", llm_year)
    if llm_match:
        return llm_match.group(0)
    return deterministic_year

def _merge_class_size(llm_class_size: str | None, deterministic_class_size: str | None) -> str | None:
    if deterministic_class_size is None:
        return llm_class_size
    if llm_class_size is None:
        return deterministic_class_size
    llm_num = _extract_class_size_number(llm_class_size)
    if llm_num is not None and 5 <= llm_num <= 40:
        return f"{llm_num} students"
    return deterministic_class_size

def _extract_class_size_number(value: str) -> int | None:
    match = re.search(r"\b(\d{1,2})\b", value or "")
    if not match:
        return None
    return int(match.group(1))

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


def _classify_summary_source_page(page: SourcePage) -> str:
    category = (page.page_category or "").lower()
    page_url = (page.source_url or "").lower()
    page_text = (page.raw_markdown or "").lower()[:4000]

    narrative_hits = 0
    operational_hits = 0
    noise_hits = 0

    if category in {"about", "programs", "facilities"}:
        narrative_hits += 3
    if category in {"news", "gallery"}:
        noise_hits += 3
    if category in {"admission", "pricing", "contact"}:
        operational_hits += 3

    narrative_hits += sum(1 for token in _SUMMARY_SOURCE_PAGE_GOOD_URL_TOKENS if token in page_url)
    noise_hits += sum(1 for token in _SUMMARY_SOURCE_PAGE_BAD_URL_TOKENS if token in page_url)
    operational_hits += sum(1 for token in _SUMMARY_SOURCE_PAGE_OPERATIONAL_URL_TOKENS if token in page_url)

    narrative_hits += sum(1 for token in _SUMMARY_SOURCE_PAGE_NARRATIVE_TEXT_TOKENS if token in page_text)
    noise_hits += sum(1 for token in _SUMMARY_SOURCE_PAGE_BAD_TEXT_TOKENS if token in page_text)
    operational_hits += sum(1 for token in _SUMMARY_SOURCE_PAGE_OPERATIONAL_TEXT_TOKENS if token in page_text)

    if any(re.search(pattern, page_text, flags=re.IGNORECASE) for pattern in _SUMMARY_SOURCE_PAGE_NEWS_PATTERNS):
        noise_hits += 2

    if narrative_hits >= max(2, operational_hits + 1) and narrative_hits >= noise_hits + 1:
        return "narrative"
    if noise_hits >= max(2, narrative_hits + 1):
        return "noise"
    if operational_hits >= max(2, narrative_hits):
        return "operational"
    if category in {"about", "programs", "facilities"} and narrative_hits > 0:
        return "narrative"
    if category in {"news", "gallery"}:
        return "noise"
    if category in {"admission", "pricing", "contact"}:
        return "operational"
    return "neutral"


def _prepare_summary_source_page_text(text: str) -> str:
    if not text:
        return ""

    prepared_lines: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith(("--- SOURCE:", "* [", "- [")):
            continue
        if stripped.startswith("[") and "](" in stripped:
            continue

        cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", stripped).strip()
        cleaned = re.sub(r"^[#>\-\*\s]+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned or len(cleaned) < 8:
            continue

        lowered = cleaned.casefold()
        if lowered in _SUMMARY_SOURCE_NAV_EXACT:
            continue
        if any(token in lowered for token in _SUMMARY_SOURCE_PAGE_BAD_TEXT_TOKENS):
            continue
        if "http://" in lowered or "https://" in lowered:
            continue

        narrative_hits = sum(1 for token in _SUMMARY_SOURCE_PAGE_NARRATIVE_TEXT_TOKENS if token in lowered)
        operational_hits = sum(1 for token in _SUMMARY_SOURCE_PAGE_OPERATIONAL_TEXT_TOKENS if token in lowered)
        noise_hits = sum(1 for token in _SUMMARY_SOURCE_PAGE_BAD_TEXT_TOKENS if token in lowered)

        if noise_hits > 0:
            continue
        if narrative_hits == 0 and operational_hits > 0:
            continue
        if narrative_hits == 0 and len(cleaned) < 60:
            continue

        prepared_lines.append(cleaned)
        if len(prepared_lines) >= 20:
            break

    if prepared_lines:
        return "\n".join(prepared_lines)
    return text

# A queued page at or under this size has room held back for it in full while packing,
# because a small page is the one an oversized page can starve completely. Larger queued
# pages get no reserve: they would be truncated either way, so holding room back for them
# only costs the higher-ranked page content it would have used.
_SMALL_QUEUED_PAGE_CHARS = 2000
# Never hold back more than this share of the budget, so a school whose fees genuinely are
# one long page keeps the bulk of it even behind several small pages.
_MAX_RESERVE_FRACTION = 3
# A *truncated* fragment below this size cannot carry a usable fee, so it is skipped.
# A complete page is never measured against this: a short page is whole evidence, not
# a fragment, and dropping it recreates the starvation this packing rule prevents.
_MIN_USABLE_PAGE_CHARS = 500


# URL words (matched against the decoded URL) that mark a fee page.
_PRICING_URL_TOKENS = (
    "pricing", "price", "fee", "tuition", "taksi", "ceni", "tseni", "цени", "такс",
    "tarif", "frais", "schulgeld", "gebuehr", "gebühr",
)  # fmt: skip
# Pricing selection: score per currency-attached price in the page text, up to a cap, so
# four prices outweigh the preferred-category and URL bonuses together.
_PRICE_EVIDENCE_SCORE = 45
_PRICE_EVIDENCE_MAX_PRICES = 4
# Text kept in front of the first price when a long page is cut to its fee section.
_PRICE_WINDOW_LEAD_CHARS = 1200


def _drop_url_variant_pages(pages: list[SourcePage]) -> list[SourcePage]:
    """Keep one crawl of each page, so two spellings do not take two prompt slots.

    The spellings differ in scheme, ``www.``, trailing slash or escaping. The newest
    crawl wins; of two crawls from the same run, the one stating more prices.
    """
    best: dict[tuple[str, str, str], SourcePage] = {}
    for page in pages:
        try:
            key = page_key(page.source_url or "")
        except ValueError:
            key = ("", page.source_url or "", "")
        kept = best.get(key)
        if kept is None or _variant_rank(page) > _variant_rank(kept):
            best[key] = page
    return list(best.values())


def _variant_rank(page: SourcePage) -> tuple[float, int, int]:
    scraped_at = getattr(page, "last_scraped_at", None)
    text = page.raw_markdown or ""
    return (
        scraped_at.timestamp() if scraped_at is not None else 0.0,
        len(currency_price_starts(text)),
        len(text),
    )


def _price_dense_window(candidate: str, allowance: int) -> str:
    """Cut a ``--- SOURCE ---`` page to ``allowance`` chars around its densest run of prices.

    Cutting at the head keeps a long page's navigation and drops a fee table further
    down. With no price beyond the head, the head is kept.
    """
    header, separator, body = candidate.partition("\n")
    room = allowance - len(header) - len(separator)
    starts = currency_price_starts(body)
    if room <= 0 or not starts or starts[-1] < room:
        return candidate[:allowance]
    best_start, best_count = 0, 0
    for first in starts:
        start = max(0, first - _PRICE_WINDOW_LEAD_CHARS)
        count = sum(1 for position in starts if start <= position < start + room)
        if count > best_count:
            best_start, best_count = start, count
    return header + separator + body[best_start : best_start + room]


def _select_pages(
    school: School,
    pages: list[SourcePage],
    preferred_categories: list[str],
    use_case: str = "general_info",
    include_tokens: tuple[str, ...] | None = None,
) -> tuple[str, list[str]]:
    """Build bounded prompt content with simple relevance ranking."""
    settings = get_settings()
    max_chars = max(2000, int(settings.extraction_max_content_chars))
    if use_case == "general_summary_source":
        max_chars = min(max_chars, 6000)
    elif use_case == "general_info" or use_case.startswith("general_"):
        # Field-focused extraction runs multiple passes; keep each prompt compact.
        max_chars = min(max_chars, 8000)

    preferred = {category.lower() for category in preferred_categories}
    school_host = _canonical_host(school.website_url)

    # Keep pages on the same domain when available to avoid polluting extraction with
    # third-party directories/news portals that may have been navigated.
    same_host_pages = [
        page
        for page in pages
        if _host_matches(_canonical_host(page.source_url), school_host)
    ]
    candidate_pages = same_host_pages or pages
    summary_page_classifications = (
        {id(page): _classify_summary_source_page(page) for page in candidate_pages}
        if use_case == "general_summary_source"
        else {}
    )
    if use_case == "general_summary_source":
        narrative_pages = [
            page for page in candidate_pages if summary_page_classifications.get(id(page)) == "narrative"
        ]
        if narrative_pages:
            candidate_pages = narrative_pages
        else:
            non_noise_pages = [
                page for page in candidate_pages if summary_page_classifications.get(id(page)) != "noise"
            ]
            candidate_pages = non_noise_pages or candidate_pages

    def score_page(page: SourcePage) -> int:
        score = 0
        category = (page.page_category or "").lower()
        page_url = (page.source_url or "").lower()
        page_text = (page.raw_markdown or "").lower()[:2500]
        if category in preferred:
            score += 100
        elif category:
            score += 20

        # Home/root URLs are usually useful fallback context.
        if page.source_url and re.search(r"https?://[^/]+/?$", page.source_url):
            score += 40

        # Slightly prefer shorter paths and recent pages.
        path_len = len(page.source_url or "")
        score += max(0, 30 - min(path_len, 30))

        page_host = _canonical_host(page.source_url)
        if _host_matches(page_host, school_host):
            score += 80

        if include_tokens:
            url_hits = sum(1 for token in include_tokens if token in page_url)
            text_hits = sum(1 for token in include_tokens if token in page_text)
            if url_hits:
                score += min(90, 35 * url_hits)
            if text_hits:
                score += min(120, 25 * text_hits)

        if use_case == "general_info":
            if category in {"about", "contact"}:
                score += 40
            if any(
                token in page_url
                for token in (
                    "about",
                    "za-nas",
                    "team",
                    "ekip",
                    "program",
                    "obuchenie",
                    "curriculum",
                    "mission",
                    "vision",
                )
            ):
                score += 60
            if any(
                token in page_url
                for token in (
                    "admission",
                    "priem",
                    "policy",
                    "privacy",
                    "gdpr",
                    "rules",
                    "regulation",
                    "internal",
                    "document",
                    "forms",
                    "application",
                    "terms",
                    "conditions",
                    "legal",
                )
            ):
                score -= 90
            if category in {"admission", "pricing"}:
                score -= 35
        elif use_case == "pricing":
            if any(token in unquote(page_url) for token in _PRICING_URL_TOKENS):
                score += 60
            # The page text is the evidence: a page that states prices outranks a
            # preferred-category page that states none (school 568's fee.html had no
            # category and lost to admission/contact pages).
            score += _PRICE_EVIDENCE_SCORE * min(
                len(currency_price_starts(page.raw_markdown or "")), _PRICE_EVIDENCE_MAX_PRICES
            )
        elif use_case == "general_summary_source":
            classification = summary_page_classifications.get(id(page))
            if classification == "narrative":
                score += 140
            elif classification == "operational":
                score -= 80
            elif classification == "noise":
                score -= 220
            if category in {"about", "programs", "facilities"}:
                score += 80
            if category in {"contact", "admission", "pricing", "gallery", "news"}:
                score -= 110
            if any(token in page_url for token in _SUMMARY_SOURCE_PAGE_GOOD_URL_TOKENS):
                score += 90
            if any(token in page_url for token in _SUMMARY_SOURCE_PAGE_BAD_URL_TOKENS):
                score -= 140
            if any(token in page_text for token in _SUMMARY_SOURCE_PAGE_BAD_TEXT_TOKENS):
                score -= 100
        elif use_case.startswith("general_"):
            if category in {"about", "contact"}:
                score += 45
            if any(token in page_url for token in ("admission", "priem", "policy", "gdpr", "terms", "legal")):
                score -= 90
        return score

    if use_case == "pricing":
        candidate_pages = _drop_url_variant_pages(candidate_pages)
    sorted_pages = sorted(candidate_pages, key=score_page, reverse=True)

    max_pages = 3 if (use_case == "general_info" or use_case.startswith("general_")) else 4
    if use_case == "general_summary_source":
        max_pages = 2

    # Materialize the pages that will actually be packed, so each one can reserve room
    # for the pages still queued behind it.
    queued: list[tuple[str, str]] = []
    for page in sorted_pages:
        if len(queued) >= max_pages:
            break
        text = (page.raw_markdown or "").strip()
        if not text:
            continue
        if use_case == "general_summary_source":
            text = _prepare_summary_source_page_text(text)
            if not text:
                continue
        queued.append((page.source_url or "", f"--- SOURCE: {page.source_url} ---\n" + text))

    content_parts: list[str] = []
    urls_used: list[str] = []
    current_chars = 0

    for index, (source_url, candidate) in enumerate(queued):
        # One oversized page must not swallow the whole budget and starve a small page
        # that carries the actual fees.
        reserved = min(
            sum(
                len(later)
                for _, later in queued[index + 1 :]
                if len(later) <= _SMALL_QUEUED_PAGE_CHARS
            ),
            max_chars // _MAX_RESERVE_FRACTION,
        )
        allowance = max_chars - current_chars - reserved

        if len(candidate) > allowance:
            if allowance < _MIN_USABLE_PAGE_CHARS:
                # Only a useless fragment would fit; skip it and keep going, so pages
                # behind it can still use the room reserved for them.
                continue
            if use_case == "pricing":
                candidate = _price_dense_window(candidate, allowance)
            else:
                candidate = candidate[:allowance]

        content_parts.append(candidate)
        urls_used.append(source_url)
        current_chars += len(candidate)

    return "\n\n".join(content_parts), [url for url in urls_used if url]

def _normalize_general_info_output(
    parsed: GeneralInfoExtractionOutput,
    country_code: str,
) -> tuple[GeneralInfoExtractionOutput, dict[str, Any] | None, dict[str, str] | None]:
    """Post-process extraction output for cleaner UI-safe fields + optional i18n split."""
    display_name_i18n = _normalize_display_name_i18n(parsed.display_name_i18n, country_code)
    language_entries = _normalize_languages(parsed.languages)
    facilities = _normalize_text_list(parsed.facilities)
    programs = _normalize_text_list(parsed.programs)
    extracurricular = _normalize_text_list(parsed.extracurricular)
    accreditations = _normalize_text_list(parsed.accreditations)
    class_size = _normalize_scalar_text(parsed.class_size, _rules()._GENERAL_INFO_CLASS_SIZE_MAX_LEN)
    founded_year = _normalize_founded_year(parsed.founded_year)
    admission = _normalize_admission_output(parsed.admission)
    operations = _normalize_operations_output(parsed.operations)
    services = _normalize_services_output(parsed.services)
    pricing_terms = _normalize_pricing_terms_output(parsed.pricing_terms)
    summary_source = _normalize_summary_source_output(
        parsed.summary_source,
        language_candidates=language_entries,
        program_candidates=programs,
    )

    primary_lang = _pick_primary_text_lang(
        country_code=country_code,
        values=(
            [entry.language for entry in language_entries]
            + facilities
            + programs
            + extracurricular
            + accreditations
            + admission.deadlines
            + admission.required_documents
            + admission.application_steps
            + admission.entrance_requirements
            + admission.available_spots
            + operations.day_options
            + operations.daily_schedule
            + operations.meals
            + operations.transport
            + operations.uniforms
            + services.support_services
            + services.safety_features
            + pricing_terms.discounts
            + pricing_terms.installments
            + pricing_terms.included_items
            + pricing_terms.excluded_items
            + pricing_terms.deposits
            + pricing_terms.application_fees
            + pricing_terms.registration_fees
        ),
    )

    split_languages = _split_language_entries(language_entries, primary_lang)
    split_facilities = _split_text_by_lang(facilities, primary_lang)
    split_programs = _split_text_by_lang(programs, primary_lang)
    split_extracurricular = _split_text_by_lang(extracurricular, primary_lang)
    split_accreditations = _split_text_by_lang(accreditations, primary_lang)

    normalized = GeneralInfoExtractionOutput(
        languages=split_languages["primary"],
        facilities=split_facilities["primary"],
        programs=split_programs["primary"],
        extracurricular=split_extracurricular["primary"],
        class_size=class_size,
        founded_year=founded_year,
        accreditations=split_accreditations["primary"],
        admission=admission,
        operations=operations,
        services=services,
        pricing_terms=pricing_terms,
        summary_source=summary_source,
        has_useful_info=False,
    )
    normalized.has_useful_info = _score_general_info_output(normalized) > 0

    extracted_i18n = _build_general_info_i18n(
        split_languages=split_languages,
        split_facilities=split_facilities,
        split_programs=split_programs,
        split_extracurricular=split_extracurricular,
        split_accreditations=split_accreditations,
    )
    return normalized, extracted_i18n, display_name_i18n

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

def _normalize_admission_output(value: AdmissionExtractionOutput) -> AdmissionExtractionOutput:
    normalized = AdmissionExtractionOutput(
        deadlines=_filter_section_values(
            value.deadlines,
            require_patterns=_rules()._ADMISSION_DEADLINE_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        required_documents=_filter_section_values(
            value.required_documents,
            require_patterns=_rules()._ADMISSION_REQUIRED_DOCUMENTS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
            heading_only_markers=_rules()._ADMISSION_REQUIRED_DOCUMENTS_HEADING_MARKERS,
        ),
        application_steps=_filter_section_values(
            value.application_steps,
            require_patterns=_rules()._ADMISSION_APPLICATION_STEPS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        entrance_requirements=_filter_section_values(
            value.entrance_requirements,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        available_spots=_filter_section_values(
            value.available_spots,
            require_patterns=_rules()._ADMISSION_AVAILABLE_SPOTS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        has_useful_info=False,
    )
    normalized.has_useful_info = any(
        (
            normalized.deadlines,
            normalized.required_documents,
            normalized.application_steps,
            normalized.entrance_requirements,
            normalized.available_spots,
        )
    )
    return normalized

def _normalize_operations_output(value: OperationsExtractionOutput) -> OperationsExtractionOutput:
    working_hours = _normalize_scalar_text(value.working_hours, _rules()._GENERAL_INFO_ITEM_MAX_LEN)
    if working_hours:
        lowered_working_hours = working_hours.lower()
        if _line_matches_any_pattern(lowered_working_hours, _rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS):
            working_hours = None
        elif _is_probable_section_noise_line(lowered_working_hours):
            working_hours = None
        elif not (
            _line_matches_any_pattern(lowered_working_hours, _rules()._OPERATIONS_WORKING_HOURS_PATTERNS)
            or _rules()._WORKING_HOURS_TIME_PATTERN.search(working_hours)
        ):
            working_hours = None

    normalized = OperationsExtractionOutput(
        working_hours=working_hours,
        day_options=_filter_section_values(
            value.day_options,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        daily_schedule=_filter_section_values(
            value.daily_schedule,
            require_patterns=_rules()._OPERATIONS_DAILY_SCHEDULE_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        meals=_filter_section_values(
            value.meals,
            require_patterns=_rules()._OPERATIONS_MEALS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
            heading_only_markers=_rules()._OPERATIONS_MEALS_HEADING_MARKERS,
        ),
        transport=_filter_section_values(
            value.transport,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        uniforms=_filter_section_values(
            value.uniforms,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        has_useful_info=False,
    )
    normalized.has_useful_info = any(
        (
            normalized.working_hours,
            normalized.day_options,
            normalized.daily_schedule,
            normalized.meals,
            normalized.transport,
            normalized.uniforms,
        )
    )
    return normalized

def _normalize_services_output(value: ServicesExtractionOutput) -> ServicesExtractionOutput:
    normalized = ServicesExtractionOutput(
        support_services=_filter_section_values(
            value.support_services,
            require_patterns=_rules()._SERVICES_SUPPORT_REQUIRE_PATTERNS,
            exclude_patterns=(*_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS, *_rules()._SERVICES_SUPPORT_EXCLUDE_PATTERNS),
        ),
        safety_features=_filter_section_values(
            value.safety_features,
            require_patterns=_rules()._SERVICES_SAFETY_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        has_useful_info=False,
    )
    normalized.has_useful_info = any((normalized.support_services, normalized.safety_features))
    return normalized

def _normalize_pricing_terms_output(value: PricingTermsExtractionOutput) -> PricingTermsExtractionOutput:
    included_items = _filter_section_values(
        value.included_items,
        require_patterns=_rules()._PRICING_TERMS_INCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    excluded_items = _filter_section_values(
        value.excluded_items,
        require_patterns=_rules()._PRICING_TERMS_EXCLUDED_REQUIRE_PATTERNS,
        exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
    )
    included_items, excluded_items = _reconcile_pricing_include_exclude(
        included_items,
        excluded_items,
    )

    normalized = PricingTermsExtractionOutput(
        discounts=_filter_section_values(
            value.discounts,
            require_patterns=_rules()._PRICING_TERMS_DISCOUNTS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        installments=_filter_section_values(
            value.installments,
            require_patterns=_rules()._PRICING_TERMS_INSTALLMENTS_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        included_items=included_items,
        excluded_items=excluded_items,
        deposits=_filter_section_values(
            value.deposits,
            require_patterns=_rules()._PRICING_TERMS_DEPOSIT_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        application_fees=_filter_section_values(
            value.application_fees,
            require_patterns=_rules()._PRICING_TERMS_APPLICATION_FEE_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        registration_fees=_filter_section_values(
            value.registration_fees,
            require_patterns=_rules()._PRICING_TERMS_REGISTRATION_FEE_REQUIRE_PATTERNS,
            exclude_patterns=_rules()._SECTION_DEFAULT_EXCLUDE_PATTERNS,
        ),
        has_useful_info=False,
    )
    normalized.has_useful_info = any(
        (
            normalized.discounts,
            normalized.installments,
            normalized.included_items,
            normalized.excluded_items,
            normalized.deposits,
            normalized.application_fees,
            normalized.registration_fees,
        )
    )
    return normalized

def _normalize_languages(values: list[ExtractedLanguageFocus]) -> list[ExtractedLanguageFocus]:
    cleaned: list[ExtractedLanguageFocus] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        for language in _extract_clean_text_candidates(value.language):
            normalized_language = _sanitize_label(language)
            if not normalized_language:
                continue

            level_text = ""
            if value.level is not None:
                level_candidates = _extract_clean_text_candidates(value.level)
                if level_candidates:
                    sanitized_level = _sanitize_label(level_candidates[0])
                    if sanitized_level:
                        level_text = sanitized_level

            dedupe_key = (normalized_language.lower(), level_text.lower())
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            cleaned.append(
                ExtractedLanguageFocus(
                    language=normalized_language,
                    level=level_text or None,
                )
            )
            if len(cleaned) >= _rules()._GENERAL_INFO_LIST_MAX_ITEMS:
                return cleaned
    return cleaned

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
            if len(cleaned) >= _rules()._GENERAL_INFO_LIST_MAX_ITEMS:
                return cleaned
    return cleaned

def _is_generic_summary_source_value(value: str) -> bool:
    lowered = str(value or "").strip().casefold()
    if not lowered:
        return True
    if lowered in _SUMMARY_SOURCE_GENERIC_EXACT:
        return True
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _SUMMARY_SOURCE_GENERIC_PATTERNS):
        return True
    slogan_markers = (
        "всяко дете може повече",
        "every child is capable of more",
        "future challenges",
        "новото време",
    )
    return any(marker in lowered for marker in slogan_markers)


def _normalize_summary_source_positioning(raw_value: Any) -> str | None:
    text = _clean_summary_source_candidate(raw_value, max_len=180)
    if not text:
        return None
    if _is_generic_summary_source_value(text):
        return None
    if not _summary_source_matches_category(text, "positioning"):
        return None
    return text


def _normalize_summary_source_list(values: Any, category: str) -> list[str]:
    if not isinstance(values, list):
        values = [values] if values is not None else []
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        for candidate in _extract_clean_text_candidates(value):
            label = _clean_summary_source_candidate(candidate, max_len=120)
            if not label or _is_generic_summary_source_value(label):
                continue
            if not _summary_source_matches_category(label, category):
                continue
            key = re.sub(r"[\W_]+", "", label.casefold())
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(label)
            if len(cleaned) >= _SUMMARY_SOURCE_MAX_ITEMS:
                return cleaned
    return cleaned


def _clean_summary_source_candidate(raw_value: Any, max_len: int) -> str | None:
    if raw_value is None:
        return None
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", str(raw_value or ""))
    text = re.sub(r"^---\s*SOURCE:.*?---\s*", "", text, flags=re.IGNORECASE)
    text = text.replace("**", "")
    text = re.sub(r"^[#>\-\*\s]+", "", text)
    label = _sanitize_label(text, max_len=max_len)
    if not label:
        return None
    lowered = label.casefold()
    if "©" in label or "(c)" in lowered:
        return None
    if lowered.startswith(("source:", "home", "начало")):
        return None
    if lowered.startswith(("в случай", "че ", "if ", "when ")):
        return None
    if "@" in label and re.search(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", label, flags=re.IGNORECASE):
        return None
    if "http://" in lowered or "https://" in lowered or "www." in lowered:
        return None
    if lowered in _SUMMARY_SOURCE_NAV_EXACT:
        return None
    tokens = re.findall(r"[a-zа-я0-9]+", lowered, flags=re.IGNORECASE)
    if len(tokens) == 1 and tokens[0] not in _SUMMARY_SOURCE_ALLOWED_SINGLE_TOKENS:
        return None
    school_type_tokens = {"училище", "детска", "градина", "school", "kindergarten", "preschool", "чоу", "чдг"}
    if len(tokens) <= 3 and any(token in school_type_tokens for token in tokens):
        return None
    if lowered.endswith((" гр", " city")):
        return None
    return label.rstrip(" .;,:")


def _summary_source_matches_category(text: str, category: str) -> bool:
    lowered = text.casefold()
    hints = _SUMMARY_SOURCE_CATEGORY_HINTS.get(category, ())
    if any(hint in lowered for hint in hints):
        return True

    token_count = len(re.findall(r"[a-zа-я0-9]+", lowered, flags=re.IGNORECASE))
    if token_count < 3:
        return False

    if category == "teaching_approach":
        return bool(
            re.search(
                r"\b(?:approach|method|model|pedagog|learning|teaching|подход|метод|модел|педагог|обучение|учене)\b",
                lowered,
                flags=re.IGNORECASE,
            )
        )
    if category == "student_experience":
        return bool(
            re.search(
                r"\b(?:activity|club|summer|camp|meal|snack|transport|sport|arts|заним|клуб|лагер|"
                r"хран|закуск|транспорт|спорт|творч)\b",
                lowered,
                flags=re.IGNORECASE,
            )
        )
    if category == "community_signals":
        return bool(
            re.search(
                r"\b(?:parent|family|community|support|partnership|родител|семей|общност|подкреп|партньор)\b",
                lowered,
                flags=re.IGNORECASE,
            )
        )
    if category == "differentiators":
        return bool(
            re.search(
                r"\b(?:licensed|license|accredit|bilingual|international|stem|ib|cambridge|waldorf|"
                r"лиценз|акредитац|двуезич|международ|stem|ib|cambridge|валдорф)\b",
                lowered,
                flags=re.IGNORECASE,
            )
        )
    if category == "positioning":
        return bool(
            re.search(
                r"\b(?:school|kindergarten|serves|offers|private|state|international|"
                r"училище|градина|предлага|частн|държавн|международ)\b",
                lowered,
                flags=re.IGNORECASE,
            )
        )
    return False


def _derive_summary_source_canonical_tags(
    *,
    positioning: str | None,
    teaching_approach: list[str],
    student_experience: list[str],
    community_signals: list[str],
    differentiators: list[str],
    language_candidates: list[ExtractedLanguageFocus] | None = None,
    program_candidates: list[str] | None = None,
    explicit_tags: list[str] | None = None,
) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()

    def add(tag: str) -> None:
        if not tag or tag in seen:
            return
        seen.add(tag)
        tags.append(tag)

    combined_values = [
        positioning or "",
        *(teaching_approach or []),
        *(student_experience or []),
        *(community_signals or []),
        *(differentiators or []),
        *(program_candidates or []),
    ]
    combined_text = f" {' '.join(value.casefold() for value in combined_values if value)} "

    for tag, patterns in _SUMMARY_SOURCE_CANONICAL_TAG_PATTERNS:
        if any(pattern in combined_text for pattern in patterns):
            add(tag)

    normalized_languages = {
        _sanitize_label(candidate.language, max_len=40).casefold()
        for candidate in (language_candidates or [])
        if _sanitize_label(candidate.language, max_len=40)
    }
    if "german" in normalized_languages:
        add("German-focused")
    if "french" in normalized_languages:
        add("French-focused")
    if "spanish" in normalized_languages:
        add("Spanish-focused")

    return tags[:6]


def _normalize_summary_source_output(
    value: SummarySourceExtractionOutput,
    *,
    language_candidates: list[ExtractedLanguageFocus] | None = None,
    program_candidates: list[str] | None = None,
) -> SummarySourceExtractionOutput:
    positioning = _normalize_summary_source_positioning(value.positioning)
    teaching_approach = _normalize_summary_source_list(value.teaching_approach, "teaching_approach")
    student_experience = _normalize_summary_source_list(value.student_experience, "student_experience")
    community_signals = _normalize_summary_source_list(value.community_signals, "community_signals")
    differentiators = _normalize_summary_source_list(value.differentiators, "differentiators")
    canonical_tags = _derive_summary_source_canonical_tags(
        positioning=positioning,
        teaching_approach=teaching_approach,
        student_experience=student_experience,
        community_signals=community_signals,
        differentiators=differentiators,
        language_candidates=language_candidates,
        program_candidates=program_candidates,
        explicit_tags=value.canonical_tags,
    )
    normalized = SummarySourceExtractionOutput(
        positioning=positioning,
        teaching_approach=teaching_approach,
        student_experience=student_experience,
        community_signals=community_signals,
        differentiators=differentiators,
        canonical_tags=canonical_tags,
        has_useful_info=False,
    )
    normalized.has_useful_info = any(
        (
            normalized.positioning,
            normalized.teaching_approach,
            normalized.student_experience,
            normalized.community_signals,
            normalized.differentiators,
            normalized.canonical_tags,
        )
    )
    return normalized


def _extract_summary_source_deterministic(text: str) -> SummarySourceExtractionOutput:
    if not text:
        return SummarySourceExtractionOutput()

    sentences = [
        fragment.strip(" -*_")
        for fragment in re.split(r"(?<=[.!?])\s+|[\n\r]+", text)
        if fragment and fragment.strip()
    ]
    buckets: dict[str, list[str]] = {key: [] for key in _SUMMARY_SOURCE_CATEGORY_HINTS}
    seen: dict[str, set[str]] = {key: set() for key in _SUMMARY_SOURCE_CATEGORY_HINTS}

    for sentence in sentences:
        candidate = _clean_summary_source_candidate(sentence, max_len=180)
        if not candidate or _is_generic_summary_source_value(candidate):
            continue
        lowered = candidate.casefold()
        if len(re.findall(r"[A-Za-zА-Яа-я]", candidate)) < 12:
            continue
        for category, hints in _SUMMARY_SOURCE_CATEGORY_HINTS.items():
            if not any(hint in lowered for hint in hints):
                continue
            key = candidate.casefold()
            if key in seen[category]:
                continue
            seen[category].add(key)
            buckets[category].append(candidate)
            if len(buckets[category]) >= _SUMMARY_SOURCE_MAX_ITEMS:
                break

    normalized = SummarySourceExtractionOutput(
        positioning=buckets["positioning"][0] if buckets["positioning"] else None,
        teaching_approach=buckets["teaching_approach"],
        student_experience=buckets["student_experience"],
        community_signals=buckets["community_signals"],
        differentiators=buckets["differentiators"],
        has_useful_info=False,
    )
    return _normalize_summary_source_output(normalized)

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
            if len(filtered) >= _rules()._GENERAL_INFO_LIST_MAX_ITEMS:
                return filtered
    return filtered

def _normalize_scalar_text(raw_value: Any, max_len: int) -> str | None:
    candidates = _extract_clean_text_candidates(raw_value)
    if not candidates:
        return None
    for candidate in candidates:
        label = _sanitize_label(candidate, max_len=max_len)
        if label:
            return label
    return None

def _normalize_founded_year(raw_value: Any) -> str | None:
    text = _normalize_scalar_text(raw_value, max_len=32)
    if not text:
        return None
    match = re.search(r"(?:19|20)\d{2}", text)
    if match:
        return match.group(0)
    return text

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
    if len(fragments) == 1 and "," in text and len(text) < 120:
        fragments = [part.strip() for part in text.split(",")]
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
        for key in _rules()._GENERAL_INFO_TEXT_KEYS:
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
        max_len = _rules()._GENERAL_INFO_ITEM_MAX_LEN

    text = str(raw_text).replace("\x00", "").strip()
    if not text:
        return None

    text = re.sub(r"^[\"'`]+|[\"'`]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" ,;|")
    if not text:
        return None

    lowered = text.lower()
    if lowered in _rules()._GENERAL_INFO_JUNK_VALUES:
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

def _build_general_info_i18n(
    split_languages: dict[str, list[ExtractedLanguageFocus]],
    split_facilities: dict[str, list[str]],
    split_programs: dict[str, list[str]],
    split_extracurricular: dict[str, list[str]],
    split_accreditations: dict[str, list[str]],
) -> dict[str, Any] | None:
    output: dict[str, Any] = {}
    for lang in ("bg", "en"):
        payload: dict[str, Any] = {}
        if split_languages[lang]:
            payload["languages"] = [entry.model_dump() for entry in split_languages[lang]]
        if split_facilities[lang]:
            payload["facilities"] = split_facilities[lang]
        if split_programs[lang]:
            payload["programs"] = split_programs[lang]
        if split_extracurricular[lang]:
            payload["extracurricular"] = split_extracurricular[lang]
        if split_accreditations[lang]:
            payload["accreditations"] = split_accreditations[lang]
        if payload:
            output[lang] = payload
    return output or None

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

def _get_usage(result: Any) -> tuple[int, int]:
    """Extract input/output tokens from pydantic-ai result."""
    usage_obj = result.usage() if callable(getattr(result, "usage", None)) else getattr(result, "usage", None)
    if usage_obj is None:
        return 0, 0

    input_tokens = getattr(usage_obj, "input_tokens", None)
    if input_tokens is None:
        input_tokens = getattr(usage_obj, "request_tokens", 0)

    output_tokens = getattr(usage_obj, "output_tokens", None)
    if output_tokens is None:
        output_tokens = getattr(usage_obj, "response_tokens", 0)

    return int(input_tokens or 0), int(output_tokens or 0)

def _is_model_or_provider_error(exc: Exception) -> bool:
    """Allow capable fallback only for model/provider-level failures."""
    if isinstance(exc, (ModelHTTPError, ModelAPIError)):
        return True
    message = str(exc).lower()
    return any(marker in message for marker in _rules()._PROVIDER_ERROR_MARKERS)

def _is_output_validation_error(exc: Exception) -> bool:
    stack = [exc]
    seen: set[int] = set()

    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        message = str(current).lower()
        if any(marker in message for marker in _rules()._OUTPUT_VALIDATION_ERROR_MARKERS):
            return True
        if isinstance(current, UnexpectedModelBehavior) and "retry" in message and "validation" in message:
            return True

        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if isinstance(cause, Exception):
            stack.append(cause)
        if isinstance(context, Exception):
            stack.append(context)

        nested = getattr(current, "exceptions", None)
        if isinstance(nested, (list, tuple)):
            for child in nested:
                if isinstance(child, Exception):
                    stack.append(child)

    return False

def _score_general_info_output(parsed: GeneralInfoExtractionOutput) -> int:
    """Heuristic quality score for general-info extraction payloads."""
    score = 0
    if any(_is_usable_text(entry.language) for entry in parsed.languages):
        score += 1
    if _count_usable_text_values(parsed.facilities) > 0:
        score += 1
    if _count_usable_text_values(parsed.programs) > 0:
        score += 1
    if _count_usable_text_values(parsed.extracurricular) > 0:
        score += 1
    if _count_usable_text_values(parsed.accreditations) > 0:
        score += 1
    if _is_usable_text(parsed.class_size):
        score += 1
    if _is_usable_text(parsed.founded_year):
        score += 1
    if _count_usable_text_values(parsed.admission.deadlines) > 0:
        score += 1
    if _count_usable_text_values(parsed.admission.required_documents) > 0:
        score += 1
    if _count_usable_text_values(parsed.admission.application_steps) > 0:
        score += 1
    if _count_usable_text_values(parsed.admission.available_spots) > 0:
        score += 1
    if _is_usable_text(parsed.operations.working_hours):
        score += 1
    if _count_usable_text_values(parsed.operations.day_options) > 0:
        score += 1
    if _count_usable_text_values(parsed.operations.meals) > 0:
        score += 1
    if _count_usable_text_values(parsed.services.support_services) > 0:
        score += 1
    if _count_usable_text_values(parsed.services.safety_features) > 0:
        score += 1
    if _count_usable_text_values(parsed.pricing_terms.discounts) > 0:
        score += 1
    if _is_usable_text(parsed.summary_source.positioning):
        score += 1
    if _count_usable_text_values(parsed.summary_source.teaching_approach) > 0:
        score += 1
    if _count_usable_text_values(parsed.summary_source.student_experience) > 0:
        score += 1
    if _count_usable_text_values(parsed.summary_source.community_signals) > 0:
        score += 1
    if _count_usable_text_values(parsed.summary_source.differentiators) > 0:
        score += 1
    if _count_usable_text_values(parsed.summary_source.canonical_tags) > 0:
        score += 1
    return score

def _count_usable_text_values(values: list[str]) -> int:
    return sum(1 for value in values if _is_usable_text(value))

def _is_usable_text(value: Any) -> bool:
    if value is None:
        return False
    return _sanitize_label(str(value)) is not None

def _normalize_key(raw: str) -> str:
    return re.sub(r"[^a-zа-я0-9]+", "", (raw or "").strip().lower())

def _coerce_price_category(raw: Optional[str | PriceCategory]) -> Optional[PriceCategory]:
    if raw is None:
        return None
    if isinstance(raw, PriceCategory):
        return raw
    if isinstance(raw, str) and "." in raw:
        tail = raw.split(".")[-1]
        normalized_tail = _normalize_key(tail)
        if normalized_tail in _rules()._CATEGORY_ALIASES:
            return _rules()._CATEGORY_ALIASES[normalized_tail]
    normalized = _normalize_key(raw)
    return _rules()._CATEGORY_ALIASES.get(normalized)

def _coerce_price_period(raw: Optional[str | PricePeriod]) -> Optional[PricePeriod]:
    if raw is None:
        return None
    if isinstance(raw, PricePeriod):
        return raw
    if isinstance(raw, str) and "." in raw:
        tail = raw.split(".")[-1]
        normalized_tail = _normalize_key(tail)
        if normalized_tail in _rules()._PERIOD_ALIASES:
            return _rules()._PERIOD_ALIASES[normalized_tail]
    normalized = _normalize_key(raw)
    return _rules()._PERIOD_ALIASES.get(normalized)

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

def _format_price_value_text(
    amount: Optional[float],
    amount_min: Optional[float],
    amount_max: Optional[float],
    currency: Optional[str],
) -> Optional[str]:
    code = (currency or "BGN")[:3].upper()
    if amount is not None:
        return f"{amount:.2f} {code}"
    if amount_min is not None and amount_max is not None:
        return f"{amount_min:.2f}-{amount_max:.2f} {code}"
    if amount_min is not None:
        return f"from {amount_min:.2f} {code}"
    if amount_max is not None:
        return f"up to {amount_max:.2f} {code}"
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
