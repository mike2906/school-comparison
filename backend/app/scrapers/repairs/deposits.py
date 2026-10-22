"""Repair command: flag stored registration fees that are deposits.

Rows the fees agent wrote before deposits were told apart, and rows a re-read keeps by rule 6
(351's "Първоначален Депозит"), are never rebuilt, so they need the flag from their own
stored words.
"""

from __future__ import annotations

import logging

from rich.console import Console
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pricing import PriceCategory, PriceSource, Pricing
from app.scrapers.extraction.agent_fees import AgentFee, names_deposit

logger = logging.getLogger(__name__)
console = Console()


def stored_row_names_deposit(row: Pricing) -> bool:
    """The stored row's label, plan name or evidence names a deposit, as for a new read."""
    context = row.pricing_context if isinstance(row.pricing_context, dict) else {}
    fee = AgentFee.model_construct(
        category=row.category,
        amount=float(row.amount) if row.amount is not None else None,
        amount_min=float(row.amount_min) if row.amount_min is not None else None,
        amount_max=float(row.amount_max) if row.amount_max is not None else None,
        plan_name=row.plan_name,
        plan_name_en=context.get("plan_name_en"),
        label=context.get("label") or "",
        evidence=context.get("evidence") or "",
        notes=context.get("notes"),
    )
    return names_deposit(fee)


async def flag_deposits(db: AsyncSession, *, apply: bool) -> list[Pricing]:
    """The agent's registration rows that name a deposit but are not flagged; with apply, flag them."""
    result = await db.execute(
        select(Pricing).where(
            Pricing.category == PriceCategory.REGISTRATION,
            Pricing.source == PriceSource.SCRAPED_WEBSITE,
        )
    )
    hits = [
        row
        for row in result.scalars().all()
        if isinstance(row.pricing_context, dict)
        and row.pricing_context.get("extractor") == "agent"
        and row.pricing_context.get("deposit") is not True
        and stored_row_names_deposit(row)
    ]
    if apply:
        for row in hits:
            row.pricing_context = {**row.pricing_context, "deposit": True}  # a new dict, so the JSON column saves
        await db.commit()
    return hits


async def flag_deposits_command(*, apply: bool) -> None:
    from app.database import async_session_maker

    async with async_session_maker() as db:
        hits = await flag_deposits(db, apply=apply)
        for row in sorted(hits, key=lambda row: (row.school_id, row.id)):
            label = row.pricing_context.get("label") or row.plan_name or ""
            console.print(f"  school {row.school_id} row {row.id}: {row.amount} {row.currency} {label}")
    state = "flagged" if apply else "would flag (dry run)"
    console.print(f"{len(hits)} deposit rows {state}")
