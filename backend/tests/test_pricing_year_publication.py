"""Year-aware pricing publication at the API serialization boundary.

Exercises the real detail query from ``SchoolService`` so the eager-load the publish gate
depends on is covered too: a forgotten ``source_page`` load would raise here rather than
silently withholding every price.
"""

from datetime import date, datetime

import pytest
from sqlalchemy import select

from app.models import Pricing, School, SchoolLocation, ScrapeType, SourcePage
from app.models.pricing import PriceSource
from app.schemas.school import SchoolResponse
from app.services.school_service import SchoolService
from app.utils.academic_year import current_academic_year


def _current_year() -> str:
    return current_academic_year(date.today())


def _previous_year() -> str:
    start = int(_current_year().split("/")[0]) - 1
    return f"{start}/{start + 1}"


async def _make_school(db_session, name: str) -> School:
    school = School(
        name_i18n={"bg": name, "en": name},
        country_code="bg",
        school_type="private",
        education_level="primary",
        city="sofia",
        scrape_status="extracted",
    )
    db_session.add(school)
    await db_session.flush()
    db_session.add(
        SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Тест 1", "en": "1 Test St"},
            lat=42.7,
            lng=23.3,
            is_primary=True,
        )
    )
    return school


async def _make_page(db_session, school: School, *, is_valid: bool = True) -> SourcePage:
    page = SourcePage(
        school_id=school.id,
        scrape_type=ScrapeType.WEBSITE,
        source_url="https://example.com/fees",
        content_hash=f"hash-{school.id}-{is_valid}",
        raw_markdown="Такси",
        is_valid=is_valid,
    )
    db_session.add(page)
    await db_session.flush()
    return page


def _price(school: School, page: SourcePage | None, **overrides) -> Pricing:
    row = dict(
        school_id=school.id,
        category="tuition",
        amount=500,
        currency="BGN",
        period="monthly",
        source=PriceSource.SCRAPED_WEBSITE,
        source_url="https://example.com/fees",
        source_page_id=page.id if page else None,
        scraped_at=datetime(2026, 9, 17, 9, 0),
        pricing_context={"confidence": 0.9},
    )
    row.update(overrides)
    return Pricing(**row)


async def _publish(db_session, school: School) -> dict:
    """Serialize through the same query the API uses."""
    query = SchoolService(db_session)._detail_query().where(School.id == school.id)
    loaded = (await db_session.execute(query)).scalar_one()
    return SchoolResponse.model_validate(loaded).model_dump()


@pytest.mark.asyncio
async def test_mixed_year_school_exposes_status_and_orders_newest_first(db_session):
    """The school 609 shape: prices stored for both the current and the previous year.

    Both publish, but each carries its own year, and the current one sorts first so a
    consumer taking the head of the list cannot pick up last year's fee.
    """
    school = await _make_school(db_session, "Mixed Year School")
    page = await _make_page(db_session, school)
    db_session.add_all(
        [
            _price(school, page, academic_year=_previous_year(), amount=400),
            _price(school, page, academic_year=_current_year(), amount=600),
        ]
    )
    await db_session.commit()

    rows = (await _publish(db_session, school))["pricing"]

    assert [row["year_status"] for row in rows] == ["current", "dated_other"]
    assert [row["academic_year_canonical"] for row in rows] == [
        _current_year(),
        _previous_year(),
    ]
    assert [float(row["amount"]) for row in rows] == [600.0, 400.0]


@pytest.mark.asyncio
async def test_historical_only_school_is_never_marked_current(db_session):
    """The school 171 regression: every stored row predates the current year.

    Its fees must publish as dated history, never as this year's price.
    """
    school = await _make_school(db_session, "Historical Only School")
    page = await _make_page(db_session, school)
    db_session.add_all(
        [
            _price(school, page, academic_year=_previous_year(), amount=400),
            _price(school, page, academic_year=_previous_year(), category="food", amount=100),
        ]
    )
    await db_session.commit()

    rows = (await _publish(db_session, school))["pricing"]

    assert len(rows) == 2
    assert {row["year_status"] for row in rows} == {"dated_other"}
    assert all(row["academic_year_canonical"] == _previous_year() for row in rows)


