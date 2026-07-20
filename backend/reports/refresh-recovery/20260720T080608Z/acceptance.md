# Sofia refresh recovery and launch acceptance

Date: `2026-07-20`

Result: **PASS at the fail-closed launch boundary.** The recovered pipeline is correctly
`partial`: 344 schools completed fresh navigation, extraction, and deterministic validation;
9 navigation failures and 33 URL-validation failures remain withheld. Phase 3 was not started.

## Run ledger

| Run | Status | Evidence |
|---|---|---|
| `4749f137-dddd-488f-9c27-211addfe8bde` | `partial` | Original fixed cohort: 386. Interruption recovered after 362 URL successes, 24 URL failures, and 20 navigation successes; extraction/spot checks/summarization never started. No live scraper process remained. |
| `483c1e91-5825-492b-8faa-92cffa2714cf` | `failed` | Explicit-cohort preflight rejected the 24 IDs whose failed URL validation had correctly cleared `website_url`; no stage or LLM work ran. |
| `585370ca-82ef-48ca-9bf5-84e079467857` | `partial` | Retried the exact current subset. URL validation produced 354 valid, 1 invalid, and 7 ambiguous. Whole-cohort navigation stalled; it was interrupted before extraction, motivating the bounded-navigation fix. |
| `882ff309-5601-4ddc-a0f4-da2b73d0a82f` | `partial` | Final bounded recovery: 353 URLs passed, 344 navigation successes, 9 timed-out navigation failures, 344/344 extraction successes, 344/344 deterministic validation successes, and 10 capable-tier spot checks. Summarization skipped. |

The nine bounded navigation failures were schools `179, 274, 301, 369, 521, 547, 559,
630, 633`. None has a current Stage 6 report, so website-derived values remain withheld.
The final fixed-cohort state is 345 `extracted`, 8 `navigated`, and 33 `failed_validate`;
the extra extracted state is historical and does not change the 344-school fresh-completion
count.

## Cost accounting

The best stored pre-refresh provider baseline is `$10.47598213`. Final provider usage was
`$10.77499006`, an interval delta of **`$0.29900793`**. The key is shared and has
`limit=null`, so that delta may include unrelated activity; all of it is conservatively
charged to this recovery.

Pipeline/provider evidence by segment:

- Original interrupted run: stored pipeline cost `$0`; conservatively assigned provider
  delta `$0.00530230`.
- Cohort-preflight failure: `$0`.
- First recovery: stored pipeline cost `$0.003727`.
- Final bounded recovery: stored pipeline cost `$0.286992` (`$0.272319` extraction plus
  URL fallback/spot-check usage).
- Targeted school 537/546 re-extractions: no PipelineRun cost row; exact provider interval
  delta `$0.01236110`.

Summing the most conservative available segment evidence gives **`$0.30838240`**, below
the approved combined hard ceiling of `$1.50` by `$1.19161760`. Pipeline estimates and
provider billing do not reconcile exactly, so the larger segment sum is used for the cap.

## Coverage

- Sofia database scope: 443 schools; fixed refresh cohort: 386.
- Freshly completed navigation/extraction/validation: 344.
- Partial/withheld within the fixed cohort: 9 navigation failures and 33 URL-validation
  failures. No discovery or cohort broadening ran.
- Current Stage 6 reports: 368/443. Publishable website-data coverage: **328/328 (100%)**,
  with zero published without a report.
- Summaries skipped; website-derived admissions remain disabled; NVO stayed unchanged at
  **5,115 rows**.
- Pricing curation backlog: **48 schools / 205 scraped rows**, all withheld; API/predicate/
  scoreboard publishable set is empty.

## Launch acceptance

