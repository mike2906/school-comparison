"""Social-links stage: which links are a school's own page, storing them, and the publish boundary.

Page fetches are stubbed."""

from __future__ import annotations

import pytest

from app.models import School
from app.scrapers.stages import social_links as stage
from app.utils.social_links import pick_links, profile_url, shown_social_links

HOME = "https://school.example/"
FB = "https://www.facebook.com/school.example"
IG = "https://www.instagram.com/school_example/"


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("https://www.facebook.com/School.Example/", ("facebook", "https://www.facebook.com/school.example")),
        ("https://m.facebook.com/school.example?ref=footer", ("facebook", "https://www.facebook.com/school.example")),
        ("https://www.facebook.com/school.example/posts/123", ("facebook", "https://www.facebook.com/school.example")),
        ("https://www.facebook.com/profile.php?id=100064", ("facebook", "https://www.facebook.com/profile.php?id=100064")),
        ("https://www.facebook.com/pages/School/12345", ("facebook", "https://www.facebook.com/pages/School/12345")),
        (
            "https://www.facebook.com/plugins/page.php?href=https%3A%2F%2Fwww.facebook.com%2Fschool.example&tabs=timeline",
            ("facebook", "https://www.facebook.com/school.example"),
        ),
        ("https://www.facebook.com/pg/school.example/about/", ("facebook", "https://www.facebook.com/school.example")),
        ("https://www.facebook.com/pg", None),
        ("https://www.facebook.com//pages/School/123", ("facebook", "https://www.facebook.com/pages/School/123")),
        ("//facebook.com/school.example", ("facebook", "https://www.facebook.com/school.example")),
        ("https://instagram.com/School_Example", ("instagram", "https://www.instagram.com/school_example/")),
        # Not the school's page: sharing, a parents' group, an event, a post, Facebook itself.
        ("https://www.facebook.com/sharer/sharer.php?u=https://school.example", None),
        ("https://www.facebook.com/groups/parents.of.school", None),
        ("https://www.facebook.com/events/123", None),
        ("https://www.facebook.com/", None),
        ("https://www.facebook.com/profile.php", None),
        ("https://www.instagram.com/p/Cx123/", None),
        ("https://www.instagram.com/explore/tags/school/", None),
        ("https://twitter.com/school", None),
        ("mailto:a@school.example", None),
    ],
)
def test_profile_url(href, expected):
    assert profile_url(href) == expected


def test_the_page_linked_most_wins_and_a_tie_names_none():
    links, ambiguous = pick_links([FB, FB + "/", "https://www.facebook.com/designer.studio", IG])
    assert links == {"facebook": FB, "instagram": IG} and ambiguous == []
    links, ambiguous = pick_links([FB, "https://www.facebook.com/other.school"])
    assert links == {} and ambiguous == ["facebook"]


def _html(*hrefs):
    menu = [f"/page{n}" for n in range(stage.MIN_HOME_LINKS)]
    return "<html><body>" + "".join(f'<a href="{h}">x</a>' for h in [*menu, *hrefs]) + "</body></html>"


def _fetcher(pages):
    async def fetch(url):
        return (url, pages[url]) if url in pages else None

    return fetch


