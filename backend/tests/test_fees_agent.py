"""R6 step 2: the fee agent's checks (``agent_fees.check_fee``) and its stage.

The check cases use page lines recorded in docs/accuracy/fresh_rescore (``run/fresh_check.log.txt``
and the ``quote`` fields of ``adj/*.json``). The first block is the 13 errors the agent made on
the fresh sample, each of which the plan says code must clear or withhold; the second block is
valid fees on the same pages, which must survive with their period. The model and the page
fetches are stubbed in the stage tests.
"""

from __future__ import annotations

import dataclasses
import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import FieldSource, Pricing, School
from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.scrapers.extraction.agent_fees import (
    HELD_CONFIDENCE,
    PUBLISH_CONFIDENCE,
    AgentFee,
    PaymentOption,
    check_fee,
    page_numbers,
)
from app.scrapers.stages import fee_check, fees_agent
from app.scrapers.stages.fees_agent import PageRead, Spend, SpendCapReached
from app.utils.display_gating import pricing_row_is_publishable
from app.utils.website_data import website_data_is_publishable

URL = "https://school.example/fees/"
HOME = "https://school.example/"


def fee(category, evidence, *, amount=None, period=None, label="fee", amount_min=None, amount_max=None, **kw):
    return AgentFee(
        category=category,
        amount=amount,
        amount_min=amount_min,
        amount_max=amount_max,
        currency="EUR",
        period=period,
        plan_name=kw.get("plan_name"),
        academic_year=kw.get("academic_year"),
        age_group=kw.get("age_group"),
        label=label,
        source_url=URL,
        evidence=evidence,
    )


def published(result) -> bool:
    return result.kept and result.confidence >= 0.7


# --- 220: toddlersacademy.eu/bg/admission.html (fresh_check.log.txt) ---------------------
PAGE_220 = """\
| **Опции за плащане**  | **Обща такса за обучение**  | EUR 8 580  |
|  **Опция 1:** 11 равни месечни вноски, платими до 5-то число на всеки месец  | EUR 780  |
|  **Опция 2:** Тримесечни вноски (отстъпка EUR 110 при плащане на 4 тримесечни вноски, платими до 5-то число на всеки период)  |  I вноска: EUR 2 310 (септ–ноем)
II вноска: EUR 2 310 (дек–фев)
III вноска: EUR 2 310 (март–май)
IV вноска: EUR 1 540 (юни–юли)   |
|  **Опция 3:** Еднократно плащане (отстъпка EUR 250 при еднократно плащане при подписване на договор)  | EUR 8 330  |
| Депозитът е платим при подписване на договор (за оборудване, консумативи, режийни разходи); приспада се от последната вноска, освен ако договорът не е прекратен предсрочно  | EUR 770  |
| **Допълнителни такси**  |  Еднократна такса за учебници и тетрадки: EUR 150
Еднократна такса за регистрация: EUR 200 (само при първо постъпване)   |
"""
LINE_220_OPTION_1 = "|  **Опция 1:** 11 равни месечни вноски, платими до 5-то число на всеки месец  | EUR 780  |"
LINE_220_OPTION_3 = "|  **Опция 3:** Еднократно плащане (отстъпка EUR 250 при еднократно плащане при подписване на договор)  | EUR 8 330  |"
LINE_220_DEPOSIT = (
    "| Депозитът е платим при подписване на договор (за оборудване, консумативи, режийни разходи); "
    "приспада се от последната вноска, освен ако договорът не е прекратен предсрочно  | EUR 770  |"
)

# --- 515: britanica-parkschool.bg/priem/. The header row is not in the recorded log; its
# labels are the ones the agent used for these columns. -------------------------------------
HEADER_515 = "| Група | Възраст | Период | Пълно плащане (годишна такса) | Две вноски | Месечно плащане |"
ROWS_515 = {
    "first": "| Първа група  | 3-4 години  | 9 месеца  | 11 400 EUR  | 2 x 5 700 EUR  | 1 x 2 992 EUR + 8 x 1 051 EUR  |",
    "second": "| Втора група  | 4-5 години  | 9 месеца  | 11 400 EUR  | 2 x 5 700 EUR  | 1 x 2 992 EUR + 8 x 1 051 EUR  |",
    "third": "| Трета група  | 5-6 години  | 9 месеца  | 11 800 EUR  | 2 x 5 900 EUR  | 1 x 2 904 EUR + 8 x 1 112 EUR  |",
    "prep": "| Подготвителна група  | 6-7 години  | 9 месеца  | 12 900 EUR  | 2 x 6 450 EUR  | 1 x 2 828 EUR + 8 x 1 259 EUR  |",
}
ADMIN_515 = [
    "Административната такса се заплаща еднократно и не подлежи на възстановяване.",
    "4. 4. Интервю с детето през декември след заплащане на административна такса от 240 евро.",
]
PAGE_515 = "\n".join([*ADMIN_515, HEADER_515, *ROWS_515.values()])

# --- 547: mechopuh.bg contract PDF. The first line is the start of the adj quote. -----------
LINE_547_INTRO = "Месечната такса за едно дете е в размер на:"
LINE_547_FULL = " 600 (шестстотин) евро (1173,50 лв.) за целодневен престой;"
LINE_547_HALF = " 550 (петстотин и петдесет) евро (1075,71 лв.) за полудневен престой."
PAGE_547 = "\n".join([LINE_547_INTRO, LINE_547_FULL, LINE_547_HALF])

# --- 531 / 569 / 632: quotes from adj/*.json -----------------------------------------------
LINE_531 = "390 евро годишно - еднократна такса - осигуряване на образователна среда и ресурси"
LINE_569 = "Учебни материли- 155 Евро"
HEADING_569 = "ДОПЪЛНИТЕЛНИ ТАКСИ ЗА УЧЕБНАТА ГОДИНА"
LINE_632 = "Получавате 10% отстъпка при плащане на пълната сума ( €7 000) наведнъж."
PAGE_531 = f"Месечна такса група 0-3 970 евро\n{LINE_531}\nМесечна такса група 3-6 г. 970 евро\n{LINE_531}"
PAGE_569 = f"{HEADING_569}\nХрана – 8 Евро за присъствен ден\n{LINE_569}"


# ---------------------------------------------------------------------------------------
# The 13 errors: each is cleared (period) or withheld
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("page", "category", "evidence", "amount"),
    [
        # Yearly fees filed as one_time (5)
        (PAGE_220, PriceCategory.TUITION, LINE_220_OPTION_3, 8330),
        (PAGE_220, PriceCategory.MATERIALS, "Еднократна такса за учебници и тетрадки: EUR 150", 150),
        (PAGE_531, PriceCategory.MATERIALS, LINE_531, 390),
        (PAGE_531, PriceCategory.MATERIALS, LINE_531, 390),
        (LINE_632, PriceCategory.TUITION, LINE_632, 7000),
    ],
)
def test_one_time_outside_registration_is_cleared(page, category, evidence, amount):
    result = check_fee(fee(category, evidence, amount=amount, period=PricePeriod.ONE_TIME), page)
    assert result.kept
    assert result.fee.period is None
    assert published(result)  # the fee publishes, with a blank period
    assert any("one_time" in check or "period" in check for check in result.checks)


@pytest.mark.parametrize("group", ["first", "second", "third", "prep"])
def test_first_instalment_of_a_plan_has_no_period(group):
    """515's 2992 (twice), 2904 and 2828 were filed as monthly; the page says '1 x A + 8 x B'."""
    amount = {"first": 2992, "second": 2992, "third": 2904, "prep": 2828}[group]
    evidence = f"{HEADER_515}\n{ROWS_515[group]}"
    result = check_fee(fee(PriceCategory.TUITION, evidence, amount=amount, period=PricePeriod.MONTHLY), PAGE_515)
    assert result.kept
    assert result.fee.period is None
    assert any("first instalment" in check for check in result.checks)


def test_last_instalment_is_not_quarterly_when_its_line_does_not_say_so():
    """220's 1540: the line says 'IV вноска ... (юни–юли)', the word 'тримесечни' is on the option's line."""
    evidence = "IV вноска: EUR 1 540 (юни–юли)   |"
    result = check_fee(fee(PriceCategory.TUITION, evidence, amount=1540, period=PricePeriod.QUARTER), PAGE_220)
    assert result.kept
    assert result.fee.period is None


def test_period_the_page_does_not_state_is_cleared():
    """569's 155 materials were filed as yearly; neither the line nor its heading names a year."""
    result = check_fee(fee(PriceCategory.MATERIALS, LINE_569, amount=155, period=PricePeriod.YEARLY), PAGE_569)
    assert result.kept
    assert result.fee.period is None
    # The heading says 'ЗА УЧЕБНАТА ГОДИНА', but it also heads per-day and monthly fees: a
    # school-year phrase names the period only on the amount's own line.
    with_heading = check_fee(
        fee(PriceCategory.MATERIALS, f"{HEADING_569}\n{LINE_569}", amount=155, period=PricePeriod.YEARLY), PAGE_569
    )
    assert with_heading.fee.period is None


def test_deposit_filed_as_tuition_is_withheld():
    """220's 770 deposit was filed as tuition."""
    result = check_fee(fee(PriceCategory.TUITION, LINE_220_DEPOSIT, amount=770, label="Депозит"), PAGE_220)
    assert result.kept
    assert result.confidence == HELD_CONFIDENCE
    assert not published(result)
    assert any("deposit" in check for check in result.checks)


def test_range_built_from_two_fees_is_not_published():
    """547's 550 to 600 'registration' range, built from the full-day and half-day fees."""
    only_one_line = fee(
        PriceCategory.REGISTRATION, LINE_547_FULL, amount_min=550, amount_max=600, period=PricePeriod.ONE_TIME
    )
    assert not check_fee(only_one_line, PAGE_547).kept  # the line holds only one end
    both_lines = fee(
        PriceCategory.REGISTRATION,
        "\n".join([LINE_547_FULL, LINE_547_HALF]),
        amount_min=550,
        amount_max=600,
        period=PricePeriod.ONE_TIME,
    )
    result = check_fee(both_lines, PAGE_547)
    assert result.kept and not published(result)
    assert any("range" in check for check in result.checks)


def test_hyphen_inside_a_label_does_not_join_two_fees_into_a_range():
    page = "Full-day 600 EUR\nHalf-day 550 EUR"
    result = check_fee(fee(PriceCategory.TUITION, page, amount_min=550, amount_max=600), page)
    assert result.kept and not published(result)


# ---------------------------------------------------------------------------------------
# Valid fees on the same pages survive
# ---------------------------------------------------------------------------------------


def test_valid_fees_keep_their_period():
    cases = [
        (PAGE_515, PriceCategory.TUITION, f"{HEADER_515}\n{ROWS_515['first']}", 11400, PricePeriod.YEARLY),
        (PAGE_515, PriceCategory.TUITION, f"{HEADER_515}\n{ROWS_515['first']}", 1051, PricePeriod.MONTHLY),
        (PAGE_515, PriceCategory.TUITION, f"{HEADER_515}\n{ROWS_515['prep']}", 1259, PricePeriod.MONTHLY),
        (PAGE_220, PriceCategory.TUITION, LINE_220_OPTION_1, 780, PricePeriod.MONTHLY),
        (PAGE_547, PriceCategory.TUITION, f"{LINE_547_INTRO}\n{LINE_547_FULL}", 600, PricePeriod.MONTHLY),
        (PAGE_547, PriceCategory.TUITION, f"{LINE_547_INTRO}\n{LINE_547_HALF}", 550, PricePeriod.MONTHLY),
        (PAGE_531, PriceCategory.MATERIALS, LINE_531, 390, PricePeriod.YEARLY),
        (PAGE_515, PriceCategory.REGISTRATION, "\n".join(ADMIN_515), 240, PricePeriod.ONE_TIME),
        (PAGE_220, PriceCategory.REGISTRATION, "Еднократна такса за регистрация: EUR 200 (само при първо постъпване)", 200, PricePeriod.ONE_TIME),
    ]  # fmt: skip
    for page, category, evidence, amount, period in cases:
        result = check_fee(fee(category, evidence, amount=amount, period=period), page)
        assert published(result), (amount, result.checks)
        assert result.confidence == PUBLISH_CONFIDENCE
        assert result.fee.period is period, (amount, result.checks)
        assert result.checks == []


