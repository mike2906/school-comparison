"""Site-phones stage: a school shows the phone its own website gives, not the register's."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import School, SchoolLocation
from app.scrapers.stages import site_phones as stage

HOME = "https://school.example/"


def _fetcher(pages):
    async def fetch(url):
        return (url, pages[url]) if url in pages else None

    return fetch


async def _school(db, phone="02/824 11 70", website=HOME, primary=True, name="СУ Пример"):
    school = School(
        name_i18n={"bg": name},
        country_code="bg",
        school_type="state",
        education_level="school",
        city="sofia",
        website_url=website,
        scrape_status="validated",
    )
    db.add(school)
    await db.flush()
    db.add(
        SchoolLocation(
            school_id=school.id,
            address_i18n={"bg": "ул. Примерна 1"},
            phone=phone,
            is_primary=primary,
            location_tags=["source=moe"],
            geocode_meta={},
        )
    )
    await db.commit()
    return school


@pytest.mark.parametrize(
    "text, numbers",
    [
        ("Тел.: 02 987 65 43, моб. 0888 123 456", ["29876543", "888123456"]),
        ("Телефон: +359 2 987 6543; Факс: 02 987 6544", ["29876543"]),  # a fax is not a phone
        ("тел. 032 123 456", []),  # Пловдив: not a Sofia landline
    ],
)
def test_page_numbers(text, numbers):
    assert stage.page_numbers(text) == numbers


def test_stored_numbers_read_register_artefacts():
    assert stage.stored_numbers("02/0887070435; /0884305108") == ["887070435", "884305108"]
    assert stage.stored_numbers("02/824 11 70") == ["28241170"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page, outcome, phone",
    [
        ("<p>Тел.: 02 987 65 43, 0888 123 456, 0877 000 111</p>", "replaced", "02 987 6543; 0888 123 456"),
        ("<p>Тел.: 02 987 65 43 и 02/824-11-70</p>", "agrees", "02/824 11 70"),  # the register's is there
        ("<p>Пишете ни</p>", "no_site_phone", "02/824 11 70"),
    ],
)
async def test_the_site_phone_replaces_a_register_phone_the_site_does_not_give(db_session, page, outcome, phone):
    await _school(db_session)
    report = await stage.run_site_phones(db_session, fetch=_fetcher({HOME: page}), apply=True)
    assert report["outcomes"] == {outcome: 1}
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    assert row.phone == phone
    if outcome == "replaced":
        assert stage.PHONE_TAG in row.location_tags and "phone_was=02/824 11 70" in row.location_tags
        # A wrong-website undo puts the register's phone back.
        assert stage.restore_register_phone(row)
        assert row.phone == "02/824 11 70" and row.location_tags == ["source=moe"]


@pytest.mark.asyncio
async def test_shared_sites_and_one_number_on_two_sites_are_held_back(db_session):
    await _school(db_session, website=HOME, name="СУ Едно")
    await _school(db_session, website="https://www.school.example/kg", name="ДГ Едно")
    other = "https://other.example/"
    third = "https://third.example/"
    await _school(db_session, website=other, name="СУ Две")
    await _school(db_session, website=third, name="СУ Три")
    designer = "<p>Сайт: 0888 999 999</p>"
    pages = {HOME: designer, "https://www.school.example/kg": designer, other: designer, third: designer}
    report = await stage.run_site_phones(db_session, fetch=_fetcher(pages))
    assert report["outcomes"] == {"shared_site": 2, "phone_on_other_sites": 2}


@pytest.mark.asyncio
async def test_a_number_under_our_address_counts_on_a_shared_site(db_session):
    # Валдорф: the kindergarten and the school share a site that lists each building's number.
    await _school(db_session, website=HOME, name="ЧДГ Валдорф")
    await _school(db_session, website=HOME + "kg", name="ЧУ Валдорф")
    page = (
        "<p>ул. Александър фон Хумболт №7 1113 София Прием: 0893330312 Зам. директор 0894377903</p>"
        "<p>ул. Примерна №1 1421 София Прием и администрация: 0894935013</p>"
    )
    report = await stage.run_site_phones(db_session, fetch=_fetcher({HOME: page, HOME + "kg": page}))
    assert {r["outcome"] for r in report["results"]} == {"replace"}
    assert report["results"][0]["new_phone"] == "0894 935 013"


def test_page_numbers_skip_a_fax_and_a_deputy_but_keep_a_phone_and_fax():
    text = "директор-тел./факс: 02 945-07-11 Зам.-директор: 02 945-29-06, 02 845-26-82"
    assert stage.page_numbers(text) == ["29450711"]
    assert stage.page_numbers("Заместник директор +359 888 576 774") == ["888576774"]  # nothing else given


@pytest.mark.asyncio
async def test_a_reimport_keeps_the_sites_phone(db_session):
    from types import SimpleNamespace

    from app.scrapers.sources.base_adapter import BaseSourceAdapter

    await _school(db_session)
    await stage.run_site_phones(db_session, fetch=_fetcher({HOME: "<p>02 987 65 43</p>"}), apply=True)
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    incoming = SimpleNamespace(district=None, phone="02/824 11 70", location_tags=[], age_groups=[], is_primary=True)
    incoming.phone = "02/111 22 33"  # the register's phone changed meanwhile
    await BaseSourceAdapter._sync_source_location(SimpleNamespace(db=db_session), row, incoming)
    assert row.phone == "02 987 6543"
    assert "phone_was=02/111 22 33" in row.location_tags  # what an undo would put back


def test_two_numbers_in_a_row_stay_two_and_the_next_buildings_number_is_not_ours():
    assert stage.page_numbers("Тел: 02 824 11 70 0888 123 456") == ["28241170", "888123456"]
    page = "ул. Примерна №1 1421 София ул. Друга №5 Прием: 0894 935 013"
    assert stage.numbers_at(page, "ул. Примерна 1") == []
    assert stage.numbers_at(page, "ул. Друга 5") == ["894935013"]
    assert stage.numbers_at("ул. Друга № 5 А тел. 02 987 65 43", "ул. Друга 5а") == ["29876543"]


def test_a_host_of_many_sites_is_not_one_shared_site():
    assert stage._site_key("https://sites.google.com/view/dg12/home") == "sites.google.com/view/dg12"
    assert stage._site_key("https://www.tzarsimeon.bg/dgrosica/home") == "tzarsimeon.bg"


def test_a_recreated_location_carries_the_sites_phone():
    phone, tags = stage.carry_site_phone("02/111 22 33", ["source=moe"], "02 987 6543")
    assert phone == "02 987 6543"
    assert tags == ["source=moe", stage.PHONE_TAG, "phone_was=02/111 22 33"]
    assert stage.carry_site_phone("02/111 22 33", ["source=moe"], None) == ("02/111 22 33", ["source=moe"])


@pytest.mark.asyncio
async def test_a_one_school_run_holds_a_page_number_for_the_full_run(db_session):
    school = await _school(db_session)
    report = await stage.run_site_phones(
        db_session, school_ids=[school.id], fetch=_fetcher({HOME: "<p>Тел.: 02 987 65 43</p>"}), apply=True
    )
    assert report["outcomes"] == {"needs_full_run": 1}
    row = (await db_session.execute(select(SchoolLocation))).scalar_one()
    assert row.phone == "02/824 11 70"


@pytest.mark.asyncio
async def test_another_schools_number_is_skipped_but_our_second_one_counts(db_session):
    await _school(db_session, phone="02 987 6543", website="https://other.example/", name="СУ Друго")
    await _school(db_session)
    pages = {HOME: "<p>Тел.: 02 987 65 43, 0888 123 456</p>", "https://other.example/": "<p>Пишете ни</p>"}
    report = await stage.run_site_phones(db_session, fetch=_fetcher(pages))
    ours = next(r for r in report["results"] if r["phone"] == "02/824 11 70")
    assert ours["outcome"] == "replace" and ours["new_phone"] == "0888 123 456"


def test_an_outside_data_protection_officers_number_is_not_the_schools():
    text = (
        "Тел.: 02 987 65 43. Длъжностно лице по защита на личните данни: Снежана Иванова, "
        "dpo@responsaprevent.bg, Тел: 0885 103 173"
    )
    assert stage.page_numbers(text) == ["29876543"]


def test_every_listing_of_our_address_counts():
    # НЕМО-Бояна: the footer gives the main office's number after our address, the list ours.
    page = 'Адрес: кв. Бояна, бул. "Ал. Пушкин" № 63 тел. 0889 599 118 ... бул. "Александър Пушкин" 63 0898 790 549'
    assert stage.numbers_at(page, 'ул. "Александър Пушкин" № 63') == ["889599118", "898790549"]
