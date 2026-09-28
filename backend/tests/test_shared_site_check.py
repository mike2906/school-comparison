"""UF42(a): shared-site check after website discovery."""

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import School, SourcePage
from app.models.school import SchoolLocation
from app.models.scrape_log import ScrapeType
from app.scrapers import shared_site_check as ssc
from app.scrapers.shared_site_check import (
    KEEP,
    REPLACE,
    WITHHOLD,
    FetchedPage,
    Member,
    SiteReader,
    address_key,
    address_matches,
    decide_member,
    describes_level,
    run_shared_site_check,
    shared_groups,
    site_group_key,
    url_names_other_level,
)
from app.utils.website_data import WEBSITE_DATA_WITHHELD_KEY, website_data_is_publishable


def _matches(address: str, text: str) -> bool:
    key = address_key(address)
    assert key is not None, address
    return address_matches(key, ssc._tokens(text))


def fake_fetcher(pages: dict[str, FetchedPage]):
    async def fetch(url: str):
        return pages.get(url) or pages.get(url.rstrip("/")) or pages.get(url.rstrip("/") + "/")

    return fetch


def page(url: str, text: str, links: list[str] | None = None) -> FetchedPage:
    return FetchedPage(url=url, text=text, links=[(link, "") for link in (links or [])])


# ---------------------------------------------------------------------------
# Maple Bear shapes (kindergarten 556 on the school's campus site, school 596 on the hub)
# ---------------------------------------------------------------------------

HUB = "https://maplebear.bg/"
SCHOOL_CAMPUS = "https://sofia-school.maplebear.bg/"
KG_CAMPUS = "https://sofia-kindergarten.maplebear.bg/"
PLOVDIV_CAMPUS = "https://plovdiv-markovo.maplebear.bg/"
STORE = "https://store.maplebear.bg/"


def maple_bear_pages(*, kindergarten_campus_has_address: bool = True) -> dict[str, FetchedPage]:
    kg_text = "Maple Bear Kindergarten Vitosha. Детска градина Maple Bear."
    if kindergarten_campus_has_address:
        kg_text += " Contact: Maple Bear Kindergarten Vitosha 16 Jordan Stubel Street Sofia Bulgaria"
    return {
        HUB: page(
            HUB,
            # The national hub lists every campus, so its own text matches everyone.
            "Maple Bear Bulgaria. Find a school: Kindergarten, Elementary School, Middle School."
            " ул. Панорамен Път 38; 16 Jordan Stubel Street; Plovdiv, ул. Марково 5",
            [SCHOOL_CAMPUS, KG_CAMPUS, PLOVDIV_CAMPUS, STORE, "https://maplebear.bg/contact/"],
        ),
        SCHOOL_CAMPUS: page(
            SCHOOL_CAMPUS,
            "Maple Bear School. Elementary School, Grade 1 to Grade 7. Preschool program.",
            ["https://sofia-school.maplebear.bg/kontakti/"],
        ),
        "https://sofia-school.maplebear.bg/kontakti/": page(
            "https://sofia-school.maplebear.bg/kontakti/",
            "Maple Bear Камбаните ул. Витошки Камбани 9 София 1756. Maple Bear Бояна ул. Панорамен Път 38 София 1616",
        ),
        KG_CAMPUS: page(KG_CAMPUS, kg_text),
        PLOVDIV_CAMPUS: page(PLOVDIV_CAMPUS, "Maple Bear Plovdiv детска градина и основно училище, ул. Марково 5"),
        # A shop footer repeats an address but is not a campus.
        STORE: page(STORE, "Maple Bear store. 16 Jordan Stubel Street. Kindergarten uniforms"),
    }