def test_a_row_alone_does_not_name_the_yearly_period():
    """Without a heading line naming the year, 515's 11400 publishes with a blank period."""
    result = check_fee(fee(PriceCategory.TUITION, ROWS_515["first"], amount=11400, period=PricePeriod.YEARLY), PAGE_515)
    assert published(result) and result.fee.period is None


def test_deposit_filed_as_registration_publishes():
    page = "При записване се заплаща еднократен депозит от 475,50 € (930 лв.), който се приспада от последния месец."
    result = check_fee(
        fee(PriceCategory.REGISTRATION, page, amount=475.5, period=PricePeriod.ONE_TIME, label="депозит"), page
    )
    assert published(result) and result.fee.period is PricePeriod.ONE_TIME


@pytest.mark.parametrize(
    "line",
    [
        "Такса: от 550 до 600 евро месечно",
        "Такса 550 – 600 EUR месечно",
        "Fee 550-600 EUR monthly",
        "Fee 550 EUR to 600 EUR monthly",
    ],
)
def test_stated_range_publishes(line):
    result = check_fee(
        fee(PriceCategory.TUITION, line, amount_min=550, amount_max=600, period=PricePeriod.MONTHLY), line
    )
    assert published(result) and result.fee.period is PricePeriod.MONTHLY


def test_evidence_must_be_on_the_page_and_hold_the_amount():
    page = "Месечна такса: 700 евро\nХрана: 90 евро"
    ok = fee(PriceCategory.TUITION, "Месечна такса: 700 евро", amount=700, period=PricePeriod.MONTHLY)
    assert published(check_fee(ok, page))
    assert not check_fee(ok.model_copy(update={"evidence": "Месечна такса: 750 евро"}), page).kept  # not on the page
    assert not check_fee(ok.model_copy(update={"evidence": ""}), page).kept
    wrong_amount = ok.model_copy(update={"amount": 90.0})  # on the page, but not in this line
    assert not check_fee(wrong_amount, page).kept
    spaced = ok.model_copy(update={"evidence": "Месечна   такса: 700 евро"})
    assert published(check_fee(spaced, page))  # whitespace is normalised


def test_quarter_term_and_semester_need_their_words():
    page = "Тримесечна такса: 2 310 EUR\nСрок на плащане: 5-то число\nСеместър 5 000 EUR"
    quarter = check_fee(
        fee(PriceCategory.TUITION, "Тримесечна такса: 2 310 EUR", amount=2310, period=PricePeriod.QUARTER), page
    )
    assert quarter.fee.period is PricePeriod.QUARTER
    as_monthly = check_fee(
        fee(PriceCategory.TUITION, "Тримесечна такса: 2 310 EUR", amount=2310, period=PricePeriod.MONTHLY), page
    )
    assert as_monthly.fee.period is None  # 'тримесечна' does not name a month
    semester = check_fee(
        fee(PriceCategory.TUITION, "Семестър 5 000 EUR", amount=5000, period=PricePeriod.SEMESTER), page
    )
    assert semester.fee.period is PricePeriod.SEMESTER


# ---------------------------------------------------------------------------------------
# Number normalisation
# ---------------------------------------------------------------------------------------


def test_page_numbers_normalise_spaces_separators_and_decimal_commas():
    numbers = page_numbers("Такса 7 400 EUR, 7 400, 1.200,00 лв, 475,50 €, 7.400 евро")
    assert {Decimal(7400), Decimal(1200), Decimal("475.5")} <= numbers
    assert Decimal("7.4") in numbers  # '7.400' may also mean 7.4


def test_page_numbers_leave_out_phones_and_dates():
    numbers = page_numbers("Тел. 0888 123 456 789, от 15.09.2026 до 30.06.2027")
    assert not {Decimal(888123456789), Decimal(15092026)} & numbers
    assert Decimal(15092026) not in numbers


def test_parts_of_a_space_joined_table_row_are_numbers():
    assert {Decimal(580), Decimal(600)} <= page_numbers("| Кл. 1 | 580 600 |")


# ---------------------------------------------------------------------------------------
# The stage (model and fetches stubbed)
# ---------------------------------------------------------------------------------------

PAGE_STAGE = "Месечна такса: 700 евро\nЕднократна такса за записване: 200 евро.\nХрана: 90 евро месечно"
FEES_STAGE = [
    fee(PriceCategory.TUITION, "Месечна такса: 700 евро", amount=700, period=PricePeriod.MONTHLY, academic_year="2026/2027"),
    fee(PriceCategory.REGISTRATION, "Еднократна такса за записване: 200 евро.", amount=200, period=PricePeriod.ONE_TIME, academic_year="2026/2027"),
]  # fmt: skip


def _find(pages):
    async def find(school):
        return pages

    return find


def _read(fees):
    async def read(school, pages, missed):
        return fees

    return read


@pytest.fixture
def pages():
    return [
        PageRead(HOME, "Начало\nКонтакти", False),
        PageRead(URL, PAGE_STAGE, True),
    ]


async def _school(db) -> School:
    school = School(
        name_i18n={"bg": "Частно училище"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://school.example",
        scrape_status="extracted",
    )
    db.add(school)
    await db.flush()
    return school


async def _old_rows(db, school, year, amounts, *, page_url=URL):
    page = (
        await db.execute(select(SourcePage).where(SourcePage.school_id == school.id, SourcePage.source_url == page_url))
    ).scalar_one_or_none()
    if page is None:
        page = SourcePage(
            school_id=school.id,
            scrape_type=ScrapeType.WEBSITE,
            source_url=page_url,
            content_hash="old",
            page_category="pricing",
            is_valid=True,
            raw_markdown="Месечна такса: 700 евро\n" + "\n".join(f"{a} евро" for a in amounts),
        )
        db.add(page)
        await db.flush()
    for amount in amounts:
        db.add(
            Pricing(
                school_id=school.id,
                category=PriceCategory.TUITION,
                amount=amount,
                currency="EUR",
                period=PricePeriod.MONTHLY,
                academic_year=year,
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=page_url,
                source_page_id=page.id,
                pricing_context={"confidence": 0.9},
            )
        )
    await db.flush()


async def _rows(db, school):
    result = await db.execute(
        select(Pricing)
        .where(Pricing.school_id == school.id)
        .options(selectinload(Pricing.source_page))
        .order_by(Pricing.academic_year, Pricing.amount)
    )
    return result.scalars().all()


async def test_dry_run_writes_nothing(db_session, pages, frozen_today):
    school = await _school(db_session)
    await db_session.commit()
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE))
    assert report["outcome"] == "would_write"
    assert report["rows_new_publishable"] == 2
    assert [f["kept"] for f in report["fees"]] == [True, True]
    assert [p["prices"] for p in report["pages"]] == [False, True]
    assert not db_session.new and not db_session.dirty
    assert (await _rows(db_session, school)) == []
    assert (await db_session.execute(select(SourcePage))).scalars().all() == []
    assert (await db_session.execute(select(FieldSource))).scalars().all() == []


async def test_apply_writes_rows_and_pages(db_session, pages, frozen_today):
    school = await _school(db_session)
    await db_session.commit()
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True)
    assert report["outcome"] == "written" and report["validation"] == "ok", report
    await db_session.refresh(school)
    assert website_data_is_publishable(school.attributes, school.scrape_status)
    rows = await _rows(db_session, school)
    assert {(r.category, float(r.amount), r.period) for r in rows} == {
        (PriceCategory.TUITION, 700.0, PricePeriod.MONTHLY),
        (PriceCategory.REGISTRATION, 200.0, PricePeriod.ONE_TIME),
    }
    for row in rows:
        assert row.source is PriceSource.SCRAPED_WEBSITE and row.source_url == URL
        assert row.pricing_context["extractor"] == "agent" and row.pricing_context["confidence"] == 0.9
        assert row.pricing_context["evidence"] and row.pricing_context["checks"] == []
        assert row.source_page.source_url == URL and row.source_page.raw_markdown == PAGE_STAGE
        assert pricing_row_is_publishable(row)
    stored = {p.source_url: p for p in (await db_session.execute(select(SourcePage))).scalars()}
    assert set(stored) == {URL, HOME}
    assert stored[URL].page_category == "pricing" and stored[HOME].page_category is None
    assert len(stored[URL].content_hash) == 64 and stored[URL].is_valid
    assert len((await db_session.execute(select(FieldSource))).scalars().all()) == 2


async def test_rows_stay_withheld_when_stage_6_fails(db_session, pages, frozen_today, monkeypatch):
    async def _failed(db, school_id, *args, **kwargs):
        return {"school_id": school_id, "status": "validation_failed", "error": "boom"}

    monkeypatch.setattr(fees_agent, "validate_school_data", _failed)
    school = await _school(db_session)
    await db_session.commit()
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True)
    assert report["outcome"] == "written" and report["validation"] == "validation_failed"
    assert not website_data_is_publishable(school.attributes, school.scrape_status)


async def test_apply_replaces_only_the_years_it_read(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2025/2026", [650])
    await _old_rows(db_session, school, "2026/2027", [700, 99])
    await db_session.commit()
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True)
    assert report["outcome"] == "written", report
    rows = await _rows(db_session, school)
    assert [(r.academic_year, float(r.amount)) for r in rows] == [
        ("2025/2026", 650.0),  # an earlier year stays as history
        ("2026/2027", 200.0),
        ("2026/2027", 700.0),  # the 2026/2027 rows were replaced
    ]
    assert rows[0].pricing_context == {"confidence": 0.9} and rows[2].pricing_context["extractor"] == "agent"


async def test_page_another_stage_ruled_invalid_keeps_the_old_rows(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2025/2026", [650])
    page = (await db_session.execute(select(SourcePage))).scalar_one()
    page.is_valid = False  # e.g. the shared-site check found the site belongs to another school
    await db_session.commit()
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True)
    assert report["outcome"] == "kept_old_rows" and "ruled invalid" in report["reason"]
    assert [float(r.amount) for r in await _rows(db_session, school)] == [650.0]
    assert page.is_valid is False


async def test_page_with_an_unsettled_verdict_keeps_the_published_rows(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700])
    page = (await db_session.execute(select(SourcePage))).scalar_one()
    old_rows = await _rows(db_session, school)
    page.is_valid = None  # URL validation was ambiguous; rows on it are withheld
    await db_session.commit()
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True)
    assert report["outcome"] == "kept_old_rows", report
    assert [r.id for r in await _rows(db_session, school)] == [r.id for r in old_rows]


async def test_stored_page_spelled_differently_is_updated_in_place(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700], page_url=URL.rstrip("/"))
    await db_session.commit()
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True)
    assert report["outcome"] == "written", report
    stored = (await db_session.execute(select(SourcePage).where(SourcePage.source_url != HOME))).scalars().all()
    assert [p.source_url for p in stored] == [URL.rstrip("/")]
    rows = await _rows(db_session, school)
    assert [(float(r.amount), r.source_page_id) for r in rows] == [(200.0, stored[0].id), (700.0, stored[0].id)]


