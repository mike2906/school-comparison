"""UF45 evidence rules (app.scrapers.price_evidence), on the real UF42 re-run pages.

The six wrong rows fixed by hand on 2026-09-28 (525, 630, 556) are rebuilt from the
plan notes against the stored page text: each must trip its rule, and the rows as fixed
must pass.
"""

from decimal import Decimal

import pytest

from app.scrapers.price_evidence import (
    PriceRow,
    amount_spans,
    check_price_row,
    normalize_text,
    period_families,
    replacement_regressions,
    stated_period,
)
from scripts.audit_price_rows_uf45 import PublishedName, audit_names

# slaveiche.com/taksi (school 525, kindergarten), source page 25916.
SLAVEICHE = """ТАКСИ
€ 530 | 1037 лв.
- целодневно гледане, ежемесечно заплащане
€ 350 | 685 лв.
- половин ден с включен обяд
/от 7.30 ч. до 12.30 ч. или от 12.00 ч. до 19.00 ч./
€ 265 | 518 лв.
– депозит за запазване на място
Отстъпки при предплащане на целодневна такса:
€ 510 | 997 лв.
- при предплащане за 3 месеца
€ 490 | 958 лв.
- при предплащане над 6 месеца
Основната такса включва всичко задължително по отглеждането и обучението на децата - храна, медицинско обслужване, обучение по програмите на МОН, както и всички учебни помагала и консумативи.
По желание на родителите, детето може да бъде включено в група по английски език, йога и/или модерни танци.
Допълнителните дейности не са включени в таксата и се заплащат отделно.
•
обучение по английски език
- 3 пъти седмично -
€ 20 / 39.12 лв.
месечна такса
йога за деца
- 1 път седмично -
модерни танци
- 2 пъти седмично -
месечна такса, като на 1-ви юни всяка година децата изнасят концерт на голяма сцена.
Допълнителна отстъпка от 10% ползват родители с второ дете в детската градина.
"""

# waldorf.bg/admission (school 630, school-level), source page 24689, fee section.
WALDORF = """ТАКСИ
Детска градина & предучилищна група:
плащане в пълен размер:
6200 евро
плащане на 2 вноски:
6600 евро
плащане на 11 вноски:
7050
евро
Предучилищен и от 1-ви до 7-ми клас:
6150 евро
6
540 евро
плащане на 3 вноски:
6990 евро
Гимназия, 8-ми до 12 клас:
6640 евро
7060 евро
7545
Други такси
кетъринг в детската градина:
1
/ден
еднократен възстановим депозит за нови деца във Валдорфската детска градина:
400
седмична такса за гостуващи ученици във Валдорфското училище:
135 евро
еднократен възстановим депозит за нови ученици във Валдорфското училище:
800 евро
Депозитите се възстановяват при завършване или напускане на детската градина/училището.
Всички суми са в евро!
"""


def row(
    amount,
    category="TUITION",
    period=None,
    plan_name=None,
    notes=None,
    amount_min=None,
    amount_max=None,
):
    return PriceRow(
        category=category,
        amount=amount,
        amount_min=amount_min,
        amount_max=amount_max,
        period=period,
        plan_name=plan_name,
        notes=notes,
    )


def rules(price_row, page, family):
    return {finding.rule for finding in check_price_row(price_row, page, family)}


# ---------------------------------------------------------------------------
# The six real cases (rebuilt wrong rows trip; rows as fixed pass)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("plan_name", ["Детска градина & предучилищна група", None])
def test_630_kindergarten_fee_filed_as_school_tuition_trips_rule_2(plan_name):
    wrong = row(Decimal("6200"), period="YEARLY", plan_name=plan_name)
    assert "2_level_fits_school" in rules(wrong, WALDORF, "school")


def test_630_invented_catering_row_copied_from_line_above_trips_rule_1():
    wrong = row(Decimal("6200"), period="YEARLY", plan_name="кетъринг в детската градина")
    assert "1_amount_near_label" in rules(wrong, WALDORF, "school")


def test_630_weekly_guest_fee_filed_as_registration_trips_rule_3():
    wrong = row(Decimal("135"), category="REGISTRATION", period="ONE_TIME")
    assert "3_period_unrepresentable" in rules(wrong, WALDORF, "school")
    with_label = row(
        Decimal("135"),
        category="REGISTRATION",
        plan_name="седмична такса за гостуващи ученици във Валдорфското училище",
    )
    assert "3_period_unrepresentable" in rules(with_label, WALDORF, "school")


