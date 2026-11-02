"""Private schools with no website get one searched for (find-websites, 2026-10-23: 33 of 174 had none)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.models import School
from app.scrapers import url_validator
from app.scrapers.stages import find_websites
from app.scrapers.url_validator import URLValidator, ValidationResult
from app.scrapers.website_discovery import WebsiteDiscoverer, directory_host

NOW = datetime(2026, 11, 1, 2, 20, tzinfo=timezone.utc)


def _school(**extra):
    values = dict(
        country_code="bg", city="sofia", name_i18n={"bg": '"ЧДГ "ЯН БИБИЯН" - МЕГА-Х" ООД'}, school_type="private",
        education_level="kindergarten", scrape_status="no_official_website",
    )
    return School(**{**values, **extra})


async def _run(db_session, verdicts, candidates=("https://janbibijan.com/",), page="<title>ДГ Ян Бибиян</title>", **options):
    validate = AsyncMock(side_effect=verdicts)
    store = AsyncMock()
    with patch.object(find_websites, "_page_text", AsyncMock(return_value=page)), patch.object(WebsiteDiscoverer, "_search_candidates", AsyncMock(return_value=list(candidates))), patch.object(
        URLValidator, "validate_url", validate
    ), patch.object(url_validator, "_update_validation_result", store), patch(
        "app.scrapers.stages.website.check_shared_sites", AsyncMock()
    ):
        report = await find_websites.run_find_websites(db_session, now=NOW, **options)
    return report, validate, store


async def test_a_dry_run_searches_and_writes_nothing(db_session):
    school = _school()
    db_session.add(school)
    await db_session.commit()
    report, validate, store = await _run(db_session, [(ValidationResult.VALID, "https://janbibijan.com/", "match")])
    assert report["found"] == 1 and report["schools"][0]["found"] == "https://janbibijan.com/"
    assert validate.await_args.kwargs["use_llm_fallback"] is False  # a run costs nothing
    store.assert_not_awaited()
    await db_session.refresh(school)
    assert school.website_url is None and find_websites.SEARCHED_AT_KEY not in (school.attributes or {})


async def test_apply_stores_the_first_valid_candidate_and_records_the_search(db_session):
    school = _school(attributes={"website_data_withheld": True})
    db_session.add(school)
    await db_session.commit()
    report, validate, store = await _run(
        db_session,
        [(ValidationResult.INVALID, None, "Directory-like listing"), (ValidationResult.VALID, None, "match")],
        candidates=("https://yanbibiyan-mega-x.com/", "https://janbibijan.com/"),
        apply=True,
    )
    tried = [entry["result"] for entry in report["schools"][0]["tried"]]
    assert tried[-1] == "valid" and report["found"] == 1
    assert store.await_args.args[2:4] == (ValidationResult.VALID, report["schools"][0]["found"])
    await db_session.refresh(school)
    assert school.attributes[find_websites.SEARCHED_AT_KEY] == NOW.isoformat()
    assert school.attributes["website_candidate_method"] == "search"
    assert "website_data_withheld" not in school.attributes  # it starts clean (store_given_site)


async def test_nothing_valid_stores_nothing_but_records_the_search(db_session):
    school = _school()
    db_session.add(school)
    await db_session.commit()
    report, _, store = await _run(db_session, [(ValidationResult.AMBIGUOUS, None, "only 1 keyword")], apply=True)
    assert report["found"] == 0
    store.assert_not_awaited()
    await db_session.refresh(school)
    assert school.website_url is None and school.attributes[find_websites.SEARCHED_AT_KEY] == NOW.isoformat()


async def test_a_school_searched_lately_waits_and_state_schools_are_searched_too(db_session):
    recent = _school(attributes={find_websites.SEARCHED_AT_KEY: (NOW - timedelta(days=30)).isoformat()})
    old = _school(attributes={find_websites.SEARCHED_AT_KEY: (NOW - timedelta(days=120)).isoformat()})
    state = _school(school_type="state", name_i18n={"bg": "ДГ №59 Елхица"})
    has_site = _school(website_url="https://janbibijan.com/", scrape_status="validated")
    db_session.add_all([recent, old, state, has_site])
    await db_session.commit()
    report, _, _ = await _run(db_session, [(ValidationResult.INVALID, None, "no")] * 6)
    assert report["schools_without_site"] == 3  # every school without a site
    assert [school["school_id"] for school in report["schools"]] == [old.id, state.id]
    # Named ids are searched whatever their last search.
    report, _, _ = await _run(db_session, [(ValidationResult.INVALID, None, "no")] * 3, school_ids=[recent.id])
    assert [school["school_id"] for school in report["schools"]] == [recent.id]


async def test_one_schools_failure_does_not_stop_the_run(db_session):
    first, second = _school(), _school()
    db_session.add_all([first, second])
    await db_session.commit()
    report, _, _ = await _run(
        db_session,
        [RuntimeError("site broke"), (ValidationResult.VALID, None, "match")],
        apply=True,
    )
    assert [("error" in school, bool(school.get("found"))) for school in report["schools"]] == [(True, False), (False, True)]
    await db_session.refresh(first)
    assert find_websites.SEARCHED_AT_KEY not in (first.attributes or {})  # searched again next time


async def test_a_valid_site_that_does_not_name_the_school_is_not_stored(db_session):
    """150 "Стив Джобс": validation took SoftUni Будител's site for it on its profile words."""
    school = _school(name_i18n={"bg": '"ЧАСТНА ПРОФЕСИОНАЛНА ГИМНАЗИЯ ПО ПРОГРАМИРАНЕ И РОБОТИКА "СТИВ ДЖОБС" ЕООД'})
    db_session.add(school)
    await db_session.commit()
    report, _, store = await _run(
        db_session,
        [(ValidationResult.VALID, "https://buditel.softuni.bg/", "match")],
        candidates=("https://buditel.softuni.bg/",),
        page="<title>СофтУни БУДИТЕЛ</title><p>Програмиране и роботика</p>",
        apply=True,
    )
    assert report["found"] == 0 and "СТИВ ДЖОБС" in report["schools"][0]["tried"][0]["reason"]
    store.assert_not_awaited()


