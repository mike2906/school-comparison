"""Intensive languages from the official 8th-grade class names."""

from app.utils.school_attributes import build_localized_attributes
from app.utils.school_facts import publication_attributes
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


def _focus(attributes, admission):
    projected = build_localized_attributes(publication_attributes(attributes, "accepted", None, "upper_secondary", admission), "en", "state")
    return [(entry["language"], entry.get("level")) for entry in projected["language_focus"]]


def test_official_languages_survive_without_site_data_and_skip_a_language_already_named():
    admission = _admission("Чужди езици - НЕ интензивно, АЕ", "Чужди езици - АЕ интензивно, НЕ")
    # No site data at all (or a site reading the gate withholds): the RUO classes still count.
    assert _focus(None, admission) == [("German", "enrichment"), ("English", "enrichment")]
    # The site's own reading of English wins; German is added.
    assert _focus({"language_focus": [{"language": "English", "level": "bilingual"}]}, admission) == [
        ("English", "bilingual"),
        ("German", "enrichment"),
    ]


def test_a_code_run_into_the_word_is_not_a_language():
    assert official_intensive_languages(_admission("Изкуства - НЕинтензивно")) == []
