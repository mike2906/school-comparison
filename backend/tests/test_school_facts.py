"""School-facts stage: the quote checks, the judge, storing, and the publish boundary.

The finder and the model calls are stubbed.
"""

from __future__ import annotations

import pytest

from app.models import School
from app.scrapers.extraction.school_facts import Fact, Facts, Graded, LanguageLabel, judge_message, quote_on_page
from app.scrapers.stages import school_facts as stage
from app.scrapers.stages.fees_agent import PageRead
from app.utils.school_facts import _labels, publishable_school_facts

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
    """The reader returns ``facts``; the judge grades ``passed`` key, ``more`` more, ``minor`` minor."""

    def __init__(self, facts, passed, more=(), minor=()):
        self.facts, self.messages = facts, []
        self.grades = Graded(key=list(passed), more=list(more), minor=list(minor))

    async def __call__(self, prompt, schema, message, school_id):
        self.messages.append((schema, message))
        return Facts(facts=self.facts) if schema is Facts else self.grades


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
    assert [f["topic"] for f in report["store"]] == ["school_day", "activities"]  # the reader's order
    assert report["store"][1]["source_url"] == ABOUT
    judged = calls.messages[1][1]
    assert "Обяд" not in judged and judged.count("Quote:") == 3


LINES = [PageRead(HOME, "\n".join(f"Ред номер {n} е достатъчно дълъг." for n in range(12)), False)]


def _line(n):
    return f"Ред номер {n} е достатъчно дълъг."


@pytest.mark.asyncio
async def test_facts_are_stored_key_first_with_their_grade():
    facts = [_fact("activities", _line(0), en="club"), _fact("languages", _line(1), en="lang")]
    facts.append(_fact("facilities", _line(2), en="room"))
    report = await stage.read_school(_school(), LINES, _Calls(facts, passed=[2], more=[1], minor=[3]))
    assert [(f["text_i18n"]["en"], f["rank"]) for f in report["store"]] == [
        ("lang", "key"),
        ("club", "more"),
        ("room", "minor"),
    ]
    assert [f["rank"] for f in report["facts"]] == ["more", "key", "minor"]


@pytest.mark.asyncio
async def test_at_most_twenty_facts_are_stored():
    facts = [_fact("activities", f"Ред номер {n} е достатъчно дълъг за факт.", en=f"f{n}") for n in range(21)]
    page = [PageRead(HOME, "\n".join(f.quote for f in facts), False)]
    report = await stage.read_school(_school(), page, _Calls(facts, passed=range(1, 22)))
    assert len(report["store"]) == 20 and report["facts"][20]["dropped"] == "over the limit"


@pytest.mark.asyncio
async def test_labels_are_stored_and_shown_to_the_judge():
    fact = _fact("languages", _line(1), en="Intensive English.")
    fact.tags = ["extended_day"]
    fact.languages = [LanguageLabel(language="English", level="enrichment")]
    fact.max_class_size = 18
    calls = _Calls([fact], passed=[1])
    report = await stage.read_school(_school(), LINES, calls)
    stored = report["store"][0]
    assert stored["tags"] == ["extended_day"] and stored["max_class_size"] == 18
    assert stored["languages"] == [{"language": "English", "level": "enrichment", "hours_per_week": None}]
    judged = calls.messages[1][1]
    assert "Tags: extended_day" in judged and "English (enrichment)" in judged and "Max class size: 18" in judged


@pytest.mark.asyncio
async def test_no_judge_call_when_no_quote_is_on_a_page():
    calls = _Calls([_fact("meals", "Обяд и две закуски за I-IV клас.")], passed=[1])
    report = await stage.read_school(_school(), PAGES, calls)
    assert report["store"] == [] and len(calls.messages) == 1


class _Db:
    def __init__(self, fresh_attributes=None):
        self.commits = 0
        self.fresh_attributes = fresh_attributes  # what another stage wrote during the read

    async def refresh(self, obj, attribute_names=None, with_for_update=None):
        assert attribute_names == ["attributes"] and with_for_update
        if self.fresh_attributes is not None:
            obj.attributes = self.fresh_attributes

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
    # Two pages read is a partial crawl: it may have missed the language pages.
    assert "languages_read" not in stored
    assert "languages_read" not in stage.facts_block([], "m", HOME)  # the open-days check's block


