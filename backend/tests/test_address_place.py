from app.schemas.school import SchoolListResponse
from app.utils.address_place import place_suffix_bg, school_town, with_place


def test_school_town_is_the_register_settlement_outside_sofia():
    assert school_town({"moe_region_code": 22, "moe_town_name": "Горни Богров"}) == "Горни Богров"
    assert school_town({"moe_region_code": 23, "moe_town_name": "Своге"}) == "Своге"
    assert school_town({"moe_region_code": 22, "moe_town_name": "София"}) is None
    # Other regions are their own city; nothing to add yet.
    assert school_town({"moe_region_code": 16, "moe_town_name": "Пловдив"}) is None
    assert school_town({"moe_town_name": "Лозен"}) is None
    assert school_town(None) is None


def test_village_and_district_are_added_when_the_address_leaves_them_out():
    resolved = {"bg": "ул. „М. Райчев“ 9", "en": 'ul. "M. Raychev" 9'}
    assert with_place(resolved, town="Горни Богров", district="Кремиковци") == {
        "bg": "ул. „М. Райчев“ 9, Горни Богров, район Кремиковци",
        "en": 'ul. "M. Raychev" 9, Gorni Bogrov, Kremikovtsi district',
    }


def test_a_sofia_address_gets_only_its_district():
    assert place_suffix_bg("ул. 501 V м. р.", town=None, district="Люлин") == "район Люлин"


def test_nothing_is_added_that_the_address_already_names():
    # The address names its village, district or a complex called after the district.
    assert place_suffix_bg("ул. „Иван Пешев“ № 1, с. Лозен, р-н Панчарево", town="Лозен", district="Панчарево") is None
    assert place_suffix_bg("с.Бистрица, ул. Бистришко шосе 24", town="Бистрица", district="Панчарево") == "район Панчарево"
    assert place_suffix_bg("ж.к. Младост-4, ул. „Детска мечта“ № 22", town=None, district="Младост") is None
    assert place_suffix_bg("Надежда -4 ул. „Звезда“ № 3", town=None, district="Надежда") is None
    assert place_suffix_bg("район Витоша, кв. Бояна, ул. „Брезите“ № 17", town=None, district="Витоша") is None
    # A town that is its own district is named once.
    assert place_suffix_bg("ул. Восток-2, № 4", town="Банкя", district="Банкя") == "Банкя"
    assert place_suffix_bg("гр. Банкя, ул. Восток-2, № 4", town="Банкя", district="Банкя") is None


def test_a_street_named_after_the_village_does_not_count_as_naming_it():
    assert place_suffix_bg("ул. Лозенска 3", town="Лозен", district=None) == "Лозен"


def test_school_response_places_each_location():
    school = SchoolListResponse.model_validate(
        {
            "id": 256,
            "name_i18n": {"bg": "115 ОУ"},
            "school_type": "state",
            "education_level": "lower_secondary",
            "attributes": {"moe_region_code": 22, "moe_town_name": "Горни Богров"},
            "admission_info": {"admin_district": "Кремиковци"},
            "locations": [
                {"id": 1, "school_id": 256, "age_groups": ["grade_1_4"], "address_i18n": {"bg": "ул. „М. Райчев“ 9"}},
                # A building's own kg.sofia district wins over the school's.
                {"id": 2, "school_id": 256, "age_groups": ["grade_1_4"], "address_i18n": {"bg": "ул. Х 1"}, "district": "Сердика"},
            ],
        }
    )
    addresses = [location["resolved_address_i18n"]["bg"] for location in school.model_dump()["locations"]]
    assert addresses == [
        "ул. „М. Райчев“ 9, Горни Богров, район Кремиковци",
        "ул. Х 1, Горни Богров, район Сердика",
    ]
    assert "district" not in school.model_dump()["locations"][0]


def test_a_street_named_after_a_place_does_not_name_the_place():
    assert place_suffix_bg("ул. „С. Врачански“ 4", town="Бусманци", district="Искър") == "Бусманци, район Искър"
    assert place_suffix_bg("ул. Сливница 3", town="Сливница", district=None) == "Сливница"
    assert place_suffix_bg("бул. „Витоша“ 3", town=None, district="Витоша") == "район Витоша"


def test_only_sofia_districts_are_shown():
    assert place_suffix_bg("ул. Х 1", town=None, district="Кършияка") is None


def test_a_stored_english_address_is_not_given_a_place_twice():
    resolved = {"bg": "ул. Х 5", "en": "ul. H 5, Gorni Bogrov"}
    assert with_place(resolved, town="Горни Богров", district="Кремиковци")["en"] == (
        "ul. H 5, Gorni Bogrov, Kremikovtsi district"
    )