async def test_fewer_than_half_the_published_rows_keeps_the_old_rows(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 710, 720, 730])
    await db_session.commit()
    one_fee = FEES_STAGE[:1]  # 1 publishable row against 4 published
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(one_fee), apply=True)
    assert report["outcome"] == "kept_old_rows"
    assert (report["rows_new_publishable"], report["rows_old_published"]) == (1, 4)
    rows = await _rows(db_session, school)
    assert [float(r.amount) for r in rows] == [700.0, 710.0, 720.0, 730.0]
    assert all(r.pricing_context == {"confidence": 0.9} for r in rows)
    assert [p.source_url for p in (await db_session.execute(select(SourcePage))).scalars()] == [URL]  # not rewritten

    # Half as many is enough
    two_fees = [FEES_STAGE[0], FEES_STAGE[0].model_copy(update={"label": "second", "plan_name": "x"})]
    assert (await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(two_fees), apply=True))[
        "outcome"
    ] == "written"


async def test_a_read_with_nothing_publishable_keeps_the_old_rows(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700])
    await db_session.commit()
    nothing_invented = [FEES_STAGE[0].model_copy(update={"evidence": "Месечна такса: 750 евро"})]  # not on the page
    for fees in ([], nothing_invented):
        report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(fees), apply=True)
        assert report["outcome"] == "kept_old_rows"
        assert len(await _rows(db_session, school)) == 1
    no_prices = [PageRead(URL, "Начало", False)]
    report = await fees_agent.run_school(db_session, school, find=_find(no_prices), read=_read(FEES_STAGE), apply=True)
    assert report["outcome"] == "kept_old_rows" and report["reason"] == "no page showing prices"


async def test_withheld_fee_is_stored_below_the_publish_floor(db_session, pages, frozen_today):
    school = await _school(db_session)
    await db_session.commit()
    deposit = fee(
        PriceCategory.TUITION, "Месечна такса: 700 евро", amount=700, label="депозит", academic_year="2026/2027"
    )
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read([*FEES_STAGE, deposit.model_copy(update={"amount": 90.0, "evidence": "Храна: 90 евро месечно"})]), apply=True
    )  # fmt: skip
    rows = {float(r.amount): r for r in await _rows(db_session, school)}
    assert rows[90.0].pricing_context["confidence"] == HELD_CONFIDENCE
    assert not pricing_row_is_publishable(rows[90.0]) and pricing_row_is_publishable(rows[700.0])
    assert report["rows_new"] == 3 and report["rows_new_publishable"] == 2


async def test_spend_cap_stops_before_the_call():
    school = School(name_i18n={"bg": "Училище"}, school_type="private", education_level="primary")
    with pytest.raises(SpendCapReached):
        await fees_agent.read_fees(school, [PageRead(URL, PAGE_STAGE, True)], Spend(0.0), ())
    assert Spend(0.5).allows(0.5) and not Spend(0.5).allows(0.51)


@pytest.mark.parametrize(
    ("line", "period"),
    [
        ("Летен лагер: 640 Евро /месец", PricePeriod.MONTHLY),
        ("Таксата е 450 евро на месец", PricePeriod.MONTHLY),
        ("Такса за учебната година: 7200 евро", PricePeriod.YEARLY),
        ("Annual tuition 7200 EUR", PricePeriod.YEARLY),
    ],
)
def test_common_period_wordings_keep_their_period(line, period):
    amount = 640.0 if "640" in line else 450.0 if "450" in line else 7200.0
    fee = AgentFee(
        category=PriceCategory.TUITION if amount != 640.0 else PriceCategory.CAMP,
        amount=amount,
        currency="EUR",
        period=period,
        label="fee",
        source_url="https://example.bg/fees",
        evidence=line,
        plan_name=None,
        academic_year=None,
        age_group=None,
    )
    checked = check_fee(fee, f"Такси\n{line}\n")
    assert checked.kept and checked.fee.period is period


async def test_a_failed_school_still_finalises_the_run(db_session, monkeypatch):
    """A rollback after one school's failure expires the loaded run; the batch must still close it."""
    import contextlib

    import crawl4ai

    from app.models.pipeline_run import PipelineRun

    school = await _school(db_session)
    await db_session.commit()

    class _Crawler:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def _fail(db, school, **kwargs):
        await db.execute(select(SourcePage))  # opens a transaction, so the failure rolls it back
        raise RuntimeError("site down")

    @contextlib.asynccontextmanager
    async def _nothing(*args, **kwargs):
        yield None

    monkeypatch.setattr(crawl4ai, "AsyncWebCrawler", _Crawler)
    monkeypatch.setattr(fees_agent, "fee_http_client", _nothing)
    monkeypatch.setattr(fees_agent, "pipeline_run_heartbeat", _nothing)
    monkeypatch.setattr(fees_agent, "run_school", _fail)
    report = await fees_agent.run_fees_agent(db_session, school_ids=[school.id])
    assert report["outcomes"] == {"error": 1}
    run = await db_session.get(PipelineRun, report["pipeline_run_id"])
    assert run.status.value != "running"


async def test_a_run_reports_each_school_as_it_goes_and_can_start_after_one(db_session, monkeypatch, tmp_path):
    """A killed Sofia dry run (2026-10-13) lost every read: the report was written only at the end."""
    import contextlib

    import crawl4ai

    first, second, third = [await _school(db_session) for _ in range(3)]
    await db_session.commit()

    class _Crawler:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def _read(db, school, **kwargs):
        return {"school_id": school.id, "outcome": "would_write"}

    @contextlib.asynccontextmanager
    async def _nothing(*args, **kwargs):
        yield None

    monkeypatch.setattr(crawl4ai, "AsyncWebCrawler", _Crawler)
    monkeypatch.setattr(fees_agent, "fee_http_client", _nothing)
    monkeypatch.setattr(fees_agent, "pipeline_run_heartbeat", _nothing)
    monkeypatch.setattr(fees_agent, "run_school", _read)
    lines = tmp_path / "run.schools.jsonl"
    lines.write_text('{"school_id": 0, "outcome": "would_write"}\n', encoding="utf-8")  # the stopped run's
    report = await fees_agent.run_fees_agent(db_session, city="sofia", after_id=first.id, lines_path=lines)
    written = [json.loads(line)["school_id"] for line in lines.read_text(encoding="utf-8").splitlines()]
    assert written == [0, second.id, third.id]
    assert [result["school_id"] for result in report["results"]] == [second.id, third.id]


async def test_a_browser_that_will_not_start_still_finalises_the_run(db_session, monkeypatch):
    import crawl4ai

    from app.models.pipeline_run import PipelineRun

    school = await _school(db_session)
    await db_session.commit()

    class _BrokenCrawler:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            raise RuntimeError("chromium did not launch")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(crawl4ai, "AsyncWebCrawler", _BrokenCrawler)
    report = await fees_agent.run_fees_agent(db_session, school_ids=[school.id])
    assert "chromium did not launch" in report["stopped"] and report["schools"] == 0
    run = await db_session.get(PipelineRun, report["pipeline_run_id"])
    assert run.status.value != "running"


@pytest.mark.parametrize(
    ("category", "evidence", "amount", "period"),
    [
        # Step 3 (docs/accuracy/step3_check.md): periods the page states that were cleared.
        (PriceCategory.REGISTRATION, "* Таксата за явяване на изпит е в размер на 75 € и се заплаща по банков път.", 75, PricePeriod.ONE_TIME),
        (PriceCategory.TUITION, "LYCÉE\nTotal droits scolaires (l'année) 1er versement\n1er enfant 8 032,81 EUR 3 213,12 EUR", 8032.81, PricePeriod.YEARLY),
        (PriceCategory.EXTRACURRICULAR, "Discipline Tarif annuel\nOption Danse 300,00 EUR", 300, PricePeriod.YEARLY),
        (PriceCategory.TUITION, "За цялата учебна година Подготвителна група\nпри плащане в срок до 8800", 8800, PricePeriod.YEARLY),
    ],
)  # fmt: skip
def test_periods_stated_in_other_words_are_kept(category, evidence, amount, period):
    result = check_fee(fee(category, evidence, amount=amount, period=period), evidence)
    assert published(result) and result.fee.period is period, result.checks


def test_one_time_still_needs_a_word_outside_registration():
    line = "Учебни материали 120 евро"
    result = check_fee(fee(PriceCategory.MATERIALS, line, amount=120, period=PricePeriod.ONE_TIME), line)
    assert result.fee.period is None


async def test_a_fee_the_read_missed_keeps_its_row_and_the_rest_is_written(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])  # the page still shows 90 (food)
    await db_session.commit()
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True)
    assert report["outcome"] == "written" and report["rows_old_kept"] == 1, report
    rows = await _rows(db_session, school)
    assert [(float(r.amount), (r.pricing_context or {}).get("extractor")) for r in rows] == [
        (90.0, None),  # kept
        (200.0, "agent"),
        (700.0, "agent"),
    ]
    assert "pricing_hold" not in (school.attributes or {})


OLD_URL = "https://school.example/old-fees/"


def _gone(*urls):
    async def gone(url):
        return url in urls

    return gone


async def test_a_gone_price_page_stops_holding_its_old_rows(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, None, [580], page_url=OLD_URL)  # last year's nursery fee
    await db_session.commit()
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True, gone=_gone(OLD_URL)
    )
    assert report["pages_gone"] == [OLD_URL], report
    published = [float(r.amount) for r in await _rows(db_session, school) if pricing_row_is_publishable(r)]
    assert sorted(published) == [200.0, 700.0]  # 580 is still stored, on a page marked invalid


async def test_a_price_page_that_still_loads_keeps_its_rows(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, None, [580], page_url=OLD_URL)
    await db_session.commit()
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True, gone=_gone()
    )
    assert "pages_gone" not in report, report
    published = [float(r.amount) for r in await _rows(db_session, school) if pricing_row_is_publishable(r)]
    assert 580.0 in published


async def test_a_dry_run_reports_a_gone_page_and_leaves_it_valid(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, None, [580], page_url=OLD_URL)
    await db_session.commit()
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read(FEES_STAGE), gone=_gone(OLD_URL)
    )
    assert report["pages_gone"] == [OLD_URL]
    old = (await db_session.execute(select(SourcePage).where(SourcePage.source_url == OLD_URL))).scalar_one()
    assert old.is_valid is True


async def test_page_gone_is_false_when_the_site_does_not_answer():
    def handler(request):
        raise httpx.ConnectError("down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fees_agent.page_gone(client, OLD_URL) is False


@pytest.mark.parametrize(("status", "gone"), [(404, True), (410, True), (200, False), (500, False)])
async def test_page_gone_reads_only_the_status(status, gone):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status))) as client:
        assert await fees_agent.page_gone(client, OLD_URL) is gone


async def test_a_gone_page_is_saved_invalid_when_no_page_shows_prices(db_session, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, None, [580], page_url=OLD_URL)
    await db_session.commit()
    no_prices = [PageRead(HOME, "Начало", False)]
    report = await fees_agent.run_school(
        db_session, school, find=_find(no_prices), read=_read([]), apply=True, gone=_gone(OLD_URL)
    )
    assert report["outcome"] == "kept_old_rows" and report["pages_gone"] == [OLD_URL]
    await db_session.rollback()  # what a later school's failure would do
    old = (await db_session.execute(select(SourcePage).where(SourcePage.source_url == OLD_URL))).scalar_one()
    await db_session.refresh(old)
    assert old.is_valid is False