@pytest.mark.asyncio
async def test_only_a_full_read_says_every_language_was_read():
    quote = "Учебният ден започва в 08:00 часа и приключва в 14:00 часа."
    extra = [PageRead(f"https://school.example/p{n}", f"Страница {n}.", False) for n in range(2)]
    school, db = _school(), _Db()
    calls = _Calls([_fact("school_day", quote)], passed=[1])
    await stage.run_school(db, school, find=await _find(PAGES + extra), call=calls, apply=True)
    assert school.attributes["school_facts"]["languages_read"] is True


@pytest.mark.asyncio
async def test_apply_keeps_keys_another_stage_wrote_during_the_read():
    quote = "Учебният ден започва в 08:00 часа и приключва в 14:00 часа."
    school = _school(school_facts={"_schema_version": 1, "facts": []})
    db = _Db(fresh_attributes={"social_links": {"facebook": "x"}})
    await stage.run_school(
        db, school, find=await _find(PAGES), call=_Calls([_fact("school_day", quote)], passed=[1]), apply=True
    )
    assert school.attributes["social_links"] == {"facebook": "x"}
    assert [f["topic"] for f in school.attributes["school_facts"]["facts"]] == ["school_day"]


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
            _fact_row(tags=["cafeteria", "made_up"], languages=[{"language": "английски", "level": "odd"}]),
            _fact_row(topic="gossip"),
            _fact_row(source_url="javascript:alert(1)"),
            _fact_row(text_i18n={"en": "Lunch."}),
            "not a fact",
        ],
    }
    published = publishable_school_facts({"school_facts": block}, HOME)
    assert published["read_at"] == "2026-10-13T20:00:00+00:00" and len(published["facts"]) == 1
    fact = published["facts"][0]
    assert "quote" not in fact
    assert {key: fact[key] for key in ("topic", "text_i18n", "source_url", "rank", "tags", "languages")} == {
        "topic": "meals",
        "text_i18n": {"bg": "Обяд.", "en": "Lunch."},
        "source_url": HOME,
        "rank": "key",
        "tags": ["cafeteria"],
        "languages": [{"language": "English", "level": None, "hours_per_week": None}],
    }
    assert publishable_school_facts({"school_facts": block | {"_schema_version": 2}}, HOME) is None
    assert publishable_school_facts({"school_facts": block | {"facts": []}}, HOME) is None


def test_only_the_first_key_facts_stay_key():
    rows = [_fact_row(topic="curriculum", text_i18n={"bg": "Б", "en": f"c{n}"}) for n in range(3)]
    rows += [_fact_row(topic=topic) for topic in ("languages", "meals", "transport", "activities", "support")]
    rows += [_fact_row(topic="facilities", text_i18n={"bg": "Б", "en": f"f{n}"}) for n in range(2)]
    rows.append(_fact_row(rank="minor"))
    block = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": rows}
    ranks = [f["rank"] for f in publishable_school_facts({"school_facts": block}, HOME)["facts"]]
    # Two curriculum facts, five topics and one facilities fact make 8; the rest show as "more".
    assert ranks == ["key", "key", "more", "key", "key", "key", "key", "key", "key", "more", "minor"]


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
        {"topic": "meals", "text_i18n": {"bg": "Обяд.", "en": "Lunch."}, "source_url": HOME, "rank": "key"}
    ]
    assert "school_facts" not in (body.get("attributes") or {})
    assert "school_facts_published" not in (body.get("attributes") or {})


@pytest.mark.asyncio
async def test_fact_labels_feed_the_filters_languages_and_class_size(client, db_session):
    rows = [
        _fact_row(tags=["meals_provided", "cafeteria", "transportation"]),
        _fact_row(topic="languages", languages=[{"language": "English", "level": "enrichment"}], rank="more"),
        _fact_row(topic="class_size", max_class_size=16, rank="minor"),
    ]
    body = await _detail(
        client, db_session, school_facts={"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": rows}
    )
    tags = body["attributes"]["filter_tags"]
    assert "cafeteria" in tags["facilities"] and "transportation" in tags["facilities"]
    assert "meals_provided" in tags["special_programs"]
    assert {"language": "English", "level": "enrichment"} in body["attributes_i18n"]["en"]["language_focus"]
    assert body["attributes"]["class_size"] == 16
    assert [f["rank"] for f in body["school_facts"]["facts"]] == ["key", "more"]  # minor is not shown


