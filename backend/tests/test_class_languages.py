"""Intensive languages from the official 8th-grade class names."""

from app.utils.class_languages import official_intensive_languages


def _admission(*names, stage="grade_8"):
    return {"cutoff_summary": [{"stage": stage, "class_name": name} for name in names]}


def test_class_names_give_their_intensive_language_only():
    admission = _admission(
        "Чужди езици - НЕ интензивно, ФЕ",
        "Природни науки - ИЕ интензивно, АЕ; БЗО и ХООС",
        "Чужди езици - ИтЕ интензивно, АЕ",
        "Компюрърна графика- интензивно АЕ, втори чужд език- НЕ",
        "Математически - НЕ интензивно, РЕ, М и ФА",
    )
    assert [entry["language"] for entry in official_intensive_languages(admission)] == [
        "German",
        "Spanish",
        "Italian",
        "English",
    ]
    assert {entry["level"] for entry in official_intensive_languages(admission)} == {"enrichment"}


def test_no_classes_or_other_stages_give_nothing():
    assert official_intensive_languages(None) == []
    assert official_intensive_languages(_admission("Чужди езици - НЕ интензивно", stage="grade_1")) == []
    # Lower-case "не интензивно" is "not intensive", not German.
    assert official_intensive_languages(_admission("Изобразително изкуство - не интензивно")) == []
