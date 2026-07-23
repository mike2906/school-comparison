# P2.14(e) bounded launch-data curation — slice 1

Date: `2026-07-23`

Result: **PASS for the first bounded identity slice; P2.14(e) remains open.** This
slice curated only school `520`. It did not run discovery, refresh, navigation,
extraction, validation, summarization, provider work, or geocoding.

## Curated identity

School `520` now publishes the source-backed identity:

- Bulgarian: `ЧОУ „Фюжън“`
- English: `Fusion School`

The existing corroboration gate was preserved. Both required signals are supported by
the cached official-site corpus:

- `website_domain_alias_match`: the official host is `school.fusion.bg`.
- `repeated_on_page_identity`: `Fusion School` appears on eight distinct cached
  official-site URLs, including the cached about page and program page.

The private `display_name_evidence.curation` record identifies the offline review
method, reviewer, review time, cache time, and the two representative source URLs:

- `https://school.fusion.bg/za-uchilishteto`
- `https://school.fusion.bg/zashto-fusion/programa`

No raw source value or curation metadata is exposed by the API.

## Queue accounting

| Queue | Before | After | Disposition |
|---|---:|---:|---|
| Missing source-backed English identities | 117 | 116 | School 520 curated |
| Uncorroborated English candidates among the remaining queue | 44 | 44 | Unchanged |
| Sofia schools with withheld scraped pricing | 48 | 48 | Unchanged |
| Withheld Sofia scraped pricing rows | 205 | 205 | Unchanged |
| Terminal NULL geocodes with failure evidence | 88 | 88 | Accepted; untouched |

The current database also contains 11 scraped pricing rows for three non-Sofia schools;
they are outside the recorded Sofia launch queue and were not touched.

School `520`'s cached official fee page supports its three stored candidate rows, but
they remain `SCRAPED_WEBSITE`. The pricing publication gate requires an accountable
human verifier and aligned verification date; this run did not fabricate or substitute
that metadata. Schools `113` and `116` and their recorded dynamic-field discrepancies
were not changed.

## Deterministic verification

- Focused resolver/display-gate/data-quality tests: `76 passed`.
- Data-quality scoreboard: display-name overrides `12/443`; publishable website-data
  coverage `328/328` with zero published without a report; pricing failing gates
  `205/205`.
- Exhaustive `scripts/audit_p2_12_boundary.py`: `passed=true`, `llm_calls=0`, all
  `1,066` API requests returned successfully, no boundary leaks, zero unexplained
  coordinate groups, and exact pricing predicate/scoreboard/API parity at zero
  published rows.

P2.14(e) remains open for the other 116 identities and 48-school/205-row Sofia pricing
queue.
