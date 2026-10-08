"""A fee table read from a picture is used only when two readings agree on its numbers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.scrapers import fee_image_reader
from app.scrapers.fee_image_reader import numbers_in, read_fee_image


def test_numbers_in_ignores_spacing_and_separators():
    assert numbers_in("Grade 1 | 14 750 € | 15.350,00") == ["1", "14750", "1535000"]


async def _read(*readings: str) -> str | None:
    agent = SimpleNamespace(run=AsyncMock(side_effect=[SimpleNamespace(output=text) for text in readings]))
    with patch.object(fee_image_reader, "create_agent", return_value=agent):
        return await read_fee_image(b"png", "image/png", school_id=1)


@pytest.mark.asyncio
async def test_two_agreeing_readings_are_used():
    assert await _read("Grade 1 | 14 750", "Grade 1 | 14750") == "Grade 1 | 14 750"


@pytest.mark.asyncio
async def test_a_misread_digit_discards_the_picture():
    assert await _read("Grade 1 | 14 750", "Grade 1 | 14 150") is None


@pytest.mark.asyncio
async def test_a_picture_without_fees_gives_nothing():
    assert await _read("NONE") is None


@pytest.mark.asyncio
async def test_same_digits_under_another_label_or_currency_discard_the_picture():
    assert await _read("Grade 1 | 1000 EUR", "Nursery | 1000 EUR") is None
    assert await _read("Grade 1 | 1000 EUR", "Grade 1 | 1000 BGN") is None
    assert await _read("Grade 1 | 1 000 EUR", "GRADE 1 | 1000 eur") == "Grade 1 | 1 000 EUR"
