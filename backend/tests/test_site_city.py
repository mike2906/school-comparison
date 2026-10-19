"""A website whose own title names another town is another school's (parent trial, 2026-10-19)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from app.models import School
from app.scrapers.site_city import identity_texts, other_town_named
from app.scrapers.stages.site_city import run_site_city
from app.scrapers.url_validator import URLValidator, ValidationResult


@pytest.mark.parametrize(
    "texts, town",
    [
        # 256's link: a Varna school with the same patron's name.
        (['ОУ  "Свети свети Кирил и Методий" гр.Варна | 164 години традиция'], "Варна"),
        (["Начало - СУ Христо Ботев - град Стара Загора"], "Стара Загора"),
        (["Home | Hristo Botev School, Plovdiv"], "Пловдив"),
        (["Начало - Варна"], "Варна"),
        # The school's own town, or one of Sofia's three other towns.
        (["Детска градина №25 Изворче, Град Банкя - Контакти"], None),
        (["48. ОУ \"Йосиф Ковачев\" - град София"], None),
        (["30. СУ \"Братя Миладинови\"-училище в София"], None),
        (["ДГ 11 Мики Маус - София, кв. Младост 1"], None),
        # Its own town named beside another (a branch, a partner): not another school's site.
        (["СУ гр. София", "Филиал гр. Пловдив"], None),
        # All capitals, or "Град" capitalised.
        (["ОУ СВ. СВ. КИРИЛ И МЕТОДИЙ ГР.ВАРНА"], "Варна"),
        (["ОУ Град Варна"], "Варна"),
        # "град" inside a word, "гр." for "група", "Детски град" as a name: no known town.
        (["Детска градина Слънце", "градски парк"], None),
        (["ДГ №12 Детски град Пинокио"], None),
        (["ДГ 5 - Яслена гр. Калинка"], None),
        # A town word that is not a whole part of the title.
        (["Русенски лъв - ДГ 12"], None),
        ([], None),
    ],
)
def test_other_town_named(texts, town):
    assert other_town_named(texts, "sofia") == town


def test_a_city_without_its_towns_listed_is_not_checked():
    assert other_town_named(["СУ гр. Варна"], "varna") is None
    assert other_town_named(["СУ гр. Варна"], None) is None


def test_identity_texts_reads_title_headings_and_site_name():
    html = (
        "<html><head><title>Начало</title><meta property='og:site_name' content='ОУ гр.Варна'></head>"
        "<body><h1>Добре дошли</h1><h1>Варна</h1><h2>Състезание в гр. Пловдив</h2></body></html>"
    )
    assert identity_texts(BeautifulSoup(html, "html.parser")) == ["Начало", "Добре дошли", "Варна", "ОУ гр.Варна"]


async def test_validator_rejects_a_site_titled_with_another_town():
    html = """<html><head><title>ОУ "Свети свети Кирил и Методий" гр.Варна</title></head>
        <body><h1>ОУ Свети свети Кирил и Методий</h1><p>училище ученици учители прием клас</p></body></html>"""
    response = MagicMock(status_code=200, url="https://ousvsvkirilimetodii.com/", text=html)
    with patch("httpx.AsyncClient") as client_class:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        client_class.return_value = client
        validator = URLValidator("bg", school_city="sofia")
        result, final_url, reason = await validator.validate_url(
            "https://ousvsvkirilimetodii.com",
            use_llm_fallback=False,
            school_name='115 основно училище "Свети, свети Кирил и Методий"',
        )
        assert (result, final_url, reason) == (ValidationResult.INVALID, None, "Website names another town: Варна")



async def test_site_city_stage_withholds_only_on_apply(db_session):
    varna = School(
        country_code="bg", city="sofia", name_i18n={"bg": "115 ОУ"}, school_type="state",
        education_level="lower_secondary", website_url="https://varna-school.bg/", scrape_status="navigated",
    )
    sofia = School(
        country_code="bg", city="sofia", name_i18n={"bg": "48 ОУ"}, school_type="state",
        education_level="lower_secondary", website_url="https://48ou.bg/", scrape_status="navigated",
    )
    db_session.add_all([varna, sofia])
    await db_session.commit()
    pages = {
        "https://varna-school.bg/": "<title>ОУ гр.Варна</title>",
        "https://48ou.bg/": "<title>48. ОУ - град София</title>",
    }

    async def fetch(url):
        return url, pages[url]

    report = await run_site_city(db_session, fetch=fetch)
    assert [hit["school_id"] for hit in report["hits"]] == [varna.id]
    assert report["checked"] == 2 and not report["applied"]

    with patch("app.scrapers.url_validator._update_validation_result", new=AsyncMock()) as update:
        report = await run_site_city(db_session, fetch=fetch, apply=True)
    update.assert_awaited_once_with(
        varna.id, "https://varna-school.bg/", ValidationResult.INVALID, None, "Website names another town: Варна"
    )
    assert report["hits"][0]["withheld"] is True


async def test_validator_reads_the_same_texts_as_the_stage():
    # A site name the title does not repeat: validation and the weekly stage must agree.
    html = """<html><head><title>48 ОУ</title><meta property="og:site_name" content="ОУ гр.Варна"></head>
        <body><h1>48 ОУ</h1><p>училище ученици учители прием клас</p></body></html>"""
    response = MagicMock(status_code=200, url="https://48ou.bg/", text=html)
    with patch("httpx.AsyncClient") as client_class:
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.return_value = response
        client_class.return_value = client
        result, _, reason = await URLValidator("bg", school_city="sofia").validate_url(
            "https://48ou.bg", use_llm_fallback=False, school_name="48 основно училище"
        )
    assert (result, reason) == (ValidationResult.INVALID, "Website names another town: Варна")


def test_a_patron_named_like_a_town_is_not_another_town():
    # 74 СУ "Гоце Делчев": a patron, unless written as a town.
    assert other_town_named(["74 СУ - Гоце Делчев"], "sofia") is None
    assert other_town_named(["СУ гр. Гоце Делчев"], "sofia") == "Гоце Делчев"
