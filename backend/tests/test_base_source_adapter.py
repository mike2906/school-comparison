from app.schemas.scraping import DiscoveredLocation, DiscoveredSchool
from app.scrapers.sources.base_adapter import BaseSourceAdapter


class DummyAdapter(BaseSourceAdapter):
    async def discover(self, limit=None, sample_ratio=0.0):
        return []


def test_ensure_i18n_fallbacks_does_not_add_synthetic_english():
    school = DiscoveredSchool(
        country_code="bg",
        city="sofia",
        name_i18n={"bg": "Частно основно училище Фюжън ЕООД"},
        school_type="private",
        education_level="primary",
        locations=[
            DiscoveredLocation(
                address_i18n={"bg": "ул. Иван Вазов 15, София"},
                age_groups=["grade_1_4"],
            )
        ],
    )

    DummyAdapter(db=None)._ensure_i18n_fallbacks(school)

    assert school.name_i18n == {"bg": "Частно основно училище Фюжън ЕООД"}
    assert school.locations[0].address_i18n == {"bg": "ул. Иван Вазов 15, София"}
