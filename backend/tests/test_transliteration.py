from app.utils.transliteration import transliterate_address, transliterate_bulgarian
from app.utils.i18n_resolver import derive_english_name, resolve_name_i18n


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


def test_resolve_name_i18n_keeps_bg_display_name_but_derives_english_from_legal_name():
    resolved = resolve_name_i18n(
        {"bg": 'Частно основно училище "Д-р Петър Берон"'},
        {"display_name_i18n": {"bg": "ЧОУ ПЕТЪР БЕРОН", "en": "ЧОУ ПЕТЪР БЕРОН"}},
    )

    assert resolved == {
        "bg": "ЧОУ ПЕТЪР БЕРОН",
        "en": "Dr. Petar Beron",
    }


def test_resolve_name_i18n_handles_malformed_nested_quotes_in_legal_name():
    resolved = resolve_name_i18n(
        {"bg": '"ЧАСТНО ОСНОВНО УЧИЛИЩЕ "Д-Р ПЕТЪР БЕРОН" ЕООД'},
        {"display_name_i18n": {"bg": "ЧОУ ПЕТЪР БЕРОН", "en": "ЧОУ ПЕТЪР БЕРОН"}},
    )

    assert resolved == {
        "bg": "ЧОУ ПЕТЪР БЕРОН",
        "en": "Dr. Petar Beron",
    }


def test_resolve_name_i18n_prefers_non_generic_bg_display_name_for_english_fallback():
    resolved = resolve_name_i18n(
        {"bg": '"ЧАСТНА ДЕТСКА ГРАДИНА НИКАТОР" ЕООД'},
        {"display_name_i18n": {"bg": "НИКАТОР"}},
    )

    assert resolved == {
        "bg": "НИКАТОР",
        "en": "Nikator",
    }
