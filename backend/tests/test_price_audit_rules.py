"""Price-row rules for the mistakes the 2026-10-09 Sofia price audit found.

Each page is rebuilt from the audit's quotes of the stored page text: the row as stored
must trip its rule (or have its period filled in), and the neighbouring rows that are
right must not.
"""

import datetime
from decimal import Decimal

import pytest

from app.scrapers.price_evidence import (
    RULE_EXPIRED,
    RULE_LEVEL,
    RULE_PERIOD_MISSING,
    PriceRow,
    amount_spans,
    check_price_row,
    heading_period,
    label_is_another_institutions,
    normalize_text,
    page_is_another_institutions,
)

TODAY = datetime.date(2026, 10, 9)


def row(amount, category="TUITION", period=None, plan_name=None, currency="EUR", age_group=None):
    return PriceRow(
        category=category,
        amount=amount,
        amount_min=None,
        amount_max=None,
        period=period,
        plan_name=plan_name,
        notes=None,
        currency=currency,
        age_group=age_group,
    )


def findings(price_row, page, family, **scope):
    return {(f.rule, f.period) for f in check_price_row(price_row, page, family, today=TODAY, **scope)}


def heading(page, amount):
    text = normalize_text(page)
    return heading_period(text, amount_spans(text, amount)[0])


# ---------------------------------------------------------------------------
# Periods given only in a table or list heading (~110 yearly and 13 monthly rows)
# ---------------------------------------------------------------------------

# 517: 8th grade €7,000 came out yearly, 5th grade €7,810 from the same table NULL.
GODISHNA_TAKSA = """ТАКСИ ЗА УЧЕБНАТА 2026/2027 ГОДИНА
ГОДИШНА ТАКСА
| Клас | Такса |
|---|---|
| 5 клас | 7810 € |
| 8 клас | 7000 € |
"""


def test_517_period_from_the_tables_heading_is_filled():
    assert findings(row(Decimal("7810"), plan_name="5 клас"), GODISHNA_TAKSA, "school") == {
        (RULE_PERIOD_MISSING, "YEARLY")
    }


@pytest.mark.parametrize(
    "page,amount,period",
    [
        ("ANNUAL TUITION FEES\nGrade 1\n€ 9 500\nGrade 2\n€ 9 800\n", Decimal("9800"), "YEARLY"),
        ("Total tuition fees (year)\n| Grade | EUR |\n| 1 | 9500 |\n| 2 | 9800 |\n", Decimal("9800"), "YEARLY"),
        # 542: "Месечни такси … Полудневен престой – 800 лв. / 750 лв."
        (
            "Месечни такси в Частна Детска Ясла\nЦелодневен престой – 900 лв.\nПолудневен престой – 800 лв. / 750 лв.\n",
            Decimal("800"),
            "MONTHLY",
        ),
        # A header row with a period per column: only the amount's own column counts.
        ("| Клас | Месечна такса | Годишна такса |\n|---|---|---|\n| 1 клас | 700 € | 7000 € |\n", Decimal("7000"), "YEARLY"),
        ("| Клас | Годишна такса | Храна |\n| 1 клас | 7000 € | 150 € |\n", Decimal("150"), None),
        # Another fee's label, not a heading: its own bare price follows it.
        ("Депозит (еднократно):\n500 €\n5 клас:\n7810 €\n", Decimal("7810"), None),
        # "- ежемесечно заплащане" describes the price above it.
        ("ТАКСИ\n€ 530\n- целодневно гледане, ежемесечно заплащане\n€ 350\n- половин ден\n", Decimal("350"), None),
        # A heading naming two periods, or prose, decides nothing.
        ("Месечни и годишни такси\nГрупа 1\n€ 600\n", Decimal("600"), None),
        (
            "Таксата за обучение се заплаща месечно, като родителите могат да изберат и друг удобен за тях начин.\n€ 600\n",
            Decimal("600"),
            None,
        ),
        # A markdown heading with no period ends the search.
        ("Годишна такса\n# Детска градина\nГрупа 1\n€ 600\n", Decimal("600"), None),
    ],
)
def test_heading_period(page, amount, period):
    assert heading(page, amount) == period


def test_a_period_stated_beside_the_amount_outranks_the_heading():
    page = "ГОДИШНА ТАКСА\nОбяд\n€ 150 на месец\n"
    assert findings(row(Decimal("150"), category="FOOD"), page, "school") == {(RULE_PERIOD_MISSING, "MONTHLY")}


def test_330_paid_by_the_previous_month_is_monthly():
    page = "Месечна такса за детската градина\n899.68 лв. / 460 €, платима до 15 число на предидущия месец\n"
    stored = row(Decimal("899.68"), currency="BGN")
    assert findings(stored, page, "kindergarten") == {(RULE_PERIOD_MISSING, "MONTHLY")}


