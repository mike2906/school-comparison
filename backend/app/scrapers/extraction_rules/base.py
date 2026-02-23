"""Default extraction rules for stage 5 website extraction."""

from __future__ import annotations

import re

from app.models.pricing import PriceCategory, PricePeriod

_CATEGORY_ALIASES: dict[str, PriceCategory] = {
    "tuition": PriceCategory.TUITION,
    "обучение": PriceCategory.TUITION,
    "таксаобучение": PriceCategory.TUITION,
    "schoolfee": PriceCategory.TUITION,
    "food": PriceCategory.FOOD,
    "храна": PriceCategory.FOOD,
    "transport": PriceCategory.TRANSPORT,
    "транспорт": PriceCategory.TRANSPORT,
    "activities": PriceCategory.ACTIVITIES,
    "activity": PriceCategory.ACTIVITIES,
    "дейности": PriceCategory.ACTIVITIES,
    "registration": PriceCategory.REGISTRATION,
    "enrollment": PriceCategory.REGISTRATION,
    "admission": PriceCategory.REGISTRATION,
    "materials": PriceCategory.MATERIALS,
    "extendedday": PriceCategory.EXTENDED_DAY,
    "extended": PriceCategory.EXTENDED_DAY,
    "uniforms": PriceCategory.UNIFORMS,
    "extracurricular": PriceCategory.EXTRACURRICULAR,
    "camp": PriceCategory.CAMP,
}

_PERIOD_ALIASES: dict[str, PricePeriod] = {
    "monthly": PricePeriod.MONTHLY,
    "month": PricePeriod.MONTHLY,
    "месечно": PricePeriod.MONTHLY,
    "месец": PricePeriod.MONTHLY,
    "yearly": PricePeriod.YEARLY,
    "annual": PricePeriod.YEARLY,
    "годишно": PricePeriod.YEARLY,
    "year": PricePeriod.YEARLY,
    "one_time": PricePeriod.ONE_TIME,
    "onetime": PricePeriod.ONE_TIME,
    "single": PricePeriod.ONE_TIME,
    "еднократно": PricePeriod.ONE_TIME,
    "quarter": PricePeriod.QUARTER,
    "quarterly": PricePeriod.QUARTER,
    "term": PricePeriod.TERM,
    "semester": PricePeriod.SEMESTER,
    "семестър": PricePeriod.SEMESTER,
}

_PROVIDER_ERROR_MARKERS = (
    "not a valid model id",
    "openrouter",
    "provider",
    "authentication",
    "failed to authenticate",
    "chat completions endpoint",
    "status_code",
)

_OUTPUT_VALIDATION_ERROR_MARKERS = (
    "output validation",
    "result validation",
    "structured parse returned no data",
    "validation error",
    "validation errors",
    "invalid json",
    "json decode",
    "json parse",
    "failed to validate",
    "failed validation",
    "exceeded maximum retries",
    "retry attempts exhausted",
)

_GENERAL_INFO_LIST_MAX_ITEMS = 20
_GENERAL_INFO_ITEM_MAX_LEN = 100
_GENERAL_INFO_CLASS_SIZE_MAX_LEN = 48
_GENERAL_INFO_JUNK_VALUES = {
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "неизвестно",
    "-",
    "--",
    ".",
}

_GENERAL_INFO_TEXT_KEYS = (
    "name",
    "title",
    "label",
    "description",
    "language",
    "program",
    "program_name",
    "facility",
    "activity",
    "accreditation",
    "value",
    "text",
)

