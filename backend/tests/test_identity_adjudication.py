"""Guards for the bounded English-identity adjudication pilot.

The pilot may recommend, never publish. These tests pin the boundary: bounded
cached-page evidence, fail-closed model handling, and deterministic guards that
can only reject.
"""

from unittest.mock import AsyncMock

import pytest

from app.models import School, SourcePage
from app.models.scrape_log import ScrapeType
from app.services.identity_adjudication import (
    MAX_MATCHED_LINES_PER_PAGE,
    MAX_PAGES_PER_CASE,
    IdentityAdjudicationVerdict,
    PageExcerpt,
    SiblingContext,
    adjudicate_case,
    apply_guards,
    build_case,
    build_cases,
    build_user_prompt,
    deterministic_reject_reason,
)


def _school(**overrides):
    payload = {
        "id": 1,
        "name_i18n": {"bg": "Частна детска градина Тест"},
        "country_code": "bg",
        "city": "sofia",
        "education_level": "kindergarten",
        "website_url": "https://example-school.com/",
        "attributes": {},
    }
    payload.update(overrides)
    return School(**payload)


def _page(page_id, url, markdown, category=None):
    return SourcePage(
        id=page_id,
        school_id=1,
        scrape_type=ScrapeType.WEBSITE,
        source_url=url,
        content_hash=f"hash-{page_id}",
        page_category=category,
        raw_markdown=markdown,
        is_valid=True,
    )


class TestDeterministicGuards:
    def test_contact_and_navigation_labels_never_pass(self):
        assert (
            deterministic_reject_reason("Email: office@busy-school.com", education_level="primary")
            == "contact_or_navigation_label"
        )
        assert (
            deterministic_reject_reason("VISIT SCHOOL", education_level="lower_secondary")
            == "contact_or_navigation_label"
        )

    def test_institution_class_must_agree_with_education_level(self):
        # The same label is safe for the school and unsafe for its sibling
        # kindergarten on the shared domain.
        assert (
            deterministic_reject_reason(
                "Uwekind International School", education_level="upper_secondary"
            )
            is None
        )
        assert (
            deterministic_reject_reason(
                "Uwekind International School", education_level="kindergarten"
            )
            == "institution_class_conflicts_with_education_level"
        )

    def test_short_single_word_identities_are_not_noise(self):
        # ``Growers`` is a published identity; the candidate-mining noise
        # heuristic would reject it, so this guard must be narrower.
        assert deterministic_reject_reason("Growers", education_level="kindergarten") is None
        assert deterministic_reject_reason("The Beehive", education_level="kindergarten") is None

    def test_prose_and_malformed_labels_are_rejected(self):
        prose = "We are a warm bilingual kindergarten that helps every child grow and thrive daily"
        assert deterministic_reject_reason(prose, education_level="kindergarten") == "noise_label"
        assert (
            deterministic_reject_reason("[Home](https://x.bg)", education_level="kindergarten")
            is not None
        )
        assert deterministic_reject_reason("", education_level="kindergarten") == "empty_candidate"