async def test_a_fee_the_read_missed_is_read_again_by_name(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])  # the page still shows 90 (food)
    await db_session.commit()
    food = fee(
        PriceCategory.FOOD, "Храна: 90 евро месечно", amount=90, period=PricePeriod.MONTHLY, academic_year="2026/2027"
    )
    asked = []

    async def read(school, pages, missed):
        asked.append(list(missed))
        return [*FEES_STAGE, food] if missed else FEES_STAGE

    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=read, apply=True)
    assert asked == [[], [Decimal("90")]]
    assert report["reread"] == {"missed": ["90"], "added": 1}
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0, report
    rows = await _rows(db_session, school)
    assert [(float(r.amount), r.category, (r.pricing_context or {}).get("extractor")) for r in rows] == [
        (90.0, PriceCategory.FOOD, "agent"),
        (200.0, PriceCategory.REGISTRATION, "agent"),
        (700.0, PriceCategory.TUITION, "agent"),
    ]


async def test_a_fee_the_checks_drop_is_read_again(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])
    await db_session.commit()
    food = fee(
        PriceCategory.FOOD, "Храна: 90 евро месечно", amount=90, period=PricePeriod.MONTHLY, academic_year="2026/2027"
    )
    stitched = food.model_copy(update={"evidence": "Обяд за целия ден: 90 евро"})  # not on the page
    asked = []

    async def read(school, pages, missed):
        asked.append(list(missed))
        return [*FEES_STAGE, food] if missed else [*FEES_STAGE, stitched]

    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=read, apply=True)
    assert asked == [[], [Decimal("90")]]
    assert report["reread"] == {"missed": ["90"], "added": 1}
    assert [f["kept"] for f in report["fees"] if f["amount"] == 90] == [False, True]
    rows = await _rows(db_session, school)
    assert [(float(r.amount), r.category) for r in rows] == [
        (90.0, PriceCategory.FOOD),
        (200.0, PriceCategory.REGISTRATION),
        (700.0, PriceCategory.TUITION),
    ]


async def test_a_second_read_only_adds_fees_the_first_did_not_give(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])
    await db_session.commit()
    food = fee(
        PriceCategory.FOOD, "Храна: 90 евро месечно", amount=90, period=PricePeriod.MONTHLY, academic_year="2026/2027"
    )

    async def read(school, pages, missed):
        # the second read drops 200 and repeats 700 without its period
        return [FEES_STAGE[0].model_copy(update={"period": None}), food] if missed else FEES_STAGE

    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=read, apply=True)
    assert report["reread"]["added"] == 1
    rows = await _rows(db_session, school)
    assert [(float(r.amount), r.period) for r in rows] == [
        (90.0, PricePeriod.MONTHLY),
        (200.0, PricePeriod.ONE_TIME),
        (700.0, PricePeriod.MONTHLY),
    ]


@pytest.mark.parametrize(
    "cap",
    [fees_agent.SpendCapReached("spent $0.05 of $0.05"), fees_agent.ProviderCostCapExceeded("run cap reached")],
)
async def test_a_spend_cap_on_the_second_read_keeps_the_first(db_session, pages, frozen_today, cap):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])
    await db_session.commit()

    async def read(school, pages, missed):
        if missed:
            raise cap
        return FEES_STAGE

    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=read, apply=True)
    assert report["reread"]["added"] == 0 and "error" in report["reread"]
    assert report["outcome"] == "written" and report["rows_old_kept"] == 1, report


async def test_a_failed_second_read_keeps_the_first(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])
    await db_session.commit()

    async def read(school, pages, missed):
        if missed:
            raise TimeoutError("read timed out")
        return FEES_STAGE

    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=read, apply=True)
    assert report["reread"]["error"] == "read timed out"
    assert report["outcome"] == "written" and report["rows_old_kept"] == 1, report


async def test_an_older_year_or_a_lev_twin_is_not_missed(db_session, frozen_today):
    page_text = "Месечна такса: 700 евро (1369.08 лв.)\nМинала година: 650 евро"
    school = await _school(db_session)
    await _old_rows(db_session, school, "2025/2026", [650])  # last year's table, still on the page
    await _old_rows(db_session, school, None, [1369.08])
    lev = next(r for r in await _rows(db_session, school) if float(r.amount) == 1369.08)
    lev.currency = "BGN"
    await db_session.commit()
    read = [fee(PriceCategory.TUITION, page_text, amount=700, period=PricePeriod.MONTHLY, academic_year="2026/2027")]
    pages = [PageRead(URL, page_text, True)]
    assert await fees_agent._missed_amounts(db_session, school, pages, read) == []


async def test_numbers_in_a_page_url_are_not_shown_amounts(db_session, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700])
    await db_session.commit()
    pages = [PageRead("https://school.example/?page_id=700", "Храна: 90 евро", True)]
    assert await fees_agent._missed_amounts(db_session, school, pages, []) == []


async def test_amounts_past_the_reader_limit_are_not_missed(db_session, frozen_today, monkeypatch):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])
    await db_session.commit()
    monkeypatch.setattr(fees_agent, "MAX_READ_CHARS", len(f"=== PAGE {URL}\n") + 30)
    pages = [PageRead(URL, "Месечна такса: 700 евро\n" + "x" * 50 + "\nХрана: 90 евро", True)]
    assert await fees_agent._missed_amounts(db_session, school, pages, []) == [Decimal("700")]


async def test_a_lev_row_is_missed_unless_its_euro_twin_is_the_same_kind_of_fee(db_session, frozen_today):
    page_text = "Храна: 200 лв.\nТранспорт: 102.26 евро"
    school = await _school(db_session)
    await _old_rows(db_session, school, None, [200])
    meals = (await _rows(db_session, school))[0]
    meals.currency, meals.category = "BGN", PriceCategory.FOOD
    await db_session.commit()
    bus = [fee(PriceCategory.TRANSPORT, "Транспорт: 102.26 евро", amount=102.26)]
    pages = [PageRead(URL, page_text, True)]
    assert await fees_agent._missed_amounts(db_session, school, pages, bus) == [Decimal("200")]


async def test_a_second_read_does_not_add_the_lev_column(db_session, frozen_today):
    page_text = "Месечна такса: 700 евро | 1369.08 лв.\nХрана: 90 евро | 176.02 лв."
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])
    await db_session.commit()
    tuition = fee(
        PriceCategory.TUITION,
        "Месечна такса: 700 евро | 1369.08 лв.",
        amount=700,
        period=PricePeriod.MONTHLY,
        academic_year="2026/2027",
    )
    food = fee(
        PriceCategory.FOOD,
        "Храна: 90 евро | 176.02 лв.",
        amount=90,
        period=PricePeriod.MONTHLY,
        academic_year="2026/2027",
    )
    lev = [
        fee(
            PriceCategory.TUITION, "Месечна такса: 700 евро | 1369.08 лв.", amount=1369.08, period=PricePeriod.MONTHLY
        ).model_copy(update={"currency": "BGN"}),
        fee(PriceCategory.FOOD, "Храна: 90 евро | 176.02 лв.", amount=176.02, period=PricePeriod.MONTHLY).model_copy(
            update={"currency": "BGN"}
        ),
    ]

    async def read(school, pages, missed):
        return [tuition, food, *lev] if missed else [tuition]

    pages = [PageRead(URL, page_text, True)]
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=read)
    assert report["reread"]["added"] == 1, report  # food 90 €; neither lev twin


async def test_no_second_read_when_nothing_published_was_missed(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700])
    await db_session.commit()
    asked = []

    async def read(school, pages, missed):
        asked.append(list(missed))
        return FEES_STAGE

    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=read)
    assert asked == [[]] and "reread" not in report


async def test_a_period_the_read_drops_but_the_page_states_is_filled_back(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700])  # monthly, as the page says
    await db_session.commit()
    no_period = [FEES_STAGE[0].model_copy(update={"period": None}), FEES_STAGE[1]]
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(no_period), apply=True)
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0, report
    rows = await _rows(db_session, school)
    assert [(float(r.amount), r.period) for r in rows] == [(200.0, PricePeriod.ONE_TIME), (700.0, PricePeriod.MONTHLY)]


async def test_an_old_bgn_row_gives_way_to_the_same_fee_in_euro(db_session, frozen_today):
    page_text = "Месечна такса: 700 евро (1369.08 лв.)"
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [1369.08])
    old = (await _rows(db_session, school))[0]
    old.currency = "BGN"
    await db_session.commit()
    read = [fee(PriceCategory.TUITION, page_text, amount=700, period=PricePeriod.MONTHLY, academic_year="2026/2027")]
    pages = [PageRead(URL, page_text, True)]
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(read), apply=True)
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0, report
    assert [(float(r.amount), r.currency) for r in await _rows(db_session, school)] == [(700.0, "EUR")]


def test_the_same_fee_read_from_two_pages_is_one_row():
    school = School(id=1, name_i18n={}, country_code="bg", school_type="private", education_level="primary")
    page = SourcePage(id=-1, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=URL)
    other = SourcePage(id=-2, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=HOME)
    tuition = check_fee(FEES_STAGE[0], PAGE_STAGE)
    relabelled = check_fee(FEES_STAGE[0].model_copy(update={"label": "Такса"}), PAGE_STAGE)
    rows, sources = fees_agent._build_rows(school, [(tuition, page), (relabelled, other)])
    assert len(rows) == len(sources) == 1


def test_the_same_fee_with_a_group_on_one_page_only_is_one_row():
    """596's secondary sheet repeats the primary sheet's transport; a read named it "Secondary School" there."""
    school = School(id=1, name_i18n={}, country_code="bg", school_type="private", education_level="primary")
    page = SourcePage(id=-1, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=URL)
    other = SourcePage(id=-2, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=HOME)
    line = "TRANSPORTATION € 2,550 annual fee (two-way service)"

    def transport(**kw):
        read = fee(PriceCategory.TRANSPORT, line, amount=2550, period=PricePeriod.YEARLY, **kw)
        return check_fee(read, line)

    rows, _ = fees_agent._build_rows(
        school, [(transport(), page), (transport(age_group="Secondary School", plan_name="Two-way"), other)]
    )
    assert [(r.age_group, r.plan_name) for r in rows] == [(None, None)]
    # Another group's fee on the same page, or another plan, stays its own row.
    rows, _ = fees_agent._build_rows(school, [(transport(), page), (transport(age_group="Grade 1"), page)])
    assert len(rows) == 2
    rows, _ = fees_agent._build_rows(
        school, [(transport(plan_name="One-way"), page), (transport(age_group="Grade 1", plan_name="Two-way"), other)]
    )
    assert len(rows) == 2
    # A held ungrouped row does not take the place of a publishable grouped one.
    held = dataclasses.replace(transport(), confidence=HELD_CONFIDENCE)
    rows, _ = fees_agent._build_rows(school, [(held, page), (transport(age_group="Grade 1"), other)])
    assert len(rows) == 2


async def test_an_amount_read_as_another_kind_of_fee_replaces_the_old_row(db_session, frozen_today):
    page_text = "Месечна такса: 700 евро\nЕднократна такса за записване: 200 евро.\nХрана: 90 евро"
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])  # 90 monthly: a period the read loses
    await db_session.commit()
    refiled = fee(PriceCategory.EXTRACURRICULAR, "Храна: 90 евро", amount=90, academic_year="2026/2027")
    report = await fees_agent.run_school(
        db_session,
        school,
        find=_find([PageRead(URL, page_text, True)]),
        read=_read([*FEES_STAGE, refiled]),
        apply=True,
    )
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0, report
    rows = await _rows(db_session, school)
    assert [(float(r.amount), r.category) for r in rows if float(r.amount) == 90] == [
        (90.0, PriceCategory.EXTRACURRICULAR)
    ]


