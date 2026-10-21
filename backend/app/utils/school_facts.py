"""School facts at the publish boundary: the topics, and which stored facts may be shown.

The school-facts stage stores ``attributes["school_facts"]``; only facts that passed its
checks are stored. This drops anything malformed or from another schema version, so the API
never serialises the stored block itself.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping
from typing import Any, Optional
from urllib.parse import urlparse

from app.utils.academic_year import current_academic_year
from app.utils.display_lists import canonical_language
from app.utils.facility_vocabulary import APPROACH_VOCAB, FACILITY_VOCAB, PROGRAM_VOCAB
from app.utils.website_data import (  # noqa: F401 (SITE_ACCEPTED_STATUSES is imported from here)
    SITE_ACCEPTED_STATUSES,
    attributes_for_publication,
    website_data_withheld_reasons,
)
from app.utils.website_name import PUBLISHED_KEY as WEBSITE_NAME_KEY
from app.utils.website_name import shown_website_name

SCHEMA_VERSION = 1
ATTRIBUTE_KEY = "school_facts"
# Where ``publication_attributes`` puts the facts that may show, for the display and filter
# projections; ``attributes_for_publication`` removes both keys from anything else.
PUBLISHED_KEY = "school_facts_published"
# Set on a read whose reader lists every foreign language the school teaches (2026-10-20).
# Such a read's languages replace the old extraction's unchecked list in the language focus.
LANGUAGES_READ_KEY = "languages_read"

# Each topic has a row on the compare page and a label in the frontend's i18n
# (`schoolFacts.topics.<topic>`). Order is display order.
TOPICS = (
    "curriculum",
    "languages",
    "school_day",
    "class_size",
    "meals",
    "transport",
    "activities",
    "facilities",
    "support",
    "admissions",
)


# A fact can name the advanced-filter tags its quote states; each feeds the filter group
# whose vocabulary has it (``compute_filter_tags``).
TAG_GROUPS = {
    **{tag: "facilities" for tag in FACILITY_VOCAB},
    **{tag: "special_programs" for tag in PROGRAM_VOCAB},
    **{tag: "teaching_approach" for tag in APPROACH_VOCAB},
}
FACT_TAGS = tuple(TAG_GROUPS)
# The language-focus levels the search filter offers (frontend ``advancedFilterOptions``).
LANGUAGE_LEVELS = ("immersion", "bilingual", "enrichment")

# Other labels a fact can carry, summed up per school in ``at_a_glance``.
ADMISSION_TYPES = ("entrance_exam", "interview", "trial_day", "open_entry")
PLACES = ("full", "waiting_list")
CURRICULA = ("bulgarian_state", "ib", "cambridge", "british", "american", "canadian", "french", "german")

# How useful the checker found a fact for choosing a school. ``key`` facts are shown first
# (``MAX_KEY_FACTS`` at most, ``MAX_KEY_PER_TOPIC`` a topic; the rest shown as ``more``);
# ``minor`` facts are not shown but their tags still feed the filters.
RANKS = ("key", "more", "minor")
MAX_KEY_FACTS = 8
MAX_KEY_PER_TOPIC = 2



def _site(url: Any) -> str:
    """Host without www, and path, for telling a moved website from the same one."""
    if not isinstance(url, str):
        return ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return f"{host}{parsed.path.rstrip('/').lower()}" if host else ""


def _languages(value: Any) -> list[dict[str, Optional[str]]]:
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        language = canonical_language(item.get("language"))
        level = item.get("level") if item.get("level") in LANGUAGE_LEVELS else None
        hours = _int_in(item.get("hours_per_week"), 1, 30)
        if language and all(row["language"] != language for row in out):
            out.append({"language": language, "level": level, "hours_per_week": hours})
    return out


def _int_in(value: Any, low: int, high: int) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high else None


def _date(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _bilingual(value: Any, limit: int) -> list[dict[str, str]]:
    """``[{"bg", "en"}]`` names (exam subjects, bus areas), short and deduped."""
    out = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, Mapping):
            continue
        names = {lang: item.get(lang).strip() for lang in ("bg", "en") if isinstance(item.get(lang), str)}
        if len(names) == 2 and all(0 < len(name) <= 60 for name in names.values()) and names not in out:
            out.append(names)
    return out[:limit]


def _labels(fact: Mapping[str, Any]) -> dict[str, Any]:
    """A stored fact's labels, cleaned; anything malformed is dropped."""

    def listed(key: str, allowed: tuple[str, ...]) -> list[str]:
        return [value for value in dict.fromkeys(_as_list(fact.get(key))) if value in allowed]

    return {
        "tags": [tag for tag in dict.fromkeys(_as_list(fact.get("tags"))) if tag in TAG_GROUPS],
        "languages": [] if _on_request(fact) else _languages(fact.get("languages")),
        "max_class_size": None if _seasonal(fact) or _one_part(fact) else _class_size(fact.get("max_class_size")),
        "instruction_languages": [
            name
            for name in dict.fromkeys(canonical_language(v) for v in _as_list(fact.get("instruction_languages")))
            if name
        ],
        "admission": _admission_types(fact),
        "admission_grade": _int_in(fact.get("admission_grade"), 0, 12),
        "admission_grade_to": _grade_to(fact),
        "exam_subjects": _exam_subjects(fact),
        "places": fact.get("places") if fact.get("places") in PLACES else None,
        "highest_grade": _int_in(fact.get("highest_grade"), 1, 12),
        "curricula": listed("curricula", CURRICULA),
        "diploma_recognised": fact.get("diploma_recognised")
        if isinstance(fact.get("diploma_recognised"), bool)
        else None,
        "applications_open": _date(fact.get("applications_open")),
        "applications_close": _date(fact.get("applications_close")),
        "open_days": sorted({d for d in map(_date, _as_list(fact.get("open_days"))) if d})[:6],
        "register_url": _url(fact.get("register_url")),
        "bus_areas": _bilingual(fact.get("bus_areas"), 20),
        "special_needs_support": True if fact.get("special_needs_support") is True else None,
    }


