"""School-facts stage: the quote checks, the judge, storing, and the publish boundary.

The finder and the model calls are stubbed.
"""

from __future__ import annotations

import pytest

from app.models import School
from app.scrapers.extraction.school_facts import Fact, Facts, Supported, judge_message, quote_on_page
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


LINES = [PageRead(HOME, "\n".join(f"Ред номер {n} е достатъчно дълъг." for n in range(12)), False)]


def _line(n):
    return f"Ред номер {n} е достатъчно дълъг."


@pytest.mark.asyncio
async def test_at_most_two_facts_a_topic():
    facts = [_fact("school_day", _line(n), en=f"Fact {n}.") for n in range(3)]
    report = await stage.read_school(_school(), LINES, _Calls(facts, passed=[1, 2, 3]))
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
    assert stored["website_url"] == HOME


@pytest.mark.asyncio
async def test_a_read_with_no_fact_kept_keeps_the_stored_facts():
    # Often a partial crawl: a thin home page and the rest timed out.
    old = {"_schema_version": 1, "facts": [{"topic": "meals"}]}
    school, db = _school(school_facts=old), _Db()
    report = await stage.run_school(db, school, find=await _find(PAGES), call=_Calls([], passed=[]), apply=True)
    assert school.attributes["school_facts"] == old and db.commits == 0 and report["kept_stored_facts"]


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
        "website_url": "https://www.school.example",
        "facts": [
            _fact_row(),
            _fact_row(topic="gossip"),
            _fact_row(source_url="javascript:alert(1)"),
            _fact_row(text_i18n={"en": "Lunch."}),
            "not a fact",
        ],
    }
    published = publishable_school_facts({"school_facts": block}, HOME)
    assert published == {
        "read_at": "2026-10-13T20:00:00+00:00",
        "facts": [{"topic": "meals", "text_i18n": {"bg": "Обяд.", "en": "Lunch."}, "source_url": HOME}],
    }
    assert publishable_school_facts({"school_facts": block | {"_schema_version": 2}}, HOME) is None
    assert publishable_school_facts({"school_facts": block | {"facts": []}}, HOME) is None


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
        client,
        db_session,
        school_facts={"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": [_fact_row()]},
    )
    assert body["school_facts"]["facts"] == [
        {"topic": "meals", "text_i18n": {"bg": "Обяд.", "en": "Lunch."}, "source_url": HOME}
    ]
    assert "school_facts" not in (body.get("attributes") or {})


@pytest.mark.asyncio
async def test_withheld_website_data_hides_the_facts(client, db_session):
    block = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": [_fact_row()]}
    body = await _detail(client, db_session, school_facts=block, website_data_withheld=True)
    assert body["school_facts"] is None


def test_facts_show_only_for_the_site_they_were_read_from():
    block = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": [_fact_row()]}
    assert publishable_school_facts({"school_facts": block}, "https://www.school.example") is not None
    assert publishable_school_facts({"school_facts": block}, "https://other.example/") is None
    assert publishable_school_facts({"school_facts": block}, None) is None
    assert publishable_school_facts({"school_facts": block | {"website_url": None}}, HOME) is None


@pytest.mark.asyncio
async def test_the_limit_cuts_in_the_readers_order_and_shows_in_topic_order():
    facts = [_fact("admissions", _line(0), en="first")] + [_fact("curriculum", _line(n), en=f"c{n}") for n in (1, 2, 3)]
    topics = ("languages", "meals", "transport", "activities", "facilities", "support")
    facts += [_fact(topic, _line(n), en=topic) for n, topic in enumerate(topics, 4)]
    report = await stage.read_school(_school(), LINES, _Calls(facts, passed=list(range(1, 11))))
    kept = [f["text_i18n"]["en"] for f in report["store"]]
    assert len(kept) == 8 and "first" in kept and kept[-1] == "first"  # admissions shows last
    assert "support" not in kept and kept.count("c1") + kept.count("c2") == 2 and "c3" not in kept


def test_a_long_page_does_not_push_later_pages_out_of_the_read():
    long = PageRead("https://school.example/staff", "x" * 60_000, False)
    later = PageRead("https://school.example/admissions", "Приемът е с тест.", False)
    text = stage.page_text([long, PAGES[2], later])
    assert "Приемът е с тест." in text and len(text) < 20_000 and "/gone" not in text


@pytest.mark.asyncio
async def test_one_line_split_into_several_facts_keeps_the_first():
    facts = [_fact("school_day", _line(1), en="a"), _fact("languages", _line(1), en="b")]
    report = await stage.read_school(_school(), LINES, _Calls(facts, passed=[1]))
    assert [f["dropped"] for f in report["facts"]] == [None, "same quote as an earlier fact"]


