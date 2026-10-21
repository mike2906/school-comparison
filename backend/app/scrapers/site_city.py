"""A website whose own title names another town belongs to another school.

115 ОУ in Sofia's Горни Богров was linked to ousvsvkirilimetodii.com, titled "ОУ "Свети
свети Кирил и Методий" гр.Варна": many schools share a patron's name, so the name check
passed, and the page published the Varna school's facts (parent trial, 2026-10-19). A
school's site names its own town in its title or main heading when it names one at all,
so a title, heading or site name that names another town rejects the site. Body text is
not read: news pages name other towns all the time (competitions, trips, partners).

Only towns in :data:`TOWNS` count. "гр." also abbreviates "група" and "Детски град" is a
kindergarten's name ("ДГ 5 Яслена гр. Калинка", "Детски град Пинокио"), so a capitalised
word after "гр." is not enough. A wrong site from a village or a town missing here is
not caught.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Optional

from bs4 import BeautifulSoup

from app.utils.transliteration import transliterate_bulgarian

# The towns a school in each city may name as its own: Столична община has three towns
# besides Sofia. A city not listed here is not checked.
OWN_TOWNS: dict[str, tuple[str, ...]] = {
    "sofia": ("София", "Банкя", "Нови Искър", "Бухово"),
}

# Bulgaria's towns (district centres and the larger municipal towns), less those whose
# name is also a common word, a person's name or a Sofia district (Елена, Левски, Средец).
TOWNS = (
    "Айтос", "Ардино", "Асеновград", "Балчик", "Банско", "Батак", "Белене", "Белица", "Белово",
    "Берковица", "Благоевград", "Ботевград", "Брацигово", "Брезник", "Бургас", "Бяла Слатина",
    "Варна", "Велики Преслав", "Велико Търново", "Велинград", "Видин", "Враца", "Габрово",
    "Горна Оряховица", "Грамада", "Девин", "Девня", "Дебелец",
    "Димитровград", "Добрич", "Долна Баня", "Драгоман", "Дряново", "Дулово", "Дупница",
    "Елхово", "Етрополе", "Златица", "Златоград", "Ихтиман", "Исперих",
    "Казанлък", "Калофер", "Карлово", "Карнобат", "Каспичан", "Кнежа", "Козлодуй", "Копривщица",
    "Костенец", "Костинброд", "Котел", "Кричим", "Крумовград", "Кубрат", "Кърджали",
    "Кюстендил", "Лом", "Ловеч", "Луковит", "Мадан", "Мездра", "Мелник", "Момчилград",
    "Монтана", "Несебър", "Нова Загора", "Нови Пазар", "Омуртаг", "Оряхово", "Павел Баня",
    "Павликени", "Пазарджик", "Панагюрище", "Перник", "Перущица", "Петрич", "Пещера", "Пирдоп",
    "Плевен", "Пловдив", "Полски Тръмбеш", "Поморие", "Попово", "Правец", "Приморско", "Провадия",
    "Първомай", "Раднево", "Радомир", "Разград", "Разлог", "Ракитово",
    "Русе", "Самоков", "Сандански", "Сапарева Баня", "Свиленград", "Свищов", "Своге", "Севлиево",
    "Силистра", "Симитли", "Сливен", "Сливница", "Смолян", "Созопол", "Сопот",
    "Стара Загора", "Стражица", "Стрелча", "Сунгурларе", "Тетевен",
    "Тервел", "Троян", "Трявна", "Тутракан", "Търговище", "Хасково", "Хисаря", "Харманли",
    "Царево", "Чепеларе", "Червен бряг", "Чипровци", "Чирпан", "Шумен", "Якоруда", "Ямбол",
)

# Towns named after a person a school may have as its patron (74 СУ "Гоце Делчев"): they
# count only after "гр."/"град".
PATRON_TOWNS = ("Генерал Тошево", "Гоце Делчев", "Елин Пелин", "Каблешково", "Раковски", "Стамболийски", "Суворово")

_TOWN_WORD = r"(?:гр\.|град|gr\.|grad)"
_SEGMENT_SPLIT_RE = re.compile(r"\s+[-–—|»:]\s+|\s*[|»]\s*|,\s*")


def _key(text: str) -> str:
    return " ".join(re.sub(r"[-\"'„“”.]", " ", text).casefold().split())


def _spellings(town: str) -> set[str]:
    return {_key(town), _key(transliterate_bulgarian(town))}


_KNOWN_TOWNS = {spelling: town for town in TOWNS for spelling in _spellings(town)}
_PATRON_TOWNS = {spelling: town for town in PATRON_TOWNS for spelling in _spellings(town)}
_TOWN_AFTER_WORD_RE = re.compile(rf"(?<![^\W\d_]){_TOWN_WORD}\s*([^\W\d_]+(?:\s+[^\W\d_]+)?)", re.IGNORECASE)


def identity_texts(soup: BeautifulSoup) -> list[str]:
    """A page's title, each of its first two main headings, and its site name."""
    texts = [soup.title.get_text(" ", strip=True) if soup.title else ""]
    texts += [tag.get_text(" ", strip=True) for tag in soup.find_all("h1")[:2]]
    texts += [str(tag.get("content") or "") for tag in soup.find_all("meta", attrs={"property": "og:site_name"})]
    return [" ".join(text.split()) for text in texts if text and text.strip()]


def _towns_in(text: str) -> list[str]:
    """Known towns the text names: after "гр."/"град", or as a whole part of a title."""
    found = []
    for match in _TOWN_AFTER_WORD_RE.finditer(text):
        words = _key(match.group(1)).split()
        # "гр. Стара Загора" or "гр. Варна, ул. ...": the two words first, then one.
        for candidate in (" ".join(words[:2]), words[0] if words else ""):
            if candidate in _KNOWN_TOWNS or candidate in _PATRON_TOWNS or candidate in _OWN_KEYS:
                found.append(candidate)
                break
    for segment in _SEGMENT_SPLIT_RE.split(text):
        key = _key(segment)
        if key in _KNOWN_TOWNS or key in _OWN_KEYS:
            found.append(key)
    return found


_OWN_KEYS = {spelling for towns in OWN_TOWNS.values() for town in towns for spelling in _spellings(town)}


def known_towns_in(text: str) -> list[str]:
    """The known towns (not the Sofia ones) a page's lines name."""
    return [
        _KNOWN_TOWNS.get(key) or _PATRON_TOWNS[key]
        for line in (text or "").splitlines()
        for key in _towns_in(" ".join(line.split()))
        if key in _KNOWN_TOWNS or key in _PATRON_TOWNS
    ]


def other_town_named(texts: Iterable[Optional[str]], city: Optional[str]) -> Optional[str]:
    """The other town a site's identity texts (:func:`identity_texts`) name, or None.

    ``city`` is the school's city code ("sofia"). Nothing is reported for a city with no
    entry in :data:`OWN_TOWNS`, or when the texts also name the school's own town.
    """
    own_towns = OWN_TOWNS.get(str(city or "").casefold())
    if not own_towns:
        return None
    own = {spelling for town in own_towns for spelling in _spellings(town)}
    named = [key for text in texts for key in _towns_in(" ".join(str(text or "").split()))]
    if any(key in own for key in named):
        return None
    others = [_KNOWN_TOWNS.get(key) or _PATRON_TOWNS[key] for key in named if key in _KNOWN_TOWNS or key in _PATRON_TOWNS]
    return others[0] if others else None
