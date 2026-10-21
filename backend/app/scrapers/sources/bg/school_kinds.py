"""Kinds of Bulgarian school the register does not tell apart by type code."""

import re

# "5. Вечерно средно училище", "IV Сменно-вечерна гимназия": registered as средно
# училище or гимназия like any other, but for pupils of 16 and over. Not "Вечерница".
_EVENING_RE = re.compile(r"(?<!\w)(?:сменно-?\s*)?вечерн[оа]\s+(?:средно\s+)?(?:училище|гимназия)", re.IGNORECASE)

# The only school band an evening school offers a child: its pupils are 16 and over.
EVENING_SCHOOL_BANDS = frozenset({"grade_8_12"})


def is_evening_school(name_bg: object) -> bool:
    return bool(_EVENING_RE.search(str(name_bg or "")))
