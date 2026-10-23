"""extraction/price_store.py: how a fee read's rows are coerced before they are stored."""

from __future__ import annotations

from app.schemas.extraction import ExtractedPrice
from app.scrapers.extraction import price_store


def _price(**fields) -> ExtractedPrice:
    return ExtractedPrice(**{"category": "tuition", "amount": 8000, "currency": "EUR", "confidence": 0.9, **fields})


def test_an_unstated_period_is_kept_unstated():
    fields = price_store.normalized_price_fields(_price(period=None))
    assert fields is not None and fields["period"] is None


def test_an_unknown_stated_period_is_rejected():
    assert price_store.normalized_price_fields(_price(period="fortnightly")) is None


def test_a_label_with_a_comma_is_kept_whole():
    """School 542: 'Детска Ясла ,,Йор Кидс“ / Целодневен престой' was cut to its heading."""
    label = "Месечни такси в Частна Детска Ясла ,,Йор Кидс“ / Целодневен престой"
    assert price_store.normalized_price_fields(_price(plan_name=label))["plan_name"] == label
    assert price_store.normalized_price_fields(_price(plan_name="N/A"))["plan_name"] is None
