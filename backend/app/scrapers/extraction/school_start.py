"""When a school's day starts, per age band, from the start times its facts state.

The school-facts stage (``app/scrapers/stages/school_facts.py``) reads them after the facts
and stores them in the facts block as ``start_times``; ``school-start`` fills them in for
facts read before. Public transport times aim at the start (``docs/TRANSIT_TIMES.md``):
8:00 for every school told a parent to leave 20-30 minutes late at a 7:30 school. The
schools' shift table (``location_age_group_shifts.shift``) holds no shift for any school.

One cheap call per school with school-day facts. Code keeps a time only when the fact it
names writes it (in its quote or its text) and it falls in a school morning or a second
shift's start (``EARLIEST``-``LATEST``). Publication: ``app/utils/school_start.py``.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

BANDS = ("preschool", "grade_1_4", "grade_5_7", "grade_8_12")
EARLIEST = "07:00"
LATEST = "14:00"  # the timetable keeps trips to 14:00 (tools/transit/build_timetable.mjs)

PROMPT = """You get numbered facts about one school's day, read from its own website, each with the page line
it comes from. For each age band, give the time the first lesson of the day starts, but only when a fact
states it:
- preschool: the preparatory group before first grade (подготвителна група, ПГ, 5 and 6 year olds)
- grade_1_4, grade_5_7, grade_8_12: grades I-IV, V-VII and VIII-XII
Rules:
- A fact about the whole school ("lessons start at 8:00", "one shift, from 7:30") holds for every band the
  school teaches; a fact about some grades holds only for their bands.
- A band taught in two shifts, or in other shifts by term, gets the first shift's start in the first term.
- Only lessons. Not the building's opening hours, morning care before lessons, after-school groups
  (занималня, целодневна организация) or a kindergarten's working hours ("from 7:00 to 19:00").
- "time": the start as the fact writes it, as HH:MM. "fact": that fact's number.
- No fact states a start: return an empty list."""


class Start(BaseModel):
    band: Literal["preschool", "grade_1_4", "grade_5_7", "grade_8_12"]
    time: str = Field(description="HH:MM")
    fact: int = Field(description="The number of the fact that states it")


class Starts(BaseModel):
    starts: list[Start] = Field(default_factory=list)


Caller = Callable[[str, type, str, int], Awaitable[Any]]

# "7:30", "07.30", "7,30 ч.", "8.00"; not part of a date ("01.09.2026") or a longer number.
_TIME_RE = re.compile(r"(?<![\d.:,])([01]?\d|2[0-3])\s?[.:,]\s?([0-5]\d)(?![\d]|[.:,]\d)")


def clock_times(text: Optional[str]) -> set[str]:
    """Every clock time ``text`` writes, as HH:MM."""
    return {f"{int(h):02d}:{m}" for h, m in _TIME_RE.findall(text or "")}


def day_facts(facts: Sequence[Any]) -> list[Mapping[str, Any]]:
    """The stored school-day facts that write a clock time."""
    return [
        fact
        for fact in facts
        if isinstance(fact, Mapping)
        and fact.get("topic") == "school_day"
        and clock_times(_written(fact))
    ]


def _written(fact: Mapping[str, Any]) -> str:
    text = fact.get("text_i18n") if isinstance(fact.get("text_i18n"), Mapping) else {}
    return "\n".join(str(part) for part in (fact.get("quote"), text.get("bg"), text.get("en")) if part)


def message(facts: Sequence[Mapping[str, Any]]) -> str:
    lines = []
    for n, fact in enumerate(facts, 1):
        text = fact.get("text_i18n") if isinstance(fact.get("text_i18n"), Mapping) else {}
        lines.append(f"{n}. {text.get('bg') or text.get('en')}\n   Page line: {fact.get('quote') or ''}")
    return "\n".join(lines)


def checked(answer: Starts, facts: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """``{band: {"time", "fact_bg"}}`` for the answers their fact writes, the first per band.

    ``fact_bg`` ties a time to its fact, so the time shows only while the fact does."""
    kept: dict[str, dict[str, str]] = {}
    for start in answer.starts:
        if start.band in kept or not 1 <= start.fact <= len(facts):
            continue
        fact = facts[start.fact - 1]
        times = clock_times(start.time)
        if len(times) != 1:
            continue
        time = times.pop()
        if time in clock_times(_written(fact)) and EARLIEST <= time <= LATEST:
            kept[start.band] = {"time": time, "fact_bg": str((fact.get("text_i18n") or {}).get("bg") or "")}
    return kept


async def read_start_times(facts: Sequence[Any], call: Caller, school_id: int) -> dict[str, dict[str, str]]:
    """The start times the stored ``facts`` state; no call when none writes a clock time."""
    day = day_facts(facts)
    if not day:
        return {}
    answer: Starts = await call(PROMPT, Starts, message(day), school_id)
    return checked(answer, day)