async def test_an_amount_read_as_the_same_fee_without_its_period_keeps_the_old_row(db_session, frozen_today):
    page_text = "Месечна такса: 700 евро\nЕднократна такса за записване: 200 евро.\nЗанималня: 90 евро"
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 90])  # 90 monthly tuition
    await db_session.commit()
    same = fee(PriceCategory.TUITION, "Занималня: 90 евро", amount=90, academic_year="2026/2027")
    report = await fees_agent.run_school(
        db_session,
        school,
        find=_find([PageRead(URL, page_text, True)]),
        read=_read([*FEES_STAGE, same]),
        apply=True,
    )
    assert report["outcome"] == "written" and report["rows_old_kept"] == 1, report
    rows = await _rows(db_session, school)
    assert [(float(r.amount), r.period) for r in rows if float(r.amount) == 90] == [(90.0, PricePeriod.MONTHLY)]


async def _refile_run(db_session, school, read_90, page_text="Месечна такса: 700 евро\nХрана: 90 евро\nКлуб: 90 евро"):
    return await fees_agent.run_school(
        db_session,
        school,
        find=_find([PageRead(URL, page_text, True)]),
        read=_read([*FEES_STAGE, *read_90]),
        apply=True,
    )


async def test_a_missed_fee_sharing_its_amount_with_another_fee_keeps_its_row(db_session, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [90])
    (await _rows(db_session, school))[0].category = PriceCategory.FOOD  # the read misses the meals
    await db_session.commit()
    club = fee(PriceCategory.EXTRACURRICULAR, "Клуб: 90 евро", amount=90, academic_year="2026/2027")
    report = await _refile_run(db_session, school, [club])
    assert report["rows_old_kept"] == 1, report
    rows = await _rows(db_session, school)
    assert PriceCategory.FOOD in {r.category for r in rows if float(r.amount) == 90}


async def test_an_amount_in_another_currency_is_not_a_refiled_fee(db_session, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [90])
    (await _rows(db_session, school))[0].currency = "BGN"
    await db_session.commit()
    bus = fee(PriceCategory.TRANSPORT, "Транспорт: 90 евро", amount=90, academic_year="2026/2027")
    report = await _refile_run(db_session, school, [bus])
    assert report["rows_old_kept"] == 1, report


async def test_two_read_fees_at_the_old_amount_keep_the_old_row(db_session, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [90])
    await db_session.commit()
    read = [
        fee(PriceCategory.EXTRACURRICULAR, "Клуб: 90 евро", amount=90, label="Клуб", academic_year="2026/2027"),
        fee(PriceCategory.FOOD, "Храна: 90 евро", amount=90, label="Храна", academic_year="2026/2027"),
    ]
    report = await _refile_run(db_session, school, read)
    assert report["rows_old_kept"] == 1, report


async def test_a_fee_read_for_another_year_is_not_a_refiled_fee(db_session, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [90])
    await db_session.commit()
    club = fee(PriceCategory.EXTRACURRICULAR, "Клуб: 90 евро", amount=90, academic_year="2025/2026")
    report = await _refile_run(db_session, school, [club])
    assert report["rows_old_kept"] == 1, report


async def test_an_old_bgn_row_rounded_to_the_lev_gives_way_to_the_euro_fee(db_session, frozen_today):
    page_text = "Месечна такса: 700 € (1369 лв.)"
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [1369])
    old = (await _rows(db_session, school))[0]
    old.currency = "BGN"
    await db_session.commit()
    read = [fee(PriceCategory.TUITION, page_text, amount=700, period=PricePeriod.MONTHLY, academic_year="2026/2027")]
    report = await fees_agent.run_school(
        db_session, school, find=_find([PageRead(URL, page_text, True)]), read=_read(read), apply=True
    )
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0, report
    assert [(float(r.amount), r.currency) for r in await _rows(db_session, school)] == [(700.0, "EUR")]


async def test_an_old_bgn_range_gives_way_to_the_same_range_in_euro(db_session, frozen_today):
    page_text = "Месечна такса: 500 – 600 евро (978 – 1174 лв.)"
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [978])
    old = (await _rows(db_session, school))[0]
    old.currency, old.amount, old.amount_min, old.amount_max = "BGN", None, 978, 1174
    await db_session.commit()
    read = [
        fee(
            PriceCategory.TUITION, page_text, amount_min=500, amount_max=600,
            period=PricePeriod.MONTHLY, academic_year="2026/2027",
        )
    ]  # fmt: skip
    report = await fees_agent.run_school(
        db_session, school, find=_find([PageRead(URL, page_text, True)]), read=_read(read), apply=True
    )
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0, report
    rows = await _rows(db_session, school)
    assert [(float(r.amount_min), float(r.amount_max), r.currency) for r in rows] == [(500.0, 600.0, "EUR")]


def test_half_yearly_in_german_is_not_yearly():
    line = "Schulgeld 3.000 € halbjährlich"
    assert check_fee(fee(PriceCategory.TUITION, line, amount=3000, period=PricePeriod.YEARLY), line).fee.period is None
    kept = check_fee(fee(PriceCategory.TUITION, line, amount=3000, period=PricePeriod.SEMESTER), line)
    assert kept.fee.period is PricePeriod.SEMESTER


def test_a_registration_fee_the_line_calls_yearly_is_not_one_time():
    line = "Годишна такса за записване: 300 евро"
    result = check_fee(fee(PriceCategory.REGISTRATION, line, amount=300, period=PricePeriod.ONE_TIME), line)
    assert result.fee.period is None


def test_two_tiers_at_one_price_on_one_page_are_two_rows():
    school = School(id=1, name_i18n={}, country_code="bg", school_type="private", education_level="primary")
    page = SourcePage(id=-1, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=URL)
    text = "1-4 клас: 700 евро\n5-7 клас: 700 евро"
    low = check_fee(fee(PriceCategory.TUITION, "1-4 клас: 700 евро", amount=700, label="1-4 клас"), text)
    high = check_fee(fee(PriceCategory.TUITION, "5-7 клас: 700 евро", amount=700, label="5-7 клас"), text)
    rows, _ = fees_agent._build_rows(school, [(low, page), (high, page)])
    assert len(rows) == 2


def _rows_for(*fees):
    school = School(id=1, name_i18n={}, country_code="bg", school_type="private", education_level="primary")
    page = SourcePage(id=-1, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=URL)
    text = " ".join(f.evidence for f in fees)
    rows, _ = fees_agent._build_rows(school, [(check_fee(f, text), page) for f in fees])
    return rows


def test_a_plan_paid_once_is_one_row_with_its_annual_price():
    plan = {"age_group": "Grade 0", "plan_name": "Plan 1", "period": PricePeriod.YEARLY}
    annual = fee(PriceCategory.TUITION, "Annual tuition fee € 8.950", amount=8950, label="Annual tuition fee", **plan)
    once = fee(PriceCategory.TUITION, "Plan 1: 1 x € 8.950", amount=8950, label="Tuition payment", **plan)
    once.installments = ["1 x € 8.950"]
    rows = _rows_for(annual, once)
    assert [row.pricing_context["label"] for row in rows] == ["Annual tuition fee"]


def test_two_single_payment_rows_of_one_plan_are_one_row():
    plan = {"age_group": "Grade 8 – 9", "plan_name": "Plan 1", "period": PricePeriod.YEARLY}
    named = fee(PriceCategory.TUITION, "Plan 1 1 x € 9,500", amount=9500, label="Plan 1", **plan)
    named.installments = ["1 x € 9,500"]
    bare = fee(PriceCategory.TUITION, "Plan 1 1 x € 9,500", amount=9500, label="1 x € 9,500", **plan)
    assert len(_rows_for(named, bare)) == 1


def test_two_activities_at_one_price_in_one_group_are_two_rows():
    group = {"age_group": "Grades 1-4", "period": PricePeriod.MONTHLY}
    football = fee(PriceCategory.EXTRACURRICULAR, "Football 60 €", amount=60, label="Football", **group)
    chess = fee(PriceCategory.EXTRACURRICULAR, "Chess 60 €", amount=60, label="Chess", **group)
    assert len(_rows_for(football, chess)) == 2


def test_an_initial_payment_is_not_a_single_payment():
    initial = fee(PriceCategory.TUITION, "1 x € 2.650 initial", amount=2650, label="1 x € 2.650 initial")
    assert not fees_agent._single_payment(initial)


def test_an_old_bgn_row_is_matched_only_to_a_euro_fee_of_its_kind():
    old = Pricing(category=PriceCategory.FOOD, amount=200, currency="BGN")
    bus = Pricing(category=PriceCategory.TRANSPORT, amount=102.26, currency="EUR")
    assert fees_agent._in_euro(old, [bus]) is None
    meals = Pricing(category=PriceCategory.FOOD, amount=102.26, currency="EUR")
    assert fees_agent._in_euro(old, [meals]) is not None


async def test_a_read_with_nothing_new_writes_nothing(db_session, frozen_today):
    page_text = "Такса: 700 евро"
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700])  # monthly: a period the read loses
    await db_session.commit()
    read = [fee(PriceCategory.TUITION, page_text, amount=700, academic_year="2026/2027")]
    report = await fees_agent.run_school(
        db_session, school, find=_find([PageRead(URL, page_text, True)]), read=_read(read), apply=True
    )
    assert report["outcome"] == "kept_old_rows" and report["reason"] == "every fee read is already stored", report
    assert [(float(r.amount), r.period) for r in await _rows(db_session, school)] == [(700.0, PricePeriod.MONTHLY)]


def test_quarterly_in_german_is_not_yearly():
    line = "Schulgeld vierteljährlich 1.500 €"
    assert check_fee(fee(PriceCategory.TUITION, line, amount=1500, period=PricePeriod.YEARLY), line).fee.period is None
    kept = check_fee(fee(PriceCategory.TUITION, line, amount=1500, period=PricePeriod.QUARTER), line)
    assert kept.fee.period is PricePeriod.QUARTER


async def test_a_withheld_row_at_a_kept_amount_still_goes(db_session, frozen_today):
    page_text = "Такса: 700 евро"
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700, 700])  # monthly: a period the read loses
    withheld = (await _rows(db_session, school))[1]
    withheld.pricing_context = {"confidence": 0.5}
    await db_session.commit()
    read = [
        fee(PriceCategory.TUITION, page_text, amount=700, academic_year="2026/2027"),
        fee(PriceCategory.REGISTRATION, page_text, amount=700, label="other", academic_year="2026/2027"),
    ]
    report = await fees_agent.run_school(
        db_session, school, find=_find([PageRead(URL, page_text, True)]), read=_read(read), apply=True
    )
    assert report["rows_old_kept"] == 1, report
    assert withheld.id not in [r.id for r in await _rows(db_session, school)]


async def test_an_undated_read_replaces_this_years_rows_and_keeps_older_years(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026-2027", [700])  # 170: stored dated, read undated
    await _old_rows(db_session, school, "2025/2026", [650])  # history
    await db_session.commit()
    undated = [fee.model_copy(update={"academic_year": None}) for fee in FEES_STAGE]
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(undated), apply=True)
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0, report
    rows = await _rows(db_session, school)
    assert sorted((float(r.amount), r.academic_year) for r in rows) == [
        (200.0, None),
        (650.0, "2025/2026"),
        (700.0, None),
    ]