def maple_members() -> tuple[Member, Member]:
    kindergarten = Member(
        school_id=556,
        name='"ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ" ООД',
        school_type="private",
        education_level="kindergarten",
        website_url="https://sofia-school.maplebear.bg/en/",
        registry_addresses=['кв. Витоша, ул. "Йордан Стубел" № 16'],
        city="sofia",
    )
    school = Member(
        school_id=596,
        name='"Частно основно училище Канадско мече" ООД',
        school_type="private",
        education_level="lower_secondary",
        website_url=HUB,
        registry_addresses=['ул. "Панорамен път" № 38'],
        city="sofia",
    )
    return kindergarten, school


@pytest.mark.asyncio
async def test_maple_bear_kindergarten_moves_from_school_site_to_its_campus():
    kindergarten, _ = maple_members()
    reader = SiteReader(fake_fetcher(maple_bear_pages()))

    decision = await decide_member(kindergarten, domain="maplebear.bg", reader=reader)

    assert decision.action == REPLACE
    assert decision.new_url == KG_CAMPUS
    assert decision.evidence["current_site"]["url_names_other_level"] is True
    assert STORE not in decision.evidence["campus_sites"]


@pytest.mark.asyncio
async def test_maple_bear_school_on_hub_moves_to_its_campus_even_though_hub_lists_its_address():
    _, school = maple_members()
    reader = SiteReader(fake_fetcher(maple_bear_pages()))

    decision = await decide_member(school, domain="maplebear.bg", reader=reader)

    assert decision.action == REPLACE
    assert decision.new_url == SCHOOL_CAMPUS
    assert decision.evidence["current_site_is_brand_hub"] is True


@pytest.mark.asyncio
async def test_maple_bear_kindergarten_withheld_when_no_campus_states_its_address():
    kindergarten, _ = maple_members()
    reader = SiteReader(fake_fetcher(maple_bear_pages(kindergarten_campus_has_address=False)))

    decision = await decide_member(kindergarten, domain="maplebear.bg", reader=reader)

    assert decision.action == WITHHOLD
    assert decision.reason == "url_names_other_level"


@pytest.mark.asyncio
async def test_hub_with_one_campus_in_the_members_city_is_still_a_hub():
    hub, sofia, plovdiv = "https://brand.bg/", "https://sofia.brand.bg/", "https://plovdiv.brand.bg/"
    pages = {
        hub: page(hub, "Brand. Основно училище. Sofia: ул. Липа 5. Plovdiv: ул. Бреза 7", [sofia, plovdiv]),
        sofia: page(sofia, "Brand Sofia. Основно училище, 1-7 клас. ул. Липа № 5"),
        plovdiv: page(plovdiv, "Brand Plovdiv. Основно училище. ул. Бреза 7"),
    }
    member = Member(1, "Brand", "private", "lower_secondary", hub, ['ул. "Липа" № 5'], city="sofia")
    decision = await decide_member(member, domain="brand.bg", reader=SiteReader(fake_fetcher(pages)))
    assert (decision.action, decision.new_url) == (REPLACE, sofia)


@pytest.mark.asyncio
async def test_hub_member_without_matching_campus_is_withheld_not_kept_on_hub():
    _, school = maple_members()
    pages = maple_bear_pages()
    pages["https://sofia-school.maplebear.bg/kontakti/"] = page(
        "https://sofia-school.maplebear.bg/kontakti/", "Maple Bear Камбаните ул. Витошки Камбани 9"
    )
    decision = await decide_member(school, domain="maplebear.bg", reader=SiteReader(fake_fetcher(pages)))

    assert decision.action == WITHHOLD
    assert decision.reason == "brand_hub_no_matching_campus"


# ---------------------------------------------------------------------------
# Generic sibling pair and legitimate multi-institution sites
# ---------------------------------------------------------------------------

def sibling(school_id, level, address, url="https://example-school.bg/"):
    return Member(
        school_id=school_id,
        name=f"Institution {school_id}",
        school_type="private",
        education_level=level,
        website_url=url,
        registry_addresses=[address],
        city="plovdiv",
    )


