"""School-facts stage: the quote checks, the judge, storing, and the publish boundary.

The finder and the model calls are stubbed.
"""

from __future__ import annotations

import pytest

from app.models import School
from app.scrapers.extraction.school_facts import Fact, Facts, Supported, quote_on_page
from app.scrapers.stages import school_facts as stage
from app.scrapers.stages.fees_agent import PageRead
from app.utils.school_facts import publishable_school_facts

HOME = "https://school.example/"
ABOUT = "https://school.example/about"
PAGES = [
    PageRead(HOME, "# Welcome\n\nУчебният ден започва в 08:00 часа и приключва в 14:00 часа.", False),
    PageRead(
        ABOUT,
        "* [Клуб „Дебати“](https://school.example/clubs) и клуб по шах\n\nМисията ни е качествено образование.",
        False,
    ),
    PageRead("https://school.example/gone", "", False, "robots.txt"),
]


def _fact(topic, quote, url=HOME, en="A fact."):
    return Fact(topic=topic, fact_en=en, fact_bg="Факт.", quote=quote, source_url=url)


def _pages(pages=PAGES):
    return [(page.url, page.text) for page in pages if page.text]


def test_quote_must_be_on_a_page_read():
    assert quote_on_page(_fact("school_day", "Учебният ден започва в 08:00 часа"), _pages()) == HOME
    assert quote_on_page(_fact("school_day", "Учебният ден започва в 07:30 часа"), _pages()) is None


def test_quote_matches_through_markdown_and_on_another_page():
    # The link's markup and the curly quotes do not count; the reader named the wrong page.
    fact = _fact("activities", 'Клуб "Дебати" и клуб по шах', url=HOME)
    assert quote_on_page(fact, _pages()) == ABOUT


def test_short_quote_is_not_evidence():
    assert quote_on_page(_fact("activities", "шах"), _pages()) is None


class _Calls:
    def __init__(self, facts, passed):
        self.facts, self.passed, self.messages = facts, passed, []

    async def __call__(self, prompt, schema, message, school_id):
        self.messages.append((schema, message))
        return Facts(facts=self.facts) if schema is Facts else Supported(passed=self.passed)


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


@pytest.mark.asyncio
async def test_read_keeps_facts_on_the_page_that_the_judge_confirms():
    facts = [
        _fact("school_day", "Учебният ден започва в 08:00 часа и приключва в 14:00 часа."),  # 1: kept
        _fact("curriculum", "Мисията ни е качествено образование.", url=ABOUT),  # 2: judged unsupported
        _fact("meals", "Обяд и две закуски за I-IV клас."),  # not on a page: never judged
        _fact("activities", "Клуб „Дебати“ и клуб по шах", url=ABOUT),  # 3: kept
    ]
    calls = _Calls(facts, passed=[1, 3])
    report = await stage.read_school(_school(), PAGES, calls)

    assert [f["dropped"] for f in report["facts"]] == [
        None,
        "quote does not state it",
        "quote not on page",
        None,
    ]
    assert [f["topic"] for f in report["store"]] == ["school_day", "activities"]  # topic order
    assert report["store"][1]["source_url"] == ABOUT
    judged = calls.messages[1][1]
    assert "Обяд" not in judged and judged.count("Quote:") == 3


@pytest.mark.asyncio
async def test_at_most_two_facts_a_topic():
    quote = "Учебният ден започва в 08:00 часа и приключва в 14:00 часа."
    facts = [_fact("school_day", quote, en=f"Fact {n}.") for n in range(3)]
    report = await stage.read_school(_school(), PAGES, _Calls(facts, passed=[1, 2, 3]))
    assert len(report["store"]) == 2
    assert report["facts"][2]["dropped"] == "over the topic or total limit"


@pytest.mark.asyncio
async def test_no_judge_call_when_no_quote_is_on_a_page():
    calls = _Calls([_fact("meals", "Обяд и две закуски за I-IV клас.")], passed=[1])
    report = await stage.read_school(_school(), PAGES, calls)
    assert report["store"] == [] and len(calls.messages) == 1