def test_525_deposit_filed_as_tuition_trips_rule_4():
    wrong = row(Decimal("265"), notes="депозит за запазване на място")
    assert "4_deposit_not_tuition" in rules(wrong, SLAVEICHE, "kindergarten")
    without_label = row(Decimal("265"))
    assert "4_deposit_not_tuition" in rules(without_label, SLAVEICHE, "kindergarten")


@pytest.mark.parametrize(
    "amount,category,notes",
    [
        (Decimal("530"), "TUITION", "целодневно гледане"),
        (Decimal("20"), "EXTRACURRICULAR", "обучение по английски език - 3 пъти седмично"),
    ],
)
def test_525_null_period_with_monthly_wording_trips_rule_3(amount, category, notes):
    wrong = row(amount, category=category, notes=notes)
    findings = check_price_row(wrong, SLAVEICHE, "kindergarten")
    assert [(f.rule, f.period) for f in findings] == [("3_period_missing", "MONTHLY")]


def test_556_name_taken_from_sibling_trips_rule_5():
    hits = audit_names(
        [
            PublishedName(556, "https://sofia-kindergarten.maplebear.bg/", ("Maple Bear Sofia",)),
            PublishedName(596, "https://maplebear.bg/", ("Maple Bear Sofia", "Maple Bear Sofia")),
        ]
    )
    assert {(h.school_id, h.rule) for h in hits} == {
        (556, "5_name_not_sibling"),
        (596, "5_name_not_sibling"),
    }


def test_556_as_fixed_passes_rule_5():
    assert audit_names(
        [
            PublishedName(556, "https://sofia-kindergarten.maplebear.bg/", ("Maple Bear Kindergarten Sofia",)),
            PublishedName(596, "https://maplebear.bg/", ("Maple Bear Sofia",)),
        ]
    ) == []


def test_same_name_on_unrelated_sites_passes_rule_5():
    assert audit_names(
        [
            PublishedName(1, "https://a-school.bg/", ("Светлина",)),
            PublishedName(2, "https://other.bg/", ("Светлина",)),
        ]
    ) == []


@pytest.mark.parametrize(
    "fixed",
    [
        row(Decimal("530"), period="MONTHLY", notes="целодневно гледане"),
        row(Decimal("350"), notes="половин ден с включен обяд"),
        row(Decimal("265"), category="REGISTRATION", period="ONE_TIME", notes="депозит за запазване на място"),
        row(
            Decimal("20"),
            category="EXTRACURRICULAR",
            period="MONTHLY",
            notes="обучение по английски език - 3 пъти седмично",
        ),
    ],
)
def test_525_rows_as_fixed_pass(fixed):
    assert rules(fixed, SLAVEICHE, "kindergarten") == set()


@pytest.mark.parametrize(
    "fixed",
    [
        row(Decimal("6150"), period="YEARLY", plan_name="Предучилищен и от 1-ви до 7-ми клас"),
        row(Decimal("6640"), period="YEARLY", plan_name="Гимназия"),
        row(
            Decimal("800"),
            category="REGISTRATION",
            period="ONE_TIME",
            plan_name="еднократен възстановим депозит за нови ученици във Валдорфското училище",
        ),
    ],
)
def test_630_rows_as_fixed_pass(fixed):
    assert rules(fixed, WALDORF, "school") == set()


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value,page,found",
    [
        (Decimal("6200"), "6200 евро", True),
        (Decimal("6200"), "6 200 €", True),
        (Decimal("6200"), "6.200 лв.", True),
        (Decimal("6200"), "6200.00", True),
        (Decimal("181.44"), "181,44 euro", True),
        (Decimal("400"), "euro 40 0 (age 2-4)", True),  # scraped split digits
        (Decimal("20"), "20% отстъпка", False),
        (Decimal("12"), "от 12.00 ч.", False),
        (Decimal("20"), "2026 г.", False),
        (Decimal("200"), "1200 лв.", False),
        (Decimal("6.5"), "6.50 евро", True),
        (Decimal("200"), "Такса 1 200 лв.", False),  # tail of a grouped number
        (Decimal("1"), "Такса 1 200 лв.", False),  # head of a grouped number
        (Decimal("970"), "група 0-3 970 евро", True),  # a range is not a group
        (Decimal("1200"), "Такса 1 200 лв.", True),
    ],
)
def test_amount_spans(value, page, found):
    assert bool(amount_spans(normalize_text(page), value)) is found