@pytest.mark.asyncio
async def test_kindergarten_loses_sibling_school_site_at_another_address():
    pages = {
        "https://example-school.bg/": page(
            "https://example-school.bg/",
            "Частно основно училище Пример. Прием в 1 клас. Адрес: гр. Пловдив, ул. Липа № 5",
        )
    }
    reader = SiteReader(fake_fetcher(pages))
    school = await decide_member(sibling(1, "lower_secondary", 'ул. "Липа" № 5'), domain="example-school.bg", reader=reader)
    kindergarten = await decide_member(sibling(2, "kindergarten", 'ул. "Бреза" № 7'), domain="example-school.bg", reader=reader)

    assert school.action == KEEP
    assert kindergarten.action == WITHHOLD
    assert kindergarten.reason == "registry_address_not_on_site"


@pytest.mark.asyncio
async def test_kindergarten_at_same_address_withheld_when_site_only_describes_the_school():
    pages = {
        "https://example-school.bg/": page(
            "https://example-school.bg/",
            "Средно училище Пример, 1-12 клас. Подготовка за училище. ул. Липа 5, Пловдив",
        )
    }
    reader = SiteReader(fake_fetcher(pages))
    kindergarten = await decide_member(sibling(2, "kindergarten", 'ул. "Липа" № 5'), domain="example-school.bg", reader=reader)

    assert kindergarten.action == WITHHOLD
    assert kindergarten.reason == "site_does_not_describe_level"


@pytest.mark.asyncio
async def test_combined_site_keeps_kindergarten_and_school_at_the_same_address():
    pages = {
        "https://example-school.bg/": page(
            "https://example-school.bg/",
            "Детска градина и основно училище Пример. ул. „Липа“ №5",
        )
    }
    reader = SiteReader(fake_fetcher(pages))
    for member in (sibling(1, "lower_secondary", 'ул. "Липа" № 5'), sibling(2, "kindergarten", 'ул. "Липа" № 5')):
        decision = await decide_member(member, domain="example-school.bg", reader=reader)
        assert decision.action == KEEP


@pytest.mark.asyncio
async def test_two_branches_listed_on_one_brand_site_both_keep_it():
    url = "https://nemo-example.com/"
    pages = {
        url: page(url, "Частна детска градина Немо", [url + "kontakti"]),
        url + "kontakti": page(
            url + "kontakti", "Клон Драгалевци: ул. Маточина 2а. Клон Бояна: ул. Александър Пушкин 63"
        ),
    }
    reader = SiteReader(fake_fetcher(pages))
    first = await decide_member(
        sibling(1, "kindergarten", 'ж. к. Драгалевци, ул. "Маточина" № 2а', url), domain="nemo-example.com", reader=reader
    )
    second = await decide_member(
        sibling(2, "kindergarten", 'ул. "Александър Пушкин" № 63', url), domain="nemo-example.com", reader=reader
    )

    assert (first.action, second.action) == (KEEP, KEEP)


@pytest.mark.asyncio
async def test_directory_listing_is_withheld():
    url = "https://spravochnik.framar.bg/obrazovanie/3401/detska-gradina-17"
    member = sibling(1, "kindergarten", 'ул. "Липа" № 5', url)
    decision = await decide_member(member, domain="framar.bg", reader=SiteReader(fake_fetcher({})))
    assert decision.action == WITHHOLD
    assert decision.reason == "directory_listing_not_official_site"


@pytest.mark.asyncio
async def test_unreachable_site_without_stored_pages_is_withheld():
    decision = await decide_member(
        sibling(1, "kindergarten", 'ул. "Липа" № 5'), domain="example-school.bg", reader=SiteReader(fake_fetcher({}))
    )
    assert decision.action == WITHHOLD


@pytest.mark.asyncio
async def test_vague_registry_address_is_withheld():
    decision = await decide_member(
        sibling(1, "kindergarten", "гр. София, ж.к. Толстой,"), domain="example-school.bg", reader=SiteReader(fake_fetcher({}))
    )
    assert decision.reason == "registry_address_too_vague"


