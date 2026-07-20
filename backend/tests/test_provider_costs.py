from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.pipeline_run import ProviderRequestLedger, ProviderRequestStatus
from app.services.pipeline_runs import finalize_pipeline_run, start_pipeline_run
from app.services.provider_costs import (
    ProviderAttributionUncertain,
    ProviderCostCapExceeded,
    attribute_provider_request,
    attribute_provider_responses,
    extract_exact_provider_attributions,
    reconcile_provider_cost,
    reserve_provider_request,
)

pytestmark = pytest.mark.asyncio


async def _run(db_session):
    return await start_pipeline_run(
        db_session,
        country="bg",
        city="sofia",
        cli_stage="extract",
        config={"provider_cost_cap_usd": 1.0},
    )


async def _reserve(db_session, run, *, reserve=0.1, client_request_id=None):
    return await reserve_provider_request(
        db_session,
        pipeline_run_id=run.id,
        stage="extract",
        school_id=None,
        model="openrouter/test-model",
        cap_usd=1.0,
        request_reserve_usd=reserve,
        client_request_id=client_request_id,
    )


async def _attribute(db_session, row, *, request_id="req-1", cost="0.10000000"):
    return await attribute_provider_request(
        db_session,
        client_request_id=row.client_request_id,
        provider_request_id=request_id,
        input_tokens=100,
        output_tokens=20,
        provider_cost_usd=cost,
        attributed_at=datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc),
    )


async def test_provider_request_attribution_persists_exact_identity_usage_and_cost(db_session):
    run = await _run(db_session)
    row = await _reserve(db_session, run, client_request_id="client-1")

    attributed = await _attribute(db_session, row, request_id="provider-abc", cost="0.01234567")

    assert attributed.status == ProviderRequestStatus.ATTRIBUTED
    assert attributed.pipeline_run_id == run.id
    assert attributed.stage == "extract"
    assert attributed.model == "openrouter/test-model"
    assert attributed.provider_request_id == "provider-abc"
    assert attributed.input_tokens == 100
    assert attributed.output_tokens == 20
    assert Decimal(str(attributed.provider_cost_usd)) == Decimal("0.01234567")
    assert attributed.attributed_at is not None


async def test_provider_cost_reconciliation_persists_discrepancy(db_session):
    run = await _run(db_session)
    row = await _reserve(db_session, run)
    await _attribute(db_session, row, cost="0.40000000")

    payload = await reconcile_provider_cost(
        db_session,
        pipeline_run_id=run.id,
        provider_total_cost_usd="0.41000000",
        reconciled_at=datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc),
    )

    assert payload["ledger_total_cost_usd"] == 0.4
    assert payload["provider_total_cost_usd"] == 0.41
    assert payload["discrepancy_usd"] == 0.01
    assert payload["attributed_requests"] == 1
    assert payload["uncertain_requests"] == 0
    assert run.metrics["provider_cost_reconciliation"] == payload


async def test_exact_cap_is_allowed_then_next_dispatch_is_refused(db_session):
    run = await _run(db_session)
    first = await _reserve(db_session, run, reserve=1.0)
    await _attribute(db_session, first, cost="1.00000000")

    with pytest.raises(ProviderCostCapExceeded):
        await _reserve(db_session, run, reserve=0.00000001)


async def test_near_cap_refuses_pre_dispatch_reservation(db_session):
    run = await _run(db_session)
    first = await _reserve(db_session, run, reserve=0.95)
    await _attribute(db_session, first, cost="0.95000000")

    with pytest.raises(ProviderCostCapExceeded):
        await _reserve(db_session, run, reserve=0.1)


async def test_missing_or_delayed_attribution_blocks_following_dispatch(db_session):
    run = await _run(db_session)
    row = await _reserve(db_session, run)

    with pytest.raises(ProviderAttributionUncertain):
        await attribute_provider_request(
            db_session,
            client_request_id=row.client_request_id,
            provider_request_id=None,
            input_tokens=100,
            output_tokens=20,
            provider_cost_usd=None,
        )
    assert row.status == ProviderRequestStatus.UNCERTAIN
    with pytest.raises(ProviderAttributionUncertain):
        await _reserve(db_session, run)


