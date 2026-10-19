"""School facts: what the reader is asked for, and the checks a fact must pass to be kept.

The school-facts stage (``app/scrapers/stages/school_facts.py``) reads a school's own pages
and lists the concrete facts a parent comparing schools would use, each filed under one of
``TOPICS`` and backed by the page line it came from. Code keeps a fact only when that line
is on the page, and a second call confirms the line states the fact. Trial results that
shaped the prompts: ``docs/accuracy/facts_trial/``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Literal, Optional
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field

from app.scrapers.fee_pages import page_key
from app.utils.school_facts import (  # noqa: F401  (SCHEMA_VERSION re-exported)
    ADMISSION_TYPES,
    CURRICULA,
    FACT_TAGS,
    LANGUAGE_LEVELS,
    PLACES,
    RANKS,
    SCHEMA_VERSION,
    TOPICS,
)

# The reader's limit. Facts are graded afterwards; the API shows the key ones first.
MAX_FACTS = 20

Topic = Literal[TOPICS]  # the reader is held to the published topic list
Tag = Literal[FACT_TAGS]
Level = Literal[LANGUAGE_LEVELS]
Rank = Literal[RANKS]
Admission = Literal[ADMISSION_TYPES]
Places = Literal[PLACES]
Curriculum = Literal[CURRICULA]

PICK_PROMPT = """You help find the pages of a school website that describe what the school is like for a child
and a parent: about the school, curriculum or programme, subjects and languages taught, profiled classes,
daily schedule and after-school care, admissions, clubs and activities, transport, meals, facilities,
support for children with special needs. You get the institution's name, the pages read so far, and the
same-site links not yet opened, as "URL | link text". Return up to 4 URLs, copied exactly from the list,
most useful first. Skip news, galleries, events, contacts, documents and policies, fee pages, and
testimonials or reviews (отзиви, мнения, "от децата", "от родителите"). Return none if nothing fits."""

# Always opened (up to 2 a site): how admission works is a key fact the picker can pass over.
# "андидатств" also matches 172's menu, which spells "Кандидатстване" with a Latin K. Not the
# director's office hours (приемно време), entrepreneurship (предприемачество) or job candidates.
ADMISSION_LINK_RE = re.compile(
    r"(?<!пред)прием(?!н)|андидатств|kandidatstv|(?<!pred)priem(?!no)|admission|enrol", re.IGNORECASE
)

# When the picker returns nothing, follow links whose URL or text names one of these topics.
FALLBACK_LINK_RE = re.compile(
    r"за[\s-]?нас|about|програм|учебн|curricul|прием|admission|дневен|режим|занималн|целодневн|"
    r"извънклас|клуб|club|activit|транспорт|transport|хран|meal|езиц|language|профил",
    re.IGNORECASE,
)

READER_PROMPT = """You read a school's own web pages (each starts with "=== PAGE <url>") and list the concrete facts
a parent comparing schools would use. File each fact under one topic:
- curriculum: the programme or qualification (Bulgarian state programme, Cambridge, IB, Montessori), profiled
  or specialised classes and their entry grade, vocational specialties
- languages: which languages are taught, from which grade, how intensively, native-speaker teachers
- school_day: start and end times, shifts, full-day schooling or after-school care (занималня, целодневно
  обучение), holiday or summer schools
- class_size: the most pupils in a class or group
- meals: own kitchen or catering, which meals are served and to whom
- transport: a school bus and the areas it covers
- activities: named sports, clubs, teams and arts on offer
- facilities: notable rooms and grounds (pool, sports hall, labs, library)
- support: psychologist, speech therapist, resource teacher, support for special educational needs
- admissions: how admission works for each grade or age as the page describes it now: an entrance exam or
  test and its subjects or rounds, an interview or talk with a psychologist, trial days, year-round admission
  while places are free, which ages or birth years are taken; and, for the current or coming school year (name
  it), the places offered and the exam dates
Do not list: fees or prices; exam results, rankings or competitions won; mission statements, values or claims
anyone could make ("individual approach", "modern facilities", "caring environment", "excellent conditions");
news, events, one-off projects; anything dated to a past school year; counts that change every year (pupils,
classes, teachers, rooms, computers, years since founding). Class size, times of day and the places offered in
an intake are not such counts. An admissions page whose dates have passed still states how admission works (not its dates or places).
Rules:
- Only what the pages state about this institution. If a page covers a sister school or kindergarten,
  leave its facts out unless they plainly apply to this one.