# ---------------------------------------------------------------------------
# Building blocks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "address,text,expected",
    [
        ('кв. Витоша, ул. "Йордан Стубел" № 16', "16 Jordan Stubel Street, Sofia", True),
        ('кв. Витоша, ул. "Йордан Стубел" № 16', "ул. Йордан Стубел 18, София", False),
        ('ул. "Панорамен път" № 38', "Maple Bear Бояна ул. Панорамен Път 38 София 1616", True),
        ('ул. "Панорамен път" № 38', "38 Panoramen Pat St.", True),
        ('ж. к. Обеля 2, ул. "106" № 3', "Адрес: ж.к. Обеля 2, ул. 106, №3", True),
        ('гр. София, ул. "Атанас Манчев", №1', "ул. „Атанас Манчев“ № 1", True),
        ("ж.к. Люлин-9, бл. 964", "ж.к. Люлин 9, до бл. 964", True),
        ("ж. к. Люлин 6", "Ерих Кестнер, жк Люлин 6, София", True),
        ('с. Бусманци, ул. "Крива ливада"', "с. Бусманци, ул. Крива ливада 11А", True),
        ('ул. "Александър Пушкин" № 63', "бул. Александър Малинов 63", False),
    ],
)
def test_address_matching(address, text, expected):
    assert _matches(address, text) is expected


@pytest.mark.parametrize("address", ["гр. София, ж.к. Толстой,", "", "гр. София"])
def test_vague_addresses_have_no_key(address):
    assert address_key(address) is None


def test_level_terms():
    assert describes_level("Частна детска градина", "kindergarten")
    assert not describes_level("Подготовка за училище, preschool", "school")
    assert describes_level("Прием в 1 - 7 клас", "school")
    assert not describes_level("Детска градина. Подготовка за 1 клас, готови за 1-ви клас", "school")
    assert describes_level("Grades 1-12", "school")
    assert describes_level("Elementary School, Grade 1", "school")
    assert not describes_level("Maple Bear School, Preschool program", "kindergarten")


def test_url_level_words_only_count_outside_the_registrable_domain():
    assert url_names_other_level("https://sofia-school.maplebear.bg/en/", "kindergarten", "maplebear.bg")
    assert not url_names_other_level("https://sofia-kindergarten.maplebear.bg/", "kindergarten", "maplebear.bg")
    assert not url_names_other_level("https://britanica-parkschool.bg/", "kindergarten", "britanica-parkschool.bg")
    assert url_names_other_level("https://example.bg/detska-gradina/", "school", "example.bg")
    assert not url_names_other_level("https://brand.bg/preschool/", "kindergarten", "brand.bg")
    assert url_names_other_level("https://brand.bg/pre-school/", "school", "brand.bg")


def test_platform_tenant_pages_group_with_their_site_root():
    assert site_group_key("https://sites.google.com/view/foo") == site_group_key(
        "https://sites.google.com/view/foo/contact"
    )
    assert site_group_key("https://sites.google.com/view/foo") != site_group_key("https://sites.google.com/view/bar")
    assert ssc.site_id("https://sites.google.com/126ou.net/new/kontakti") == "sites.google.com/126ou.net/new"
    assert ssc.site_id("https://sites.google.com/a/school.bg/one/home") == "sites.google.com/a/school.bg/one"
    assert ssc.site_id("https://sites.google.com/a/school.bg/two") == "sites.google.com/a/school.bg/two"


@pytest.mark.asyncio
async def test_platform_front_page_is_never_treated_as_a_brand_hub(db_session):
    for idx in (1, 2):
        school = School(
            name_i18n={"bg": f"P{idx}"}, country_code="bg", city="sofia", school_type="state",
            education_level="lower_secondary", website_url="https://sites.google.com/", scrape_status="extracted",
        )
        school.locations = [SchoolLocation(address_i18n={"bg": 'ул. "Липа" № 5'}, is_primary=True, location_tags=[])]
        db_session.add(school)
    await db_session.commit()
    hub = page(
        "https://sites.google.com/",
        "Google Sites",
        ["https://sofia-school.sites.google.com/", "https://other-school.sites.google.com/"],
    )
    fetched: list[str] = []

    async def fetch(url):
        fetched.append(url)
        return hub if url.rstrip("/") == "https://sites.google.com" else None

    report = await run_shared_site_check(db_session, dry_run=True, fetcher=fetch)
    assert [m["action"] for g in report["groups"] for m in g["members"]] == [WITHHOLD, WITHHOLD]
    assert not any("sofia-school" in url for url in fetched)