_GENERAL_INFO_SECTION_CONFIG: dict[str, dict[str, tuple[str, ...] | list[str]]] = {
    "languages": {
        "preferred_categories": ["about", "contact"],
        "include_tokens": (
            "language",
            "languages",
            "език",
            "езици",
            "чужд",
            "двуезич",
            "bilingual",
            "english",
            "английски",
            "немски",
            "deutsch",
            "french",
            "français",
        ),
    },
    "facilities": {
        "preferred_categories": ["facilities", "about", "contact"],
        "include_tokens": (
            "facility",
            "facilities",
            "campus",
            "base",
            "classroom",
            "laboratory",
            "library",
            "sport",
            "pool",
            "material",
            "база",
            "сграда",
            "класна",
            "лаборатория",
            "библиотека",
            "двор",
            "физкултур",
            "плувен",
        ),
    },
    "programs": {
        "preferred_categories": ["programs", "about", "admission", "contact"],
        "include_tokens": (
            "program",
            "curriculum",
            "method",
            "montessori",
            "waldorf",
            "ib",
            "cambridge",
            "stem",
            "robotics",
            "club",
            "programme",
            "програма",
            "обучение",
            "клуб",
            "извънклас",
            "занимания",
            "роботика",
        ),
    },
    "metadata": {
        "preferred_categories": ["about", "contact"],
        "include_tokens": (
            "founded",
            "established",
            "since",
            "year",
            "class",
            "students",
            "accreditation",
            "accredited",
            "founding",
            "основан",
            "създаден",
            "учреден",
            "година",
            "клас",
            "ученици",
            "акредитац",
            "лиценз",
        ),
    },
    "admission": {
        "preferred_categories": ["admission", "about", "contact"],
        "include_tokens": (
            "admission",
            "enrollment",
            "apply",
            "application",
            "deadline",
            "requirements",
            "документи",
            "прием",
            "записване",
            "срок",
            "кандидат",
            "изпит",
            "интервю",
            "свободни места",
        ),
    },
    "operations": {
        "preferred_categories": ["about", "contact", "admission"],
        "include_tokens": (
            "working hours",
            "opening hours",
            "daily schedule",
            "full day",
            "half day",
            "menu",
            "meals",
            "transport",
            "uniform",
            "работно време",
            "дневен режим",
            "целоднев",
            "полуднев",
            "меню",
            "хранене",
            "транспорт",
            "униформ",
        ),
    },
    "services": {
        "preferred_categories": ["about", "contact", "admission"],
        "include_tokens": (
            "psychologist",
            "speech therapist",
            "nurse",
            "medical",
            "security",
            "cctv",
            "психолог",
            "логопед",
            "медицин",
            "сестра",
            "охран",
            "видеонаблюдение",
            "сигурност",
        ),
    },
    "pricing_terms": {
        "preferred_categories": ["pricing", "admission", "contact"],
        "include_tokens": (
            "discount",
            "installment",
            "deposit",
            "application fee",
            "registration fee",
            "includes",
            "not included",
            "отстъп",
            "вноск",
            "депозит",
            "такса кандидат",
            "такса запис",
            "включен",
            "не включен",
        ),
    },
}

_DETERMINISTIC_LANGUAGE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Bulgarian", ("български", "bulgarian")),
    ("English", ("английски", "english")),
    ("German", ("немски", "германски", "deutsch", "german")),
    ("French", ("френски", "français", "french")),
    ("Spanish", ("испански", "spanish", "español")),
    ("Italian", ("италиански", "italian", "italiano")),
    ("Russian", ("руски", "russian")),
    ("Turkish", ("турски", "turkish")),
    ("Greek", ("гръцки", "greek")),
    ("Chinese", ("китайски", "chinese", "mandarin")),
    ("Japanese", ("японски", "japanese")),
    ("Hebrew", ("иврит", "hebrew")),
    ("Arabic", ("арабски", "arabic")),
)

_LANGUAGE_CONTEXT_MARKERS = (
    "език",
    "езици",
    "чужд",
    "двуезич",
    "language",
    "languages",
    "bilingual",
    "teaching",
    "instruction",
    "обучение",
)

_ACCREDITATION_CONTEXT_MARKERS = (
    "accredit",
    "authorized",
    "licensed",
    "certified",
    "affiliated",
    "акредитац",
    "акредит",
    "оторизиран",
    "лиценз",
    "сертифи",
    "удостоверен",
)