- Only what the school itself states: a pupil's, parent's or graduate's quoted remark or review is not a fact.
- A fact about one part of the school (one grade or intake, a profile, a course, a summer school or camp) says
  which part, in its own words.
- Each fact is one short line in English (fact_en) and Bulgarian (fact_bg), saying only what its quote says.
- For every fact, copy into "quote" the page line it comes from, verbatim and in the page's language, and the
  URL from that page's PAGE line. When one line is not enough (a list or table, such as shift times by grade),
  copy its heading line and the rows you use, up to 8 lines. The quote alone must state the fact. Each fact has
  its own quote: do not split one line into several facts.
- Name only what the quote itself names: if a page lists taekwondo in text and fencing only as a picture,
  the fact is about taekwondo.
- Only what the school has or does now, or has set for the current or coming school year. Plans for
  something that does not exist yet ("ще създаваме", "will open") are not facts.
- Add nothing the quote lacks: no year, adjective, number or clause from elsewhere. A fact that says less
  but all of it from the quote is better than one that says more.
- Be specific: name the sports, clubs, languages, rooms or hours. "Sports activities are part of the programme"
  or "pupils are at school for the full day" with nothing more says too little; give the detail the page has
  (which sports, until what time) or leave the fact out. The subjects every school teaches under the state
  programme are not worth a fact.
- Label a fact, only when its quote states it:
  - tags, from this list: transportation (a school bus or transport service), cafeteria (the school's own
    canteen or kitchen), meals_provided (meals served to pupils), extended_day (full-day schooling or
    after-school care: целодневно обучение, занималня), sports_facilities (a gym, pool, pitch or courts),
    library, computer_lab, music_program, sports_program (sports training or teams), arts_program,
    montessori, waldorf, ib_program, project_based
  - languages: each language the quote says is taught beyond the standard state programme, with a level when
    stated: immersion (most teaching is in it), bilingual (some subjects are taught in it), enrichment
    (intensive or extended study of it); no level otherwise. Write the language's English name.
    For a language the quote gives weekly hours for, add hours_per_week.
  - instruction_languages: the languages most lessons are taught in, when the quote says.
  - max_class_size: the most pupils in a class or group of the school year, as a number, only when the quote gives
    it for the whole school; never one grade's, intake's, profile's or course's, nor a summer school's, camp's or
    holiday programme's.
  - admission: how children are admitted, only as the quote states it: any of entrance_exam, interview,
    trial_day, open_entry (the quote says there is no test or interview); admission_grade: the grade it is
    for (0 for kindergarten or preschool), only when the quote names the grade or the kindergarten or
    preschool class; for a range of grades (grades 2 to 4), admission_grade
    is the first and admission_grade_to the last; exam_subjects: the subjects of an entrance exam, each as {"bg", "en"}.
  - places: full or waiting_list when the quote says a class or grade has no places or a waiting list for the
    current or coming school year (admission_grade for which grade).
  - highest_grade: the highest grade the school teaches, as a number, when the quote says (1 to 12).
  - curricula: the programmes the quote names (not guessed from the school type), any of bulgarian_state,
    ib, cambridge, british, american, canadian, french, german; diploma_recognised: true or false only when the quote says whether the diploma is
    recognised in Bulgaria.
  - applications_open, applications_close: the dates applications open and close for the current or coming
    school year, as YYYY-MM-DD, only when the quote gives the full date.
  - open_days: the dates of open days, open lessons or days of open doors (Ден на отворените врати), as
    YYYY-MM-DD, only when the quote gives the full date; register_url: the link to sign up for them,
    copied exactly from the page's markdown link, only when the page has one.
  - bus_areas: the districts or areas the school bus covers, each as {"bg", "en"} (the name in Bulgarian and
    in Latin letters).
  - special_needs_support: true when the quote says the school supports children with special educational needs.
