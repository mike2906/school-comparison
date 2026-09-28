"""Exact, fail-closed provider request accounting for tracked pipeline runs."""

from __future__ import annotations

import asyncio
import contextvars
import datetime
import inspect
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline_run import (
    PipelineRun,
    PipelineStatus,
    ProviderRequestLedger,
    ProviderRequestStatus,
)


class ProviderCostError(RuntimeError):
    pass


class ProviderCostCapExceeded(ProviderCostError):
    pass


class ProviderAttributionUncertain(ProviderCostError):
    pass


@dataclass(frozen=True)
class ProviderCostScope:
    pipeline_run_id: str
    stage: str
    cap_usd: Decimal
    request_reserve_usd: Decimal
    dispatch_lock: asyncio.Lock


_SCOPE: contextvars.ContextVar[ProviderCostScope | None] = contextvars.ContextVar(
    "provider_cost_scope", default=None
)


def _usd(value: Any, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProviderAttributionUncertain(f"{field} is missing or invalid") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ProviderAttributionUncertain(f"{field} is missing or invalid")
    return parsed.quantize(Decimal("0.00000001"))


@contextmanager
def provider_cost_scope(
    *,
    pipeline_run_id: str,
    stage: str,
    cap_usd: float,
    request_reserve_usd: float,
):
    cap = _usd(cap_usd, field="provider cost cap")
    reserve = _usd(request_reserve_usd, field="provider request reserve")
    if cap <= 0 or reserve <= 0:
        raise ProviderAttributionUncertain("provider cost cap and request reserve must be positive")
    token = _SCOPE.set(
        ProviderCostScope(pipeline_run_id, stage, cap, reserve, asyncio.Lock())
    )
    try:
        yield
    finally:
        _SCOPE.reset(token)


async def reserve_provider_request(
    db: AsyncSession,
    *,
    pipeline_run_id: str,
    stage: str,
    school_id: Optional[int],
    model: str,
    cap_usd: Decimal | float,
    request_reserve_usd: Decimal | float,
    client_request_id: Optional[str] = None,
) -> ProviderRequestLedger:
    """Persist a pre-dispatch reservation after checking spend against the cap."""
    cap = _usd(cap_usd, field="provider cost cap")
    reserve = _usd(request_reserve_usd, field="provider request reserve")
    run = (
        await db.execute(
            select(PipelineRun).where(PipelineRun.id == pipeline_run_id).with_for_update()
        )
    ).scalar_one_or_none()
    if run is None or run.status != PipelineStatus.RUNNING:
        raise ProviderAttributionUncertain("pipeline run is missing or no longer running")

    existing = list(
        (
            await db.execute(
                select(ProviderRequestLedger).where(
                    ProviderRequestLedger.pipeline_run_id == pipeline_run_id
                )
            )
        ).scalars()
    )
    # A pending or uncertain request (e.g. a timeout; the provider may have billed it)
    # counts at the request reserve, its assumed maximum cost, so it does not block the
    # rest of the run but still counts toward the cap.
    uncertain_count = sum(
        row.status != ProviderRequestStatus.ATTRIBUTED for row in existing
    )
    spend = sum(
        (
            _usd(row.provider_cost_usd, field="provider cost")
            for row in existing
            if row.status == ProviderRequestStatus.ATTRIBUTED
        ),
        Decimal("0"),
    ) + reserve * uncertain_count
    if spend + reserve > cap:
        raise ProviderCostCapExceeded(
            f"provider cost cap would be exceeded: spent={spend} "
            f"(incl. {uncertain_count} uncertain at {reserve}) reserve={reserve} cap={cap}"
        )

    row = ProviderRequestLedger(
        client_request_id=client_request_id or str(uuid4()),
        pipeline_run_id=pipeline_run_id,
        stage=stage,
        school_id=school_id,
        model=model,
        status=ProviderRequestStatus.PENDING,
    )
    db.add(row)
    await db.commit()
    return row


async def mark_provider_request_uncertain(
    db: AsyncSession, *, client_request_id: str, reason: str
) -> ProviderRequestLedger:
    row = (
        await db.execute(
            select(ProviderRequestLedger).where(
                ProviderRequestLedger.client_request_id == client_request_id
            )
        )
    ).scalar_one()
    if row.status == ProviderRequestStatus.ATTRIBUTED:
        return row
    row.status = ProviderRequestStatus.UNCERTAIN
    row.uncertainty_reason = reason
    db.add(row)
    await db.commit()
    return row


async def _assert_provider_cost_cap(
    db: AsyncSession, *, pipeline_run_id: str
) -> None:
    """Fail after persisting exact evidence when one response crosses the run cap."""
    run = await db.get(PipelineRun, pipeline_run_id)
    raw_cap = (run.config or {}).get("provider_cost_cap_usd") if run is not None else None
    if raw_cap is None:
        return
    cap = _usd(raw_cap, field="provider cost cap")
    rows = list(
        (
            await db.execute(
                select(ProviderRequestLedger).where(
                    ProviderRequestLedger.pipeline_run_id == pipeline_run_id,
                    ProviderRequestLedger.status == ProviderRequestStatus.ATTRIBUTED,
                )
            )
        ).scalars()
    )
    total = sum(
        (_usd(row.provider_cost_usd, field="provider cost") for row in rows),
        Decimal("0"),
    )
    if total > cap:
        raise ProviderCostCapExceeded(
            f"provider cost cap exceeded after exact attribution: spent={total} cap={cap}"
        )


async def attribute_provider_request(
    db: AsyncSession,
    *,
    client_request_id: str,
    provider_request_id: Any,
    input_tokens: Any,
    output_tokens: Any,
    provider_cost_usd: Any,
    attributed_at: Optional[datetime.datetime] = None,
) -> ProviderRequestLedger:
    """Attach exact provider evidence; identical retries are idempotent."""
    row = (
        await db.execute(
            select(ProviderRequestLedger).where(
                ProviderRequestLedger.client_request_id == client_request_id
            )
        )
    ).scalar_one()
    request_id = str(provider_request_id or "").strip()
    try:
        input_count = int(input_tokens)
        output_count = int(output_tokens)
        cost = _usd(provider_cost_usd, field="provider-reported cost")
        if not request_id or input_count < 0 or output_count < 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError, ProviderAttributionUncertain) as exc:
        await mark_provider_request_uncertain(
            db,
            client_request_id=client_request_id,
            reason="provider request ID, token counts, or exact cost missing/delayed",
        )
        raise ProviderAttributionUncertain(
            "provider request ID, token counts, or exact cost missing/delayed"
        ) from exc

    evidence = (request_id, input_count, output_count, cost)
    prior = (
        row.provider_request_id,
        row.input_tokens,
        row.output_tokens,
        _usd(row.provider_cost_usd, field="provider cost")
        if row.provider_cost_usd is not None
        else None,
    )
    if row.status == ProviderRequestStatus.ATTRIBUTED:
        if prior != evidence:
            raise ProviderAttributionUncertain(
                "idempotent attribution disagrees with persisted evidence"
            )
        await _assert_provider_cost_cap(db, pipeline_run_id=row.pipeline_run_id)
        return row

    row.provider_request_id = request_id
    row.input_tokens = input_count
    row.output_tokens = output_count
    row.provider_cost_usd = cost
    row.status = ProviderRequestStatus.ATTRIBUTED
    row.uncertainty_reason = None
    row.attributed_at = attributed_at or datetime.datetime.now(datetime.timezone.utc)
    db.add(row)
    await db.commit()
    await _assert_provider_cost_cap(db, pipeline_run_id=row.pipeline_run_id)
    return row


