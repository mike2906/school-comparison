"""Exact, fail-closed provider request accounting for tracked pipeline runs."""

from __future__ import annotations

import asyncio
import contextvars
import datetime
import inspect
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Mapping, Optional
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


class ProviderCreditsExhausted(ProviderCostCapExceeded):
    """The provider account is out of credit or refused the key: no later call can succeed,
    so the run stops like a reached cap instead of failing every school one by one."""


class ProviderAttributionUncertain(ProviderCostError):
    pass


class ProviderResponseUnattributed(ProviderAttributionUncertain):
    """A response came back without exact cost or usage (e.g. an errored generation).

    Its ledger row is uncertain and counts toward the cap at the request reserve."""


@dataclass(frozen=True)
class ProviderCostScope:
    pipeline_run_id: str
    stage: str
    cap_usd: Decimal
    request_reserve_usd: Decimal
    # Calls in flight at once (1 for most stages). Reservations stay one at a time, so
    # each cap check counts every call still in flight at the request reserve.
    dispatch_slots: asyncio.Semaphore
    reserve_lock: asyncio.Lock
    # A model's own reserve where its calls have a known worst case (the fee reader caps its
    # output); any other model counts at request_reserve_usd.
    model_reserves: Mapping[str, Decimal] = field(default_factory=dict)


_SCOPE: contextvars.ContextVar[ProviderCostScope | None] = contextvars.ContextVar(
    "provider_cost_scope", default=None
)


@dataclass
class _PricedCall:
    """The requests a self-pricing model (the Anthropic route) made inside one billable call.
    A call that fails after every request came back (invalid output twice, a cut-off answer)
    is then booked at its exact cost, not at the request reserve."""

    started: int = 0
    responses: list[Any] = field(default_factory=list)


_PRICED_CALL: contextvars.ContextVar[_PricedCall | None] = contextvars.ContextVar("priced_call", default=None)


def note_priced_request_started() -> None:
    if (call := _PRICED_CALL.get()) is not None:
        call.started += 1


def note_priced_response(response: Any) -> None:
    if (call := _PRICED_CALL.get()) is not None:
        call.responses.append(response)