def test_amount_not_on_page_trips_rule_1():
    assert "1_amount_near_label" in rules(row(Decimal("999")), SLAVEICHE, "kindergarten")


def test_price_after_other_fees_label_trips_rule_1():
    page = "СТАНДАРТНА\nТАКСА\nEUR\n6600\nТакса кандидатстване\nEUR 70\nКандидатствай"
    assert "1_amount_near_label" in rules(
        row(Decimal("70"), category="REGISTRATION", period="ONE_TIME", plan_name="СТАНДАРТНА"), page, "school"
    )
    assert rules(row(Decimal("6600"), period="YEARLY", plan_name="СТАНДАРТНА"), page, "school") == set()


def test_monthly_and_yearly_columns_of_one_row_pass_rule_1():
    page = "Nursery one\n1-2\n08:00 - 18:00\n650.00 €\n6 792.00 €\nNursery two"
    assert rules(row(Decimal("6792"), period="YEARLY", plan_name="Nursery one"), page, "kindergarten") == set()


def test_table_header_labels_are_not_judged_by_rule_1():
    page = "8. клас | 9. клас\nТакса обучение | € 3020 | € 3000\nТакса програми | € 3020 | € 3020"
    assert "1_amount_near_label" not in rules(row(Decimal("3000"), period="YEARLY", plan_name="9. клас"), page, "school")


@pytest.mark.parametrize(
    "label,family,trips",
    [
        ("Детска градина", "school", True),
        ("Яслена група", "school", True),
        ("Ясла", "school", True),
        ("Nursery", "school", True),
        ("Pre-Kindergarten", "school", False),  # an international school's reception class
        ("Предучилищна група", "school", False),
        ("Гимназия", "kindergarten", True),
        ("Grades 1-12", "kindergarten", True),
        ("подготовка за 1 клас", "kindergarten", False),
        ("Детска градина", "kindergarten", False),
    ],
)
def test_rule_2_level_vocabulary(label, family, trips):
    page = f"{label}:\n500 евро"
    assert ("2_level_fits_school" in rules(row(Decimal("500"), period="MONTHLY", plan_name=label), page, family)) is trips


@pytest.mark.parametrize(
    "context,families,unrepresentable",
    [
        ("€ 530 - целодневно гледане, ежемесечно заплащане", {"MONTHLY"}, False),
        ("месечен абонамент", {"MONTHLY"}, False),
        ("785 euro/месец", {"MONTHLY"}, False),
        ("годишен абонамент", {"YEARLY"}, False),
        ("еднократен възстановим депозит", {"ONE_TIME"}, False),
        ("тримесечна такса", {"QUARTER"}, False),
        ("- 3 пъти седмично - € 20", set(), False),
        ("4 хранения дневно", set(), False),
        ("целодневно обучение", set(), False),
        ("полудневно обучение", set(), False),
        ("часовете се провеждат ежедневно", set(), False),
        ("ред на провеждане на часовете", set(), False),
        ("за следващата учебна година", set(), False),
        ("за 3 до 7-годишни деца", set(), False),
        ("€ 20\nмесечна такса", {"MONTHLY"}, False),  # an amount then its period, not an age
        ("500 месечно", {"MONTHLY"}, False),
        ("такса на срок", {"TERM"}, False),
        ("семестриална такса", {"SEMESTER"}, False),
        ("седмична такса за гостуващи ученици", set(), True),
        ("кетъринг 1 /ден", set(), True),
        ("15 лв. на ден", set(), True),
        ("€40 per hour", set(), True),
    ],
)
def test_period_families(context, families, unrepresentable):
    assert period_families(normalize_text(context)) == (families, unrepresentable)


def test_own_line_period_outranks_table_header_below():
    text = normalize_text("School meals | 181,44 euro/ month |\nAnnual fee | Monthly fee")
    span = amount_spans(text, Decimal("181.44"))[0]
    assert stated_period(text, span) == ({"MONTHLY"}, False)


