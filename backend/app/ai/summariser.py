"""LLM-powered bilingual school summarization from structured facts."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry

from app.ai.client import calculate_cost, create_agent, get_model
from app.services.provider_costs import execute_billable_request
from app.config import get_settings
from app.schemas.llm_outputs import SchoolSummaryStrict
from app.utils.i18n_resolver import derive_english_name
from app.utils.transliteration import transliterate_bulgarian


class SummaryIdentity(BaseModel):
    name_i18n: dict[str, str] = Field(default_factory=dict)
    display_name_i18n: dict[str, str] = Field(default_factory=dict)
    school_type: str
    education_level: str
    city: str | None = None
    locality_i18n: dict[str, str] = Field(default_factory=dict)
    primary_address_i18n: dict[str, str] = Field(default_factory=dict)
    district: str | None = None


class SummaryOffering(BaseModel):
    languages: list[str] = Field(default_factory=list)
    programs: list[str] = Field(default_factory=list)
    facilities: list[str] = Field(default_factory=list)
    extracurricular: list[str] = Field(default_factory=list)
    accreditations: list[str] = Field(default_factory=list)
    founded_year: str | None = None
    class_size: str | None = None
    positioning: str | None = None
    teaching_approach: list[str] = Field(default_factory=list)
    student_experience: list[str] = Field(default_factory=list)
    community_signals: list[str] = Field(default_factory=list)
    differentiators: list[str] = Field(default_factory=list)
    canonical_tags: list[str] = Field(default_factory=list)


class SummaryOperations(BaseModel):
    admission_deadlines: list[str] = Field(default_factory=list)
    required_documents: list[str] = Field(default_factory=list)
    application_steps: list[str] = Field(default_factory=list)
    entrance_requirements: list[str] = Field(default_factory=list)
    available_spots: list[str] = Field(default_factory=list)
    working_hours: str | None = None
    day_options: list[str] = Field(default_factory=list)
    meals: list[str] = Field(default_factory=list)
    transport: list[str] = Field(default_factory=list)
    uniforms: list[str] = Field(default_factory=list)
    support_services: list[str] = Field(default_factory=list)
    safety_features: list[str] = Field(default_factory=list)


class SummaryPricing(BaseModel):
    has_pricing: bool = False
    fee_categories: list[str] = Field(default_factory=list)
    pricing_terms: list[str] = Field(default_factory=list)


class SummaryAcademic(BaseModel):
    exam_result_types: list[str] = Field(default_factory=list)
    recent_exam_years: list[int] = Field(default_factory=list)
    has_admission_thresholds: bool = False
    has_points_history: bool = False
    has_min_score_history: bool = False


class SummaryInput(BaseModel):
    identity: SummaryIdentity
    offering: SummaryOffering | None = None
    operations: SummaryOperations | None = None
    pricing: SummaryPricing | None = None
    academic: SummaryAcademic | None = None


_GENERIC_BG_PREFIX_RE = re.compile(
    r"^(?:\d+\s+)?(?:частн(?:а|о)?|държавн(?:а|о)?|международн(?:а|о)?|основно|начално|"
    r"средно|обединено|езиково|профилирано|училище|детска|градина|с\s+ранно)\b",
    flags=re.IGNORECASE,
)
_GENERIC_BG_ABBREVIATION_RE = re.compile(
    r"^(?:\d+\s+)?(?:ОУ|ОбУ|СУ|НУ|ПГ|ППМГ|ДГ|ЦДГ|ЧОУ|ЧСУ|ЧДГ)\b",
    flags=re.IGNORECASE,
)
_LEGAL_SUFFIX_RE = re.compile(r"(?:\s*[-,]?\s*)\b(?:ЕООД|ООД|АД|ЕАД|ЕТ)\b\.?$", flags=re.IGNORECASE)
_ABBREVIATED_EN_NAME_RE = re.compile(r"^(?:st|sv)\.\s+(?:st|sv)\.?$", flags=re.IGNORECASE)
SUMMARY_MODEL_TIER = "capable"
_FOCUS_PATTERN_PHRASES: tuple[tuple[tuple[str, ...], dict[str, str]], ...] = (
    (("montessori",), {"bg": "с Монтесори подход", "en": "with a Montessori approach"}),
    (("waldorf",), {"bg": "с Валдорф подход", "en": "with a Waldorf approach"}),
    (
        ("reggio emilia", "reggio-emilia", "reggio"),
        {"bg": "с подход Reggio Emilia", "en": "with a Reggio Emilia approach"},
    ),
    (
        ("international baccalaureate", " ib ", "ib programme", "ib program"),
        {"bg": "с IB програма", "en": "with an IB programme"},
    ),
    (("cambridge",), {"bg": "с програма Cambridge", "en": "with a Cambridge programme"}),
    (("a-level", "a level"), {"bg": "с A-Level програма", "en": "with an A-Level programme"}),
    (
        ("project-based", "project based", "проектно базирано"),
        {"bg": "с проектно базирано обучение", "en": "with project-based learning"},
    ),
    (
        ("fusion model", "fusion educational model", "educational model fusion", "модел fusion"),
        {"bg": "с образователния модел Fusion", "en": "with the Fusion educational model"},
    ),
    (("stem", "steam"), {"bg": "с фокус върху STEM", "en": "with a STEM focus"}),
    (("bilingual", "двуезич"), {"bg": "с двуезична програма", "en": "with a bilingual programme"}),
    (
        ("programming", "robotics", "програмиран", "роботик"),
        {"bg": "с фокус върху програмиране и роботика", "en": "with a focus on programming and robotics"},
    ),
    (
        ("performing arts", "scenic arts", "сценични изкуства", "actor", "acting", "pop and jazz", "пеене"),
        {"bg": "с фокус върху сценичните изкуства", "en": "with a focus on performing arts"},
    ),
    (
        ("early foreign language education", "ранно чуждоезиково"),
        {"bg": "с ранно чуждоезиково обучение", "en": "with early foreign language education"},
    ),
    (
        ("german-focused", "немска", "немски език", "german-language", "german school"),
        {"bg": "с фокус върху немски език", "en": "with German language focus"},
    ),
    (
        ("french-focused", "френска", "френски език"),
        {"bg": "с фокус върху френски език", "en": "with French language focus"},
    ),
    (("spanish-focused",), {"bg": "с фокус върху испански език", "en": "with Spanish language focus"}),
)
_LANGUAGE_LABELS_BG = {
    "english": "английски",
    "german": "немски",
    "french": "френски",
    "spanish": "испански",
    "italian": "италиански",
    "bulgarian": "български",
    "russian": "руски",
}
_LANGUAGE_LABELS_EN = {
    "английски": "English",
    "немски": "German",
    "френски": "French",
    "испански": "Spanish",
    "италиански": "Italian",
    "български": "Bulgarian",
    "руски": "Russian",
}
_GENERIC_FOCUS_BLOCKED_TOKENS = (
    "license",
    "licensed",
    "licence",
    "лиценз",
    "акредитац",
    "accredit",
    "рд ",
    "rd ",
    "мон",
    "ministry of education",
    "мисия",
    "mission",
    "директор",
    "ръководител",
    "teacher",
    "teachers",
    "учител",
    "преподавател",
)
_GENERIC_FOCUS_BLOCKED_VALUES = {
    "образователни направления",
    "теми",
    "themes",
    "activities",
    "дейности",
    "programs",
}
_SHORT_PROMOTIONAL_TOKENS = (
    "innovative",
    "leading",
    "premier",
    "exceptional",
    "outstanding",
    "top-tier",
    "иноватив",
    "водещ",
    "изключител",
)
_SHORT_SIGNAL_TOKENS = {
    "bg": ("подход", "програма", "модел", "обучение", "предлага", "следва", "език"),
    "en": ("approach", "programme", "program", "model", "offers", "provides", "follows", "language"),
}
_SEMANTIC_BLOCK_PATTERNS = {
    "bg": (
        r"\b(?:няма|липсва(?:т)?|не\s+(?:предлага|разполага|предоставя|са?\s+уточнен))\b",
        r"\b(?:в\s+момента|днес|тази\s+година|наскоро)\b",
        r"\b(?:родител(?:и|ите)|семейств(?:а|ата))\s+(?:хвалят|споделят|казват|оценяват)\b",
        r"\b(?:най-добр|водещ|изключител|престиж|ненадминат|вдъхнов|динамичн|световна\s+класа)\w*\b",
        r"\b(?:една|две|три|четири)\s+(?:трет[аи]|четвърт[аи]|пет[аи])\b",
        r"\b(?:не\s+е\s+наличн|не\s+са\s+наличн|няма\s+данни|не\s+са\s+посочен)\w*\b",
    ),
    "en": (
        r"\b(?:does\s+not|doesn't|do\s+not|don't|is\s+not|isn't|are\s+not|aren't)\s+(?:offer|provide|have|available|specified|listed|given)\b",
        r"\b(?:no\s+(?:information|details|data)|lacks?|without)\b",
        r"\b(?:currently|today|this\s+year|recently|at\s+present)\b",
        r"\b(?:parents?|families)\s+(?:praise|say|report|describe|commend)\b",
        r"\b(?:best|leading|exceptional|prestigious|unrivalled|unparalleled|world-class|top-tier|outstanding|dynamic|inspir(?:e|es|ed|ing))\b",
        r"\b(?:one|two|three|four)-(?:third|thirds|quarter|quarters|fifth|fifths)\b",
    ),
}


def _parse_school_summary(result: Any) -> SchoolSummaryStrict:
    output = getattr(result, "output", None)
    if isinstance(output, SchoolSummaryStrict):
        return output
    if isinstance(output, dict):
        return SchoolSummaryStrict.model_validate(output)

    data = getattr(result, "data", None)
    if isinstance(data, SchoolSummaryStrict):
        return data
    if isinstance(data, dict):
        return SchoolSummaryStrict.model_validate(data)

    raise ValueError("Summarization result missing structured output")


def _summary_text_issue(text: str, lang: str, field_name: str = "long") -> str | None:
    """Return why a summary text fails validation, or None if it passes."""
    lowered = text.casefold()
    if "no information available" in lowered or "няма информация" in lowered:
        return f"{lang} summary must omit missing-data filler"
    if any(
        re.search(pattern, lowered, flags=re.IGNORECASE)
        for pattern in _SEMANTIC_BLOCK_PATTERNS[lang]
    ):
        return f"{lang} {field_name} summary contains an unsafe semantic claim"
    if re.search(r"\b\d{1,2}:\d{2}\b", text):
        return f"{lang} summary must omit exact time ranges"
    if re.search(r"\b20\d{2}/20\d{2}\b", text):
        return f"{lang} summary must omit academic-year ranges"
    if re.search(r"\b\d+\s+(?:учени(?:ка|ци)|students?|тома|volumes|classrooms?|rooms?)\b", lowered):
        return f"{lang} summary must omit exact counts"
    if "%" in text or re.search(r"\b\d+\s*(?:percent|per\s+cent|процента?)\b", lowered):
        return f"{lang} summary must omit unstable proportions"
    return None


def _validate_summary_payload(summary: SchoolSummaryStrict) -> SchoolSummaryStrict:
    cleaned_i18n: dict[str, dict[str, str]] = {}
    raw_summary_i18n = summary.summary_i18n
    if isinstance(raw_summary_i18n, str):
        raw_i18n = json.loads(raw_summary_i18n)
    elif hasattr(raw_summary_i18n, "model_dump"):
        raw_i18n = raw_summary_i18n.model_dump()
    else:
        raw_i18n = dict(raw_summary_i18n)
    for lang in ("bg", "en"):
        raw_value = raw_i18n.get(lang)
        if raw_value is None:
            raise ModelRetry(f"summary_i18n must include {lang}")
        short = " ".join((raw_value.get("short") or "").split()).strip()
        long = " ".join((raw_value.get("long") or "").split()).strip()
        if not short or not long:
            raise ModelRetry(f"{lang} summary must include non-empty short and long fields")
        for field_name, text in (("short", short), ("long", long)):
            issue = _summary_text_issue(text, lang, field_name)
            if issue:
                raise ModelRetry(issue)
        cleaned_i18n[lang] = {"short": short, "long": long}
    return SchoolSummaryStrict.model_validate({"summary_i18n": cleaned_i18n})


def validate_summary_i18n(summary_i18n: Any) -> dict[str, dict[str, str]]:
    """Validate a summary at the persistence boundary."""
    validated = _validate_summary_payload(
        SchoolSummaryStrict.model_validate({"summary_i18n": summary_i18n})
    )
    return validated.summary_i18n.model_dump()


async def generate_school_summary(summary_input: SummaryInput, *, school_id: int | None = None) -> dict[str, Any]:
    """Generate BG + EN summaries from structured facts."""
    settings = get_settings()
    system_prompt = (
        "You write bilingual school directory summaries for parents comparing schools. "
        "Use only the provided structured facts. "
        "Tone must be neutral, factual, and concise. "
        "Do not use rankings, superlatives, or promotional language. "
        "Do not invent or infer facts beyond the structured input. "
        "Do not include exact fees, exact exam scores, phone numbers, or unstable operational details. "
        "If the structured input includes concise narrative themes such as teaching approach, student experience, "
        "community signals, differentiators, or canonical tags, use them to explain the school's educational model in plain factual prose. "
        "Treat school_type and education_level as authoritative labels and do not reinterpret them. "
        "You may include a school number only when it is part of the official school name. "
        "Otherwise avoid exact numbers, counts, years, academic-year ranges, opening hours, room counts, class sizes, library volumes, and similar unstable details. "
        "If pricing, admissions, operations, or exam data exists, refer to its availability or broad category rather than quoting numbers. "
        "Do not mention that information is missing or unavailable; omit absent fact groups entirely. "
        "Return Bulgarian and English summaries in exactly this JSON shape: "
        "{\"summary_i18n\":{\"bg\":{\"short\":\"...\",\"long\":\"...\"},\"en\":{\"short\":\"...\",\"long\":\"...\"}}}. "
        "Each language must include a short one-sentence summary and a long summary of 2-4 sentences."
    )
    user_prompt = (
        "Structured school facts (JSON):\n"
        f"{json.dumps(summary_input.model_dump(mode='json', exclude_none=True), ensure_ascii=False, sort_keys=True)}"
    )
    agent = create_agent(
        tier=SUMMARY_MODEL_TIER,
        system_prompt=system_prompt,
        result_type=SchoolSummaryStrict,
        retries=1,
        output_retries=1,
    )

    @agent.output_validator
    def _summary_output_validator(data: Any) -> Any:
        if isinstance(data, SchoolSummaryStrict):
            return _validate_summary_payload(data)
        parsed = SchoolSummaryStrict.model_validate(data)
        return _validate_summary_payload(parsed)

    try:
        result = await asyncio.wait_for(
            execute_billable_request(
                lambda: agent.run(user_prompt),
                model=get_model(SUMMARY_MODEL_TIER),
                school_id=school_id,
                stage="summarize",
            ),
            timeout=float(settings.summarization_llm_timeout_seconds),
        )
        parsed = _parse_school_summary(result)
        validated = _validate_summary_payload(parsed)
        summary_i18n = validated.summary_i18n.model_dump()
        fallback_i18n = _build_fallback_summary(summary_input)
        for lang in ("bg", "en"):
            summary_i18n[lang]["short"] = _choose_short_summary(
                summary_i18n[lang]["short"],
                fallback_i18n[lang]["short"],
                summary_input,
                lang,
            )
        from app.scrapers import extractor_helpers as helpers

        input_tokens, output_tokens = helpers._get_usage(result)
        token_cost_usd = calculate_cost(SUMMARY_MODEL_TIER, input_tokens, output_tokens)
        return {
            "summary_i18n": summary_i18n,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "token_cost_usd": round(float(token_cost_usd), 6),
            "model_tier": SUMMARY_MODEL_TIER,
            "model": get_model(SUMMARY_MODEL_TIER),
            "generation_mode": "llm",
        }
    except Exception as exc:
        summary_i18n = _build_fallback_summary(summary_input)
        try:
            summary_i18n = validate_summary_i18n(summary_i18n)
        except Exception as fallback_exc:
            raise ValueError(
                f"Generated summary and deterministic fallback failed validation: {fallback_exc}"
            ) from exc
        return {
            "summary_i18n": summary_i18n,
            "input_tokens": 0,
            "output_tokens": 0,
            "token_cost_usd": 0.0,
            "model_tier": "deterministic",
            "model": "template_v1",
            "generation_mode": "fallback",
            "fallback_reason": str(exc),
        }


def _human_city(city: str | None, lang: str) -> str | None:
    if not city:
        return None
    mapping = {
        "sofia": {"bg": "София", "en": "Sofia"},
    }
    return mapping.get(city.strip().lower(), {}).get(lang) or city


def _clean_name(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = _LEGAL_SUFFIX_RE.sub("", text).strip()
    if text.count('"') % 2 == 1:
        text = text.replace('"', "")
    if text.count("„") != text.count("“"):
        text = text.replace("„", "").replace("“", "")
    return text.strip()


def _format_school_kind(summary_input: SummaryInput, lang: str) -> str:
    school_type = (summary_input.identity.school_type or "").strip().lower()
    education_level = (summary_input.identity.education_level or "").strip().lower()
    bg_map = {
        ("private", "kindergarten"): "частна детска градина",
        ("private", "primary"): "частно училище",
        ("private", "lower_secondary"): "частно училище",
        ("private", "upper_secondary"): "частна гимназия",
        ("state", "kindergarten"): "държавна детска градина",
        ("state", "primary"): "държавно училище",
        ("state", "lower_secondary"): "държавно училище",
        ("state", "upper_secondary"): "държавно училище",
        ("international", "kindergarten"): "международна детска градина",
        ("international", "primary"): "международно училище",
        ("international", "lower_secondary"): "международно училище",
        ("international", "upper_secondary"): "международно училище",
    }
    en_map = {
        ("private", "kindergarten"): "private kindergarten",
        ("private", "primary"): "private school",
        ("private", "lower_secondary"): "private school",
        ("private", "upper_secondary"): "private high school",
        ("state", "kindergarten"): "state kindergarten",
        ("state", "primary"): "state school",
        ("state", "lower_secondary"): "state school",
        ("state", "upper_secondary"): "state school",
        ("international", "kindergarten"): "international kindergarten",
        ("international", "primary"): "international school",
        ("international", "lower_secondary"): "international school",
        ("international", "upper_secondary"): "international school",
    }
    if lang == "bg":
        return bg_map.get((school_type, education_level), "училище")
    return en_map.get((school_type, education_level), "school")


def _pick_name(summary_input: SummaryInput, lang: str) -> str:
    identity = summary_input.identity
    raw_bg = _clean_name(identity.name_i18n.get("bg") or "")
    raw_en = _clean_name(identity.name_i18n.get("en") or "")
    display_bg = _clean_name(identity.display_name_i18n.get("bg") or "")
    display_en = _clean_name(identity.display_name_i18n.get("en") or "")

    if lang == "bg":
        if display_bg:
            if not _contains_cyrillic(display_bg) and _contains_cyrillic(raw_bg):
                return raw_bg
            if _looks_generic_bg_name(display_bg) and raw_bg:
                return raw_bg
            return display_bg
        return raw_bg or display_en or raw_en or "Училище"

    quoted_bg = _extract_quoted_core(raw_bg) or _extract_quoted_core(display_bg)
    acronym_bg = _extract_bg_acronym(display_bg) or _extract_bg_acronym(raw_bg)
    candidates = [
        display_en,
        _derive_english_name_from_bg(quoted_bg),
        transliterate_bulgarian(acronym_bg) if acronym_bg else "",
        raw_en,
        _derive_english_name_from_bg(raw_bg),
        _derive_english_name_from_bg(display_bg),
    ]
    for candidate in candidates:
        normalized = _normalize_english_name(candidate)
        if normalized:
            return normalized
    return "School"


def _truncate_items(values: list[str], count: int = 3) -> list[str]:
    return [value for value in values if value][:count]


def _join_values(values: list[str], lang: str) -> str:
    values = _truncate_items(values)
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        conj = " и " if lang == "bg" else " and "
        return f"{values[0]}{conj}{values[1]}"
    conj = " и " if lang == "bg" else " and "
    return f"{', '.join(values[:-1])}{conj}{values[-1]}"


def _sentence_case(value: str) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    if not compact:
        return ""
    return compact[0].upper() + compact[1:]


def _contains_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-я]", value or ""))


def _looks_generic_bg_name(value: str) -> bool:
    compact = _clean_name(value)
    if not compact:
        return True
    return bool(_GENERIC_BG_PREFIX_RE.match(compact) or _GENERIC_BG_ABBREVIATION_RE.match(compact))


def _extract_quoted_core(value: str) -> str:
    matches = re.findall(r'[„"“]([^"“”„]+)["”]', value or "")
    for match in reversed(matches):
        candidate = _clean_name(match)
        if candidate and not _looks_generic_bg_name(candidate):
            return candidate
    return ""


def _extract_bg_acronym(value: str) -> str:
    compact = _clean_name(value)
    if not compact or not _looks_generic_bg_name(compact):
        return ""
    tokens = re.findall(r"[A-ZА-Я0-9-]{2,}", compact)
    for token in reversed(tokens):
        if token.upper() in {"ЕООД", "ООД", "АД", "ЕАД"}:
            continue
        if token.isdigit():
            continue
        if token.endswith("-") or len(token) < 3:
            continue
        return token
    return ""


def _derive_english_name_from_bg(value: str) -> str:
    if not value:
        return ""
    return _clean_name(derive_english_name(value) or "")


def _normalize_english_name(value: str) -> str:
    compact = _clean_name(value)
    if not compact or _contains_cyrillic(compact):
        return ""
    if _ABBREVIATED_EN_NAME_RE.match(compact):
        return ""
    return compact


def _indefinite_article(value: str) -> str:
    compact = value.strip().lower()
    if not compact:
        return "a"
    return "an" if compact[0] in {"a", "e", "i", "o", "u"} else "a"


def _english_category_sentence(values: list[str], noun: str, fallback: str) -> str:
    if not values:
        return fallback
    if any(_contains_cyrillic(value) for value in values):
        return fallback
    joined = _join_values(values, "en")
    return f"The available information highlights {joined}."


def _flatten_focus_candidates(summary_input: SummaryInput) -> list[str]:
    offering = summary_input.offering
    candidates: list[str] = []
    identity = summary_input.identity
    candidates.extend(identity.display_name_i18n.values())
    candidates.extend(identity.name_i18n.values())
    if not offering:
        return [re.sub(r"\s+", " ", value).strip() for value in candidates if value and str(value).strip()]
    candidates.extend(offering.canonical_tags)
    candidates.extend(offering.programs)
    candidates.extend(offering.differentiators)
    candidates.extend(offering.teaching_approach)
    if offering.positioning:
        candidates.append(offering.positioning)
    candidates.extend(offering.student_experience)
    return [re.sub(r"\s+", " ", value).strip() for value in candidates if value and str(value).strip()]


def _match_focus_pattern(summary_input: SummaryInput, lang: str) -> str | None:
    normalized = f" {' '.join(value.casefold() for value in _flatten_focus_candidates(summary_input))} "
    if not normalized.strip():
        return None
    for patterns, phrase_by_lang in _FOCUS_PATTERN_PHRASES:
        if any(pattern in normalized for pattern in patterns):
            return phrase_by_lang[lang]
    return None


def _normalize_language_name(value: str, lang: str) -> str:
    base = re.sub(r"\s*\([^)]*\)", "", value or "").strip()
    if not base:
        return ""
    normalized = re.sub(r"\s+", " ", base)
    if lang == "bg":
        translated = _LANGUAGE_LABELS_BG.get(normalized.casefold())
        if translated:
            return translated
        if _contains_cyrillic(normalized):
            return normalized
        return transliterate_bulgarian(normalized) or normalized
    translated_en = _LANGUAGE_LABELS_EN.get(normalized.casefold())
    if translated_en:
        return translated_en
    if _contains_cyrillic(normalized):
        return derive_english_name(normalized) or transliterate_bulgarian(normalized) or normalized
    return normalized


def _language_focus_phrase(summary_input: SummaryInput, lang: str) -> str | None:
    offering = summary_input.offering
    if not offering or not offering.languages:
        return None
    seen: set[str] = set()
    languages: list[str] = []
    for raw_value in offering.languages:
        label = _normalize_language_name(raw_value, lang)
        if not label:
            continue
        key = label.casefold()
        if key in seen or key in {"bulgarian", "български"}:
            continue
        if _summary_text_issue(label, lang) is not None:
            continue
        seen.add(key)
        languages.append(label)
    if not languages:
        return None
    joined = _join_values(languages[:2], lang)
    if lang == "bg":
        return f"с фокус върху {joined} език"
    return f"with {joined} language focus"


def _generic_focus_phrase(summary_input: SummaryInput, lang: str) -> str | None:
    offering = summary_input.offering
    if not offering or lang != "bg":
        return None
    generic_values = offering.programs + offering.teaching_approach + offering.differentiators
    for value in generic_values:
        text = re.sub(r"\s+", " ", value or "").strip().rstrip(".")
        if not text:
            continue
        if len(text) > 60:
            continue
        lowered = text.casefold()
        if lowered in _GENERIC_FOCUS_BLOCKED_VALUES:
            continue
        if any(token in lowered for token in _GENERIC_FOCUS_BLOCKED_TOKENS):
            continue
        if re.search(r"\b\d{2,}\b", lowered):
            continue
        if any(token in lowered for token in ("school", "училище", "kindergarten", "детска градина")):
            continue
        if not _contains_cyrillic(text):
            continue
        if _summary_text_issue(text, "bg") is not None:
            continue
        return f"с акцент върху {text}"
    return None


def _short_focus_phrase(summary_input: SummaryInput, lang: str) -> str | None:
    return _match_focus_pattern(summary_input, lang) or _language_focus_phrase(summary_input, lang) or _generic_focus_phrase(summary_input, lang)


def _short_summary_has_meaningful_signal(value: str, lang: str) -> bool:
    compact = re.sub(r"\s+", " ", value or "").strip()
    if not compact:
        return False
    lowered = compact.casefold()
    if any(token in lowered for token in _SHORT_PROMOTIONAL_TOKENS):
        return False
    return any(token in lowered for token in _SHORT_SIGNAL_TOKENS[lang])


def _choose_short_summary(
    llm_short: str,
    fallback_short: str,
    summary_input: SummaryInput,
    lang: str,
) -> str:
    if _short_focus_phrase(summary_input, lang):
        return fallback_short
    return llm_short if _short_summary_has_meaningful_signal(llm_short, lang) else fallback_short


def _build_fallback_summary(summary_input: SummaryInput) -> dict[str, dict[str, str]]:
    return {
        "bg": _build_fallback_summary_for_lang(summary_input, "bg"),
        "en": _build_fallback_summary_for_lang(summary_input, "en"),
    }


def _build_fallback_summary_for_lang(summary_input: SummaryInput, lang: str) -> dict[str, str]:
    name = _pick_name(summary_input, lang)
    school_kind = _format_school_kind(summary_input, lang)
    city = summary_input.identity.locality_i18n.get(lang) or _human_city(
        summary_input.identity.city,
        lang,
    )
    focus_phrase = _short_focus_phrase(summary_input, lang)

    if lang == "bg":
        short = f"{name} е {school_kind}"
        if city:
            short += f" в {city}"
        if focus_phrase:
            short += f" {focus_phrase}"
        short += "."
    else:
        short = f"{name} is {_indefinite_article(school_kind)} {school_kind}"
        if city:
            short += f" in {city}"
        if focus_phrase:
            short += f" {focus_phrase}"
        short += "."

    long_parts: list[str] = [short.rstrip(".") + "."]

    offering = summary_input.offering
    if offering:
        if offering.positioning:
            long_parts.append(_sentence_case(offering.positioning.rstrip(".")) + ".")

        narrative_values = (
            offering.teaching_approach
            or offering.differentiators
            or offering.student_experience
            or offering.community_signals
        )
        if narrative_values:
            joined = _join_values(narrative_values, lang)
            if lang == "bg":
                long_parts.append(f"Наличната информация описва подхода и средата чрез {joined}.")
            else:
                if any(_contains_cyrillic(value) for value in narrative_values):
                    long_parts.append(
                        "The available information highlights the school's learning model and community approach."
                    )
                else:
                    long_parts.append(
                        f"The available information highlights the school's learning model through {joined}."
                    )

        feature_values = offering.programs or offering.facilities or offering.extracurricular or offering.languages
        if feature_values:
            if lang == "bg":
                joined = _join_values(feature_values, lang)
                long_parts.append(f"В наличната информация се открояват {joined}.")
            else:
                long_parts.append(
                    _english_category_sentence(
                        feature_values,
                        noun="programs",
                        fallback="The available information highlights facilities, activities, or learning offerings.",
                    )
                )

    pricing = summary_input.pricing
    academic = summary_input.academic
    operations = summary_input.operations

    if pricing and pricing.has_pricing:
        joined = _join_values(pricing.fee_categories, lang)
        if lang == "bg":
            long_parts.append(
                "Налична е информация за таксите"
                + (f", включително {joined}" if joined else "")
                + "."
            )
        else:
            long_parts.append(
                "Pricing information is available"
                + (f", including {joined}" if joined else "")
                + "."
            )
    elif academic and (academic.exam_result_types or academic.has_admission_thresholds):
        if lang == "bg":
            long_parts.append("Налична е информация за изпитните резултати или приема.")
        else:
            long_parts.append("Exam or admissions information is available.")

    if operations:
        ops_values = operations.support_services or operations.meals or operations.day_options or operations.safety_features
        if ops_values:
            if lang == "bg":
                joined = _join_values(ops_values, lang)
                long_parts.append(f"Сайтът съдържа и данни за организацията на деня и услугите, например {joined}.")
            else:
                if any(_contains_cyrillic(value) for value in ops_values):
                    long_parts.append("The site also includes day-to-day and support-service information.")
                else:
                    joined = _join_values(ops_values, lang)
                    long_parts.append(f"The site also includes day-to-day and service information such as {joined}.")
        elif lang == "bg":
            long_parts.append("Сайтът съдържа и информация за организацията на деня и условията за прием.")
        else:
            long_parts.append("The site also includes information about day-to-day operations and admissions.")

    # Optional sentences quote extracted fragments verbatim; drop any the validator would
    # reject so the fallback itself stays publishable.
    long_parts = long_parts[:1] + [
        part for part in long_parts[1:] if part and _summary_text_issue(part, lang) is None
    ]
    long = " ".join(_sentence_case(part) for part in long_parts[:4] if part).strip()
    return {"short": short.strip(), "long": long.strip()}


__all__ = [
    "SummaryAcademic",
    "SummaryIdentity",
    "SummaryInput",
    "SummaryOffering",
    "SummaryOperations",
    "SummaryPricing",
    "generate_school_summary",
    "validate_summary_i18n",
]
