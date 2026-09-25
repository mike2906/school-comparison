"""Do two Bulgarian addresses name the same building?

Used where a point found for one record is about to be reused for another: a GeoJSON name
match (the register's address vs the location's) and an official building point shared with
a tenant school. Strict on purpose: a missing pin is safer than a wrong one, so an address
that cannot be compared (no house number, a street on one side only) does not agree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Latin letters that source data uses in place of look-alike Cyrillic ones.
_LATIN_TO_CYRILLIC = str.maketrans("AaBEeKkMHOoPpCcTXxy", "АаВЕеКкМНОоРрСсТХху")
_AREA_MARKER = r"(?:кв|ж\.?\s*к|жк|в\.?\s*з|район|р-н|местност)"
_STREET_MARKER = r"(?:ул|бул|пл|алея)"
# A neighbourhood/district name runs from its marker to the next separator or street marker.
_AREA_RE = re.compile(
    rf"(?<![а-яa-z]){_AREA_MARKER}(?:\.\s*|\s+)(.*?)(?=[,|№(]|(?<![а-яa-z])(?:{_STREET_MARKER}|бл)[.\s]|$)"
)
# A leading/trailing segment that is only a locality and/or a postcode ("1700 СТОЛИЧНА").
_LOCALITY_SEGMENT_RE = re.compile(r"(?:(?:гр|с)\.\s*)?(?:\d{4}\s+)?[^\d]*?(?:\s+\d{4})?")
_WORD_RE = re.compile(r"[а-яa-z]+")
# Address vocabulary and personal titles, which say nothing about which street it is.
_STOPWORDS = {
    "ул", "улица", "бул", "булевард", "пл", "площад", "алея", "гр", "град", "жк", "кв",
    "квартал", "район", "местност", "бл", "блок", "вх", "вход", "ет", "етаж", "ап", "офис",
    "до", "на", "по", "партер", "сграда", "сградата", "корпус", "част", "микрорайон",
    "ген", "генерал", "проф", "професор", "акад", "академик", "полк", "полковник",
    "подполковник", "майор", "капитан", "поручик", "свети", "света", "бивше",
}


@dataclass(frozen=True)
class _Parts:
    areas: frozenset[str]  # neighbourhood names, with their numbers ("младост2")
    street: frozenset[str]  # street name words; "#210" for a numbered street
    numbers: frozenset[str]  # house (or block) numbers


def _parse(address: str) -> _Parts:
    text = address.translate(_LATIN_TO_CYRILLIC).casefold()
    text = re.sub(r"[\"'„“”«»]", " ", text).replace("|", ",")
    segments = [s.strip() for s in text.split(",")]
    # Drop a leading/trailing locality segment ("гр. София", "1700 СТОЛИЧНА") and inline ones.
    while len(segments) > 1 and _is_locality(segments[-1]):
        segments.pop()
    while len(segments) > 1 and _is_locality(segments[0]):
        segments.pop(0)
    text = ", ".join(segments)
    text = re.sub(r"(?<![а-яa-z])(?:гр|с)\.\s*[а-я-]+(?:\s+\d{4})?", " ", text)
    text = re.sub(r"\([^)]*\)?", " ", text)
    # Floors, apartments, entrances, offices.
    text = re.sub(r"(?<![а-яa-z])(?:ет|ап|вх|офис)\.?\s*[0-9а-яa-z]+", " ", text)

    areas = set()
    for match in _AREA_RE.finditer(text):
        name = "".join(
            w for w in re.findall(r"[а-яa-z]+|\d+", match.group(1)) if w not in _STOPWORDS
        )
        if name:
            areas.add(name)
    text = _AREA_RE.sub(" ", text)

    # Numbered streets (ул. 210, ул. "504-та") are names, not house numbers.
    numbered = set(re.findall(rf"(?<![а-яa-z]){_STREET_MARKER}\.?\s*(\d+)(?:\s*-\s*[а-я]+)?", text))
    text = re.sub(rf"(?<![а-яa-z]){_STREET_MARKER}\.?\s*\d+(?:\s*-\s*[а-я]+)?", " ", text)
    ordinals = re.findall(r"(\d+)\s*-\s*(?:ми|ви|ри|ти|та|то|ва|ра|ия|ят)(?![а-я])", text)
    text = re.sub(r"(\d+)\s*-\s*(?:ми|ви|ри|ти|та|то|ва|ра|ия|ят)(?![а-я])", " ", text)
    street = {f"#{n.lstrip('0')}" for n in [*numbered, *ordinals]} | {
        w for w in _WORD_RE.findall(text) if len(w) >= 3 and w not in _STOPWORDS
    }
    marked = re.findall(r"(?:№|\bno\.?|(?<![а-яa-z])бл\.?)\s*(\d+)", text)
    numbers = marked or re.findall(r"(?<!\d)(\d{1,3})(?!\d)", text)
    return _Parts(
        frozenset(areas), frozenset(street), frozenset(n.lstrip("0") or "0" for n in numbers)
    )


def _is_locality(segment: str) -> bool:
    if re.search(rf"(?<![а-яa-z]){_STREET_MARKER}|{_AREA_MARKER}\.|бл\.|№", segment):
        return False
    return bool(_LOCALITY_SEGMENT_RE.fullmatch(segment))


def same_building(first: str, second: str, *, allow_unnumbered: bool = False) -> bool:
    """Whether two addresses give the same street (or neighbourhood) and house number.

    Street names agree when one's words are all in the other ("Средорек" and "Средорек,
    ПГЕХ"); block addresses without a street need the same neighbourhood and block.
    With ``allow_unnumbered``, two addresses without any number agree when they name the
    same street and neighbourhood ("ж.к. Люлин - III микрорайон"): enough when something
    else (the institution's name) already ties the two records together.
    """
    a, b = _parse(first or ""), _parse(second or "")
    if allow_unnumbered and not a.numbers and not b.numbers:
        return (a.street, a.areas) == (b.street, b.areas) and bool(a.street or a.areas)
    if not a.numbers or not b.numbers or not a.numbers & b.numbers:
        return False
    if a.street and b.street:
        return a.street <= b.street or b.street <= a.street
    if not a.street and not b.street:
        return bool(a.areas & b.areas)
    return False


# "№ 9а", "No. 14 Е", "№47-47б" -> the number and an optional building letter. A letter
# after a space counts only when nothing but a separator follows ("№ 2 в сградата" is 2).
_HOUSE = r"(\d+)(?:([а-я])(?![а-я])|\s+([а-я])(?=\s*(?:[,(|]|$)))?"
_MARKED_HOUSE_RE = re.compile(rf"(?:№|(?<![а-яa-z])no\.?)\s*{_HOUSE}")
# The number right after a street name: ул. "Кадемлия" 15, бул. Никола Вапцаров 47.
_STREET_HOUSE_RE = re.compile(
    rf"(?<![а-яa-z]){_STREET_MARKER}\.?\s*[^\d,|№]*?[а-яa-z][^\d,|№]*?,?\s*{_HOUSE}"
)


def house_number(address: str) -> str | None:
    """The house number an address asks for ("15а"), or None.

    Only a marked number (№, No) or the one right after the street name. Block numbers,
    neighbourhood numbers (Младост 4), floors and postcodes are not house numbers.
    """
    text = re.sub(r"[\"'„“”«»]", " ", (address or "").translate(_LATIN_TO_CYRILLIC).casefold())
    text = re.sub(r"\([^)]*\)?", " ", text)
    text = re.sub(r"(?<![а-яa-z])(?:бл|ет|ап|вх|офис)\.?\s*[0-9а-яa-z]+", " ", text)
    text = re.sub(r"(?<![№\d])(?<!№\s)\b\d{4}\b", " ", text)  # postcodes, numbered streets
    match = _MARKED_HOUSE_RE.search(text) or _STREET_HOUSE_RE.search(text)
    if not match:
        return None
    return match.group(1).lstrip("0") + (match.group(2) or match.group(3) or "")


def same_house_number(result_house_number: object, address: str) -> bool:
    """Whether a geocoder's house number is exactly the one the address asks for."""
    wanted = house_number(address)
    if not wanted or not isinstance(result_house_number, str):
        return False
    found = re.match(
        r"\s*(\d+)\s*([а-я])?(?![а-я\d])",
        result_house_number.translate(_LATIN_TO_CYRILLIC).casefold(),
    )
    return bool(found) and found.group(1).lstrip("0") + (found.group(2) or "") == wanted