_ACCREDITATION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("International Baccalaureate (IB)", ("international baccalaureate", "ib diploma", "ib programme", "ib program")),
    ("Cambridge International", ("cambridge international", "cambridge assessment", "cambridge english")),
    ("Council of International Schools (CIS)", ("council of international schools", "cis accreditation", "cis accredited")),
    ("COBIS", ("cobis",)),
    ("Pearson Edexcel", ("pearson", "edexcel")),
    ("МОН лиценз", ("мон", "министерство на образованието", "министерството на образованието")),
)

_ADMISSION_DEADLINE_PATTERNS = (
    r"план-?прием",
    r"календар прием",
    r"прием[^.\n]{0,60}(?:20\d{2}|учебна)",
    r"краен срок",
    r"\bdeadline\b",
    r"\badmission\b",
)
_ADMISSION_DOCUMENT_PATTERNS = (
    r"необходими документи",
    r"документи за",
    r"заявление по образец",
    r"акт за раждане",
    r"удостоверение",
    r"имунизацион",
    r"медицинск[аи]",
    r"\bdocuments required\b",
)
_ADMISSION_STEPS_PATTERNS = (
    r"заявление",
    r"онлайн форма",
    r"кандидатств",
    r"\bapplication form\b",
    r"\benrollment\b",
)
_ADMISSION_ENTRANCE_PATTERNS = (
    r"входн(?:и|o) изпит",
    r"приемен изпит",
    r"интервю",
    r"\binterview\b",
    r"\bentrance test\b",
)
_ADMISSION_SPOTS_PATTERNS = (
    r"свободни места",
    r"незаети места",
    r"\bavailable slots\b",
    r"\bcapacity\b",
)

_OPERATIONS_WORKING_HOURS_PATTERNS = (
    r"работно време",
    r"working hours",
    r"opening hours",
)
_OPERATIONS_DAY_OPTIONS_PATTERNS = (
    r"целоднев",
    r"полуднев",
    r"extended day",
    r"after[- ]school",
)
_OPERATIONS_DAILY_SCHEDULE_PATTERNS = (
    r"дневен режим",
    r"дневна програма",
    r"daily schedule",
)
_OPERATIONS_MEALS_PATTERNS = (
    r"хранене",
    r"меню",
    r"седмично меню",
    r"закуска",
    r"обяд",
    r"следобедна закуска",
    r"топъл обяд",
    r"\bfood\b",
    r"\bmeals\b",
    r"\blunch\b",
    r"\bbreakfast\b",
)
_OPERATIONS_TRANSPORT_PATTERNS = (
    r"училищен автобус",
    r"такса транспорт",
    r"\btransport\b",
    r"\bschool bus\b",
)
_OPERATIONS_UNIFORMS_PATTERNS = (
    r"униформ",
    r"\buniform\b",
)

_SERVICES_SUPPORT_PATTERNS = (
    r"\bпсихолог(?!ия)\w*\b",
    r"логопед",
    r"медицинск[аи]",
    r"медицинска сестра",
    r"педагогически съветник",
    r"социален педагог",
    r"ресурсен учител",
    r"\bnurse\b",
    r"\bcounsel(?:or|ling)\b",
    r"\bresource teacher\b",
    r"\bspecial educator\b",
    r"speech therapist",
)
_SERVICES_SUPPORT_EXCLUDE_PATTERNS = (
    r"наръчник на родителя",
    r"лично за мама",
    r"вкусно с мама",
    r"учим и играем заедно",
    r"здраве и психология",
)
_SERVICES_SAFETY_PATTERNS = (
    r"охран",
    r"видеонаблюдение",
    r"контрол на достъп",
    r"\bsecurity\b",
    r"\bcctv\b",
)

