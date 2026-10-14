"""Open days: which lines of a school's pages to watch, and what the reader is asked for.

The open-days stage (``app/scrapers/stages/open_days.py``) re-reads a few pages of each
school often and calls the model only when the lines around an open-day or application
mention change. Those lines, cleaned (markup, spacing and quote marks left out), are hashed;
a news feed or visitor counter elsewhere on the page does not count as a change. Facts it
keeps go through the school-facts checks (quote on a page read, sign-up link on a page, the
judge) and are stored with the school's other facts.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from collections.abc import Sequence
from typing import Optional

from pydantic import BaseModel, Field

from app.scrapers.extraction.school_facts import Fact, _normalise
from app.utils.school_facts import OPEN_ONLY_WINDOW_DAYS

# A line that announces an open day or an application window.
MENTION_RE = re.compile(
    r"отворен\w*\s+врат|open[\s-]?(?:day|house|door)|открит\w*\s+(?:урок|час|ден|занимани)|"
    r"информационн\w*\s+(?:среща|ден)|info(?:rmation)?\s+(?:day|session|evening|meeting)|"
    r"кандидатстван|прием\s+на\s+документи|подаване\s+на\s+документи|applications?\s+(?:open|close|deadline)",
    re.IGNORECASE,
)
# Same-site links worth opening from the home page: open days first, then admissions pages.
LINK_RE = re.compile(
    r"отворен|open[\s-]?(?:day|house)|прием|priem|admission|кандидат|kandidat|запис|zapis|enrol",
    re.IGNORECASE,
)
# Lines kept around each mention: for the hash, and (wider) for the reader.
HASH_CONTEXT = 2
READ_CONTEXT = 8
MAX_EXCERPT_CHARS = 6_000

# A date the page gives without a year is read as the next such date; only one this close
# is believed (an undated line from last spring would otherwise become next spring).
UNDATED_YEAR_MAX_DAYS = 200

READER_PROMPT = """You read lines from a school's own web pages (each part starts with "=== PAGE <url>") that mention
open days or applications. List only these facts, each with topic "admissions":
- open days: days of open doors (Ден на отворените врати), open lessons, information meetings or visits for
  prospective parents, with their dates in open_days as YYYY-MM-DD, and register_url: the link to sign up,
  copied exactly from the page's markdown link, only when the page has one;
- application windows: the dates applications open and close, in applications_open and applications_close as
  YYYY-MM-DD, and admission_grade: the grade they are for (0 for kindergarten), when stated.
Today is {today}. Only dates from today on: leave out anything already past. When the quote gives a day and
month but no year, use the next such date from today and set year_stated to false.
Rules:
- Only what the pages state about this institution.
- Each fact is one short line in English (fact_en) and Bulgarian (fact_bg), saying only what its quote says.
- For every fact, copy into "quote" the page lines it comes from, verbatim and in the page's language (a heading
  and the line with the date, up to 8 lines), and the URL from that page's PAGE line. The quote alone must state
  the fact and its dates.
- If the lines announce no upcoming date, return no facts: that is a correct answer, never pad."""


class DateFact(Fact):
    year_stated: bool = Field(default=True, description="False when the quote gives the date without a year")


class DateFacts(BaseModel):
    facts: list[DateFact] = Field(description="Upcoming open days and application windows; empty if none")


def _lines(text: str) -> list[str]:
    return [line for line in (" ".join(_normalise(raw).split()) for raw in text.splitlines()) if line]


def mention_lines(text: str, context: int = HASH_CONTEXT) -> list[str]:
    """The page's cleaned lines that mention open days or applications, with ``context``
    lines on each side, in page order. Empty when nothing mentions them."""
    lines = _lines(text)
    keep: set[int] = set()
    for n, line in enumerate(lines):
        if MENTION_RE.search(line):
            keep.update(range(max(0, n - context), min(len(lines), n + context + 1)))
    return [lines[n] for n in sorted(keep)]


# A date on a line: 15.11, 15/11, 2026-11-15, or a month's name.
_DATE_RE = re.compile(
    r"\b\d{1,2}\s*[./]\s*\d{1,2}\b|\b\d{4}-\d{2}-\d{2}\b|"
    r"януар|феврув|март|април|\bмай\b|\bюни\b|\bюли\b|август|септемв|октомв|ноемв|декемв|"
    r"january|february|march|april|\bmay\b|june|july|august|september|october|november|december",
    re.IGNORECASE,
)


def mention_hash(text: str) -> str:
    """A hash of the mention lines, or "" when the page has none. Numbers on a nearby line
    with no date (a visitor counter, a time stamp) do not count."""
    lines = [
        line if MENTION_RE.search(line) or _DATE_RE.search(line) else re.sub(r"\d+", "#", line)
        for line in mention_lines(text)
    ]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16] if lines else ""


def excerpt(text: str) -> str:
    """The raw lines around each mention (links kept, for the sign-up address), for the reader."""
    raw = [line for line in text.splitlines() if line.strip()]
    keep: set[int] = set()
    for n, line in enumerate(raw):
        if MENTION_RE.search(_normalise(line)):
            keep.update(range(max(0, n - READ_CONTEXT), min(len(raw), n + READ_CONTEXT + 1)))
    out, last = [], -2
    for n in sorted(keep):
        if n != last + 1:
            out.append("...")
        out.append(raw[n])
        last = n
    return "\n".join(out)[:MAX_EXCERPT_CHARS]


_MD_LINK_RE = re.compile(r"\]\(<?([^()\s<>]+)")


def mention_links(text: str) -> list[str]:
    """Link addresses on the raw lines that mention open days or applications (a news
    headline linking its article)."""
    found: list[str] = []
    for line in text.splitlines():
        if MENTION_RE.search(_normalise(line)):
            found += [link for link in _MD_LINK_RE.findall(line) if link not in found]
    return found


def has_dates(fact: Fact) -> bool:
    return bool(fact.open_days or fact.applications_open or fact.applications_close)


def upcoming(fact: DateFact, today: datetime.date) -> Optional[str]:
    """Why the fact cannot be kept (a malformed or past date, an undated one too far ahead), or None.

    Open days must be today or later; an application window counts until it closes."""
    try:
        days = [datetime.date.fromisoformat(d) for d in fact.open_days]
        opens = datetime.date.fromisoformat(fact.applications_open) if fact.applications_open else None
        closes = datetime.date.fromisoformat(fact.applications_close) if fact.applications_close else None
    except ValueError:
        return "malformed date"
    if not (days or opens or closes):
        return "no date"
    if not fact.year_stated and any(
        not 0 <= (d - today).days <= UNDATED_YEAR_MAX_DAYS for d in [*days, *(x for x in (opens, closes) if x)]
    ):
        return "date without a year too far ahead"
    if days:
        return None if max(days) >= today else "past date"
    if closes:  # an application window counts until it closes
        return None if closes >= today else "past date"
    return None if (today - opens).days <= OPEN_ONLY_WINDOW_DAYS else "past date"


def judge_note(today: datetime.date, facts: Sequence[DateFact]) -> str:
    """A first line for the judge when a date's year comes from today's date, not the quote."""
    if all(fact.year_stated for fact in facts):
        return ""
    return (
        f"Today is {today.isoformat()}. A date whose quote gives the day and month but no year is the next such "
        "date from today; that year is not something the fact adds.\n\n"
    )
