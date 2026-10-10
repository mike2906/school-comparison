# Tech debt review and refactor plan

Written 2026-10-10 against `main` at `fdbc0b9` (after PR #218). It is a plan only: no refactor PRs until Mike approves it.

Updated the same day with the findings of the test audit (the 2026-10-10 test audit, kept in the project files rather than the repo). The changes are marked **(test audit)**.

## Verdict: refactor in place, don't start a new project

A rewrite would be the wrong move. The codebase is not spaghetti yet; it is a healthy core with a few overgrown files around it.

- **The safety net is strong for behaviour, weaker for moves.** See the (test audit) caveat below.
- **Raw numbers.** There are 1,826 backend tests, all passing in about 1m45s, with 83% line coverage. The core extraction code is 91-98% covered (`extractor.py`, `extractor_helpers.py`, `price_evidence.py`). Those tests encode months of Bulgarian-specific edge cases: decimal commas, BGN/EUR, age groups by calendar year, display-name junk and so on. A new project would throw that knowledge away and relearn it bug by bug.
- **(test audit) Caveat.** About 150 of those tests cover dead code, so the count will drop in Phase 0. Many extractor and CLI tests also call or patch private functions by module path. That doesn't weaken the case against a rewrite, but it changes how the moves must be done. See the ground rules below.
- **The debt is concentrated.** About ten files hold most of it. Models, routers, schemas and services are small and reasonably layered.
- **Most of the fix is mechanical.** Moving functions into smaller modules, deleting a dead path and merging duplicate helpers are all low-risk changes when the tests stay green.

What a new project *would* buy is a clean frontend in TypeScript. That is not worth a rewrite either. It can be adopted file by file later if wanted (see "Not recommended now").

## Combined sequence (tech-debt plan and test audit)

This section is the single order of work. The phase sections further down hold the detail for each step. Steps are numbered T1-T4 (tests) and P0-P4 (refactor phases).

There are three independent lanes. Within a lane, order matters. Across lanes, PRs can interleave freely.

### Lane A: backend tests, then backend refactor

| # | Step | Kind | Depends on | Can start |
|---|---|---|---|---|
| T1 | **Fix the time bomb.** `test_extractor.py:4789` relies on `date.today()` and fails from 2027-09-15. Freeze the date. | Test only, 1 small PR | nothing | Now |
| T2 | **Rewrite the ~10 tests that can't fail.** Only those on code that stays: the `test_api.py` compare-five, crossover and search tests, `test_data_quality.py:275`, the validator dedup test, the timeout tests and the CLI usage totals. | Test only, 1-2 PRs | nothing | Now |
| T3 | **Pin the unprotected behaviour** found by mutation testing: the `display_gating` monthly multiplier, peer median, January-June admission cycle and `NVO_MIN_PUPILS` boundary; the `{subject: value}` asserts in the three NVO import tests; the year-in-fee-table filter and the month and ordinal tables in `price_evidence`. | Test only, 1-2 PRs | nothing | Now |
| T4 | **Write the price-parsing and BGN tests as one shared table.** It covers mixed separators, `6.200 лв.` and BGN detection, and runs against *both* amount parsers (`extractor_helpers._parse_price_amount_token` and `price_evidence`'s). | Test only, 1 PR | nothing | Now; it touches no production code |
| P0 | **Delete dead code together with its ~150 dead tests:** Celery, the finished scripts, `education.py` if Mike agrees, and the no-ops. Also add the AGENTS.md "no new code in giant files" rule. | Deletion, 2-3 PRs | nothing | Now |
| T5 | **Switch the 28 price-LLM mocks** from `PriceExtractionOutput` to `PageFees`, so `_prices_from_fee_lines` is actually exercised. | Test only, 1 PR | nothing, but it edits `test_extractor.py` | After Monday, since #212/#217 just touched this area |
| T0 | **Record LLM answers (before Monday).** An opt-in `EXTRACTION_LLM_RECORD_DIR` setting saves each successful extraction LLM answer to disk, keyed by a hash of the prompts, output type and tier. It is off by default, so behaviour is unchanged. With it set during Monday's run, the schools re-extracted then can later be replayed for free. It only covers those schools; a full LLM baseline would need a full recorded extraction run. | Code, 1 small PR | nothing | Now |
| T6 | **Offline extraction diff, added after the Monday question.** A small script runs `app/scrapers/deterministic.run_deterministic_extraction` over every school's stored pages in the launch DB and writes one JSON line per school. It needs no network, LLM or DB writes. It runs once on the code before a refactor and once after, and any difference blocks the PR. This covers the deterministic layer for all schools; the 20-case golden corpus already covers it for a sample in CI. **It does not cover the LLM half.** Raw LLM answers were not stored, so re-running the LLM steps meant paying for new calls. T0 starts recording them, and T6 adds a replay mode for the recorded answers. Schools without a recording are covered only by the unit tests, which is why T5 matters. | Script, 1 PR | nothing | After Monday (it reads the launch DB on Mike's PC) |
| P2 | **Untangle `cli.py`.** Every move PR retargets the patches on the moved functions (see the ground rules). | Refactor, 3-4 PRs | P0 (Celery gone, so stage logic exists only once), T2 (the CLI usage-total tests fixed) | After Monday |
| P3 | **Split `extractor_helpers.py`** and dedupe it. | Refactor, 6-8 PRs | T4, T5 and T6 (step 13, merging the parsers, is only safe when the shared table passes on both parsers before the merge and on the survivor after), and P2 for the shared helper modules | After P2 |
| P4 | **Split the long functions** (`validate_school_data`, `_extract_prices`, `_extract_general_info`). | Refactor, 3 PRs | P3, plus T5 for `_extract_prices` | After P3 |

### Lane B: frontend

| # | Step | Kind | Depends on | Can start |
|---|---|---|---|---|
| P1a | **Move the duplicated helpers into `src/utils/`, with tests in the same PR.** Tests for the admission and NVO helpers are required. They can't come first, because the in-component copies aren't exported, so they become testable only by moving. | Refactor and tests, 2-3 PRs | nothing | Now |
| P1b | **Fix the `SchoolCard` currency display and the locale drift.** | Behaviour change, 1 PR | P1a | After P1a |
| P1c | **Split the big components.** | Refactor, 3 PRs | P1a | After P1a |

### Lane C: decisions that are not code moves

- **The data-quality scoreboard metric needs redefining.** The test audit found that `_website_validation_coverage` can only ever report 0 published without a report and 100% coverage. That is a product question about what the metric should mean, not a refactor. It is parked until Mike decides, and its five constant-asserting tests are left alone until then.

### Where this differs from the suggested order "all tests first, then Phase 0"

- **T2 skips tests on code that P0 deletes.** Rewriting the `test_pipeline_integration.py:262-296` tests or the Celery tests would be wasted work, so they are simply deleted in P0. The same goes for the identity-adjudication tests if the pilot script goes.
- **T1-T4 and P0 don't depend on each other.** Tests on live code and deletions of dead code don't overlap, so they run side by side rather than one after the other. The real dependencies are later: T4 and T5 before P3, and the patch rule for P2-P4.
- **T4 is written against both parsers.** Testing only the helpers' parser would leave the merge in step 13 unproven. With one table that both pass, the merge becomes a provable no-op for those inputs.
- **The frontend tests come with the move, not before it** (P1a), for the reason given in that row.
- **T5 waits for Monday.** It edits the same test file as the extractor work that just merged.

Phases P0 and P1 alone, together with T1-T4, remove about 3,000 lines, fix a likely display bug and close the gaps that mutation testing found. If the appetite is limited, stop there and re-assess.

## Where the debt is

### 1. Four oversized backend files

| File | Lines | Problem |
|---|---|---|
| `app/scrapers/extractor_helpers.py` | 4,967 | Contains 165 private `_foo` functions in about 17 unrelated clusters: display names, price-line parsing, price dedupe, contacts, page selection, summary normalisation and others. Many other modules import its "private" helpers. `_get_usage` has 5 external callers. |
| `app/scrapers/cli.py` | 4,683 | This is not just a CLI. It holds about 56 DB query sites, the age-group inference rules, geocode repair, and raw Nominatim HTTP calls (`cli.py:1633-1690`) that bypass `app/services/geocoding`. It has 58% coverage, the lowest of the big modules, and its two stage dispatch chains are duplicated (`:967-997` and `:1079-1121`). Tests import its private helpers, which locks the file in place. |
| `app/scrapers/extractor.py` | 2,351 | `_extract_general_info` is 340 lines and `_extract_prices` is 315, each with nested closures. |
| `app/scrapers/validator.py` | 1,734 | `validate_school_data` is one function of **582 lines**, the longest in the repo. |

### 2. A dead second pipeline: Celery

`tasks/scrape_tasks.py` (1,123 lines, 21% covered) re-implements every stage's cohort query and batch loop. Nothing runs it:
- `cli run` without `--sync` just prints "Celery mode not yet implemented" (`cli.py:884`).
- The beat schedule is empty, and no worker is defined in docker-compose. Prod has no Redis.
- The README and deploy README both say Celery is not used.

The copy has already drifted from the CLI. Its website-discovery cohort differs, it has no recovery stage and no cost tracking, and it alone has spot checks. That makes it a trap for any agent that greps for a stage. `AGENTS.md` still says "wired into the CLI/Celery pipeline" and "Redis is for Celery".

### 3. Duplicated logic (backend)

- **`_normalize_text_list` exists three times** with different behaviour: `extractor_helpers.py:4243`, `summarizer.py:147` and `validator.py:161`. `school_attributes.py:209` is a fourth near-copy.
- **Founded year is parsed three ways, and two of them can disagree.**
  - `extractor_helpers.py:4583` takes any `(19|20)\d{2}`.
  - `validator.py:206` requires 1800 up to the current year.
  - `extractor_helpers.py:464` is a third path.
- **Currency and period detection are written twice.**
  - The helpers have their own currency sets (`extractor_helpers.py:1296`) and period regex chains (`:1468` and `:1528`), even though they already import `price_evidence`.
  - `price_evidence` handles `$`, `£`, USD and GBP. The helpers don't.
- **There are about eight "normalise text for comparison" functions** across `price_evidence`, `extractor_helpers`, `validator`, `school_attributes` and `extractor`.
- **Class size is parsed twice.** `school_attributes.py` checks plausibility and `extractor_helpers.py:486` doesn't.
- **The per-country rules indirection is unused.** The `_ACTIVE_RULES` ContextVar (`extractor_helpers.py:57`) is never set, and `extraction_rules/bg.py` just re-exports `base`. Yet there are 122 `_rules()._X` call sites routing through it.
- **Two modules have near-identical names.** `app/scrapers/summarizer.py` (pipeline/DB) and `app/ai/summariser.py` (LLM) differ only by z/s.

### 4. Tangled imports

- **Import cycle:** `url_validator` → `campus_sync` → `shared_site_check` → `url_validator`. It is held together by imports inside functions.
- **The AI layer imports from the scrapers layer.** `ai/summariser.py` imports `scrapers.extractor_helpers._get_usage`.
- **Private names cross module boundaries.** `navigator.py` imports `price_evidence._PRICE_RE`.

### 5. Frontend: big components and copy-pasted helpers

| File | Lines | Problem |
|---|---|---|
| `SearchPage.jsx` | 2,192 | One component of about 2,100 lines, with 29 `useState` and 25 `useEffect` calls. It mixes URL sync, localStorage, geolocation, filtering and sorting, and a 200-line inline advanced-filters renderer. |
| `ComparePage.jsx` | 1,951 | About 30 helpers plus 9 inner components in one file. |
| `SchoolMap.jsx` | 1,592 | Ten inner components. Its type colours differ from `SchoolCard`'s. |
| `SchoolCard.jsx` | 1,526 | About 25 helpers. The component itself is about 940 lines. |

The costlier problem is duplication, because it causes display drift between pages:
- **NVO, admission and status helpers are copied three ways.** The benchmark, trend and performance styles, `getAdmissionRequirement`, `getLastAdmittedPoints`, `getMinNvoScore`, `getStatusInfo` and the language labels all exist in `SchoolCard`, `ComparePage` and `SchoolDetailPage/helpers.js`.
- **There are five price/number formatters.**
- **Likely user-visible bug.** The expanded pricing section in `SchoolCard.jsx:538-550` prints raw amounts with `item.currency`. Every other price surface goes through `displayPrice` / `toEur`, so a BGN row would show as BGN on the card and as EUR elsewhere. To be confirmed against real data.
- **The locale mapping is inconsistent.** Most places map to `en-US` and `KeyFacts` maps to `en-GB`.
- **Distance has a hardcoded unit.** `formatDistance` hardcodes "km" and the "." decimal separator, which is wrong in Bulgarian.

There are no component tests. The 25 frontend tests cover pure utils only. `react-hooks/exhaustive-deps` is switched off, which is risky in a component with 25 effects.

### 6. Accumulated one-off scripts

There are 11 finished one-off scripts in `backend/scripts/`, about 2,600 lines in total:
- `audit_p2_12_boundary.py`, `repair_p2_12_boundary.py`, `geocode_pin_gaps_uf44.py`
- `merge_kindergarten_buildings.py`, `backfill_nvo_pupil_counts.py`, `backfill_i18n_en.py`, `cleanup_synthetic_i18n_en.py`
- `refresh_pricing_evidence.py`, `pilot_identity_adjudication.py`, `repair_city_scope.py`

Several are kept alive only by their own tests. Git history already preserves them.

### Not debt (leave alone)

- **Large test files.** `test_extractor.py` is 5,554 lines, which is fine for a table of edge cases.
- **Size alone.** `price_evidence.py` (1,105 lines, 98% covered, freshly reworked) is cohesive.
- **API surface.** Routers, models and schemas are small.
- **Config.** No environment variables are read outside `config.py`.

## The plan

Every step below follows the same ground rules:
- **One small PR per step.** No behaviour change unless the step says so.
- **Moves keep a re-export shim.** The old module re-exports what moved, so callers and tests don't all change in one PR. Callers are updated in a follow-up PR and the shim is dropped at the end.
- **Pure-move PRs must show the full test suite green, and that is not enough on its own (test audit).** If a test patches a function at its old path (`patch("app.scrapers.cli._run_x")`, `patch.object(scraper_cli, ...)`), the patch silently stops intercepting once the function moves. The shim keeps the name importable, so nothing errors and the test runs real code instead.
- **Rules that follow from that:**
  - Every move PR greps `tests/` for `patch(` and `patch.object(` targets naming each moved function, and retargets them to the new module in the same PR.
  - The shim never re-exports a name that any test patches. A stale patch then raises `AttributeError` instead of passing quietly. This is safe for `cli.py`, because nothing in `app/` or `scripts/` imports it.
  - Each PR body lists the patch targets it changed, and reviewers check that list.
  - Plain calls through the shim (`helpers._X(...)` in `test_extractor.py`) are fine. They still exercise the moved code.
- **Review follows the merge policy.**
  - Small PRs get a fresh-context code review.
  - The big pure-move PRs are larger in diff than in risk, so Codex is best spent on the dedupe steps (3.x and 5.x), where behaviour actually changes.

### Phase 0: delete and document (can start any time; no overlap with Monday's data work)

1. **Remove the Celery path.** Delete `tasks/`, the `celery[redis]` and `redis` dependencies, and the Redis service in `docker-compose.yml`. Remove the CLI's non-`--sync` branch. Fix the `AGENTS.md` wording. Retire the tests that only exercise `tasks/`, after porting the one useful thing (spot checks, if still wanted) to the CLI. About −1,300 lines.
   - *Decision for Mike:* are spot checks still wanted as a stage? If yes, port them to the CLI first. If no, drop them too.
   - Delete the Celery tests in the same PR: `test_pipeline_integration.py:299-396` and `test_summarizer.py:613` (test audit).
2. **Archive the finished one-off scripts** above, together with their own tests (test audit). The list:
   - `test_refresh_pricing_evidence.py`, `test_p2_12_boundary_repair.py` and `test_identity_adjudication.py`. The last one also needs one new test for `_registrable_domain`, the only part of it used in production.
   - Most of `test_sofia_municipal_points.py` and `test_city_scope_repair.py`. Keep the 2 GeocodingService tests and the settlement-name test.
   - The no-op `_ensure_i18n_fallbacks` and the unused `get_openai_model`, with their tests.
   - *Decision for Mike:* `app/utils/education.py`, with 56 tests, is never imported by the app. The test audit recommends deleting it and writing the admission calculator test-first when that work starts.

   Together with step 1, this removes about 150 tests. The count drops, but no live code loses protection.
   - Keep `audit_price_rows_uf45.py` if it is still used as a standing price audit.
3. **Add a working rule to `AGENTS.md`.** No new functions go into `cli.py`, `extractor_helpers.py`, `extractor.py` or `SearchPage.jsx`. New code goes into a focused module. This stops the growth while the rest of the plan runs, and costs nothing.

### Phase 1: frontend shared helpers (independent of the backend, user-visible payoff)

4. **Move the duplicated helpers into `src/utils/`.** This creates `nvoDisplay.js` (benchmark, trend and performance), `admission.js`, `format.js` (currency, number, percent, distance and one locale mapping) and `schoolLocation.js` (age groups, shifts, primary location). Delete the copies in `SchoolCard`, `ComparePage`, `SchoolMap` and `SchoolDetailPage/helpers.js`, and add node tests for the moved helpers. Tests for the admission and NVO helpers are required, not optional (test audit). Split this into 2-3 PRs by helper group.
5. **Fix the currency bug and locale drift (behaviour change, small).**
   - `SchoolCard`'s expanded pricing goes through `displayPrice`.
   - One locale mapping is used everywhere.
   - `formatDistance` is localised.
6. **Split the big components one PR each, with no behaviour change.**
   - `ComparePage`: move the row and section components into files.
   - `SearchPage`: extract `useUrlFilters`, a `useUserLocation` hook and an `AdvancedFilters` component.
   - `SchoolMap`: move the inner components out.
   - Each is checked in the running app (screenshots before and after) as well as with lint and build.

### Phase 2: untangle `cli.py` (backend; after Monday's run)

The patch-retargeting rule above matters most here. The `_run_all_stages_batch` tests patch 6 sibling functions on the `cli` module (test audit).

7. **Make the helpers the tests depend on public.** Move the cohort selectors (`_select_all_stage_cohort` and the per-stage queries) into `app/scrapers/cohorts.py`. Move the age-group inference into `app/services/age_groups.py`. Point the tests at the new modules. This unlocks everything else in this phase.
8. **Route the geocode-repair commands through the geocoding service.**
   - Move `_repair_oblast_geocodes_command` and the out-of-bounds/location repairs into `app/services/geocoding/repair.py`.
   - Replace the raw Nominatim calls in `cli.py` with the service's provider tiers.
   - This changes behaviour slightly, so it goes in its own PR, checked with the geocoding tests.
9. **Move the stage runners** (`_run_*_batch`) next to their stages. Collapse the two `if stage ==` chains in `_run_sync` into one table. `cli.py` ends up as thin Click wrappers, aiming for under 1,000 lines.

### Phase 3: split `extractor_helpers.py` (after Monday; it is the code PRs #212 and #217 just changed)

Prerequisites from the test audit (test-only PRs, done before step 10):
- **Fix the price LLM mocks.** 28 test calls mock the price LLM with the old `PriceExtractionOutput` type. Only 3 use the real `PageFees`, so `_prices_from_fee_lines` is barely exercised. Switch the mocks to `PageFees`.
- **Add the missing price tests.** These are the price-parsing, BGN and monthly-multiplier tests listed in the test audit's section 3. Without them, step 13 (merging the two price parsers) is a behaviour change with only one of the two parsers under test.

10. **Turn it into a package by cluster.** This is a pure move with a shim that re-exports everything. Suggested modules:
    - `display_name.py`, `price_lines.py`, `price_filters.py`
    - `contacts.py`, `page_selection.py`, `section_text.py`
    - `summary_source.py`, `llm_usage.py`
    - Promote the cross-module "private" functions to public names (`get_usage`, `normalize_display_name_i18n`).
11. **Remove the unused `_ACTIVE_RULES` indirection.** Call the rules module directly, replacing 122 call sites. If multi-country extraction comes, it can be reintroduced at the one place it is needed.
12. **Dedupe the text normalisers into one `app/utils/text.py`, each with a test.** This covers text-list dedupe, the comparison key, markdown-link stripping and `_now_iso`. It changes behaviour subtly, so it is one PR per helper, with the differing behaviours pinned in tests first.
13. **Make the price helpers use `price_evidence` for currency and period.** This deletes the helpers' own sets and regex chains. It is a behaviour change (`$` and `£` become recognised). Run it with the price-audit evidence rows in `price-audit/` as a regression check.
14. **Define the founded-year and class-size rules once each,** in `school_attributes`, with the validator and extractor calling them.
15. **Rename `scrapers/summarizer.py`** to `summary_pipeline.py`. Move `get_usage` out of `scrapers` so `ai/` stops importing from it. Break the `url_validator` / `campus_sync` / `shared_site_check` cycle by moving the shared pieces into a small leaf module.

### Phase 4: long functions

16. **Split `validate_school_data`** (582 lines) into per-section validators.
17. **Split `_extract_general_info` and `_extract_prices`** into named steps, replacing the nested closures.

These come last because they are the riskiest, and they get easier once phases 2 and 3 have made the helpers they call importable and testable on their own.

## Not recommended now

- **TypeScript migration.** It is a big churn for a solo project. If wanted later, enable `allowJs` and convert new utils only.
- **Turning `react-hooks/exhaustive-deps` back on globally.** It would flood `SearchPage` with warnings. Turn it on per file as each component is split in step 6.
- **Adding `ruff format`.** `pyproject.toml` deliberately has no formatter, to avoid reflowing prompt strings. Reformatting the whole codebase would also bury `git blame` mid-refactor. Revisit after phase 3 if wanted.
- **React Query, Redux or a state library.** There is no real problem for one to solve yet.
- **Coverage targets or file-size CI gates.** The `AGENTS.md` rule in step 3 is enough for a one-person repo.

## Decisions taken (2026-10-10)

Mike asked for the work to go ahead without waiting for him, so these defaults were taken:

1. **Phases 0 and 1 and the test-only steps start now.** The price, extractor and CLI phases wait until after Monday's data steps.
2. **Spot checks: nothing to port.** The CLI's validate-data stage already runs sampled spot checks (`_run_validate_data_batch`). The Celery task was a duplicate.
3. **`audit_price_rows_uf45.py` and `refresh_prices.py` stay.** They are still price tooling, and `price_evidence.py` cites the audit.
4. **`app/utils/education.py` is deleted with its tests.** The admission calculator gets written test-first when that work starts.
5. **Kept on purpose:** `merge_kindergarten_buildings.py` (needed again if kg.sofia.bg is re-imported), `import_sofia_municipal_points.py` (the geocoding write gate references it), and `pilot_identity_adjudication.py` with its service, which needs its own review before deletion.
6. **Still open for Mike:** what the data-quality scoreboard's website-validation coverage should measure (lane C).
