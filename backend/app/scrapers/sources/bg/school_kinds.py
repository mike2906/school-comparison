"""Kinds of Bulgarian school the register does not tell apart by type code."""

import re
from typing import Iterable

# "5. Вечерно средно училище", "IV Сменно-вечерна гимназия", "Вечерна професионална
# гимназия": registered as средно училище or гимназия like any other, but for pupils of 16
# and over. Not a kindergarten called "Вечерница".
_EVENING_RE = re.compile(
    r"(?<!\w)(?:сменно-?\s*)?вечерн[оа](?:-сменн[оа])?\s+(?:\w+\s+)?(?:училище|гимназия)", re.IGNORECASE
)

# The only school band an evening school offers a child: its pupils are 16 and over.
EVENING_SCHOOL_BANDS = frozenset({"grade_8_12"})


def is_evening_school(name_bg: object) -> bool:
    return bool(_EVENING_RE.search(str(name_bg or "")))


def child_bands(bands: Iterable[str], name_bg: object, education_level: object) -> list[str]:
    """The bands a school can offer a child: an evening school's grades 1-7 are for adults.
    Every stage that writes school bands passes them through here."""
    bands = list(bands)
    if str(education_level or "") == "kindergarten" or not is_evening_school(name_bg):
        return bands
    return [band for band in bands if band in EVENING_SCHOOL_BANDS]
