"""Canonical academic-year handling shared by extraction and the publish boundary.

The school-facing text is kept verbatim in ``pricing.academic_year``; everything that
compares or displays a year goes through here. Nothing is stored in canonical form, so
these helpers stay pure and the canonical value can never drift from the raw one.
"""

from __future__ import annotations

import re
from datetime import date

# Bulgarian sites write the year with a hyphen, en/em dash or slash, and sometimes
# abbreviate the second half ("2025/26"). Any surrounding spacing is incidental.
_YEAR_RANGE_RE = re.compile(r"(20\d{2})\s*[-‐-―/]\s*(20\d{2}|\d{2})")

# Bulgarian school years start in mid-September; the first of the month is close
# enough for a rollover boundary and keeps the rule explainable.
_ROLLOVER_MONTH = 9

CURRENT = "current"
DATED_OTHER = "dated_other"
NOT_STATED = "not_stated"


def normalize_academic_year(value: str | None) -> str | None:
    """Return ``"YYYY/YYYY"`` for a real academic year, else ``None``.

    Only consecutive years qualify. Multi-year spans such as a "2022-2027" development
    strategy look identical to a year range but are not academic years, and treating
    them as one would silently mislabel prices.
    """
    match = _YEAR_RANGE_RE.search(str(value or ""))
    if not match:
        return None

    start_text, end_text = match.group(1), match.group(2)
    start = int(start_text)
    # "2025/26" abbreviates the end year against the start year's century.
    end = int(end_text) if len(end_text) == 4 else int(start_text[:2] + end_text)

    if end - start != 1:
        return None
    return f"{start}/{end}"


def academic_year_is_resolvable(value: str | None) -> bool:
    """True when a stored year is publishable: either absent, or a real academic year."""
    if not str(value or "").strip():
        return True
    return normalize_academic_year(value) is not None


def current_academic_year(today: date | None = None) -> str:
    """Return the academic year in progress, rolling over on 1 September."""
    today = today or date.today()
    start = today.year if today.month >= _ROLLOVER_MONTH else today.year - 1
    return f"{start}/{start + 1}"


def academic_year_status(value: str | None, today: date | None = None) -> str:
    """Classify a stored year for display.

    Returns :data:`NOT_STATED` when the school published no year, :data:`CURRENT` when it
    matches the year in progress, and :data:`DATED_OTHER` otherwise. A value that does not
    normalize also lands on :data:`DATED_OTHER` so an unparseable year is never presented
    as current; the publish gate rejects those rows before they reach display anyway.
    """
    if not str(value or "").strip():
        return NOT_STATED
    canonical = normalize_academic_year(value)
    if canonical is None:
        return DATED_OTHER
    return CURRENT if canonical == current_academic_year(today) else DATED_OTHER