@pytest.mark.asyncio
async def test_undated_price_publishes_as_not_stated(db_session):
    school = await _make_school(db_session, "Undated School")
    page = await _make_page(db_session, school)
    db_session.add(_price(school, page, academic_year=None))
    await db_session.commit()

    rows = (await _publish(db_session, school))["pricing"]

    assert len(rows) == 1
    assert rows[0]["year_status"] == "not_stated"
    assert rows[0]["academic_year_canonical"] is None
    # The school's own wording stays available for auditability.
    assert rows[0]["source_url"] == "https://example.com/fees"


@pytest.mark.asyncio
async def test_undated_rows_sort_after_dated_rows(db_session):
    school = await _make_school(db_session, "Ordering School")
    page = await _make_page(db_session, school)
    db_session.add_all(
        [
            _price(school, page, academic_year=None, amount=100),
            _price(school, page, academic_year=_current_year(), amount=200),
        ]
    )
    await db_session.commit()

    rows = (await _publish(db_session, school))["pricing"]

    assert [row["year_status"] for row in rows] == ["current", "not_stated"]


@pytest.mark.asyncio
async def test_unbacked_and_invalidated_evidence_withhold_pricing(db_session):
    """Rule 5: an old price with no live backing stops being published."""
    unlinked = await _make_school(db_session, "Unlinked School")
    db_session.add(_price(unlinked, None, academic_year=_current_year()))

    invalidated = await _make_school(db_session, "Invalidated School")
    dead_page = await _make_page(db_session, invalidated, is_valid=False)
    db_session.add(_price(invalidated, dead_page, academic_year=_current_year()))
    await db_session.commit()

    assert (await _publish(db_session, unlinked))["pricing"] == []
    assert (await _publish(db_session, invalidated))["pricing"] == []


@pytest.mark.asyncio
async def test_unresolvable_academic_year_is_withheld(db_session):
    """A development-strategy span must not be published as a fee year."""
    school = await _make_school(db_session, "Bad Year School")
    page = await _make_page(db_session, school)
    db_session.add(_price(school, page, academic_year="2022-2027"))
    await db_session.commit()

    assert (await _publish(db_session, school))["pricing"] == []


@pytest.mark.asyncio
async def test_historical_price_stays_publishable_while_its_evidence_is_valid(db_session):
    """A 2025-26 fee is withheld only if its own page goes invalid.

    A later crawl that simply finds no newer pricing must not silently unpublish it.
    """
    school = await _make_school(db_session, "Surviving History School")
    page = await _make_page(db_session, school)
    db_session.add(_price(school, page, academic_year=_previous_year(), amount=650))
    await db_session.commit()

    rows = (await _publish(db_session, school))["pricing"]
    assert len(rows) == 1
    assert rows[0]["year_status"] == "dated_other"
    # It carries its real year, so the UI can label it rather than imply it is current.
    assert rows[0]["academic_year_canonical"] == _previous_year()

    # Now the crawler invalidates the page that backs it.
    page.is_valid = False
    await db_session.commit()

    assert (await _publish(db_session, school))["pricing"] == []


@pytest.mark.asyncio
async def test_historical_and_current_publish_together_with_distinct_status(db_session):
    """After a newer year is extracted, both years publish, each labelled correctly."""
    school = await _make_school(db_session, "Two Year School")
    page = await _make_page(db_session, school)
    db_session.add_all(
        [
            _price(school, page, academic_year=_previous_year(), amount=650),
            _price(school, page, academic_year=_current_year(), amount=800),
        ]
    )
    await db_session.commit()

    rows = (await _publish(db_session, school))["pricing"]
    assert [(row["year_status"], row["academic_year_canonical"]) for row in rows] == [
        ("current", _current_year()),
        ("dated_other", _previous_year()),
    ]


@pytest.mark.asyncio
async def test_unstated_period_publishes_as_null_through_the_api(db_session):
    """School 615's food fee: a supported amount whose period the page never states.

    It must publish with ``period`` null — not be dropped, and not be given a guessed
    period to satisfy the schema.
    """
    school = await _make_school(db_session, "Unstated Period School")
    page = await _make_page(db_session, school)
    db_session.add(_price(school, page, category="food", amount=88, currency="EUR", period=None))
    db_session.add(_price(school, page, amount=500, currency="EUR", period="monthly"))
    await db_session.commit()

    rows = (await _publish(db_session, school))["pricing"]

    by_category = {row["category"]: row for row in rows}
    assert by_category["food"]["period"] is None
    assert float(by_category["food"]["amount"]) == 88.0
    # A stated period is unaffected.
    assert by_category["tuition"]["period"] == "monthly"
