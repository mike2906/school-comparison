"""Translations of the website and register text a school's page shows as written.

Fee names and age groups come from the school's website, 8th-grade class names from the
РУО lists, and programme, facility and activity items from the website or the old
extraction. They are in one language, so the other language's page showed them as written:
Bulgarian on 13 of 30 English pages in the parent trial (2026-10-19), and English fee
names on the Bulgarian pages of schools with English sites.

The ``translate-texts`` stage (``app.scrapers.stages.text_translations``) translates each
school's shown texts once and stores them in ``attributes["text_translations"]``
(internal, never published as such): ``{"en": {text: translation}, "bg": {...}}``, keyed
by the text as shown with its spaces collapsed. A text the model could not translate is
stored as null, so it is not asked again. The response projections read them here and add
``plan_name_en``/``plan_name_bg`` (fees), ``class_name_en``/``building_en`` (cut-offs) and
the translated list items; the page falls back to the text as written when there is none.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Optional

from app.utils.display_names import translate_bg_school_name
from app.utils.list_translations import is_bulgarian

ATTRIBUTE_KEY = "text_translations"
SCHEMA_VERSION = 1
LOCALES = ("en", "bg")
# A fee's own words the page shows (``PricingResponse``); the agent's English comes first.
PRICE_FIELDS = ("plan_name", "age_group")
# The cut-off lists the API sends (``SchoolAdmissionInfo``).
CUTOFF_LISTS = ("cutoffs", "cutoff_summary")

_LATIN_RE = re.compile(r"[A-Za-z]")


def key(text: Any) -> str:
    return " ".join(str(text or "").split())


def needs_translation(text: Any, locale: str) -> bool:
    """True when ``text`` is in the other language: mostly Cyrillic for the English page,
    Latin letters and no Cyrillic majority for the Bulgarian one."""
    value = key(text)
    if not value:
        return False
    if locale == "en":
        return is_bulgarian(value)
    if locale == "bg":
        return bool(_LATIN_RE.search(value)) and not is_bulgarian(value)
    return False


def stored(attributes: Any) -> dict[str, dict[str, Optional[str]]]:
    """The stored translations by locale; empty when none or of another schema version."""
    block = attributes.get(ATTRIBUTE_KEY) if isinstance(attributes, Mapping) else None
    if not isinstance(block, Mapping) or block.get("_schema_version") != SCHEMA_VERSION:
        return {locale: {} for locale in LOCALES}
    return {
        locale: {
            str(source): (value if isinstance(value, str) and value.strip() else None)
            for source, value in (block.get(locale) or {}).items()
        }
        if isinstance(block.get(locale), Mapping)
        else {}
        for locale in LOCALES
    }


def translation(translations: Mapping[str, Mapping[str, Optional[str]]], text: Any, locale: str) -> Optional[str]:
    """``text`` in ``locale``'s language, or None when it is already in it or has none."""
    if not needs_translation(text, locale):
        return None
    return (translations.get(locale) or {}).get(key(text))


def translate_list(items: list[str], locale: str, translations: Mapping[str, Mapping[str, Optional[str]]]) -> list[str]:
    return [translation(translations, item, locale) or item for item in items]


def price_texts(row: Any) -> dict[str, list[str]]:
    """A fee row's texts that need a translation, by locale (the agent's English counts)."""
    context = _value(row, "pricing_context")
    context = context if isinstance(context, Mapping) else {}
    out: dict[str, list[str]] = {locale: [] for locale in LOCALES}
    for field in PRICE_FIELDS:
        text = _value(row, field)
        if needs_translation(text, "en") and not str(context.get(f"{field}_en") or "").strip():
            out["en"].append(key(text))
        if needs_translation(text, "bg"):
            out["bg"].append(key(text))
    return out


def cutoff_texts(admission_info: Any) -> list[str]:
    """The 8th-grade class names the cut-off lists show in Bulgarian."""
    if not isinstance(admission_info, Mapping):
        return []
    return [
        key(row.get("class_name"))
        for name in CUTOFF_LISTS
        for row in admission_info.get(name) or []
        if isinstance(row, Mapping) and needs_translation(row.get("class_name"), "en")
    ]


def translate_price_context(context: Any, row: Any, translations: Mapping[str, Mapping[str, Optional[str]]]) -> None:
    """Fill a published fee row's ``<field>_en`` (when the agent gave none) and ``<field>_bg``."""
    for field in PRICE_FIELDS:
        text = _value(row, field)
        if not getattr(context, f"{field}_en", None):
            setattr(context, f"{field}_en", translation(translations, text, "en"))
        setattr(context, f"{field}_bg", translation(translations, text, "bg"))


def translate_cutoffs(payload: dict[str, Any], translations: Mapping[str, Mapping[str, Optional[str]]]) -> None:
    """Add ``class_name_en`` and ``building_en`` to the public cut-off rows, in place.

    A building is a kindergarten's own registered name, so it is translated the way school
    names are (``translate_bg_school_name``), not by the model."""
    for name in CUTOFF_LISTS:
        for row in payload.get(name) or []:
            if not isinstance(row, dict):
                continue
            if class_name := translation(translations, row.get("class_name"), "en"):
                row["class_name_en"] = class_name
            building = row.get("building")
            if needs_translation(building, "en") and (english := translate_bg_school_name(building)):
                row["building_en"] = english


def _value(row: Any, field: str) -> Any:
    return row.get(field) if isinstance(row, Mapping) else getattr(row, field, None)
