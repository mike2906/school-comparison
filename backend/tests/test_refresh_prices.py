"""scripts/refresh_prices.py: a prices-only refresh must not leave a school ``navigated``."""

from __future__ import annotations

import pytest

from app.models import School
from scripts.refresh_prices import _restore_statuses, _statuses


def _school(status: str) -> School:
    return School(
        name_i18n={"bg": "Тест"}, country_code="bg", city="sofia", school_type="private",
        education_level="primary", website_url="https://school.bg", scrape_status=status,
    )  # fmt: skip


@pytest.mark.asyncio
async def test_crawl_status_is_undone_after_navigation(db_session):
    """Left ``navigated``, a published school's website data and validation report are
    withheld by the API until a full extraction runs."""
    published, fresh = _school("summarized"), _school("navigated")
    db_session.add_all([published, fresh])
    await db_session.commit()
    before = await _statuses(db_session, [published.id, fresh.id])

    published.scrape_status = "navigated"  # what navigation does on a successful crawl
    await db_session.commit()
    await _restore_statuses(db_session, before)

    await db_session.refresh(published)
    await db_session.refresh(fresh)
    assert (published.scrape_status, fresh.scrape_status) == ("summarized", "navigated")
