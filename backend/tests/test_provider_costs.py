import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.pipeline_run import (
    PipelineRun,
    PipelineStatus,
    ProviderRequestLedger,
    ProviderRequestStatus,
)
from app.services.pipeline_runs import finalize_pipeline_run, start_pipeline_run
from app.services.provider_costs import (
    ProviderAttributionUncertain,
    ProviderCostCapExceeded,
    attribute_provider_request,
    attribute_provider_responses,
    execute_billable_request,
    extract_exact_provider_attributions,
    mark_provider_request_uncertain,
    provider_cost_scope,
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


async def test_exact_attribution_over_cap_is_persisted_and_fails_run(db_session):
    run = await _run(db_session)
    row = await _reserve(db_session, run, reserve=0.1)

    with pytest.raises(ProviderCostCapExceeded, match="after exact attribution"):
        await _attribute(db_session, row, cost="1.00000001")

    persisted_row = await db_session.get(ProviderRequestLedger, row.id)
    assert persisted_row.status == ProviderRequestStatus.ATTRIBUTED
    assert Decimal(str(persisted_row.provider_cost_usd)) == Decimal("1.00000001")

    finalized = await finalize_pipeline_run(
        db_session,
        run,
        country="bg",
        city="sofia",
        stage_summaries=[{"processed": 1, "succeeded": 1, "failed": 0}],
    )
    assert finalized.status == PipelineStatus.FAILED
    assert finalized.metrics["provider_cost_ledger"]["cap_exceeded"] is True
    assert finalized.error_summary == (
        "provider cost cap exceeded: spent=1.00000001 cap=1.0"
    )


async def test_near_cap_refuses_pre_dispatch_reservation(db_session):
    run = await _run(db_session)
    first = await _reserve(db_session, run, reserve=0.95)
    await _attribute(db_session, first, cost="0.95000000")

    with pytest.raises(ProviderCostCapExceeded):
        await _reserve(db_session, run, reserve=0.1)


async def test_billable_batch_concurrency_is_serialized_until_exact_attribution(monkeypatch):
    import app.database as database
    import app.services.provider_costs as provider_costs

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(database, "async_session_maker", lambda: _Session())
    reservation_count = 0

    async def _reserve(*_args, **_kwargs):
        nonlocal reservation_count
        reservation_count += 1
        return SimpleNamespace(client_request_id=f"client-{reservation_count}")

    monkeypatch.setattr(provider_costs, "reserve_provider_request", _reserve)
    monkeypatch.setattr(
        provider_costs,
        "extract_exact_provider_attributions",
        lambda _result: [{"provider_request_id": "mock"}],
    )
    monkeypatch.setattr(
        provider_costs, "attribute_provider_responses", lambda *_args, **_kwargs: _noop()
    )

    active = 0
    max_active = 0

    async def _noop():
        return None

    async def _call():
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return object()

    with provider_cost_scope(
        pipeline_run_id="run-1",
        stage="extract",
        cap_usd=1.0,
        request_reserve_usd=0.1,
    ):
        await asyncio.gather(
            execute_billable_request(_call, model="mock", school_id=1),
            execute_billable_request(_call, model="mock", school_id=2),
        )

    assert reservation_count == 2
    assert max_active == 1


async def test_uncertain_request_counts_at_reserve_and_later_dispatch_runs(db_session):
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

    # The uncertain call no longer blocks the run: the next dispatch is reserved.
    later = await _reserve(db_session, run)
    await _attribute(db_session, later, request_id="req-later", cost="0.01000000")
    assert later.status == ProviderRequestStatus.ATTRIBUTED


async def test_uncertain_requests_still_count_toward_the_cap(db_session):
    run = await _run(db_session)  # cap 1.0
    for _ in range(9):
        row = await _reserve(db_session, run, reserve=0.1)
        await mark_provider_request_uncertain(
            db_session, client_request_id=row.client_request_id, reason="timeout"
        )

    # 9 uncertain requests at the 0.1 reserve leave room for exactly one more.
    last = await _reserve(db_session, run, reserve=0.1)
    await mark_provider_request_uncertain(
        db_session, client_request_id=last.client_request_id, reason="timeout"
    )
    with pytest.raises(ProviderCostCapExceeded, match="10 uncertain"):
        await _reserve(db_session, run, reserve=0.1)


async def test_uncertain_and_exact_spend_are_combined_against_the_cap(db_session):
    run = await _run(db_session)
    exact = await _reserve(db_session, run, reserve=0.1)
    await _attribute(db_session, exact, cost="0.85000000")
    timed_out = await _reserve(db_session, run, reserve=0.1)
    await mark_provider_request_uncertain(
        db_session, client_request_id=timed_out.client_request_id, reason="timeout"
    )

    # 0.85 exact + 0.1 uncertain + 0.1 reserve > 1.0
    with pytest.raises(ProviderCostCapExceeded):
        await _reserve(db_session, run, reserve=0.1)


async def test_reconciliation_counts_only_exact_spend_with_uncertain_rows(db_session):
    run = await _run(db_session)
    timed_out = await _reserve(db_session, run)
    await mark_provider_request_uncertain(
        db_session, client_request_id=timed_out.client_request_id, reason="timeout"
    )
    exact = await _reserve(db_session, run)
    await _attribute(db_session, exact, cost="0.02000000")

    payload = await reconcile_provider_cost(
        db_session, pipeline_run_id=run.id, provider_total_cost_usd="0.03000000"
    )

    assert payload["ledger_total_cost_usd"] == 0.02
    assert payload["discrepancy_usd"] == 0.01
    assert payload["attributed_requests"] == 1
    assert payload["uncertain_requests"] == 1


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
        "cap_exceeded": False,
    }


