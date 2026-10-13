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
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.scrapers.fee_pages import page_key
from app.utils.school_facts import SCHEMA_VERSION, TOPICS  # noqa: F401  (re-exported for the stage)

MAX_FACTS = 8

Topic = Literal[
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
]

PICK_PROMPT = """You help find the pages of a school website that describe what the school is like for a child
and a parent: about the school, curriculum or programme, subjects and languages taught, profiled classes,
daily schedule and after-school care, admissions, clubs and activities, transport, meals, facilities,
support for children with special needs. You get the institution's name, the pages read so far, and the
same-site links not yet opened, as "URL | link text". Return up to 4 URLs, copied exactly from the list,
most useful first. Skip news, galleries, events, contacts, documents and policies, and fee pages.
Return none if nothing fits."""

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
- admissions: how admission works (test, interview, ranking) when stated as the current process
Do not list: fees or prices; exam results, rankings or competitions won; mission statements, values or claims
anyone could make ("individual approach", "modern facilities", "caring environment", "excellent conditions");
news, events, one-off projects; anything dated to a past school year; counts that change every year (pupils,
classes, teachers, rooms, computers, years since founding). Class size and times of day are not such counts.
Rules:
- Only what the pages state about this institution. If a page covers a sister school or kindergarten,
  leave its facts out unless they plainly apply to this one.
- Each fact is one short line in English (fact_en) and Bulgarian (fact_bg), saying only what its quote says.
- For every fact, copy into "quote" the one page line it comes from, verbatim and in the page's language, and
  the URL from that page's PAGE line. The quote alone must state the fact.
- At most 8 facts, the most useful for choosing a school first, and at most 2 per topic. Fewer is fine. If
  the pages state nothing concrete, return no facts: that is a correct answer, never pad."""

JUDGE_PROMPT = """You check facts written about a school against the website lines they were taken from.
For each numbered item you get a fact and a quote. Answer yes only if the quote by itself states everything
the fact says: no part of the fact may come from elsewhere or be inferred, and a mission statement, slogan or
general claim does not state a concrete fact. Return the numbers of the items that pass."""


assert Topic.__args__ == TOPICS  # one list: the Literal is what the reader is held to


class Fact(BaseModel):
    topic: Topic
    fact_en: str
    fact_bg: str
    quote: str = Field(description="The one page line the fact comes from, copied verbatim")
    source_url: str = Field(description="The URL from the PAGE line of the page the quote is on")


class Facts(BaseModel):
    facts: list[Fact] = Field(description="Concrete facts, at most 8; empty if the pages state none")


class Supported(BaseModel):
    # Required: with a default the model can answer with no list at all.
    passed: list[int] = Field(description="The numbers of the items whose quote states the fact")


def _normalise(text: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # markdown images
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # markdown links keep their text
    text = re.sub(r"[*_#>|`\\]+", " ", text)
    text = text.replace("“", '"').replace("”", '"').replace("„", '"').replace("’", "'")
    text = text.replace("–", "-").replace("—", "-")
    return " ".join(text.casefold().split())


def quote_on_page(fact: Fact, pages: Sequence[tuple[str, str]]) -> Optional[str]:
    """The URL of the page (url, text) the fact's quote is on, or None.

    The page the fact names is tried first; a quote found on another page read is credited
    to that page, since the reader sometimes copies the wrong PAGE line."""
    quote = _normalise(fact.quote).strip(" .…;:-")
    if len(quote) < 12:
        return None
    named = [p for p in pages if page_key(p[0]) == page_key(fact.source_url)]
    for url, text in [*named, *(p for p in pages if p not in named)]:
        if quote in _normalise(text):
            return url
    return None


def judge_message(facts: Sequence[Fact]) -> str:
    return "\n\n".join(f"{n}. Fact: {fact.fact_en}\nQuote: {fact.quote}" for n, fact in enumerate(facts, 1))


def stored_fact(fact: Fact, url: str) -> dict:
    return {
        "topic": fact.topic,
        "text_i18n": {"bg": fact.fact_bg.strip(), "en": fact.fact_en.strip()},
        "source_url": url,
        "quote": fact.quote.strip(),
    }
