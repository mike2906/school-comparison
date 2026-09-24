"""UF34/UF37: display cleanup of register-formatted names and addresses.

Inputs are real stored values from the Sofia launch DB; stored data is never rewritten,
only the derived ``resolved_*`` display values.
"""

import pytest

from app.utils.display_names import tidy_bg_address, tidy_bg_name, translate_bg_school_name
from app.utils.i18n_resolver import resolve_address_i18n, resolve_name_i18n


@pytest.mark.parametrize(
    ("stored_bg", "expected_bg", "expected_en"),
    [
        # ALL-CAPS register names; type words translated, ordinal numbers in English.
        (
            '94 СРЕДНО УЧИЛИЩЕ "Димитър Страшимиров"',
            "94 Средно училище „Димитър Страшимиров“",
            '94th Secondary School "Dimitar Strashimirov"',
        ),
        (
            '32. СРЕДНО УЧИЛИЩЕ С ИЗУЧАВАНЕ НА ЧУЖДИ  ЕЗИЦИ "СВЕТИ КЛИМЕНТ ОХРИДСКИ"',
            "32. Средно училище с изучаване на чужди езици „Свети Климент Охридски“",
            '32nd Secondary School with foreign languages "St. Kliment Ohridski"',
        ),
        (
            '152 ОСНОВНО УЧИЛИЩЕ "СВ.СВ. КИРИЛ И МЕТОДИЙ"',
            "152 Основно училище „Св. св. Кирил и Методий“",
            '152nd Primary School "Sts. Cyril and Methodius"',
        ),
        (
            '91.НЕМСКА ЕЗИКОВА ГИМНАЗИЯ ,,Професор Константин Гълъбов"',
            "91. Немска езикова гимназия „Професор Константин Гълъбов“",
            '91st German Language High School "Prof. Konstantin Galabov"',
        ),
        (
            '138 СРЕДНО УЧИЛИЩЕ за западни и източни езици "ПРОФ. ВАСИЛ ЗЛАТАРСКИ"',
            "138 Средно училище за западни и източни езици „Проф. Васил Златарски“",
            '138th Secondary School of Western and Eastern Languages "Prof. Vasil Zlatarski"',
        ),
        # Mixed-case names: whitespace, stray spaces inside quotes, Latin look-alikes.
        (
            '69 Средно  училище "Димитър Маринов"',
            "69 Средно училище „Димитър Маринов“",
            '69th Secondary School "Dimitar Marinov"',
        ),
        (
            '75 ОСНОВНО УЧИЛИЩЕ  " ТОДОР КАБЛЕШКОВ "',
            "75 Основно училище „Тодор Каблешков“",
            '75th Primary School "Todor Kableshkov"',
        ),
        (
            '123 Средно училищe "Стефан Стамболов"',  # Latin "e" typed in the register
            "123 Средно училище „Стефан Стамболов“",
            '123rd Secondary School "Stefan Stambolov"',
        ),
        (
            "Първа английска езикова гимназия",
            "Първа английска езикова гимназия",
            "First English Language High School",
        ),
        (
            'Втора английска езикова гимназия  "Томас Джеферсън"',
            "Втора английска езикова гимназия „Томас Джеферсън“",
            'Second English Language High School "Tomas Dzhefersan"',
        ),
        (
            'Професионална гимназия  по туризъм "Алеко Константинов"',
            "Професионална гимназия по туризъм „Алеко Константинов“",
            'Vocational High School of Tourism "Aleko Konstantinov"',
        ),
        (
            'Софийска математическа гимназия  "Паисий Хилендарски"',
            "Софийска математическа гимназия „Паисий Хилендарски“",
            'Sofia High School of Mathematics "Paisiy Hilendarski"',
        ),
        (
            '9 Френска езикова гимназия  "Алфонс дьо Ламартин"',
            "9 Френска езикова гимназия „Алфонс дьо Ламартин“",
            '9th French Language High School "Alfons dyo Lamartin"',
        ),
        (
            "ДГ №31 Люлин (с яслени групи)",
            "ДГ №31 Люлин (с яслени групи)",
            "Kindergarten No. 31 Lyulin (with nursery groups)",
        ),
        (
            'ДЪРЖАВНА ДЕТСКА ГРАДИНА  "СРЕДЕЦ" КЪМ МИНИСТЕРСТВО НА ОТБРАНАТА',
            "Държавна детска градина „Средец“ към Министерство на отбраната",
            'State Kindergarten "Sredets" (Ministry of Defence)',
        ),
        # Legal-form suffixes and prefixes, wrapping and unbalanced quotes.
        (
            '"ЧАСТНА ДЕТСКА ГРАДИНА ГЕРМАНИ БГ" ЕАД',
            "Германи БГ",
            "Germani BG",
        ),
        (
            '"ЧАСТНА ДЕТСКА ГРАДИНА "ПРОЛЕТ" ООД',
            "Пролет",
            "Prolet",
        ),
        (
            'ФОНДАЦИЯ "ЧАСТНО СРЕДНО УЧИЛИЩЕ "РОНАЛД ЛАУДЕР"',
            "Частно средно училище „Роналд Лаудер“",
            'Private Secondary School "Ronald Lauder"',
        ),
        (
            '"ЧАСТНА ПРОФЕСИОНАЛНА ГИМНАЗИЯ ПО ИКОНОМИКА, ТУРИЗЪМ И ИНФОРМАТИКА БУЛПРОГРЕС" ЕООД',
            "Частна професионална гимназия по икономика, туризъм и информатика Булпрогрес",
            "Private Vocational High School of Economics, Tourism and Informatics Bulprogres",
        ),
        (
            "АМЕРИКАНСКИ КОЛЕЖ В  СОФИЯ",
            "Американски колеж в София",
            "American College in Sofia",
        ),
        (
            "ПЪРВА ЧАСТНА МАТЕМАТИЧЕСКА ГИМНАЗИЯ",
            "Първа частна математическа гимназия",
            "First Private High School of Mathematics",
        ),
        # A lone caps brand in a mixed-case name is kept; a caps run is recased.
        ('"Частна детска градина "НЕМО" ООД', "НЕМО", "NEMO"),
        ('Частна детска градина "ПОД 1 ПОКРИВ" ЕООД', "Под 1 Покрив", "Pod 1 Pokriv"),
    ],
)
def test_resolve_name_cleans_register_formatting(stored_bg, expected_bg, expected_en):
    assert resolve_name_i18n({"bg": stored_bg}) == {"bg": expected_bg, "en": expected_en}