_PRICING_TERMS_DISCOUNT_PATTERNS = (
    r"отстъп",
    r"\bdiscount\b",
    r"второ дете",
    r"\bsibling\b",
)
_PRICING_TERMS_INSTALLMENTS_PATTERNS = (
    r"вноск",
    r"\binstallment\b",
)
_PRICING_TERMS_INCLUDED_PATTERNS = (
    r"включен[ао]?",
    r"включва",
    r"\bincludes?\b",
)
_PRICING_TERMS_EXCLUDED_PATTERNS = (
    r"не е включ",
    r"\bnot included\b",
    r"\bexcludes?\b",
)
_PRICING_TERMS_DEPOSIT_PATTERNS = (
    r"депозит",
    r"\bdeposit\b",
)
_PRICING_TERMS_APPLICATION_FEE_PATTERNS = (
    r"такса за кандидат",
    r"\bapplication fee\b",
    r"\bentry fee\b",
)
_PRICING_TERMS_REGISTRATION_FEE_PATTERNS = (
    r"такса запис",
    r"\bregistration fee\b",
    r"\benrollment fee\b",
)

_SECTION_NAV_NOISE_PATTERNS = (
    r"\bначало\b",
    r"\bhome\b",
    r"\bновини\b",
    r"\bконтакти\b",
    r"\bкарта на сайта\b",
    r"\bелектронен дневник\b",
    r"\bfor parents\b",
)
_SECTION_REGULATORY_NOISE_PATTERNS = (
    r"медицински стандарти",
    r"центрове за спешна медицинска помощ",
    r"\bнаредба\b",
    r"\bревматология\b",
)
_MENU_BREADCRUMB_NOISE_PATTERNS = (
    r"за родителите.*електронен дневник",
    r"^\s*[-#*]+\s*(за родителите|начало|новини|контакти)\b",
)
_SECTION_DEFAULT_EXCLUDE_PATTERNS = (
    *_SECTION_NAV_NOISE_PATTERNS,
    *_SECTION_REGULATORY_NOISE_PATTERNS,
    *_MENU_BREADCRUMB_NOISE_PATTERNS,
)

_ADMISSION_DEADLINE_REQUIRE_PATTERNS = (
    r"\b(20\d{2}|19\d{2})\b",
    r"\b\d{1,2}[./-]\d{1,2}[./-](?:20\d{2}|19\d{2})\b",
    r"краен срок",
    r"план-?прием",
    r"календар прием",
    r"admission",
)
_ADMISSION_REQUIRED_DOCUMENTS_REQUIRE_PATTERNS = (
    r"документ",
    r"заявлен",
    r"удостовер",
    r"декларац",
    r"свидетелств",
    r"имунизац",
    r"акт за раждане",
    r"заявление по образец",
    r"копие от",
    r"documents required",
)
_ADMISSION_APPLICATION_STEPS_REQUIRE_PATTERNS = (
    r"заявлен",
    r"кандидатств",
    r"форма",
    r"form",
    r"submit",
)
_ADMISSION_AVAILABLE_SPOTS_REQUIRE_PATTERNS = (
    r"свободни места",
    r"незаети места",
    r"available slots",
    r"capacity",
)

_OPERATIONS_DAILY_SCHEDULE_REQUIRE_PATTERNS = (
    r"дневен режим",
    r"дневна програма",
    r"daily schedule",
)
_OPERATIONS_MEALS_REQUIRE_PATTERNS = (
    r"хранене",
    r"меню",
    r"седмично меню",
    r"закуска",
    r"обяд",
    r"следобедна закуска",
    r"топъл обяд",
    r"\bfood\b",
    r"\bmeals\b",
    r"\blunch\b",
    r"\bbreakfast\b",
)

_SERVICES_SUPPORT_REQUIRE_PATTERNS = (
    r"\bпсихолог(?!ия)\w*\b",
    r"логопед",
    r"медицинска сестра",
    r"медицински кабинет",
    r"педагогически съветник",
    r"социален педагог",
    r"ресурсен учител",
    r"\bnurse\b",
    r"\bcounsel(?:or|ling)\b",
    r"\bresource teacher\b",
    r"\bspecial educator\b",
    r"speech therapist",
)
_SERVICES_SAFETY_REQUIRE_PATTERNS = (
    r"охран",
    r"видеонаблюдение",
    r"контрол на достъп",
    r"\bsecurity\b",
    r"\bcctv\b",
)