async def test_a_read_of_next_year_keeps_this_years_rows(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [650])  # still in force
    await db_session.commit()
    next_year = [fee.model_copy(update={"academic_year": "2027/2028"}) for fee in FEES_STAGE]
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(next_year), apply=True)
    assert report["outcome"] == "written", report
    rows = await _rows(db_session, school)
    assert (650.0, "2026/2027") in [(float(r.amount), r.academic_year) for r in rows]


async def test_an_unpublished_row_on_a_page_the_read_never_opened_stays(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, None, [480], page_url="https://school.example/kindergarten.pdf")
    old = (await _rows(db_session, school))[0]
    old.pricing_context = {"confidence": 0.5}  # below the publish floor, waiting for review
    await db_session.commit()
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True)
    assert report["outcome"] == "written", report
    assert 480.0 in [float(r.amount) for r in await _rows(db_session, school)]


async def test_an_undated_fee_beside_next_years_fees_leaves_this_years_rows(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [650])  # this year's tuition, still in force
    await db_session.commit()
    read = [
        FEES_STAGE[0].model_copy(update={"academic_year": "2027/2028"}),
        FEES_STAGE[1].model_copy(update={"academic_year": None}),  # registration: rarely dated
    ]
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(read), apply=True)
    assert report["outcome"] == "written", report
    assert (650.0, "2026/2027") in [(float(r.amount), r.academic_year) for r in await _rows(db_session, school)]


@pytest.mark.parametrize(
    "line",
    [
        "Такса при записване 300 лв., платима в срок до 15 юни",  # a deadline, not a term
        "Записване 300 лв. еднократно; месечна такса 500 лв.",  # a neighbour's period
    ],
)
def test_a_registration_fee_stays_one_time_beside_other_words(line):
    result = check_fee(fee(PriceCategory.REGISTRATION, line, amount=300, period=PricePeriod.ONE_TIME), line)
    assert result.fee.period is PricePeriod.ONE_TIME


def test_tiers_on_one_page_survive_the_same_fee_read_first_elsewhere():
    school = School(id=1, name_i18n={}, country_code="bg", school_type="private", education_level="primary")
    home = SourcePage(id=-2, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=HOME)
    fees_page = SourcePage(id=-1, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=URL)
    text = "1-4 клас: 700 евро\n5-7 клас: 700 евро"
    once = check_fee(fee(PriceCategory.TUITION, "1-4 клас: 700 евро", amount=700, label="Такса"), text)
    low = check_fee(fee(PriceCategory.TUITION, "1-4 клас: 700 евро", amount=700, label="1-4 клас"), text)
    high = check_fee(fee(PriceCategory.TUITION, "5-7 клас: 700 евро", amount=700, label="5-7 клас"), text)
    rows, _ = fees_agent._build_rows(school, [(once, home), (low, fees_page), (high, fees_page)])
    assert len(rows) == 2


async def test_an_undated_read_leaves_the_same_number_in_another_currency(db_session, pages, frozen_today):
    school = await _school(db_session)
    await _old_rows(db_session, school, "2026/2027", [700])
    old = (await _rows(db_session, school))[0]
    old.currency = "BGN"  # 700 лв. is another fee than 700 €
    await db_session.commit()
    undated = [f.model_copy(update={"academic_year": None}) for f in FEES_STAGE]
    await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(undated), apply=True)
    assert (700.0, "BGN") in [(float(r.amount), r.currency) for r in await _rows(db_session, school)]


async def test_apply_keeps_attributes_another_stage_wrote_during_the_read(db_session, pages, frozen_today):
    from sqlalchemy import update

    school = await _school(db_session)
    await db_session.commit()
    attributes = dict(school.attributes or {})

    async def read(school_, pages_, missed):
        # Another stage (social links) writes its key while the reader runs.
        await db_session.execute(
            update(School)
            .where(School.id == school.id)
            .values(attributes={**attributes, "social_links": {"facebook": "https://facebook.com/x"}})
            .execution_options(synchronize_session=False)
        )
        return FEES_STAGE

    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=read, apply=True)
    assert report["outcome"] == "written", report
    await db_session.refresh(school)
    assert school.attributes["social_links"] == {"facebook": "https://facebook.com/x"}


@pytest.mark.parametrize(
    "period,evidence,named",
    [
        ("daily", "Храна – 7.5 € на ден", True),
        ("daily", "Дневна форма на обучение – 8000 €", False),
        ("weekly", "Лятна занималня – 120 € на седмица", True),
        ("weekly", "Английски, 4 учебни часа седмично – 60 €", False),
        ("hourly", "Индивидуален урок – 30 € на час", True),
        ("hourly", "Такса 30 € на час по английски", True),
        ("daily", "Занималня до 18 часа дневно – 10 €", True),
        ("weekly", "Плуване до 3 пъти седмично – 80 € на месец", False),
        ("daily", "Такса Храна (четири пъти дневно) - 140.00 евро", False),
    ],
)
def test_per_unit_periods_need_a_price_word_not_a_timetable(period, evidence, named):
    from app.scrapers.extraction.agent_fees import _names_period

    assert _names_period(PricePeriod(period), evidence, [Decimal("30")], PriceCategory.EXTRACURRICULAR) is named


# --- A period a stored row proved: 606's flattened table, its period in a sentence below ---
PAGE_PROVED = (
    "Такса обучение\n€ 3000\nЕднократна такса за записване: 200 евро.\n"
    "Годишната такса за обучение се заплаща на три вноски."
)
PROOF = "Такса обучение\n€ 3000\nГодишната такса за обучение се заплаща на три вноски."


async def _proved_row(db, school, period=PricePeriod.YEARLY, evidence=PROOF, extractor="agent"):
    page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url=URL,
        content_hash="old",
        page_category="pricing",
        is_valid=True,
        raw_markdown=PAGE_PROVED,
    )
    db.add(page)
    await db.flush()
    db.add(
        Pricing(
            school_id=school.id,
            category=PriceCategory.TUITION,
            amount=3000,
            currency="EUR",
            period=period,
            academic_year="2026/2027",
            source=PriceSource.SCRAPED_WEBSITE,
            source_url=URL,
            source_page_id=page.id,
            pricing_context={"confidence": 0.9, "extractor": extractor, "evidence": evidence},
        )
    )
    await db.commit()


def _bare_read():
    """The heading left out of the evidence this time: the check clears the period."""
    return [
        fee(PriceCategory.TUITION, "€ 3000", amount=3000, period=PricePeriod.YEARLY, academic_year="2026/2027"),
        FEES_STAGE[1],
    ]


async def _run(db, school, page_text=PAGE_PROVED):
    pages = [PageRead(URL, page_text, True)]
    return await fees_agent.run_school(db, school, find=_find(pages), read=_read(_bare_read()), apply=True)


async def test_a_period_a_stored_row_proved_comes_back_when_the_read_leaves_its_heading_out(db_session, frozen_today):
    school = await _school(db_session)
    await _proved_row(db_session, school)
    report = await _run(db_session, school)
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0 and not report["regressions"], report
    rows = [r for r in await _rows(db_session, school) if float(r.amount) == 3000]
    assert [(r.period, r.pricing_context["extractor"]) for r in rows] == [(PricePeriod.YEARLY, "agent")]
    assert "Годишната такса" in rows[0].pricing_context["evidence"]  # the next re-read can prove it again
    assert any("stored row's evidence" in check for check in rows[0].pricing_context["checks"])


async def test_a_stored_proof_no_longer_on_the_page_brings_nothing_back(db_session, frozen_today):
    school = await _school(db_session)
    await _proved_row(db_session, school)
    report = await _run(db_session, school, "Такса обучение\n€ 3000\nЕднократна такса за записване: 200 евро.")
    rows = [r for r in await _rows(db_session, school) if float(r.amount) == 3000]
    assert all("stored row's evidence" not in check for f in report["fees"] for check in f.get("checks", []))
    assert [r.pricing_context.get("checks") for r in rows] == [None]  # the old row, untouched


async def test_a_stored_row_without_evidence_proves_nothing(db_session, frozen_today):
    school = await _school(db_session)
    await _proved_row(db_session, school, extractor=None)
    report = await _run(db_session, school)
    assert report["rows_old_kept"] == 1 and report["regressions"], report


async def test_a_stored_proof_must_satisfy_todays_rules(db_session, frozen_today):
    school = await _school(db_session)  # one_time is never valid for tuition, whatever the old row says
    await _proved_row(db_session, school, period=PricePeriod.ONE_TIME, evidence="Такса обучение\n€ 3000 еднократно")
    report = await _run(db_session, school)
    assert not _stored_notes(report), report["fees"]
    assert all(f["period"] is None for f in report["fees"] if f.get("amount") == 3000), report["fees"]


@pytest.mark.parametrize(
    "evidence",
    [
        "Прием за Подготвителна група: 5-годишни деца. Необходимо е заплащане на такса за обследване в размер на 20 €.",
        "„Питагор Junior“ е за 6 годишни деца; таксата за участие е 20 евро.",
    ],
)
def test_an_age_in_years_does_not_make_a_registration_fee_recurring(evidence):
    checked = check_fee(fee(PriceCategory.REGISTRATION, evidence, amount=20, period=PricePeriod.ONE_TIME), evidence)
    assert checked.fee.period is PricePeriod.ONE_TIME, checked.checks


def test_an_age_in_years_does_not_name_the_yearly_period():
    line = "Такса за 3-годишни деца: 450 евро"
    assert check_fee(fee(PriceCategory.TUITION, line, amount=450, period=PricePeriod.YEARLY), line).fee.period is None


# --- A table row the read rebuilt in a layout the page does not have (596's secondary tuition) ---
PAGE_TABLE = "Grade 8 – 9 | Plan 1 – € 9,500 / Plan 2 – € 9,700 | 1 x € 9,500\nЕднократна такса за записване: 200 евро."
ROW_PROOF = "Grade 8 – 9 | Plan 1 – € 9,500 / Plan 2 – € 9,700 | 1 x € 9,500"


async def _table_run(db, school, page_text):
    rebuilt = fee(
        PriceCategory.TUITION, "Grade 8 – 9 Plan 1 – € 9,500 1 x € 9,500", amount=9500, academic_year="2026/2027"
    )
    pages = [PageRead(URL, page_text, True)]
    return await fees_agent.run_school(db, school, find=_find(pages), read=_read([rebuilt, FEES_STAGE[1]]), apply=True)


async def test_a_fee_whose_copied_line_is_not_on_the_page_stands_on_the_stored_rows_evidence(db_session, frozen_today):
    school = await _school(db_session)
    await _proved_row(db_session, school, period=None, evidence=ROW_PROOF)
    row = (await _rows(db_session, school))[0]
    row.amount = 9500
    await db_session.commit()
    report = await _table_run(db_session, school, PAGE_TABLE)
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0 and not report["regressions"], report
    rows = [r for r in await _rows(db_session, school) if float(r.amount) == 9500]
    assert len(rows) == 1 and "stored row" in " ".join(rows[0].pricing_context["checks"]), rows


async def test_a_stored_rows_evidence_the_page_no_longer_has_does_not_rescue_a_fee(db_session, frozen_today):
    school = await _school(db_session)
    await _proved_row(db_session, school, period=None, evidence="Grade 8 – 9 | Plan 1 – € 9,500 | 1 x € 9,500")
    row = (await _rows(db_session, school))[0]
    row.amount = 9500
    await db_session.commit()
    report = await _table_run(
        db_session, school, "Grade 8 – 9 Plan 1 – € 9,500\nЕднократна такса за записване: 200 евро."
    )
    tuition = [f for f in report["fees"] if f.get("amount") == 9500]
    assert tuition and all(not f["kept"] for f in tuition), report["fees"]


