from app.utils.transliteration import transliterate_address, transliterate_bulgarian
from app.utils.i18n_resolver import derive_english_name, resolve_address_i18n, resolve_name_i18n


CORROBORATED_DISPLAY = {
    "display_name_evidence": {
        "signals": ["website_domain_alias_match", "repeated_on_page_identity"],
        "status": "corroborated",
    }
}


def test_transliterate_bulgarian_basic():
    assert transliterate_bulgarian("София") == "Sofia"
    assert transliterate_bulgarian("СОФИЯ") == "SOFIA"
    assert transliterate_bulgarian("ДГ №25 Изворче") == "DG №25 Izvorche"
    assert transliterate_bulgarian("ЧАСТНА") == "CHASTNA"
    assert transliterate_bulgarian("Частна") == "Chastna"
    assert transliterate_bulgarian("България") == "Bulgaria"


def test_transliterate_address_preserves_abbreviations():
    address = 'гр. София, ж.к. "Дружба", ул. "5016", № 3'
    result = transliterate_address(address)
    assert result.startswith('gr. Sofia, zh.k. "Druzhba", ul. "5016"')


def test_derive_english_name_normalizes_common_honorifics():
    assert derive_english_name("Д-р Петър Берон") == "Dr. Petar Beron"
    assert derive_english_name("Св. Климент Охридски") == "St. Kliment Ohridski"


def test_derive_english_name_preserves_short_uppercase_acronyms():
    assert derive_english_name("ЕСПА") == "ESPA"


def test_derive_english_name_translates_common_institution_types():
    assert derive_english_name('21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"') == '21 Secondary School "Hristo Botev"'
    assert derive_english_name('145 Основно училище "Симеон Радев"') == '145 Primary School "Simeon Radev"'
    assert derive_english_name('Детска градина "Звездичка"') == 'Kindergarten "Zvezdichka"'


def test_resolve_name_i18n_keeps_bg_display_name_but_derives_english_from_legal_name():
    resolved = resolve_name_i18n(
        {"bg": 'Частно основно училище "Д-р Петър Берон"'},
        {
            "display_name_i18n": {"bg": "ЧОУ ПЕТЪР БЕРОН", "en": "ЧОУ ПЕТЪР БЕРОН"},
            **CORROBORATED_DISPLAY,
        },
    )

    assert resolved == {
        "bg": "ЧОУ ПЕТЪР БЕРОН",
        "en": "Dr. Petar Beron",
    }


def test_resolve_name_i18n_handles_malformed_nested_quotes_in_legal_name():
    resolved = resolve_name_i18n(
        {"bg": '"ЧАСТНО ОСНОВНО УЧИЛИЩЕ "Д-Р ПЕТЪР БЕРОН" ЕООД'},
        {
            "display_name_i18n": {"bg": "ЧОУ ПЕТЪР БЕРОН", "en": "ЧОУ ПЕТЪР БЕРОН"},
            **CORROBORATED_DISPLAY,
        },
    )

    assert resolved == {
        "bg": "ЧОУ ПЕТЪР БЕРОН",
        "en": "Dr. Petar Beron",
    }


def test_resolve_name_i18n_prefers_non_generic_bg_display_name_for_english_fallback():
    resolved = resolve_name_i18n(
        {"bg": '"ЧАСТНА ДЕТСКА ГРАДИНА НИКАТОР" ЕООД', "en": '"Chastna detska gradina Nikator" EOOD'},
        {"display_name_i18n": {"bg": "НИКАТОР"}, **CORROBORATED_DISPLAY},
    )

    assert resolved == {
        "bg": "НИКАТОР",
        "en": "Nikator",
    }


def test_resolve_name_i18n_does_not_prefer_raw_legal_english_when_display_bg_exists():
    resolved = resolve_name_i18n(
        {"bg": '"ЧАСТНА ДЕТСКА ГРАДИНА "ДЕТСКА МЕЧТА" ООД', "en": '"Chastna detska gradina "Detska mechta" OOD'},
        {"display_name_i18n": {"bg": "Детска мечта"}, **CORROBORATED_DISPLAY},
    )

    assert resolved == {
        "bg": "Детска мечта",
        "en": "Detska mechta",
    }


