"""R6: the fee agent's page finder on the sites step 3 found it missing (docs/accuracy/step3_check.md).

The browser, the HTTP client, robots.txt and the link-picking model are stubbed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import School
from app.scrapers.stages import fees_agent
from app.scrapers.stages.fees_agent import Spend, SpendCapReached

HOME = "https://meridian22.org/"


class _Robots:
    async def can_fetch(self, url, user_agent="*"):
        return True


class _Crawler:
    """Serves each URL's (markdown, links) in turn; None fails the render."""

    def __init__(self, pages):
        self.pages = {url: list(results) for url, results in pages.items()}
        self.calls: list[str] = []

    async def arun(self, url, config):
        self.calls.append(url)
        results = self.pages.get(url) or [None]
        result = results.pop(0) if len(results) > 1 else results[0]
        if result is None:
            return SimpleNamespace(success=False)
        text, links, html = (*result, "")[:3]
        return SimpleNamespace(
            success=True,
            html=html,
            markdown=SimpleNamespace(raw_markdown=text),
            links={"internal": [{"href": h, "text": t} for h, t in links if "meridian22.org" in h],
                   "external": [{"href": h, "text": t} for h, t in links if "meridian22.org" not in h]},
            redirected_url=url,
        )  # fmt: skip


class _Client:
    def __init__(self, api=None):
        self.api = api or {}
        self.gets: list[tuple[str, dict]] = []

    async def get(self, url, params=None, timeout=None, **kw):
        self.gets.append((url, params or {}))
        body = self.api.get((params or {}).get("slug"))
        return SimpleNamespace(status_code=200 if body is not None else 404, json=lambda: body)


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    import crawl4ai.utils

    monkeypatch.setattr(crawl4ai.utils, "RobotsParser", _Robots)
    monkeypatch.setattr(fees_agent, "RETRY_SECONDS", 0)


def _school():
    return School(id=1, name_i18n={}, country_code="bg", school_type="private", website_url=HOME)


def _picks(urls=None, error=None):
    async def pick(school, read, links, spend, *args):
        if error:
            raise error
        return urls or []

    return pick


@pytest.mark.parametrize(
    ("url", "same"),
    [
        ("https://meridian22.bg/taksi", True),  # the umbrella site's sister domain
        ("https://www.meridian22.org/x", True),
        ("https://facebook.com/meridian22", False),
        ("https://abc.org/", False),
    ],
)
def test_same_site_takes_the_sister_domain(url, same):
    assert fees_agent._same_site(url, HOME) is same


def test_a_short_name_on_another_ending_is_not_the_same_site():
    assert fees_agent._same_site("https://abc.bg/", "https://abc.org/") is False


@pytest.mark.parametrize(
    ("url", "home"),
    [
        ("https://www.google.bg/maps", "https://sites.google.com/view/dg-slunce"),
        ("https://sofia.org/", "https://dg42.sofia.bg/"),
    ],
)
def test_a_home_on_a_shared_domain_takes_no_sister_domains(url, home):
    assert fees_agent._same_site(url, home) is False


async def test_a_pdf_is_never_looked_up_in_the_wordpress_api(monkeypatch):
    pdf = "https://meridian22.org/taksi.pdf"
    crawler = _Crawler({HOME: [("Начало " + "x" * 300, [(pdf, "Такси")])]})
    client = _Client()

    async def get(url, params=None, timeout=None, **kw):
        client.gets.append((url, params or {}))
        return SimpleNamespace(status_code=404, url=url, content=b"")

    client.get = get
    monkeypatch.setattr(fees_agent, "_pick", _picks([pdf]))
    await fees_agent.find_pages(crawler, None, client, _school(), Spend(0.05))
    assert client.gets == [(pdf, {})]


async def test_a_run_that_stopped_still_stops_the_school(monkeypatch):
    from app.services.provider_costs import ProviderAttributionUncertain

    crawler = _Crawler({HOME: [("Начало " + "x" * 300, [("https://meridian22.org/taksi/", "Такси")])]})
    monkeypatch.setattr(fees_agent, "_pick", _picks(error=ProviderAttributionUncertain("run is no longer running")))
    with pytest.raises(ProviderAttributionUncertain):
        await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05))