- Up to 20 facts, the most useful for choosing a school first. Fewer is fine. If the pages state nothing
  concrete, return no facts: that is a correct answer, never pad."""

JUDGE_PROMPT = """You check facts written about a school against the website lines they were taken from.
For each numbered item you get a fact (in English), any labels on it (tags, languages, admission, dates and so
on), and a quote
(usually Bulgarian). Pass an item when the quote states what the fact and every label say; translation and plain
rewording are fine. A heading, list item or link title that names
a specialty, subject, club, sport or room is enough for a fact that only says the school offers or has it.
The page's address can supply context the quote leaves out (a quote on /anglijski-ezik/ is about English).
Fail an item when the fact adds something neither says (a number, a grade, a scope, a claim), when the quote
describes a plan for something that does not exist yet (a menu or timetable set for the current or coming
school year is not a plan), or
when the quote is a bare label ("Училищен психолог") stretched into a description, or a slogan with nothing
concrete in it, or when a label goes beyond the quote.
Grade each item that passes by how much it helps a parent choose between schools, and list its number under one:
- key: the programme or profile, which languages and how intensively, school hours or after-school care, a
  school bus, meals, class size, support staff, or how admission works
- more: concrete and true but secondary, such as a named club, sport, room or event that recurs every year
- minor: true but of little use for choosing (a room every school has, a detail of a timetable)
An item that does not pass is listed under none."""


class LanguageLabel(BaseModel):
    language: str = Field(description="The language's English name")
    level: Optional[Level] = None
    hours_per_week: Optional[int] = None


class Name(BaseModel):
    bg: str
    en: str


class Fact(BaseModel):
    topic: Topic
    fact_en: str
    fact_bg: str
    quote: str = Field(description="The page line (or a heading and up to 8 rows) the fact comes from, verbatim")
    source_url: str = Field(description="The URL from the PAGE line of the page the quote is on")
    tags: list[Tag] = Field(default_factory=list, description="Filter tags the quote states")
    languages: list[LanguageLabel] = Field(default_factory=list, description="Languages taught beyond the standard")
    max_class_size: Optional[int] = Field(default=None, description="The most pupils in a class, if the quote gives it")
    instruction_languages: list[str] = Field(default_factory=list)
    admission: list[Admission] = Field(default_factory=list)
    admission_grade: Optional[int] = None
    admission_grade_to: Optional[int] = None
    exam_subjects: list[Name] = Field(default_factory=list)
    places: Optional[Places] = None
    highest_grade: Optional[int] = None
    curricula: list[Curriculum] = Field(default_factory=list)
    diploma_recognised: Optional[bool] = None
    applications_open: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    applications_close: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    bus_areas: list[Name] = Field(default_factory=list)
    special_needs_support: Optional[bool] = None
    open_days: list[str] = Field(default_factory=list, description="YYYY-MM-DD")
    register_url: Optional[str] = None


# The labels a fact can carry, stored and restored as they are (``Fact`` validates them).
LABEL_FIELDS = (
    "tags",
    "languages",
    "max_class_size",
    "instruction_languages",
    "admission",
    "admission_grade",
    "admission_grade_to",
    "exam_subjects",
    "places",
    "highest_grade",
    "curricula",
    "diploma_recognised",
    "applications_open",
    "applications_close",
    "bus_areas",
    "special_needs_support",
    "open_days",
    "register_url",
)


class Facts(BaseModel):
    facts: list[Fact] = Field(description="Concrete facts, at most 20; empty if the pages state none")


class Graded(BaseModel):
    # Required: with defaults the model can answer with no lists at all.
    key: list[int] = Field(description="Items that pass and matter most for choosing a school")
    more: list[int] = Field(description="Items that pass and are concrete but secondary")
    minor: list[int] = Field(description="Items that pass but are of little use for choosing")

    def rank(self, n: int) -> Optional[str]:
        """The best grade item ``n`` got, or None when it did not pass."""
        return next((rank for rank in RANKS if n in getattr(self, rank)), None)


@lru_cache(maxsize=512)  # each page is matched against every quote part
def _normalise(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # markdown images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # markdown links keep their text
    text = re.sub(r"[*_#>|`\\\[\]]+", " ", text)  # also a link's text quoted without its address
    text = text.replace("“", '"').replace("”", '"').replace("„", '"').replace("’", "'")
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)  # "образование , което" as the reader tidies it
    return " ".join(text.casefold().split())


def quote_on_page(fact: Fact, pages: Sequence[tuple[str, str]]) -> Optional[str]:
    """The URL of the page (url, text) the fact's quote is on, or None.

    The page the fact names is tried first; a quote found on another page read is credited
    to that page, since the reader sometimes copies the wrong PAGE line."""
    whole = _strip(_normalise(fact.quote))
    if not whole:
        return None
    # A quote of several lines (a list or table) matches when each of its lines is on the
    # page; the reader may leave out rows in between.
    parts = [whole] + [_strip(_normalise(line)) for line in fact.quote.splitlines()]
    parts = [part for part in parts if part]
    named = [p for p in pages if page_key(p[0]) == page_key(fact.source_url)]
    for url, text in [*named, *(p for p in pages if p not in named)]:
        if _on_page(whole, text) or (len(parts) > 2 and all(_on_page(part, text) for part in parts[1:])):
            return url
    return None


def link_on_page(url: Optional[str], pages: Sequence[tuple[str, str]]) -> bool:
    """Whether ``url`` is a link on one of the pages read (the reader must not write its own)."""
    if not url or not url.startswith(("http://", "https://")):
        return False
    target = _link_key(url.strip())
    return any(
        _link_key(urljoin(page_url, link)) == target for page_url, text in pages for link in _LINK_RE.findall(text)
    )


def check_link(fact: Fact, pages: Sequence[tuple[str, str]]) -> None:
    """Drop the fact's sign-up link unless it is a link on a page read (new and carried facts)."""
    if fact.register_url and not link_on_page(fact.register_url, pages):
        fact.register_url = None


