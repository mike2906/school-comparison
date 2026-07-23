# P2.14(e) bounded launch-data curation — identity queue closure

Date: `2026-07-23`

Result: **PASS for complete classification of the recorded 117-school identity
queue. P2.14(e) remains open only for accountable human pricing verification.** No
discovery, refresh, navigation, extraction, validation, summarization, provider work,
or geocoding ran.

## Identity outcome

| Decision | Schools | Meaning |
|---|---:|---|
| Published | 10 | Cached official evidence satisfied the existing gates |
| Corroborated but withheld | 1 | School 521 remains fail-closed after its navigation failure |
| Deferred | 96 | No explicit cached source-backed English identity |
| Rejected | 10 | Contact, navigation, or cross-institution labels removed |
| Unclassified | 0 | The full recorded queue has a decision |

The exact IDs, curated identities, evidence URLs, rejected values/reasons, and deferred
IDs are recorded in `identity-decisions.json`. Mechanical transliterations and
host-derived aliases were not promoted as source-backed English identities; those
fallbacks already remain available at serialization time.

An intermediate audit briefly touched six schools outside the recorded queue. The
cohort reconciliation caught the scope error before acceptance; all six were restored
exactly from recoverable rejected-candidate metadata, and none appears in the final
manifest. The final database ledger is exactly 117 records with zero unclassified.

## Unchanged launch boundaries

- Pricing remains **48 Sofia schools / 205 `SCRAPED_WEBSITE` rows**, all withheld.
- Schools 113 and 116 retain their dynamic-field discrepancies and remain gated.
- All 88 terminal NULL geocodes remain accepted and untouched.
- School 521's `The Beehive` identity remains stored but unpublished; no validation
  evidence or status was invented.

## Remaining blocker

Pricing promotion requires an accountable human verifier identity and row-by-row review
of the live official source, including amount/range, currency, period, age group, plan,
academic year, inclusions, exclusions, discounts, and installments. Offline cached
review by Codex cannot truthfully supply that human-verification metadata. Sparse pricing
is acceptable, so unverified rows remain safely hidden.

## Deterministic verification

- Full backend suite: `890 passed` (141 existing deprecation warnings).
- Exhaustive `scripts/audit_p2_12_boundary.py`: `passed=true`, `llm_calls=0`, all
  1,066 API requests succeeded, no boundary leaks, zero unexplained coordinate groups,
  and exact pricing predicate/scoreboard/API parity at zero published rows.
- Data-quality scoreboard: publishable website-data coverage `328/328`, zero published
  without a report, display-name overrides `21/443`, and `205/205` scraped pricing rows
  withheld.
- Manifest invariant check: 117 total = 10 published + 1 corroborated-but-withheld +
  96 deferred + 10 rejected; zero unclassified.

P2.14(e) can be closed once the user either supplies a human verifier for a bounded
pricing slice or explicitly defers all pricing curation for launch.
