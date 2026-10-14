"""Run a per-school step for many schools, a few at a time (the site agents' batch loop).

Each school gets its own database session: one ``AsyncSession`` is not safe across tasks.
With one worker the caller's session is used, as before workers existed. A stop exception
(a spend cap, a run no longer running) from any school stops new schools from starting;
schools already in flight finish. Plan: ``docs/SITE_AGENT_RUNS.md``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker

DEFAULT_WORKERS = 4

Work = Callable[[AsyncSession, int], Awaitable[dict[str, Any]]]


async def run_schools(
    db: AsyncSession,
    school_ids: Sequence[int],
    work: Work,
    *,
    workers: int = 1,
    stop_on: tuple[type[BaseException], ...] = (),
    on_result: Optional[Callable[[dict[str, Any]], None]] = None,
    session_factory: Callable[[], Any] = async_session_maker,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """``work(session, school_id)`` for each id; returns the results in id order and why
    the run stopped early (None when every school ran). ``work`` handles a school's own
    failure; only ``stop_on`` exceptions end the run."""
    workers = max(1, int(workers))
    results: list[dict[str, Any]] = []
    stopped: Optional[str] = None
    queue = list(school_ids)

    @asynccontextmanager
    async def session():
        if workers == 1:
            yield db
        else:
            async with session_factory() as own:
                yield own

    async def worker() -> None:
        nonlocal stopped
        async with session() as school_db:
            while queue and stopped is None:
                school_id = queue.pop(0)
                try:
                    result = await work(school_db, school_id)
                except stop_on as exc:
                    stopped = stopped or f"stopped before school {school_id}: {exc}"
                    return
                results.append(result)
                if on_result is not None:
                    on_result(result)

    async with asyncio.TaskGroup() as group:  # an unexpected error cancels the other workers
        for _ in range(min(workers, len(queue))):
            group.create_task(worker())
    order = {school_id: n for n, school_id in enumerate(school_ids)}
    results.sort(key=lambda result: order.get(result.get("school_id"), len(order)))
    return results, stopped
