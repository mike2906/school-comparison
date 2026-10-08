"""Fee-link follow-up: which links are followed and what is stored."""

from __future__ import annotations

import httpx
import pytest

from app.models import School
from app.scrapers import fee_pages
from app.scrapers import navigator as navigator_module
from app.scrapers.fee_pages import fee_link_candidates, fetch_fee_documents
from app.scrapers.navigator import NavigatedPage, WebsiteNavigator

SITE = "https://school.bg"


def _urls(links, **kwargs):
    return [url for url, _ in fee_link_candidates(links, base_url=SITE, site_url=SITE, **kwargs)]


def test_candidates_are_same_site_links_that_name_fees():
    links = [
        ("/admission/fees/", "Fees"),
        ("/uploads/2026/taksi-2026-2027.pdf", "Такси за учебната 2026/2027"),
        ("/finansovi-usloviya/", "Финансови условия"),
        ("/registration/frais-de-scolarite-2026-2027/", ""),
        ("/priem", "Прием и такси"),
        ("/feedback", "Feedback"),
        ("/center", "Център за кариерно развитие"),
        ("/coffee", "Coffee morning"),
        ("/kontakti", "Контакти"),
        ("/uploads/fees.png", "Fees"),
        ("https://other.bg/fees", "Fees"),
        ("mailto:fees@school.bg", "Fees"),
    ]

    assert _urls(links) == [
        "https://school.bg/uploads/2026/taksi-2026-2027.pdf",
        "https://school.bg/admission/fees/",
        "https://school.bg/finansovi-usloviya/",
        "https://school.bg/priem",
        "https://school.bg/registration/frais-de-scolarite-2026-2027/",
    ]


def test_candidates_list_one_spelling_of_a_page():
    links = [("/taksi", "Такси"), ("https://www.school.bg/taksi/", "Такси"), ("/taksi#top", "")]

    assert _urls(links) == ["https://school.bg/taksi"]


def test_a_school_does_not_follow_the_kindergarten_fee_list():
    """School 300: one page links the kindergarten's and the school's fee PDFs."""
    links = [
        ("/uploads/taksi-dg-2026-2027.pdf", "Информация за учебните такси за 2026/2027 г. /Детска градина/"),
        ("/uploads/taksi-2026-2027-uchilishte.pdf", "Информация за учебните такси за 2026/2027 г. /Училище/"),
    ]  # fmt: skip

    assert _urls(links, school_family="school") == [
        "https://school.bg/uploads/taksi-2026-2027-uchilishte.pdf"
    ]
    assert len(_urls(links)) == 2


def _client(routes: dict[str, httpx.Response], requested: list[str]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return routes.get(str(request.url), httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=body)


def _text(html: str) -> str:
    return html.replace("<p>", "").replace("</p>", "\n")


@pytest.mark.asyncio
async def test_fee_page_and_the_pdf_it_links_are_fetched(monkeypatch):
    monkeypatch.setattr(fee_pages, "pdf_text", lambda data: "I - IV клас 7000 евро")
    requested: list[str] = []
    routes = {
        "https://school.bg/fees": _html(
            '<p>Fees and financial conditions</p><a href="/files/fees-2026.pdf">Fees 2026/2027</a>'
            '<a href="/fees">Fees</a><a href="/about">About</a>'
        ),
        "https://school.bg/files/fees-2026.pdf": httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.7 ..."
        ),
    }

    async with _client(routes, requested) as client:
        documents = await fetch_fee_documents(
            [("https://school.bg/fees", "Fees"), ("https://school.bg/about", "About")],
            site_url=SITE,
            known_urls=["https://school.bg/", "https://school.bg/about"],
            html_to_text=_text,
            client=client,
        )

    assert requested == ["https://school.bg/fees", "https://school.bg/files/fees-2026.pdf"]
    assert [document.url for document in documents] == requested
    # The link text is the PDF's heading: it carries the year the file may not repeat.
    assert documents[1].text == "Fees 2026/2027\nI - IV клас 7000 евро"


@pytest.mark.asyncio
async def test_known_pages_failures_and_other_sites_are_not_stored(monkeypatch):
    requested: list[str] = []
    routes = {
        "https://school.bg/taksi": httpx.Response(500),
        "https://school.bg/ceni": httpx.Response(
            302, headers={"location": "https://payments.example.com/ceni"}
        ),
        "https://payments.example.com/ceni": _html("<p>Такса 500 евро</p>"),
        "https://school.bg/fees.pdf": httpx.Response(
            200, headers={"content-type": "application/pdf"}, content=b"not a pdf"
        ),
    }
    links = [
        ("https://school.bg/taksi", "Такси"),
        ("https://school.bg/ceni", "Цени"),
        ("https://school.bg/fees.pdf", "Fees"),
        ("https://school.bg/fees/", "Fees"),
    ]

    async with _client(routes, requested) as client:
        documents = await fetch_fee_documents(
            links, site_url=SITE, known_urls=["http://www.school.bg/fees"], html_to_text=_text, client=client
        )  # fmt: skip

    assert documents == []
    assert "https://school.bg/fees/" not in requested