@pytest.mark.parametrize("languages_read,shown", [(False, ["English", "Russian"]), (True, ["English"])])
def test_a_read_listing_every_language_replaces_the_old_extractions(languages_read, shown):
    from app.utils.school_attributes import build_localized_attributes
    from app.utils.school_facts import PUBLISHED_KEY

    # 625: the old extraction listed Russian, German and Italian; the site names only English.
    block = {
        "_schema_version": 1,
        "read_at": "t",
        "website_url": HOME,
        "facts": [_fact_row(topic="languages", languages=[{"language": "English"}])],
    }
    if languages_read:
        block["languages_read"] = True
    published = publishable_school_facts({"school_facts": block}, HOME)
    attrs = {"extracted": {"languages": ["Russian"]}, PUBLISHED_KEY: published}
    focus = build_localized_attributes(attrs, "en", "private")["language_focus"]
    assert sorted(entry["language"] for entry in focus) == shown


@pytest.mark.parametrize(
    "text",
    [
        "Russian or another foreign language can also be studied if parents are interested.",
        "Additional Russian or another foreign language available on parental interest",
        "Spanish is offered at parents' request",
    ],
)
def test_a_language_offered_only_on_request_is_not_labelled(text):
    fact = _fact_row(topic="languages", text_i18n={"bg": "x", "en": text}, languages=[{"language": "Russian"}])
    assert _labels(fact)["languages"] == []


def test_a_taught_language_keeps_its_label():
    fact = _fact_row(topic="languages", text_i18n={"bg": "x", "en": "Russian is taught from grade 5"}, languages=[{"language": "Russian"}])
    assert [item["language"] for item in _labels(fact)["languages"]] == ["Russian"]


def test_a_read_that_kept_no_language_leaves_the_old_extractions():
    from app.utils.school_attributes import build_localized_attributes
    from app.utils.school_facts import PUBLISHED_KEY

    # 267 (НПМГ): a full read kept facts, none naming a language; its old list stays.
    block = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "languages_read": True, "facts": [_fact_row()]}
    published = publishable_school_facts({"school_facts": block}, HOME)
    attrs = {"extracted": {"languages": ["German"]}, PUBLISHED_KEY: published}
    focus = build_localized_attributes(attrs, "en", "private")["language_focus"]
    assert [entry["language"] for entry in focus] == ["German"]


@pytest.mark.asyncio
async def test_the_languages_read_marker_stays_internal(client, db_session):
    block = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "languages_read": True, "facts": [_fact_row()]}
    body = await _detail(client, db_session, school_facts=block)
    assert "languages_read" not in body["school_facts"]


@pytest.mark.asyncio
async def test_fact_labels_of_hidden_facts_do_not_feed_the_filters(client, db_session):
    block = {
        "_schema_version": 1,
        "read_at": "t",
        "website_url": "https://elsewhere.example/",
        "facts": [_fact_row(tags=["transportation"])],
    }
    body = await _detail(client, db_session, school_facts=block)
    assert "transportation" not in body["attributes"]["filter_tags"]["facilities"]
    assert body["school_facts"] is None


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


def test_a_long_page_does_not_push_later_pages_out_of_the_read():
    long = PageRead("https://school.example/staff", "x" * 60_000, False)
    later = PageRead("https://school.example/admissions", "Приемът е с тест.", False)
    text = stage.page_text([long, PAGES[2], later])
    assert "Приемът е с тест." in text and len(text) < 20_000 and "/gone" not in text


@pytest.mark.asyncio
async def test_one_line_split_into_several_facts_on_a_topic_keeps_the_first():
    facts = [
        _fact("school_day", _line(1), en="a"),
        _fact("school_day", _line(1), en="b"),
        _fact("languages", _line(1), en="c"),
    ]
    report = await stage.read_school(_school(), LINES, _Calls(facts, passed=[1, 2]))
    assert [f["dropped"] for f in report["facts"]] == [None, "same quote as an earlier fact", None]


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


def test_the_judge_reads_admission_grade_0_as_kindergarten():
    # 545, 616: "accepts children aged 2 to 7" failed against a bare "Admission grade: 0".
    fact = _fact("admissions", "ЧДГ приема деца на възраст от 2 до 7 год.")
    fact.admission_grade = 0
    assert "Admission grade: 0 (kindergarten or preschool class)" in judge_message([(fact, HOME)])


def test_emoji_pictures_and_emoji_in_quotes_match():
    # 609: WordPress shows emoji as pictures; the reader quotes the emoji itself.
    page = [
        (HOME, "![✅](https://s.w.org/images/core/emoji/17.0.2/svg/2705.svg) Заявления се приемат от 02.02.2026 г.")
    ]
    assert quote_on_page(_fact("admissions", "✅ Заявления се приемат от 02.02.2026 г."), page) == HOME