def _failed_call_attributions(call: _PricedCall) -> Optional[list[dict[str, Any]]]:
    """Exact attributions of the answers a failed call got back; None when it got none or
    the route does not price its responses. A request still in flight is not among them."""
    if not call.responses:
        return None
    try:
        return _attributions(call.responses)
    except ProviderAttributionUncertain:
        return None


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
    max_in_flight: int = 1,
    model_reserves: Optional[Mapping[str, float]] = None,
):
    cap = _usd(cap_usd, field="provider cost cap")
    reserve = _usd(request_reserve_usd, field="provider request reserve")
    per_model = {model: _usd(value, field="model request reserve") for model, value in (model_reserves or {}).items()}
    if cap <= 0 or reserve <= 0 or any(value <= 0 for value in per_model.values()):
        raise ProviderAttributionUncertain("provider cost cap and request reserve must be positive")
    token = _SCOPE.set(
        ProviderCostScope(
            pipeline_run_id,
            stage,
            cap,
            reserve,
            asyncio.Semaphore(max(1, int(max_in_flight))),
            asyncio.Lock(),
            per_model,
        )
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
    model_reserves: Optional[Mapping[str, Decimal]] = None,
) -> ProviderRequestLedger:
    """Persist a pre-dispatch reservation after checking spend against the cap.

    ``model_reserves`` gives a model's own reserve; others count at ``request_reserve_usd``."""
    cap = _usd(cap_usd, field="provider cost cap")
    default_reserve = _usd(request_reserve_usd, field="provider request reserve")
    reserves = {name: _usd(value, field="model request reserve") for name, value in (model_reserves or {}).items()}

    def reserve_for(name: str) -> Decimal:
        return reserves.get(name, default_reserve)

    reserve = reserve_for(model)
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
    # counts at its model's request reserve, its assumed maximum cost, so it does not block
    # the rest of the run but still counts toward the cap.
    uncertain = [row for row in existing if row.status != ProviderRequestStatus.ATTRIBUTED]
    uncertain_count = len(uncertain)
    uncertain_usd = sum((reserve_for(row.model) for row in uncertain), Decimal("0"))
    spend = sum(
        (
            _usd(row.provider_cost_usd, field="provider cost")
            for row in existing
            if row.status == ProviderRequestStatus.ATTRIBUTED
        ),
        Decimal("0"),
    ) + uncertain_usd
    if spend + reserve > cap:
        raise ProviderCostCapExceeded(
            f"provider cost cap would be exceeded: spent={spend} "
            f"(incl. {uncertain_count} uncertain at {uncertain_usd}) reserve={reserve} cap={cap}"
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
    return _attributions(_provider_responses(result))


def _attributions(responses: list[Any]) -> list[dict[str, Any]]:
    attributions: list[dict[str, Any]] = []
    for response in responses:
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
    keep_reservation: bool = False,
) -> list[ProviderRequestLedger]:
    """Attribute one agent dispatch as one ledger row per provider response.

    ``keep_reservation`` books every response on a row of its own and leaves the
    reservation row to the caller: a failed call's request still in flight stays on it."""
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
        row = reservation if index == 0 and not keep_reservation else ProviderRequestLedger(
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

    ``timeout_seconds`` bounds only the provider call, not the wait for a dispatch
    slot, so queued calls in a batch don't use up their timeout before dispatch.
    """
    scope = _SCOPE.get()
    if scope is None:
        return await _bounded(call, timeout_seconds)

    # A scope is shared by all tasks spawned inside the batch. With one slot (the
    # default) the dispatch-through-attribution interval is serialized, so every next
    # cap decision uses the preceding request's exact provider cost. With more, each
    # cap decision counts the calls still in flight at the request reserve.
    async with scope.dispatch_slots:
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

    async with scope.reserve_lock, async_session_maker() as ledger_db:
        reservation = await reserve_provider_request(
            ledger_db,
            pipeline_run_id=scope.pipeline_run_id,
            stage=stage or scope.stage,
            school_id=school_id,
            model=model,
            cap_usd=scope.cap_usd,
            request_reserve_usd=scope.request_reserve_usd,
            model_reserves=scope.model_reserves,
        )
    priced = _PricedCall()
    token = _PRICED_CALL.set(priced)
    try:
        result = await _bounded(call, timeout_seconds)
    except BaseException as exc:
        attributions = _failed_call_attributions(priced)
        # A request still out when the call failed (a timeout during a retry) may be billed:
        # it keeps the reservation, uncertain at the request reserve, beside the answers' rows.
        in_flight = attributions is None or len(priced.responses) < priced.started
        if in_flight:
            async with async_session_maker() as ledger_db:
                await mark_provider_request_uncertain(
                    ledger_db,
                    client_request_id=reservation.client_request_id,
                    reason=f"provider dispatch ended without exact attribution: {type(exc).__name__}",
                )
        if attributions:
            try:
                async with async_session_maker() as ledger_db:
                    await attribute_provider_responses(
                        ledger_db,
                        reservation_client_request_id=reservation.client_request_id,
                        attributions=attributions,
                        keep_reservation=in_flight,
                    )
            except ProviderAttributionUncertain:
                pass  # it marked the reservation uncertain
        raise
    finally:
        _PRICED_CALL.reset(token)

    try:
        attributions = extract_exact_provider_attributions(result)
    except ProviderAttributionUncertain as exc:
        async with async_session_maker() as ledger_db:
            await mark_provider_request_uncertain(
                ledger_db,
                client_request_id=reservation.client_request_id,
                reason="provider attribution missing or delayed after response",
            )
        raise ProviderResponseUnattributed(str(exc)) from exc
    try:
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
