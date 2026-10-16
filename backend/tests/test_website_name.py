"""Website-name stage: reading the Latin name a school's website uses, the checks it must
pass, the publish boundary, the English name and search. Page fetches and the model are stubbed."""

from __future__ import annotations

import pytest

from app.models import School, SchoolLocation
from app.scrapers.stages import website_name as stage
from app.utils.i18n_resolver import resolve_name_i18n
from app.utils.school_facts import publication_attributes
from app.utils.website_name import address_name, is_latin_name, matches_site_or_register, shown_website_name

HOME = "https://uwekind.com/"
REGISTER = '"ЧАСТНО СРЕДНО УЧИЛИЩЕ УВЕКИНД" ООД'


def _html(title="Uwekind International School", body="Welcome to UWEKIND"):
    return f"<html><head><title>{title}</title></head><body><h1>{body}</h1><p>Lozen park 1</p></body></html>"


def _fetcher(pages):
    async def fetch(url):
        return (url, pages[url]) if url in pages else None

    return fetch


def _caller(answer):
    async def call(message, school_id):
        return stage.SiteName(latin_name=answer)

    return call


def _school(**kwargs):
    defaults = dict(
        name_i18n={"bg": REGISTER},
        country_code="bg",
        school_type="private",
        education_level="upper_secondary",
        city="sofia",
        website_url=HOME,
        scrape_status="validated",
    )
    return School(**(defaults | kwargs))


def test_latin_names():
    assert is_latin_name("UWEKIND") and is_latin_name("Lycée Français de Sofia") and is_latin_name("Under1roof")
    assert not is_latin_name("Увекинд") and not is_latin_name("UWE Увекинд") and not is_latin_name("1")
    assert not is_latin_name("x" * 81) and not is_latin_name(None)


def test_a_name_must_share_a_distinctive_word_with_the_address_or_the_register():
    assert matches_site_or_register("Busy School", "https://busy-school.com/", ["ЧНУ БИЗИ"])
    assert matches_site_or_register("ABC KinderCare", "https://abckinder.org/", ["ЧДГ АВСЛАНДИЯ"])
    # The site writes the register name its own way (Drujba for Дружба).
    assert matches_site_or_register("Drujba Schools", "https://example.org/", ["Училища Дружба"])
    # A template's name, or only generic words, is not the school's.
    assert not matches_site_or_register("Kidscool Site", "https://usmivkabg.com/", ["ЧДГ УСМИВКА"])
    assert not matches_site_or_register("International School Sofia", "https://school-sofia.bg/", ["ЧСУ ПИТАГОР"])


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Uwekind International School", None),
        ("UWEKIND", None),
        (None, "none on the page"),
        ("Увекинд", "not a Latin name"),
        ("Uwekind Academy of Arts", "not on the page"),
        ("Kidscool", "not on the page"),
    ],
)
def test_check_name(answer, expected):
    _, text = stage.page_summary(_html())
    assert stage.check_name(answer, text, HOME, [REGISTER]) == expected


def test_page_summary_has_title_site_name_logo_and_headings():
    html = (
        '<html><head><title>Начало - Busy School</title><meta property="og:site_name" content="Busy School">'
        '</head><body><img class="site-logo" alt="BUSY School logo" src="/l.png"><script>var x="Spam"</script>'
        "<h1>ЧАСТНО НАЧАЛНО УЧИЛИЩЕ БИЗИ</h1></body></html>"
    )
    summary, text = stage.page_summary(html)
    assert "Title: Начало - Busy School" in summary and "og:site_name: Busy School" in summary
    assert "Logo text: BUSY School logo" in summary and "Headings: ЧАСТНО НАЧАЛНО УЧИЛИЩЕ БИЗИ" in summary
    assert "Spam" not in text


@pytest.mark.asyncio
async def test_apply_stores_the_name_dry_run_writes_nothing_and_a_page_not_read_keeps_it(db_session):
    school = _school(attributes={"other": 1})
    db_session.add(school)
    await db_session.commit()
    fetch = _fetcher({HOME: _html()})

    report = await stage.run_website_name(db_session, school_ids=[school.id], fetch=fetch, call=_caller("UWEKIND"))
    assert report["outcomes"] == {"found": 1} and "website_name" not in school.attributes

    await stage.run_website_name(db_session, school_ids=[school.id], fetch=fetch, call=_caller("UWEKIND"), apply=True)
    stored = school.attributes["website_name"]
    assert stored["name"] == "UWEKIND" and stored["website_url"] == HOME and school.attributes["other"] == 1

    report = await stage.run_website_name(
        db_session, school_ids=[school.id], fetch=_fetcher({}), call=_caller("X"), apply=True
    )
    assert report["outcomes"] == {"not_read": 1} and school.attributes["website_name"]["name"] == "UWEKIND"

    # A page read with no name kept clears it.
    report = await stage.run_website_name(
        db_session, school_ids=[school.id], fetch=fetch, call=_caller("My Blog"), apply=True
    )
    assert report["outcomes"] == {"dropped": 1} and school.attributes["website_name"]["name"] is None