def test_the_judge_sees_the_heading_above_the_quote():
    page = (
        "Меню\n\n## Kандидатстване\n\nКандидатстване за II-VII клас за учебната 2027/2028 г.\n\n"
        "Дати изпит I кръг: очаквайте през месец февруари\n\n"
        "При успешно преминаване на I кръг следва покана за II кръг. Той е индивидуален и се състои от изпит "
        "по английски език и разговор с психолог."
    )
    quote = "При успешно преминаване на I кръг следва покана за II кръг."
    message = judge_message([(_fact("admissions", quote), HOME)], [(HOME, page)])
    assert "Above the quote: kандидатстване / кандидатстване за ii-vii клас" in message
    assert "дати изпит i кръг" in message
    assert "Above the quote" not in judge_message([(_fact("admissions", "Няма го"), HOME)], [(HOME, page)])


def test_stored_fact_pages_put_key_pages_first():
    from app.scrapers.stages.school_facts import stored_fact_pages

    facts = [
        {"source_url": "https://s.example/a", "rank": "more"},
        {"source_url": "https://s.example/a", "rank": "more"},
        {"source_url": "https://s.example/b", "rank": "key"},
        {"source_url": "https://s.example/c", "rank": "key"},
        {"source_url": "https://s.example/c", "rank": "key"},
        {"rank": "key"},
    ]
    school = School(id=1, name_i18n={}, country_code="bg", attributes={"school_facts": {"facts": facts}})
    assert stored_fact_pages(school) == ["https://s.example/c", "https://s.example/b", "https://s.example/a"]
    assert stored_fact_pages(School(id=2, name_i18n={}, country_code="bg")) == []


def test_ticks_crosses_and_circled_numbers_keep_their_meaning():
    from app.scrapers.extraction.school_facts import _normalise

    assert _normalise("✘ Училищен транспорт") != _normalise("✔ Училищен транспорт")
    assert _normalise("✔ Училищен транспорт") == _normalise("✅ Училищен транспорт")
    assert _normalise("Група ①") != _normalise("Група ②")
    assert _normalise("👉 Дата на провеждане") == _normalise("Дата на провеждане")


def test_context_comes_from_the_whole_line_match():
    from app.scrapers.extraction.school_facts import quote_context

    page = "Прием в I клас\nНе се изисква изпит по математика.\nПрием в V клас\nИзпит по математика"
    assert quote_context("Изпит по математика", page)[-1] == "прием в v клас"


@pytest.mark.parametrize(
    ("text", "today", "past"),
    [
        ("For 2025/2026 the school takes 14 pupils into grade 1.", "2026-10-19", True),
        ("For 2026/2027 the school takes 14 pupils into grade 1.", "2026-10-19", False),
        ("For 2027/28 the preparatory class takes 70 children.", "2026-10-19", False),
        ("For 2026-2027 the school takes 14 pupils.", "2027-09-01", True),
        ("Grade 8 has an entrance exam in maths.", "2030-01-01", False),
        ("Children born 2019-2020 are admitted to the preparatory group.", "2026-10-19", False),
        ("The exam is on 2025-06-10.", "2026-10-19", False),
    ],
)
def test_admissions_facts_for_a_past_school_year_are_hidden(text, today, past):
    from app.utils.school_facts import _past_school_years_only

    assert _past_school_years_only(text, today) is past