| Criterion | Result | Evidence |
|---|---|---|
| Full API boundary | PASS | 1,066/1,066 BG/EN list/detail/compare requests returned 200 across all 443 Sofia schools; 442 are listable and school 338 remains detail/compare-only because it has no location. |
| List-view NVO | PASS | `/schools` again eager-loads and serializes `exam_results`; focused API regression and frontend NVO tests pass. |
| Validation-report gate | PASS | 328/328 publishable schools have current reports; zero publish without a report. |
| Summaries/admissions/pricing | PASS | Zero summaries, website admissions, scraped pricing, or withheld pricing rows serialized. |
| Provenance/internal keys | PASS | Zero raw/rejected provenance values and zero internal keys serialized. |
| Display text/name quality | PASS | Zero Markdown, bare-URL, email-contact, or corroborated page-prose labels serialized. Eleven identity overrides remain publishable; eight corroborated prose candidates are semantically withheld. |
| Geocoding | PASS | Force run processed 480 locations: 393 initially accepted and 87 failed; targeted correction leaves 392 geocoded with 392/392 precision metadata and 88 terminal NULL failures. Zero out-of-bounds, partial-coordinate, invalid-terminal, or unexplained duplicate cases. |
| Cached truth sets | PASS | Runs `db4ba90d-6894-4164-a873-34ee79687fad` and `396dffe5-c905-44e2-8582-1d4c1bcdbd66` match expected partial statuses, 18-school cohorts, and costs `$0.029856` / `$0.923238`; known rejected cases do not leak. |
| Spot checks | PASS, withheld | 10 checked; schools 113 and 116 had discrepancies (unsupported founded year; omitted class-size plus founded-year contradiction). These dynamic fields are outside launch scope and remain withheld. |
| Pricing parity | PASS | Shared predicate, scoreboard, and API all publish zero row IDs. |

Deterministic commands: `scripts/audit_p2_12_boundary.py` (`passed=true`, `llm_calls=0`),
the data-quality scoreboard, and `docs/audits.sql` with `ON_ERROR_STOP=1` all completed.
The full backend suite passed **865 tests** (141 pre-existing deprecation warnings).
Frontend verification passed all **7 tests**, ESLint with zero warnings, and the Vite
production build (existing bundle-size advisory only).

## English display-name audit and curation failures

The refresh did not justify weakening the corroboration gate. School 520's previous
uncorroborated `Fusion School` candidate was removed; it still resolves through the
mechanical fallback because no corroborated source-backed identity was regenerated.

A deterministic private/international audit found **117** website-backed Sofia schools
with neither a registry English name nor a publishable corroborated English identity; 44
retain an uncorroborated English candidate. Obvious contact-label candidates at schools
153, 512, and 569 are withheld. These 117 require human curation/source confirmation:

`149, 150, 153, 154, 155, 157, 171, 172, 175, 177, 178, 179, 180, 182, 183,
184, 185, 187, 213, 214, 215, 271, 272, 286, 287, 300, 301, 302, 329, 330,
339, 350, 372, 381, 393, 404, 405, 506, 507, 510, 511, 512, 514, 516, 520,
521, 522, 524, 525, 526, 527, 529, 530, 531, 533, 534, 535, 536, 537, 538,
539, 541, 542, 544, 545, 546, 547, 548, 550, 552, 554, 556, 558, 561, 562,
564, 565, 568, 569, 572, 574, 576, 577, 581, 583, 584, 585, 587, 588, 589,
590, 591, 592, 595, 596, 597, 598, 599, 600, 601, 602, 603, 606, 609, 610,
613, 615, 624, 625, 628, 629, 630, 631, 632, 633, 634, 635`.

The heuristic page-evidence audit inspected 353 website-backed schools and produced 205
review leads; it is intentionally high-recall and is a curation worklist, not a publication
signal. A CLI shadowing bug discovered during this audit was fixed (`builtins.list`).

## Structural fixes closed during recovery

- Batch `all` uses bounded navigation chunks, so isolated slow websites become explicit
  partial failures instead of stranding the run.
- Corroborated display names also pass deterministic identity semantics; contact labels,
  copyright/admission/SEO prose, and overly long labels stay internal.
- Nominatim results without a house number are `approximate`; approximate results cannot
  collapse distinct addresses onto one coordinate. Location 1147 is now terminal NULL.
- The verified Dr Petar Beron shared campus (locations 1082/1091) is documented in the
  duplicate allowlist.
- The display-name scoreboard now counts the same semantically publishable override set as
  the API.

No launch criterion was waived. Stop condition: do not begin Phase 3.