def _link_key(url: str) -> tuple[str, str, str, str]:
    # Only the path's trailing slash is ignored; the query must match exactly.
    parts = urlsplit(url)
    return parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query


# A markdown link's destination, with one level of parentheses inside it (``/a(b)``).
_LINK_RE = re.compile(r"\]\(<?((?:[^()\s<>]|\([^()\s<>]*\))+)")


def _strip(text: str) -> str:
    return text.strip(" .…;:-")


def _on_page(quote: str, text: str) -> bool:
    # A short quote (a heading such as "Таекуондо") must be a whole line, not part of one.
    if len(quote) >= 12:
        return quote in _normalise(text)
    return any(_strip(_normalise(line)) == quote for line in text.splitlines())


def stored_as_fact(stored: dict) -> Optional[Fact]:
    """A stored fact back as a ``Fact``, to check its quote against a new read."""
    try:
        text = stored["text_i18n"]
        return Fact(
            topic=stored["topic"],
            fact_en=text["en"],
            fact_bg=text["bg"],
            quote=stored["quote"],
            source_url=stored["source_url"],
            **{key: stored[key] for key in LABEL_FIELDS if stored.get(key) is not None},
        )
    except (KeyError, TypeError, ValueError):
        return None


def quote_key(fact: Fact) -> str:
    return _strip(_normalise(fact.quote))


def _labels(fact: Fact) -> str:
    """The fact's labels as ``\nName: value`` lines for the judge, unset ones left out."""
    lines = []
    for key in LABEL_FIELDS:
        value = getattr(fact, key)
        if value in (None, []) or key == "register_url":  # checked in code (``link_on_page``)
            continue
        if isinstance(value, list):
            value = ", ".join(_label_text(item) for item in value)
        elif key in ("admission_grade", "admission_grade_to") and value == 0:
            value = "0 (kindergarten or preschool class)"  # a bare 0 reads as a grade the quote never names
        lines.append(f"\n{key.replace('_', ' ').capitalize()}: {value}")
    return "".join(lines)


def _label_text(item: object) -> str:
    if isinstance(item, LanguageLabel):
        extra = [
            part for part in (item.level, f"{item.hours_per_week} h a week" if item.hours_per_week else None) if part
        ]
        return f"{item.language} ({', '.join(extra)})" if extra else item.language
    if isinstance(item, Name):
        return f"{item.bg} / {item.en}"
    return str(item)


def judge_message(quoted: Sequence[tuple[Fact, str]]) -> str:
    """The numbered items for the judge: each fact, its labels, its quote, and the page the quote is on."""
    return "\n\n".join(
        f"{n}. Fact: {fact.fact_en}{_labels(fact)}\nPage: {url}\nQuote: {fact.quote}"
        for n, (fact, url) in enumerate(quoted, 1)
    )


def stored_fact(fact: Fact, url: str, rank: str) -> dict:
    return {
        "topic": fact.topic,
        "text_i18n": {"bg": fact.fact_bg.strip(), "en": fact.fact_en.strip()},
        "source_url": url,
        "quote": fact.quote.strip(),
        "rank": rank,
        **fact.model_dump(include=set(LABEL_FIELDS)),
    }
