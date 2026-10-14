"""Price-row rules for the mistakes the 2026-10-09 Sofia price audit found.

Each page is rebuilt from the audit's quotes of the stored page text: the row as stored
must trip its rule (or have its period filled in), and the neighbouring rows that are
right must not.
"""

import datetime
from decimal import Decimal

import pytest

from app.scrapers.extraction import price_lines, text_values
from app.scrapers.price_evidence import (
    RULE_EXPIRED,
    RULE_LEVEL,
    RULE_PERIOD_MISSING,
    PriceRow,
    amount_spans,
    check_price_row,
    fee_number_starts,
    heading_period,
    label_is_another_institutions,
    normalize_text,
    offer_end,
    page_is_another_institutions,
    period_families,
    replacement_regressions,
    stated_period,
    states_another_unit,
)

TODAY = datetime.date(2026, 10, 9)

# Extraction helpers read the clock too; pin it so year-relative cases do not expire.
pytestmark = pytest.mark.usefixtures("frozen_today")


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
        # Another section's heading ends the search: a markdown or bold line, or a line
        # over fee lines that carry their own words.
        ("Годишна такса\n# Детска градина\nГрупа 1\n€ 600\n", Decimal("600"), None),
        (
            "Годишна такса за обучение\n1-4 клас | 6000 лв.\n5-7 клас | 7000 лв.\n"
            "Допълнителни услуги\nТранспорт | 80 лв.\nХрана | 150 лв.\n",
            Decimal("150"),
            None,
        ),
        ("**Месечна такса**\nЦелодневна група 900 лв.\n**Други такси**\nУниформа 120 лв.\n", Decimal("120"), None),
        ("**Месечна такса**\nЦелодневна група 900 лв.\nПолудневна група 600 лв.\n", Decimal("600"), "MONTHLY"),
        # A fee laid out unlike the list under the heading is not in that list.
        ("Годишни такси\n1 клас | 7000 €\nХрана\n150 €\n", Decimal("150"), None),
    ],
)
def test_heading_period(page, amount, period):
    assert heading(page, amount) == period


def test_a_period_stated_beside_the_amount_outranks_the_heading():
    page = "ГОДИШНА ТАКСА\nОбяд\n€ 150 на месец\n"
    assert findings(row(Decimal("150"), category="FOOD"), page, "school") == {(RULE_PERIOD_MISSING, "MONTHLY")}


def test_330_paid_by_the_previous_month_is_monthly():
    """The words after the euro twin are the lev figure's too."""
    page = "Б. ВТОРИ ВАРИАНТ 8:00 – 15.00 часа\n899.68 лв. / 460 € / платима до 15 число на предидущия месец\n"
    for amount, currency in ((Decimal("899.68"), "BGN"), (Decimal("460"), "EUR")):
        stored = row(amount, currency=currency)
        assert findings(stored, page, "kindergarten") == {(RULE_PERIOD_MISSING, "MONTHLY")}
    # The twin's clause only: "а при годишно плащане" is another fee's.
    page = "Такса 900 лв. / 460 €, а при годишно плащане 9000 лв.\n"
    assert findings(row(Decimal("900"), currency="BGN"), page, "kindergarten") == set()
    # Two prices in one currency are two fees: the second's words are not the first's.
    assert stated_period(normalize_text("650 € / 6 792 € годишно"), (0, 3)) == set()


