# P2.14(e) bounded launch-data curation — slice 3

Date: `2026-07-23`

Result: **PASS for a bounded ten-school classification slice; P2.14(e) remains
open.** This was a correctness pass: five unsafe candidates were rejected and five
plausible candidates were explicitly deferred. No identity was newly published.
No discovery, refresh, navigation, extraction, validation, summarization, provider
work, or geocoding ran.

## Rejected candidates

| School | Rejected candidate | Reason |
|---:|---|---|
| 153 | `Email: kindergarten@uwekind.bg` | Contact label, not an identity |
| 512 | `E-mail: kindergarten@busy-bg.com` | Contact label, not an identity |
| 569 | `Email: office@busy-school.com` | Contact label, not an identity |
| 633 | `VISIT SCHOOL` | Navigation/action label, not an identity |
| 634 | `Uwekind International School` | Network label belongs to the school record, not this kindergarten record |

The unsafe `display_name_i18n` values and any associated evidence were removed. A
private, recoverable `display_name_curation` record retains the rejected candidate,
reason, reviewer, timestamp, and offline-review method.

## Deferred candidates

| School | Candidate | Reason |
|---:|---|---|
| 527 | `Under 1 Roof` | Only slash variants of one article; no independent core page |
| 542 | `Your Kids` | Only www/non-www variants of one homepage; no independent core page |
| 178 | `UNI School` | No explicit cached English identity |
| 339 | `Edison School` | No explicit cached English identity |
| 404 | `Quest School` | No explicit cached English identity |

Deferred candidates remain stored and withheld. Their private curation records make the
decision explicit so later work does not treat them as unreviewed or as publication
signals.

## Queue accounting

| Queue | Before slice 3 | After slice 3 | Disposition |
|---|---:|---:|---|
| Missing source-backed published English identities | 107 | 107 | No gate weakened |
| Stored uncorroborated English candidates | 34 | 29 | Five unsafe candidates removed |
| Explicitly deferred candidates | 0 | 5 | Reviewed; still withheld |
| Rejected candidates | 0 | 5 | Removed with audit metadata |
| Schools with no stored English candidate | 72 | 77 | Includes five rejections |
| Corroborated identity stored but withheld | 1 | 1 | School 521 unchanged |
| Sofia schools with withheld scraped pricing | 48 | 48 | Unchanged |
| Withheld Sofia scraped pricing rows | 205 | 205 | Unchanged |
| Terminal NULL geocodes with failure evidence | 88 | 88 | Accepted; untouched |

Schools 113 and 116 and their dynamic-field discrepancies were not changed. No pricing
row was promoted.

## Deterministic verification

- Focused resolver/display-gate/data-quality/API tests: `144 passed`.
- Targeted affected-school API sweep: 12 list/detail requests across both locales,
  zero rejected-value or private-curation-metadata leaks.
- Data-quality scoreboard: display-name overrides `21/443`; publishable website-data
  coverage `328/328` with zero published without a report; pricing failing gates
  `205/205`.
- Exhaustive `scripts/audit_p2_12_boundary.py`: `passed=true`, `llm_calls=0`, all
  `1,066` API requests succeeded, no boundary leaks, zero unexplained coordinate groups,
  and exact pricing predicate/scoreboard/API parity at zero published rows.

P2.14(e) remains open for 107 missing published identities and the 48-school/205-row
Sofia pricing queue.