def test_term_row_with_term_wording_passes():
    assert rules(row(Decimal("900"), period="TERM"), "Такса на срок:\n900 евро", "school") == set()


def test_period_conflict_and_the_yearly_one_time_overlap():
    page = "Годишна такса:\n6000 евро"
    assert "3_period_conflict" in rules(row(Decimal("6000"), period="MONTHLY"), page, "school")
    assert rules(row(Decimal("6000"), period="YEARLY"), page, "school") == set()
    assert rules(row(Decimal("6000"), period="ONE_TIME"), page, "school") == set()


@pytest.mark.parametrize(
    "line,period,trips",
    [
        ("Плащане I вноска до 31.03.2026 г. - 3975€ / 7774,42 лв.", "ONE_TIME", True),
        ("Плащане 1-ва вноска - 3975€", "YEARLY", True),
        ("First installment: €3,975", None, True),
        ("Стандартна такса: за 1 вноска: €3975", "YEARLY", False),
        ("Плащане I вноска - 3975€", "MONTHLY", False),  # a monthly installment may stand
    ],
)
def test_installment_filed_as_tuition(line, period, trips):
    hit = "4_installment_not_tuition" in rules(row(Decimal("3975"), period=period), line, "school")
    assert hit is trips


def test_missing_page_text_is_reported():
    assert rules(row(Decimal("100")), None, "school") == {"1_amount_near_label"}


# ---------------------------------------------------------------------------
# Codex review of #127
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "amount,currency,trips",
    [
        (Decimal("530"), "EUR", False),
        (Decimal("530"), "BGN", True),  # the page shows €530; 530 лв. would be wrong
        (Decimal("1037"), "BGN", False),  # the BGN companion
        (Decimal("1037"), "EUR", True),
    ],
)
def test_amount_must_be_shown_in_the_rows_currency(amount, currency, trips):
    price_row = PriceRow(
        category="TUITION", amount=amount, amount_min=None, amount_max=None,
        period="MONTHLY", plan_name=None, notes="целодневно гледане", currency=currency,
    )
    assert ("1_amount_near_label" in rules(price_row, SLAVEICHE, "kindergarten")) is trips


def test_a_bare_amount_is_not_judged_on_currency():
    price_row = PriceRow(
        category="TUITION", amount=Decimal("6150"), amount_min=None, amount_max=None,
        period="YEARLY", plan_name="Гимназия", notes=None, currency="BGN",
    )
    assert "1_amount_near_label" not in rules(price_row, "Гимназия:\n6150\nВсички суми са в евро!", "school")


@pytest.mark.parametrize(
    "page,trips",
    [("Такса: от 500 до 900 евро", False), ("Такса: от 500 до 700 евро", True)],
)
def test_both_ends_of_a_range_must_be_on_the_page(page, trips):
    price_row = row(None, period="MONTHLY", amount_min=Decimal("500"), amount_max=Decimal("900"))
    assert ("1_amount_near_label" in rules(price_row, page, "school")) is trips


def test_rule_6_counts_fees_that_share_an_amount():
    page = "Такса записване: 500 евро\nМесечна такса: 500 евро"
    published = [(row(Decimal("500"), category="REGISTRATION"), page), (row(Decimal("500"), period="MONTHLY"), page)]
    proposed = [(row(Decimal("500"), period="MONTHLY"), page)]
    assert replacement_regressions(published, proposed, "school") == [
        "drops 1 of 2 rows at 500.00 (REGISTRATION 500.00, TUITION 500.00), which the page still shows"
    ]


def test_rule_6_allows_a_fee_refiled_under_another_category():
    """525's €265 deposit moved from tuition to registration: same fee, not a loss."""
    published = [(row(Decimal("265"), period="ONE_TIME"), SLAVEICHE)]
    proposed = [(row(Decimal("265"), category="REGISTRATION", period="ONE_TIME"), SLAVEICHE)]
    assert replacement_regressions(published, proposed, "kindergarten") == []


# School 183 (sianie-bg.com/?pg=ceni): the deposit's label is split from its colon.
SIANIE = """Целодневно пребиваване:
Детска градина - 675 EUR
Депозит
: 450 EUR
Таксите се внасят месец за месец
"""


