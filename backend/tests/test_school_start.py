"""Start times from a school's facts: the checks, the stages that store them, the API."""

from __future__ import annotations

import pytest

from app.models import School, SchoolLocation
from app.scrapers.extraction.school_start import Start, Starts, checked, clock_times, read_start_times
from app.scrapers.stages import school_start as stage
from app.utils.school_facts import publishable_school_facts
from app.utils.school_start import shown_start_times

HOME = "https://school.example/"
SHIFTS_BG = "Дневен режим: първа смяна – уроците са от 7:30 до 13:10 ч., втора смяна – от 13:50 до 19:30 ч."


def _fact(bg, topic="school_day", quote=None):
    return {
        "topic": topic,
        "text_i18n": {"bg": bg, "en": "A fact."},
        "source_url": HOME,
        "quote": quote or bg,
        "rank": "key",
    }


def _block(facts, **extra):
    return {"_schema_version": 1, "read_at": "t", "website_url": HOME, "facts": facts} | extra


class _Calls:
    def __init__(self, *starts):
        self.starts, self.messages = list(starts), []

    async def __call__(self, prompt, schema, message, school_id):
        assert schema is Starts
        self.messages.append(message)
        return Starts(starts=self.starts)


def test_clock_times_reads_the_ways_a_page_writes_a_time_but_not_a_date():
    assert clock_times("от 7.30 до 13,10 ч., втори час 08:20") == {"07:30", "13:10", "08:20"}
    assert clock_times("от 01.09.2026 г.") == set()
    assert clock_times("12 СУ, 3 смени") == set()


def test_a_time_counts_only_when_its_fact_writes_it_and_it_is_a_school_start():
    facts = [_fact(SHIFTS_BG), _fact("Детската градина работи от 7:00 до 19:00 часа.")]
    answer = Starts(
        starts=[
            Start(band="grade_1_4", time="7:30", fact=1),
            Start(band="grade_1_4", time="13:50", fact=1),  # the band's first answer stays
            Start(band="grade_5_7", time="08:00", fact=1),  # not in its fact
            Start(band="grade_8_12", time="19:30", fact=1),  # the end of the day, not a start
            Start(band="preschool", time="07:30", fact=3),  # no such fact
        ]
    )
    assert checked(answer, facts) == {"grade_1_4": {"time": "07:30", "fact_bg": SHIFTS_BG}}


@pytest.mark.asyncio
async def test_no_call_when_no_school_day_fact_writes_a_time():
    calls = _Calls()
    facts = [_fact("Училището работи на една смяна."), _fact("Обяд в 12:30.", topic="meals")]
    assert await read_start_times(facts, calls, 7) == {}
    assert calls.messages == []


@pytest.mark.asyncio
async def test_the_reader_sees_each_fact_with_its_page_line():
    calls = _Calls(Start(band="grade_1_4", time="07:30", fact=1))
    starts = await read_start_times([_fact("Учебният ден започва в 7:30 ч.", quote="Начало: 7.30 ч.")], calls, 7)
    assert starts["grade_1_4"]["time"] == "07:30"
    assert "1. Учебният ден започва в 7:30 ч.\n   Page line: Начало: 7.30 ч." in calls.messages[0]


def test_a_start_time_shows_only_while_its_fact_shows():
    fact = _fact(SHIFTS_BG)
    starts = {"grade_1_4": {"time": "07:30", "fact_bg": SHIFTS_BG}, "grade_8_12": {"time": "7:3", "fact_bg": SHIFTS_BG}}
    attrs = {"school_facts": _block([fact], start_times=starts)}
    assert shown_start_times(attrs, publishable_school_facts(attrs, HOME)) == {"grade_1_4": "07:30"}
    # The facts were read again and the fact is gone, or they no longer show (site moved).
    reread = {"school_facts": _block([_fact("Учебният ден започва в 8:00 ч.")], start_times=starts)}
    assert shown_start_times(reread, publishable_school_facts(reread, HOME)) is None
    assert shown_start_times(attrs, publishable_school_facts(attrs, "https://moved.example/")) is None


def _school(**attrs):
    return School(
        name_i18n={"bg": "Училище", "en": "School"},
        country_code="bg",
        school_type="state",
        education_level="secondary",
        city="sofia",
        website_url=HOME,
        scrape_status="extracted",
        attributes=attrs,
    )


@pytest.mark.asyncio
async def test_the_stage_fills_in_start_times_once_and_dry_run_writes_nothing(db_session):
    facts = [_fact(SHIFTS_BG), _fact("Обяд.", topic="meals")]
    school, plain = _school(other=1, school_facts=_block(facts)), _school(school_facts=_block([_fact("Обяд.")]))
    db_session.add_all([school, plain])
    await db_session.commit()
    calls = _Calls(Start(band="grade_1_4", time="07:30", fact=1))

    report = await stage.run_school_start(db_session, call=calls)
    assert report["outcomes"] == {"found": 1} and "start_times" not in school.attributes["school_facts"]

    await stage.run_school_start(db_session, call=calls, apply=True)
    block = school.attributes["school_facts"]
    assert block["start_times"] == {"grade_1_4": {"time": "07:30", "fact_bg": SHIFTS_BG}}
    assert block["facts"] == facts and school.attributes["other"] == 1
    assert len(calls.messages) == 2  # the school with no time on its school day is never asked

    report = await stage.run_school_start(db_session, call=calls, apply=True)
    assert report["schools"] == 0  # stored already


@pytest.mark.asyncio
async def test_the_list_shows_the_start_times_of_shown_facts(seeded_db, seeded_client):
    starts = {"grade_1_4": {"time": "07:30", "fact_bg": SHIFTS_BG}}
    school = _school(school_facts=_block([_fact(SHIFTS_BG)], start_times=starts))
    seeded_db.add(school)
    await seeded_db.flush()
    seeded_db.add(SchoolLocation(school_id=school.id, address_i18n={"bg": "София"}, lat=42.7, lng=23.3, is_primary=True))
    await seeded_db.commit()
    rows = (await seeded_client.get("/schools")).json()
    listed = next(row for row in rows if row["id"] == school.id)
    assert listed["start_times"] == {"grade_1_4": "07:30"}
    assert "fact_bg" not in str(listed) and all("start_times" in row for row in rows)