def test_brands_are_the_quoted_parts_that_name_the_school():
    assert find_websites.brands('"ЧАСТНА ПГ ПО ПРОГРАМИРАНЕ И РОБОТИКА "СТИВ ДЖОБС" ЕООД') == ["СТИВ ДЖОБС"]
    assert find_websites.brands('"ЧДГ "ЯН БИБИЯН" - МЕГА-Х" ООД') == ["ЯН БИБИЯН", "МЕГА-Х"]
    assert find_websites.brands('"ЧАСТНА ДЕТСКА ГРАДИНА ДЕТСКИ ОАЗИС" ЕООД') == []
    assert find_websites.brand_named('"ЧДГ "ЯН БИБИЯН" - МЕГА-Х" ООД', "Детска градина Yan Bibiyan")
    assert find_websites.brand_named("ЧДГ Веда", "anything")


KG_HOME = "<title>Детска градина №59 Елхица</title><h1>ДГ 59</h1><p>Добре дошли в ДГ №59 \"Елхица\", Младост</p>"


def _kindergarten(**extra):
    return _school(school_type="state", name_i18n={"bg": "ДГ №59 Елхица (с яслени групи)"}, scrape_status="pending", **extra)


async def test_a_state_kindergarten_is_tried_at_its_home_page_once_a_site(db_session):
    """A directory's page about the kindergarten never counts: only a site's home page is
    validated, and the directory's own home page names no single kindergarten."""
    school = _kindergarten()
    db_session.add(school)
    await db_session.commit()
    report, validate, store = await _run(
        db_session,
        [(ValidationResult.VALID, "https://dg-59.com/", "match")],
        candidates=(
            "https://obrazovatelen-register.bg/dg-59-elhitsa-sofiya/",
            "https://mladost.sofia.bg/obrazovanie/detski-gradini",
            "https://dg-59.com/za-nas/ekip.html",
            "https://dg-59.com/kontakti",
        ),
        page=KG_HOME,
        apply=True,
    )
    assert [call.args[0] for call in validate.await_args_list] == ["https://dg-59.com/"]
    assert report["schools"][0]["found"] == "https://dg-59.com/"
    store.assert_awaited_once()


async def test_a_state_kindergarten_needs_its_number_and_its_own_name_on_the_home_page(db_session):
    school = _kindergarten()
    db_session.add(school)
    await db_session.commit()
    for page, reason in (
        ("<title>59 СУ Васил Левски</title><p>Елхица</p>", "does not name kindergarten 59"),
        ("<title>Детска градина №58</title><p>Елхица</p>", "does not name kindergarten 59"),
        ("<title>Детска градина №59</title><p>Добре дошли</p>", "does not name елхица"),
        ("<title>59 ДГ - подготовка за 59 СУ</title><p>Елхица</p>", "does not name kindergarten 59"),
    ):
        report, _, store = await _run(
            db_session, [(ValidationResult.VALID, "https://dg59.bg/", "match")], candidates=("https://dg59.bg/",), page=page
        )
        assert report["found"] == 0 and reason in report["schools"][0]["tried"][0]["reason"], page
        store.assert_not_awaited()