_PRICING_TERMS_DISCOUNTS_REQUIRE_PATTERNS = (
    r"отстъп",
    r"\bdiscount\b",
    r"второ дете",
    r"\bsibling\b",
)
_PRICING_TERMS_INSTALLMENTS_REQUIRE_PATTERNS = (
    r"вноск",
    r"\binstallment\b",
)
_PRICING_TERMS_INCLUDED_REQUIRE_PATTERNS = (
    r"включва",
    r"включен",
    r"\bincludes?\b",
)
_PRICING_TERMS_EXCLUDED_REQUIRE_PATTERNS = (
    r"не е включ",
    r"\bnot included\b",
    r"\bexcludes?\b",
)
_PRICING_TERMS_DEPOSIT_REQUIRE_PATTERNS = (
    r"депозит",
    r"\bdeposit\b",
)
_PRICING_TERMS_APPLICATION_FEE_REQUIRE_PATTERNS = (
    r"такса за кандидат",
    r"\bapplication fee\b",
    r"\bentry fee\b",
)
_PRICING_TERMS_REGISTRATION_FEE_REQUIRE_PATTERNS = (
    r"такса запис",
    r"\bregistration fee\b",
    r"\benrollment fee\b",
)
_ADMISSION_REQUIRED_DOCUMENTS_HEADING_MARKERS = (
    "необходими документи",
    "прием необходими документи",
    "документи за прием",
    "documents required",
    "required documents",
)
_OPERATIONS_MEALS_HEADING_MARKERS = (
    "меню",
    "седмично меню",
    "месечно меню",
    "weekly menu",
    "monthly menu",
    "menu",
)
_PRICING_NEGATED_INCLUDE_PATTERNS = (
    *_PRICING_TERMS_EXCLUDED_PATTERNS,
    *_PRICING_TERMS_EXCLUDED_REQUIRE_PATTERNS,
    r"\bне\b[^.\n]{0,30}\bвключ",
)

_WORKING_HOURS_TIME_PATTERN = re.compile(
    r"(?:\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b\s*[-–]\s*\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b|\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b)"
)

# Bulgarian phone: 0XXXXXXXXX (mobile), 02-XXXXXXX (Sofia landline), 0800... (toll-free)
# Also handles +359XXXXXXXXX, 00359XXXXXXXXX international formats
_PHONE_PATTERN = re.compile(
    r"(?:(?:\+359|00359|0)(?:\s*[-./]?\s*))"  # prefix: +359, 00359, or 0
    r"(?:8[7-9]\d|2|[3-9]\d)"                  # area/mobile prefix
    r"(?:\s*[-./]?\s*\d){5,8}",               # remaining digits with optional separators
    re.IGNORECASE,
)
_EMAIL_PATTERN = re.compile(
    r"[a-z0-9._%+\-]+\s*(?:\[\s*a\s*t\s*\]|@)\s*[a-z0-9.\-]+\.[a-z]{2,}",
    re.IGNORECASE,
)
_CONTACT_NOISE_TOKENS = frozenset({
    "example", "yourname", "email@", "@domain", "placeholder", "username",
    "noreply", "no-reply", "donotreply", "info@info", "test@test",
})

