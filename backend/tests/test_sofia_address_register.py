"""Sofia's address register: parsing our addresses and matching them to register points."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services.geocoding.bg import sofia_address_register as reg

CSV = """rn;region;settlement;lareaunit;block;street;streetnum;entrance;n;e
9567;Витоша;гр. София;"";"";ул. Ваклинец;10;"";42.624546;23.314579
11045;Витоша;гр. София;"";"";ул. Ветрушка;5А;"";42.665715;23.263970
116781;Банкя;гр. Банкя;"";"";ул. Тенев баир;2;"";42.703233;23.135833
87860;Банкя;гр. Банкя;"";"";ул. Майор Коста Паница;1;"";42.706707;23.144138
92541;Банкя;гр. Банкя;"";"";ул. Майор Коста Паница;1;"";42.706408;23.142726
70072;Нови Искър;с. Балша;"";"";ул. Акация;1;"";42.850954;23.267945
99356;Младост;гр. София;ж.к. Младост 4;460А;"";"";1;42.630736;23.382160
99357;Младост;гр. София;ж.к. Младост 4;460А;"";"";2;42.630573;23.381966
99358;Младост;гр. София;ж.к. Младост 4;460А;"";"";3;42.630376;23.382090
21248;Младост;гр. София;ж.к. Младост 4;460;"";"";1;42.630076;23.381605
1;Студентски;гр. София;"";"";бул. Свети Климент Охридски;49;"";42.650000;23.350000
2;Красно село;гр. София;ж.к. Стрелбище;10;бул. Петко Юрданов Тодоров;"";Ж;42.680000;23.290000
3;Оборище;гр. София;"";"";ул. Росица;12;"";42.700000;23.340000
4;Витоша;гр. София, кв. Драгалевци;"";"";ул. Росица;12;"";42.620000;23.310000
5;Витоша;гр. София, кв. Драгалевци;"";"";ул. Яна Язова;6;"";42.630000;23.320000
6;Средец;гр. София;"";"";бул. Витоша;5;"";42.690000;23.320000
7;Панчарево;с. Лозен;"";"";ул. Първа;1;"";42.600000;23.480000
8;Панчарево;гр. София;"";"";ул. Първа;1;"";42.640000;23.400000
9;Красно село;гр. София;"";"";бул. Цар Борис III;5;"";42.680000;23.280000
10;Изгрев;гр. София;кв. Гео Милев;5;"";"";А;42.680000;23.360000
11;Лозенец;гр. София;"";"";ул. Тирана;10;"";42.670000;23.330000
12;Оборище;гр. София;"";"";ул. Стара планина;13;"";42.697000;23.334000
13;Кремиковци;гр. София, кв. Кремиковци;"";"";ул. Стара планина;13;"";42.786000;23.506000
14;Кремиковци;гр. София, кв. Кремиковци;"";"";ул. Стара планина;16;"";42.786578;23.506657
15;Витоша;гр. София;"";"";ул. Александър Малинов;79Б;"";42.640000;23.370000
"""
REGISTER = reg.read_register(CSV)


def _match(address, district=None):
    return reg.match(reg.parse(address, district), REGISTER)


@pytest.mark.parametrize(
    "address, point",
    [
        ('район Витоша, ж.к. Драгалевци, ул. "Ваклинец" № 10', (42.624546, 23.314579)),
        ('район Витоша, ул. "Ветрушка" № 5а', (42.665715, 23.26397)),  # lower-case letter
        ("гр. Банкя, ул. Тенев баир 2", (42.703233, 23.135833)),  # number after the name, another town
        ('с. Балша, ул. "Акация" № 1', (42.850954, 23.267945)),
        ('ж. к. Младост 4, бл. 460А, вх. 2, ет. 5, ап. 18', (42.630573, 23.381966)),
        ('район Студентски, бул. "Св. Климент Охридски" № 49', (42.65, 23.35)),  # Св. = Свети
        ('ж. к. Стрелбище, бул. "П. Ю. Тодоров", бл. 10, вх. Ж, ет. 2', (42.68, 23.29)),  # initials
        ('ул. "Яна Язова" № 6', (42.63, 23.32)),  # a кв. of Sofia is still Sofia
        ("с. Лозен ул. Първа № 1", (42.6, 23.48)),  # the village, with no comma after it
        ('бул. "Цар Борис III" № 5', (42.68, 23.28)),
        ("кв. Гео Милев, бл. 5, вх. А", (42.68, 23.36)),  # a кв. complex
        ('ул. "Тирана" № 10, бл. 2', (42.67, 23.33)),  # the register files the block elsewhere
        ("ул. Александър Малинов № 79 Б", (42.64, 23.37)),  # a space before the letter
        ('район Кремиковци, ул. "Стара планина" № 16', (42.786578, 23.506657)),  # district named
    ],
)
def test_an_address_in_the_register_gets_its_point(address, point):
    found = _match(address)
    assert found.outcome == "matched"
    assert found.point == pytest.approx(point, abs=1e-6)


def test_a_block_without_an_entrance_takes_the_centre_of_its_entrances():
    found = _match("ж.к. Младост 4, бл. 460А")
    assert found.outcome == "matched" and len(found.rows) == 3  # not block 460
    assert found.point == pytest.approx((42.630562, 23.382072), abs=1e-6)


@pytest.mark.parametrize(
    "address, outcome",
    [
        ('ул. "Ваклинец" № 36', "not_in_register"),  # the street is there, the number is not
        ('ул. "Ветрушка" № 5', "not_in_register"),  # 5 is not 5А
        ('ул. "Акация" № 1', "not_in_register"),  # Балша's street, but no village named: Sofia
        ('ул. "Майор Коста Паница"', "no_address"),  # no number
        ('район Младост, ж. к. "Младост 2", до бл. 227', "no_address"),  # next to a block
        ("ж. к. Люлин 6", "no_address"),
        ('ул. "Витоша" № 5', "not_in_register"),  # бул. Витоша is another street
        ('бул. "Цар Борис" № 5', "not_in_register"),  # no numeral, another street
        ("гр. Банкя, ул. Майор Коста Паница № 1", "ambiguous"),  # two points 120 m apart
        ('ул. "Росица" № 12', "ambiguous"),  # in two districts
    ],
)
def test_an_address_the_register_cannot_place_alone_is_not_matched(address, outcome):
    assert _match(address).outcome == outcome


def test_our_district_settles_an_address_found_in_two_places():
    assert _match('район Оборище, ул. "Росица" № 12').point == (42.7, 23.34)
    assert _match('ул. "Росица" № 12', district="Оборище").point == (42.7, 23.34)


def test_the_register_zip_is_read():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("address_sofia.csv", CSV)
    assert len(reg.read_register_zip(buffer.getvalue())) == len(REGISTER) == 25


def test_a_street_name_used_for_two_streets_needs_our_district():
    # Only Кремиковци's ул. Стара планина has № 16, but the name is two streets in Sofia.
    assert _match('ул. "Стара планина" № 16').outcome == "ambiguous"
    assert _match('ул. "Стара планина" № 13', district="Оборище").point == (42.697, 23.334)
    assert _match('район Оборище, ул. "Стара планина" № 16').outcome == "ambiguous"


def test_a_match_says_whether_our_district_was_checked():
    assert _match('район Витоша, ул. "Ветрушка" № 5а').region_checked
    assert not _match('ул. "Ветрушка" № 5а').region_checked


def test_and_between_numbers_is_not_a_letter():
    assert reg.parse('ул. "Тирана" № 16 и 18').number == "16"


@pytest.mark.parametrize(
    "text, address, named",
    [
        ("Visit us at 80A Ralevitsa Street", 'ул. Ралевица № 80А', True),
        ("Адрес: гр. София, ул. „Ралевица“ № 80А", 'ул. Ралевица № 80А', True),
        ("гр. София,улица Триадица №5А", 'ул. "Триадица" № 5А', True),
        ("2 Tenev Bair, Bankya", "гр. Банкя, ул. Тенев баир 2", True),
        ("ж.к. Младост 4, бл. 460А, вх. 2", "ж.к. Младост 4, бл. 460А", True),
        ("ул. Ралевица № 80 А", 'ул. Ралевица № 80А', True),  # a space before the letter
        ("ж.к. „Младост 2\", ул. „Генерал-Майор Васил Делов“ 10", 'ул. "Генерал Васил Делов" № 10', True),
        ("ул. Ралевица № 8", 'ул. Ралевица № 80А', False),
        ("ж.к. Младост 1, бл. 460А", "ж.к. Младост 4, бл. 460А", False),  # another complex
        ("5 пъти седмично", 'ул. "П. Ю. Тодоров" № 5', False),  # initials alone
        ("бул. Витоша 5", 'ул. "Витоша" № 5', False),  # another kind of street
        ("ул. Стефан Караджа 5", 'ул. "Хаджи Димитър Караджа" № 5', False),
        ("III 5 клас", 'бул. "Цар Борис III" № 5', False),
        ("ул. Ралевица 12, 5 етаж", 'ул. Ралевица № 5', False),  # a floor
        ("ул. Ралевица, бл. 5", 'ул. Ралевица № 5', False),  # a block is not a house
        ("Ралевица 1/5", 'ул. Ралевица № 5', False),
        ("ул. Ралевица 80", 'ул. Ралевица № 80А', False),
        ("Младост 1, бл. 460", "ж.к. Младост 4, бл. 460А", False),
        ("ул. Ралевица № 80А", 'ул. "Майор Коста Паница"', False),  # no number of ours
    ],
)
def test_a_page_names_our_address(text, address, named):
    assert reg.names_address(reg.page_text(text), reg.parse(address)) is named