def test_platform_with_unknown_tenant_prefix_groups_conservatively(monkeypatch):
    monkeypatch.setattr(ssc, "_PATH_TENANT_SEGMENTS", {})
    assert site_group_key("https://sites.google.com/view/foo") == site_group_key("https://sites.google.com/view/bar")


@pytest.mark.asyncio
async def test_platform_tenants_do_not_share_evidence():
    stored = {
        ssc.site_id("https://sites.google.com/view/tenant-a/kontakti"): [
            "Основно училище А, 1 - 7 клас. ул. Липа № 5"
        ]
    }
    reader = SiteReader(fake_fetcher({}), stored)
    tenant_b = sibling(2, "lower_secondary", 'ул. "Липа" № 5', "https://sites.google.com/view/tenant-b")
    decision = await decide_member(tenant_b, domain="", reader=reader)
    assert decision.action == WITHHOLD
    assert await reader.site_texts("https://sites.google.com/view/tenant-a") == stored[
        "sites.google.com/view/tenant-a"
    ]


def test_path_hosted_platform_sites_are_not_one_shared_site():
    first = site_group_key("https://sites.google.com/view/89ousofia")
    second = site_group_key("https://sites.google.com/view/school-vakarel")
    assert first != second
    assert site_group_key("https://sofia-school.maplebear.bg/en/") == site_group_key("https://maplebear.bg/")


def test_shared_groups_need_two_members():
    members = [sibling(1, "kindergarten", "ул. Липа 5"), sibling(2, "primary", "ул. Липа 5"),
               sibling(3, "primary", "ул. Липа 5", "https://other.bg/")]
    assert list(shared_groups(members)) == ["example-school.bg"]


def _location(address, tags=()):
    return SimpleNamespace(address_i18n={"bg": address}, location_tags=list(tags), is_primary=True, id=1)


def test_website_derived_address_is_not_evidence_unless_officially_confirmed():
    school = SimpleNamespace(
        id=1, name_i18n={"bg": "X"}, school_type="private", education_level="kindergarten",
        website_url="https://x.bg", city="sofia",
        locations=[_location("ул. Липа 5", ["address_source=website_contact"])],
    )
    member = ssc._member_from_school(school)
    assert member.registry_addresses == []
    assert member.website_derived_addresses == ["ул. Липа 5"]

    school.locations = [_location("ул. Липа 5", ["address_source=website_contact", "coords_source=sofia_municipal"])]
    assert ssc._member_from_school(school).registry_addresses == ["ул. Липа 5"]


# ---------------------------------------------------------------------------
# Database: dry run writes nothing; apply uses the existing withholding state
# ---------------------------------------------------------------------------

async def _seed(db_session):
    rows = {}
    for key, level, url, address in (
        ("kg", "kindergarten", "https://sofia-school.maplebear.bg/en/", 'кв. Витоша, ул. "Йордан Стубел" № 16'),
        ("school", "lower_secondary", HUB, 'ул. "Панорамен път" № 38'),
        ("pair_kg", "kindergarten", "https://example-school.bg/", 'ул. "Бреза" № 7'),
        ("pair_school", "lower_secondary", "https://example-school.bg/", 'ул. "Липа" № 5'),
    ):
        school = School(
            name_i18n={"bg": key},
            country_code="bg",
            city="sofia",
            school_type="private",
            education_level=level,
            website_url=url,
            scrape_status="extracted",
            attributes={"data_validation": {"_schema_version": 1, "status": "ok"}, "validated_website_url": url},
        )
        school.locations = [SchoolLocation(address_i18n={"bg": address}, is_primary=True, location_tags=[])]
        db_session.add(school)
        rows[key] = school
    await db_session.flush()
    db_session.add(
        SourcePage(
            school_id=rows["kg"].id,
            scrape_type=ScrapeType.WEBSITE,
            source_url="https://sofia-school.maplebear.bg/contact",
            content_hash="x",
            raw_markdown="Maple Bear Бояна ул. Панорамен Път 38",
            is_valid=True,
        )
    )
    await db_session.commit()
    return rows


