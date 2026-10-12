"""A re-read fee with no period keeps the published period only when it is the same fee."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.pricing import PriceCategory, PricePeriod, Pricing
from app.scrapers import price_carry_over
from app.scrapers.price_carry_over import carry_over_periods

PAGE = SimpleNamespace(id=1, raw_markdown="Годишна такса\n4950 €\n")


def _row(period=None, page_id=1, currency="EUR", year="2026-2027", amount=4950):
    return Pricing(
        id=None, category=PriceCategory.TUITION, amount=amount, currency=currency,
        period=period, academic_year=year, source_page_id=page_id,
    )  # fmt: skip


@pytest.fixture(autouse=True)
def _all_publishable(monkeypatch):
    monkeypatch.setattr(price_carry_over, "pricing_row_is_publishable", lambda row: True)


def _carried(old, new):
    source = SimpleNamespace(field_key="pricing.tuition")
    carry_over_periods(SimpleNamespace(attributes=None), [PAGE], [old], [new], [source])
    return new.period


def test_the_same_fee_keeps_its_period():
    assert _carried(_row(PricePeriod.YEARLY), _row(year="2026/2027")) == PricePeriod.YEARLY


@pytest.mark.parametrize(
    "new",
    [_row(page_id=2), _row(currency="BGN"), _row(year="2025-2026"), _row(amount=4725)],
    ids=["another page", "another currency", "another year", "another amount"],
)
def test_another_fee_at_the_amount_does_not(new):
    assert _carried(_row(PricePeriod.YEARLY), new) is None
