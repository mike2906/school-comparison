"""Fee discounts: a sibling or early-payment discount the site states is read beside the fees.

596 (Maple Bear) prints "5% for a second child, 10% for a third"; the reader used to skip a
line with no amount. Code keeps a discount only when its evidence is on the page, holds its
one number, and both sentences state that number.
"""

from __future__ import annotations

import pytest

from app.models import Pricing, School
from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.scrapers.extraction.agent_fees import AgentDiscount, AgentFee, check_discount
from app.scrapers.stages import fees_agent
from app.scrapers.stages.fees_agent import FeeRead, PageRead
from app.utils.fee_discounts import publishable_fee_discounts

URL = "https://school.example/fees"
HOME = "https://school.example"
FEE_LINE = "Monthly tuition: 700 EUR"
SIBLING_EN = "5% discount for the second child, 10% for the third"
SIBLING_BG = "Отстъпка 5% за второ дете"
PAGE = f"Fees 2026/2027\n{FEE_LINE}\n{SIBLING_EN}\n{SIBLING_BG}\n"


def discount(**update) -> AgentDiscount:
    base = AgentDiscount(
        kind="sibling",
        percent=5,
        child=2,
        text_i18n={"en": "5% off tuition for a second child", "bg": "5% отстъпка от таксата за второ дете"},
        source_url=URL,
        evidence=SIBLING_EN,
    )
    return base.model_copy(update=update)


def test_a_sibling_discount_the_page_states_is_kept():
    kept = check_discount(discount(evidence=SIBLING_BG), PAGE)
    assert kept is not None and kept.percent == 5 and kept.child == 2


def test_two_discounts_from_one_line_are_both_kept():
    third = discount(
        percent=10,
        child=3,
        text_i18n={"en": "10% off for a third child", "bg": "10% отстъпка за трето дете"},
    )
    assert check_discount(discount(), PAGE) is not None
    assert check_discount(third, PAGE) is not None


def test_an_amount_discount_keeps_its_currency_and_a_percent_one_has_none():
    amount = discount(
        kind="early_payment",
        child=None,
        percent=None,
        amount=100,
        currency="EUR",
        evidence="Early payment: 100 EUR off",
        text_i18n={"en": "100 EUR off for early payment", "bg": "100 EUR отстъпка при ранно плащане"},
    )
    page = PAGE + "Early payment: 100 EUR off\n"
    assert check_discount(amount, page).currency == "EUR"
    assert check_discount(discount(currency="EUR"), PAGE).currency is None


@pytest.mark.parametrize(
    "update, page",
    [
        ({"percent": 7}, PAGE),  # the number is not in the evidence
        ({"evidence": "15% discount for the second child"}, PAGE),  # the line is not on the page
        ({"text_i18n": {"en": "Discount for a second child", "bg": "5% отстъпка"}}, PAGE),  # no number in the text
        ({"text_i18n": {"en": "5% off for a second child", "bg": ""}}, PAGE),
        ({"amount": 5, "currency": "EUR"}, PAGE),  # both set
        ({"percent": None}, PAGE),  # neither set
        ({"percent": 15, "evidence": "Enrol by 15 May: 10% off", "text_i18n": {"en": "15% off", "bg": "15% отстъпка"}},
         PAGE + "Enrol by 15 May: 10% off\n"),  # 15 is a date, not the percentage
        ({"child": 3}, PAGE.replace(SIBLING_EN, "5% discount for the second child")),  # the page names the second
        ({"evidence": "5% sibling discount", "percent": 5, "child": 2}, PAGE + "5% sibling discount\n"),  # no child named
        ({"evidence": "2% sibling discount", "percent": 2, "text_i18n": {"en": "2% off", "bg": "2% отстъпка"}},
         PAGE + "2% sibling discount\n"),  # the 2 is the percentage, not the second child
    ],
)  # fmt: skip
def test_a_discount_the_page_does_not_back_is_dropped(update, page):
    assert check_discount(discount(**update), page) is None


def test_a_free_place_for_a_third_child_is_kept():
    free = discount(
        percent=100,
        child=3,
        evidence="100% discount for the third child",
        text_i18n={"en": "100% off for a third child", "bg": "100% отстъпка за трето дете"},
    )
    assert check_discount(free, PAGE + "100% discount for the third child\n") is not None


# ---------------------------------------------------------------------------
# The stage
# ---------------------------------------------------------------------------


def _fee() -> AgentFee:
    return AgentFee(
        category=PriceCategory.TUITION,
        amount=700,
        currency="EUR",
        period=PricePeriod.MONTHLY,
        plan_name=None,
        academic_year="2026/2027",
        age_group=None,
        label="Monthly tuition",
        source_url=URL,
        evidence=FEE_LINE,
    )


def _read(*discounts: AgentDiscount):
    async def read(_school, _pages, _missed):
        fees = FeeRead([_fee()])
        fees.discounts = list(discounts)
        return fees

    return read


async def _find(_school):
    return [PageRead(URL, PAGE, True)]