def test_resolve_name_i18n_prefers_clean_display_brand_for_english_alignment():
    resolved = resolve_name_i18n(
        {"bg": '"ЧАСТНА ПРОФЕСИОНАЛНА ГИМНАЗИЯ ПО ПРОГРАМИРАНЕ И РОБОТИКА "СТИВ ДЖОБС" ЕООД'},
        {"display_name_i18n": {"bg": "СофтУни БУДИТЕЛ"}, **CORROBORATED_DISPLAY},
    )

    assert resolved == {
        "bg": "СофтУни БУДИТЕЛ",
        "en": "SoftUni BUDITEL",
    }


def test_resolve_name_i18n_strips_generic_german_school_prefix_for_english():
    resolved = resolve_name_i18n(
        {"bg": '"Частна немска гимназия Ерих Кестнер" ООД'},
        {"display_name_i18n": {"bg": "немска гимназия Ерих Кестнер"}, **CORROBORATED_DISPLAY},
    )

    assert resolved == {
        "bg": "немска гимназия Ерих Кестнер",
        "en": "Erih Kestner",
    }


def test_resolve_name_i18n_strips_legal_suffixes_when_no_display_name_exists():
    resolved = resolve_name_i18n(
        {"bg": '"Частно основно училище с изучаване на английски език "Меридиан 22" ЕООД'},
    )

    assert resolved == {
        "bg": "Меридиан 22",
        "en": "Meridian 22",
    }


def test_resolve_name_i18n_strips_association_suffix_when_no_display_name_exists():
    resolved = resolve_name_i18n(
        {"bg": '"ЧАСТНО ОСНОВНО УЧИЛИЩЕ МИЛЕА" Сдружение'},
    )

    assert resolved == {
        "bg": "МИЛЕА",
        "en": "MILEA",
    }


def test_resolve_name_i18n_preserves_clean_raw_english_when_no_display_name_exists():
    resolved = resolve_name_i18n(
        {"bg": "Американски международен колеж", "en": "American International College"},
    )

    assert resolved == {
        "bg": "Американски международен колеж",
        "en": "American International College",
    }


def test_resolve_name_i18n_ignores_generic_numbered_school_display_label():
    resolved = resolve_name_i18n(
        {"bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"'},
        {"display_name_i18n": {"bg": "21. СУ"}, **CORROBORATED_DISPLAY},
    )

    assert resolved == {
        "bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"',
        "en": '21 Secondary School "Hristo Botev"',
    }


def test_resolve_name_i18n_ignores_uncorroborated_display_name():
    resolved = resolve_name_i18n(
        {"bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"'},
        {"display_name_i18n": {"bg": "Прием след 7. клас", "en": "Admissions news"}},
    )

    assert resolved == {
        "bg": '21 СРЕДНО УЧИЛИЩЕ "ХРИСТО БОТЕВ"',
        "en": '21 Secondary School "Hristo Botev"',
    }


def test_resolve_name_i18n_rejects_corroborated_page_prose():
    legal_name = {"bg": 'Частно средно училище "Свети Наум"'}
    legal_fallback = resolve_name_i18n(legal_name)
    bad_labels = (
        "2016 © Частно езиково училище Свети Наум. Всички права запазени.",
        "Приемът в английска частна детска градина бива два вида:",
        "Ръководство на частно средно училище Джани Родари",
        "Нашето семейство включва Първа Частна Математическа Гимназия",
        "Our Values At Deni Diderot School",
        "Да бъдеш преподавател в училище Маариф",
        "Email: school@example.com",
        "KITA - Частна детска градина в ж.к. Драгалевци - Частна детска ясла-Detska gradina",
    )

    for label in bad_labels:
        resolved = resolve_name_i18n(
            legal_name,
            {"display_name_i18n": {"bg": label, "en": label}, **CORROBORATED_DISPLAY},
        )
        assert resolved == legal_fallback


def test_resolve_address_i18n_derives_english_without_persisting_it():
    resolved = resolve_address_i18n(
        {"bg": 'бул. "Джеймс Баучер" № 116, ет. 1, ап. 4'},
    )

    assert resolved == {
        "bg": 'бул. "Джеймс Баучер" № 116, ет. 1, ап. 4',
        "en": 'bul. "Dzheyms Baucher" № 116, et. 1, ap. 4',
    }
