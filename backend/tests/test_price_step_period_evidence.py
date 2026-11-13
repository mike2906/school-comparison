"""Rule 6 and periods Stage 6 filled in: no evidence once today's checks no longer give them."""

from decimal import Decimal
from types import SimpleNamespace

from app.scrapers.extraction.price_step import _as_evidence, auto_filled_period_row_ids
from app.scrapers.price_evidence import PriceRow, replacement_regressions

# 632 (Acatamus): "Месечна такса" is the €700 heading's own word, not the pay-in-full €7,000's.
ACATAMUS = """## ТАКСИ ЗА ОБУЧЕНИЕ
######  ТАКСИ ЗА УЧЕБНАТА 2026 / 2027
##  Месечна такса за обучение: 7 00 евро
  * Получавате 10% отстъпка при плащане на пълната сума ( €7 000) наведнъж.
  * Допълнително всеки ученик заплаща 150 евро на месец за уъркшопи.
"""

MONTHLY_LINE = "Месечна такса за обучение: 700 евро на месец\n"


def stored(row_id, amount, period, plan_name=None):
    return SimpleNamespace(
        id=row_id,
        category="TUITION",
        amount=Decimal(amount),
        amount_min=None,
        amount_max=None,
        period=period,
        plan_name=plan_name,
        pricing_context={},
        currency="EUR",
        age_group=None,
    )


def fix(row_id, code="pricing_period_from_text"):
    return {"code": code, "field_path": f"pricing[{row_id}].period", "fixed_value": "monthly"}


def test_filled_rows_come_from_the_current_and_old_reports():
    attributes = {
        "data_validation": {"auto_fixes": [fix(6657)]},
        "data_validation_history": [
            {"auto_fixes": [fix(5398), fix(5399, code="price_currency_from_text")]},
            {"auto_fixes": [{"code": "pricing_period_from_text", "field_path": "pricing[12].amount"}]},
            "junk",
        ],
    }
    assert auto_filled_period_row_ids(attributes) == {6657, 5398}
    assert auto_filled_period_row_ids(None) == set()


def test_632_a_filled_period_todays_checks_no_longer_give_is_dropped():
    row = stored(6657, "7000", "monthly", plan_name="Плащане на пълната сума наведнъж")
    assert _as_evidence(row, ACATAMUS, "school", auto_filled=True).period is None
    # Read by the reader or set by a person: still evidence.
    assert _as_evidence(row, ACATAMUS, "school", auto_filled=False).period == "MONTHLY"


def test_a_filled_period_the_page_still_states_stays():
    row = stored(1, "700", "monthly")
    assert _as_evidence(row, MONTHLY_LINE, "school", auto_filled=True).period == "MONTHLY"


def test_632_rereading_the_7000_without_a_period_is_no_regression_once_dropped():
    old = stored(6657, "7000", "monthly", plan_name="Плащане на пълната сума наведнъж")
    new = PriceRow.from_pricing(stored(None, "7000", None, plan_name="Плащане на пълната сума наведнъж"))
    kept = [(PriceRow.from_pricing(old), ACATAMUS)]
    assert replacement_regressions(kept, [(new, ACATAMUS)], "school")  # the old guard holds it
    weighed = [(_as_evidence(old, ACATAMUS, "school", auto_filled=True), ACATAMUS)]
    assert replacement_regressions(weighed, [(new, ACATAMUS)], "school") == []


def test_a_filled_period_stays_when_the_page_text_is_missing():
    row = stored(6657, "7000", "monthly")
    assert _as_evidence(row, None, "school", auto_filled=True).period == "MONTHLY"


def test_an_old_lev_row_weighed_in_euro_is_checked_at_its_lev_amount():
    from app.scrapers.stages.fees_agent import _InEuro

    row = stored(1, "899.68", "yearly")
    row.currency = "BGN"
    page = "Годишна такса за обучение: 899.68 лв.\n"
    weighed = _as_evidence(_InEuro(row, (Decimal("460"), None, None)), page, "school", auto_filled=True)
    assert weighed.period == "YEARLY"
    assert weighed.amount == Decimal("460")
