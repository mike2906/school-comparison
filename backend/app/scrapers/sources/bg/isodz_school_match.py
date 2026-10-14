"""Match ИСОДЗ (kg.sofia.bg) school institutions to our schools by the Sofia school number.

kg.sofia.bg lists 1st-grade schools and school preparatory groups (ПГУ) under its own ids,
which only kindergartens carry in ``attributes.kg_sofia_id(s)``. Sofia's municipal schools
are numbered ("61. ОУ ...") and their НЕИСПУО code is 22DDNNN with NNN the school number
(2203061 = 61. ОУ), so the number picks the school; a shared word of the patron's name
("Кирил", "Ковачев") confirms it. Anything ambiguous stays unmatched.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

_NUMBER_RE = re.compile(r"^\s*(\d{1,3})\b")
# Latin letters that ИСОДЗ names sometimes carry inside Cyrillic words ("Kовачев").
_LATIN_TO_CYRILLIC = str.maketrans("aceiopxykmhbt", "асеіорхукмнвт")
_WORD_RE = re.compile(r"[а-яіѝ]+")
_STOP_WORDS = {
    "основно", "средно", "училище", "начално", "обединено", "свети", "света", "академик",
    "професор", "генерал", "езиково", "езикова", "гимназия", "изучаване", "чужди", "езици",
    "организация", "полудневна", "целодневна", "сграда",
}


def school_number(name: Optional[str]) -> Optional[int]:
    match = _NUMBER_RE.match(name or "")
    return int(match.group(1)) if match else None


def _words(name: Optional[str]) -> set[str]:
    text = (name or "").lower().translate(_LATIN_TO_CYRILLIC)
    return {w for w in _WORD_RE.findall(text) if len(w) >= 4 and w not in _STOP_WORDS}


def _code_number(institutional_id: Optional[str]) -> Optional[int]:
    code = str(institutional_id or "")
    if len(code) == 7 and code.startswith("22") and code.isdigit():
        return int(code[-3:])
    return None


def number_index(schools: Iterable[Any]) -> dict[int, list[Any]]:
    """School number -> Sofia-city state schools whose НЕИСПУО code ends in it."""
    index: dict[int, list[Any]] = {}
    for school in schools:
        if getattr(school, "school_type", None) != "state" or getattr(school, "education_level", None) == "kindergarten":
            continue
        number = _code_number(school.institutional_id)
        if number is not None:
            index.setdefault(number, []).append(school)
    return index


def match_school(institution_name: Optional[str], index: dict[int, list[Any]]) -> Optional[Any]:
    number = school_number(institution_name)
    if number is None:
        return None
    candidates = index.get(number, [])
    if len(candidates) != 1:
        return None
    school = candidates[0]
    wanted = _words(institution_name)
    if not wanted:  # e.g. '204. ОУ ""': the number is all there is
        return school
    have = _words((school.name_i18n or {}).get("bg"))
    return school if wanted & have else None
