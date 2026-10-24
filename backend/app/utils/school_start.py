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


def shown_start_times(attributes: Any, shown_facts: Optional[Mapping[str, Any]]) -> Optional[dict[str, str]]:
    """``{band: "HH:MM"}`` for the stored start times whose fact is among ``shown_facts``
    (the ``publishable_school_facts`` block), or None."""
    if not isinstance(attributes, Mapping) or not isinstance(shown_facts, Mapping):
        return None
    block = attributes.get(ATTRIBUTE_KEY)
    starts = block.get(START_TIMES_KEY) if isinstance(block, Mapping) else None
    if not isinstance(starts, Mapping):
        return None
    shown = {
        (fact.get("text_i18n") or {}).get("bg")
        for fact in shown_facts.get("facts") or []
        if isinstance(fact, Mapping) and fact.get("topic") == "school_day"
    }
    times = {
        band: start["time"]
        for band, start in starts.items()
        if isinstance(start, Mapping)
        and isinstance(start.get("time"), str)
        and _HHMM_RE.match(start["time"])
        and start.get("fact_bg") in shown
    }
    return times or None