def _stored_notes(report):
    return [check for f in report["fees"] for check in f.get("checks", []) if "stored row" in check]


async def test_a_period_proved_for_one_group_does_not_pass_to_another_at_the_same_price(db_session, frozen_today):
    page_text = "Начален етап: € 3000 годишно\nГимназия: € 3000\nЕднократна такса за записване: 200 евро."
    school = await _school(db_session)
    await _proved_row(db_session, school, evidence="Начален етап: € 3000 годишно")
    secondary = fee(PriceCategory.TUITION, "Гимназия: € 3000", amount=3000, academic_year="2026/2027")
    pages = [PageRead(URL, page_text, True)]
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read([secondary, FEES_STAGE[1]]), apply=False
    )
    assert not _stored_notes(report), report["fees"]


async def test_a_stored_heading_that_moved_away_from_the_amount_proves_nothing(db_session, frozen_today):
    filler = "\n".join(f"Ред {n}" for n in range(20))
    page_text = f"Годишната такса за обучение се заплаща на три вноски.\n{filler}\nТакса обучение\n€ 3000\n{PAGE_STAGE}"
    school = await _school(db_session)
    await _proved_row(db_session, school)
    pages = [PageRead(URL, page_text, True)]
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(_bare_read()), apply=False)
    assert not _stored_notes(report), report["fees"]


async def test_a_read_that_says_another_period_keeps_its_doubt(db_session, frozen_today):
    school = await _school(db_session)
    await _proved_row(db_session, school)
    monthly = [_bare_read()[0].model_copy(update={"period": PricePeriod.MONTHLY}), FEES_STAGE[1]]
    pages = [PageRead(URL, PAGE_PROVED, True)]
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(monthly), apply=False)
    assert not _stored_notes(report), report["fees"]


@pytest.mark.parametrize("line", ["Такса обучение: € 3000 – годишно", "Такса обучение: 3000 - годишно"])
def test_a_dashed_period_after_an_amount_is_not_an_age(line):
    checked = check_fee(fee(PriceCategory.TUITION, line, amount=3000, period=PricePeriod.YEARLY), line)
    assert checked.fee.period is PricePeriod.YEARLY, checked.checks


async def test_one_line_with_two_groups_at_one_price_passes_no_period(db_session, frozen_today):
    line = "Начален етап | € 3000 годишно | Гимназия | € 3000"
    school = await _school(db_session)
    await _proved_row(db_session, school, evidence=line)
    secondary = fee(PriceCategory.TUITION, line, amount=3000, age_group="Гимназия", academic_year="2026/2027")
    pages = [PageRead(URL, f"{line}\nЕднократна такса за записване: 200 евро.", True)]
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read([secondary, FEES_STAGE[1]]), apply=False
    )
    assert not _stored_notes(report), report["fees"]


# 596's extras table in the PDF text, and a reader's cleaned copy of its rows.
PAGE_PDF_TABLE = "ADDITIONAL SERVICES\nService | Annual fee\nTransport two- / way | € 2,550\nExtended day | € 700\nLunch | 2 | 550 meals\n"


def test_a_table_row_quoted_without_its_cell_joins_is_on_the_page():
    bus = fee(PriceCategory.TRANSPORT, "Transport two-way € 2,550", amount=2550)
    result = check_fee(bus, PAGE_PDF_TABLE)
    assert result.kept
    late = fee(PriceCategory.EXTENDED_DAY, "Extended day € 700", amount=700)
    assert check_fee(late, PAGE_PDF_TABLE).kept


def test_a_loose_quote_still_needs_the_page_words_in_order():
    bus = fee(PriceCategory.TRANSPORT, "Transport one-way € 2,550", amount=2550)
    assert check_fee(bus, PAGE_PDF_TABLE).checks == ["dropped: evidence line is not on the page"]
    swapped = fee(PriceCategory.EXTENDED_DAY, "€ 700 Extended day", amount=700)
    assert not check_fee(swapped, PAGE_PDF_TABLE).kept


def test_a_cell_join_between_two_numbers_does_not_make_one_number():
    food = fee(PriceCategory.FOOD, "Lunch 2 550 meals", amount=2550)
    assert check_fee(food, PAGE_PDF_TABLE).checks == ["dropped: evidence line is not on the page"]


def test_a_cleaned_quote_is_checked_with_the_page_rows_cells():
    page = "TUITION\nPlan | Price | Instalments\nGrade 1 | € 8.950 | € 750 x 9 monthly\n"
    tuition = fee(PriceCategory.TUITION, "Grade 1 € 8.950 € 750 x 9 monthly", amount=8950, period=PricePeriod.MONTHLY)
    tuition = tuition.model_copy(update={"payment_options": [PaymentOption(payments=9, instalment=750)]})
    result = check_fee(tuition, page)
    assert result.kept
    assert result.fee.period is None
    assert result.checks[0] == "period monthly cleared: the evidence does not name it"


def test_a_cell_join_after_a_number_and_a_dot_does_not_make_one_number():
    food = fee(PriceCategory.FOOD, "Grade 1. 550 meals", amount=1550)
    assert not check_fee(food, "Grade 1. | 550 meals\nOther € 1550\n").kept


def test_a_cleaned_quote_takes_no_period_from_a_cell_it_did_not_quote():
    lunch = fee(PriceCategory.FOOD, "Lunch € 5", amount=5, period=PricePeriod.MONTHLY)
    result = check_fee(lunch, "EXTRAS\nLunch | € 5 | € 100 monthly\n")
    assert result.kept
    assert result.fee.period is None


def test_a_cleaned_quote_without_the_amount_does_not_borrow_it_from_the_row():
    bus = fee(PriceCategory.TRANSPORT, "Transport two-way", amount=2550)
    assert check_fee(bus, PAGE_PDF_TABLE).checks == ["dropped: evidence line does not hold the amount"]


def test_a_cleaned_quote_takes_no_period_from_a_cell_line_it_did_not_quote():
    meals = fee(PriceCategory.FOOD, "Meals € 6", amount=6, period=PricePeriod.MONTHLY)
    result = check_fee(meals, "EXTRAS\nMeals | € 6 / € 110 monthly\n")
    assert result.kept
    assert result.fee.period is None


def test_a_quote_without_the_pages_bold_markers_is_on_the_page():
    """564's "... 20 €**." quoted as "... 20 €."."""
    page = "Прием\n**Необходимо е заплащане на такса за обследване в размер на 20 €**.\n"
    assessment = fee(
        PriceCategory.REGISTRATION, "Необходимо е заплащане на такса за обследване в размер на 20 €.", amount=20
    )
    assert check_fee(assessment, page).kept


def test_a_cleaned_header_matches_the_table_its_pdf_prints_twice():
    """596's header is on the page as text and as cells; the reader quoted it without empty cells."""
    page = (
        "ANNUAL TUITION FEES 2026-2027\nGrade Payment Plan Plan 1 Plan 2 Plan 3\n"
        "Grade 0 Plan 1 - € 8.950 1 x € 8.950\n"
        "Grade | Payment Plan |  | Plan 1 |  | Plan 2 | Plan 3\n"
        "Grade 0 |  | Plan 1 - € 8.950 / Plan 2 - € 9.150 |  | 1 x € 8.950 | \n"
    )
    evidence = (
        "ANNUAL TUITION FEES 2026-2027\nGrade | Payment Plan | Plan 1 | Plan 2 | Plan 3\n"
        "Grade 0 |  | Plan 1 - € 8.950 / Plan 2 - € 9.150 |  | 1 x € 8.950 |"
    )
    tuition = fee(PriceCategory.TUITION, evidence, amount=8950, period=PricePeriod.YEARLY)
    result = check_fee(tuition, page)
    assert result.kept
    assert result.fee.period is PricePeriod.YEARLY


# 596's primary PDF: the tuition table as text under its heading, and again as cells far below.
# A price above the heading keeps it from being the page's title (_title_names_period).
PAGE_TWO_LAYOUTS = "\n".join(
    [
        "Application fee: € 100",
        "ANNUAL TUITION FEES 2026-2027",
        "Grade Payment Plan Plan 1 Plan 2 Plan 3",
        "Grade 0 Plan 1 - € 8.950 1 x € 8.950 2 x € 4.575 1 x € 2.650 initial",
        "9 x € 750 monthly",
        "Grade 2 - 4 Plan 1 - € 8.950 1 x € 8.950 2 x € 4.575 1 x € 2.650 initial",
        "9 x € 750 monthly",
        "Еднократна такса за записване: 200 евро.",
        *(f"Activity {n} - by semester" for n in range(20)),
        "Grade | Payment Plan | Plan 1 | Plan 2",
        "Grade 0 | Plan 1 - € 8.950 | 1 x € 8.950 | 2 x € 4.575 | 1 x € 2.650 initial / 9 x € 750 monthly",
        "Grade 2 - 4 | Plan 1 - € 8.950 | 1 x € 8.950 | 2 x € 4.575 | 1 x € 2.650 initial / 9 x € 750 monthly",
    ]
)
TEXT_PROOF = (
    "ANNUAL TUITION FEES 2026-2027\nGrade Payment Plan Plan 1 Plan 2 Plan 3\n"
    "Grade 0 Plan 1 - € 8.950 1 x € 8.950 2 x € 4.575 1 x € 2.650 initial\n9 x € 750 monthly"
)


def _cells_read(group):
    row = f"{group} | Plan 1 - € 8.950 | 1 x € 8.950 | 2 x € 4.575 | 1 x € 2.650 initial / 9 x € 750 monthly"
    read = fee(
        PriceCategory.TUITION,
        f"Grade | Payment Plan | Plan 1 | Plan 2\n{row}",
        amount=8950,
        period=PricePeriod.YEARLY,
        academic_year="2026/2027",
        age_group=group,
    )
    plans = [
        PaymentOption(payments=1, instalment=8950),
        PaymentOption(payments=2, instalment=4575),
        PaymentOption(payments=9, first_payment=2650, instalment=750),
    ]
    return read.model_copy(update={"payment_options": plans})


async def _two_layouts(db, school, group):
    await _proved_row(db, school, evidence=TEXT_PROOF)
    row = (await _rows(db, school))[0]
    row.amount, row.age_group = 8950, "Grade 0"
    await db.commit()
    pages = [PageRead(URL, PAGE_TWO_LAYOUTS, True)]
    return await fees_agent.run_school(
        db, school, find=_find(pages), read=_read([_cells_read(group), FEES_STAGE[1]]), apply=False
    )


async def test_a_stored_proof_of_the_same_group_stands_for_another_layout_of_its_table(db_session, frozen_today):
    school = await _school(db_session)
    report = await _two_layouts(db_session, school, "Grade 0")
    tuition = next(f for f in report["fees"] if f["amount"] == 8950)
    assert tuition["period"] == "yearly", tuition
    assert _stored_notes(report)


async def test_another_layouts_proof_does_not_pass_to_another_group(db_session, frozen_today):
    school = await _school(db_session)
    report = await _two_layouts(db_session, school, "Grade 2 - 4")
    tuition = next(f for f in report["fees"] if f["amount"] == 8950)
    assert tuition["period"] is None, tuition
    assert not _stored_notes(report)