def test_517_a_one_time_word_under_a_yearly_fee_gives_way_to_the_heading():
    """517's 5th grade: "Еднократно плащане: До 09.07.2026 г." under the amount."""
    page = (
        "ГОДИШНА ТАКСА | НА ДВЕ ВНОСКИ | НА ОСЕМ ВНОСКИ\n"
        "7810 евро | 2 х 4022 евро | 8 х 1045 евро\n"
        "Еднократно плащане: До 09.07.2026 г.\n"
    )
    assert (RULE_PERIOD_MISSING, "YEARLY") in findings(row(Decimal("7810"), plan_name="ГОДИШНА ТАКСА"), page, "school")


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
        ("School readiness program", True, False),
        ("Целодневна група (обяд в училищния стол)", True, False),
        ("Annual fee per pupil", False, False),
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
    assert page_is_another_institutions("https://school.brand.bg/fees", set(), "kindergarten", True)
    assert not page_is_another_institutions("https://britanica-parkschool.bg/fees", set(), "kindergarten", True)


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
        ("Цена: 8000 евро. Записване до 15.09.2026 г.\n", Decimal("8000"), "TUITION", False),
        ("Годишна такса 6000 €. Отстъпка 5% при записване до 31.05.2026\n", Decimal("6000"), "TUITION", False),
        ("Отстъпка 10% за записани до 31.03.2026 г.\nГодишна такса 6000 €\n", Decimal("6000"), "TUITION", False),
        ("Цени за договори, сключени до 31.03.2026\n5500 €\n", Decimal("5500"), "TUITION", True),
        ("Early bird price 7500 EUR, offer valid from 1 January 2026 until 30 May 2026\n", Decimal("7500"), "TUITION", True),
        # An early-bird discount noted beside the regular price leaves the price alone.
        ("Годишна такса: 6 000 €\nРанно записване до 31.03.2026 г. - 10% отстъпка\n", Decimal("6000"), "TUITION", False),
        ("Годишна такса 6000 €. При записване до 31.05.2026 г. отстъпка 10%.\n", Decimal("6000"), "TUITION", False),
        # 598: the price for children enrolled before a passed date; the one after stays.
        (
            "Цени за целодневно обучение:\n1240 лв. (634,03 euro)/месец за деца записани до 01.10.2025г.\n"
            "1380 лв.(705,61 euro)/месец за деца записани след 01.10.2025г.\n",
            Decimal("634.03"),
            "TUITION",
            True,
        ),
        (
            "Цени за целодневно обучение:\n1240 лв. (634,03 euro)/месец за деца записани до 01.10.2025г.\n"
            "1380 лв.(705,61 euro)/месец за деца записани след 01.10.2025г.\n",
            Decimal("705.61"),
            "TUITION",
            False,
        ),
    ],
)
def test_expired_offers(page, amount, category, expired):
    period = None if category == "CAMP" else "YEARLY"
    stored = row(amount, category=category, period=period)
    assert any(rule == RULE_EXPIRED for rule, _ in findings(stored, page, "school")) is expired


def test_517_extraction_reads_the_heading_when_the_word_beside_the_amount_cannot_fit():
    from app.schemas.extraction import ExtractedPrice
    from app.scrapers.extraction.fee_lines import _filter_model_prices

    page = (
        "--- SOURCE: https://school.test/fees ---\n"
        "ГОДИШНА ТАКСА | НА ДВЕ ВНОСКИ | НА ОСЕМ ВНОСКИ\n"
        "7810 евро | 2 х 4022 евро | 8 х 1045 евро\n"
        "Еднократно плащане: До 09.07.2026 г.\n"
    )
    price = ExtractedPrice(category="tuition", amount=7810, currency="EUR", plan_name="ГОДИШНА ТАКСА", confidence=0.9)
    assert [row.period for row in _filter_model_prices([price], page)] == ["yearly"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 564: a lev amount with a decimal comma stays one amount.
        ("7774,42 лв. - 2 вноски", ["7774,42 лв. - 2 вноски"]),
        ("827€ / 1617,47 лв. - 10 вноски", ["827€ / 1617,47 лв. - 10 вноски"]),
        # A comma between words still separates values.
        ("Английски, немски, френски", ["Английски", "немски", "френски"]),
        ("2 вноски,10 вноски", ["2 вноски", "10 вноски"]),
    ],
)
def test_text_list_keeps_decimal_commas(raw, expected):
    assert text_values._normalize_text_list([raw]) == expected


# ---------------------------------------------------------------------------
# Amounts, currencies and dates as written on Bulgarian fee pages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.234,56 лв.", (1234.56, "BGN")),  # dot thousands, decimal comma
        ("1,234.56 EUR", (1234.56, "EUR")),  # comma thousands, decimal point
        ("6.200 лв.", (6200.0, "BGN")),  # a dot before three digits groups thousands
        ("12.50 евро", (12.5, "EUR")),  # before two digits it is a decimal point
        ("6,200 EUR", (6200.0, "EUR")),
        ("181,44 лв.", (181.44, "BGN")),
        ("1.234.567 лв", (1234567.0, "BGN")),
        ("1,234,567 €", (1234567.0, "EUR")),
        ("6 200 лв.", (6200.0, "BGN")),
        ("лв 450", (450.0, "BGN")),
        ("BGN 1 200", (1200.0, "BGN")),
        ("500 лева", (500.0, "BGN")),
        ("€ 7 500", (7500.0, "EUR")),
        ("300 USD", (None, None)),
    ],
)
def test_price_line_amount_and_currency(text, expected):
    assert price_lines._extract_price_amount_currency(text) == expected


