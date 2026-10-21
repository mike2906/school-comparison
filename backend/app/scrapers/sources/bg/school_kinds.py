"""Kinds of Bulgarian school the register does not tell apart by type code."""

import re

# "5. Вечерно средно училище", "IV Сменно-вечерна гимназия": registered as средно
# училище or гимназия like any other, but for pupils of 16 and over.
_EVENING_RE = re.compile(r"вечерн", re.IGNORECASE)


def is_evening_school(name_bg: object) -> bool:
    return bool(_EVENING_RE.search(str(name_bg or "")))