@pytest.mark.parametrize(
    ("succeeded", "expected_status"),
    [(1, PipelineStatus.PARTIAL), (0, PipelineStatus.FAILED)],
)
async def test_pipeline_finalization_never_completes_with_uncertain_cost(
    db_session, succeeded, expected_status
):
    run = await _run(db_session)
    row = await _reserve(db_session, run)
    row.status = ProviderRequestStatus.UNCERTAIN
    row.uncertainty_reason = "provider cost was delayed"
    await db_session.commit()

    finalized = await finalize_pipeline_run(
        db_session,
        run,
        country="bg",
        city="sofia",
        stage_summaries=[
            {
                "processed": 1,
                "succeeded": succeeded,
                "failed": 1 - succeeded,
            }
        ],
    )

    assert finalized.status == expected_status
    assert finalized.error_summary == "provider cost attribution incomplete for 1 request(s)"
    assert finalized.metrics["provider_cost_ledger"]["uncertain"] == 1
    persisted = await db_session.get(PipelineRun, run.id)
    assert persisted.status == expected_status


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


async def test_timed_out_dispatch_is_uncertain_and_later_dispatches_still_run(
    monkeypatch, async_engine, db_session
):
    import app.database as database
    import app.services.provider_costs as provider_costs
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    monkeypatch.setattr(
        database,
        "async_session_maker",
        async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False),
    )
    request_ids = iter(["req-a", "req-b"])
    monkeypatch.setattr(
        provider_costs,
        "extract_exact_provider_attributions",
        lambda _result: [
            {
                "provider_request_id": next(request_ids),
                "input_tokens": 10,
                "output_tokens": 2,
                "provider_cost_usd": Decimal("0.01"),
            }
        ],
    )
    run = await start_pipeline_run(
        db_session,
        country="bg",
        city="sofia",
        cli_stage="extract",
        config={"provider_cost_cap_usd": 0.3},
    )

    async def _slow():
        await asyncio.sleep(1.0)

    async def _fast():
        return object()

    with provider_cost_scope(
        pipeline_run_id=run.id, stage="extract", cap_usd=0.3, request_reserve_usd=0.1
    ):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                execute_billable_request(_slow, model="mock", school_id=None), timeout=0.05
            )
        # The timed-out request no longer blocks the run.
        await execute_billable_request(_fast, model="mock", school_id=None)
        await execute_billable_request(_fast, model="mock", school_id=None)
        # A second timeout still dispatches (0.12 + 0.1 reserve), but then the
        # counted spend is 0.1 + 0.1 uncertain + 0.02 exact and 0.32 > the 0.3 cap.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                execute_billable_request(_slow, model="mock", school_id=None), timeout=0.05
            )
        with pytest.raises(ProviderCostCapExceeded):
            await execute_billable_request(_fast, model="mock", school_id=None)

    rows = list(
        (
            await db_session.execute(
                select(ProviderRequestLedger)
                .where(ProviderRequestLedger.pipeline_run_id == run.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    assert sorted(row.status.value for row in rows) == [
        "attributed",
        "attributed",
        "uncertain",
        "uncertain",
    ]
