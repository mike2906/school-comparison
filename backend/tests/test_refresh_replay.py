"""The 2026-10-12 targeted refresh, replayed from its recorded model answers.

171 and 564 were held by rule 6, 533 too, and 610 was written with its one-payment
yearly fees as monthly. Each test runs the recorded answer through `_extract_prices` over
the stored pages and checks what is written, and what Stage 6 then withholds.
"""

from __future__ import annotations

import pytest

from app.models.pricing import PriceCategory, PricePeriod
from app.scrapers.price_evidence import RULE_EXPIRED, PriceRow, check_price_row
from tests.refresh_replay import replay

pytestmark = pytest.mark.usefixtures("frozen_today")


def _tuition(rows):
    return sorted(
        (float(row.amount), row.period.value if row.period else None)
        for row in rows
        if row.category == PriceCategory.TUITION
    )


def _expired(rows, pages_text):
    return sorted(
        float(row.amount)
        for row in rows
        if any(
            finding.rule == RULE_EXPIRED
            for finding in check_price_row(PriceRow.from_pricing(row), pages_text[row.source_page_id], "school")
        )
    )


@pytest.mark.asyncio
async def test_171_expired_tiers_go_and_the_hand_set_yearly_periods_stay(db_session):
    result, rows = await replay(db_session, 171)

    assert result.get("held") is None
    current = [row for row in rows if row.academic_year == "2026/2027"]
    # The Christmas discounts are listed under the fees, not stored; the yearly periods
    # set by hand from "за учебната 2026 - 2027 г." carry over to the same fees.
    assert _tuition(current) == [
        (4500.0, None), (4725.0, "yearly"), (4950.0, "yearly"), (5555.0, None),
        (6600.0, None), (6969.0, None), (7269.0, "yearly"),
    ]  # fmt: skip
    pages = {row.source_page_id: row.source_page.raw_markdown for row in current}
    # Stage 6 withholds every tier whose enrolment deadline has passed.
    assert _expired(current, pages) == [4500.0, 4725.0, 6600.0, 6969.0]


@pytest.mark.asyncio
async def test_533_camp_for_all_trips_loses_its_monthly_period(db_session):
    result, rows = await replay(db_session, 533)

    assert result.get("held") is None
    periods = {row.category.value: row.period.value if row.period else None for row in rows}
    assert periods["camp"] is None  # "≈ 950 EUR / за всички"
    assert periods["transport"] == "monthly"  # "≈ 200 EUR / месец"


@pytest.mark.asyncio
async def test_564_yearly_fees_without_their_instalments(db_session):
    result, rows = await replay(db_session, 564)

    assert result.get("held") is None
    # "ЕДНОКРАТНО ПЛАЩАНЕ" under "Годишна такса обучение": yearly. The 2- and
    # 10-instalment plans' parts (3975, 827 a month) are listed under each fee.
    assert _tuition(rows) == [(6640.0, "yearly"), (6990.0, "yearly"), (7580.0, "yearly")]


@pytest.mark.asyncio
async def test_610_one_payment_is_the_yearly_fee(db_session):
    result, rows = await replay(db_session, 610)

    assert result.get("held") is None
    # "Плащане на 1 вноска" beside the 2- and 10-instalment plans, not "10 месечни
    # вноски"; the undated rows filed in lev are the same fees and go.
    assert _tuition(rows) == [(7400.0, "yearly"), (7400.0, "yearly"), (7650.0, "yearly"), (7800.0, "yearly")]
    assert {row.currency for row in rows} == {"EUR"}


@pytest.mark.asyncio
async def test_581_a_course_length_is_not_a_nursery_age(db_session):
    result, rows = await replay(db_session, 581)

    assert result.get("held") is None
    # "IGCSE 2 years in 1" is two years' course in one, not a fee for two-year-olds; the
    # hand-set yearly periods carry over to the fees read again.
    assert _tuition(rows) == [
        (16858.0, "yearly"), (17227.0, "yearly"), (17227.0, "yearly"),
        (17596.0, "yearly"), (17596.0, "yearly"), (19688.0, None),
    ]  # fmt: skip
    (materials,) = [row for row in rows if row.category == PriceCategory.MATERIALS and float(row.amount) == 738.0]
    # Stage 6 does not read the course length on the amount's own line as a nursery age.
    assert check_price_row(PriceRow.from_pricing(materials), materials.source_page.raw_markdown, "school") == []


@pytest.mark.asyncio
async def test_590_a_fee_paid_at_the_start_of_each_school_year_is_yearly(db_session):
    result, rows = await replay(db_session, 590, education_level="kindergarten")

    by_category = {(row.category.value, float(row.amount)): row.period for row in rows}
    assert by_category[("materials", 120.0)] == PricePeriod.YEARLY
    assert by_category[("tuition", 530.0)] == PricePeriod.MONTHLY
    assert all(row.currency == "EUR" for row in rows)