def test_a_short_quote_must_be_a_whole_line():
    page = [(HOME, "### [Таекуондо](https://school.example/taekwondo/)\nТаекуондо клубът е затворен. , ок")]
    assert quote_on_page(_fact("activities", "### [Таекуондо](https://school.example/taekwondo/)"), page) == HOME
    assert quote_on_page(_fact("activities", "клубът"), page) is None


def test_a_space_before_a_comma_does_not_break_the_match():
    page = [(HOME, "Образование , което съчетава подготовка с интензивен английски.")]
    assert quote_on_page(_fact("languages", "Образование, което съчетава подготовка"), page) == HOME


def test_the_menu_every_page_repeats_is_left_out_of_the_read():
    menu = "Начало\nЗа нас\nКонтакти"
    pages = [PageRead(f"https://school.example/{n}", f"{menu}\nСтраница {n} има свой текст.", False) for n in range(4)]
    text = stage.page_text(pages)
    assert "Контакти" not in text and all(f"Страница {n}" in text for n in range(4))


def test_a_list_quote_matches_row_by_row():
    page = [
        (
            HOME,
            "Двусменен режим:\n\nІ срок\n1. V – VІІІ клас – първа смяна от 7:30 до 13:15 часа\n2. ІХ-ХІІ клас – втора смяна",
        )
    ]
    rows = "Двусменен режим:\n1. V – VІІІ клас – първа смяна от 7:30 до 13:15 часа\n2. ІХ-ХІІ клас – втора смяна"
    assert quote_on_page(_fact("school_day", rows), page) == HOME
    assert quote_on_page(_fact("school_day", rows + "\n3. Нов ред, който го няма"), page) is None


def test_the_judge_sees_the_page_of_each_quote():
    message = judge_message([(_fact("languages", "ниво B2"), "https://school.example/anglijski-ezik/")])
    assert "Page: https://school.example/anglijski-ezik/" in message


@pytest.mark.asyncio
async def test_a_full_read_with_no_fact_kept_replaces_the_stored_facts():
    old = {"_schema_version": 1, "facts": [{"topic": "meals"}]}
    pages = [PageRead(f"https://school.example/{n}", f"Страница {n} без конкретни факти.", False) for n in range(4)]
    school = _school(school_facts=old)
    await stage.run_school(_Db(), school, find=await _find(pages), call=_Calls([], passed=[]), apply=True)
    assert school.attributes["school_facts"]["facts"] == []


def test_a_moved_website_on_the_same_host_hides_the_facts():
    block = {
        "_schema_version": 1,
        "read_at": "t",
        "website_url": "https://example.bg/school-a/",
        "facts": [_fact_row()],
    }
    assert publishable_school_facts({"school_facts": block}, "https://www.example.bg/school-a") is not None
    assert publishable_school_facts({"school_facts": block}, "https://example.bg/school-b/") is None


@pytest.mark.asyncio
async def test_a_rejected_website_hides_the_facts(client, db_session):
    block = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": [_fact_row()]}
    school = School(
        name_i18n={"bg": "Училище", "en": "School"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url=HOME,
        scrape_status="failed_validate",
        attributes={"school_facts": block},
    )
    db_session.add(school)
    await db_session.commit()
    body = (await client.get(f"/schools/{school.id}")).json()
    assert body["school_facts"] is None


@pytest.mark.asyncio
async def test_a_stored_fact_whose_quote_is_still_on_the_page_is_carried():
    still = _fact_row(topic="school_day", quote=_line(2), text_i18n={"bg": "Б", "en": "Still there."})
    gone = _fact_row(topic="meals", quote="Обяд и две закуски за I-IV клас.", text_i18n={"bg": "Б", "en": "Gone."})
    school = _school(school_facts={"_schema_version": 1, "facts": [still, gone]})
    new = [_fact("languages", _line(5), en="New.")]
    report = await stage.read_school(school, LINES, _Calls(new, passed=[1]))
    assert [f["text_i18n"]["en"] for f in report["store"]] == ["New.", "Still there."]
    assert report["carried"] == ["Still there."]


def test_a_link_quoted_without_its_address_matches():
    page = [
        (
            HOME,
            "2026– 2031 | Профил „Икономическо развитие“\n[Училищен учебен план / 8. В](https://school.example/plan.pdf)",
        )
    ]
    quote = "2026– 2031 | Профил „Икономическо развитие“\n[Училищен учебен план / 8. В]"
    assert quote_on_page(_fact("curriculum", quote), page) == HOME
