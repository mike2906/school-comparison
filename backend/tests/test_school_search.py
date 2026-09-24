"""UF32: name search understands how parents write school names."""

import re
from pathlib import Path

import pytest

from app.models.school import School, SchoolLocation
from app.utils.school_search import (
    RANK_ADDRESS,
    RANK_ALIAS,
    RANK_CONTAINS,
    RANK_NUMBER,
    RANK_PREFIX,
    SCHOOL_NAME_ALIASES,
    TYPE_ABBREVIATIONS,
    normalize,
    query_tokens,
    rank_school,
)

SU_119 = '119 Средно училище "Академик Михаил Арнаудов"'
SU_1190 = '1190 Средно училище "Тест"'
SMG = 'Софийска математическа гимназия  "Паисий Хилендарски"'
NPMG = 'Национална природо-математическа  гимназия "Академик Любомир Чакалов"'
AEG_1 = "Първа английска езикова гимназия"
AEG_2 = 'Втора английска езикова гимназия  "Томас Джеферсън"'
IEG_164 = '164. гимназия с преподаване  на испански език "Мигел де Сервантес"'
OU_150 = '150-то ОСНОВНО УЧИЛИЩЕ  "ЦАР СИМЕОН ПЪРВИ"'


def _rank(query: str, name: str, *, addresses=()) -> int | None:
    return rank_school(query_tokens(query), registry_name=name, names=[name], addresses=addresses)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("№119", "119"),
        ("ДГ №5 Надежда", "дг 5 надежда"),
        ("119-то СУ", "119 су"),
        ("119то", "119"),
        ("1-ва АЕГ", "1 аег"),
        ("156-о Обединено", "156 обединено"),
        ("100-но ОУ", "100 оу"),
        ("119th school", "119 school"),
        ("School No. 7", "school 7"),
        ('91.НЕМСКА ЕЗИКОВА ,,Проф."', "91 немска езикова проф"),
    ],
)
def test_normalize(text, expected):
    assert normalize(text) == expected


@pytest.mark.parametrize(
    "query", ["119", "119 СУ", "СУ 119", "119-то", "№119", "119th", "119 su", "119-то СОУ"]
)
def test_numbered_school_queries_match(query):
    assert _rank(query, SU_119) == RANK_NUMBER


def test_numbers_match_whole_numbers_only():
    assert _rank("119", SU_1190) is None
    assert _rank("19", SU_119) is None


def test_type_abbreviation_must_fit_the_school():
    assert _rank("119 ОУ", SU_119) is None
    assert _rank("150 ОУ", OU_150) == RANK_NUMBER
    assert _rank("ОУ 150", OU_150) == RANK_NUMBER
    assert _rank("164 ИЕГ", IEG_164) == RANK_NUMBER


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("СМГ", SMG),
        ("smg", SMG),
        ("смг паисий", SMG),
        ("НПМГ", NPMG),
        ("NPMG", NPMG),
        ("1 АЕГ", AEG_1),
        ("1-ва АЕГ", AEG_1),
        ("I АЕГ", AEG_1),
        ("Първа АЕГ", AEG_1),
        ("1 AEG", AEG_1),
        ("2 АЕГ", AEG_2),
        ("II AEG", AEG_2),
    ],
)
def test_curated_aliases(query, name):
    assert _rank(query, name) == RANK_ALIAS


def test_alias_does_not_leak_to_other_schools():
    assert _rank("СМГ", NPMG) is None
    assert _rank("2 АЕГ", AEG_1) is None
    assert _rank("смг вазов", SMG) is None


def test_latin_query_matches_cyrillic_name_by_transliteration():
    assert _rank("sofiyska matematicheska", SMG) == RANK_PREFIX
    assert _rank("matemat", SMG) == RANK_PREFIX
    assert _rank("atematich", SMG) == RANK_CONTAINS


def test_address_only_match_ranks_last_and_nonsense_is_empty():
    assert _rank("Витоша", SMG, addresses=["бул. Витоша 1"]) == RANK_ADDRESS
    assert _rank("xqzv", SMG, addresses=["бул. Витоша 1"]) is None
    assert query_tokens(" .,- ") == []


def test_every_alias_targets_a_normalized_substring():
    for alias, target in SCHOOL_NAME_ALIASES.items():
        assert normalize(alias) == alias
        assert normalize(target) == target


def _js_table(source: str, name: str) -> dict[str, object]:
    block = re.search(rf"export const {name} = \{{(.*?)\n\}}", source, re.S).group(1)
    table: dict[str, object] = {}
    for key, value in re.findall(r"'([^']+)':\s*(\[[^\]]*\]|'[^']*')", block):
        if value.startswith("["):
            table[key] = tuple(re.findall(r"'([^']*)'", value))
        else:
            table[key] = value.strip("'")
    return table


def test_frontend_tables_match_backend():
    js_path = Path(__file__).resolve().parents[2] / "frontend/src/utils/schoolSearch.js"
    if not js_path.exists():
        pytest.skip("frontend not checked out")
    source = js_path.read_text(encoding="utf-8")
    assert _js_table(source, "SCHOOL_NAME_ALIASES") == SCHOOL_NAME_ALIASES
    assert _js_table(source, "TYPE_ABBREVIATIONS") == TYPE_ABBREVIATIONS


async def _add_school(db, name_bg: str, *, name_en: str | None = None) -> School:
    name_i18n = {"bg": name_bg}
    if name_en:
        name_i18n["en"] = name_en
    school = School(
        name_i18n=name_i18n,
        country_code="bg",
        school_type="state",
        education_level="secondary",
        city="sofia",
        attributes={},
    )
    db.add(school)
    await db.flush()
    db.add(
        SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тест 1, София"},
            lat=42.7,
            lng=23.3,
            is_primary=True,
        )
    )
    return school


@pytest.mark.asyncio
async def test_search_endpoint_uses_aliases_numbers_and_ranking(seeded_db, seeded_client):
    su_119 = await _add_school(seeded_db, SU_119)
    su_1190 = await _add_school(seeded_db, SU_1190)
    smg = await _add_school(seeded_db, SMG)
    npmg = await _add_school(seeded_db, NPMG)
    aeg_1 = await _add_school(seeded_db, AEG_1, name_en="First English Language School")
    await seeded_db.commit()

    async def ids(query: str) -> list[int]:
        response = await seeded_client.get("/schools/search", params={"q": query})
        assert response.status_code == 200
        return [row["id"] for row in response.json()]

    for query in ("119 СУ", "СУ 119", "119-то", "№119", "119"):
        assert await ids(query) == [su_119.id], query
    assert await ids("1190") == [su_1190.id]
    assert await ids("СМГ") == [smg.id]
    assert await ids("SMG") == [smg.id]
    assert await ids("НПМГ") == [npmg.id]
    assert await ids("1 АЕГ") == [aeg_1.id]
    assert await ids("First English") == [aeg_1.id]
    assert await ids("xqzv") == []
    # Same tier (word prefix): the shorter registry name comes first.
    assert await ids("математическа") == [smg.id, npmg.id]