async def test_the_finder_follows_fee_links_onto_the_sister_domain(monkeypatch):
    crawler = _Crawler({
        HOME: [("Добре дошли", [("https://meridian22.bg/taksi", "Такси")])],
        "https://meridian22.bg/taksi": [("Месечна такса: 700 евро " + "x" * 300, [])],
    })  # fmt: skip
    monkeypatch.setattr(fees_agent, "_pick", _picks(["https://meridian22.bg/taksi"]))
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05))
    assert [(p.url, p.prices) for p in read][-1] == ("https://meridian22.bg/taksi", True)


async def test_a_failed_render_is_tried_once_more(monkeypatch):
    crawler = _Crawler({HOME: [None, ("Месечна такса: 700 евро " + "x" * 300, [])]})
    monkeypatch.setattr(fees_agent, "_pick", _picks())
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05))
    assert crawler.calls == [HOME, HOME]
    assert read[0].prices


async def test_a_thin_page_is_read_from_the_wordpress_api(monkeypatch):
    fees = "https://meridian22.org/taksi/"
    crawler = _Crawler({HOME: [("Начало " + "x" * 300, [(fees, "Такси")])], fees: [("Зареждане…", [])]})
    client = _Client(
        {
            "taksi": [
                {"link": "https://meridian22.org/uchilishte/taksi/", "content": {"rendered": "<p>Такса 900 евро</p>"}},
                {
                    "link": fees,
                    "content": {"rendered": "<table><tr><td>Месечна такса</td><td>700 евро</td></tr></table>"},
                },
            ]
        }
    )
    monkeypatch.setattr(fees_agent, "_pick", _picks([fees]))
    read = await fees_agent.find_pages(crawler, None, client, _school(), Spend(0.05))
    page = next(p for p in read if p.url == fees)
    assert "700 евро" in page.text and "900" not in page.text and page.prices  # the page at this URL, not its namesake
    assert client.gets == [("https://meridian22.org/wp-json/wp/v2/pages", {"slug": "taksi", "_fields": "content,link"})]


async def test_a_thin_page_without_wordpress_keeps_its_own_text(monkeypatch):
    crawler = _Crawler({HOME: [("Зареждане…", [])]})
    monkeypatch.setattr(fees_agent, "_pick", _picks())
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05))
    assert read[0].text == "Зареждане…"


async def test_a_failed_pick_falls_back_to_the_fee_links(monkeypatch):
    fees = "https://meridian22.org/taksi/"
    crawler = _Crawler({
        HOME: [("Начало " + "x" * 300, [(fees, "Такси"), ("https://meridian22.org/news/", "Новини")])],
        fees: [("Месечна такса: 700 евро " + "x" * 300, [])],
    })  # fmt: skip
    monkeypatch.setattr(fees_agent, "_pick", _picks(error=TimeoutError("read timed out")))
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05))
    assert [p.url for p in read] == [HOME, fees]


async def test_a_pick_that_came_back_without_a_cost_falls_back_to_the_fee_links(monkeypatch):
    from app.services.provider_costs import ProviderResponseUnattributed

    fees = "https://meridian22.org/taksi/"
    crawler = _Crawler({
        HOME: [("Начало " + "x" * 300, [(fees, "Такси"), ("https://meridian22.org/news/", "Новини")])],
        fees: [("Месечна такса: 700 евро " + "x" * 300, [])],
    })  # fmt: skip
    monkeypatch.setattr(fees_agent, "_pick", _picks(error=ProviderResponseUnattributed("no cost")))
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05))
    assert [p.url for p in read] == [HOME, fees]


async def test_an_unattributed_pick_counts_at_the_request_reserve(monkeypatch):
    from app.services.provider_costs import ProviderResponseUnattributed

    seen = {}

    def _agent(tier, prompt, output, **kwargs):
        seen.update(kwargs["model_settings"])
        return SimpleNamespace(run=None)

    async def _unattributed(*_args, **_kwargs):
        raise ProviderResponseUnattributed("no cost")

    monkeypatch.setattr(fees_agent, "create_agent", _agent)
    monkeypatch.setattr(fees_agent, "execute_billable_request", _unattributed)
    spend = Spend(1.0)
    with pytest.raises(ProviderResponseUnattributed):
        await fees_agent._pick(_school(), [], [("https://meridian22.org/taksi/", "Такси")], spend)
    assert spend.spent_usd == float(fees_agent.get_settings().provider_request_reserve_usd)
    assert seen["parallel_tool_calls"] is False


