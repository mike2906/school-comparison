import asyncio

import pytest

from app.scrapers.stages.school_pool import run_schools

pytestmark = pytest.mark.asyncio


class _Session:
    opened = 0

    async def __aenter__(self):
        _Session.opened += 1
        return self

    async def __aexit__(self, *_args):
        return None

    def in_transaction(self):
        return False


class Stop(Exception):
    pass


async def test_one_worker_uses_the_callers_session_in_id_order():
    seen = []

    async def work(db, school_id):
        seen.append((db, school_id))
        return {"school_id": school_id}

    results, stopped = await run_schools("caller", [3, 1, 2], work)
    assert seen == [("caller", 3), ("caller", 1), ("caller", 2)]
    assert [r["school_id"] for r in results] == [3, 1, 2] and stopped is None


async def test_workers_get_their_own_sessions_and_results_keep_the_given_order():
    _Session.opened = 0
    running = 0
    most = 0

    async def work(db, school_id):
        nonlocal running, most
        assert isinstance(db, _Session)
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.01 * (5 - school_id))  # later ids finish first
        running -= 1
        return {"school_id": school_id}

    lines = []
    results, stopped = await run_schools(
        "caller", [1, 2, 3, 4], work, workers=3, on_result=lines.append, session_factory=_Session
    )
    assert most == 3 and _Session.opened == 3
    assert [r["school_id"] for r in results] == [1, 2, 3, 4]
    assert [r["school_id"] for r in lines] == [1, 2, 3, 4] and stopped is None  # in order, for --after-id


async def test_an_unhandled_error_is_that_schools_error_and_the_rest_still_run():
    async def work(db, school_id):
        if school_id == 2:
            raise RuntimeError("connection reset")
        return {"school_id": school_id, "outcome": "ok"}

    results, stopped = await run_schools("caller", [1, 2, 3], work, workers=2, session_factory=_Session)
    assert [r["outcome"] for r in results] == ["ok", "error", "ok"] and stopped is None


async def test_a_stop_ends_the_run_and_no_new_school_starts():
    started = []

    async def work(db, school_id):
        started.append(school_id)
        if school_id == 2:
            raise Stop("cap")
        await asyncio.sleep(0.01)
        return {"school_id": school_id}

    results, stopped = await run_schools(
        "caller", [1, 2, 3, 4, 5], work, workers=2, stop_on=(Stop,), session_factory=_Session
    )
    assert stopped == "stopped before school 2: cap"
    assert 2 not in [r["school_id"] for r in results]
    assert started == [1, 2]  # 1 finishes; 3 never starts


def test_site_key_joins_a_domains_endings_and_keeps_shared_hosts_apart():
    from app.scrapers.stages.school_pool import site_key

    assert site_key("https://www.meridian22.org/") == site_key("https://meridian22.bg/sch") == "meridian22"
    assert site_key("https://dg42.sofia.bg/") != site_key("https://dg43.sofia.bg/")
    assert site_key(None) is None


async def test_schools_on_one_site_never_run_at_the_same_time():
    running: set[str] = set()
    clashes = []
    sites = {1: "https://alpha.bg/", 2: "https://alpha.bg/x", 3: "https://bravo.bg/", 4: "https://alpha.org/"}

    async def work(db, school_id):
        site = sites[school_id].split("/")[2].split(".")[0]
        if site in running:
            clashes.append(school_id)
        running.add(site)
        await asyncio.sleep(0.01)
        running.discard(site)
        return {"school_id": school_id}

    results, _ = await run_schools("caller", [1, 2, 3, 4], work, workers=4, sites=sites, session_factory=_Session)
    assert clashes == [] and [r["school_id"] for r in results] == [1, 2, 3, 4]
