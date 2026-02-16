from app.utils.transliteration import transliterate_address, transliterate_bulgarian


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
