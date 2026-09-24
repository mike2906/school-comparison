"""School name search: normalisation, type abbreviations, curated aliases and ranking.

Parents write "СМГ", "1 АЕГ", "119 СУ", "СУ 119", "119-то", "№119" or Latin
"sofiyska matematicheska"; registry names read "119 Средно училище ..." or
"Софийска математическа гимназия ...". This module bridges the two.

The frontend list filter (frontend/src/utils/schoolSearch.js) mirrors these rules and
tables; tests/test_school_search.py fails when the two tables drift apart.
"""

import re
from typing import Iterable, Optional

# Official Bulgarian transliteration (Transliteration Act 2009), lowercase only.
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sht", "ъ": "a", "ь": "y", "ю": "yu", "я": "ya",
}

# School-type abbreviations and the word stems they stand for. A query token that is one
# of these matches a name containing the abbreviation as a word or all of its stems, so
# "119 СУ" and "СУ 119" find "119 Средно училище ..." (and "119 ОУ" does not).
TYPE_ABBREVIATIONS: dict[str, tuple[str, ...]] = {
    "су": ("средн", "училищ"),
    "соу": ("средн", "училищ"),  # pre-2016 name, still widely used
    "оу": ("основн", "училищ"),
    "ну": ("начал", "училищ"),
    "дг": ("детск", "градин"),
    "пг": ("гимназ",),  # професионална / профилирана гимназия
    "сеу": ("средн", "езиков", "училищ"),
    "ег": ("езиков", "гимназ"),
    "пег": ("езиков", "гимназ"),
    "аег": ("английск", "гимназ"),
    "нег": ("немск", "гимназ"),
    "фег": ("френск", "гимназ"),
    "иег": ("испанск", "гимназ"),
    "чсу": ("частн", "средн", "училищ"),
    "чоу": ("частн", "основн", "училищ"),
    "чну": ("частн", "начал", "училищ"),
    "чдг": ("частн", "детск", "градин"),
}

# Curated acronyms for well-known Sofia schools whose names have no number or whose
# acronym is not derivable from the type table. Alias (normalised) -> normalised
# substring of the registry name (name_i18n["bg"]). Each target was checked to match
# exactly one Sofia school in the launch DB (2026-09-24); the source is the MoE registry
# abbreviation (attributes.moe_abbreviation) unless noted. Latin spellings
# (SMG, NPMG, 1 AEG, ...) match through transliteration.
SCHOOL_NAME_ALIASES: dict[str, str] = {
    "смг": "софийска математическа гимназия",  # 2216306, MoE "СМГ"
    "нпмг": "национална природо математическа гимназия",  # 2211304, MoE "НПМГ"
    "1 аег": "първа английска езикова гимназия",  # 2216301, MoE "Първа АЕГ"
    "i аег": "първа английска езикова гимназия",
    "първа аег": "първа английска езикова гимназия",
    "2 аег": "втора английска езикова гимназия",  # 2206302, MoE "ІІ АЕГ"
    "ii аег": "втора английска езикова гимназия",
    "втора аег": "втора английска езикова гимназия",
    "нгдек": "национална гимназия за древни езици и култури",  # 2902702, MoE "НГДЕК"
    "нфсг": "национална финансово стопанска гимназия",  # 2211420, MoE "НФСГ"
    "нтбг": "национална търговско банкова гимназия",  # 2224433, MoE "НТБГ"
    "спге": "софийска професионална гимназия по електроника",  # 2205407, MoE "СПГЕ"
    "туес": "технологично училище електронни системи",  # 2213350, MoE "ТУЕС"
    "нму": "национално музикално училище",  # 2902101, MoE "НМУ"
    "нгпи": "национална гимназия за приложни изкуства",  # 2902501, MoE "НГПИ"
    "нуии": "национално училище за изящни изкуства",  # 2902401, MoE "НУИИ"
    "acs": "американски колеж",  # 2213507, attributes.name_aliases "ACS"
}

# Rank tiers (lower is better).
RANK_ALIAS = 0
RANK_NUMBER = 1
RANK_PREFIX = 2
RANK_CONTAINS = 3
RANK_ADDRESS = 4

