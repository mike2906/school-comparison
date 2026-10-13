"""R6 step 2: the fee agent's checks (``agent_fees.check_fee``) and its stage.

The check cases use page lines recorded in docs/accuracy/fresh_rescore (``run/fresh_check.log.txt``
and the ``quote`` fields of ``adj/*.json``). The first block is the 13 errors the agent made on
the fresh sample, each of which the plan says code must clear or withhold; the second block is
valid fees on the same pages, which must survive with their period. The model and the page
fetches are stubbed in the stage tests.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models import FieldSource, Pricing, School
from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.models.scrape_log import ScrapeType
from app.models.source_page import SourcePage
from app.scrapers.extraction.agent_fees import HELD_CONFIDENCE, PUBLISH_CONFIDENCE, AgentFee, check_fee, page_numbers
from app.scrapers.stages import fees_agent
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
    async def read(school, pages):
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
        await fees_agent.read_fees(school, [PageRead(URL, PAGE_STAGE, True)], Spend(0.0))
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
    report = await fees_agent.run_fees_agent(db_session, school_id=school.id)
    assert report["outcomes"] == {"error": 1}
    run = await db_session.get(PipelineRun, report["pipeline_run_id"])
    assert run.status.value != "running"


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
    report = await fees_agent.run_fees_agent(db_session, school_id=school.id)
    assert "chromium did not launch" in report["stopped"] and report["schools"] == 0
    run = await db_session.get(PipelineRun, report["pipeline_run_id"])
    assert run.status.value != "running"