async def test_a_spend_cap_during_the_pick_still_stops_the_school(monkeypatch):
    crawler = _Crawler({HOME: [("Начало " + "x" * 300, [("https://meridian22.org/taksi/", "Такси")])]})
    monkeypatch.setattr(fees_agent, "_pick", _picks(error=SpendCapReached("cap")))
    with pytest.raises(SpendCapReached):
        await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05))


async def test_a_site_without_the_wordpress_api_is_asked_once(monkeypatch):
    fees, prices = "https://meridian22.org/taksi/", "https://meridian22.org/tseni/"
    crawler = _Crawler(
        {HOME: [("Начало", [(fees, "Такси"), (prices, "Цени")])], fees: [("…", [])], prices: [("…", [])]}
    )
    client = _Client()  # every API call is a 404
    monkeypatch.setattr(fees_agent, "_pick", _picks([fees, prices]))
    await fees_agent.find_pages(crawler, None, client, _school(), Spend(0.05))
    assert len(client.gets) == 1


async def test_a_wordpress_api_that_is_down_for_a_moment_is_asked_again(monkeypatch):
    fees, prices = "https://meridian22.org/taksi/", "https://meridian22.org/tseni/"
    crawler = _Crawler(
        {HOME: [("Начало", [(fees, "Такси"), (prices, "Цени")])], fees: [("…", [])], prices: [("…", [])]}
    )
    client = _Client()

    async def get(url, params=None, timeout=None, **kw):
        client.gets.append((url, params or {}))
        return SimpleNamespace(status_code=503, json=lambda: None)

    client.get = get
    monkeypatch.setattr(fees_agent, "_pick", _picks([fees, prices]))
    await fees_agent.find_pages(crawler, None, client, _school(), Spend(0.05))
    assert len(client.gets) == 3


_FEES_PICTURE = "https://meridian22.org/wp-content/uploads/fees-2026-700x453.png"
_PICTURE_PAGE = '<main><img src="/wp-content/uploads/fees-2026-700x453.png" width="700"></main>'


def _picture_reads(monkeypatch, text="Grade 1 | 7 880 €"):
    reads = []

    async def read_image(client, picture, *, page_url, site_url, school_id, stage, allowed, on_cost):
        reads.append((picture[:2], stage))
        on_cost(0.03)
        return (picture[0], text)

    monkeypatch.setattr(fees_agent.fee_images_module, "read_image", read_image)
    return reads


async def test_a_fee_picture_is_read_as_a_page_of_its_own(monkeypatch):
    reads = _picture_reads(monkeypatch)
    crawler = _Crawler({HOME: [("Такси: депозит " + "x" * 300, [], _PICTURE_PAGE)]})
    spend = Spend(0.5)
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), spend, read_pictures=True)
    original = "https://meridian22.org/wp-content/uploads/fees-2026.png"
    assert reads == [((original, _FEES_PICTURE), fees_agent.STAGE)]
    assert [(p.url, p.text, p.prices) for p in read][:-1] == [(original, "Grade 1 | 7 880 €", True)]
    assert spend.spent_usd == pytest.approx(0.03)


async def test_pictures_are_read_only_when_asked(monkeypatch):
    reads = _picture_reads(monkeypatch)
    crawler = _Crawler({HOME: [("Такси " + "x" * 300, [], _PICTURE_PAGE)]})
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.5))
    assert reads == [] and [p.url for p in read] == [HOME]


async def test_a_fee_picture_past_the_spend_cap_stops_the_school(monkeypatch):
    reads = _picture_reads(monkeypatch)
    crawler = _Crawler({HOME: [("Такси " + "x" * 300, [], _PICTURE_PAGE)]})
    with pytest.raises(SpendCapReached):
        await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.01), read_pictures=True)
    assert reads == []


