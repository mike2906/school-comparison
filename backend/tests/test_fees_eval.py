"""The fees eval's scorer and its offline path (no model: a fake reader)."""

import importlib.util
import types
from pathlib import Path

import pytest

from app.models.pricing import PriceCategory, PricePeriod
from app.scrapers.extraction.agent_fees import AgentFee, AgentFees, PaymentOption

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fees_eval.py"
spec = importlib.util.spec_from_file_location("fees_eval", SCRIPT)
fees_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fees_eval)

URL = "https://example.bg/fees"


def _row(category, amount, period=None, currency="EUR", options=()):
    return types.SimpleNamespace(
        category=types.SimpleNamespace(value=category),
        period=types.SimpleNamespace(value=period) if period else None,
        amount=amount,
        amount_min=None,
        amount_max=None,
        currency=currency,
        pricing_context={"label": "x", "payment_options": list(options)},
    )


def _fee(fid, category, amount, period=None, currency="EUR"):
    return {"fid": fid, "category": category, "amount": amount, "currency": currency, "period": period}


def test_score_school_verdicts():
    key = [
        _fee("f1", "tuition", 7300, "yearly"),
        _fee("f2", "tuition", 3850),
        _fee("f3", "registration", 500, "one_time"),
        _fee("f4", "food", 90, "monthly"),
    ]
    rows = [
        _row("tuition", 7300, "yearly", options=[{"instalment": 3850}]),  # right, finds f2 too
        _row("tuition", 7300, "yearly"),  # duplicate
        _row("registration", 500),  # blank period
        _row("tuition", 90, "monthly"),  # wrong category
        _row("transport", 1234, "yearly"),  # not in the key
        _row("food", 176.02, "monthly", currency="BGN"),  # 90 EUR in leva: right
    ]
    scored = fees_eval.score_school(rows, key)
    assert [row["verdict"] for row in scored["rows"]] == ["right", "duplicate", "blank", "wrong", "wrong", "right"]
    assert scored["found"] == ["f1", "f2", "f3", "f4"]
    summary = fees_eval.summarize({1: scored})
    assert summary["wrong_rate"] == round(3 / 6, 4)  # 2 wrong and 1 duplicate of 6 rows
    assert summary["found_rate"] == 1.0


def test_same_amount_for_several_groups():
    key = [_fee("f1", "tuition", 9000, "yearly"), _fee("f2", "tuition", 9000, "yearly"), _fee("f3", "tuition", 9000)]
    rows = [_row("tuition", 9000, "yearly"), _row("tuition", 9000, "yearly"), _row("tuition", 9000, "yearly")]
    scored = fees_eval.score_school(rows, key)
    assert [row["verdict"] for row in scored["rows"]] == ["right", "right", "duplicate"]
    assert scored["found"] == ["f1", "f2"]


def test_option_and_own_row_score_alike_in_either_order():
    key = [_fee("f1", "tuition", 7300, "yearly"), _fee("f2", "tuition", 3850)]
    plan = _row("tuition", 7300, "yearly", options=[{"instalment": 3850}])
    part = _row("tuition", 3850)
    first = fees_eval.score_school([plan, part], key)
    second = fees_eval.score_school([part, plan], key)
    assert sorted(r["verdict"] for r in first["rows"]) == sorted(r["verdict"] for r in second["rows"])
    assert first["found"] == second["found"] == ["f1", "f2"]


def test_range_row_needs_the_same_range():
    key = [
        _fee("f1", "tuition", 6000, "yearly"),
        {**_fee("f2", "tuition", None), "amount_min": 6000, "amount_max": 9500},
    ]
    row = _row("tuition", None, "yearly")
    row.amount_min, row.amount_max = 6000, 9500
    scored = fees_eval.score_school([row], key)
    assert scored["rows"][0]["fid"] == "f2"


def test_single_amount_does_not_match_a_range():
    key = [{**_fee("f1", "tuition", None), "amount_min": 10, "amount_max": 20}]
    scored = fees_eval.score_school([_row("tuition", 10)], key)
    assert scored["rows"][0]["verdict"] == "wrong" and scored["found"] == []


def test_check_against_baseline():
    baseline = {"wrong_rate": 0.10, "found_rate": 0.70}
    assert fees_eval.check({"wrong_rate": 0.14, "found_rate": 0.64}, baseline) == []
    assert len(fees_eval.check({"wrong_rate": 0.20, "found_rate": 0.50}, baseline)) == 2


def test_key_loads_both_sets():
    key = fees_eval.load_key()
    assert 541 not in key  # blocked
    assert len(key[520]) >= 10  # judged key, with the fees the judges added
    assert key[272] == []  # a site that states no fees stays in, so a row there counts as wrong


@pytest.mark.asyncio
async def test_read_school_offline(monkeypatch):
    line = "Годишна такса: 7300 €\nНа 2 вноски: 2 x 3850 €"
    fee = AgentFee(
        category=PriceCategory.TUITION,
        amount=7300,
        currency="EUR",
        period=PricePeriod.YEARLY,
        plan_name=None,
        academic_year=None,
        age_group=None,
        label="Годишна такса",
        source_url=URL,
        evidence=line,
        payment_options=[PaymentOption(payments=2, instalment=3850)],
    )
    invented = fee.model_copy(update={"amount": 9999, "evidence": "Такса: 9999 €"})

    class FakeAgent:
        async def run(self, message):
            assert "=== PAGE https://example.bg/fees" in message
            return types.SimpleNamespace(output=AgentFees(fees=[fee, invented]), all_messages=lambda: [])

    monkeypatch.setattr(fees_eval, "reader_agent", lambda: FakeAgent())
    saved = {
        "school_id": 1,
        "name_i18n": {"bg": "Училище"},
        "school_type": "private",
        "education_level": "primary",
        "pages": [
            {"url": "https://example.bg/", "text": "Начало", "prices": False, "note": None},
            {"url": URL, "text": f"Такси\n{line}\n", "prices": True, "note": None},
        ],
    }
    spend = fees_eval.Spend(0.10)
    rows = await fees_eval.read_school(saved, spend)
    assert [(row.amount, row.category.value) for row in rows] == [(7300, "tuition")]  # the invented fee is dropped
    assert 0 < spend.spent_usd < 0.01  # no cost reported: the estimate is counted
    scored = fees_eval.score_school(rows, [_fee("f1", "tuition", 7300, "yearly"), _fee("f2", "tuition", 3850)])
    assert scored["found"] == ["f1", "f2"]