def _school(**kwargs):
    defaults = dict(
        name_i18n={"bg": "Училище", "en": "School"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url=HOME,
        scrape_status="validated",
    )
    return School(**(defaults | kwargs))


@pytest.mark.asyncio
async def test_the_contact_page_is_read_when_the_home_page_links_neither():
    pages = {HOME: _html("/kontakti"), HOME + "kontakti": _html(FB)}
    read = await stage.read_school(_school(), _fetcher(pages))
    assert read["links"] == {"facebook": FB} and read["pages"] == [HOME, HOME + "kontakti"]


@pytest.mark.asyncio
async def test_the_contact_page_must_be_on_the_schools_own_site():
    home = "https://sites.google.com/view/a"
    pages = {home: _html("https://sites.google.com/view/b/contact"), "https://sites.google.com/view/b/contact": _html(FB)}
    read = await stage.read_school(_school(website_url=home), _fetcher(pages))
    assert read["links"] == {} and read["pages"] == [home]


@pytest.mark.asyncio
async def test_a_page_widget_iframe_counts():
    widget = "https://www.facebook.com/plugins/page.php?href=https%3A%2F%2Fwww.facebook.com%2Fschool.example"
    read = await stage.read_school(_school(), _fetcher({HOME: _html() + f'<iframe src="{widget}"></iframe>'}))
    assert read["links"] == {"facebook": FB}


@pytest.mark.asyncio
async def test_apply_stores_links_dry_run_writes_nothing_and_a_site_not_read_keeps_them(db_session):
    school = _school(attributes={"other": 1})
    db_session.add(school)
    await db_session.commit()
    fetch = _fetcher({HOME: _html(FB, IG)})

    report = await stage.run_social_links(db_session, school_ids=[school.id], fetch=fetch)
    assert report["found"] == {"facebook": 1, "instagram": 1} and "social_links" not in school.attributes

    await stage.run_social_links(db_session, school_ids=[school.id], fetch=fetch, apply=True)
    stored = school.attributes["social_links"]
    assert stored["facebook"] == FB and stored["instagram"] == IG and stored["website_url"] == HOME
    assert school.attributes["other"] == 1

    report = await stage.run_social_links(db_session, school_ids=[school.id], fetch=_fetcher({}), apply=True)
    assert report["outcomes"] == {"not_read": 1} and school.attributes["social_links"]["facebook"] == FB


@pytest.mark.asyncio
async def test_a_page_linked_from_two_schools_sites_is_dropped(db_session):
    designer = "https://www.facebook.com/web.studio"
    other = _school(
        website_url="https://other.example/",
        attributes={"social_links": {"_schema_version": 1, "website_url": "https://other.example/", "facebook": designer}},
    )
    sibling = _school(website_url=HOME + "kindergarten")  # same website: may share a page
    school = _school()
    db_session.add_all([other, sibling, school])
    await db_session.commit()
    pages = {HOME: _html(designer, IG), HOME + "kindergarten": _html(IG)}
    report = await stage.run_social_links(db_session, school_ids=[school.id, sibling.id], fetch=_fetcher(pages))
    by_id = {r["school_id"]: r for r in report["results"]}
    assert by_id[school.id]["links"] == {"instagram": IG} and by_id[school.id]["shared"] == ["facebook"]
    assert by_id[sibling.id]["links"] == {"instagram": IG}


@pytest.mark.asyncio
async def test_apply_clears_a_shared_page_stored_for_a_school_outside_the_run(db_session):
    designer = "https://www.facebook.com/web.studio"
    stored = {"_schema_version": 1, "website_url": "https://other.example/", "facebook": designer, "instagram": IG}
    other = _school(website_url="https://other.example/", attributes={"social_links": stored})
    school = _school()
    db_session.add_all([other, school])
    await db_session.commit()
    await stage.run_social_links(db_session, school_ids=[school.id], fetch=_fetcher({HOME: _html(designer)}), apply=True)
    assert "facebook" not in other.attributes["social_links"] and other.attributes["social_links"]["instagram"] == IG
    assert "facebook" not in school.attributes["social_links"]


def _block(**overrides):
    return {"_schema_version": 1, "read_at": "t", "website_url": HOME, "facebook": FB} | overrides


def test_links_show_only_for_an_accepted_current_website_without_a_withholding_marker():
    attrs = {"social_links": _block()}
    assert shown_social_links(attrs, "validated", HOME) == {"facebook": FB}
    assert shown_social_links(attrs, "pending", HOME) is None
    assert shown_social_links(attrs, "validated", "https://moved.example/") is None
    assert shown_social_links(attrs | {"website_data_withheld": True}, "validated", HOME) is None
    assert shown_social_links({"social_links": _block(facebook="https://www.facebook.com/groups/x")}, "validated", HOME) is None


@pytest.mark.asyncio
async def test_detail_response_carries_the_links(client, db_session):
    school = _school(attributes={"social_links": _block(instagram=IG)})
    db_session.add(school)
    await db_session.commit()
    body = (await client.get(f"/schools/{school.id}")).json()
    assert body["social_links"] == {"facebook": FB, "instagram": IG}
    assert "social_links" not in (body.get("attributes") or {})


@pytest.mark.asyncio
async def test_a_home_page_that_is_a_file_still_finds_its_contact_page():
    home = "https://school.example/index.php"
    pages = {home: _html("/kontakti"), "https://school.example/kontakti": _html(FB)}
    read = await stage.read_school(_school(website_url=home), _fetcher(pages))
    assert read["links"] == {"facebook": FB}


@pytest.mark.asyncio
async def test_a_thin_home_page_counts_as_not_read_and_keeps_the_stored_links(db_session):
    school = _school(attributes={"social_links": _block()})
    db_session.add(school)
    await db_session.commit()
    thin = _fetcher({HOME: '<a href="/x">x</a>'})
    report = await stage.run_social_links(db_session, school_ids=[school.id], fetch=thin, apply=True)
    assert report["outcomes"] == {"not_read": 1} and school.attributes["social_links"]["facebook"] == FB


@pytest.mark.asyncio
async def test_a_tie_keeps_the_stored_page(db_session):
    school = _school(attributes={"social_links": _block()})
    db_session.add(school)
    await db_session.commit()
    tie = _fetcher({HOME: _html(FB, "https://www.facebook.com/web.studio", IG)})
    await stage.run_social_links(db_session, school_ids=[school.id], fetch=tie, apply=True)
    assert school.attributes["social_links"]["facebook"] == FB and school.attributes["social_links"]["instagram"] == IG


@pytest.mark.asyncio
async def test_a_school_not_read_still_counts_when_telling_a_shared_page(db_session):
    designer = "https://www.facebook.com/web.studio"
    down = _school(
        website_url="https://down.example/",
        attributes={"social_links": _block(website_url="https://down.example/", facebook=designer)},
    )
    school = _school()
    db_session.add_all([down, school])
    await db_session.commit()
    await stage.run_social_links(
        db_session, school_ids=[down.id, school.id], fetch=_fetcher({HOME: _html(designer)}), apply=True
    )
    assert "facebook" not in school.attributes["social_links"]
    assert "facebook" not in down.attributes["social_links"]


@pytest.mark.asyncio
async def test_a_broken_href_is_skipped_and_a_parents_board_link_is_not_the_school():
    board = "https://www.facebook.com/profile.php?id=61577575697354"
    html = _html(FB) + '<a href="https://[instagram-feed%20feed=3]">x</a>'
    html += f'<a href="{board}">Училищно настоятелство</a><a href="{board}" title="Настоятелство">f</a>'
    read = await stage.read_school(_school(), _fetcher({HOME: html}))
    assert read["links"] == {"facebook": FB}
