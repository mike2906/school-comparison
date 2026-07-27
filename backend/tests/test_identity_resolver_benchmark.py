from unittest.mock import AsyncMock

import pytest

from app.models import School
from app.services.identity_resolver_benchmark import (
    _english_candidate,
    _identity_key,
    evaluate_identity_resolver_benchmark,
    load_identity_resolver_benchmark,
)


def test_benchmark_fixture_has_fixed_non_overlapping_populations():
    benchmark = load_identity_resolver_benchmark()
    good_ids = {row["id"] for row in benchmark["known_good"]}
    bad_ids = {row["id"] for row in benchmark["known_bad"]}
    deferred_ids = set(benchmark["deferred_ids"])

    assert len(good_ids) == 11
    assert len(bad_ids) == 10
    assert len(deferred_ids) == 96
    assert not (good_ids & bad_ids or good_ids & deferred_ids or bad_ids & deferred_ids)
    assert len(good_ids | bad_ids | deferred_ids) == 117


def test_english_candidate_does_not_count_bulgarian_only_label():
    assert _english_candidate({"bg": "Таткова градина"}) is None
    assert _english_candidate({"bg": "Under 1 Roof"}) == "Under 1 Roof"
    assert _english_candidate({"bg": "Йор Кидс", "en": "Your Kids"}) == "Your Kids"


def test_identity_key_ignores_display_punctuation():
    assert _identity_key("Montessori Children’s House") == _identity_key(
        "Montessori Children's House"
    )


@pytest.mark.asyncio
async def test_benchmark_queries_only_production_valid_cached_pages():
    school = School(
        id=1,
        name_i18n={"bg": "Тестово училище"},
        country_code="bg",
        attributes={},
    )

    class _ScalarResult:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return self

        def all(self):
            return self._values

    db = AsyncMock()
    db.execute.side_effect = [_ScalarResult([school]), _ScalarResult([])]

    await evaluate_identity_resolver_benchmark(
        db,
        benchmark={"known_good": [], "known_bad": [], "deferred_ids": [1]},
    )

    page_query = db.execute.await_args_list[1].args[0]
    assert "source_pages.is_valid IS true" in str(page_query)
