# Bounded English-identity LLM adjudication pilot

- Mode: `live_bounded_adjudication`
- Generated: 2026-07-27T11:21:43.230554+00:00
- Cases: 14 (4 missed known-good, 10 known-bad traps)
- LLM calls: 10 (input 8942 / output 1190 tokens, $0.002056)

## Result

- Recovered known-good identities: 3/4 [393, 506, 624]
- Held for insufficient provenance: []
- Still rejected: [635]
- Known-bad leaked after guards: 0/10 []
- Known-bad accepted by the model before guards: [634]

## Boundary

- No database write, publication, refresh, provider discovery call, OCR, or
  geocode attempt was performed; evidence came only from cached valid pages.
- Accepts are recommendations for manual promotion through
  `app.services.identity_curation`, which still requires 2 same-domain source URLs and a named reviewer.
- Accept floor: confidence >= 0.7; deterministic guards can
  only reject, never accept.

## Cases

| School | Candidate | Expected | Verdict | Reason | Conf | Decision | Guards |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 393 | Montessori Children’s House | accept | accept | own_official_identity | 1.00 | recommend_manual_promotion | — |
| 506 | The Anglo-American School of Sofia | accept | accept | own_official_identity | 1.00 | recommend_manual_promotion | — |
| 624 | Funky Monkey Kindergarten | accept | accept | own_official_identity | 1.00 | recommend_manual_promotion | — |
| 635 | Uwekind International School | accept | accept | own_official_identity | 0.95 | rejected | unverifiable_quote |
| 153 | Email: kindergarten@uwekind.bg | reject | — | no_english_candidate | — | rejected | — |
| 381 | Educational Technologies Schools | reject | reject | network_or_group_label | 0.90 | rejected | — |
| 512 | E-mail: kindergarten@busy-bg.com | reject | — | no_english_candidate | — | rejected | — |
| 514 | Prof. Nikolai Raynov | reject | reject | different_institution_on_shared_domain | 0.90 | rejected | — |
| 546 | ЧОУ ПЕТЪР БЕРОН | reject | — | no_english_candidate | — | rejected | — |
| 569 | Email: office@busy-school.com | reject | — | no_english_candidate | — | rejected | — |
| 587 | Drujba School - Sofia | reject | reject | different_institution_on_shared_domain | 0.90 | rejected | — |
| 589 | Discoverer International School | reject | reject | insufficient_official_evidence | 0.70 | rejected | — |
| 633 | VISIT SCHOOL | reject | reject | contact_or_navigation_label | 0.90 | rejected | — |
| 634 | Uwekind International School | reject | accept | own_official_identity | 0.95 | rejected | institution_class_conflicts_with_education_level |

## Accepts stopped by deterministic guards

Each row is a label the model was willing to publish. A row with a
single guard means that guard is the only defense in front of it.

- School 635 — `Uwekind International School` (expected accept, level upper_secondary, 2 supporting URLs): unverifiable_quote (sole defense)
- School 634 — `Uwekind International School` (expected reject, level kindergarten, 3 supporting URLs): institution_class_conflicts_with_education_level (sole defense)