class TestCaseAssembly:
    def test_evidence_is_bounded_and_prefers_matching_pages(self):
        school = _school()
        matched_markdown = "\n".join(
            ["# Sunny House", "Sunny House welcomes you", "Sunny House team"] * 5
        )
        pages = [
            _page(index, f"https://example-school.com/page-{index}", matched_markdown)
            for index in range(1, 10)
        ]
        case = build_case(school=school, candidate={"en": "Sunny House"}, pages=pages)

        assert len(case.excerpts) == MAX_PAGES_PER_CASE
        assert all(len(excerpt.lines) <= MAX_MATCHED_LINES_PER_PAGE for excerpt in case.excerpts)
        assert all(excerpt.contains_candidate for excerpt in case.excerpts)
        # Supporting URLs count every matched cached page, not only the excerpts.
        assert len(case.supporting_source_urls) == 9

    def test_supporting_urls_deduplicate_like_the_promotion_gate(self):
        school = _school(website_url="https://aas-sofia.org/")
        markdown = "The Anglo-American School of Sofia"
        pages = [
            _page(1, "https://www.aas-sofia.org", markdown),
            _page(2, "https://www.aas-sofia.org/", markdown),
        ]
        case = build_case(
            school=school,
            candidate={"en": "The Anglo-American School of Sofia"},
            pages=pages,
        )
        assert len(case.supporting_source_urls) == 1

    def test_candidate_matching_respects_token_boundaries(self):
        # ``Sunny House`` must not count as present in ``Sunny Houses``: two such
        # near-matches would otherwise satisfy both the presence check and the
        # provenance count for a label that never appears.
        pages = [
            _page(1, "https://example-school.com/", "Welcome to Sunny Houses"),
            _page(2, "https://example-school.com/about", "About Sunny Houses", "about"),
        ]
        case = build_case(school=_school(), candidate={"en": "Sunny House"}, pages=pages)
        assert case.supporting_source_urls == ()
        assert all(not excerpt.contains_candidate for excerpt in case.excerpts)

    def test_candidate_matching_ignores_punctuation_and_case(self):
        case = build_case(
            school=_school(),
            candidate={"en": "Sunny House"},
            pages=[_page(1, "https://example-school.com/", "**SUNNY HOUSE** — since 2009")],
        )
        assert len(case.supporting_source_urls) == 1

    def test_supporting_urls_require_the_exact_official_host(self):
        # The promotion gate rejects any source whose host differs from the
        # configured website host, so a sibling host on the same registrable
        # domain must not count here either.
        school = _school(website_url="https://school.example.org/")
        markdown = "Sunny House"
        pages = [
            _page(1, "https://school.example.org/", markdown),
            _page(2, "https://network.example.org/members", markdown),
            _page(3, "https://school.example.org/about", markdown, "about"),
        ]
        case = build_case(school=school, candidate={"en": "Sunny House"}, pages=pages)
        assert [
            url for url in case.supporting_source_urls if "network.example.org" in url
        ] == []
        assert len(case.supporting_source_urls) == 2

    def test_bulgarian_only_candidate_yields_no_english_case(self):
        case = build_case(
            school=_school(),
            candidate={"bg": "ЧОУ ПЕТЪР БЕРОН"},
            pages=[_page(1, "https://example-school.com/", "ЧОУ ПЕТЪР БЕРОН")],
        )
        assert case.candidate_en is None
        assert case.blocked_reason == "no_english_candidate"
        assert case.adjudicable is False

    def test_shared_domain_siblings_reach_the_prompt(self):
        case = build_case(
            school=_school(education_level="upper_secondary"),
            candidate={"en": "Sunny School"},
            pages=[_page(1, "https://example-school.com/", "Sunny School")],
            siblings=[
                SiblingContext(
                    school_id=42,
                    registry_name="Частна детска градина Съни",
                    education_level="kindergarten",
                    website_url="https://example-school.com/kg",
                )
            ],
        )
        prompt = build_user_prompt(case)
        assert "school 42" in prompt
        assert "Частна детска градина Съни" in prompt
        assert "is NOT this school's identity" in prompt