@pytest.mark.parametrize(
    "name,month",
    [
        ("януари", 1), ("февруари", 2), ("март", 3), ("април", 4), ("май", 5), ("юни", 6),
        ("юли", 7), ("август", 8), ("септември", 9), ("октомври", 10), ("ноември", 11), ("декември", 12),
        ("january", 1), ("february", 2), ("march", 3), ("april", 4), ("may", 5), ("june", 6),
        ("july", 7), ("august", 8), ("september", 9), ("october", 10), ("november", 11), ("december", 12),
    ],
)  # fmt: skip
def test_offer_deadline_reads_every_month_name(name, month):
    if name.isascii():
        context = f"Early bird price, offer valid until 15 {name} 2026\n5500 EUR"
    else:
        context = f"Цени за договори, сключени до 15 {name} 2026\n5500 €"
    assert offer_end(context) == datetime.date(2026, month, 15)


def test_years_in_a_fee_table_are_not_read_as_bare_prices():
    text = "| Клас | Такса |\n|---|---|\n| 2025 | 2026 |\n| 1-4 | 7500 |\n"
    assert [text[start:start + 4] for start in fee_number_starts(text)] == ["7500"]


# --- 2026-10-12 refresh (171, 533, 564, 610) ---------------------------------------------


def _heading(page, amount):
    text = normalize_text(page)
    return heading_period(text, amount_spans(text, amount)[-1])


def test_610_a_flattened_plan_header_is_not_the_fees_heading():
    page = (
        "Програма\nГрупа/Клас\nПлащане на 1 вноска\nПлащане на 2 вноски\nПлащане на 10 месечни вноски\n"
        "Подготвителен клас\nПУК 1\n7 400\n2 х 3 900 = 7 800\n"
    )
    assert _heading(page, Decimal("7400")) is None


def test_610_one_payment_is_yearly():
    assert period_families("плащане на 1 вноска") == {"YEARLY"}
    assert period_families("плащане на 10 месечни вноски") == {"MONTHLY"}
    page = "Програма | Плащане на 1 вноска | Плащане на 2 вноски\nГимназия | 7 400 | 2 х 3 900 = 7 800\n"
    findings = check_price_row(row(7400), page, "school", today=TODAY)
    assert [(f.rule, f.period) for f in findings] == [(RULE_PERIOD_MISSING, "YEARLY")]


def test_564_one_off_payment_under_a_yearly_heading_is_yearly():
    page = (
        "Годишна такса обучение и финансови условия\nза ПГ - 4 .клас за 2026-2027 учебна година:\n"
        "ЕДНОКРАТНО ПЛАЩАНЕ\n-\nПлащане до 31.03.2026 г. - 7580€\n"
    )
    findings = check_price_row(row(7580), page, "school", today=TODAY)
    assert [(f.rule, f.period) for f in findings] == [(RULE_PERIOD_MISSING, "YEARLY")]


def test_171_a_tier_whose_enrolment_deadline_passed_two_lines_up_has_expired():
    page = (
        "6300 €\nПри записване в срок до 23.01.2026 г.\n"
        "Заплащане на 2 вноски (I вноска до 23.01.2026 г.; II вноска до 20.11.2026 г.)\n6600 €\n"
        "При записване след 20.02.2026 г.\nПри 2 вноски (I вноска при сключване на\n"
        "договор; II вноска до 20.11.2026 г.)\n7 269 €\n"
    )
    assert [f.rule for f in check_price_row(row(6600), page, "school", today=TODAY)] == [RULE_EXPIRED]
    assert check_price_row(row(7269), page, "school", today=TODAY) == []


def test_594_a_date_in_another_columns_header_does_not_end_this_fee():
    page = (
        "GRADE | Admission Fee | Educational Resources Fee | "
        "TUITION FEE RETURNING STUDENTS with signed contracts before 30.04.2025 | TUITION FEE NEW STUDENTS\n"
        "Grade 1 | 500 | 565 | 14 750 | 15 350\n"
    )
    assert check_price_row(row(565, category="MATERIALS", period="YEARLY"), page, "school", today=TODAY) == []
    assert RULE_EXPIRED in [f.rule for f in check_price_row(row(14750), page, "school", today=TODAY)]