@pytest.mark.asyncio
async def test_fetches_are_capped_and_robots_rules_respected():
    requested: list[str] = []
    links = [(f"https://school.bg/fees/{n}", "Fees") for n in range(20)]
    routes = {url: _html("<p>Такса 500 евро</p>") for url, _ in links}

    async def disallowed(url: str) -> bool:
        return url.endswith("/0")

    async with _client(routes, requested) as client:
        documents = await fetch_fee_documents(
            links, site_url=SITE, known_urls=[], html_to_text=_text, client=client, disallowed=disallowed
        )  # fmt: skip

    assert len(requested) == fee_pages.MAX_FEE_FETCHES == len(documents)
    assert "https://school.bg/fees/0" not in requested


def _school() -> School:
    return School(
        id=1, name_i18n={"bg": "Тест"}, country_code="bg", city="sofia",
        school_type="private", education_level="primary", website_url=SITE,
    )  # fmt: skip


def _patch_http(monkeypatch, routes, requested):
    monkeypatch.setattr(navigator_module, "fee_http_client", lambda: _client(routes, requested))

    async def allowed(self, url: str) -> bool:
        return False

    monkeypatch.setattr(WebsiteNavigator, "robots_disallows", allowed)


@pytest.mark.asyncio
async def test_crawl_gains_the_linked_fee_page_and_replaces_a_priceless_fee_tab(monkeypatch):
    """School 404: the rendered fee page had lost its fee tab; the plain HTML states it."""
    requested: list[str] = []
    long_fees = "<main><p>Такси</p><p>За ученици от 1 до 3 клас: 6 750 евро / година</p><p>За ученици от 4 до 7 клас: 7 500 евро / година</p></main>"  # fmt: skip
    routes = {
        "https://school.bg/priem-i-taksi/": _html(long_fees),
        "https://school.bg/transport-fees": _html("<main><p>Училищен транспорт от врата до врата</p><p>Такса за транспорт за учебната година</p><p>90 евро месечно</p></main>"),
    }  # fmt: skip
    _patch_http(monkeypatch, routes, requested)
    home = NavigatedPage(
        url=SITE,
        category="about",
        markdown="Начало",
        content_hash="h1",
        links=[("https://school.bg/priem-i-taksi/", "Прием и такси"), ("https://school.bg/transport-fees", "Transport fees")],
    )  # fmt: skip
    fee_tab = NavigatedPage(
        url="https://school.bg/priem-i-taksi", category="pricing", markdown="Прием и такси. Стъпки.", content_hash="h2"
    )  # fmt: skip

    pages = await navigator_module._follow_fee_links(WebsiteNavigator(), _school(), SITE, [home, fee_tab])

    assert [page.url for page in pages] == [SITE, "https://school.bg/priem-i-taksi", "https://school.bg/transport-fees"]  # fmt: skip
    assert "6 750 евро" in pages[1].markdown and pages[1].category == "pricing"
    assert "90 евро" in pages[2].markdown


@pytest.mark.asyncio
async def test_crawled_page_that_already_states_prices_is_left_alone(monkeypatch):
    requested: list[str] = []
    _patch_http(monkeypatch, {}, requested)
    fees = NavigatedPage(
        url="https://school.bg/taksi", category="pricing", markdown="Такса 7000 евро", content_hash="h",
        links=[("https://school.bg/taksi", "Такси")],
    )  # fmt: skip

    pages = await navigator_module._follow_fee_links(WebsiteNavigator(), _school(), SITE, [fees])

    assert pages == [fees]
    assert requested == []


@pytest.mark.asyncio
async def test_follow_up_failure_keeps_the_crawl_result(monkeypatch):
    def broken_client():
        raise RuntimeError("no network")

    monkeypatch.setattr(navigator_module, "fee_http_client", broken_client)
    home = NavigatedPage(url=SITE, category="about", markdown="Начало", content_hash="h", links=[("https://school.bg/fees", "Fees")])  # fmt: skip

    assert await navigator_module._follow_fee_links(WebsiteNavigator(), _school(), SITE, [home]) == [home]
