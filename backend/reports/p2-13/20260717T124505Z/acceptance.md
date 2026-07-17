# P2.13 exhaustive deterministic Sofia acceptance

Date: `2026-07-17`

Result: **PASS** after one bounded fail-closed publication-gate fix. The one-time full
refresh was not started.

## Scope and method

- `443` Sofia DB-scoped schools were audited, which is a superset of the cached
  `363`-school full-refresh projection.
- The API sweep made `1,066` ASGI requests: the full Sofia list plus detail for all
  `443` schools and compare batches covering all `443`, repeated with BG and EN
  `Accept-Language` headers. All returned HTTP 200.
- The list serialized `442` schools in both locales. School `338` has no locations and
  is intentionally excluded by the listable-location query; its detail and compare
  payloads were still audited.
- Stored-data checks reused `docs/audits.sql`, the data-quality scoreboard, and the
  extended `scripts/audit_p2_12_boundary.py`.
- `llm_calls=0`. No extraction, summarization, LLM URL fallback, or LLM spot check ran.

## Launch acceptance

| Criterion | Result | Evidence |
|---|---|---|
| Current validation report for every school with publishable website data | PASS | The initial audit found a fail-open edge (`324/338` eligible schools lacked current reports). The bounded fix now requires a schema-version-1 report before any website-derived branch can publish, while preserving curated top-level attributes. Final scoreboard: `14/14` eligible, `100%`, `0` published without report. SQL inventories `378` stored candidate schools: `14` meet the report-state gate and `364` are withheld. |
| Zero summaries serialized | PASS | `0` hits across all list/detail/compare payloads in both locales. Stored summaries remain internal while the launch flag is off. |
| Zero website-derived admission fields serialized | PASS | `0` localized entry-requirement, deadline, or available-spot hits; no raw `website_extracted` keys. |
| Curated pricing only, with required evidence | PASS | `0` pricing rows serialized. Shared predicate=`0`, scoreboard publishable=`0`, API=`0`; all `220` stored Sofia pricing rows fail the launch publication predicate. |
| Provenance metadata-only | PASS | `0` unexpected provenance keys; no `value_text`, `value_json`, field paths, rejected values, or school-529 truth-set values reached the API. |
| No Markdown or bare URLs in display fields | PASS | `0` hits under the shared production sanitizer across all `1,066` responses; school 367 remained clean in six payloads. Explicit URL fields were excluded from this display-text assertion. |
| No internal API keys | PASS | `0` raw extraction, validation, source-ref, `moe_*`, or other internal-key hits. |
| High-risk scraped dynamic fields withheld | PASS | `0` uncurated class-size, schedule/opening-hours, established-year, or analogous launch-scope hits. |
| Sofia coordinate bounds | PASS | `0` Sofia-labeled out-of-bounds coordinates and `0` partial lat/lng pairs across `480` locations. |
| Geocode precision metadata | PASS | `394/394` geocoded locations have `exact` or `approximate` precision metadata (`100%`). |
| Duplicate coordinates | PASS | Seven known shared-address/campus groups; `0` unexplained groups. |
| Terminal geocode failures | PASS | All `86` deterministic terminal-failure locations are null and carry status, provider, and accepted rejection-reason evidence; `0` invalid terminal states. Named regressions `1143`, `1145`, and `1188` pass. |
| Cached truth sets | PASS | Stored runs `db4ba90d-6894-4164-a873-34ee79687fad` and `396dffe5-c905-44e2-8582-1d4c1bcdbd66` match their intentional `partial` statuses, 18-school cohorts, and exact costs (`$0.029856`, `$0.923238`). Known rejected/incorrect cases `105`, `153`, `310`, `529`, `538`, and `570` have `0` boundary leaks. The cached `db4ba90d` 10-school spot-check sample's actionable school-310 contradiction remains withheld. |
| Pricing gate / scoreboard / API parity | PASS | Exact set parity at zero publishable row IDs. |

No acceptance criterion was waived.

## Spend guardrail evidence

The active OpenRouter key metadata query returned:

- usage: `$10.47598213`
- limit: `null`
- limit remaining: `null`
- cached cheap-tier full-refresh projection: approximately `$0.752620`, including
  the recorded 25% contingency, with summarization skipped

Recommendation for the user: provide `$5` of additional key headroom. Because current
usage already exceeds `$5`, set an absolute cumulative limit of about **`$15.50`** if
the dashboard's limit is cumulative for this key; if using a reset/new key whose usage
starts at zero, set **`$5`**. This is a recommendation only—the dashboard decision is
left to the user.

## Verification

- `uv run python scripts/audit_p2_12_boundary.py` — `passed=true`, `llm_calls=0`.
- `uv run python -m app.scrapers.cli data-quality --city sofia --country bg` —
  `14/14` current-report coverage, `394/394` precision coverage, zero publishable
  pricing.
- `docs/audits.sql` under `psql -v ON_ERROR_STOP=1` — completed without SQL errors.
- Focused API/data-quality/boundary suites — `92 passed`.
- Full backend suite — `857 passed` with `140` pre-existing deprecation warnings.

Stop condition observed: no full refresh or subsequent phase work was started.