async def test_a_stored_proof_of_another_fee_in_the_group_lends_no_period(db_session, frozen_today):
    page_text = "Извънкласни дейности 1–4 клас\nШах – 300 лв. годишно\nМесечни такси за клубове\nРоботика | 300 лв."
    school = await _school(db_session)
    await _proved_row(db_session, school, evidence="Шах – 300 лв. годишно")
    row = (await _rows(db_session, school))[0]
    row.category, row.amount, row.currency, row.age_group = PriceCategory.EXTRACURRICULAR, 300, "BGN", "1–4 клас"
    await db_session.commit()
    robotics = fee(PriceCategory.EXTRACURRICULAR, "Роботика | 300 лв.", amount=300, age_group="1–4 клас")
    robotics = robotics.model_copy(update={"currency": "BGN", "academic_year": "2026/2027"})
    pages = [PageRead(URL, page_text, True)]
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read([robotics, FEES_STAGE[1]]), apply=False
    )
    assert not _stored_notes(report), report["fees"]


async def test_a_stored_proof_lends_no_period_when_the_reads_own_line_names_another(db_session, frozen_today):
    page_text = PAGE_TWO_LAYOUTS.replace(
        "Grade 0 | Plan 1 - € 8.950 | 1 x € 8.950 | 2 x € 4.575 | 1 x € 2.650 initial / 9 x € 750 monthly",
        "Grade 0 | Plan 1 - € 8.950 per semester",
    )
    school = await _school(db_session)
    await _proved_row(db_session, school, evidence=TEXT_PROOF)
    row = (await _rows(db_session, school))[0]
    row.amount, row.age_group = 8950, "Grade 0"
    await db_session.commit()
    read = fee(
        PriceCategory.TUITION,
        "Grade 0 | Plan 1 - € 8.950 per semester",
        amount=8950,
        academic_year="2026/2027",
        age_group="Grade 0",
    )
    pages = [PageRead(URL, page_text, True)]
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read([read, FEES_STAGE[1]]), apply=False
    )
    tuition = next(f for f in report["fees"] if f["amount"] == 8950)
    assert tuition["period"] != "yearly", tuition


async def test_a_stored_proof_of_another_row_with_the_group_in_a_column_lends_no_period(db_session, frozen_today):
    page_text = "Клубове по интереси\nШах | 1–4 клас | 300 лв. годишно\nМесечни такси\nРоботика | 1–4 клас | 300 лв."
    school = await _school(db_session)
    await _proved_row(db_session, school, evidence="Шах | 1–4 клас | 300 лв. годишно")
    row = (await _rows(db_session, school))[0]
    row.category, row.amount, row.currency, row.age_group = PriceCategory.EXTRACURRICULAR, 300, "BGN", "1–4 клас"
    await db_session.commit()
    robotics = fee(PriceCategory.EXTRACURRICULAR, "Роботика | 1–4 клас | 300 лв.", amount=300, age_group="1–4 клас")
    robotics = robotics.model_copy(update={"currency": "BGN", "academic_year": "2026/2027"})
    pages = [PageRead(URL, page_text, True)]
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read([robotics, FEES_STAGE[1]]), apply=False
    )
    assert not _stored_notes(report), report["fees"]


# --- 596's gaps in the fees eval: ranges with a currency on each end, the title's period ---


@pytest.mark.parametrize(
    "line, low, high, period",
    [
        ("FOOD Licensed catering company € 1,290 – € 1,340 annual /", 1290, 1340, PricePeriod.YEARLY),
        ("€ 200 - € 550 per term,", 200, 550, PricePeriod.TERM),
        ("2,524 BGN – 2,620 BGN annual fee", 2524, 2620, PricePeriod.YEARLY),
    ],
)
def test_a_range_with_a_currency_on_each_end_publishes(line, low, high, period):
    result = check_fee(fee(PriceCategory.FOOD, line, amount_min=low, amount_max=high, period=period), line)
    assert published(result) and result.fee.period is period, result.checks


def test_two_priced_items_are_still_no_range():
    line = "Sweatshirt: € 25 - Polo T-shirt: € 14"
    result = check_fee(fee(PriceCategory.UNIFORMS, line, amount_min=14, amount_max=25), line)
    assert result.kept and not published(result)


PAGE_TITLED = "\n".join(
    [
        "Maple Bear Sofia School",
        "ANNUAL TUITION FEES 2026-2027",
        "REGISTRATION FEE: One-time fee for initial enrollment of € 800, paid upon enrollment.",
        "Grade 0 Plan 1 - € 8.950 1 x € 8.950 2 x € 4.575 1 x € 2.650 initial",
        "Plan 2 - € 9.150 9 x € 750 monthly",
        "Plan 3 - € 9.400",
    ]
)


@pytest.mark.parametrize(
    "evidence, amount, period, kept",
    [
        ("Grade 0 Plan 1 - € 8.950 1 x € 8.950 2 x € 4.575 1 x € 2.650 initial", 8950, PricePeriod.YEARLY, True),
        ("Plan 3 - € 9.400", 9400, PricePeriod.YEARLY, True),
        ("Plan 3 - € 9.400", 9400, PricePeriod.MONTHLY, False),  # the title names the year
        ("Grade 0 Plan 1 - € 8.950 1 x € 8.950 2 x € 4.575 1 x € 2.650 initial", 4575, PricePeriod.YEARLY, False),
        ("Plan 2 - € 9.150 9 x € 750 monthly", 9150, PricePeriod.YEARLY, False),  # its line says monthly
    ],
)
def test_a_page_titled_annual_tuition_names_the_period_of_its_tuition(evidence, amount, period, kept):
    result = check_fee(fee(PriceCategory.TUITION, evidence, amount=amount, period=period), PAGE_TITLED)
    assert published(result)
    assert result.fee.period is (period if kept else None), result.checks


def test_the_title_names_no_period_for_other_fees_or_below_a_price():
    extra = check_fee(
        fee(PriceCategory.MATERIALS, "Plan 3 - € 9.400", amount=9400, period=PricePeriod.YEARLY), PAGE_TITLED
    )
    assert extra.fee.period is None
    below = "Registration: € 800\n" + PAGE_TITLED
    tuition = check_fee(fee(PriceCategory.TUITION, "Plan 3 - € 9.400", amount=9400, period=PricePeriod.YEARLY), below)
    assert tuition.fee.period is None


def test_an_evidence_heading_naming_another_period_beats_the_title():
    page = PAGE_TITLED + "\nMonthly tuition plan\nGrade 1: € 900"
    read = fee(PriceCategory.TUITION, "Monthly tuition plan\nGrade 1: € 900", amount=900, period=PricePeriod.YEARLY)
    assert check_fee(read, page).fee.period is None


@pytest.mark.parametrize(
    "page, evidence, amount",
    [
        ("Tuition fees for the school year 2026/2027\nGrade 1 | € 650", "Grade 1 | € 650", 650),  # a bare "year"
        (PAGE_TITLED + "\nMonthly tuition - nursery\nNursery | € 900", "Nursery | € 900", 900),  # a second sheet
        ("ANNUAL TUITION FEES\nGrade 5 | € 4.575 x 2", "Grade 5 | € 4.575 x 2", 4575),  # an instalment
    ],
)
def test_the_title_lends_no_period_where_it_may_not_head_the_fee(page, evidence, amount):
    read = fee(PriceCategory.TUITION, evidence, amount=amount, period=PricePeriod.YEARLY)
    assert check_fee(read, page).fee.period is None


def _check(missed=(), wrong=()):
    return fee_check.FeeCheck(
        missed=[
            fee_check.MissedFee(
                category="food", amount=amount, amount_min=None, amount_max=None, currency="EUR",
                period="monthly", who=None, evidence="Храна: 90 евро месечно", page=URL,
            )
            for amount in missed
        ],
        wrong=[fee_check.WrongFee(fee=index, problem="period", evidence="…") for index in wrong],
    )  # fmt: skip


def _checker(result=None, error=None):
    seen = []

    async def check(school, pages, fees):
        seen.append(list(fees))
        if error:
            raise error
        return result

    check.seen = seen
    return check


async def test_a_fee_the_check_finds_is_read_again(db_session, pages, frozen_today):
    school = await _school(db_session)  # no stored rows: only the check knows about the food fee
    await db_session.commit()
    food = fee(
        PriceCategory.FOOD, "Храна: 90 евро месечно", amount=90, period=PricePeriod.MONTHLY, academic_year="2026/2027"
    )
    asked = []

    async def read(school, pages, missed):
        asked.append(list(missed))
        return [*FEES_STAGE, food] if missed else FEES_STAGE

    # 90 is printed and not read; 1234 is not on the page; 700 was read already.
    check = _checker(_check(missed=[90, 1234, 700], wrong=[0, 7]))
    report = await fees_agent.run_school(db_session, school, find=_find(pages), read=read, apply=True, check=check)
    assert asked == [[], [Decimal("90")]]
    assert check.seen == [FEES_STAGE]
    assert report["check"]["reread"] == ["90"]
    assert report["reread"] == {"missed": ["90"], "added": 1}
    assert [w["category"] for w in report["check"]["wrong"]] == ["tuition", None]  # 7: no such fee
    assert report["check"]["wrong"][0]["amounts"] == ["700"]
    rows = await _rows(db_session, school)
    assert sorted(float(r.amount) for r in rows) == [90.0, 200.0, 700.0]


async def test_a_clean_check_reads_once(db_session, pages, frozen_today):
    school = await _school(db_session)
    await db_session.commit()
    asked = []

    async def read(school, pages, missed):
        asked.append(list(missed))
        return FEES_STAGE

    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=read, check=_checker(_check())
    )
    assert asked == [[]] and "reread" not in report
    assert report["check"] == {"missed": [], "wrong": [], "reread": []}


@pytest.mark.parametrize("error", [SpendCapReached("cap"), RuntimeError("timed out")])
async def test_a_failed_check_leaves_the_read_as_it_was(db_session, pages, frozen_today, error):
    school = await _school(db_session)
    await db_session.commit()
    report = await fees_agent.run_school(
        db_session, school, find=_find(pages), read=_read(FEES_STAGE), apply=True, check=_checker(error=error)
    )
    assert "error" in report["check"] and "reread" not in report
    assert report["outcome"] == "written", report
    assert sorted(float(r.amount) for r in await _rows(db_session, school)) == [200.0, 700.0]


def test_the_check_names_each_read_fee_once():
    message = fee_check.check_message("Meridian", "=== PAGE x\nтекст", FEES_STAGE)
    assert message.endswith(
        "=== FEES THE READ GAVE\n"
        f"0. tuition | 700 EUR | period monthly | year 2026/2027 | page {URL}\n"
        f"1. registration | 200 EUR | period one_time | year 2026/2027 | page {URL}"
    )
    assert fee_check.check_message("Meridian", "", []).endswith("(no fees)")


def test_a_missed_range_rereads_both_ends():
    check = fee_check.FeeCheck(
        missed=[
            fee_check.MissedFee(
                category="activities", amount=None, amount_min=200, amount_max=550, currency="EUR",
                period=None, who=None, evidence="от 200 до 550 евро", page=URL,
            )
        ],
        wrong=[],
    )  # fmt: skip
    assert fee_check.amounts_to_reread(check, "Занимания: от 200 до 550 евро", []) == [Decimal("200"), Decimal("550")]


async def test_a_refused_checker_account_stops_the_run(db_session, pages, frozen_today):
    from app.services.provider_costs import ProviderCreditsExhausted

    school = await _school(db_session)
    await db_session.commit()
    check = _checker(error=ProviderCreditsExhausted("credit balance is too low"))
    with pytest.raises(ProviderCreditsExhausted):
        await fees_agent.run_school(db_session, school, find=_find(pages), read=_read(FEES_STAGE), check=check)
