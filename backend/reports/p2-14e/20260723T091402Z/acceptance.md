# P2.14(e) bounded launch-data curation — slice 2

Date: `2026-07-23`

Result: **PASS for a bounded ten-school offline review; nine identities newly
publish and one remains correctly withheld. P2.14(e) remains open.** No discovery,
refresh, navigation, extraction, validation, summarization, provider work, or
geocoding ran.

## Reviewed identities

| School | Bulgarian identity | English identity | Publication result |
|---:|---|---|---|
| 393 | Детска къща Монтесори | Montessori Children’s House | Published |
| 506 | Англо-американско училище в София | The Anglo-American School of Sofia | Published |
| 521 | Пчелното кошерче | The Beehive | Stored; website-data gate withholds |
| 524 | Малките принцове | Les Petits Princes | Published |
| 556 | Maple Bear Sofia | Maple Bear Sofia | Published |
| 576 | Малки стъпки – Литъл Степс | Little Steps | Published |
| 596 | Maple Bear | Maple Bear | Published |
| 615 | Растеж | Growers | Published |
| 624 | Фънки Мънки | Funky Monkey Kindergarten | Published |
| 635 | Увекинд | Uwekind International School | Published |

Each identity was checked against its legal school, official host, two genuinely
distinct cached official-site paths, and at least one cached core/about/contact/admission
page. The private curation record stores the reviewer, review/cache times, source URLs,
and the existing `website_domain_alias_match` and `repeated_on_page_identity` signals.
The raw evidence and curation metadata remain private.

School 624's extracted slogan-like candidate was not published. It was manually reduced
to the identity-only label `Funky Monkey Kindergarten` before curation.

## Fail-closed school 521 result

School 521 was part of the recorded navigation-failure cohort. Its identity is now
source-backed in storage, but `website_data_is_publishable` remains false. The detail API
therefore continues to expose the mechanical fallback `Pchelnoto kosherche`, not the
curated `The Beehive` value. No validation report was invented and no withholding marker
was bypassed.

## Queue accounting

| Queue | Before slice 2 | After slice 2 | Disposition |
|---|---:|---:|---|
| Missing source-backed **published** English identities | 116 | 107 | Nine newly published |
| Uncorroborated English candidates in the remaining queue | 44 | 34 | Ten reviewed |
| Corroborated identity stored but withheld | 0 | 1 | School 521 |
| Sofia schools with withheld scraped pricing | 48 | 48 | Unchanged |
| Withheld Sofia scraped pricing rows | 205 | 205 | Unchanged |
| Terminal NULL geocodes with failure evidence | 88 | 88 | Accepted; untouched |

Schools 113 and 116 and their dynamic-field discrepancies were not changed. No pricing
row was promoted because accountable human-verification metadata was not supplied.

## Deterministic verification

- Focused resolver/display-gate/data-quality tests: `76 passed`.
- Detail API checks: all ten returned `200`; nine returned the curated identities and
  school 521 returned its expected fallback.
- Data-quality scoreboard: display-name overrides `21/443`; publishable website-data
  coverage `328/328` with zero published without a report; pricing failing gates
  `205/205`.
- Exhaustive `scripts/audit_p2_12_boundary.py`: `passed=true`, `llm_calls=0`, all
  `1,066` API requests succeeded, no boundary leaks, zero unexplained coordinate groups,
  and exact pricing predicate/scoreboard/API parity at zero published rows.

P2.14(e) remains open for 107 missing published identities and the 48-school/205-row
Sofia pricing queue.
