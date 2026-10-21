"""flag-deposits: stored registration rows that name a deposit get the flag a new read sets."""

import pytest
from sqlalchemy import select

from app.models.pricing import PriceCategory, PricePeriod, PriceSource, Pricing
from app.scrapers.repairs.deposits import flag_deposits


def _row(school_id, category, amount, label, evidence, **context):
    return Pricing(
        school_id=school_id,
        category=category,
        amount=amount,
        currency="EUR",
        period=PricePeriod.ONE_TIME,
        source=PriceSource.SCRAPED_WEBSITE,
        pricing_context={"extractor": "agent", "label": label, "evidence": evidence, **context},
    )


@pytest.mark.asyncio
async def test_flags_only_stored_registration_rows_that_name_a_deposit(db_session, sample_schools):
    school_id = sample_schools[0].id
    rows = [
        # 351: kept by rule 6 from a read before deposits were told apart
        _row(
            school_id,
            PriceCategory.REGISTRATION,
            600,
            "Първоначален Депозит",
            "Първоначален Депозит в размер на 600€ се заплаща при подписване на Договора;",
        ),
        # the words before the amount make only the 500 a deposit
        _row(school_id, PriceCategory.REGISTRATION, 200, "Такса записване", "такса за записване 200 €, депозит 500 €"),
        _row(
            school_id,
            PriceCategory.REGISTRATION,
            500,
            "Депозит",
            "такса за записване 200 €, депозит 500 €",
            deposit=True,
        ),
        _row(school_id, PriceCategory.TUITION, 590, "Депозит", "депозит 590 €"),
    ]
    old_extractor = _row(school_id, PriceCategory.REGISTRATION, 300, "Депозит", "депозит 300 €")
    old_extractor.pricing_context = {"label": "Депозит", "evidence": "депозит 300 €"}
    db_session.add_all([*rows, old_extractor])
    await db_session.commit()

    dry = await flag_deposits(db_session, apply=False)
    assert [row.amount for row in dry] == [600]
    assert rows[0].pricing_context.get("deposit") is None

    await flag_deposits(db_session, apply=True)
    stored = {row.id: row for row in (await db_session.execute(select(Pricing))).scalars()}
    assert stored[rows[0].id].pricing_context["deposit"] is True
    assert stored[rows[0].id].pricing_context["label"] == "Първоначален Депозит"
    assert "deposit" not in stored[rows[1].id].pricing_context
    assert "deposit" not in stored[rows[3].id].pricing_context
    assert "deposit" not in stored[old_extractor.id].pricing_context
    assert await flag_deposits(db_session, apply=False) == []
