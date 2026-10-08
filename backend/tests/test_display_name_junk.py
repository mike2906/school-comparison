"""Page copy stored as a display name (live-test finding: schools 305, 507, 517)."""

import pytest

from app.scrapers.extractor_helpers import _is_low_quality_display_name, _normalize_display_name_i18n

JUNK = [
    "Ръководство на частно средно училище”Джани РоДари",
    "Нашето семейство включва Първа Частна Математическа Гимназия",
    "Мили учителки от детска градина Куест Джуниър, искаме да ви благодарим за вниманието, "
    "търпението и любовта, които отдадохте на нашето дете през тези 4 години.",
]

GOOD = [
    "Софийска гимназия по хлебни и сладкарски технологии",
    "Четвърто Основно Училище Джон Атанасов в гр.София",
    "ДГ №11 Мики Маус (с яслени групи)",
    "Частна професионална гимназия по сценични изкуства АКАТАМУС",
]


@pytest.mark.parametrize("value", JUNK)
def test_junk_display_name_rejected(value):
    assert _is_low_quality_display_name(value)
    assert _normalize_display_name_i18n({"bg": value}, "bg") is None


@pytest.mark.parametrize("value", GOOD)
def test_real_display_name_still_accepted(value):
    assert not _is_low_quality_display_name(value)
    assert _normalize_display_name_i18n({"bg": value}, "bg")