async def test_places_the_picker_leaves_free_go_to_this_years_fee_links(monkeypatch):
    forms = "https://meridian22.org/admission/forms/"
    old = "https://meridian22.org/fees-2019-2020/"
    this_year = "https://meridian22.org/school-fees-2026-2027/"
    others = [f"https://meridian22.org/fees-{n}/" for n in range(5)]
    links = [(forms, "Forms"), (old, "Fees"), *[(u, "Fees") for u in others], (this_year, "School fees")]
    crawler = _Crawler({HOME: [("Начало " + "x" * 300, links)]})
    monkeypatch.setattr(fees_agent, "_pick", _picks([forms]))
    monkeypatch.setattr(fees_agent, "current_academic_year", lambda: "2026/2027")
    monkeypatch.setattr(fees_agent, "MAX_HOPS", 1)
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05))
    assert [p.url for p in read][1:3] == [forms, this_year]
    assert len(read) == 1 + fees_agent.MAX_PICKS


async def test_a_fee_picture_with_bare_numbers_goes_to_the_reader(monkeypatch):
    _picture_reads(monkeypatch, text="Fee picture on https://meridian22.org/taksi-2026/\nGrade 1 | 15 350 | 14 750")
    crawler = _Crawler({HOME: [("Такси " + "x" * 300, [], _PICTURE_PAGE)]})
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.5), read_pictures=True)
    assert read[0].prices is True


async def test_a_picture_whose_only_numbers_are_in_its_heading_shows_no_price(monkeypatch):
    _picture_reads(monkeypatch, text="Fee picture on https://meridian22.org/taksi-2026/\nУчебна програма")
    crawler = _Crawler({HOME: [("Такси " + "x" * 300, [], _PICTURE_PAGE)]})
    read = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.5), read_pictures=True)
    assert read[0].prices is False


def test_this_years_pages_take_a_year_suffix():
    pattern = fees_agent._this_years_pages()
    start = int(fees_agent.current_academic_year()[:4])
    assert pattern.search(f"такси {start}/{start + 1}г.") and pattern.search(f"taksi_{start}_{start + 1}_bg")
    assert not pattern.search(f"{start}/{start + 1}0")


async def test_a_shared_cache_serves_the_second_finder_without_fetching_again(monkeypatch):
    fees = "https://meridian22.org/taksi"
    about = "https://meridian22.org/za-nas"
    crawler = _Crawler(
        {
            HOME: [("Начало " + "x" * 300, [(fees, "Такси"), (about, "За нас")])],
            fees: [("Такса 500 лв. " + "x" * 300, [])],
            about: [("За училището " + "x" * 300, [])],
        }
    )
    cache = fees_agent.SiteCache()
    monkeypatch.setattr(fees_agent, "_pick", _picks([about, fees]))
    first = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05), cache=cache)
    monkeypatch.setattr(fees_agent, "_pick", _picks([fees]))
    second = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05), cache=cache)
    assert crawler.calls == [HOME, about, fees]  # each page rendered once
    assert [p.url for p in second] == [HOME, fees]
    assert [(p.text, p.prices) for p in second] == [(p.text, p.prices) for p in first if p.url != about]


async def test_a_failed_page_is_not_cached_so_the_second_finder_tries_again(monkeypatch):
    broken = "https://meridian22.org/taksi"
    crawler = _Crawler({HOME: [("Начало " + "x" * 300, [(broken, "Такси")])]})
    tries = []

    async def fetch(crawler_, run_config, client, url):
        if url == broken:
            tries.append(url)
            raise TimeoutError("slow site")
        return await original(crawler_, run_config, client, url)

    original = fees_agent._fetch
    monkeypatch.setattr(fees_agent, "_fetch", fetch)
    monkeypatch.setattr(fees_agent, "_pick", _picks([broken]))
    cache = fees_agent.SiteCache()
    await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05), cache=cache)
    second = await fees_agent.find_pages(crawler, None, _Client(), _school(), Spend(0.05), cache=cache)
    assert crawler.calls == [HOME] and tries == [broken, broken]
    assert second[-1].url == broken and second[-1].note == "slow site"


def test_calls_in_flight_count_against_the_cap():
    spend = Spend(0.10)
    with spend.hold(0.06):
        assert not spend.allows(0.05)  # a second worker's call would pass the cap
        with pytest.raises(SpendCapReached):
            with spend.hold(0.05):
                pass
    assert spend.pending_usd == 0 and spend.allows(0.05)