@pytest.mark.asyncio
async def test_default_run_reads_only_private_and_international_schools(db_session):
    private, state = _school(), _school(school_type="state", website_url="https://state.example/")
    db_session.add_all([private, state])
    await db_session.commit()
    pages = {HOME: _html(), "https://state.example/": _html()}
    report = await stage.run_website_name(db_session, fetch=_fetcher(pages), call=_caller("UWEKIND"))
    assert [r["school_id"] for r in report["results"]] == [private.id]


def _block(**overrides):
    return {"_schema_version": 1, "read_at": "t", "website_url": HOME, "name": "UWEKIND"} | overrides


def test_the_name_shows_only_for_an_accepted_current_website_without_a_withholding_marker():
    attrs = {"website_name": _block()}
    assert shown_website_name(attrs, "validated", HOME) == "UWEKIND"
    assert shown_website_name(attrs, "pending", HOME) is None
    assert shown_website_name(attrs, "validated", "https://moved.example/") is None
    assert shown_website_name(attrs | {"website_data_withheld": True}, "validated", HOME) is None
    assert shown_website_name({"website_name": _block(name=None)}, "validated", HOME) is None
    assert shown_website_name({"website_name": _block(name="Увекинд")}, "validated", HOME) is None


def test_the_english_name_is_the_websites_name_unless_a_corroborated_display_name_exists():
    attrs = {"website_name": _block()}
    resolved = resolve_name_i18n({"bg": REGISTER}, publication_attributes(attrs, "validated", HOME))
    assert resolved["en"] == "UWEKIND" and resolved["bg"] != "UWEKIND"
    # Not shown (website moved): the derived name stays.
    assert (
        resolve_name_i18n({"bg": REGISTER}, publication_attributes(attrs, "validated", "https://x.example/"))["en"]
        != "UWEKIND"
    )
    display = {
        "display_name_i18n": {"en": "Uwekind School"},
        "display_name_evidence": {"signals": ["website_domain_alias_match", "repeated_on_page_identity"]},
        "data_validation": {"_schema_version": 1},
    }
    shown = publication_attributes(attrs | display, "extracted", HOME)
    assert resolve_name_i18n({"bg": REGISTER}, shown)["en"] == "Uwekind School"
    # The raw block never rides along unprojected.
    assert "website_name" not in shown


@pytest.mark.asyncio
async def test_search_and_list_find_the_school_by_its_website_name(seeded_db, seeded_client):
    school = _school(attributes={"website_name": _block(name="Busy School")})
    seeded_db.add(school)
    await seeded_db.flush()
    seeded_db.add(
        SchoolLocation(school_id=school.id, address_i18n={"bg": "ул. Тест 1"}, lat=42.7, lng=23.3, is_primary=True)
    )
    await seeded_db.commit()
    rows = (await seeded_client.get("/schools/search", params={"q": "busy school"})).json()
    assert [row["id"] for row in rows] == [school.id]
    assert rows[0]["resolved_name_i18n"]["en"] == "Busy School"


def test_address_name():
    assert address_name("https://busy-school.com/", "validated") == "busy school"
    assert address_name("https://www.sofia-school.maplebear.bg/x", "navigated") == "sofia school maplebear"
    assert address_name("https://busy-school.com/", "pending") is None
    assert address_name(None, "validated") is None


@pytest.mark.asyncio
async def test_search_finds_a_brand_in_the_web_address_below_name_matches(seeded_db, seeded_client):
    brand = _school(name_i18n={"bg": "ЧДГ КУЕСТ"}, website_url="https://questjunior.bg/")
    named = _school(name_i18n={"bg": "Quest Academy"}, website_url="https://other.example/")
    pending = _school(name_i18n={"bg": "ЧДГ Друга"}, website_url="https://questother.bg/", scrape_status="pending")
    seeded_db.add_all([brand, named, pending])
    await seeded_db.flush()
    for school in (brand, named, pending):
        seeded_db.add(
            SchoolLocation(school_id=school.id, address_i18n={"bg": "ул. Тест 1"}, lat=42.7, lng=23.3, is_primary=True)
        )
    await seeded_db.commit()
    rows = (await seeded_client.get("/schools/search", params={"q": "quest"})).json()
    assert [row["id"] for row in rows] == [named.id, brand.id]
