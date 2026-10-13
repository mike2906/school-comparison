"""Payment options: one fee paid in parts is listed under that fee, not stored as fees of its own.

596 (Maple Bear) published 42 rows: per grade band a plan 1 total, a plan 2 total and its
"2 x € 4.575", a plan 3 total, its first payment and its monthly instalment. Parents saw
"Plan 1 / Plan 2 / Plan 3" and amounts with no period. The reader now gives one fee per band
with its payment options; code checks the options' numbers against the evidence.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Pricing, School
from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.scrapers.extraction.agent_fees import AgentFee, PaymentOption, check_fee
from app.scrapers.stages import fees_agent
from app.scrapers.stages.fees_agent import PageRead

URL = "https://school.example/fees.pdf"

PAGE_596 = """\
Annual Tuition Fees 2026-2027
Grade 0
Plan 1 € 8.950 1 x € 8.950
Plan 2 € 9.150 2 x € 4.575
Plan 3 € 9.400 1 x € 2.650 initial + 9 x € 750 monthly
Registration fee € 800
"""
EVIDENCE_596 = "\n".join(PAGE_596.splitlines()[1:5])


def tuition(**update) -> AgentFee:
    fee = AgentFee(
        category=PriceCategory.TUITION,
        amount=8950,
        currency="EUR",
        period=None,
        plan_name="Plan 1",
        academic_year="2026-2027",
        age_group="Grade 0",
        label="Plan 1",
        source_url=URL,
        evidence=EVIDENCE_596,
        payment_options=[
            PaymentOption(label="Plan 1", payments=1, instalment=8950),
            PaymentOption(label="Plan 2", total=9150, payments=2, instalment=4575),
            PaymentOption(label="Plan 3", total=9400, payments=9, instalment=750, first_payment=2650),
        ],
    )
    return fee.model_copy(update=update)


def test_options_the_evidence_prints_are_kept_and_one_payment_is_the_fee_itself():
    result = check_fee(tuition(), PAGE_596)
    assert result.kept and result.checks == []
    assert [(o.label, o.total, o.payments, o.instalment, o.first_payment) for o in result.fee.payment_options] == [
        ("Plan 2", 9150, 2, 4575, None),
        ("Plan 3", 9400, 9, 750, 2650),
    ]


def test_an_option_with_a_number_the_evidence_lacks_is_dropped():
    made_up = tuition(payment_options=[PaymentOption(label="Plan 4", payments=10, instalment=990)])
    result = check_fee(made_up, PAGE_596)
    assert result.kept and result.fee.payment_options == []
    assert result.checks == ["payment option dropped: 990 is not in the evidence"]


def test_a_count_written_only_in_words_is_cleared_and_a_printed_total_still_stands():
    line = "| Подготвителен | 4500 Евро | 4600 Евро |\n| Клас | Еднократно плащане | На два пъти |"
    fee = tuition(
        amount=4500, evidence=line, payment_options=[PaymentOption(label="На два пъти", total=4600, payments=2)]
    )
    result = check_fee(fee, line)
    assert [(o.total, o.payments) for o in result.fee.payment_options] == [(4600, None)]

    no_total = fee.model_copy(update={"payment_options": [PaymentOption(payments=2, instalment=4600)]})
    result = check_fee(no_total, line)
    assert result.fee.payment_options == []
    assert result.checks == ["payment option dropped: it states neither a total nor a count of instalments"]


def _school() -> School:
    return School(id=1, name_i18n={}, country_code="bg", school_type="private", education_level="primary")


def test_rows_carry_the_options_and_lose_the_payment_plan_name():
    page = SourcePage(id=-1, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=URL)
    rows, _ = fees_agent._build_rows(_school(), [(check_fee(tuition(), PAGE_596), page)])
    assert len(rows) == 1
    row = rows[0]
    assert float(row.amount) == 8950 and row.plan_name is None
    assert row.pricing_context["payment_options"] == [
        {"label": "Plan 2", "total": 9150.0, "payments": 2, "instalment": 4575.0},
        {"label": "Plan 3", "total": 9400.0, "payments": 9, "instalment": 750.0, "first_payment": 2650.0},
    ]


def test_a_name_that_is_not_a_payment_plan_stays():
    page = SourcePage(id=-1, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=URL)
    full_day = tuition(plan_name="Full day")
    rows, _ = fees_agent._build_rows(_school(), [(check_fee(full_day, PAGE_596), page)])
    assert rows[0].plan_name == "Full day"


async def _run_596(db_session, read_fee, extra=(), page_text=PAGE_596):
    school = School(
        name_i18n={"bg": "Частно училище"},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        website_url="https://school.example",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()
    page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url=URL,
        content_hash="old",
        page_category="pricing",
        is_valid=True,
        raw_markdown=page_text,
    )
    db_session.add(page)
    await db_session.flush()
    # What 596 published: each plan total and each instalment a row of its own.
    old = [(8950, PricePeriod.YEARLY), (9150, PricePeriod.YEARLY), (4575, None), (9400, PricePeriod.YEARLY)]
    old += [(2650, None), (750, PricePeriod.MONTHLY), *extra]
    for amount, period in old:
        db_session.add(
            Pricing(
                school_id=school.id,
                category=PriceCategory.TUITION,
                amount=amount,
                currency="EUR",
                period=period,
                plan_name="Plan 1",
                age_group="Grade 0",
                academic_year="2026-2027",
                source=PriceSource.SCRAPED_WEBSITE,
                source_url=URL,
                source_page_id=page.id,
                pricing_context={"confidence": 0.9},
            )
        )
    await db_session.commit()

    async def find(_school):
        return [PageRead(URL, page_text, True)]

    async def read(_school, _pages, _missed):
        return [read_fee]

    report = await fees_agent.run_school(db_session, school, find=find, read=read, apply=True)
    rows = (
        (
            await db_session.execute(
                select(Pricing).where(Pricing.school_id == school.id).options(selectinload(Pricing.source_page))
            )
        )
        .scalars()
        .all()
    )
    return report, rows


async def test_old_instalment_rows_give_way_to_one_fee_with_its_options(db_session, frozen_today):
    fee = tuition(period=PricePeriod.YEARLY, evidence="Annual Tuition Fees 2026-2027\n" + EVIDENCE_596)
    report, rows = await _run_596(db_session, fee)
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0, report
    assert [(float(r.amount), len(r.pricing_context["payment_options"])) for r in rows] == [(8950.0, 2)]


async def test_an_old_plan_row_the_read_missed_goes_once_its_group_has_options(db_session, frozen_today):
    # 596's re-read missed plan amounts, and rule 6 kept 25 old "Plan N" rows beside the fees.
    page = PAGE_596 + "Plan 4 € 9.990 3 x € 3.330\n"
    fee = tuition(period=PricePeriod.YEARLY, evidence="Annual Tuition Fees 2026-2027\n" + EVIDENCE_596)
    report, rows = await _run_596(db_session, fee, extra=[(9990, PricePeriod.YEARLY), (3330, None)], page_text=page)
    assert report["outcome"] == "written" and report["rows_old_kept"] == 0, report
    assert [float(r.amount) for r in rows] == [8950.0]


def test_an_options_line_does_not_vouch_for_the_fees_own_period():
    result = check_fee(tuition(period=PricePeriod.MONTHLY), PAGE_596)
    assert result.fee.period is None
    assert "period monthly cleared: the evidence does not name it" in result.checks


def test_a_deposit_named_on_an_options_line_does_not_hold_the_fee():
    page = "Annual tuition € 8.950\nPlan 3: 1 x € 2.650 капаро + 9 x € 750"
    fee = tuition(
        evidence=page,
        payment_options=[PaymentOption(label="Plan 3", payments=9, instalment=750, first_payment=2650)],
    )
    result = check_fee(fee, page)
    assert result.confidence == 0.9 and len(result.fee.payment_options) == 1, result.checks


def test_a_plan_name_with_words_after_it_is_dropped_too():
    assert fees_agent._PAYMENT_PLAN_NAME_RE.match("План 2 – на вноски")
    assert fees_agent._PAYMENT_PLAN_NAME_RE.match("Вариант Б")
    assert not fees_agent._PAYMENT_PLAN_NAME_RE.match("Plan Advanced")