def _pages_with_pair():
    pages = maple_bear_pages()
    pages["https://example-school.bg/"] = page(
        "https://example-school.bg/", "Частно основно училище Пример. 1 клас. ул. Липа № 5"
    )
    return pages


@pytest.mark.asyncio
async def test_dry_run_reports_and_writes_nothing(db_session):
    rows = await _seed(db_session)

    report = await run_shared_site_check(db_session, dry_run=True, fetcher=fake_fetcher(_pages_with_pair()))

    assert report["counts"] == {"groups": 2, "institutions": 4, "kept": 1, "replaced": 2, "withheld": 1}
    await db_session.refresh(rows["pair_kg"])
    assert rows["pair_kg"].website_url == "https://example-school.bg/"
    assert rows["pair_kg"].scrape_status == "extracted"


@pytest.mark.asyncio
async def test_apply_withholds_and_replaces_through_existing_state(db_session):
    rows = await _seed(db_session)

    await run_shared_site_check(db_session, dry_run=False, fetcher=fake_fetcher(_pages_with_pair()))

    for school in rows.values():
        await db_session.refresh(school)
    withheld, replaced, kept = rows["pair_kg"], rows["kg"], rows["pair_school"]

    assert withheld.website_url is None
    assert withheld.scrape_status == "failed_validate"
    assert withheld.attributes[WEBSITE_DATA_WITHHELD_KEY] is True
    assert "validated_website_url" not in withheld.attributes
    assert withheld.attributes["website_candidate_url"] == "https://example-school.bg/"
    assert not website_data_is_publishable(withheld.attributes, withheld.scrape_status)

    assert replaced.website_url == KG_CAMPUS
    assert replaced.scrape_status == "pending"
    assert replaced.attributes[WEBSITE_DATA_WITHHELD_KEY] is True
    assert rows["school"].website_url == SCHOOL_CAMPUS
    old_pages = (
        await db_session.execute(select(SourcePage).where(SourcePage.school_id == replaced.id,
                                                          SourcePage.scrape_type == ScrapeType.WEBSITE))
    ).scalars().all()
    assert old_pages and all(p.is_valid is False for p in old_pages)

    assert kept.website_url == "https://example-school.bg/"
    assert kept.scrape_status == "extracted"
    assert website_data_is_publishable(kept.attributes, kept.scrape_status)
    # A keep is recorded as positive evidence for exactly this site.
    assert kept.attributes["shared_site_check"]["action"] == "keep"
    assert kept.attributes["shared_site_check"]["new_url"] == "https://example-school.bg/"
    assert replaced.attributes["shared_site_check"]["action"] == "replace"


@pytest.mark.asyncio
async def test_school_ids_limit_the_check_to_their_groups(db_session):
    rows = await _seed(db_session)
    report = await run_shared_site_check(
        db_session, dry_run=True, school_ids=[rows["pair_kg"].id], fetcher=fake_fetcher(_pages_with_pair())
    )
    assert [group["group"] for group in report["groups"]] == ["example-school.bg"]