def _provider_responses(result: Any) -> list[Any]:
    messages: list[Any] = []
    all_messages = getattr(result, "all_messages", None)
    if callable(all_messages):
        messages = list(all_messages() or [])
    responses = [
        message
        for message in messages
        if getattr(message, "provider_response_id", None)
        or isinstance(getattr(message, "provider_details", None), dict)
    ]
    response = getattr(result, "response", None)
    if not responses and response is not None:
        responses.append(response)
    return responses


def _usage_counts(usage: Any) -> tuple[Any, Any]:
    return (
        getattr(usage, "input_tokens", getattr(usage, "request_tokens", None)),
        getattr(usage, "output_tokens", getattr(usage, "response_tokens", None)),
    )


def extract_exact_provider_attributions(result: Any) -> list[dict[str, Any]]:
    """Extract exact identity, usage, cost, and time for every provider response."""
    attributions: list[dict[str, Any]] = []
    for response in _provider_responses(result):
        details = getattr(response, "provider_details", None)
        details = details if isinstance(details, dict) else {}
        request_id = getattr(response, "provider_response_id", None) or next(
            (
                details.get(key)
                for key in ("request_id", "id", "response_id", "generation_id")
                if details.get(key)
            ),
            None,
        )
        cost = details.get("cost")
        usage = getattr(response, "usage", None)
        if callable(usage):
            usage = usage()
        if inspect.isawaitable(usage):
            usage = None
        input_tokens, output_tokens = _usage_counts(usage)
        if request_id is None or cost is None or usage is None:
            raise ProviderAttributionUncertain(
                "provider request ID, per-request tokens, or exact cost is missing/delayed"
            )
        attributions.append(
            {
                "provider_request_id": request_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "provider_cost_usd": _usd(cost, field="provider-reported cost"),
                "attributed_at": getattr(response, "timestamp", None),
            }
        )
    if not attributions:
        raise ProviderAttributionUncertain(
            "provider request ID, per-request tokens, or exact cost is missing/delayed"
        )
    return attributions


