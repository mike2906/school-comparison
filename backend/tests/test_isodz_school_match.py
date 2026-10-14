"""ИСОДЗ school institutions matched to our schools by Sofia school number."""

from types import SimpleNamespace

from app.scrapers.sources.bg.isodz_school_match import match_school, number_index, school_number


def _school(id_, code, name, level="lower_secondary", type_="state"):
    return SimpleNamespace(id=id_, institutional_id=code, name_i18n={"bg": name}, education_level=level, school_type=type_)


SCHOOLS = [
    _school(188, "2203061", '61Основно училище "Св. св. Кирил и Методий"'),
    _school(221, "2206003", 'Трето средно  училище "Марин Дринов"', "upper_secondary"),
    _school(234, "2207150", '150-то ОСНОВНО УЧИЛИЩЕ  "ЦАР СИМЕОН ПЪРВИ"'),
    _school(365, "2220048", '48 ОСНОВНО УЧИЛИЩЕ  "ЙОСИФ КОВАЧЕВ"'),
    _school(266, "2211139", '139 Основно училище "Захарий Круша"'),
    _school(311, "2214101", '101 СРЕДНО УЧИЛИЩЕ "БАЧО КИРО"', "upper_secondary"),
    _school(498, "2902101", 'Национално музикално училище "Любомир Пипков"', "upper_secondary"),
    _school(214, "2204595", '"ЧАСТНО ОСНОВНО УЧИЛИЩЕ СВЕТЛИНА" ЕООД', type_="private"),
    _school(325, "2216306", 'Софийска математическа гимназия  "Паисий Хилендарски"', "upper_secondary"),
]
INDEX = number_index(SCHOOLS)


def test_school_number():
    assert school_number('61. ОУ "Св. Св. Кирил и Методий"') == 61
    assert school_number("96. Лев Николаевич Толстой") == 96
    assert school_number('ДГ №1 "Червената шапчица"') is None


def test_only_sofia_city_state_school_codes_are_indexed():
    assert [s.id for s in INDEX[101]] == [311]  # not 2902101, a national school
    assert 595 not in INDEX  # private
    assert [s.id for s in INDEX[306]] == [325]  # codes ≥ 300 are not school numbers; no ИСОДЗ school has one


def test_matches_by_number_and_patron():
    assert match_school('61. ОУ "Св. Св. Кирил и Методий"', INDEX).id == 188
    assert match_school('3. СУ "Марин Дринов"', INDEX).id == 221
    assert match_school("150. Цар Симеон I", INDEX).id == 234
    assert match_school('48. ОУ "Йосиф Kовачев"', INDEX).id == 365  # Latin K
    assert match_school('139. ОУ "Захари Круша"', INDEX).id == 266  # Захари / Захарий


def test_a_different_patron_or_unknown_number_stays_unmatched():
    assert match_school('101. СУ "Любомир Пипков"', INDEX) is None
    assert match_school('7. СУ "Свети Седмочисленици"', INDEX) is None


def test_empty_patron_matches_on_number_alone():
    assert match_school('48. ОУ ""', INDEX).id == 365