_NO_MARKER_RE = re.compile(r"(?<![a-z])no\.(?=\s*\d)")
_NON_WORD_RE = re.compile(r"[\W_]+")
_DIGIT_LETTER_RE = re.compile(r"(\d)(?=[^\W\d_])")
_LETTER_DIGIT_RE = re.compile(r"([^\W\d_])(?=\d)")
# "119-то", "1-ва", "2-ри", "100-но", "156-о", "119th" -> the bare number.
_ORDINAL_RE = re.compile(r"(\d+) (?:[вртмн][аиоя]|о|th|st|nd|rd)(?= |$)")
_FIRST_NUMBER_RE = re.compile(r"\d+")


def normalize(text: object) -> str:
    """Lowercase, drop "№"/"No." and punctuation, split digits from letters, drop ordinals."""
    value = str(text or "").lower().replace("№", " ")
    value = _NO_MARKER_RE.sub(" ", value)
    value = _NON_WORD_RE.sub(" ", value)
    value = _DIGIT_LETTER_RE.sub(r"\1 ", value)
    value = _LETTER_DIGIT_RE.sub(r"\1 ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return _ORDINAL_RE.sub(r"\1", value)


def transliterate(text: str) -> str:
    """Latin form of already-normalised text; Latin input is returned unchanged."""
    return "".join(_TRANSLIT.get(char, char) for char in text)


_TYPE_LATIN = {transliterate(key): stems for key, stems in TYPE_ABBREVIATIONS.items()}
_ALIASES_LATIN = [
    (transliterate(alias).split(" "), normalize(target))
    for alias, target in SCHOOL_NAME_ALIASES.items()
]


class _Name:
    __slots__ = ("norm", "latin", "words")

    def __init__(self, value: object):
        self.norm = normalize(value)
        self.latin = transliterate(self.norm)
        self.words = self.norm.split(" ") + self.latin.split(" ")


def _token_in(token: str, name: _Name, *, prefix: bool = False) -> bool:
    latin = transliterate(token)
    if token.isdigit():
        return re.search(rf"(?<!\d){token}(?!\d)", name.norm) is not None
    if latin in _TYPE_LATIN:
        return latin in name.words or all(
            transliterate(stem) in name.latin for stem in _TYPE_LATIN[latin]
        )
    if prefix:
        return any(word.startswith(token) or word.startswith(latin) for word in name.words)
    return token in name.norm or latin in name.latin


def _tokens_rank(tokens: list[str], names: list[_Name]) -> Optional[int]:
    best: Optional[int] = None
    first_number = next((token for token in tokens if token.isdigit()), None)
    for name in names:
        if not name.norm or not all(_token_in(token, name) for token in tokens):
            continue
        name_number = _FIRST_NUMBER_RE.search(name.norm)
        if first_number is not None and name_number and name_number.group() == first_number:
            rank = RANK_NUMBER
        elif all(_token_in(token, name, prefix=True) for token in tokens):
            rank = RANK_PREFIX
        else:
            rank = RANK_CONTAINS
        best = rank if best is None else min(best, rank)
    return best


def _alias_remainders(tokens: list[str], registry_name: str) -> list[list[str]]:
    """Query tokens left over after each curated alias that names this school."""
    latin_tokens = [transliterate(token) for token in tokens]
    remainders = []
    for alias_tokens, target in _ALIASES_LATIN:
        if target not in registry_name:
            continue
        size = len(alias_tokens)
        for start in range(len(tokens) - size + 1):
            if latin_tokens[start:start + size] == alias_tokens:
                remainders.append(tokens[:start] + tokens[start + size:])
    return remainders


def query_tokens(query: str) -> list[str]:
    normalized = normalize(query)
    return normalized.split(" ") if normalized else []


def rank_school(
    tokens: list[str],
    *,
    registry_name: object,
    names: Iterable[object],
    addresses: Iterable[object] = (),
) -> Optional[int]:
    """Rank a school for the query tokens, or None when it does not match.

    ``registry_name`` is name_i18n["bg"] (alias targets refer to it); ``names`` are
    every public name (registry and resolved, all languages).
    """
    if not tokens:
        return None
    name_objs = [_Name(value) for value in names]
    registry = normalize(registry_name)
    for remainder in _alias_remainders(tokens, registry):
        if not remainder or _tokens_rank(remainder, name_objs) is not None:
            return RANK_ALIAS
    rank = _tokens_rank(tokens, name_objs)
    if rank is not None:
        return rank
    if _tokens_rank(tokens, [_Name(value) for value in addresses]) is not None:
        return RANK_ADDRESS
    return None