@pytest.mark.parametrize("plan_name", ["Целодневно пребиваване", None])
def test_183_tuition_is_not_the_next_lines_deposit(plan_name):
    tuition = row(Decimal("675"), period="MONTHLY", plan_name=plan_name)
    assert rules(tuition, SIANIE, "kindergarten") == set()


def test_183_deposit_filed_as_tuition_still_trips_rule_4():
    assert "4_deposit_not_tuition" in rules(row(Decimal("450"), plan_name="Депозит"), SIANIE, "kindergarten")
    assert "4_deposit_not_tuition" in rules(row(Decimal("450")), SIANIE, "kindergarten")


def test_deposit_context_trips_rule_4_even_when_the_label_omits_the_word():
    wrong = row(Decimal("265"), notes="за запазване на място")
    assert "4_deposit_not_tuition" in rules(wrong, SLAVEICHE, "kindergarten")


# arlekino.info/taksi (school 199, kindergarten) as crawled on 2026-10-08: a table with
# one cell per line, so the column headers are nowhere near the amounts.
ARLEKINO = """Такси
Такси за обучение в частна детска градина и ясла Арлекино
Програма
Възраст
Часове
Такса / месец
Такса / годишно плащане
Nursery one
1-2
08:00 - 18:00
650.00 €
6 792.00 €
Book
4 - 5
08:00 - 18:00
450.00 €
4 702.00 €
Preschool
5 - 7
08:00 - 18:00
450.00 €
4 702.00 €
Включени в цената са
три здравословни хранения
на ден.
"""


def test_rule_6_holds_a_period_change_the_page_does_not_state():
    """199's re-run: the published yearly fees came back as monthly, with no plan names."""
    published = [
        (row(Decimal("6792"), period="YEARLY", plan_name="Nursery one"), ARLEKINO),
        # Three published tiers at an amount the page shows twice: not a dropped fee,
        # and the row that remains must still keep the period.
        (row(Decimal("4702"), period="YEARLY", plan_name="Starter"), ARLEKINO),
        (row(Decimal("4702"), period="YEARLY", plan_name="Book"), ARLEKINO),
        (row(Decimal("4702"), period="YEARLY", plan_name="Preschool"), ARLEKINO),
    ]
    proposed = [
        (row(Decimal("650"), period="MONTHLY"), ARLEKINO),
        (row(Decimal("6792"), period="MONTHLY"), ARLEKINO),
        (row(Decimal("450"), period="MONTHLY"), ARLEKINO),
        (row(Decimal("4702"), period="MONTHLY"), ARLEKINO),
    ]
    assert replacement_regressions(published, proposed, "kindergarten") == [
        "changes the YEARLY period of TUITION 6792.00 to MONTHLY, "
        "which the page does not state next to the amount",
        "changes the YEARLY period of TUITION 4702.00 to MONTHLY, "
        "which the page does not state next to the amount",
    ]
    # The same amounts with their periods kept may replace the published rows.
    kept = [(row(Decimal("6792"), period="YEARLY"), ARLEKINO), (row(Decimal("4702"), period="YEARLY"), ARLEKINO)]
    assert replacement_regressions(published, kept, "kindergarten") == []


def test_rule_6_allows_a_period_change_the_page_states():
    """302: rows stored yearly although the page says "Месечна такса" beside them."""
    page = "Месечна такса: 680 евро"
    published = [(row(Decimal("680"), period="YEARLY"), page)]
    proposed = [(row(Decimal("680"), period="MONTHLY"), page)]
    assert replacement_regressions(published, proposed, "kindergarten") == []
    # Another fee at the same amount stating another period settles nothing.
    both = "Храна: 680 евро месечно\nТакса: 680 евро годишно"
    assert replacement_regressions(
        [(row(Decimal("680"), period="YEARLY"), both)], [(row(Decimal("680"), period="MONTHLY"), both)], "kindergarten"
    ) == [
        "changes the YEARLY period of TUITION 680.00 to MONTHLY, "
        "which the page does not state next to the amount"
    ]
    # A yearly fee and a one-time fee are one statement to rule 3; a swap is not a change.
    once = [(row(Decimal("680"), period="ONE_TIME"), "Такса: 680 евро")]
    assert replacement_regressions([(row(Decimal("680"), period="YEARLY"), "Такса: 680 евро")], once, "school") == []

