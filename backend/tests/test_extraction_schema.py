"""Focused validation tests for extraction output schemas."""

import pytest
from pydantic import ValidationError

from app.schemas.extraction import ExtractedPrice


@pytest.mark.parametrize("confidence", [-0.01, 1.01, float("nan"), True, "0.9"])
def test_extracted_price_rejects_invalid_confidence(confidence):
    with pytest.raises(ValidationError):
        ExtractedPrice(
            category="tuition",
            amount=500,
            currency="BGN",
            period="monthly",
            confidence=confidence,
        )


def test_extracted_price_requires_confidence():
    with pytest.raises(ValidationError):
        ExtractedPrice(category="tuition", amount=500, currency="BGN", period="monthly")


@pytest.mark.parametrize("confidence", [0.0, 0.7, 1.0])
def test_extracted_price_accepts_bounded_confidence(confidence):
    price = ExtractedPrice(
        category="tuition",
        amount=500,
        currency="BGN",
        period="monthly",
        confidence=confidence,
    )
    assert price.confidence == confidence
