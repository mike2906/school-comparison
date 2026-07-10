"""Map free-text extraction values onto the advanced-filter's controlled vocabulary.

The extractor emits free text for facilities/programs ("Медицински кабинет",
"3D printers", "STEM център"), but the advanced-filter UI
(``SearchPage.DEFAULT_ADVANCED_OPTIONS``) offers a fixed set of canonical option keys
(``cafeteria``, ``library``, ``sports_program`` …). The two never intersected, so those
filters only ever matched seeded schools (P1.9).

This module is the single source of truth for that vocabulary and the deterministic
free-text → canonical-tag mapping. It runs at the publish boundary inside
``build_filterable_attributes`` (so the ``/schools`` matcher and ``/schools/filters``
agree), not by rewriting ``attributes`` — the free text stays intact for display.

Matching is deliberately conservative substring/exact matching over normalized
(lowercased, whitespace-collapsed) text in both Bulgarian and English. Recall is
imperfect on purpose: the goal is a plausible, non-empty intersection, not a perfect
classifier. The canonical tag sets here MUST stay in sync with
``frontend/src/components/SearchPage/SearchPage.jsx`` ``DEFAULT_ADVANCED_OPTIONS``.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize(value: object) -> str:
    return _WHITESPACE_RE.sub(" ", str(value)).strip().lower()


# Each canonical tag maps to matcher rules:
#   "contains" — normalized value contains any of these substrings
#   "equals"   — normalized value equals one of these exactly (for short/ambiguous
#                stems like "стол" or "ib" that are unsafe as substrings)
#   "prefix"   — matched at a word boundary with any suffix (\b<stem>), for stems that
#                are a substring of an unrelated word mid-word but should still match
#                their own inflections: "спорт"/"sport" matches "спортна"/"sports" but
#                not "транспорт"/"transport"; "стем"/"stem" matches "STEM" but not
#                "система"/"system"; "хран" matches "хранене" but not "охрана".
_Rule = Mapping[str, tuple[str, ...]]

FACILITY_VOCAB: dict[str, _Rule] = {
    "sports_facilities": {
        "contains": (
            "физкултур", "спортна площадк", "спортни площадк", "спортна зала",
            "спортен комплекс", "спортна база", "футболно игрище", "футболен",
            "стадион", "фитнес", "стена за катерене", "плувен басейн", "басейн",
            "gymnasium", "sports hall", "sports field", "sports ground",
            "swimming pool", "climbing wall", "fitness",
        ),
        "equals": ("gym", "pool"),
    },
    "cafeteria": {
        "contains": ("столов", "cafeteria", "canteen", "dining", "бюфет", "лавка"),
        "equals": ("стол",),
    },
    "library": {
        "contains": ("библиотек", "library", "читалн"),
        "equals": (),
    },
    "computer_lab": {
        "contains": (
            "компютър", "computer lab", "computer room", "computer classroom",
            "кабинет по информационни технологии", "informatics lab", "ict lab",
        ),
        # "стем"/"stem" are substrings of "система"/"system"; match at a word boundary.
        "prefix": ("стем", "stem"),
        "equals": (),
    },
    "transportation": {
        "contains": ("транспорт", "transport", "автобус", "school bus", "шатъл", "shuttle"),
        "equals": ("bus",),
    },
}

PROGRAM_VOCAB: dict[str, _Rule] = {
    "music_program": {
        "contains": (
            "музик", "music", "вокал", "пиано", "piano", "цигулк", "оркест",
            "orchestr", "солфеж", "пеене", "инструментал", "choir",
        ),
        "equals": ("хор",),
    },
    "sports_program": {
        "contains": (
            "футбол", "football", "soccer", "волейбол", "volleyball",
            "баскетбол", "basketball", "плуван", "swimming", "тенис", "tennis",
            "таекуондо", "taekwondo", "джудо", "judo", "карате", "karate", "йога",
            "yoga", "гимнастик", "gymnastics", "атлетик", "athletic", "катерене",
            "climbing",
        ),
        # "спорт"/"sport" are substrings of "транспорт"/"transport"; match at a boundary.
        "prefix": ("спорт", "sport"),
        "equals": (),
    },
    "arts_program": {
        "contains": (
            "изкуств", "изобразител", "танц", "dance", "хореограф", "балет", "ballet",
            "рисуван", "painting", "drawing", "театр", "theatr", "керамик", "ceramic",
            "приложни дейности",
        ),
        "equals": ("art", "arts"),
    },
    "extended_day": {
        "contains": (
            "целодневн", "extended day", "after school", "after-school",
            "занимания по интереси", "организирани групи", "продължен ден",
            "day care", "daycare",
        ),
        "equals": (),
    },
    "meals_provided": {
        "contains": ("кетъринг", "catering", "meal", "обяд", "lunch", "закуск", "столов"),
        # "хран" matches "храна"/"хранене" but not "охрана" (security) / "съхранение".
        "prefix": ("хран",),
        "equals": ("стол",),
    },
}

APPROACH_VOCAB: dict[str, _Rule] = {
    "montessori": {"contains": ("montessori", "монтесори"), "equals": ()},
    "waldorf": {"contains": ("waldorf", "валдорф", "щайнер", "steiner"), "equals": ()},
    "ib_program": {
        "contains": (
            "international baccalaureate", "ib diploma", "ib world", "ib program",
            "ib програма", "primary years programme", "дипломна програма",
        ),
        "equals": ("ib",),
    },
    "project_based": {
        # "based learning" alone is too loose ("play-based learning" is not project-based);
        # require an explicit project reference.
        "contains": (
            "project based", "project-based", "project-based learning", "проектно",
            "проектно базирано", "проектно-базиран", "проектно обучение",
        ),
        "equals": (),
    },
}


def _matches(normalized: str, rule: _Rule) -> bool:
    if normalized in rule.get("equals", ()):
        return True
    if any(needle in normalized for needle in rule.get("contains", ())):
        return True
    return any(
        re.search(rf"\b{re.escape(stem)}", normalized) for stem in rule.get("prefix", ())
    )


def canonical_tags(values: Iterable[object], vocab: Mapping[str, _Rule]) -> list[str]:
    """Return the sorted canonical tags matched by any of ``values``.

    ``values`` are free-text strings from the extraction projection; ``vocab`` is one of
    ``FACILITY_VOCAB`` / ``PROGRAM_VOCAB`` / ``APPROACH_VOCAB``. Already-canonical inputs
    (e.g. seeded ``"cafeteria"``) pass through because the tag key is itself a match
    substring.
    """
    matched: set[str] = set()
    for value in values:
        normalized = _normalize(value)
        if not normalized:
            continue
        for tag, rule in vocab.items():
            if tag in matched:
                continue
            # An already-canonical value (seeded "sports_facilities", "extended_day")
            # passes through even when the tag key is not one of its match substrings.
            if normalized == tag or _matches(normalized, rule):
                matched.add(tag)
    return sorted(matched)
