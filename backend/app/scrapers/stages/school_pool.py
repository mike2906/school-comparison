"""Run a per-school step for many schools, a few at a time (the site agents' batch loop).

Each school gets its own database session: one ``AsyncSession`` is not safe across tasks.
With one worker the caller's session is used, as before workers existed. A stop exception
(a spend cap, a run no longer running) from any school stops new schools from starting;
schools already in flight finish. Schools on one site (an umbrella domain with a page per
school) run in one worker, one after another, so no site gets two of our crawls at once.
Plan: ``docs/SITE_AGENT_RUNS.md``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.scrapers.shared_site_check import registrable_domain, site_id

logger = logging.getLogger(__name__)

Work = Callable[[AsyncSession, int], Awaitable[dict[str, Any]]]


def site_key(url: Optional[str]) -> Optional[str]:
    """Schools whose crawls can reach the same site share a key: a domain of its own under
    any ending when its name has 5+ letters (meridian22.org and meridian22.bg, as the fees
    finder's ``_same_site``), else the host, or the tenant on a path-hosted platform
    (sites.google.com/view/x). Hosts on a shared domain (dg42.sofia.bg) keep their own."""
    site = site_id(url)
    if not site:
        return None
    name = site.split(".", 1)[0]
    return name if site == registrable_domain(url) and len(name) >= 5 else site


async def run_schools(
    db: AsyncSession,
    school_ids: Sequence[int],
    work: Work,
    *,
    workers: int = 1,
    stop_on: tuple[type[BaseException], ...] = (),
    on_result: Optional[Callable[[dict[str, Any]], None]] = None,
    session_factory: Callable[[], Any] = async_session_maker,
    sites: Optional[Mapping[int, Optional[str]]] = None,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """``work(session, school_id)`` for each id; returns the results in id order and why
    the run stopped early (None when every school ran). ``stop_on`` exceptions end the run;
    any other error is that school's ``error`` result. ``sites`` maps a school id to its
    website, to keep schools on one site in one worker. ``on_result`` gets results in the
    order of ``school_ids``, each once every school before it is done, so a resume after the
    last one reported skips no school."""
    workers = max(1, int(workers))
    done: dict[int, dict[str, Any]] = {}
    reported = 0
    stopped: Optional[str] = None
    groups: dict[Any, list[int]] = {}
    for school_id in school_ids:
        groups.setdefault(site_key((sites or {}).get(school_id)) or ("school", school_id), []).append(school_id)
    queue = list(groups.values())

    @asynccontextmanager
    async def session():
        if workers == 1:
            yield db
        else:
            async with session_factory() as own:
                yield own

    def report_ready() -> None:
        nonlocal reported
        while reported < len(school_ids) and school_ids[reported] in done:
            if on_result is not None:
                on_result(done[school_ids[reported]])
            reported += 1

    async def worker() -> None:
        nonlocal stopped
        async with session() as school_db:
            while queue and stopped is None:
                for school_id in queue.pop(0):
                    if stopped is not None:
                        return
                    try:
                        result = await work(school_db, school_id)
                    except stop_on as exc:
                        stopped = stopped or f"stopped before school {school_id}: {exc}"
                        return
                    except Exception as exc:  # one school's failure is that school's error result
                        logger.exception("school %s failed", school_id)
                        try:
                            if school_db.in_transaction():
                                await school_db.rollback()
                        except Exception:  # a dropped connection: the session starts over next school
                            logger.exception("rollback after school %s failed", school_id)
                        result = {"school_id": school_id, "outcome": "error", "reason": str(exc)[:200]}
                    done[school_id] = result
                    report_ready()

    try:
        async with asyncio.TaskGroup() as group:
            for _ in range(min(workers, len(queue))):
                group.create_task(worker())
    except Exception as exc:  # e.g. a session that cannot open: keep what the schools read
        logger.exception("school pool stopped")
        stopped = stopped or f"run stopped: {exc!r}"[:300]
    return [done[school_id] for school_id in school_ids if school_id in done], stopped