def extract_exact_provider_attribution(result: Any) -> dict[str, Any]:
    """Extract one exact provider response, rejecting hidden multi-request aggregation."""
    attributions = extract_exact_provider_attributions(result)
    if len(attributions) != 1:
        raise ProviderAttributionUncertain(
            "multiple billable provider responses require per-request ledger rows"
        )
    return attributions[0]


async def attribute_provider_responses(
    db: AsyncSession,
    *,
    reservation_client_request_id: str,
    attributions: list[dict[str, Any]],
) -> list[ProviderRequestLedger]:
    """Attribute one agent dispatch as one ledger row per provider response."""
    if not attributions:
        raise ProviderAttributionUncertain("provider returned no attributable responses")
    reservation = (
        await db.execute(
            select(ProviderRequestLedger).where(
                ProviderRequestLedger.client_request_id == reservation_client_request_id
            )
        )
    ).scalar_one()
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for attribution in attributions:
        request_id = str(attribution.get("provider_request_id") or "").strip()
        if not request_id or request_id in seen_ids:
            await mark_provider_request_uncertain(
                db,
                client_request_id=reservation_client_request_id,
                reason="provider response IDs are missing or duplicated",
            )
            raise ProviderAttributionUncertain(
                "provider response IDs are missing or duplicated"
            )
        seen_ids.add(request_id)
        try:
            input_tokens = int(attribution.get("input_tokens"))
            output_tokens = int(attribution.get("output_tokens"))
            cost = _usd(
                attribution.get("provider_cost_usd"), field="provider-reported cost"
            )
            if input_tokens < 0 or output_tokens < 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError, ProviderAttributionUncertain) as exc:
            await mark_provider_request_uncertain(
                db,
                client_request_id=reservation_client_request_id,
                reason="provider token counts or exact cost are invalid",
            )
            raise ProviderAttributionUncertain(
                "provider token counts or exact cost are invalid"
            ) from exc
        normalized.append(
            {
                "provider_request_id": request_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "provider_cost_usd": cost,
                "attributed_at": attribution.get("attributed_at"),
            }
        )

    rows: list[ProviderRequestLedger] = []
    for index, attribution in enumerate(normalized):
        row = reservation if index == 0 else ProviderRequestLedger(
            client_request_id=str(uuid4()),
            pipeline_run_id=reservation.pipeline_run_id,
            stage=reservation.stage,
            school_id=reservation.school_id,
            model=reservation.model,
            requested_at=reservation.requested_at,
        )
        row.provider_request_id = attribution["provider_request_id"]
        row.input_tokens = attribution["input_tokens"]
        row.output_tokens = attribution["output_tokens"]
        row.provider_cost_usd = attribution["provider_cost_usd"]
        row.status = ProviderRequestStatus.ATTRIBUTED
        row.uncertainty_reason = None
        row.attributed_at = attribution.get("attributed_at") or datetime.datetime.now(
            datetime.timezone.utc
        )
        db.add(row)
        rows.append(row)
    await db.commit()
    await _assert_provider_cost_cap(db, pipeline_run_id=reservation.pipeline_run_id)
    return rows