@pytest.mark.asyncio
async def test_withhold_clears_site_derived_locations_but_keeps_officially_confirmed_address(db_session):
    rows = await _seed(db_session)
    pair_kg = rows["pair_kg"]
    await db_session.refresh(pair_kg, ["locations"])
    copied = pair_kg.locations[0]
    copied.address_i18n = {"bg": "ул. Липа 5"}
    copied.lat, copied.lng = 42.6, 23.3
    copied.geocode_meta = {"method": "website_map_link"}
    copied.location_tags = ["address_source=website_contact", "coords_source=website_map_link"]
    confirmed = SchoolLocation(
        school_id=pair_kg.id,
        address_i18n={"bg": 'ул. "Бреза" № 7'},
        is_primary=False,
        lat=42.7,
        lng=23.4,
        location_tags=["address_source=website_contact", "coords_source=sofia_municipal"],
    )
    db_session.add(confirmed)
    await db_session.commit()
    copied_id, confirmed_id, pair_kg_id = copied.id, confirmed.id, pair_kg.id

    report = await run_shared_site_check(db_session, dry_run=True, fetcher=fake_fetcher(_pages_with_pair()))
    row = next(m for g in report["groups"] for m in g["members"] if m["school_id"] == pair_kg_id)
    assert row["action"] == WITHHOLD
    assert [loc["location_id"] for loc in row["applied"]["would_clear_locations"]] == [copied_id]
    await db_session.refresh(copied)
    assert copied.address_i18n == {"bg": "ул. Липа 5"}  # dry run changed nothing

    await run_shared_site_check(db_session, dry_run=False, fetcher=fake_fetcher(_pages_with_pair()))
    await db_session.refresh(copied)
    await db_session.refresh(confirmed)
    assert copied.address_i18n == {}
    assert (copied.lat, copied.lng) == (None, None)
    assert copied.location_tags == []
    assert confirmed.address_i18n == {"bg": 'ул. "Бреза" № 7'}
    assert (confirmed.lat, confirmed.lng) == (42.7, 23.4)
    assert "address_source=website_contact" in confirmed.location_tags
    assert confirmed_id != copied_id


@pytest.mark.asyncio
async def test_repair_websites_runs_recovered_urls_through_the_check(db_session):
    from unittest.mock import AsyncMock, patch

    from app.scrapers import cli as scraper_cli

    school = School(
        name_i18n={"bg": "Възстановен"}, country_code="bg", school_type="private", education_level="kindergarten",
        city="sofia", scrape_status="failed_validate",
    )
    db_session.add(school)
    await db_session.commit()

    class SessionContext:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *_args):
            return False

    with (
        patch("app.database.async_session_maker", side_effect=lambda: SessionContext()),
        patch.object(scraper_cli, "_run_recover_failed_school", new=AsyncMock()),
        patch.object(scraper_cli, "_run_shared_site_check", new=AsyncMock()) as check_mock,
    ):
        await scraper_cli._repair_websites_command(
            school_name=None, school_id=school.id, city="sofia", country="bg", limit=None,
            include_state=False, dry_run=False, recover_failed=True, run_extract=False,
        )

    check_mock.assert_awaited_once()
    assert check_mock.await_args.kwargs["school_ids"] == [school.id]


@pytest.mark.asyncio
async def test_website_discovery_batch_runs_the_check_for_its_schools(db_session):
    from unittest.mock import AsyncMock, patch

    from app.scrapers import cli as scraper_cli

    school = School(
        name_i18n={"bg": "Нов"}, country_code="bg", school_type="private", education_level="kindergarten",
        city="sofia", scrape_status="pending",
    )
    db_session.add(school)
    await db_session.commit()

    with (
        patch.object(scraper_cli, "_run_discover_website", new=AsyncMock(return_value={"found": True})),
        patch.object(scraper_cli, "_run_shared_site_check", new=AsyncMock()) as check_mock,
    ):
        await scraper_cli._run_discover_websites_batch(db=db_session, country="bg", city="sofia", limit=None)

    check_mock.assert_awaited_once()
    assert check_mock.await_args.kwargs["school_ids"] == [school.id]
    assert check_mock.await_args.kwargs["country"] == "bg"
