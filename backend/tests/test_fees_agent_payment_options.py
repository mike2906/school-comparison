"""Payment options: one fee paid in parts is listed under that fee, not stored as fees of its own.

596 (Maple Bear) published 42 rows: per grade band a plan 1 total, a plan 2 total and its
"2 x € 4.575", a plan 3 total, its first payment and its monthly instalment. Parents saw
"Plan 1 / Plan 2 / Plan 3" and amounts with no period. The reader now gives one fee per band
with its payment options; code checks the options' numbers against the evidence.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import Pricing, School
from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.scrapers.extraction.agent_fees import AgentFee, PaymentOption, _count_is_printed, check_fee
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


def test_rows_keep_the_english_for_the_site_words_they_keep():
    """625 (dgsofia.com): "половин ден" showed in Bulgarian on the English page."""
    page = SourcePage(id=-1, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=URL)
    half_day = tuition(
        plan_name="половин ден", plan_name_en="Half day", age_group_en="Preparatory year (grade 0)", payment_options=[]
    )
    plan = tuition(plan_name_en="Plan 1", age_group=None, age_group_en="Grade 0")
    rows, _ = fees_agent._build_rows(
        _school(), [(check_fee(half_day, PAGE_596), page), (check_fee(plan, PAGE_596), page)]
    )
    by_name = {row.plan_name: row.pricing_context for row in rows}
    assert by_name["половин ден"]["plan_name_en"] == "Half day"
    assert by_name["половин ден"]["age_group_en"] == "Preparatory year (grade 0)"
    # The payment plan's name is dropped, and the English for it and for no age group with it.
    assert "plan_name_en" not in by_name[None] and "age_group_en" not in by_name[None]


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


def test_a_count_must_be_written_as_a_count_of_payments():
    line = "Grade 2 | Plan 2 | € 9.150 | € 4.575 each"
    fee = tuition(amount=9150, evidence=line, payment_options=[PaymentOption(payments=2, instalment=4575)])
    result = check_fee(fee, line)
    assert result.fee.payment_options == []  # the 2 is the grade's and the plan's, not a count


def test_an_options_cell_does_not_vouch_for_the_fees_period():
    line = "| Grade 0 | € 8.950 | 1 x € 2.650 + 9 x € 750 monthly |"
    fee = tuition(
        period=PricePeriod.MONTHLY,
        evidence=line,
        payment_options=[PaymentOption(payments=9, instalment=750, first_payment=2650)],
    )
    result = check_fee(fee, line)
    assert result.fee.period is None and len(result.fee.payment_options) == 1


@pytest.mark.parametrize(
    ("payments", "text", "printed"),
    [
        (10, "10 месечни вноски по 950 €", True),
        (3, "3 equal instalments of € 3.100", True),
        (2, "€ 4.575 x 2", True),
        (2, "2 x € 4.575", True),
        (2, "Grade 2 | Plan 2 | € 4.575", False),
        (2, "12 x € 500", False),
    ],
)
def test_a_count_of_payments_as_sites_write_it(payments, text, printed):
    assert _count_is_printed(payments, text) is printed


def test_a_name_that_starts_like_a_plan_but_is_not_one_stays():
    assert not fees_agent._PAYMENT_PLAN_NAME_RE.match("Вариант с обяд")
    assert not fees_agent._PAYMENT_PLAN_NAME_RE.match("Опция с транспорт")
    assert fees_agent._PAYMENT_PLAN_NAME_RE.match("Plan 3")


def test_the_report_shows_each_fees_group_and_payment_options():
    report = fees_agent._fee_report(check_fee(tuition(), PAGE_596), None)
    assert report["age_group"] == "Grade 0"
    assert [option["label"] for option in report["payment_options"]] == ["Plan 2", "Plan 3"]


# 596's secondary fee PDF as pdf_text reads it: the grade label sits on the plan 2 line.
PAGE_596_SECONDARY = """\
ANNUAL TUITION FEES 2026-2027 · SECONDARY SCHOOL
REGISTRATION & APPLICATION FEE: A one-time application fee of € 200 is payable
Grade Payment Plan Plan 1 Plan 2 Plan 3
Plan 1 – € 9,500
1 x € 2,950 initial
Grade 8 – 9 Plan 2 – € 9,700 1 x € 9,500 2 x € 4,850
9 x € 780 monthly
Plan 3 – € 9,950
"""


def test_a_row_label_the_pdf_put_on_another_line_may_be_quoted_with_the_row():
    evidence = (
        "ANNUAL TUITION FEES 2026-2027 · SECONDARY SCHOOL\nGrade Payment Plan Plan 1 Plan 2 Plan 3\n"
        "Grade 8 – 9 Plan 1 – € 9,500\n1 x € 2,950 initial\nPlan 2 – € 9,700 1 x € 9,500 2 x € 4,850\n"
        "9 x € 780 monthly\nPlan 3 – € 9,950"
    )
    fee = tuition(
        amount=9500,
        age_group="Grade 8 – 9",
        period=PricePeriod.YEARLY,
        evidence=evidence,
        payment_options=[
            PaymentOption(label="Plan 2", total=9700, payments=2, instalment=4850),
            PaymentOption(label="Plan 3", total=9950, payments=9, instalment=780, first_payment=2950),
        ],
    )
    result = check_fee(fee, PAGE_596_SECONDARY)
    assert result.kept and len(result.fee.payment_options) == 2, result.checks


def test_a_line_made_of_words_not_on_the_page_is_still_dropped():
    fee = tuition(amount=9500, evidence="Grade 11 – 12 Plan 1 – € 9,500")
    assert not check_fee(fee, PAGE_596_SECONDARY).kept


def test_one_time_in_english_names_the_period_of_a_registration_fee():
    line = "ANNUAL TUITION FEES 2026-2027\nOne-time fee for initial enrollment of € 800"
    fee = tuition(category=PriceCategory.REGISTRATION, amount=800, period=PricePeriod.ONE_TIME, evidence=line)
    fee = fee.model_copy(update={"payment_options": []})
    assert check_fee(fee, line).fee.period is PricePeriod.ONE_TIME


def test_a_label_quoted_with_a_row_it_does_not_sit_beside_is_dropped():
    # "Grade 8 – 9" is on the plan 2 line; € 2,950 is the plan 3 first payment two lines up.
    fee = tuition(amount=2950, evidence="Grade 8 – 9 € 2,950")
    assert not check_fee(fee, PAGE_596_SECONDARY).kept


def test_one_time_in_english_does_not_beat_a_recurring_word_on_the_same_line():
    line = "Annual re-enrolment fee € 300, payable at one time"
    fee = tuition(category=PriceCategory.REGISTRATION, amount=300, period=PricePeriod.ONE_TIME, evidence=line)
    fee = fee.model_copy(update={"payment_options": []})
    assert check_fee(fee, line).fee.period is not PricePeriod.ONE_TIME


@pytest.mark.parametrize(
    "evidence",
    [
        "9 x € 780 monthly Plan 1 – € 9,500",  # another line's period word joined to the row
        "Grade 8 – 9 Plan 2 – € 9,700 Plan 1 – € 9,500",  # a whole row joined to another row
    ],
)
def test_a_label_holds_no_price_or_period_of_its_own(evidence):
    fee = tuition(amount=9500, period=PricePeriod.MONTHLY, evidence=evidence)
    assert not check_fee(fee, PAGE_596_SECONDARY).kept


@pytest.mark.parametrize(
    ("line", "one_time"),
    [
        ("One–time fee for initial enrollment of € 800", True),
        ("Re-enrolment fee € 800 per term, one-time payment", False),
    ],
)
def test_one_time_in_english_with_a_dash_and_beside_a_term(line, one_time):
    page = f"ANNUAL TUITION FEES 2026-2027\n{line}"
    fee = tuition(category=PriceCategory.REGISTRATION, amount=800, period=PricePeriod.ONE_TIME, evidence=page)
    fee = fee.model_copy(update={"payment_options": []})
    assert (check_fee(fee, page).fee.period is PricePeriod.ONE_TIME) is one_time


def test_a_label_may_name_the_fee_but_not_carry_a_bare_price():
    page = "Годишна такса\nКлас 1 – 4\n9 500 лв.\nPlan 2 – 9,700 1 x 9,500\nPlan 1 – 9,500"
    named = tuition(amount=9500, period=PricePeriod.YEARLY, evidence="Годишна такса 9 500 лв.")
    assert check_fee(named, page).kept
    borrowed = tuition(amount=9500, evidence="Plan 2 – 9,700 Plan 1 – 9,500")
    assert not check_fee(borrowed, page).kept


@pytest.mark.parametrize(
    ("printed", "code"), [("евро", "EUR"), ("Euro", "EUR"), ("€", "EUR"), ("лв.", "BGN"), ("eur", "EUR")]
)
def test_a_currency_word_is_stored_as_its_code(printed, code):
    # 158: the reader returned "евро" and every fee failed the publish gate's code check.
    fee = tuition(amount=590, evidence="590 евро").model_dump()
    assert AgentFee.model_validate({**fee, "currency": printed}).currency == code


def test_english_that_changes_a_number_is_not_kept():
    page = SourcePage(id=-1, school_id=1, scrape_type=ScrapeType.WEBSITE, source_url=URL)
    prepaid = tuition(plan_name="Еднократно плащане за 6 месеца", plan_name_en="Paid for 12 months at once", payment_options=[])
    rows, _ = fees_agent._build_rows(_school(), [(check_fee(prepaid, PAGE_596), page)])
    assert rows[0].plan_name == "Еднократно плащане за 6 месеца"
    assert "plan_name_en" not in rows[0].pricing_context
    assert fees_agent._english_for("за 6 месеца", "Paid for 6 months at once") == "Paid for 6 months at once"