async def test_provider_attribution_is_idempotent_and_rejects_disagreement(db_session):
    run = await _run(db_session)
    row = await _reserve(db_session, run)
    first = await _attribute(db_session, row, request_id="provider-idempotent", cost="0.1")
    second = await _attribute(db_session, row, request_id="provider-idempotent", cost="0.1")

    assert second.id == first.id
    with pytest.raises(ProviderAttributionUncertain):
        await _attribute(db_session, row, request_id="provider-different", cost="0.1")


async def test_pipeline_finalization_prefers_exact_ledger_over_summary_estimate(db_session):
    run = await _run(db_session)
    row = await _reserve(db_session, run)
    await _attribute(db_session, row, request_id="provider-final", cost="0.12345678")

    finalized = await finalize_pipeline_run(
        db_session,
        run,
        country="bg",
        city="sofia",
        stage_summaries=[
            {
                "processed": 1,
                "succeeded": 1,
                "failed": 0,
                "input_tokens": 999,
                "output_tokens": 999,
                "token_cost_usd": 9.0,
            }
        ],
    )

    assert finalized.total_llm_cost_usd == pytest.approx(0.123457)
    assert finalized.metrics["llm_usage"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "token_cost_usd": pytest.approx(0.123457),
    }
    assert finalized.metrics["provider_cost_ledger"] == {
        "requests": 1,
        "attributed": 1,
        "uncertain": 0,
    }


async def test_multi_response_dispatch_keeps_one_exact_row_per_provider_request(db_session):
    run = await _run(db_session)
    reservation = await _reserve(db_session, run)
    first_at = datetime(2026, 7, 20, 8, 0, tzinfo=timezone.utc)
    second_at = datetime(2026, 7, 20, 8, 1, tzinfo=timezone.utc)
    result = SimpleNamespace(
        all_messages=lambda: [
            SimpleNamespace(
                provider_response_id="provider-retry-1",
                provider_details={"cost": 0.01},
                usage=SimpleNamespace(input_tokens=10, output_tokens=2),
                timestamp=first_at,
            ),
            SimpleNamespace(
                provider_response_id="provider-retry-2",
                provider_details={"cost": 0.02},
                usage=SimpleNamespace(input_tokens=20, output_tokens=4),
                timestamp=second_at,
            ),
        ]
    )

    attributions = extract_exact_provider_attributions(result)
    rows = await attribute_provider_responses(
        db_session,
        reservation_client_request_id=reservation.client_request_id,
        attributions=attributions,
    )

    assert [row.provider_request_id for row in rows] == [
        "provider-retry-1",
        "provider-retry-2",
    ]
    assert [row.input_tokens for row in rows] == [10, 20]
    assert [Decimal(str(row.provider_cost_usd)) for row in rows] == [
        Decimal("0.01000000"),
        Decimal("0.02000000"),
    ]
    persisted = list(
        (
            await db_session.execute(
                select(ProviderRequestLedger).where(
                    ProviderRequestLedger.pipeline_run_id == run.id
                )
            )
        ).scalars()
    )
    assert len(persisted) == 2


async def test_invalid_later_response_does_not_persist_partial_attribution(db_session):
    run = await _run(db_session)
    reservation = await _reserve(db_session, run)

    with pytest.raises(ProviderAttributionUncertain):
        await attribute_provider_responses(
            db_session,
            reservation_client_request_id=reservation.client_request_id,
            attributions=[
                {
                    "provider_request_id": "provider-valid-first",
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "provider_cost_usd": "0.01",
                },
                {
                    "provider_request_id": "provider-invalid-second",
                    "input_tokens": -1,
                    "output_tokens": 4,
                    "provider_cost_usd": "0.02",
                },
            ],
        )

    persisted = list(
        (
            await db_session.execute(
                select(ProviderRequestLedger).where(
                    ProviderRequestLedger.pipeline_run_id == run.id
                )
            )
        ).scalars()
    )
    assert len(persisted) == 1
    assert persisted[0].provider_request_id is None
    assert persisted[0].status == ProviderRequestStatus.UNCERTAIN
