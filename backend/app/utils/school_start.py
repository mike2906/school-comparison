"""Which start times a school response shows (``app/scrapers/extraction/school_start.py``).

A start time is stored in the facts block with the fact it comes from and shows only while
that fact shows (``shown_school_facts``): same website, same gates, no second set of rules.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Optional

from app.utils.school_facts import ATTRIBUTE_KEY

START_TIMES_KEY = "start_times"
_HHMM_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
# A timetable row whose hours were lost on the page: "00 – 08.35 | 08.55 – 09.30" (19 СУ). The
# reader then takes the second lesson for the first: no start time comes from such a line.
# Not a year ("2026/27 – 8:00", "2025-26 – 8.00") or a school number ("№ 19 – 7.30").
_LOST_HOUR_RE = re.compile(r"(?<![\d.:,/\-–№])(?<!№ )\d{2}\s*[–—−-]\s*\d{1,2}[.:,]\d{2}(?!\d)")
# "7:30", "07.30", "7,30 ч.", "8.00"; not part of a date ("01.09.2026") or a longer number, and
# not a list of grades ("за 1, 2, 9, 10 клас" is no 09:10): no space after a comma.
_TIME_RE = re.compile(r"(?<![\d.:,])([01]?\d|2[0-3])(?:\s?[.:]\s?|,)([0-5]\d)(?![\d]|[.:,]\d)")


def clock_times(text: Optional[str]) -> set[str]:
    """Every clock time ``text`` writes, as HH:MM."""
    return {f"{int(h):02d}:{m}" for h, m in _TIME_RE.findall(text or "")}


def written(fact: Mapping[str, Any]) -> str:
    """A fact's quote and texts, where its times must be written."""
    text = fact.get("text_i18n") if isinstance(fact.get("text_i18n"), Mapping) else {}
    return "\n".join(str(part) for part in (fact.get("quote"), text.get("bg"), text.get("en")) if part)


def garbled(quote: Any) -> bool:
    """The page line is a timetable with lost hours (``_LOST_HOUR_RE``)."""
    return isinstance(quote, str) and bool(_LOST_HOUR_RE.search(quote))


def shown_start_times(attributes: Any, shown_facts: Optional[Mapping[str, Any]]) -> Optional[dict[str, str]]:
    """``{band: "HH:MM"}`` for the stored start times whose fact is among ``shown_facts``
    (the ``publishable_school_facts`` block), or None."""
    if not isinstance(attributes, Mapping) or not isinstance(shown_facts, Mapping):
        return None
    block = attributes.get(ATTRIBUTE_KEY)
    starts = block.get(START_TIMES_KEY) if isinstance(block, Mapping) else None
    if not isinstance(starts, Mapping):
        return None
    # Stored before the lost-hours and grade-list checks: the stored fact decides here too.
    day = [fact for fact in block.get("facts") or [] if isinstance(fact, Mapping) and fact.get("topic") == "school_day"]
    lost = {_bg(fact) for fact in day if garbled(fact.get("quote"))}
    shown = {
        _bg(fact)
        for fact in shown_facts.get("facts") or []
        if isinstance(fact, Mapping) and fact.get("topic") == "school_day"
    } - lost
    times = {
        band: start["time"]
        for band, start in starts.items()
        if isinstance(start, Mapping)
        and isinstance(start.get("time"), str)
        and _HHMM_RE.match(start["time"])
        and start.get("fact_bg") in shown
        and any(_bg(fact) == start["fact_bg"] and start["time"] in clock_times(written(fact)) for fact in day)
    }
    return times or None


def _bg(fact: Mapping[str, Any]) -> Optional[str]:
    text = fact.get("text_i18n")
    return text.get("bg") if isinstance(text, Mapping) else None
