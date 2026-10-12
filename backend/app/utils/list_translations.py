"""Translate programme, facility and activity items a school lists in one language only.

Most school websites list these in Bulgarian only (a few in English only), so the other
locale's page fell back to the untranslated items (site review 2026-10-12, H). The
translations live in ``list_translations.json`` (``bg_to_en`` and ``en_to_bg``, keyed by
the item as served). An item without an entry is shown as it is, so a re-scrape that
brings new wording degrades to the old fallback until the file is refreshed with
``scripts/list_untranslated_items.py``.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterable

_GLOSSARY_PATH = Path(__file__).with_name("list_translations.json")
_CYRILLIC_RE = re.compile(r"[Ѐ-ӿ]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_WHITESPACE_RE = re.compile(r"\s+")


def _key(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


@lru_cache(maxsize=1)
def _glossary() -> dict[str, dict[str, str]]:
    raw = json.loads(_GLOSSARY_PATH.read_text(encoding="utf-8"))
    return {
        direction: {_key(source): target for source, target in entries.items()}
        for direction, entries in raw.items()
    }


def is_bulgarian(text: str) -> bool:
    """Mostly Cyrillic text; the same test as the frontend's ``isCyrillicTag``."""
    cyrillic = len(_CYRILLIC_RE.findall(text))
    return cyrillic > 0 and cyrillic >= len(_LATIN_RE.findall(text))


def needs_translation(text: str, locale: str) -> bool:
    """True when an item is not written in ``locale``'s language."""
    if locale == "en":
        return is_bulgarian(text)
    if locale == "bg":
        return not is_bulgarian(text)
    return False


def _entries(locale: str) -> dict[str, str]:
    direction = {"en": "bg_to_en", "bg": "en_to_bg"}.get(locale)
    return _glossary().get(direction, {}) if direction else {}


def is_untranslated(item: str, locale: str) -> bool:
    """An item in the other language with no glossary entry."""
    return needs_translation(item, locale) and _key(item) not in _entries(locale)


def translate_items(items: Iterable[str], locale: str) -> list[str]:
    """Items in ``locale``'s language, translated where the glossary has an entry."""
    entries = _entries(locale)
    return [
        entries.get(_key(item), item) if needs_translation(item, locale) else item
        for item in items
    ]