def test_admission_links_match_both_alphabets_and_k():
    from app.scrapers.extraction.school_facts import ADMISSION_LINK_RE

    for text in (
        "https://www.svetasofia.com/kandidatstvane/ Kандидатстване",
        "/priem-v-parvi-klas/",
        "Прием",
        "/admission/",
        "Приемни изпити",
    ):
        assert ADMISSION_LINK_RE.search(text)
    for text in (
        "https://www.svetasofia.com/drugite-za-nas/ot-decata От децата",
        "/priemno-vreme/ Приемно време на директора",
        "/predpriemachestvo/ Предприемачество",
        "/kandidati-za-rabota/ Кариери",
    ):
        assert not ADMISSION_LINK_RE.search(text)


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
@pytest.mark.parametrize(
    ("status", "shown"),
    [
        ("failed_validate", False),
        ("no_official_website", False),
        ("pending", False),
        (None, False),
        ("validated", True),
        ("extraction_failed", True),
    ],
)
async def test_facts_show_only_for_a_website_url_validation_accepted(client, db_session, status, shown):
    block = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": [_fact_row()]}
    school = School(
        name_i18n={"bg": "Училище", "en": "School"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url=HOME,
        scrape_status=status,
        attributes={"school_facts": block},
    )
    db_session.add(school)
    await db_session.commit()
    body = (await client.get(f"/schools/{school.id}")).json()
    assert (body["school_facts"] is not None) is shown


@pytest.mark.asyncio
async def test_a_stored_fact_whose_quote_is_still_on_the_page_is_carried():
    still = _fact_row(topic="school_day", quote=_line(2), text_i18n={"bg": "Б", "en": "Still there."})
    gone = _fact_row(topic="meals", quote="Обяд и две закуски за I-IV клас.", text_i18n={"bg": "Б", "en": "Gone."})
    school = _school(school_facts={"_schema_version": 1, "facts": [still, gone]})
    new = [_fact("languages", _line(5), en="New.")]
    report = await stage.read_school(school, LINES, _Calls(new, passed=[1]))
    assert [f["text_i18n"]["en"] for f in report["store"]] == ["New.", "Still there."]
    assert report["carried"] == ["Still there."]


@pytest.mark.asyncio
async def test_a_carried_fact_loses_a_sign_up_link_the_pages_no_longer_have():
    row = _fact_row(
        topic="admissions",
        quote=_line(2),
        text_i18n={"bg": "Б", "en": "Open day."},
        open_days=["2026-11-12"],
        register_url="https://forms.example/old",
    )
    school = _school(school_facts={"_schema_version": 1, "facts": [row]})
    report = await stage.read_school(school, LINES, _Calls([_fact("languages", _line(5), en="New.")], passed=[1]))
    carried = [f for f in report["store"] if f["text_i18n"]["en"] == "Open day."][0]
    assert carried["open_days"] == ["2026-11-12"] and carried["register_url"] is None
    linked = [PageRead(HOME, LINES[0].text + "\n[Запиши се](https://forms.example/old)", False)]
    report = await stage.read_school(school, linked, _Calls([_fact("languages", _line(5), en="New.")], passed=[1]))
    assert [f["register_url"] for f in report["store"] if f["text_i18n"]["en"] == "Open day."] == [
        "https://forms.example/old"
    ]


def test_a_link_quoted_without_its_address_matches():
    page = [
        (
            HOME,
            "2026– 2031 | Профил „Икономическо развитие“\n[Училищен учебен план / 8. В](https://school.example/plan.pdf)",
        )
    ]
    quote = "2026– 2031 | Профил „Икономическо развитие“\n[Училищен учебен план / 8. В]"
    assert quote_on_page(_fact("curriculum", quote), page) == HOME


@pytest.mark.asyncio
async def test_a_fact_tag_makes_the_school_match_the_filter(seeded_client, seeded_db):
    from app.models.school import SchoolLocation

    rows = [_fact_row(topic="transport", tags=["transportation"])]
    school = School(
        name_i18n={"bg": "Училище с автобус", "en": "Bus school"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url=HOME,
        scrape_status="validated",
        attributes={"school_facts": {"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": rows}},
    )
    seeded_db.add(school)
    await seeded_db.flush()
    seeded_db.add(
        SchoolLocation(school_id=school.id, address_i18n={"bg": "ул. 1"}, lat=42.69, lng=23.32, is_primary=True)
    )
    await seeded_db.commit()

    listed = (await seeded_client.get("/schools?facilities=transportation")).json()
    rows = {row["id"]: row for row in (listed["items"] if isinstance(listed, dict) else listed)}
    assert "transportation" in rows[school.id]["attributes"]["filter_tags"]["facilities"]  # the cards' counts
    assert "website_url" not in rows[school.id]
    options = (await seeded_client.get("/schools/filters")).json()
    assert "transportation" in options["facilities"]


def test_at_a_glance_sums_up_the_labels_and_drops_a_closed_window():
    import datetime

    from app.utils.school_facts import at_a_glance

    facts = [
        {
            "admission": ["entrance_exam"],
            "admission_grade": 5,
            "exam_subjects": [{"bg": "Математика", "en": "Maths"}, {"bg": "Английски", "en": "English"}],
        },
        {"admission": ["interview", "trial_day"], "admission_grade": 1, "places": "full"},
        {"highest_grade": 4, "curricula": ["cambridge"], "bus_areas": [{"bg": "Бояна", "en": "Boyana"}]},
        {"applications_open": "2026-03-01", "applications_close": "2026-04-30"},
    ]
    glance = at_a_glance(facts, "2026-03-20T10:00:00", today=datetime.date(2026, 4, 1))
    assert glance["admission"][0] == {
        "grade": 5,
        "grade_to": None,
        "types": ["entrance_exam"],
        "subjects": [{"bg": "Математика", "en": "Maths"}, {"bg": "Английски", "en": "English"}],
    }
    assert glance["places"] == [{"grade": 1, "grade_to": None, "status": "full"}]
    assert glance["highest_grade"] == 4 and glance["curricula"] == ["cambridge"]
    assert glance["applications"] == {"open": "2026-03-01", "close": "2026-04-30"}
    assert at_a_glance(facts, today=datetime.date(2026, 5, 1))["applications"] is None


def test_at_a_glance_merges_grades_and_ages_out_stale_dates_and_places():
    import datetime

    from app.utils.school_facts import at_a_glance

    facts = [
        {"admission": ["entrance_exam"], "admission_grade": 5, "exam_subjects": [{"bg": "Математика", "en": "Maths"}]},
        {"admission": ["interview"], "admission_grade": 5, "places": "waiting_list"},
        {"highest_grade": 4},
        {"highest_grade": 12},
        {"applications_open": "2025-03-01", "applications_close": "2025-04-30"},
        {"applications_open": "2026-11-01", "applications_close": "2026-12-15"},
    ]
    today = datetime.date(2026, 10, 14)
    glance = at_a_glance(facts, "2026-10-01", today=today)
    assert glance["admission"] == [
        {
            "grade": 5,
            "grade_to": None,
            "types": ["entrance_exam", "interview"],
            "subjects": [{"bg": "Математика", "en": "Maths"}],
        }
    ]
    assert glance["highest_grade"] == 12
    assert glance["applications"] == {"open": "2026-11-01", "close": "2026-12-15"}
    assert glance["places"] == [{"grade": 5, "grade_to": None, "status": "waiting_list"}]
    assert at_a_glance(facts, "2026-01-10", today=today)["places"] == []  # read too long ago
    open_only = [{"applications_open": "2026-03-01"}]
    assert at_a_glance(open_only, today=today)["applications"] is None
    assert at_a_glance(open_only, today=datetime.date(2026, 4, 1))["applications"] == {
        "open": "2026-03-01",
        "close": None,
    }


@pytest.mark.asyncio
async def test_minor_facts_labels_stay_out_of_the_glance(client, db_session):
    rows = [
        _fact_row(topic="curriculum", highest_grade=7),
        _fact_row(topic="transport", bus_areas=[{"bg": "Бояна", "en": "Boyana"}], rank="minor"),
    ]
    body = await _detail(
        client, db_session, school_facts={"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": rows}
    )
    glance = body["school_facts"]["at_a_glance"]
    assert glance["highest_grade"] == 7 and glance["bus_areas"] == []
    assert "class_size" not in glance


@pytest.mark.asyncio
async def test_the_detail_response_has_the_labels_at_a_glance(client, db_session):
    rows = [
        _fact_row(topic="admissions", admission=["entrance_exam"], admission_grade=5, places="bogus"),
        _fact_row(topic="curriculum", highest_grade=4, curricula=["cambridge", "made_up"], rank="more"),
    ]
    body = await _detail(
        client, db_session, school_facts={"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": rows}
    )
    glance = body["school_facts"]["at_a_glance"]
    assert glance["admission"] == [{"grade": 5, "grade_to": None, "types": ["entrance_exam"], "subjects": []}]
    assert glance["places"] == [] and glance["highest_grade"] == 4 and glance["curricula"] == ["cambridge"]
    assert "admission" not in body["school_facts"]["facts"][0]  # labels show only summed up


@pytest.mark.asyncio
async def test_admission_labels_reach_the_judge_and_the_store():
    fact = Fact.model_validate(
        _fact("admissions", _line(3), en="Grade 5 entry is by exam in maths and English.").model_dump()
        | {"admission": ["entrance_exam"], "admission_grade": 5, "exam_subjects": [{"bg": "Математика", "en": "Maths"}]}
    )
    calls = _Calls([fact], passed=[1])
    stored = (await stage.read_school(_school(), LINES, calls))["store"][0]
    assert stored["admission"] == ["entrance_exam"] and stored["exam_subjects"] == [{"bg": "Математика", "en": "Maths"}]
    judged = calls.messages[1][1]
    assert (
        "Admission: entrance_exam" in judged
        and "Admission grade: 5" in judged
        and "Exam subjects: Математика / Maths" in judged
    )


def test_a_malformed_stored_list_is_dropped_not_raised():
    from app.utils.school_facts import _labels

    labels = _labels({"tags": [["cafeteria"], {"a": 1}, "cafeteria"], "admission": [["x"]], "curricula": [{}]})
    assert labels["tags"] == ["cafeteria"] and labels["admission"] == [] and labels["curricula"] == []


def test_admission_facts_without_a_grade_are_not_merged_and_ranges_are_kept():
    from app.utils.school_facts import _labels, at_a_glance

    facts = [
        _labels({"admission": ["entrance_exam"], "admission_grade": 2, "admission_grade_to": 4}),
        _labels({"admission": ["entrance_exam"], "exam_subjects": [{"bg": "Математика", "en": "Maths"}]}),
        _labels({"admission": ["interview"]}),
        _labels({"admission": ["interview"], "admission_grade": 5, "admission_grade_to": 3}),  # bad range
    ]
    rows = at_a_glance(facts)["admission"]
    assert [(r["grade"], r["grade_to"], r["types"]) for r in rows] == [
        (2, 4, ["entrance_exam"]),
        (None, None, ["entrance_exam"]),
        (None, None, ["interview"]),
        (5, None, ["interview"]),
    ]


def test_a_sign_up_link_must_be_a_link_on_a_page_read():
    from app.scrapers.extraction.school_facts import link_on_page

    pages = [(ABOUT, "[Запиши се](/open-day/) и [форма](https://forms.example/x)")]
    assert link_on_page("https://school.example/open-day", pages)  # relative link, trailing slash
    assert link_on_page("https://forms.example/x", pages)
    assert not link_on_page("https://forms.example/made-up", pages)
    assert not link_on_page("javascript:alert(1)", pages)
    tricky = [(ABOUT, "[a](https://f.example/r?token=abc/) [b](https://f.example/reg(foo))")]
    assert not link_on_page("https://f.example/r?token=abc", tricky)  # query must match exactly
    assert link_on_page("https://f.example/r?token=abc/", tricky)
    assert not link_on_page("https://f.example/reg(foo", tricky)
    assert link_on_page("https://f.example/reg(foo)", tricky)


@pytest.mark.asyncio
async def test_open_days_are_stored_and_an_invented_sign_up_link_is_dropped():
    pages = [PageRead(HOME, _line(0) + "\n[Запиши се](https://forms.example/x)", False)]
    kept = _fact("admissions", _line(0), en="Open day on 12 November.").model_dump()
    invented = _fact("admissions", _line(0), en="Open day on 3 December.").model_dump()
    facts = [
        Fact.model_validate(kept | {"open_days": ["2026-11-12"], "register_url": "https://forms.example/x"}),
        Fact.model_validate(
            invented | {"topic": "school_day", "open_days": ["2026-12-03"], "register_url": "https://x.example/"}
        ),
    ]
    calls = _Calls(facts, passed=[1, 2])
    stored = (await stage.read_school(_school(), pages, calls))["store"]
    assert [(f["open_days"], f["register_url"]) for f in stored] == [
        (["2026-11-12"], "https://forms.example/x"),
        (["2026-12-03"], None),
    ]
    assert "forms.example" not in calls.messages[1][1]  # the judge is not asked about links


def test_at_a_glance_lists_upcoming_open_days_only():
    import datetime

    from app.utils.school_facts import _labels, at_a_glance

    facts = [
        _labels({"open_days": ["2026-12-03", "2026-10-01", "bad"], "register_url": "https://f.example/x"})
        | {"source_url": HOME},
        _labels({"open_days": ["2026-11-12"], "register_url": "ftp://nope"}) | {"source_url": ABOUT},
    ]
    days = at_a_glance(facts, today=datetime.date(2026, 10, 14))["open_days"]
    assert days == [
        {"date": "2026-11-12", "register_url": None, "source_url": ABOUT},
        {"date": "2026-12-03", "register_url": "https://f.example/x", "source_url": HOME},
    ]


@pytest.mark.parametrize(
    "overrides,shown",
    [
        ({}, 18),
        ({"quote": "Лятна академия: групи до 18 деца"}, None),  # 541: a summer school's group size
        ({"text_i18n": {"bg": "Лагер до 18 деца.", "en": "Camp groups of up to 18."}}, None),
        ({"source_url": "https://www.school.example/summer-school"}, None),
        ({"source_url": "https://www.school.example/campus", "quote": "Up to 18 pupils on our campus"}, 18),
    ],
)
def test_a_summer_programmes_class_size_is_not_the_schools(overrides, shown):
    block = {
        "_schema_version": 1,
        "read_at": "2026-10-13T20:00:00+00:00",
        "website_url": "https://www.school.example",
        "facts": [_fact_row(topic="class_size", max_class_size=18, source_url="https://www.school.example/about") | overrides],
    }
    out = publishable_school_facts({"school_facts": block}, "https://www.school.example")
    assert out["facts"][0]["max_class_size"] == shown


@pytest.mark.parametrize(
    "english,shown",
    [
        ("Classes have up to 18 pupils.", 18),
        ("For the 2026/2027 intake, the school has approved one first-grade class with 18 pupils.", None),  # 278
        ("The planned class capacity is 24 pupils in grade 1 and 18 in grade 5.", None),  # 333
        ("The computer-science courses work in groups of 5 to 18 participants.", None),  # 517
        ("The foreign-languages profile has one class of 18 pupils.", None),  # 619
        ("Classes in all grades have at most 18 pupils.", 18),
        ("There are 23 children in each preparatory group and 18 pupils in each grade 1 class.", None),  # 204
        ("Each grade is limited to 18 pupils.", 18),
        ("Classes in all grades include at most 18 pupils.", 18),
        ("Classes from grade 1 to 12 have at most 18 pupils.", 18),
        ("Of course, classes have at most 18 pupils.", 18),
    ],
)
def test_one_grades_class_size_is_not_the_schools(english, shown):
    block = {
        "_schema_version": 1,
        "read_at": "2026-10-13T20:00:00+00:00",
        "website_url": "https://www.school.example",
        "facts": [_fact_row(topic="class_size", max_class_size=18, text_i18n={"bg": "До 18.", "en": english})],
    }
    out = publishable_school_facts({"school_facts": block}, "https://www.school.example")
    assert out["facts"][0]["max_class_size"] == shown
    assert out["facts"][0]["text_i18n"]["en"] == english  # the fact itself still shows, scope and all


@pytest.mark.parametrize(
    "english,hidden",
    [
        ("A Grade 12 student says there are 20 students per class.", True),  # 506
        ("According to a Grade 12 student, classes have 20 pupils.", True),
        ("Pupils write an entrance test in maths and Bulgarian.", False),
        ("Parents can share feedback with the head teacher.", False),
    ],
)
def test_which_remarks_are_hearsay(english, hidden):
    from app.utils.school_facts import _HEARSAY_RE

    assert bool(_HEARSAY_RE.search(english)) is hidden


def test_a_pupils_remark_is_not_a_school_fact():
    remark = _fact_row(
        topic="class_size",
        max_class_size=20,
        text_i18n={"bg": "Ученик казва...", "en": "A Grade 12 student says there are 20 students per class."},
    )  # 506
    block = {
        "_schema_version": 1,
        "read_at": "2026-10-13T20:00:00+00:00",
        "website_url": "https://www.school.example",
        "facts": [remark, _fact_row()],
    }
    out = publishable_school_facts({"school_facts": block}, "https://www.school.example")
    assert [f["topic"] for f in out["facts"]] == ["meals"]


def test_summer_programme_facts_are_never_key():
    summer = _fact_row(
        topic="school_day",
        rank="key",
        text_i18n={"bg": "Лятната програма е 08:30–18:00.", "en": "The summer programme runs 08:30–18:00."},
    )  # 541
    block = {
        "_schema_version": 1,
        "read_at": "2026-10-13T20:00:00+00:00",
        "website_url": "https://www.school.example",
        "facts": [summer, _fact_row(rank="key")],
    }
    out = publishable_school_facts({"school_facts": block}, "https://www.school.example")
    assert [(f["topic"], f["rank"]) for f in out["facts"]] == [("school_day", "more"), ("meals", "key")]


def test_a_school_year_fact_that_mentions_summer_stays_key():
    lunch = _fact_row(rank="key", quote="Обядът се сервира всеки ден, включително през лятото.")
    block = {
        "_schema_version": 1,
        "read_at": "2026-10-13T20:00:00+00:00",
        "website_url": "https://www.school.example",
        "facts": [lunch],
    }
    out = publishable_school_facts({"school_facts": block}, "https://www.school.example")
    assert out["facts"][0]["rank"] == "key"