def _grade_to(fact: Mapping[str, Any]) -> Optional[int]:
    """The last grade of a stated range, kept only after a valid first grade."""
    first, last = _int_in(fact.get("admission_grade"), 0, 12), _int_in(fact.get("admission_grade_to"), 0, 12)
    return last if first is not None and last is not None and last > first else None


def _url(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.startswith(("http://", "https://")) and len(value) <= 500:
        return value
    return None


def _as_list(value: Any) -> list[str]:
    """The strings of a stored list; anything else (a nested list, a dict) is dropped."""
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _subject_key(subject: Any) -> str:
    if isinstance(subject, Mapping):
        return str(subject.get("bg") or subject.get("en") or "").casefold().strip()
    return str(subject).casefold().strip()


PLACES_SHOWN_DAYS = 180
OPEN_ONLY_WINDOW_DAYS = 120


def at_a_glance(
    facts: list[Mapping[str, Any]], read_at: Optional[str] = None, today: Optional[datetime.date] = None
) -> dict[str, Any]:
    """The school's labels summed up from its facts, key facts first.

    Lists are joined; admission rows are merged per grade; the highest grade is the
    largest stated. The first application window that has not closed is shown (a window
    with only an opening date for ``OPEN_ONLY_WINDOW_DAYS`` after it opens). Full / waiting
    list places show only for ``PLACES_SHOWN_DAYS`` after the read; open days only until
    their date, the next four at most. Class size is left to
    the school's own ``class_size`` field, which applies the curated value and its gate."""
    today = today or datetime.date.today()

    def first(key: str) -> Any:
        return next((fact[key] for fact in facts if fact.get(key) is not None), None)

    bus_areas: list[dict[str, str]] = []
    for fact in facts:
        bus_areas += [area for area in fact.get("bus_areas") or [] if area not in bus_areas]
    admission: list[dict[str, Any]] = []
    for fact in facts:
        if not (fact.get("admission") or fact.get("exam_subjects")):
            continue
        grade, grade_to = fact.get("admission_grade"), fact.get("admission_grade_to")
        # Facts for the same grades merge; a fact that names no grade stays a row of its own.
        row = next(
            (r for r in admission if grade is not None and (r["grade"], r["grade_to"]) == (grade, grade_to)),
            None,
        )
        if row is None:
            row = {"grade": grade, "grade_to": grade_to, "types": [], "subjects": []}
            admission.append(row)
        row["types"] += [t for t in fact.get("admission") or [] if t not in row["types"]]
        # "Математика" and "математика" from two facts are one subject (568, 575).
        seen = {_subject_key(s) for s in row["subjects"]}
        for subject in fact.get("exam_subjects") or []:
            if _subject_key(subject) not in seen:
                seen.add(_subject_key(subject))
                row["subjects"].append(subject)
    # A row naming no grade reads as the whole school's rule: "no entrance test" there beside
    # graded rows with a test, or a test beside graded "no entrance test" rows, contradicts
    # them ("8 клас: Приемен изпит; Без приемен изпит", 329). Its fact still shows.
    graded = {t for row in admission if row["grade"] is not None for t in row["types"]}
    tested = {t for row in admission for t in row["types"]} - {"open_entry"}
    for row in admission:
        if row["grade"] is not None:
            continue
        if tested and "open_entry" in row["types"]:
            row["types"].remove("open_entry")
        elif "open_entry" in graded and "entrance_exam" in row["types"]:
            # A school-wide exam the graded rows deny; its subjects are that exam's.
            row["types"].remove("entrance_exam")
            row["subjects"] = []
    for row in admission:
        # "No entrance test" beside an exam, interview or trial day for the same grades.
        if "open_entry" in row["types"] and len(row["types"]) > 1:
            row["types"].remove("open_entry")
    admission = [row for row in admission if row["types"] or row["subjects"]]
    places = []
    read = _date(read_at[:10]) if isinstance(read_at, str) else None
    if read and (today - datetime.date.fromisoformat(read)).days <= PLACES_SHOWN_DAYS:
        for fact in facts:
            row = {
                "grade": fact.get("admission_grade"),
                "grade_to": fact.get("admission_grade_to"),
                "status": fact.get("places"),
            }
            if row["status"] and row not in places:
                places.append(row)
    window = None
    for fact in facts:
        opens, closes = fact.get("applications_open"), fact.get("applications_close")
        if closes:
            current = datetime.date.fromisoformat(closes) >= today
        elif opens:
            current = (today - datetime.date.fromisoformat(opens)).days <= OPEN_ONLY_WINDOW_DAYS
        else:
            continue
        if current:
            window = {"open": opens, "close": closes}
            break
    languages: list[dict[str, Any]] = []
    for fact in facts:
        for item in fact.get("languages") or []:
            if all(row["language"] != item["language"] for row in languages):
                languages.append(item)
    open_days: dict[str, dict[str, Any]] = {}
    for fact in facts:
        for day in fact.get("open_days") or []:
            if datetime.date.fromisoformat(day) >= today and day not in open_days:
                open_days[day] = {
                    "date": day,
                    "register_url": fact.get("register_url"),
                    "source_url": fact.get("source_url"),
                }
    grades = [fact["highest_grade"] for fact in facts if fact.get("highest_grade") is not None]
    return {
        "admission": admission,
        "places": places,
        "highest_grade": max(grades) if grades else None,
        "curricula": [c for c in CURRICULA if c in {v for f in facts for v in f.get("curricula") or []}],
        "diploma_recognised": first("diploma_recognised"),
        "applications": window,
        "instruction_languages": list(dict.fromkeys(v for f in facts for v in f.get("instruction_languages") or [])),
        "languages": languages,
        "bus_areas": bus_areas,
        "special_needs_support": first("special_needs_support"),
        "open_days": [open_days[day] for day in sorted(open_days)][:4],
    }


def _class_size(value: Any) -> Optional[int]:
    return _int_in(value, 1, 60)


# A summer school, camp or holiday programme: its group size is not the school year's (541).
# Whole words for English: "campus" is not a camp. Not "holidays": "care during the
# holidays" beside the school year's class size is no summer school.
_SEASONAL_RE = re.compile(r"лят(?:н|о)|лагер|\bsummer\b|\bcamps?\b", re.IGNORECASE)


def _seasonal(fact: Mapping[str, Any]) -> bool:
    text = fact.get("text_i18n") if isinstance(fact.get("text_i18n"), Mapping) else {}
    parts = [*text.values(), fact.get("quote"), fact.get("source_url")]
    return any(isinstance(part, str) and _SEASONAL_RE.search(part) for part in parts)


# A class size for one grade, intake, profile or course is not the school's (278: one
# first-grade class of 18; 333: 24 in grade 1, 26 in grade 5; 517: course groups). The
# fact still shows with its scope in its words; only the class-size tile leaves it out.
# Read from the English line the reader writes, which names the scope the same way.
_ONE_PART_RE = re.compile(r"\b(?:grades?|preparatory|intake|profiles?|tracks?|(?<!of )courses?)\b", re.IGNORECASE)
# "In all grades" or "grades 1 to 12", but not "in each grade 1 class" (204).
_ALL_GRADES_RE = re.compile(
    r"\b(?:all|every|each)\s+grades?\b(?!\s*(?:\d|(?-i:[IVX]+)\b))|\bgrades?\s+(?:1|I)\s*(?:to|-|–)\s*(?:12|XII)\b",
    re.IGNORECASE,
)
# A pupil's or parent's remark quoted on the site is not something the school states
# (506: "A Grade 12 student says there are 20 students per class").
_HEARSAY_RE = re.compile(
    r"\b(?:students?|pupils?|parents?|graduates?|alumn\w*)\b[^.]{0,40}?\b(?:says|said|recalls|recalled)\b"
    r"|\baccording to (?:an?|one)\s+(?:[\w-]+\s+){0,3}?(?:student|pupil|parent|graduate|alumn\w*)",
    re.IGNORECASE,
)
# A school year ("2026/2027", "2027/28", "2026-2027"): the second year follows the first.
# Not a date ("2025-06-10") or birth years ("born 2019-2020").
_SCHOOL_YEAR_RE = re.compile(r"(born\s+(?:in\s+)?)?\b(20\d\d)\s*[/–-]\s*(?:20)?(\d\d)\b", re.IGNORECASE)


def _past_school_years_only(text: str, today: str) -> bool:
    """Whether ``text`` names school years and all of them are before the one in progress.

    An admissions fact names the year its places or exam dates are for (the reader is told
    to); a page left up from an earlier campaign then stops showing when the next school
    year begins, while this year's intake still shows, with its year named."""
    years = [
        int(first)
        for born, first, second in _SCHOOL_YEAR_RE.findall(text)
        if not born and (int(first) + 1) % 100 == int(second)
    ]
    if not years:
        return False
    current = int(current_academic_year(datetime.date.fromisoformat(today))[:4])
    return max(years) < current


# A fact about a summer school or camp, as its English line names it (541: "The 2026 summer
# programme runs..."); a school-year fact whose quote only mentions summer stays key.
_SEASONAL_EN_RE = re.compile(r"\bsummer\b|\bcamps?\b", re.IGNORECASE)


def _english(fact: Mapping[str, Any]) -> str:
    text = fact.get("text_i18n")
    value = text.get("en") if isinstance(text, Mapping) else None
    return value if isinstance(value, str) else ""


# A language offered only if parents ask (527: "Russian ... if parents are interested") is
# not one the school teaches; the reader is told so but still labels some.
_ON_REQUEST_RE = re.compile(
    r"\b(?:on|upon|at)\s+(?:the\s+)?(?:parents?'?\s+|parental\s+)?(?:request|interest|demand)\b"
    r"|\bif\s+(?:enough\s+)?parents\s+(?:are\s+)?(?:interested|wish|want|ask)",
    re.IGNORECASE,
)


# The national exams (НВО) a state school ranks eighth-graders by are not the school's own
# entrance exam (332); and an "entrance exam" label needs an exam or test in the text (332's
# "documents accepted ... ranking" fact).
_NATIONAL_EXAM_RE = re.compile(r"\bNVO\b|\bnational (?:external )?(?:assessment|exam)", re.IGNORECASE)
_EXAM_WORD_RE = re.compile(r"\b(?:exam\w*|tests?|testing|assessment)\b", re.IGNORECASE)
_OWN_EXAM_RE = re.compile(r"\b(?:entrance|admission|aptitude)\s+(?:exam\w*|tests?)\b", re.IGNORECASE)


def _admission_types(fact: Mapping[str, Any]) -> list[str]:
    types = [value for value in dict.fromkeys(_as_list(fact.get("admission"))) if value in ADMISSION_TYPES]
    english = _english(fact)
    national_only = _NATIONAL_EXAM_RE.search(english) and not _OWN_EXAM_RE.search(english)
    if "entrance_exam" in types and english and (national_only or not _EXAM_WORD_RE.search(english)):
        types.remove("entrance_exam")
    return types


def _exam_subjects(fact: Mapping[str, Any]) -> list[dict[str, str]]:
    english = _english(fact)
    if english and _NATIONAL_EXAM_RE.search(english) and not _OWN_EXAM_RE.search(english):
        return []  # the НВО's subjects, not the school's exam's
    return _bilingual(fact.get("exam_subjects"), 8)


def _on_request(fact: Mapping[str, Any]) -> bool:
    return bool(_ON_REQUEST_RE.search(_english(fact)))


def _one_part(fact: Mapping[str, Any]) -> bool:
    english = _english(fact)
    return bool(_ONE_PART_RE.search(english)) and not _ALL_GRADES_RE.search(english)


# A kindergarten on its school's website (515, 555, 587, 634) is read the school's facts too:
# a fact naming a school grade or a school-only programme is the school's. "Until they enter
# first grade" is about the kindergarten.
KINDERGARTEN_LEVELS = frozenset({"kindergarten", "nursery"})
_SCHOOL_GRADES_RE = re.compile(
    r"\bgrades?\s*\d|\bclass(?:es)?\s*\d|\b\d+(?:st|nd|rd|th)\s+class"
    r"|\b(?:\d+(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth)"
    r"[- ]grade|\bIB\b|\bIGCSE\b|\bA[- ]levels?\b|\b(?:lower|upper) secondary"
    r"|\b(?:secondary|school|Bulgarian|two)\s+diplomas?\b|\bmatura|\bNVO\b",
    re.IGNORECASE,
)
# Only the phrase is left out, so a school grade named elsewhere in the fact still counts.
_BEFORE_SCHOOL_RE = re.compile(
    r"(?:until|before|ready for|readiness for|prepar\w* (?:\w+ )?for|up to)\s+(?:the\s+)?(?:\w+\s+){0,2}"
    r"(?:first grade|1st grade|grade 1\b)|first[- ]grade readiness",
    re.IGNORECASE,
)


def _school_grades_fact(fact: Mapping[str, Any]) -> bool:
    return bool(_SCHOOL_GRADES_RE.search(_BEFORE_SCHOOL_RE.sub(" ", _english(fact))))


def publishable_school_facts(
    attributes: Any, website_url: Optional[str], education_level: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """``{"read_at", "facts": [...], "at_a_glance": {...}, "languages_read"}``, or None when none can show.

    Each fact is ``{"topic", "text_i18n", "source_url", "rank"}`` plus its labels
    (``_labels``), never the quote, in stored order (most useful first). Only the first
    ``MAX_KEY_FACTS`` key facts, ``MAX_KEY_PER_TOPIC`` a topic, stay ``key``; later ones
    show as ``more``.

    Facts show only while ``website_url`` is still the site they were read from: a school
    whose site was found wrong, replaced or removed stops showing them until it is re-read.
    A kindergarten shows none about school grades (``_school_grades_fact``)."""
    kindergarten = str(education_level or "").casefold() in KINDERGARTEN_LEVELS
    block = attributes.get(ATTRIBUTE_KEY) if isinstance(attributes, Mapping) else None
    if not isinstance(block, Mapping) or block.get("_schema_version") != SCHEMA_VERSION:
        return None
    if not _site(website_url) or _site(block.get("website_url")) != _site(website_url):
        return None
    facts = []
    key_per_topic: dict[str, int] = {}
    today = datetime.date.today().isoformat()
    for fact in block.get("facts") or []:
        if not isinstance(fact, Mapping):
            continue
        text = fact.get("text_i18n")
        url = fact.get("source_url")
        if not (
            fact.get("topic") in TOPICS
            and isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and isinstance(text, Mapping)
            and all(isinstance(text.get(lang), str) and text[lang].strip() for lang in ("bg", "en"))
        ):
            continue
        if _HEARSAY_RE.search(_english(fact)):
            continue
        if kindergarten and _school_grades_fact(fact):
            continue
        if fact.get("topic") == "admissions" and _past_school_years_only(_english(fact), today):
            continue  # "14 places for 2025/2026" is a past campaign's
        labels = _labels(fact)
        if kindergarten:
            # A school grade on a kindergarten's fact is the reader's slip ("preparatory groups
            # for 5- and 6-year-olds" as grades 5-6, 587): 0 is the kindergarten's grade.
            for key in ("admission_grade", "admission_grade_to", "highest_grade"):
                if labels.get(key):
                    labels[key] = None
        if labels.get("open_days") and max(labels["open_days"]) < today and not (
            labels.get("applications_close") and labels["applications_close"] >= today
        ):
            continue  # an open day that has passed ("Open day on 16 April") is not news any more
        topic = fact["topic"]
        rank = fact.get("rank") if fact.get("rank") in RANKS else "key"
        if rank == "key" and _SEASONAL_EN_RE.search(_english(fact)):
            # A summer school's hours or meals under "School day" or "Meals" read as the
            # school year's (541, 591); they stay behind "show more" and off the compare page.
            rank = "more"
        if rank == "key":
            if key_per_topic.get(topic, 0) >= MAX_KEY_PER_TOPIC or sum(key_per_topic.values()) >= MAX_KEY_FACTS:
                rank = "more"
            else:
                key_per_topic[topic] = key_per_topic.get(topic, 0) + 1
        facts.append(
            {
                "topic": topic,
                "text_i18n": {"bg": text["bg"], "en": text["en"]},
                "source_url": url,
                "rank": rank,
                **labels,
            }
        )
    if not facts:
        return None
    read_at = str(block.get("read_at") or "")
    # Minor facts are never shown, so their labels stay out of the summary too.
    shown = [fact for fact in facts if fact["rank"] != "minor"]
    return {
        "read_at": read_at,
        "facts": facts,
        "at_a_glance": at_a_glance(shown, read_at),
        LANGUAGES_READ_KEY: block.get(LANGUAGES_READ_KEY) is True,
    }


def shown_school_facts(
    attributes: Any, scrape_status: Any, website_url: Optional[str], education_level: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """The facts that may cross the API boundary (``publishable_school_facts``), or None.

    Facts carry their own page checks, so they do not wait for a Stage 6 report; a
    website-data withholding marker (failed URL check, shared site, re-extraction) still
    hides them, and so does a website URL validation has not accepted."""
    if "withheld_marker" in website_data_withheld_reasons(attributes, scrape_status):
        return None
    if str(scrape_status or "").strip().lower() not in SITE_ACCEPTED_STATUSES:
        return None
    return publishable_school_facts(attributes, website_url, education_level)


def publication_attributes(
    attributes: Any, scrape_status: Any, website_url: Optional[str], education_level: Optional[str] = None
) -> dict[str, Any]:
    """``attributes_for_publication``, plus the shown facts under ``PUBLISHED_KEY`` so the
    display and filter projections can use their tags, languages and class size, and the
    shown website name for the English name (``app.utils.i18n_resolver``)."""
    attrs = attributes_for_publication(attributes, scrape_status)
    facts = shown_school_facts(attributes, scrape_status, website_url, education_level)
    if facts:
        attrs[PUBLISHED_KEY] = facts
    if website_name := shown_website_name(attributes, scrape_status, website_url):
        attrs[WEBSITE_NAME_KEY] = website_name
    return attrs