__all__ = [
    "_CATEGORY_ALIASES",
    "_PERIOD_ALIASES",
    "_PROVIDER_ERROR_MARKERS",
    "_OUTPUT_VALIDATION_ERROR_MARKERS",
    "_GENERAL_INFO_LIST_MAX_ITEMS",
    "_GENERAL_INFO_ITEM_MAX_LEN",
    "_GENERAL_INFO_CLASS_SIZE_MAX_LEN",
    "_GENERAL_INFO_JUNK_VALUES",
    "_GENERAL_INFO_TEXT_KEYS",
    "_GENERAL_INFO_SECTION_CONFIG",
    "_DETERMINISTIC_LANGUAGE_PATTERNS",
    "_LANGUAGE_CONTEXT_MARKERS",
    "_ACCREDITATION_CONTEXT_MARKERS",
    "_ACCREDITATION_KEYWORDS",
    "_ADMISSION_DEADLINE_PATTERNS",
    "_ADMISSION_DOCUMENT_PATTERNS",
    "_ADMISSION_STEPS_PATTERNS",
    "_ADMISSION_ENTRANCE_PATTERNS",
    "_ADMISSION_SPOTS_PATTERNS",
    "_OPERATIONS_WORKING_HOURS_PATTERNS",
    "_OPERATIONS_DAY_OPTIONS_PATTERNS",
    "_OPERATIONS_DAILY_SCHEDULE_PATTERNS",
    "_OPERATIONS_MEALS_PATTERNS",
    "_OPERATIONS_TRANSPORT_PATTERNS",
    "_OPERATIONS_UNIFORMS_PATTERNS",
    "_SERVICES_SUPPORT_PATTERNS",
    "_SERVICES_SUPPORT_EXCLUDE_PATTERNS",
    "_SERVICES_SAFETY_PATTERNS",
    "_PRICING_TERMS_DISCOUNT_PATTERNS",
    "_PRICING_TERMS_INSTALLMENTS_PATTERNS",
    "_PRICING_TERMS_INCLUDED_PATTERNS",
    "_PRICING_TERMS_EXCLUDED_PATTERNS",
    "_PRICING_TERMS_DEPOSIT_PATTERNS",
    "_PRICING_TERMS_APPLICATION_FEE_PATTERNS",
    "_PRICING_TERMS_REGISTRATION_FEE_PATTERNS",
    "_SECTION_NAV_NOISE_PATTERNS",
    "_SECTION_REGULATORY_NOISE_PATTERNS",
    "_MENU_BREADCRUMB_NOISE_PATTERNS",
    "_SECTION_DEFAULT_EXCLUDE_PATTERNS",
    "_ADMISSION_DEADLINE_REQUIRE_PATTERNS",
    "_ADMISSION_REQUIRED_DOCUMENTS_REQUIRE_PATTERNS",
    "_ADMISSION_APPLICATION_STEPS_REQUIRE_PATTERNS",
    "_ADMISSION_AVAILABLE_SPOTS_REQUIRE_PATTERNS",
    "_OPERATIONS_DAILY_SCHEDULE_REQUIRE_PATTERNS",
    "_OPERATIONS_MEALS_REQUIRE_PATTERNS",
    "_SERVICES_SUPPORT_REQUIRE_PATTERNS",
    "_SERVICES_SAFETY_REQUIRE_PATTERNS",
    "_PRICING_TERMS_DISCOUNTS_REQUIRE_PATTERNS",
    "_PRICING_TERMS_INSTALLMENTS_REQUIRE_PATTERNS",
    "_PRICING_TERMS_INCLUDED_REQUIRE_PATTERNS",
    "_PRICING_TERMS_EXCLUDED_REQUIRE_PATTERNS",
    "_PRICING_TERMS_DEPOSIT_REQUIRE_PATTERNS",
    "_PRICING_TERMS_APPLICATION_FEE_REQUIRE_PATTERNS",
    "_PRICING_TERMS_REGISTRATION_FEE_REQUIRE_PATTERNS",
    "_ADMISSION_REQUIRED_DOCUMENTS_HEADING_MARKERS",
    "_OPERATIONS_MEALS_HEADING_MARKERS",
    "_PRICING_NEGATED_INCLUDE_PATTERNS",
    "_WORKING_HOURS_TIME_PATTERN",
    "_PHONE_PATTERN",
    "_EMAIL_PATTERN",
    "_CONTACT_NOISE_TOKENS",
]