def test_state_site_problem_tells_kindergartens_nurseries_and_schools_apart():
    def page(title, body=""):
        return f"<title>{title}</title><body>{body}</body>"

    def problem(name, level, html):
        return find_websites.state_site_problem(name, find_websites.state_kind(name, level), html)

    kg = "kindergarten"
    assert problem("ДГ №18 Детски свят (с яслени групи)", kg, page("Начало - Детска градина 18 - Детски свят")) is None
    assert problem('74 СУ "ГОЦЕ ДЕЛЧЕВ"', "upper_secondary", page("74 Средно Училище Гоце Делчев")) is None
    assert problem('6 ОУ "Граф Игнатиев"', "lower_secondary", page('Шесто Основно Училище "Граф Н.П. Игнатиев"')) is None
    assert problem('203 ПЕГ "Свети Методий"', "upper_secondary", page('Начало - 203. ПЕГ "Свети Методий"')) is None
    assert problem("ДГ №202  (с яслени групи)", kg, page("ДГ 202")) is None  # no own words: the number decides
    assert problem("СДЯ №69", kg, page("Детска ясла №69")) is None
    assert problem("ДГ №59 Елхица", kg, page("ДГ 59 Елхица - подготовка за училище")) is None
    # Sofia numbers its nurseries apart: СДЯ №69 is not ДГ №69 Жар птица, nor the other way.
    assert problem("СДЯ №69", kg, page("Детска градина 69 Жар птица"))
    assert problem("СДЯ №69", "nursery", page("69 СУ"))
    assert problem("ДГ №69 Жар птица (с яслени групи)", kg, page("СДЯ 69", "Жар птица"))
    assert problem("ДГ №59 Елхица", kg, page("59 СУ Васил Левски", "Елхица"))
    assert problem('59 СУ "Васил Левски"', "upper_secondary", page("ДГ 59 Елхица", "Васил Левски"))
    assert problem("ДГ №136 Славия", kg, page("ДГ 136", "детска градина Slaviya")) is None  # either script
    # Two own words, not one: "детски" is on every kindergarten's page.
    assert problem("ДГ №18 Детски свят (с яслени групи)", kg, page("Детска градина 18", "Детски игри"))


def test_directory_hosts_are_not_a_schools_site():
    assert directory_host("https://www.obrazovanieto.bg/detski-gradini/12-sdq-bebelandiq/") == "obrazovanieto.bg"
    assert directory_host("https://obrazovatelen-register.bg/dg-13-kalinka-sofiya/") == "obrazovatelen-register.bg"
    assert directory_host("https://nadezhda.sofia.bg/obrazovanie") == "sofia.bg"
    assert directory_host("https://kg.sofia.bg/") == "sofia.bg"
    assert directory_host("https://www.ds-sofia.bg/") is None  # Deutsche Schule Sofia
    assert directory_host("https://dg3sofia.com") is None


def test_a_home_page_keeps_a_platform_tenant():
    assert find_websites.home_page("https://dg-59.com/za-nas/ekip.html") == "https://dg-59.com/"
    assert find_websites.home_page("https://sites.google.com/view/dg59elhitsa/home") == "https://sites.google.com/view/dg59elhitsa/"


async def test_validation_fails_a_stored_directory_page_and_clears_what_it_gave():
    verdict, final_url, reason = await URLValidator().validate_url("https://obrazovatelen-register.bg/dg-13-kalinka-sofiya/")
    assert verdict is ValidationResult.INVALID and final_url is None
    assert url_validator._should_clear_website_derived_data(verdict, reason)


def test_latin_titles_and_numbered_sofia_hosts():
    def problem(name, title):
        return find_websites.state_site_problem(name, find_websites.state_kind(name, "kindergarten"), f"<title>{title}</title>")

    assert problem("ДГ №59 Елхица", "Detska gradina 59 Elhitsa") is None
    assert problem("ДГ №59 Елхица", "Градина 59 Елхица")  # a quarter, not a kindergarten
    assert directory_host("https://dg42.sofia.bg/") is None
    assert directory_host("https://mladost.sofia.bg/obrazovanie") == "sofia.bg"
