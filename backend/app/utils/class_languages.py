"""Languages a school teaches intensively, read from its official 8th-grade classes.

RUO class names abbreviate the intensive language: "Чужди езици - НЕ интензивно, АЕ" is
German intensive with English as the second language. A school's facts read can miss
them (73 СУ and 31 СУ listed only Bulgarian), so the published language focus adds them.
"""

import re
from typing import Any

# RUO's abbreviations, as they appear in Sofia's class names.
_LANGUAGE_CODES = {
    "АЕ": "English",
    "НЕ": "German",
    "ФЕ": "French",
    "ИЕ": "Spanish",
    "ИтЕ": "Italian",
    "РЕ": "Russian",
    "ЯЕ": "Japanese",
    "КитЕ": "Chinese",
    "АрЕ": "Arabic",
    "КорЕ": "Korean",
    "РумЕ": "Romanian",
}
_CODE = "|".join(sorted(map(re.escape, _LANGUAGE_CODES), key=len, reverse=True))
# Case-sensitive: lower-case "не интензивно" would mean "not intensive".
_INTENSIVE_RE = re.compile(rf"(?<!\w)(?:({_CODE})\s*-?\s*интензивно|интензивно\s+({_CODE}))(?!\w)")
# How the display projection names an intensive study (app.utils.school_attributes).
INTENSIVE_LEVEL = "enrichment"


def official_intensive_languages(admission_info: Any) -> list[dict[str, str]]:
    """Language focus entries for the intensive languages of the school's 8th-grade classes."""
    rows = admission_info.get("cutoff_summary") if isinstance(admission_info, dict) else None
    languages: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("stage") != "grade_8" or not isinstance(row.get("class_name"), str):
            continue
        for match in _INTENSIVE_RE.finditer(row["class_name"]):
            language = _LANGUAGE_CODES[match.group(1) or match.group(2)]
            if language not in languages:
                languages.append(language)
    return [{"language": language, "level": INTENSIVE_LEVEL} for language in languages]