class TestVerdictGuards:
    def _case(self, **overrides):
        return build_case(
            school=_school(**overrides.pop("school", {})),
            candidate={"en": "Sunny House"},
            pages=overrides.pop(
                "pages",
                [
                    _page(1, "https://example-school.com/", "Sunny House welcomes you"),
                    _page(2, "https://example-school.com/about", "About Sunny House", "about"),
                ],
            ),
        )

    def _verdict(self, **overrides):
        payload = {
            "verdict": "accept",
            "reason_code": "own_official_identity",
            "confidence": 0.9,
            "quoted_evidence": ["Sunny House welcomes you"],
        }
        payload.update(overrides)
        return IdentityAdjudicationVerdict(**payload)

    def test_corroborated_accept_only_recommends_manual_promotion(self):
        decision, failures = apply_guards(self._case(), self._verdict())
        assert decision == "recommend_manual_promotion"
        assert failures == []

    def test_unverifiable_quote_rejects(self):
        decision, failures = apply_guards(
            self._case(),
            self._verdict(quoted_evidence=["Sunny House is the best school in Sofia"]),
        )
        assert decision == "rejected"
        assert "unverifiable_quote" in failures

    def test_accept_carrying_a_rejection_reason_fails_closed(self):
        decision, failures = apply_guards(
            self._case(), self._verdict(reason_code="network_or_group_label")
        )
        assert decision == "rejected"
        assert "inconsistent_accept_reason" in failures

    def test_quote_verification_respects_token_boundaries(self):
        case = build_case(
            school=_school(),
            candidate={"en": "Sunny House"},
            pages=[
                _page(1, "https://example-school.com/", "Sunny House welcomes you"),
                _page(2, "https://example-school.com/about", "About Sunny House", "about"),
            ],
        )
        decision, failures = apply_guards(
            case, self._verdict(quoted_evidence=["Sunny House welcomes yous"])
        )
        assert decision == "rejected"
        assert "unverifiable_quote" in failures

    def test_quote_spanning_two_evidence_lines_is_unverifiable(self):
        # A quote stitched from the tail of one line and the head of the next
        # exists in no source, so it must not corroborate an acceptance.
        case = build_case(
            school=_school(),
            candidate={"en": "Sunny House"},
            pages=[
                _page(
                    1,
                    "https://example-school.com/",
                    "Sunny House welcomes you\nOur teachers are certified",
                ),
                _page(2, "https://example-school.com/about", "About Sunny House", "about"),
            ],
        )
        decision, failures = apply_guards(
            case,
            self._verdict(quoted_evidence=["welcomes you Our teachers are certified"]),
        )
        assert decision == "rejected"
        assert "unverifiable_quote" in failures

    def test_accept_without_any_quote_rejects(self):
        # An empty quote list must not pass verification vacuously.
        decision, failures = apply_guards(self._case(), self._verdict(quoted_evidence=[]))
        assert decision == "rejected"
        assert "missing_evidence_quote" in failures

    def test_accept_with_only_blank_quotes_rejects(self):
        decision, failures = apply_guards(
            self._case(), self._verdict(quoted_evidence=["   ", "\n"])
        )
        assert decision == "rejected"
        assert "missing_evidence_quote" in failures

    def test_candidate_absent_from_cached_pages_rejects(self):
        case = build_case(
            school=_school(),
            candidate={"en": "Sunny House"},
            pages=[_page(1, "https://example-school.com/", "Some other kindergarten")],
        )
        decision, failures = apply_guards(case, self._verdict(quoted_evidence=[]))
        assert decision == "rejected"
        assert "candidate_absent_from_cached_pages" in failures

    def test_single_source_url_holds_instead_of_recommending(self):
        case = build_case(
            school=_school(),
            candidate={"en": "Sunny House"},
            pages=[_page(1, "https://example-school.com/", "Sunny House welcomes you")],
        )
        decision, failures = apply_guards(case, self._verdict())
        assert decision == "hold_insufficient_provenance"
        assert failures == ["insufficient_source_urls"]

    def test_low_confidence_rejects(self):
        decision, failures = apply_guards(self._case(), self._verdict(confidence=0.4))
        assert decision == "rejected"
        assert "confidence_below_floor" in failures

    def test_model_reject_stays_rejected(self):
        decision, failures = apply_guards(
            self._case(), self._verdict(verdict="reject", reason_code="network_or_group_label")
        )
        assert decision == "rejected"
        assert failures == []

    def test_guards_override_an_accept_that_contradicts_education_level(self):
        case = build_case(
            school=_school(education_level="kindergarten"),
            candidate={"en": "Sunny International School"},
            pages=[
                _page(1, "https://example-school.com/", "Sunny International School"),
                _page(2, "https://example-school.com/about", "Sunny International School", "about"),
            ],
        )
        decision, failures = apply_guards(
            case, self._verdict(quoted_evidence=["Sunny International School"])
        )
        assert decision == "rejected"
        assert "institution_class_conflicts_with_education_level" in failures