def test_a_monthly_tier_is_not_given_its_neighbours_period():
    """The Beehive's "Full-day 850 €" stands beside "Half-day 500 € /per month": the
    neighbour's period is not read across. The floor gate withholds the bare €850."""
    page = "Fees\nHalf-day 500 € /per month\nFull-day 850 €\n"
    assert heading(page, Decimal("850")) is None


# ---------------------------------------------------------------------------
# A sibling school's fees on a kindergarten, and the other way round
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "label,shares_site,foreign",
    [
        ("Учебни материали / ПК, 1-12 клас", False, True),  # 634
        ("Обяд / Ученици в Лозен", False, True),  # 634
        ("IB програма", False, True),  # 526
        ("Занималня", False, True),  # 587
        ("Подготвителна група", False, False),
        ("подготовка за 1 клас", False, False),
        ("Целодневна група 3-6 години", True, False),
        # A school's name counts only on a site the kindergarten shares with a school.
        ("Регистрационна такса за прием в ЧОУ „Светлина“", True, True),  # 510
        ("Такса кандидатстване за училище", True, True),  # 634's €150
        ("Лятно училище по английски", False, False),
        ("Подготовка за училище", True, False),
    ],
)
def test_kindergarten_fee_labels(label, shares_site, foreign):
    assert label_is_another_institutions(label, "kindergarten", set(), shares_site=shares_site) is foreign


def test_510_kindergarten_does_not_read_the_schools_page():
    school_page = "https://svetlina.net/school/"
    assert page_is_another_institutions(school_page, set(), "kindergarten", shares_site=True)
    assert not page_is_another_institutions(school_page, set(), "kindergarten", shares_site=False)
    assert not page_is_another_institutions("https://svetlina.net/kindergarten/taksi", set(), "kindergarten", True)
    assert not page_is_another_institutions("https://svetlina.net/preschool/", set(), "kindergarten", True)


# 522 (school) and 587 (kindergarten) share one Druzhba page.
DRUZHBA = """Такси за учебната 2026/2027 година
Детска градина
Първа и Втора група от възраст: 3г. – 4г. 7500 за година
Трета и Четвърта група от възраст: 5г. – 6г. 7865 за година
Училище
| Клас | Годишна такса |
|---|---|
| 1 - 12 клас | 7150 |
"""


def test_522_school_row_whose_amount_is_the_kindergartens():
    stored = row(Decimal("7865"), period="YEARLY", plan_name="1 - 12 клас")
    assert (RULE_LEVEL, None) in findings(stored, DRUZHBA, "school", shares_site=True)
    # Without a kindergarten on the site there is nobody else the fee could be for.
    assert (RULE_LEVEL, None) not in findings(stored, DRUZHBA, "school")
    own = row(Decimal("7150"), period="YEARLY", plan_name="1 - 12 клас")
    assert (RULE_LEVEL, None) not in findings(own, DRUZHBA, "school", shares_site=True)


def test_587_kindergarten_keeps_its_own_row_on_the_shared_page():
    own = row(Decimal("7865"), period="YEARLY", plan_name="Трета и Четвърта група")
    assert (RULE_LEVEL, None) not in findings(own, DRUZHBA, "kindergarten", shares_site=True)
    school = row(Decimal("7150"), period="YEARLY", plan_name="Годишна такса")
    assert (RULE_LEVEL, None) in findings(school, DRUZHBA, "kindergarten", shares_site=True)


# ---------------------------------------------------------------------------
# Offers whose last day has passed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "page,amount,category,expired",
    [
        # 581: a language trip abroad, priced for last spring.
        ("Пролетен лагер в Малта\nЦени: 1 050 EUR до 7 февруари 2026 г.\n", Decimal("1050"), "CAMP", True),
        # 171: early-enrolment tiers.
        ("Годишна такса 2026/2027\n9 500 € при договор, сключен до 19.12.2025 г. (първите 7 договора)\n", Decimal("9500"), "TUITION", True),
        ("Годишна такса 2026/2027\n9 800 € при договор до 20.02.2026 г.\n", Decimal("9800"), "TUITION", True),
        # The price that applies after the offer is the current one.
        ("Годишна такса 2026/2027\nслед 20.02.2026 г. – 10 200 €\n", Decimal("10200"), "TUITION", False),
        # An offer still open.
        ("Годишна такса 2027/2028\n9 900 € при договор до 19.12.2026 г.\n", Decimal("9900"), "TUITION", False),
        # A due date and the end of enrolment are not the end of the fee.
        ("Годишна такса 8000 €, платима до 15.09.2026 г.\n", Decimal("8000"), "TUITION", False),
        ("Годишна такса 8000 €. Записване до 15.09.2026 г.\n", Decimal("8000"), "TUITION", False),
    ],
)
def test_expired_offers(page, amount, category, expired):
    period = None if category == "CAMP" else "YEARLY"
    stored = row(amount, category=category, period=period)
    assert any(rule == RULE_EXPIRED for rule, _ in findings(stored, page, "school")) is expired
