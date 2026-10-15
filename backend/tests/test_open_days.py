"""Open-days stage: the model is called only when an open-day mention changes, and dates
it finds pass the school-facts checks before they join the stored facts."""

from __future__ import annotations

import datetime

import pytest

from app.models import School
from app.scrapers.extraction import open_days as rules
from app.scrapers.extraction.open_days import DateFact, DateFacts
from app.scrapers.extraction.school_facts import Graded
from app.scrapers.stages import open_days as stage

TODAY = datetime.date(2026, 10, 14)
HOME = "https://school.example/"
NEWS = "https://school.example/news/open-day"
ADMISSIONS = "https://school.example/priem"
ANNOUNCEMENT = "Ден на отворените врати – 15.11.2026 г. от 10:00 ч."
HOME_TEXT = f"# Добре дошли\n\nНовини\n\n[{ANNOUNCEMENT}]({NEWS})\n\nПосетители: 1234\n\n" + "\n".join(
    f"Ред {n}" for n in range(20)
)


def _school(**attrs):
    return School(
        id=7,
        name_i18n={"bg": "Училище", "en": "School"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url=HOME,
        attributes=attrs,
    )


def _fetcher(pages, links=None):
    opened = []

    async def fetch(url):
        opened.append(url)
        return url, pages.get(url, ""), (links or {}).get(url, [])

    fetch.opened = opened
    return fetch


class _Calls:
    def __init__(self, facts=(), key=(), more=(), minor=()):
        self.facts, self.messages = list(facts), []
        self.grades = Graded(key=list(key), more=list(more), minor=list(minor))

    async def __call__(self, prompt, schema, message, school_id):
        self.messages.append((schema, message))
        return DateFacts(facts=self.facts) if schema is DateFacts else self.grades


def _date_fact(quote=ANNOUNCEMENT, days=("2026-11-15",), url=HOME, **extra):
    return DateFact(
        topic="admissions",
        fact_en="Open day on 15 November 2026.",
        fact_bg="Ден на отворените врати на 15 ноември 2026.",
        quote=quote,
        source_url=url,
        open_days=list(days),
        **extra,
    )


def test_the_hash_moves_with_the_mention_lines_only():
    base = rules.mention_hash(HOME_TEXT)
    assert base and rules.mention_hash(HOME_TEXT.replace("Ред 15", "Ред петнадесет")) == base
    assert rules.mention_hash(HOME_TEXT.replace("15.11.2026", "22.11.2026")) != base
    assert rules.mention_hash("# Добре дошли\n\nНовини") == ""


@pytest.mark.asyncio
async def test_a_new_mention_is_read_and_its_date_kept_as_shown():
    calls = _Calls([_date_fact()], minor=[1])
    report = await stage.check_school(_school(), _fetcher({HOME: HOME_TEXT}), calls, TODAY)
    assert [schema for schema, _ in calls.messages] == [DateFacts, Graded]
    assert report["outcome"] == "1 added, 0 removed"
    # "minor" for choosing a school, but an upcoming open day still shows at a glance.
    assert [(row["open_days"], row["rank"], row["source_url"]) for row in report["add"]] == [
        (["2026-11-15"], "more", HOME)
    ]
    assert report["watch"] == {HOME: rules.mention_hash(HOME_TEXT)}


@pytest.mark.asyncio
async def test_an_unchanged_mention_costs_no_model_call():
    school = _school(open_days_watch={"pages": {HOME: rules.mention_hash(HOME_TEXT)}})
    calls = _Calls()
    page = HOME_TEXT.replace("Посетители: 1234", "Посетители: 1240").replace("Ред 9", "Нова новина")
    report = await stage.check_school(school, _fetcher({HOME: page}), calls, TODAY)
    assert calls.messages == [] and report["outcome"] == "unchanged"


@pytest.mark.asyncio
async def test_past_and_far_undated_dates_and_quotes_not_on_a_page_are_dropped():
    facts = [
        _date_fact(days=["2026-03-17"]),
        _date_fact(days=["2027-09-15"], year_stated=False),
        _date_fact(quote="Ден на отворените врати – 1 декември"),
    ]
    calls = _Calls(facts)
    report = await stage.check_school(_school(), _fetcher({HOME: HOME_TEXT}), calls, TODAY)
    assert [row["dropped"] for row in report["facts"]] == [
        "past date",
        "date without a year too far ahead",
        "quote not on page",
    ]
    assert len(calls.messages) == 1 and report["outcome"] == "changed, nothing new"


def test_an_application_window_counts_until_it_closes():
    fact = DateFact(
        topic="admissions",
        fact_en="x",
        fact_bg="x",
        quote="q",
        source_url=HOME,
        applications_open="2026-09-01",
        applications_close="2026-10-20",
    )
    assert rules.upcoming(fact, TODAY) is None
    assert rules.upcoming(fact, datetime.date(2026, 10, 21)) == "past date"


@pytest.mark.asyncio
async def test_a_changed_headline_opens_its_article():
    article = "# Ден на отворените врати\n\nОчакваме ви на 15.11.2026 г.\n\n[Запишете се](https://school.example/form)"
    fact = _date_fact(quote="Очакваме ви на 15.11.2026 г.", url=NEWS, register_url="https://school.example/form")
    calls = _Calls([fact], key=[1])
    fetch = _fetcher({HOME: HOME_TEXT, NEWS: article})
    report = await stage.check_school(_school(), fetch, calls, TODAY)
    assert NEWS in fetch.opened and report["changed"] == [HOME, NEWS]
    row = report["add"][0]
    assert row["source_url"] == NEWS and row["register_url"] == "https://school.example/form" and row["rank"] == "key"


@pytest.mark.asyncio
async def test_a_stored_date_whose_quote_left_its_page_is_removed():
    stored = {
        "topic": "admissions",
        "text_i18n": {"bg": "Ден", "en": "Open day"},
        "source_url": ADMISSIONS,
        "quote": "Ден на отворените врати – 28.10.2026 г.",
        "rank": "more",
        "open_days": ["2026-10-28"],
    }
    other = {"topic": "meals", "text_i18n": {"bg": "Х", "en": "M"}, "source_url": HOME, "quote": "Кухня", "rank": "key"}
    school = _school(school_facts={"_schema_version": 1, "website_url": HOME, "facts": [stored, other]})
    admissions = "# Прием\n\n" + "Прием в 1. клас по документи.\n" * 20
    calls = _Calls()
    report = await stage.check_school(school, _fetcher({HOME: "# Добре дошли", ADMISSIONS: admissions}), calls, TODAY)
    assert calls.messages == [] and report["outcome"] == "0 added, 1 removed"
    assert stage.merged([stored, other], report["add"], report["remove"]) == [other]

    # A thin read of the page (a cookie wall, a timeout) removes nothing.
    report = await stage.check_school(school, _fetcher({HOME: "# Добре дошли", ADMISSIONS: "# Прием"}), calls, TODAY)
    assert report["outcome"] == "unchanged"


@pytest.mark.asyncio
async def test_home_links_named_for_open_days_are_opened():
    links = {
        HOME: [("https://school.example/otvoreni-vrati", "Отворени врати"), ("https://other.example/priem", "Прием")]
    }
    fetch = _fetcher({HOME: "# Добре дошли"}, links)
    await stage.check_school(_school(), fetch, _Calls(), TODAY)
    assert fetch.opened == [HOME, "https://school.example/otvoreni-vrati"]


class _Db:
    def __init__(self, fresh_attributes=None):
        self.commits, self.fresh_attributes = 0, fresh_attributes

    async def refresh(self, obj, attribute_names=None, with_for_update=None):
        assert attribute_names == ["attributes", "website_url"] and with_for_update
        if self.fresh_attributes is not None:
            obj.attributes = self.fresh_attributes

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_apply_stores_the_dates_and_hashes_and_dry_run_writes_nothing():
    school, db = _school(other=1), _Db()
    calls = _Calls([_date_fact()], key=[1])
    await stage.run_school(db, school, fetch=_fetcher({HOME: HOME_TEXT}), call=calls, apply=False, today=TODAY)
    assert school.attributes == {"other": 1} and db.commits == 0

    db = _Db(fresh_attributes={"other": 1, "social_links": {"facebook": "x"}})
    report = await stage.run_school(db, school, fetch=_fetcher({HOME: HOME_TEXT}), call=calls, apply=True, today=TODAY)
    attributes = school.attributes
    assert report["stored"] == 1 and db.commits == 1 and attributes["social_links"] == {"facebook": "x"}
    assert attributes["school_facts"]["website_url"] == HOME and attributes["school_facts"]["_schema_version"] == 1
    assert attributes["school_facts"]["facts"][0]["open_days"] == ["2026-11-15"]
    assert attributes["open_days_watch"]["pages"] == {HOME: rules.mention_hash(HOME_TEXT)}
    assert attributes["open_days_watch"]["dated"] == [HOME]


@pytest.mark.asyncio
async def test_dates_a_full_facts_read_dropped_are_read_again():
    # The hash did not change, but the page's stored date is gone: read it again once.
    school = _school(
        school_facts={"_schema_version": 1, "website_url": HOME, "facts": []},
        open_days_watch={"pages": {HOME: rules.mention_hash(HOME_TEXT)}, "dated": [HOME]},
    )
    calls = _Calls([_date_fact()], key=[1])
    report = await stage.check_school(school, _fetcher({HOME: HOME_TEXT}), calls, TODAY)
    assert report["outcome"] == "1 added, 0 removed"


@pytest.mark.asyncio
async def test_a_home_page_not_read_changes_nothing():
    school, db = _school(other=1), _Db()
    report = await stage.run_school(db, school, fetch=_fetcher({}), call=_Calls(), apply=True, today=TODAY)
    assert report["outcome"] == "not_read" and db.commits == 0 and school.attributes == {"other": 1}


def test_the_stage_is_tracked_for_the_cost_ledger():
    from app.services.pipeline_runs import stage_is_tracked

    assert stage_is_tracked(stage.STAGE)


def test_a_moved_sign_up_link_is_a_change():
    page = f"{ANNOUNCEMENT}\n\n[Запишете се](https://school.example/form-1)"
    assert rules.mention_hash(page) != rules.mention_hash(page.replace("form-1", "form-2"))


@pytest.mark.asyncio
async def test_a_page_redirected_to_another_site_is_not_read():
    async def fetch(url):
        if url == ADMISSIONS:
            return "https://other.example/news", HOME_TEXT, []
        return url, HOME_TEXT if url == HOME else "", []

    school = _school(open_days_watch={"pages": {ADMISSIONS: "x"}})
    calls = _Calls([_date_fact()], key=[1])
    report = await stage.check_school(school, fetch, calls, TODAY)
    assert report["changed"] == [HOME]
    assert {"url": ADMISSIONS, "note": "redirected to https://other.example/news"} in report["pages"]


@pytest.mark.asyncio
async def test_nothing_is_written_when_the_website_changed_during_the_check():
    class _MovedDb(_Db):
        async def refresh(self, obj, attribute_names=None, with_for_update=None):
            assert "website_url" in attribute_names
            obj.website_url = "https://new-site.example/"

        async def rollback(self):
            pass

    school, db = _school(other=1), _MovedDb()
    report = await stage.run_school(
        db, school, fetch=_fetcher({HOME: HOME_TEXT}), call=_Calls([_date_fact()], key=[1]), apply=True, today=TODAY
    )
    assert report["outcome"] == "website changed during the check" and db.commits == 0
    assert school.attributes == {"other": 1}


def test_a_passed_open_day_is_not_shown():
    from app.utils.school_facts import publishable_school_facts

    def row(day):
        return {
            "topic": "admissions",
            "text_i18n": {"bg": "Ден", "en": f"Open day on {day}"},
            "source_url": HOME,
            "rank": "more",
            "open_days": [day],
        }

    block = {"_schema_version": 1, "website_url": HOME, "facts": [row("2000-04-16"), row("2999-11-15")]}
    shown = publishable_school_facts({"school_facts": block}, HOME)
    assert [fact["open_days"] for fact in shown["facts"]] == [["2999-11-15"]]


def test_a_window_already_open_or_beside_a_past_open_day_is_kept():
    def window(**extra):
        return DateFact(topic="admissions", fact_en="x", fact_bg="x", quote="q", source_url=HOME, **extra)

    assert rules.upcoming(window(applications_open="2026-10-01", applications_close="2026-11-30"), TODAY) is None
    assert rules.upcoming(window(open_days=["2026-10-10"], applications_close="2026-11-30"), TODAY) is None


@pytest.mark.asyncio
async def test_the_same_line_with_a_new_sign_up_link_replaces_the_stored_fact():
    page = f"{ANNOUNCEMENT}\n\n[Запишете се](https://school.example/form-2)"
    stored = {
        "topic": "admissions",
        "text_i18n": {"bg": "Ден", "en": "Open day"},
        "source_url": HOME,
        "quote": ANNOUNCEMENT,
        "rank": "more",
        "open_days": ["2026-11-15"],
        "register_url": "https://school.example/form-1",
    }
    school = _school(school_facts={"_schema_version": 1, "website_url": HOME, "facts": [stored]})
    calls = _Calls([_date_fact(register_url="https://school.example/form-2")], key=[1])
    report = await stage.check_school(school, _fetcher({HOME: page}), calls, TODAY)
    facts = stage.merged([stored], report["add"], report["remove"])
    assert [row["register_url"] for row in facts] == ["https://school.example/form-2"]


def test_over_the_limit_a_fact_without_a_date_goes():
    others = [{"topic": "meals", "quote": str(n), "source_url": HOME, "rank": "more"} for n in range(20)]
    new = {"topic": "admissions", "quote": "d", "source_url": HOME, "rank": "minor", "open_days": ["2026-11-15"]}
    rows = stage.merged(others, [new], [])
    assert len(rows) == 20 and new in rows and others[-1] not in rows


@pytest.mark.asyncio
async def test_watched_pages_leave_room_for_a_changed_headlines_article():
    watched = {f"https://school.example/p{n}": "x" for n in range(6)}
    fetch = _fetcher({HOME: HOME_TEXT, NEWS: "Ден на отворените врати на 15.11.2026 г."})
    await stage.check_school(_school(open_days_watch={"pages": watched}), fetch, _Calls(), TODAY)
    assert NEWS in fetch.opened


def test_the_dated_passage_beats_a_menu_naming_open_days():
    menu = "\n".join(f"* [Пункт {n}](/p{n})" for n in range(30)) + "\n* [Дни на отворените врати](/otvoreni-vrati)\n"
    filler = "\n".join(
        f"Кандидатстване: общ текст за родителите, ред {n}, " + "дълго описание " * 20 for n in range(40)
    )
    page = menu * 2 + filler + "\n## Ден на отворените врати - 26.10.2026\nГрупа 1: 16:00-17:00"
    out = rules.excerpt(page)
    assert "26.10.2026" in out and len(out) <= rules.MAX_EXCERPT_CHARS + 50


def test_on_an_open_days_page_every_dated_line_is_read():
    page = "# Дни на отворените врати\n" + "\n".join(f"Текст {n}" for n in range(30)) + "\nДата: 14.11.2026 (събота)"
    assert "14.11.2026" in rules.excerpt(page, "https://s.example/events/open-days-2026-2027/")
    assert "14.11.2026" not in rules.excerpt(page, "https://s.example/news/")


@pytest.mark.asyncio
async def test_the_same_open_day_with_a_longer_quote_replaces_the_stored_fact():
    stored = {
        "topic": "admissions",
        "text_i18n": {"bg": "Ден", "en": "Open day"},
        "source_url": HOME,
        "quote": "Ден на отворените врати",
        "rank": "more",
        "open_days": ["2026-11-15"],
    }
    school = _school(school_facts={"_schema_version": 1, "website_url": HOME, "facts": [stored]})
    calls = _Calls([_date_fact()], key=[1])
    report = await stage.check_school(school, _fetcher({HOME: HOME_TEXT}), calls, TODAY)
    facts = stage.merged([stored], report["add"], report["remove"])
    assert [row["quote"] for row in facts] == [ANNOUNCEMENT] and report["facts"][0]["rank"] == "key"


def test_an_application_date_without_a_year_is_not_believed():
    # "Приемът в 1. клас започва на 21 април": last campaign's page, read in October.
    fact = DateFact(
        topic="admissions",
        fact_en="x",
        fact_bg="x",
        quote="q",
        source_url=HOME,
        applications_open="2027-04-21",
        year_stated=False,
    )
    assert rules.upcoming(fact, TODAY) == "application date without a year"
    assert (
        rules.upcoming(_date_fact(days=["2027-04-16"], year_stated=False), TODAY) == "date without a year too far ahead"
    )
    assert rules.upcoming(_date_fact(days=["2026-11-15"], year_stated=False), TODAY) is None