class TestAdjudicationCall:
    class _Agent:
        def __init__(self, output=None, error=None):
            self._output = output
            self._error = error
            self.calls = 0

        async def run(self, prompt):
            self.calls += 1
            if self._error is not None:
                raise self._error
            return type("Result", (), {"output": self._output})()

    def _case(self, **overrides):
        return build_case(
            school=_school(**overrides.get("school", {})),
            candidate=overrides.get("candidate", {"en": "Sunny House"}),
            pages=overrides.get(
                "pages",
                [
                    _page(1, "https://example-school.com/", "Sunny House welcomes you"),
                    _page(2, "https://example-school.com/about", "About Sunny House", "about"),
                ],
            ),
        )

    async def test_provider_failure_fails_closed(self):
        agent = self._Agent(error=TimeoutError("provider timeout"))
        row = await adjudicate_case(self._case(), agent_factory=lambda: agent)
        assert row["decision"] == "rejected"
        assert row["reason_code"] == "adjudication_failed"
        assert row["guard_failures"] == ["error:TimeoutError"]
        # The request was dispatched and may have been billed, so cost reporting
        # must not claim that no call was made.
        assert row["llm_called"] is True

    async def test_agent_construction_failure_rejects_instead_of_aborting(self):
        # An unset or malformed OPENROUTER_API_KEY must reject this case and let
        # the run finish, not abort the whole pilot.
        def _factory():
            raise RuntimeError("OPENROUTER_API_KEY is not configured")

        row = await adjudicate_case(self._case(), agent_factory=_factory)
        assert row["decision"] == "rejected"
        assert row["reason_code"] == "adjudication_failed"
        # Nothing was dispatched, so nothing could have been billed.
        assert row["llm_called"] is False

    async def test_invalid_model_output_fails_closed(self):
        agent = self._Agent(output={"verdict": "maybe"})
        row = await adjudicate_case(self._case(), agent_factory=lambda: agent)
        assert row["decision"] == "rejected"
        assert row["guard_failures"] == ["invalid_model_output"]

    async def test_prefiltered_candidate_spends_no_llm_call(self):
        agent = self._Agent()
        case = self._case(
            candidate={"en": "Email: office@example-school.com"},
            pages=[_page(1, "https://example-school.com/", "Email: office@example-school.com")],
        )
        row = await adjudicate_case(case, agent_factory=lambda: agent, prefilter_rejects=True)
        assert agent.calls == 0
        assert row["llm_called"] is False
        assert row["decision"] == "rejected"

    async def test_case_without_cached_evidence_spends_no_llm_call(self):
        agent = self._Agent()
        case = build_case(
            school=_school(), candidate={"en": "Sunny House"}, pages=[]
        )
        row = await adjudicate_case(case, agent_factory=lambda: agent)
        assert agent.calls == 0
        assert row["decision"] == "rejected"
        assert row["reason_code"] == "insufficient_official_evidence"

    async def test_accepted_case_is_a_recommendation_not_a_publication(self):
        agent = self._Agent(
            output=IdentityAdjudicationVerdict(
                verdict="accept",
                reason_code="own_official_identity",
                confidence=0.95,
                quoted_evidence=["Sunny House welcomes you"],
            )
        )
        row = await adjudicate_case(self._case(), agent_factory=lambda: agent)
        assert agent.calls == 1
        assert row["decision"] == "recommend_manual_promotion"
        # No publication-shaped output: the row carries evidence for a human.
        assert set(row) & {"name_i18n", "published", "promoted"} == set()


async def test_case_building_reads_only_valid_cached_website_pages():
    school = _school()

    class _Result:
        def __init__(self, values):
            self._values = values

        def scalars(self):
            return self

        def all(self):
            return self._values

    db = AsyncMock()
    db.execute.side_effect = [_Result([school]), _Result([]), _Result([])]

    await build_cases(db, [{"school_id": 1, "candidate": {"en": "Sunny House"}}])

    page_query = str(db.execute.await_args_list[1].args[0])
    assert "source_pages.is_valid IS true" in page_query
    assert "source_pages.raw_markdown IS NOT NULL" in page_query


async def test_missing_school_is_an_error_not_a_silent_skip():
    class _Result:
        def scalars(self):
            return self

        def all(self):
            return []

    db = AsyncMock()
    db.execute.side_effect = [_Result()]

    with pytest.raises(ValueError, match="missing from database"):
        await build_cases(db, [{"school_id": 999, "candidate": {"en": "Sunny House"}}])
