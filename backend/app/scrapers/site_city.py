"""A website whose own title names another town belongs to another school.

115 ОУ in Sofia's Горни Богров was linked to ousvsvkirilimetodii.com, titled "ОУ "Свети
свети Кирил и Методий" гр.Варна": many schools share a patron's name, so the name check
passed, and the page published the Varna school's facts (parent trial, 2026-10-19). A
school's site names its own town in its title or main heading when it names one at all,
so a title, heading or site name that names another town rejects the site. Body text is
not read: news pages name other towns all the time (competitions, trips, partners).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Optional

from app.utils.transliteration import transliterate_bulgarian

# The towns a school in each city may name as its own: Столична община has three towns
# besides Sofia. A city not listed here is not checked.
OWN_TOWNS: dict[str, tuple[str, ...]] = {
    "sofia": ("софия", "банкя", "нови искър", "бухово"),
}

# Bulgaria's larger towns, for a title that names one without "гр." ("... - Варна").
_TOWNS = (
    "Айтос", "Асеновград", "Благоевград", "Ботевград", "Бургас", "Варна", "Велико Търново",
    "Велинград", "Видин", "Враца", "Габрово", "Гоце Делчев", "Горна Оряховица", "Димитровград",
    "Добрич", "Дупница", "Казанлък", "Карлово", "Карнобат", "Кърджали", "Кюстендил", "Лом",
    "Ловеч", "Монтана", "Нова Загора", "Пазарджик", "Панагюрище", "Перник", "Петрич", "Плевен",
    "Пловдив", "Поморие", "Попово", "Провадия", "Разград", "Русе", "Самоков", "Сандански",
    "Свиленград", "Свищов", "Севлиево", "Силистра", "Сливен", "Смолян", "Стара Загора",
    "Троян", "Търговище", "Харманли", "Хасково", "Чирпан", "Шумен", "Ямбол",
)

_LETTER = r"[^\W\d_]"
# "гр. Варна", "гр.Варна", "град Варна", "gr. Varna"; a second capitalised word for
# "Стара Загора". The town starts with a capital, so "град" in "детска градина" or
# "градски" never matches.
_PREFIXED_TOWN_RE = re.compile(
    rf"(?<!{_LETTER})(?:гр\.|град|gr\.|grad)\s*(?P<town>[A-ZА-Я]{_LETTER}+(?:[ -][A-ZА-Я]{_LETTER}+)?)"
)
_SEGMENT_SPLIT_RE = re.compile(r"\s+[-–—|»:]\s+|\s*[|»]\s*|,\s*")


def _key(text: str) -> str:
    return " ".join(text.casefold().replace("-", " ").split())


def _spellings(town: str) -> set[str]:
    return {_key(town), _key(transliterate_bulgarian(town))}


_KNOWN_TOWNS = {spelling: town for town in _TOWNS for spelling in _spellings(town)}


def _is_own(name: str, own: set[str]) -> bool:
    key = _key(name)
    return any(key == town or key.startswith(f"{town} ") for town in own)


def other_town_named(identity_texts: Iterable[Optional[str]], city: Optional[str]) -> Optional[str]:
    """The other town a site's title, main heading or site name names, or None.

    ``identity_texts`` are those texts; ``city`` is the school's city code ("sofia").
    A town named with "гр."/"град" counts whatever it is; a town named alone counts only
    when it is a whole part of the title ("Начало - Варна") and one of the larger towns.
    Nothing is reported for a city with no entry in :data:`OWN_TOWNS`, or when the texts
    also name the school's own town.
    """
    own_names = OWN_TOWNS.get(str(city or "").casefold())
    if not own_names:
        return None
    own = {spelling for town in own_names for spelling in _spellings(town)}
    others: list[str] = []
    for text in identity_texts:
        text = " ".join(str(text or "").split())
        if not text:
            continue
        for match in _PREFIXED_TOWN_RE.finditer(text):
            town = match.group("town")
            if _is_own(town, own):
                return None
            others.append(town)
        for segment in _SEGMENT_SPLIT_RE.split(text):
            key = _key(segment.strip(" .\"'„“”"))
            if key in own:
                return None
            if key in _KNOWN_TOWNS:
                others.append(_KNOWN_TOWNS[key])
    return others[0] if others else None
