"""Summary-source facts: which pages to read, cleaning and categorising the values, canonical tags."""

from __future__ import annotations

import re
from typing import Any

from app.models.source_page import SourcePage
from app.schemas.extraction import (
    ExtractedLanguageFocus,
    SummarySourceExtractionOutput,
)
from app.scrapers.extraction.text_values import (
    _extract_clean_text_candidates,
    _sanitize_label,
)

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