class _Db:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


async def _find(pages):
    async def find(school):
        return pages

    return find


@pytest.mark.asyncio
async def test_apply_replaces_stored_facts_and_dry_run_writes_nothing():
    quote = "Учебният ден започва в 08:00 часа и приключва в 14:00 часа."
    old = {"_schema_version": 1, "facts": [{"topic": "meals"}]}

    school, db = _school(other=1, school_facts=old), _Db()
    calls = _Calls([_fact("school_day", quote)], passed=[1])
    await stage.run_school(db, school, find=await _find(PAGES), call=calls, apply=False)
    assert school.attributes["school_facts"] == old and db.commits == 0

    report = await stage.run_school(db, school, find=await _find(PAGES), call=calls, apply=True)
    stored = school.attributes["school_facts"]
    assert report["stored"] == 1 and db.commits == 1 and school.attributes["other"] == 1
    assert stored["_schema_version"] == 1 and [f["topic"] for f in stored["facts"]] == ["school_day"]


@pytest.mark.asyncio
async def test_a_site_read_with_nothing_concrete_clears_the_facts():
    school = _school(school_facts={"_schema_version": 1, "facts": [{"topic": "meals"}]})
    await stage.run_school(_Db(), school, find=await _find(PAGES), call=_Calls([], passed=[]), apply=True)
    assert school.attributes["school_facts"]["facts"] == []


@pytest.mark.asyncio
async def test_a_site_not_read_keeps_the_stored_facts():
    old = {"_schema_version": 1, "facts": [{"topic": "meals"}]}
    school, db = _school(school_facts=old), _Db()
    unread = [PageRead(HOME, "", False, "robots.txt")]
    report = await stage.run_school(db, school, find=await _find(unread), call=_Calls([], []), apply=True)
    assert report["outcome"] == "nothing_read" and school.attributes["school_facts"] == old and db.commits == 0


def _fact_row(**overrides):
    return {
        "topic": "meals",
        "text_i18n": {"bg": "Обяд.", "en": "Lunch."},
        "source_url": HOME,
        "quote": "x",
    } | overrides


def test_publishable_facts_drop_malformed_rows_and_the_quote():
    block = {
        "_schema_version": 1,
        "read_at": "2026-10-13T20:00:00+00:00",
        "facts": [
            _fact_row(),
            _fact_row(topic="gossip"),
            _fact_row(source_url="javascript:alert(1)"),
            _fact_row(text_i18n={"en": "Lunch."}),
            "not a fact",
        ],
    }
    published = publishable_school_facts({"school_facts": block})
    assert published == {
        "read_at": "2026-10-13T20:00:00+00:00",
        "facts": [{"topic": "meals", "text_i18n": {"bg": "Обяд.", "en": "Lunch."}, "source_url": HOME}],
    }
    assert publishable_school_facts({"school_facts": block | {"_schema_version": 2}}) is None
    assert publishable_school_facts({"school_facts": block | {"facts": []}}) is None


async def _detail(client, db_session, **attrs):
    school = School(
        name_i18n={"bg": "Училище", "en": "School"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url=HOME,
        scrape_status="extracted",
        attributes=attrs,
    )
    db_session.add(school)
    await db_session.commit()
    response = await client.get(f"/schools/{school.id}")
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_detail_response_shows_facts_without_quotes(client, db_session):
    body = await _detail(
        client, db_session, school_facts={"_schema_version": 1, "read_at": "t", "facts": [_fact_row()]}
    )
    assert body["school_facts"]["facts"] == [
        {"topic": "meals", "text_i18n": {"bg": "Обяд.", "en": "Lunch."}, "source_url": HOME}
    ]
    assert "school_facts" not in (body.get("attributes") or {})


@pytest.mark.asyncio
async def test_withheld_website_data_hides_the_facts(client, db_session):
    block = {"_schema_version": 1, "read_at": "t", "facts": [_fact_row()]}
    body = await _detail(client, db_session, school_facts=block, website_data_withheld=True)
    assert body["school_facts"] is None