def test_number_that_is_part_of_a_brand_is_not_made_ordinal():
    assert translate_bg_school_name("101 ЗАЩО", source="101 ЗАЩО") == "101 Zashto"


def test_tidy_bg_name_keeps_acronyms_and_roman_numerals():
    assert tidy_bg_name('74 СУ "ГОЦЕ ДЕЛЧЕВ"') == "74 СУ „Гоце Делчев“"
    assert tidy_bg_name('12 Средно  училище " Цар Иван Асен II"') == "12 Средно училище „Цар Иван Асен II“"
    assert tidy_bg_name("''Ягодина") == "Ягодина"


def test_resolved_name_keeps_stored_english_name():
    resolved = resolve_name_i18n(
        {"bg": "АМЕРИКАНСКИ КОЛЕЖ В  СОФИЯ", "en": "American College of Sofia"}
    )
    assert resolved == {"bg": "Американски колеж в София", "en": "American College of Sofia"}


@pytest.mark.parametrize(
    ("stored_bg", "expected_bg", "expected_en"),
    [
        ("АЛЕЯ ФЛОЙД БЛЯК, 1799 СТОЛИЧНА", "Алея Флойд Бляк", "Aleya Floyd Blyak"),
        ("гр. София, ул. 208 № 17 - II м. р.", "ул. 208 № 17 - II м. р.", "ul. 208 No. 17 - II m. r."),
        (
            'СОФИЯ,Ж.К.МЛАДОСТ-1, ул."Владимир Пашов" № 2',
            "ж.к. Младост-1, ул. „Владимир Пашов“ № 2",
            'zh.k. Mladost-1, ul. "Vladimir Pashov" No. 2',
        ),
        ('УЛ. "ЦАР СИМЕОН І" № 62', "ул. „Цар Симеон І“ № 62", 'ul. "Tsar Simeon I" No. 62'),
        ('бул. " Цар Борис ІІІ " № 128', "бул. „Цар Борис ІІІ“ № 128", 'bul. "Tsar Boris III" No. 128'),
        ('ул."Зорница"№63', "ул. „Зорница“ № 63", 'ul. "Zornitsa" No. 63'),
        (
            'гр. София, ул. "Hикола Габровски", №26',  # Latin "H" typed in the register
            "ул. „Никола Габровски“, № 26",
            'ul. "Nikola Gabrovski", No. 26',
        ),
        (
            'Район Красно село, бул. "Ген. М. Д. Скобелев" № 58',
            "Район Красно село, бул. „Ген. М. Д. Скобелев“ № 58",
            'Krasno selo district, bul. "Gen. M. D. Skobelev" No. 58',
        ),
        (
            'ж. к. Редута, ул."Румен войвода" № 6, местност "Подуяне-Редута", район "Слатина" - СО',
            "ж.к. Редута, ул. „Румен войвода“ № 6, местност „Подуяне-Редута“, район „Слатина“",
            'zh.k. Reduta, ul. "Rumen voyvoda" No. 6, mestnost "Poduyane-Reduta", Slatina district',
        ),
        # Another town keeps its prefix; an odd stray quote is left as published.
        ('гр. Банкя, ул. "П. Д. Петков", №15', "гр. Банкя, ул. „П. Д. Петков“, № 15", 'gr. Bankya, ul. "P. D. Petkov", No. 15'),
        ('ул. Яна Язова" № 6', 'ул. Яна Язова" № 6', 'ul. Yana Yazova" No. 6'),
    ],
)
def test_resolve_address_cleans_register_formatting(stored_bg, expected_bg, expected_en):
    assert resolve_address_i18n({"bg": stored_bg}) == {"bg": expected_bg, "en": expected_en}


def test_resolved_address_keeps_stored_english_address():
    resolved = resolve_address_i18n({"bg": "ул. Пиротска 78", "en": "78 Pirotska St."})
    assert resolved == {"bg": "ул. Пиротска 78", "en": "78 Pirotska St."}


def test_tidy_bg_address_leaves_short_caps_acronyms():
    assert tidy_bg_address('ул. "Георги Георгиев - ГЕЦ" № 48') == "ул. „Георги Георгиев - ГЕЦ“ № 48"