async def execute_billable_request(
    call: Callable[[], Awaitable[Any]],
    *,
    model: str,
    school_id: Optional[int],
    stage: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
) -> Any:
    """Run a provider call with durable reservation and exact post-response attribution.

    ``timeout_seconds`` bounds only the provider call, not the wait for the dispatch
    lock, so queued calls in a batch don't use up their timeout before dispatch.
    """
    scope = _SCOPE.get()
    if scope is None:
        return await _bounded(call, timeout_seconds)

    # A scope is shared by all tasks spawned inside the batch. Serializing the
    # dispatch-through-attribution interval makes every next cap decision use
    # the preceding request's exact provider cost, and prevents ordinary batch
    # concurrency from colliding with its pending reservation.
    async with scope.dispatch_lock:
        return await _execute_tracked_billable_request(
            call,
            scope=scope,
            model=model,
            school_id=school_id,
            stage=stage,
            timeout_seconds=timeout_seconds,
        )


async def _bounded(call: Callable[[], Awaitable[Any]], timeout_seconds: Optional[float]) -> Any:
    if timeout_seconds is None:
        return await call()
    return await asyncio.wait_for(call(), timeout=timeout_seconds)


async def _execute_tracked_billable_request(
    call: Callable[[], Awaitable[Any]],
    *,
    scope: ProviderCostScope,
    model: str,
    school_id: Optional[int],
    stage: Optional[str],
    timeout_seconds: Optional[float] = None,
) -> Any:
    """Execute one serialized request within an active provider-cost scope."""

    from app.database import async_session_maker

    async with async_session_maker() as ledger_db:
        reservation = await reserve_provider_request(
            ledger_db,
            pipeline_run_id=scope.pipeline_run_id,
            stage=stage or scope.stage,
            school_id=school_id,
            model=model,
            cap_usd=scope.cap_usd,
            request_reserve_usd=scope.request_reserve_usd,
        )
    try:
        result = await _bounded(call, timeout_seconds)
    except BaseException as exc:
        async with async_session_maker() as ledger_db:
            await mark_provider_request_uncertain(
                ledger_db,
                client_request_id=reservation.client_request_id,
                reason=f"provider dispatch ended without exact attribution: {type(exc).__name__}",
            )
        raise

    try:
        attributions = extract_exact_provider_attributions(result)
        async with async_session_maker() as ledger_db:
            await attribute_provider_responses(
                ledger_db,
                reservation_client_request_id=reservation.client_request_id,
                attributions=attributions,
            )
    except ProviderAttributionUncertain:
        async with async_session_maker() as ledger_db:
            await mark_provider_request_uncertain(
                ledger_db,
                client_request_id=reservation.client_request_id,
                reason="provider attribution missing or delayed after response",
            )
        raise
    return result


async def reconcile_provider_cost(
    db: AsyncSession,
    *,
    pipeline_run_id: str,
    provider_total_cost_usd: Any,
    reconciled_at: Optional[datetime.datetime] = None,
) -> dict[str, Any]:
    """Persist the discrepancy between ledger-attributed spend and provider total."""
    run = await db.get(PipelineRun, pipeline_run_id)
    if run is None:
        raise ValueError(f"Unknown pipeline run {pipeline_run_id}")
    rows = list(
        (
            await db.execute(
                select(ProviderRequestLedger).where(
                    ProviderRequestLedger.pipeline_run_id == pipeline_run_id
                )
            )
        ).scalars()
    )
    ledger_total = sum(
        (
            _usd(row.provider_cost_usd, field="provider cost")
            for row in rows
            if row.status == ProviderRequestStatus.ATTRIBUTED
        ),
        Decimal("0"),
    )
    provider_total = _usd(provider_total_cost_usd, field="provider total")
    discrepancy = (provider_total - ledger_total).quantize(Decimal("0.00000001"))
    payload = {
        "ledger_total_cost_usd": float(ledger_total),
        "provider_total_cost_usd": float(provider_total),
        "discrepancy_usd": float(discrepancy),
        "attributed_requests": sum(row.status == ProviderRequestStatus.ATTRIBUTED for row in rows),
        "uncertain_requests": sum(row.status != ProviderRequestStatus.ATTRIBUTED for row in rows),
        "reconciled_at": (
            reconciled_at or datetime.datetime.now(datetime.timezone.utc)
        ).isoformat(),
    }
    metrics = dict(run.metrics or {})
    metrics["provider_cost_reconciliation"] = payload
    run.metrics = metrics
    db.add(run)
    await db.commit()
    return payload
