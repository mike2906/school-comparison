"""Glossary translation of programme/facility/activity lists (site review 2026-10-12, H)."""

import json

import pytest

from app.utils import list_translations
from app.utils.list_translations import is_untranslated, translate_items
from app.utils.school_attributes import build_localized_attributes

GLOSSARY = {
    "bg_to_en": {"Физкултурен салон": "Gym", "Библиотека": "Library"},
    "en_to_bg": {"Suggestopedic method of teaching": "Сугестопедичен метод на обучение"},
}


@pytest.fixture
def glossary(monkeypatch):
    monkeypatch.setattr(
        list_translations,
        "_glossary",
        lambda: {
            direction: {list_translations._key(k): v for k, v in entries.items()}
            for direction, entries in GLOSSARY.items()
        },
    )


@pytest.mark.usefixtures("glossary")
class TestTranslateItems:
    def test_translates_other_language_items_case_insensitively(self):
        assert translate_items(["физкултурен  салон", "Library"], "en") == ["Gym", "Library"]
        assert translate_items(["Suggestopedic method of teaching"], "bg") == [
            "Сугестопедичен метод на обучение"
        ]

    def test_keeps_items_without_an_entry(self):
        assert translate_items(["Басейн", "STEM"], "en") == ["Басейн", "STEM"]
        assert translate_items(["STEM"], "bg") == ["STEM"]

    def test_is_untranslated(self):
        assert is_untranslated("Басейн", "en")
        assert not is_untranslated("Библиотека", "en")
        assert not is_untranslated("Library", "en")

    def test_projection_translates_and_dedupes(self):
        # The English page used to show the Bulgarian-only list as is.
        attrs = {"extracted": {"facilities": ["Библиотека", "Физкултурен салон", "Басейн"]}}
        assert build_localized_attributes(attrs, "en")["facilities"] == [
            "Library",
            "Gym",
            "Басейн",
        ]
        assert build_localized_attributes(attrs, "bg")["facilities"] == [
            "Библиотека",
            "Физкултурен салон",
            "Басейн",
        ]
        mixed = {"extracted": {"facilities": ["Library", "Библиотека"]}}
        assert build_localized_attributes(mixed, "en")["facilities"] == ["Library"]


def test_glossary_file_is_well_formed():
    raw = json.loads(list_translations._GLOSSARY_PATH.read_text(encoding="utf-8"))
    assert set(raw) == {"bg_to_en", "en_to_bg"}
    for direction, entries in raw.items():
        for source, target in entries.items():
            assert isinstance(target, str) and target.strip(), (direction, source)
            # An entry must be in the direction its section says.
            assert list_translations.needs_translation(
                source, "en" if direction == "bg_to_en" else "bg"
            ), (direction, source)
