"""Clean the free-text lists a school page shows: languages, programmes, facilities.

Runs inside the display projection (``app.utils.school_attributes``), so it applies to
every stored row on the next request and never rewrites ``attributes``.

Languages: the extractor's ``languages`` list is what a school *teaches* (English,
German, ...), not what it teaches *in*. A Bulgarian state school or kindergarten always
teaches in Bulgarian; extra languages are a language focus. Names arrive as "English",
"английски език", "bg" ..., so both lists are mapped to one English name per language
(the frontend translates it). Values that are not a language ("Information
Technologies", "втори чужд език") are dropped.

Programmes and facilities: scraped lists also hold register metadata ("МОН лиценз")
and one-off events ("Волейболен турнир ...", "Седмица на бащата"). Those are dropped.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

INSTRUCTION_LANGUAGE_STATE = "Bulgarian"

# Lower-cased alias → English name. Bulgarian names are the masculine adjective form
# ("английски"); suffixes like " език" / " language" are stripped before lookup.
_LANGUAGE_NAMES: dict[str, tuple[str, ...]] = {
    "English": ("english", "en", "английски", "английский", "англ."),
    "Bulgarian": ("bulgarian", "bg", "български", "бълг."),
    "German": ("german", "de", "немски"),
    "French": ("french", "fr", "френски"),
    "Spanish": ("spanish", "es", "испански"),
    "Russian": ("russian", "ru", "руски"),
    "Italian": ("italian", "it", "италиански"),
    "Chinese": ("chinese", "mandarin", "zh", "китайски"),
    "Japanese": ("japanese", "ja", "японски"),
    "Turkish": ("turkish", "tr", "турски"),
    "Greek": ("greek", "el", "гръцки"),
    "Arabic": ("arabic", "ar", "арабски"),
    "Korean": ("korean", "ko", "корейски"),
    "Romanian": ("romanian", "ro", "румънски"),
    "Hebrew": ("hebrew", "he", "иврит"),
    "Danish": ("danish", "da", "датски"),
    "Norwegian": ("norwegian", "no", "норвежки"),
    "Latin": ("latin", "la", "латински"),
    "Portuguese": ("portuguese", "pt", "португалски"),
    "Polish": ("polish", "pl", "полски"),
    "Czech": ("czech", "cs", "чешки"),
    "Serbian": ("serbian", "sr", "сръбски"),
    "Ukrainian": ("ukrainian", "uk", "украински"),
    "Dutch": ("dutch", "nl", "нидерландски", "холандски"),
    "Swedish": ("swedish", "sv", "шведски"),
}
_LANGUAGE_ALIASES: dict[str, str] = {
    alias: name for name, aliases in _LANGUAGE_NAMES.items() for alias in aliases
}
_LANGUAGE_SUFFIX_RE = re.compile(r"\s+(?:език|езикът|езика|language)$")

# Register metadata, not a programme: "МОН лиценз", "Лицензирана от МОН", "Licensed by
# the Ministry of Education".
_REGISTER_RE = re.compile(
    r"\bмон\b|ministry of education|министерств|\bрегистъра?\b"
    r"|^(?:лиценз|licen[cs])\w*(?:\s+\w+)?$",
    re.IGNORECASE,
)
# One-off events: tournaments, "Седмица на ...", "Ден на ...", festivals, concerts.
_EVENT_RE = re.compile(
    r"турнир|tournament|\bседмица на\b|\bден на\b|\bday of\b|\bweek of\b"
    r"|фестивал|festival|празник|концерт\b|concert\b",
    re.IGNORECASE,
)


def canonical_language(value: Any) -> Optional[str]:
    """The English name of a language value, or None when it is not a known language."""
    if not isinstance(value, str):
        return None
    text = _LANGUAGE_SUFFIX_RE.sub("", value.strip().lower())
    return _LANGUAGE_ALIASES.get(text)


def canonical_languages(values: Iterable[Any]) -> list[str]:
    """Known languages from ``values``, as English names, deduped in order."""
    seen: list[str] = []
    for value in values:
        name = canonical_language(value)
        if name and name not in seen:
            seen.append(name)
    return seen


def instruction_languages(curated: Iterable[Any], school_type: Optional[str]) -> list[str]:
    """The languages a school teaches in.

    A state school or kindergarten teaches in Bulgarian. Otherwise only a curated value
    counts: the scraped language list is what a school teaches, so it is a language focus.
    """
    if school_type == "state":
        return [INSTRUCTION_LANGUAGE_STATE]
    return canonical_languages(curated)


def is_list_noise(text: str, field: str) -> bool:
    """True for a programme/facility/activity item that is not one.

    The event rule skips facilities: "concert hall" is a place, not a concert.
    """
    if _REGISTER_RE.search(text):
        return True
    return field != "facilities" and bool(_EVENT_RE.search(text))
