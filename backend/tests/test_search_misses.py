"""No-result search counter: daily counts of cleaned-up text, nothing personal, only repeats read back."""

from __future__ import annotations

import datetime

import pytest

from app.services import search_misses

DAY = datetime.date(2026, 10, 19)


def test_clean_query_drops_short_long_and_personal_text():
    assert search_misses.clean_query("  Tatkova   GRADINA ") == "tatkova gradina"
    assert search_misses.clean_query("ab") is None
    assert search_misses.clean_query("x" * 61) is None
    assert search_misses.clean_query("ivan@example.com") is None
    assert search_misses.clean_query("0888 123 456") is None
    assert search_misses.clean_query("https://school.bg") is None
    assert search_misses.clean_query(None) is None
    # School numbers are fine.
    assert search_misses.clean_query("119 СУ") == "119 су"


@pytest.mark.asyncio
async def test_only_text_seen_on_several_days_is_read_back(db_session):
    for day in (DAY, DAY - datetime.timedelta(days=1)):
        assert await search_misses.record_miss(db_session, "Busy School", today=day)
    await search_misses.record_miss(db_session, "busy school", today=DAY)
    await search_misses.record_miss(db_session, "my child ivan", today=DAY)
    # Older than the window.
    await search_misses.record_miss(db_session, "old miss", today=DAY - datetime.timedelta(days=40))
    await search_misses.record_miss(db_session, "old miss", today=DAY - datetime.timedelta(days=41))
    assert await search_misses.list_misses(db_session, today=DAY) == [{"query": "busy school", "searches": 3, "days": 2}]


@pytest.mark.asyncio
async def test_new_texts_stop_at_the_daily_cap(db_session, monkeypatch):
    monkeypatch.setattr(search_misses, "MAX_TEXTS_PER_DAY", 2)
    assert await search_misses.record_miss(db_session, "first", today=DAY)
    assert await search_misses.record_miss(db_session, "second", today=DAY)
    assert not await search_misses.record_miss(db_session, "third", today=DAY)
    # A text already counted that day still goes up.
    assert await search_misses.record_miss(db_session, "first", today=DAY)


@pytest.mark.asyncio
async def test_the_endpoint_never_fails_the_page(client):
    assert (await client.post("/search-misses", json={"q": "tatkovagradina"})).status_code == 204
    assert (await client.post("/search-misses", json={"q": "a@b.c"})).status_code == 204
    assert (await client.post("/search-misses", json={"q": "x" * 300})).status_code == 422
    assert (await client.get("/search-misses")).json() == []
