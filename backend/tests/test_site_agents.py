"""site-agents: which agents read which school, on one shared page cache."""

from __future__ import annotations

import contextlib

import pytest

from app.models import School
from app.scrapers.stages import fees_agent, school_facts, site_agents

pytestmark = pytest.mark.asyncio


class _Crawler:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@contextlib.asynccontextmanager
async def _nothing(*args, **kwargs):
    yield None


async def _add(db, school_type, status):
    school = School(
        country_code="bg",
        city="sofia",
        name_i18n={"bg": f"{school_type} {status}"},
        school_type=school_type,
        education_level="school",
        website_url="https://example.bg/",
        scrape_status=status,
    )
    db.add(school)
    await db.flush()
    return school.id


async def test_each_school_gets_the_agents_its_stage_would_give_it_on_one_cache(db_session, monkeypatch):
    import crawl4ai

    state_ok = await _add(db_session, "state", "validated")
    state_new = await _add(db_session, "state", "pending")
    private_ok = await _add(db_session, "private", "extracted")
    private_new = await _add(db_session, "private", "pending")
    await db_session.commit()
    caches: dict[int, list] = {}

    def stub(name):
        async def run(db, school, *, find, apply, **kwargs):
            await find(school)
            return {"school_id": school.id, "outcome": name}

        return run

    async def find_pages(crawler, run_config, client, school, spend, *, cache, **kwargs):
        caches.setdefault(school.id, []).append(cache)
        return []

    monkeypatch.setattr(crawl4ai, "AsyncWebCrawler", _Crawler)
    monkeypatch.setattr(site_agents, "fee_http_client", _nothing)
    monkeypatch.setattr(site_agents, "pipeline_run_heartbeat", _nothing)
    monkeypatch.setattr(site_agents, "find_pages", find_pages)
    monkeypatch.setattr(school_facts, "run_school", stub("facts"))
    monkeypatch.setattr(fees_agent, "run_school", stub("fees"))
    report = await site_agents.run_site_agents(db_session, city="sofia", workers=1)
    by_id = {r["school_id"]: {k for k in ("facts", "fees") if k in r} for r in report["results"]}
    assert by_id == {state_ok: {"facts"}, private_ok: {"facts", "fees"}, private_new: {"fees"}}
    assert state_new not in by_id
    first, second = caches[private_ok]
    assert first is second  # both finders of a school share its cache
    assert caches[private_new][0] is not first


async def test_a_failed_agent_leaves_the_other_one_running(db_session, monkeypatch):
    import crawl4ai

    private_ok = await _add(db_session, "private", "extracted")
    await db_session.commit()

    async def fail(db, school, **kwargs):
        raise RuntimeError("reader timed out")

    async def fees(db, school, **kwargs):
        return {"school_id": school.id, "outcome": "would_write"}

    monkeypatch.setattr(crawl4ai, "AsyncWebCrawler", _Crawler)
    monkeypatch.setattr(site_agents, "fee_http_client", _nothing)
    monkeypatch.setattr(site_agents, "pipeline_run_heartbeat", _nothing)
    monkeypatch.setattr(school_facts, "run_school", fail)
    monkeypatch.setattr(fees_agent, "run_school", fees)
    report = await site_agents.run_site_agents(db_session, school_ids=[private_ok], workers=1)
    (result,) = report["results"]
    assert result["facts"]["outcome"] == "error" and result["fees"]["outcome"] == "would_write"