async def _school(db_session, **attributes) -> School:
    school = School(
        name_i18n={"bg": "Частно училище", "en": "Private school"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url=HOME,
        scrape_status="extracted",
        attributes=attributes or None,
    )
    db_session.add(school)
    await db_session.commit()
    return school


async def test_apply_stores_the_kept_discounts(db_session, frozen_today):
    school = await _school(db_session)
    made_up = discount(percent=7)
    report = await fees_agent.run_school(db_session, school, find=_find, read=_read(discount(), made_up), apply=True)
    assert report["outcome"] == "written"
    assert [d["kept"] for d in report["discounts"]] == [True, False]
    block = school.attributes["fee_discounts"]
    assert block["_schema_version"] == 1 and block["website_url"] == HOME and block["read_at"]
    assert [(d["kind"], d["percent"], d["child"]) for d in block["discounts"]] == [("sibling", 5, 2)]
    assert "kept" not in block["discounts"][0]


async def test_a_dry_run_reports_the_discounts_and_stores_nothing(db_session, frozen_today):
    school = await _school(db_session)
    report = await fees_agent.run_school(db_session, school, find=_find, read=_read(discount()))
    assert report["outcome"] == "would_write"
    assert report["discounts"][0]["kept"] is True
    assert "fee_discounts" not in (school.attributes or {})


async def test_a_read_with_no_discount_clears_the_stored_ones(db_session, frozen_today):
    old = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "discounts": [{"kind": "sibling"}]}
    school = await _school(db_session, fee_discounts=old)
    await fees_agent.run_school(db_session, school, find=_find, read=_read(), apply=True)
    assert school.attributes["fee_discounts"]["discounts"] == []


async def test_a_thin_read_leaves_the_stored_discounts_alone(db_session, frozen_today):
    old = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "discounts": [{"kind": "sibling"}]}
    school = await _school(db_session, fee_discounts=old)

    async def read(_school, _pages, _missed):
        return FeeRead()

    report = await fees_agent.run_school(db_session, school, find=_find, read=read, apply=True)
    assert report["outcome"] == "kept_old_rows"
    assert school.attributes["fee_discounts"] == old


async def test_a_reader_that_gives_no_discounts_leaves_the_stored_ones_alone(db_session, frozen_today):
    old = {"_schema_version": 1, "read_at": "t", "website_url": HOME, "discounts": [{"kind": "sibling"}]}
    school = await _school(db_session, fee_discounts=old)

    async def read(_school, _pages, _missed):
        return [_fee()]  # a plain list: no discounts were asked for

    await fees_agent.run_school(db_session, school, find=_find, read=read, apply=True)
    assert school.attributes["fee_discounts"] == old


# ---------------------------------------------------------------------------
# The publish boundary
# ---------------------------------------------------------------------------


def _stored(**update) -> dict:
    item = {
        "kind": "sibling",
        "percent": 5,
        "amount": None,
        "currency": None,
        "child": 2,
        "text_i18n": {"en": "5% off", "bg": "5% отстъпка"},
        "source_url": URL,
    }
    return item | update


def _block(*items, **update) -> dict:
    return {
        "fee_discounts": {"_schema_version": 1, "read_at": "t", "website_url": HOME, "discounts": list(items)} | update
    }


def test_publishable_discounts_drop_malformed_items():
    items = [
        _stored(),
        _stored(kind="made_up"),
        _stored(text_i18n={"en": "5% off", "bg": " "}),
        _stored(source_url="javascript:alert(1)"),
        _stored(percent="5"),
        "junk",
    ]
    published = publishable_fee_discounts(_block(*items), HOME, [URL])
    assert [d["percent"] for d in published] == [5.0, None]
    assert published[0].keys() == {"kind", "percent", "amount", "currency", "child", "text_i18n", "source_url"}


def test_discounts_of_another_schema_version_or_a_moved_website_are_not_published():
    assert publishable_fee_discounts(_block(_stored(), _schema_version=2), HOME, [URL]) is None
    assert publishable_fee_discounts(_block(_stored()), "https://other.example", [URL]) is None
    assert publishable_fee_discounts(_block(_stored(), website_url=None), HOME, [URL]) is None
    assert publishable_fee_discounts(_block(), HOME, [URL]) is None
    assert publishable_fee_discounts(_block(_stored()), "https://www.school.example/", [URL]) is not None


def test_a_discount_shows_only_while_a_fee_from_its_own_page_is_published():
    assert publishable_fee_discounts(_block(_stored()), HOME, ["https://www.school.example/fees/"]) is not None
    assert publishable_fee_discounts(_block(_stored()), HOME, ["https://school.example/other-fees"]) is None
    assert publishable_fee_discounts(_block(_stored()), HOME, []) is None


async def _detail(client, db_session, *, priced: bool):
    school = await _school(
        db_session, **_block(_stored()), data_validation={"_schema_version": 1, "status": "ok", "issues": []}
    )
    if priced:
        page = SourcePage(
            school_id=school.id,
            scrape_type=ScrapeType.WEBSITE,
            source_url=URL,
            content_hash="h",
            page_category="pricing",
            is_valid=True,
        )
        db_session.add(page)
        await db_session.flush()
        db_session.add(
            Pricing(
                school_id=school.id,
                category=PriceCategory.TUITION,
                amount=700,
                currency="EUR",
                period=PricePeriod.MONTHLY,
                academic_year="2026-2027",
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=URL,
                source_page_id=page.id,
                pricing_context={"confidence": 0.9},
            )
        )
        await db_session.commit()
    response = await client.get(f"/schools/{school.id}")
    assert response.status_code == 200
    return response.json()


async def test_discounts_show_beside_published_fees(client, db_session):
    body = await _detail(client, db_session, priced=True)
    assert body["pricing"], "the fee must publish for this test to mean anything"
    assert [d["text_i18n"]["en"] for d in body["fee_discounts"]] == ["5% off"]
    assert "fee_discounts" not in (body.get("attributes") or {})


async def test_discounts_do_not_show_without_a_published_fee(client, db_session):
    body = await _detail(client, db_session, priced=False)
    assert body["pricing"] == [] and body["fee_discounts"] == []
