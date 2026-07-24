# P2.14(e) canonical identity promotion — acceptance

Date: `2026-07-24`

Result: **PASS.** School 521 now publishes the source-backed canonical English identity
`The Beehive`; its unrelated website-derived payload remains fail-closed. No refresh,
provider call, navigation, extraction, validation, summarization, or geocode retry ran.

## Data outcome

- `schools.name_i18n.en`: `The Beehive`.
- Canonical curation metadata records the reviewed value, reviewer, promotion actor,
  timestamps, and both official source URLs.
- Two verified `name_i18n.en` field-source rows point to:
  - `https://www.thebeehive.bg/enroll/book-a-visit`
  - `https://www.thebeehive.bg/programs`
- `scrape_status` remains `extracted` and no `data_validation` report was added. The
  website-data predicate therefore continues to withhold facilities, admissions,
  summaries, pricing, and all other private scrape branches.
- Repeating the committed command returns `already_promoted` and creates no duplicate
  provenance rows.

## Reusable boundary

`promote-curated-identities` defaults to dry-run and accepts only explicit school IDs. A
candidate must have the existing corroborated status and two identity signals, manual
review metadata, at least two distinct URLs matching the normalized official website
host, a semantic EN identity, and no conflicting canonical EN value. Promotion records
durable canonical-curation metadata plus per-source evidence.

Authoritative imports merge only an explicitly promoted canonical EN locale when the
incoming source omits EN. An incoming EN value still wins, and uncurated legacy EN values
are not preserved.

## Verification

- Focused identity/import/API/data-quality tests: `29 passed`.
- Full backend suite: `897 passed` (`149` existing deprecation warnings).
- Exhaustive `scripts/audit_p2_12_boundary.py`: `passed=true`, `llm_calls=0`, all 1,066
  API requests succeeded, no internal/private field leaks, and exact pricing
  predicate/scoreboard/API parity.
- Data-quality scoreboard: curated identities `11/11` published, `0` blocked, `0`
  conflicts; publishable website-data coverage `328/328`; pricing `205/205` withheld.

P2.14(e) remains open only for the separate launch decision on the 48-school / 205-row
human pricing-verification queue.