def test_533_a_unit_after_the_amount_that_is_no_period():
    page = "Зелени училища (приблизително) | ≈ 950 EUR / за всички\nТранспорт | ≈ 200 EUR / месец\n"
    assert states_another_unit(page, 950)
    assert not states_another_unit(page, 200)


def test_rule_6_accepts_an_amount_listed_under_a_fee():
    page = "Годишна такса 7580 €\nI вноска 3975 €\n"
    published = [(row(7580, period="YEARLY"), page), (row(3975, period="YEARLY"), page)]
    proposed = [(row(7580, period="YEARLY"), page)]
    assert replacement_regressions(published, proposed, "school")
    assert replacement_regressions(published, proposed, "school", accounted_for=[("tuition", 3975.0)]) == []


def test_rule_6_a_listed_amount_accounts_only_for_its_own_category():
    """A €500 instalment does not let a €500 registration fee go, nor vouch for a period."""
    page = "Записване 500 €\nТакса 5000 € годишно\nI вноска 500 €\n"
    published = [(row(5000, period="YEARLY"), page), (row(500, category="REGISTRATION", period="ONE_TIME"), page)]
    proposed = [(row(5000, period="YEARLY"), page)]
    assert replacement_regressions(published, proposed, "school", accounted_for=[("tuition", 500.0)]) == [
        "drops REGISTRATION 500.00, which the page still shows"
    ]
    # The fee that remains at a listed amount still keeps its period.
    page = "Такса 500 €\nI вноска 500 €\n"
    published = [(row(500, period="YEARLY"), page), (row(500, period="YEARLY", plan_name="плащане"), page)]
    proposed = [(row(500), page)]
    assert replacement_regressions(published, proposed, "school", accounted_for=[("tuition", 500.0)]) == [
        "loses the YEARLY period of TUITION 500.00"
    ]


def test_a_course_length_is_not_an_age():
    """581's "IGCSE 2 years in 1" is the gymnasium's, not a nursery group's."""
    assert not label_is_another_institutions("IGCSE 2 years in 1", "school", set(range(8, 13)))
    assert not label_is_another_institutions("IGCSE 2 години за 1 (5 предмета)", "school", set(range(8, 13)))
    # Also as the line an amount stands on, which Stage 6 reads as the amount's heading.
    assert not label_is_another_institutions(
        "IGCSE 2 years in 1\nTextbook and/or study materials price for IGCSE 2 years in 1 – €738",
        "school",
        set(range(8, 13)),
    )
    assert label_is_another_institutions("Група 2 години", "school", set(range(8, 13)))
    for nursery in ("деца на 3 години за 10 месеца", "2 години за 1 месец", "група 3 years in 2026", "2 г. за 1 месец", "2 години за 1 мес.", "3 years in 1 term", "деца 2 години за 1-2 месеца", "2 години за 1 – 2 месеца"):
        assert label_is_another_institutions(nursery, "school", set(range(8, 13))), nursery


@pytest.mark.parametrize(
    ("text", "families"),
    [
        # 590: the materials fee's note is its only period wording.
        ("Такса учебни материали | 120 € / 235 лв.\nЗаплаща се в началото на всяка учебна година", {"YEARLY"}),
        # Another period word on the line wins (510's wording, a monthly fee reviewed each year).
        ("Учебни материали: 250 € – заплаща се еднократно в началото на всяка учебна година", {"ONE_TIME"}),
        ("Monthly fee 800 EUR (fees are reviewed every year)", {"MONTHLY"}),
        # When a first instalment is due is not how often the fee is paid.
        ("I вноска: 3000 лв. – в началото на учебната година", set()),
    ],
)
def test_every_school_year_is_yearly_only_when_nothing_else_says(text, families):
    from app.scrapers.price_evidence import period_families

    assert period_families(text.casefold()) == families


@pytest.mark.parametrize(
    "text",
    [
        "Целодневна група\n650 лв.\nЦените се актуализират всяка година.",
        "Full day: 650 EUR (reviewed every year)",
        "Такса за кръжок 40 лв., записване всяка година",
    ],
)
def test_a_note_that_prices_change_every_year_names_no_period(text):
    from app.scrapers.price_evidence import period_families

    assert period_families(text.casefold()) == set()
