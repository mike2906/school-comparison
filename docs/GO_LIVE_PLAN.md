# Go-Live Plan — Consolidated from three AI reviews (July 2026)

Sources: full project review + data-quality architecture review (2026-07-06), recruiter
assessment (2026-07-07). This file is the single backlog. Each task is written so a
coding agent (Claude Code or Codex) can execute it in one session with no extra context.

Evidence paths under `backend/reports/` point to local run reports. They are gitignored
and not in the repository.

**Core diagnosis (why we're in circles):** quality signals are recorded *after*
publication instead of enforced *before* it. Extraction writes straight into the rows
the API serves; validation status, confidence scores, and FieldSource provenance are
written but never read at display time. Every extraction bug is instantly a UI bug,
and every fix lands as a repair command or client-side filter instead of a gate.

**Standing rule:** a repair command may only be merged together with the pipeline gate
that makes it unnecessary for future runs. No more compensating transactions.

**Definition of "live":** deployed, Sofia data passing the quality gates, README
presentable, CI green. Not: TypeScript migration, multi-country polish, dashboards.
**Revised 2026-07-15:** launch is deliberately sparse and fail-closed — generative
summaries, website-derived admission fields, and scraped pricing are out of launch scope.
See "Launch-scope revision (2026-07-15)" at the end of Phase 2.

---

## Phase 0 — Verification substrate (do FIRST; ~half a day; prerequisite for agent autonomy)

Agents can only work autonomously when there's a cheap objective pass/fail signal.

- [x] **P0.1 Fix the failing test.** `backend/tests/test_pipeline_integration.py::test_validate_invalid_url`
      fails on current branch. Diagnose and fix (test or code, whichever is wrong).
      *Verify:* `cd backend && uv run pytest` → 0 failures.
- [x] **P0.2 GitHub Actions CI.** Two jobs: backend (`uv sync`, `uv run pytest`,
      `uv run ruff check` once P4.2 lands — until then pytest only) and frontend
      (`npm ci`, `npm run lint` if configured, `npm run build`, `npm test` if vitest exists).
      Use `astral-sh/setup-uv`. Badge in README.
      *Verify:* workflow green on push.
- [x] **P0.3 Clean the tree.** Delete `*Zone.Identifier`, `repomix-output.xml`,
      `NVO_CHART_EXAMPLE.html`; move `sofia_school_compare_plan_v2.md` and
      `PROJECT_STATUS.md` to `docs/`. Add patterns to `.gitignore` (note: repo has a
      file literally named `gitignore:Zone.Identifier` — remove it too).
      *Verify:* `git status` clean, `ls` at root shows only real project files.

## Phase 1 — Break the data-quality circle (the publish boundary)

Order matters within this phase; tasks touch overlapping files — run sequentially.

- [x] **P1.1 Serialization allowlist (CRITICAL).** `backend/app/schemas/school.py`:
      `SchoolAttributes` currently `extra="allow"` and `SchoolListResponse.attributes`
      is a raw dict — the API ships `data_validation`, raw `extracted` LLM payloads,
      `source_refs`, `moe_*` codes to the browser, and `frontend/src/utils/schoolAttributes.js:180-236`
      merges raw `extracted` client-side. Fix: build the display payload server-side
      from an explicit allowlist; do the `extracted` merge in the schema layer; stop
      serializing `extracted`, `extracted_i18n`, `data_validation`, `source_refs`, `moe_*`.
      Update frontend to consume the merged payload (it mostly just renders what it's given).
      *Verify:* API response for a scraped school contains no internal keys; UI unchanged
      for a clean school.
      **Done.** Projection lives in `app/utils/school_attributes.py`; API serves
      `attributes` + `attributes_i18n: {bg, en}` (both `extra="forbid"`). `SchoolResponse`
      leaked identically to `SchoolListResponse` and was fixed too. `display_name_i18n`
      is no longer served (`resolved_name_i18n` supersedes it). Attributes payload
      shrank 74% (1349 KiB → 346 KiB across 540 schools). Parity with the old
      client-side merge verified on all 540 schools × 2 locales × 6 fields: 0 mismatches.
      `SchoolService.matches_filter` now reads the same projection, so `?facilities=…`
      matches extracted data instead of nothing. See P1.9 for the part deliberately
      left out, and P1.11 for the dead UI reads this exposed.
- [x] **P1.2 Validate inside extract.** `backend/app/scrapers/extractor.py:~1023-1025`
      overwrites `attributes.extracted` and deletes the previous validation report at
      commit time, so unvalidated data is live until Stage 6 runs. Fix: run
      `validate_school_data` (deterministic checks + evidence filtering) inside the
      extract stage before commit, so evidence-filtered data is the only data that goes
      live. Stage 6 becomes audit + sampling (spot checks, cross-school checks).
      *Verify:* extraction of a fixture school never leaves an unvalidated window;
      existing pipeline tests pass.
      **Done.** `extract_school` now flushes staged pricing/general-info/provenance rows,
      runs deterministic validation in the same transaction, and commits only the
      validation-filtered payload plus `data_validation`. Validation failure rolls back
      the extraction transaction instead of publishing. The extractor test now asserts
      unsupported fields are removed before commit and a current validation report exists.
- [x] **P1.3 Geocode precision model + write-time rejection.** `backend/app/services/geocoding/base.py`:
      add `method` (`geojson_name_match` | `nominatim_address` | `nominatim_fallback`)
      and `precision` (`exact` | `approximate`) to `GeocodingResult`; persist to a new
      non-serialized `SchoolLocation.geocode_meta` JSON column (autogenerate migration);
      refuse to write a GeoJSON name-match point another location already holds with a
      different address; write NULL instead of wrong (UI already hides NULL coords).
      Add Sofia municipality bbox sanity check at write time, else NULL + warning.
      *Verify:* duplicate-coordinate audit query returns fewer groups after a re-geocode
      of affected schools; unit tests for the rejection paths.
      **Done.** `GeocodingResult` now carries controlled `method` / `precision`, accepted
      and rejected writes persist private `geocode_meta`, and the shared write gate rejects
      duplicate approximate GeoJSON name matches with different addresses plus strict
      Sofia-city out-of-bounds points. The gate is wired through both `GeocodingService`
      and source-adapter upserts, so MoE-imported coordinates pass through it too.
      The write gate uses the same Sofia municipality bounds as the API/map filters,
      so Bankya-style Stolichna municipality locations remain visible.
- [x] **P1.4 Display-name corroboration gate.** Display name currently overrides the
      registry name in `i18n_resolver.resolve_name_i18n` with no corroboration. Fix:
      only allow override when ≥2 independent signals agree (website-domain alias match
      + repeated on-page identity — both already computed in
      `extractor_helpers._extract_host_seed_aliases` / `_promote_repeated_display_name_candidate`);
      otherwise fall back to registry `name_i18n`. Then retire the `cleanup_display_names`
      repair command.
      *Verify:* tests covering corroborated vs uncorroborated cases; audit query
      (display vs registry names) shows overrides are plausible names, not headlines.
      **Done.** Extraction now stores private `display_name_evidence` only when the
      display name matches both a website-domain alias and repeated on-page identity.
      `resolve_name_i18n` ignores `display_name_i18n` without that corroboration, so
      stale or headline-like display names fall back to registry-derived names at the
      API boundary. The `cleanup-display-names` repair command and its mutation helper
      were removed; `audit-display-names` remains for inspection.
- [x] **P1.4a Tighten repeated display-name identity.** Follow-up from PR review:
      `repeated_on_page_identity` currently compares normalized brand-core tokens, so
      related labels such as `Maple Bear Sofia` and `Maple Bear Academy` can collapse
      to the same identity key. Require the same normalized display label (or an
      equivalently strict canonical label) to appear on at least 2 pages before the
      repeated-page signal is granted.
      *Verify:* regression test: proposed `Maple Bear Sofia` is rejected when pages
      repeat `Maple Bear Academy`; existing valid repeated-name cases still pass.
      **Done.** The repeated-page identity key now preserves the normalized full
      display label instead of collapsing to brand-core tokens, so institution and
      location qualifiers remain part of the repeated-name proof.
      - [ ] *Cleanup (low priority, non-blocking).* `_refine_display_identity_label`
            (`backend/app/scrapers/extractor.py`) duplicates the prefix-stripping
            sequence in `helpers._normalize_display_name_case`. Dedupe into a shared
            helper in a focused PR — the two intentionally diverge (identity keying is
            more aggressive), so keep regression tests green and change no gate behavior.
- [x] **P1.5 Golden-fixture corpus.** Commit ~20 real cached `source_pages.raw_markdown`
      pages + expected pricing/display-name/general-info outputs as pytest fixtures.
      Run the deterministic extraction path against them in CI. Every future pricing/
      display-name fix adds a fixture instead of gambling.
      *Verify:* `uv run pytest tests/test_golden_corpus*` green in CI.
      **Done.** 20 real Sofia schools captured under `tests/golden_corpus/cases/`
      (167 pages, ~2 MB; low-signal uncategorized gallery/news pages trimmed, all
      categorized pages kept). The deterministic (no-LLM/no-network/no-DB) path lives
      in the app as `app/scrapers/deterministic.py::run_deterministic_extraction`,
      composed entirely of the same leaf helpers production runs — `_supported_price_rows`,
      `_normalized_price_fields`, `_build_extracted_attributes`,
      `_build_deterministic_general_info_output`, `_build_display_name_evidence` — which
      were extracted from `_extract_prices`/`_extract_general_info` so the corpus can't
      silently drift from production (refactor verified byte-identical extraction output).
      `test_golden_corpus.py` re-runs that shared function against the committed markdown
      and asserts each `case.json` snapshot. Snapshots are characterization baselines of
      *current* behavior; each case carries a `known_issues` list naming fields that are
      wrong-on-purpose plus the plan task that fixes them (e.g. `class_size` → P1.10).
      Review rule: a snapshot diff touching a field with no matching `known_issues` entry
      is an unexplained behavior change and must be justified in the PR. Regenerate from
      the live DB with `uv run python -m scripts.build_golden_corpus`. Coverage spans
      private/international/state types, pricing tables, display-name overrides, and the
      Fusion (520) / English-primary (510) cases.
- [x] **P1.6 Quality scoreboard.** Wire `PipelineRun` (modeled in
      `app/models/pipeline_run.py`, currently never written) into the CLI batch runners,
      and snapshot 6 metrics per run: % schools validation-ok, duplicate-coordinate
      groups, % locations precision=exact, % display-name overrides, spot-check
      discrepancy rate, pricing rows failing gates (no source_url / confidence <0.7).
      Add a `data-quality` CLI report reusing queries from the repair commands.
      Implement the webhook alert or delete `alert_webhook_url`/`alert_failure_threshold`.
      *Verify:* run pipeline on a few schools → PipelineRun row exists with metrics.
      **Done.** `app/services/data_quality.py::compute_quality_metrics` computes the six
      metrics in Python (portable across Postgres/SQLite) over schools in country/city
      scope. Note two metrics read where the data actually lives, not the plan's first
      guess: spot-check discrepancy comes from `attributes.data_validation.spot_check`
      (the `spot_check_results` table is unused), and precision from
      `SchoolLocation.geocode_meta.precision` (P1.3). `app/services/pipeline_runs.py`
      wraps the batch `run` path: `start_pipeline_run` (status=running) →
      stage execution → `finalize_pipeline_run` snapshots metrics + per-school counts
      (extract/validate-data/summarize batch runners now return summary dicts) + status
      (completed/partial/failed) into the new `pipeline_runs.metrics` JSON column
      (autogenerate migration `6cb27777e0d2`). A `data-quality` CLI command prints the
      scoreboard + recent runs. Deleted `alert_webhook_url`/`alert_failure_threshold`
      (no alerting infra; scoreboard supersedes). Verified live: a `validate-data` run
      recorded a `completed` PipelineRun with a full metrics snapshot. The report also
      confirms the gate-based metrics are near-empty on current data (10 duplicate-coord
      groups, 0 precision records, 0 corroborated overrides) because it predates the
      P1.1–P1.4 gates — this is the "prove the fixes helped" baseline for P2.4's re-scrape.
      - [x] *Cleanup (from P1.5 review).* Hoisted the `_select_pages` category-list
            literals into `extractor.PRICING_PAGE_CATEGORIES` /
            `GENERAL_INFO_PAGE_CATEGORIES` / `SUMMARY_SOURCE_PAGE_CATEGORIES`, imported by
            `deterministic.py`, so page selection can't drift between production and the
            golden corpus (verified byte-identical).
- [x] **P1.7 Field-level display gating.** Mirror the summarizer's
      `_blocked_summary_sections` pattern in serialization: a field with an error-level
      issue in the current validation report is excluded from the API response.
      Pricing: hide rows with confidence <0.7 or no `source_url`. Summaries: drop
      `needs_review` from `SUMMARY_ELIGIBLE_VALIDATION_STATUSES` (keep `ok` only).
      *Verify:* tests: school with error-level validation issue on field X → X absent from API.
      **Done.** New `app/utils/display_gating.py` is the single home for the gate:
      `blocked_display_fields(attributes)` reads `attributes.data_validation` and maps
      error-level issues + actionable spot-check discrepancies (`contradiction`/
      `unsupported`, same kinds the summarizer treats as actionable) onto the public
      display fields they feed (`attributes.extracted.{languages,facilities,programs,
      accreditations,extracurricular,class_size}` → `language_focus`/
      `languages_of_instruction`/`facilities`/`special_programs`/`activities_offered`/
      `class_size`). `school_attributes.build_base_attributes`/`build_localized_attributes`
      empty those fields, so the gate applies to both the API payload *and* the filter
      matcher (they share this projection). Pricing gating lives in the same module
      (`passes_pricing_gate`) and is applied by a new `SchoolPricingMixin` computed field
      on `SchoolResponse`/`SchoolListResponse` (drops rows with no `source_url` or
      `pricing_context.confidence` < floor). Summarizer now keeps only `{"ok"}` eligible.
      Tests: `test_api.py::TestDisplayGating` (field + pricing gates through the real
      endpoint) and updated `test_summarizer.py` (needs_review now rejected; pricing-section
      drop re-covered via an `ok`-status spot-check discrepancy).
      - [x] *From P1.6 review:* `PRICING_CONFIDENCE_FLOOR` moved to
            `app/utils/display_gating.py`; `app/services/data_quality.py` now imports it
            (re-exported for existing callers), so the scoreboard metric and the display
            gate share one constant.
      - [x] *From P1.6 review (finding-1 last corner):* `cli._run_discover_batch` now counts
            a crashed adapter as `failed` in its `_stage_summary` (covered by
            `test_run_discover_batch_counts_crashed_adapter_as_failed`), so a discover run
            whose only adapter crashes records FAILED, not COMPLETED with all-zero counts.
      - [x] *From P1.7 Codex review (two P2 findings):* (1) the pricing gate now also drops a
            row whose `pricing[{id}]` path carries an error-level issue even when it clears
            the source_url/confidence gate — `blocked_pricing_row_ids` + `SchoolPricingMixin`
            (extends `SchoolAttributesMixin` to reach the report). (2) a *stored* summary is
            withheld in serialization once validation regresses below `ok`, not just blocked
            from regeneration — `summary_is_publishable` gates `SchoolBase.summary_i18n`.
            Both inert on current data (no school has a summary or a non-ok report yet);
            covered by `test_pricing_row_with_validation_error_is_hidden` and
            `test_stored_summary_hidden_when_validation_not_ok`.
      - [x] *Hardening (self-review of P1.7).* (1) **Drift guard:** the extracted-field
            mapping keys are now coupled to `validator.SPOT_CHECK_EXTRACTED_ROOT_KEYS`
            (hoisted to a module constant) with a guard test — the mapping can't silently
            stop matching if the validator's spot-check path vocabulary changes. Note the
            extracted-field gate is only reachable via LLM spot-check discrepancies; the
            deterministic validator emits `attributes.extracted.*` as auto-fixes, and its
            only error-level issues are on pricing amounts. (2) **Real-validator test:**
            `test_display_gating.py::test_real_validator_error_gates_pricing_row_and_summary`
            runs the actual `validate_school_data` on a negative price and asserts the
            serialized payload hides that row + the summary — so the gate is proven against
            real validator output, not just hand-written reports. (3) **DRY:** summarizer's
            `_blocked_summary_sections` and the display gate now share
            `iter_blocking_field_paths` (one definition of "blocking"). (4) Filter-coupling
            and the then-current no-report summary behavior were documented + tested;
            P1.13 later replaced that leniency with a strict fail-closed rule.
      - [x] *From P1.7-hardening Codex review:* the drift guard originally checked mapping
            keys against `SPOT_CHECK_EXTRACTED_ROOT_KEYS`, a broader vocabulary than the
            spot-check *core* scope — `_normalize_spot_check_output` drops discrepancies
            outside `SPOT_CHECK_CORE_FIELD_PREFIXES`, so facilities/extracurricular/
            accreditations were mapped but unreachable (false confidence). Fix: widened the
            spot-check core scope to include those free-text display fields (deliberate
            decision — makes the capable model evaluate them and can raise the spot-check
            discrepancy-rate metric), and re-pointed the guard at `_spot_check_path_is_core`
            so the gate's coverage and the spot-check scope stay in lockstep. Added an
            end-to-end test that a facilities discrepancy now survives normalization and
            drives the gate.
- [x] **P1.8 Split `location_tags`.** Provenance strings (`source=moe_registry`,
      `source_esri_id=…`) move to `geocode_meta`; `location_tags` stays purely semantic;
      delete the client-side allowlist filter in `frontend/src/utils/locationFocus.js`.
      *Verify:* no provenance strings in API responses; focus tags still render.
      **Done.** Enforced at the publish boundary rather than by physically relocating the
      strings — consistent with P1.1/P1.7's model, where `location_tags` (like `attributes`)
      is an internal scratchpad and the allowlist runs at serialization. New
      `app/utils/location_tags.py::semantic_location_tags` projects raw tags down to the six
      public focus tags (`science_focus`/`arts_focus`/`sports_focus`/`music_focus`/
      `technology_focus`/`language_focus`); `SchoolLocationBase` now ingests the column as
      excluded `raw_location_tags` and exposes `location_tags` as a computed field over that
      projection, so provenance/coords metadata (`source=…`, `source_esri_id=…`,
      `coords_source=…`, `address_source=…`, `location_recovered=…`, `coords_cleared=…`,
      `coords_precision=…`) never ship even though they stay in the column for the repair
      commands that read them. Frontend `locationFocus.js` lost `isLocationFocusTag` /
      `getLocationFocusTags` (the client-side allowlist); `SchoolMap.jsx` / `SchoolCard.jsx`
      now render tags as-is. Deliberate divergence from the literal "move to `geocode_meta`":
      those tags are read by-string across `cli.py`/`extractor.py` repair paths, and
      `geocode_meta` already carries P1.3's structured `method`/`precision` — overloading it
      with legacy string tags would clash. Test:
      `test_api.py::test_location_tags_serialize_semantic_only`.
- [x] **P1.9 Controlled vocabulary for `facilities` / `special_programs`** (found while
      doing P1.1). The advanced-filter UI offers a canonical vocabulary
      (`SearchPage.jsx:27` `DEFAULT_ADVANCED_OPTIONS`: `cafeteria`, `library`,
      `computer_lab`…) but the extractor emits free text (`"Medical care"`,
      `"Медицински кабинет"`, `"3D printers"`). The two have never intersected, so
      those filters only ever matched seeded schools. `GET /schools/filters` currently
      hides this by returning `[]` — it reads raw top-level keys that no scraped school
      has. Wiring it to the merged projection returns ~355 facility / ~400 program
      options and would render ~360 checkboxes, so it was left reading raw on purpose
      (see the comment in `school_service.get_available_filters`). Real fix: map free
      text → canonical tags at extraction time, then serve the vocabulary from the
      endpoint. Best done after P1.5's golden corpus exists.
      *Verify:* `/schools/filters` returns only canonical keys; filter counts non-zero
      for scraped schools.
      **Done.** Mapped at the publish/projection boundary (like P1.7/P1.8), not by
      rewriting `attributes` — so it applies to the existing 540 schools with no
      re-scrape, and the free text stays intact for display. New
      `app/utils/facility_vocabulary.py` holds the canonical vocab (kept in sync with
      `SearchPage.DEFAULT_ADVANCED_OPTIONS`) and a deterministic BG/EN free-text →
      tag mapper (`canonical_tags`) using conservative substring/exact/whole-word rules
      (whole-word guards short latin stems like `stem` inside `system`; exact-token
      guards `стол`/`ib`). `build_filterable_attributes` now emits canonical tags for
      `facilities`/`special_programs`/`teaching_approach` (programs + extracurricular
      feed special_programs; programs feed teaching_approach for Montessori/IB), and
      `get_available_filters` reads that same projection instead of raw top-level keys —
      so options and the matcher can't diverge. The free-text display lists are left
      intact (they still render via the frontend's `getOptionLabel`); the canonical tags
      are served *additively* as `attributes.filter_tags` (see the Sol-review follow-up
      below), not by replacing the free text. Verified on
      live data: `/schools/filters` returns only canonical keys, non-zero coverage
      (facilities 74 / special_programs 134 / teaching_approach 29 schools), and
      `facilities=cafeteria` matches 8 schools (was 0 for scraped schools). Tests:
      `test_facility_vocabulary.py` (mapping + false-positive guards) and
      `test_school_attributes.py::TestFilterableProjection` (endpoint canonical options
      + matching). The frontend amenity-flag booleans (`facilities.includes('cafeteria')`)
      and the P1.10/P1.11 display gaps are separate and left as-is.
      - *From Sol review (two P1 findings):* (1) **Counts stayed 0 in the UI.** Canonical
        tags existed only in the backend's private filter projection, but the frontend
        counted canonical keys against the free-text display lists (`school.attributes.
        facilities`), so every count was 0 and zero-count checkboxes are disabled —
        parents couldn't select the now-working filters. Fix: serve the canonical tags to
        the browser. New `SchoolFilterTags` on `SchoolDisplayAttributes` exposes
        `attributes.filter_tags.{facilities,special_programs,teaching_approach}` (built by
        `school_attributes.compute_filter_tags`, shared with `build_filterable_attributes`
        so payload/matcher/options never diverge); free-text lists stay for display.
        `SearchPage.advancedCounts` + `matchesSelected` now count against `filter_tags`.
        Live check: computer_lab 39 / sports_program 101 / arts_program 84 schools, etc.
        (2) **Substring false positives.** Bare `спорт`/`sport` matched inside
        `транспорт`/`transport`; `стем`/`stem` inside `система`/`system`; `хран` inside
        `охрана`; `based learning` matched `play-based learning`. Added a word-boundary
        `prefix` rule type (`\b<stem>`, suffix allowed) so `спортна`/`sports` match but
        `транспорт`/`transport` don't, `STEM` matches but `система` doesn't, `хранене`
        matches but `охрана` doesn't; tightened `project_based` to require an explicit
        project reference. Regression tests in `test_facility_vocabulary.py`. Verified
        schools 380/422 no longer tagged sports (620 is a true positive — real
        Футбол/Волейбол).
      - *From P1.9 Codex review (one P2):* scraped rows store transport/meals under
        `extracted.operations.{transport,meals}` and pedagogy under
        `extracted.summary_source.teaching_approach` — outside the projected lists — so
        `transportation`/`meals_provided`/`project_based` stayed near-zero.
        `compute_filter_tags` now also seeds the mapper from those nested lists (locale
        overlay included), gated the same way as their sibling display field (transport
        ↔ `facilities` block, meals ↔ `special_programs` block). Live coverage went
        `transportation` 0→36, `meals_provided` 5→137, `project_based` 0→11; every
        canonical option is now non-zero. Tests:
        `test_filter_tags_include_nested_extraction_sources` +
        `test_nested_sources_respect_validation_gating`.
      - *From P1.9 Codex re-review (two P2 recall gaps):* (1) an approach recorded only in
        `summary_source.{canonical_tags,positioning,differentiators}` (with an empty
        `teaching_approach`) was dropped — now mined into the approach mapper (the vocab
        is narrow/distinctive, so prose mining is low-risk; live counts moved only
        `ib_program` 18→19 / `montessori` 12→13). (2) English-only meal text (`Food`,
        `breakfast`, `snack`) missed `meals_provided` — added those words. Verified against
        the cited golden cases (560 → `ib_program`, 154 `Food…` → `meals_provided`).
      - *Post-merge follow-up:* amenity flags in SchoolCard, SchoolDetailPage, and
        ComparePage now read the same `attributes.filter_tags` as the advanced filters;
        the localized free-text lists remain display-only. This fixes meals, transport,
        extended-day, library, computer-lab, and sports-facility indicators for scraped
        schools and removes inconsistent compare display/sort values. English `food`
        recall was also tightened using golden case 154: curriculum/news phrases such as
        `food projects` and `Food Revolution Day` no longer imply meal service, while the
        case's real caterer offer still maps to `meals_provided`. Future vocabulary
        precision/recall changes must add a regression backed by a committed golden case
        when one demonstrates the behavior; synthetic unit cases remain useful for
        boundary mechanics only.
- [x] **P1.10 BG users see no attributes for English-primary schools.** When a school's
      site is English, `attributes.extracted.<lists>` come back empty and all content
      lands under `extracted_i18n.en`, so the `bg` projection is empty (e.g. school 510:
      `extracted.facilities == []`, `extracted_i18n.en.facilities == ["Medical care", …]`).
      Pre-existing — the old client-side merge behaved identically — but now visible in
      `attributes_i18n.bg`. Fix in `extractor_helpers._build_general_info_i18n` /
      `_pick_primary_text_lang`: always populate the primary-language slot, or translate.
      Related: extraction of `class_size` from `"5 students"` yields `5` (school 510).
      Both are extraction-quality bugs — add fixtures under P1.5.
      **Done.** Fixed at the projection boundary (like P1.7–P1.9), not by re-scraping —
      current extraction already populates the primary slot (`_pick_primary_text_lang`
      returns the dominant on-site language), so the residual gap was 328/540 *existing*
      schools whose stale rows left the primary slot empty with content only under
      `extracted_i18n.<other>`. `school_attributes._localized_extracted` now backfills any
      field the requested locale left empty from another locale's override, so a BG viewer
      of an English-primary school sees the extracted facts (in whatever language they
      exist) instead of a blank section; fields the requested locale already populated are
      never touched (no cross-contamination). Because it's in the shared projection it heals
      all existing schools with no re-scrape and feeds the filter matcher too.
      `class_size`: `_parse_class_size` now rejects values outside `[8, 40]` — the DB shows a
      large mislabelled cluster of `"5 students"` (teacher:student ratios / small-group
      figures) with a clean break at 8, and >40 is total enrolment. The raw `"5 students"`
      capture stays in the golden snapshots (faithful extraction); the conflation is
      discarded downstream, so the 17 `P1.10` class_size `known_issues` were removed.
      Tests: `test_school_attributes.py` (locale backfill + no-override guard; class_size
      floor/ceiling), full suite green (656).
- [x] **P1.11 Dead attribute reads in the UI.** The frontend reads 23 `attributes.*`
      keys; 11 of them are written by *nothing* — not the scrapers, not `seed_data.py`:
      `accessible`, `admission_requirement`, `admission_status`, `after_school_care`,
      `application_deadline`, `enrollment_status`, `entry_requirements`,
      `operating_hours`, `schedule_hours`, `spots_available`,
      `transportation_available` (`ComparePage.jsx`, `SchoolCard.jsx`,
      `SchoolDetailPage/helpers.js`). They render as permanently-absent sections.
      Real data for several of them sits unused in `extracted.admission` /
      `extracted.operations`, which the projection does not surface. Decide per field:
      wire it through the allowlist, or delete the read. Guard: the
      `TestAllowlistCoversWrittenFields` invariant in `tests/test_school_attributes.py`
      only covers what `seed_data.py` writes; extend it once these are resolved.
      **Done.** The publish projection now surfaces the scraper-backed values parents
      can actually use: `admission.{entrance_requirements,deadlines,available_spots}`
      become localized `entry_requirements` / `application_deadlines` /
      `available_spots`; `operations.daily_schedule` becomes `daily_schedule`; and
      `operations.working_hours` fills the existing `school_hours` field (an explicit
      seeded `school_hours` still wins). All five paths are coupled to the P1.7 display
      gate, and `operations` joined the capable-model spot-check core scope so an
      unsupported schedule/hours value can be withheld. Compare/card/detail views now
      consume those projected fields. Reads with no producer were deleted:
      attribute-level admission/enrollment status aliases, `accessible`,
      `after_school_care`, `transportation_available`, `operating_hours`,
      `schedule_hours`, singular deadline/spots aliases, and `admission_requirement`.
      Status continues to use `admission_info.status`; after-school care uses location
      `has_organised_groups` / the canonical `extended_day` tag; transport uses the
      canonical `transportation` tag; accessibility can still render from a real
      facilities value. The allowlist invariant now pins the four new localized
      scraper fields, with projection/gating tests for all five paths and a frontend
      regression proving the retired boolean aliases no longer affect amenity flags.
      Follow-up review fixed the newly reachable Bulgarian admission snippets too:
      one shared classifier recognizes BG/EN interview/test/no-exam phrases, evaluates
      negative phrases first (`без изпит` is not “test required”), and preserves unknown
      extracted text as a visible fallback instead of silently dropping it.
      Self-review also found pre-existing false `working_hours` captures in the golden
      corpus (headings, reversed ranges, and 1–2 hour contact windows); the projection
      now publishes only explicit forward daytime ranges lasting 4–14 hours and strips
      surrounding heading noise, healing existing rows without a re-scrape. Extract-time
      deterministic validation now evidence-filters `working_hours` and `daily_schedule`
      against cached source pages as well, so new runs cannot open an unvalidated window
      before the capable-model Stage 6 audit.
      Codex review follow-up: whole-section `admission` / `operations` discrepancies now
      block every projected child (child-level discrepancies remain narrow), and mixed
      requirement lists no longer let “no exam” hide a separately required interview or
      test; multiple distinct requirements fall back to the full extracted text.
      Re-review tightened the section gate so broad parent blocks apply only to the exact
      parent path (an unrelated `operations.transport` issue cannot hide schedules), and
      added common Bulgarian negative forms such as `няма/без приемен изпит`.

### Phase 1 closeout findings (audit 2026-07-13)

The Phase 1 implementation and automated coverage are strong, but the live Sofia
scoreboard exposed three fail-closed/measurement gaps that must be fixed before the
one-time P2.4 data refresh. Do not run the full scrape before these land, or the refresh
will need to be repeated.

- [x] **P1.12 Make quality metrics report coverage honestly.**
      `services/data_quality.py::_validation_ok` currently divides by schools that already
      have a report, so the live scoreboard says `100%` for `25/25` although only 25 of 540
      Sofia schools have reports (4.6% coverage). Likewise, location precision reports
      `n/a` because it divides by the zero locations carrying precision metadata, while
      491 locations already have coordinates. Report quality and coverage separately:
      validation-ok / all schools plus validation-report coverage; exact / geocoded
      locations plus precision-metadata coverage. Review the display-name denominator for
      the same ambiguity. Keep PipelineRun snapshots and CLI output on the same definitions.
      *Verify:* a fixture with 10 schools, 2 `ok` reports, and 8 missing reports displays
      20% validation-ok and 20% report coverage, never 100%; a location with coordinates
      but no precision counts against precision coverage.
      **Done.** Validation-ok and exact-precision percentages now use all schools and all
      geocoded locations respectively, with separate report/metadata coverage. Display-name
      overrides use all schools, with candidate coverage and corroboration conversion shown
      separately. CLI and PipelineRun snapshots share these definitions.
- [x] **P1.13 Fail closed when summary validation is absent.**
      `display_gating.summary_is_publishable` currently publishes a stored summary when
      `data_validation` or its status is missing. This conflicts with P1.7's “keep `ok`
      only” rule and would publish a stored summary without a current report. The audit
      initially mistook the JSON `{}` default for content; current Sofia data has zero
      non-empty summaries, but the fail-open path still had to be closed before regeneration.
      Require a current `status == "ok"` to publish a summary. Decide explicitly whether
      all other scraped display fields should also require a current report; the safest
      launch policy is to fail closed after P2.4 has backfilled every school.
      *Verify:* missing/empty/`needs_review` reports hide stored summaries; only `ok`
      publishes them; real API tests cover the behavior.
      **Done.** Stored summaries now require an explicit current `ok` report. Other
      allowlisted display fields remain field-gated rather than globally hidden during the
      transition; P2.4 requires 100% report coverage before launch, removing the missing-
      report state from production.
- [x] **P1.14 Make the pricing-confidence gate fail closed.**
      `passes_pricing_gate` currently accepts missing, non-numeric, and boolean confidence,
      while `ExtractedPrice.confidence` defaults omitted model output to `1.0` and is not
      constrained to `[0, 1]`. Make extraction confidence required and bounded (the
      deterministic fallback already supplies `0.7`), and publish only a real numeric
      confidence `>= PRICING_CONFIDENCE_FLOOR`. Reuse the exact gate predicate in the
      scoreboard rather than duplicating near-equivalent logic. Per the project testing
      rule, add price-validation edge-case tests before implementation.
      *Verify:* missing, boolean, negative, over-1, and below-floor confidence cannot be
      published; scoreboard failure counts exactly match serialization.
      **Done.** `ExtractedPrice.confidence` is required, strict, finite, and bounded to
      `[0, 1]`; the publish gate accepts only numeric `[0.7, 1]` values with a source URL;
      the scoreboard calls that same predicate. Schema, gate, API, and metric regressions
      cover malformed and boundary values.
- [x] **P1.15 Finish the serialization allowlist across sibling JSON payloads.** The
      original P1.1 boundary covered `schools.attributes`, but `admission_info`,
      `FieldSource.value_json`, and `pricing_context` still crossed the API as unrestricted
      dictionaries. Live re-audit found 341 schools exposing
      `admission_info.website_extracted` and 4,158 field-source rows exposing raw structured
      values; 14/18 and 18/18 pilot schools respectively exercised those paths.
      **Done.** Admission data now uses a positive official/curated-key projection and never
      serializes `website_extracted`; scraped admission display fields continue through the
      localized attribute gate. Parent-facing provenance exposes only the fields the source
      UI consumes, never raw values, internal paths, notes, or submitter metadata. Pricing
      context is typed and drops unknown internal keys. List, detail, and compare share the
      boundary, with an API regression containing deliberate secret keys.
- [x] **P1.16 Treat website-derived data as one publishable lifecycle.** URL failures
      previously removed only some extracted attributes for a few strong failure reasons,
      leaving validation state, admission mirrors, pricing, provenance, and sometimes old
      content public. Seven existing `no_official_website` schools still demonstrated the
      stale-data state.
      **Done.** A shared website publication predicate gates extracted attributes, branded
      names, filters/search, summaries, scraped pricing, and scraped provenance. Invalid or
      ambiguous URL validation sets a persistent withholding marker; successful URL
      validation alone cannot reopen the old payload. Only extraction plus deterministic
      validation in the same transaction clears the marker. Confirmed identity mismatches
      additionally purge every website-derived branch/row atomically while preserving
      government/official data. Tests cover transient withholding, strong cleanup, API,
      search, filters, and marker clearance.
- [x] **P1.17 Route website-map coordinates through the geocode write gate.** Contact-page
      map-link coordinates were written directly, bypassing Sofia bounds and leaving six
      current rows without precision metadata.
      **Done.** `website_map_link` is a controlled exact geocoding method and extraction now
      calls the shared write gate. Accepted points persist method/precision/provider metadata;
      rejected points remain NULL with the rejection reason. Tests cover accepted refreshes
      and out-of-bounds rejection. The six legacy rows will receive metadata during the
      planned force-regeocode after the website refresh.
- [x] **P1.18 Separate global report coverage from launch eligibility.** A literal 100% of
      all schools was not achievable or meaningful for registry-only schools: at re-audit,
      42 schools without a website URL also had no website-validation report and batch `all`
      could never select them.
      **Done.** The scoreboard retains global report coverage as an informational measure and
      adds coverage among schools whose website-derived data is currently publishable, plus
      a hard `published_without_report` count. Registry-only and explicitly withheld payloads
      are excluded from that denominator. Pipeline snapshots and CLI output share the same
      definitions; launch requires 100% eligible coverage and zero published-without-report.

## Phase 2 — User-facing correctness bugs

- [x] **P2.1 diff=2 age-group gap.** `app/utils/education.py:15-24`: nursery covers
      diff 0–1, `first` starts at 3, so 2-year-olds get zero results.
      `tests/test_education.py:111-116` asserts the bug. Extend nursery to `max_diff: 2`
      (confirm against kg.sofia.bg group definitions), fix test, mirror in
      `countries.education_config` and frontend config.
      **Done.** Sofia's current admission material continues to separate nursery-age
      candidates from first kindergarten group, which begins at calendar-year difference
      3, so the existing enrollment-year-minus-birth-year model now maps the complete
      0–2 range to `nursery`. Updated the backend fallback, Bulgaria country seed/update
      config, reference data seed, and frontend fallback. Replaced the tests that preserved
      the gap with backend boundary assertions and added frontend regressions for both the
      fallback and country-config paths. Existing databases pick up the persisted country
      JSON change by rerunning `uv run python scripts/seed_country_bg.py` during deployment.
      Verified: 672 backend tests, 30 frontend tests, frontend lint, and frontend build pass.
- [x] **P2.2 Slim the list endpoint.** Drop `exam_results` and `field_sources` from
      `SchoolListResponse` and remove the corresponding `selectinload`s in
      `app/services/school_service.py:34-41` (detail view fetches them).
      **Done.** List/search queries load locations and gated pricing only; detail and
      compare queries retain exam results and field sources. API regressions pin both
      response shapes.
- [x] **P2.3 exam-averages tightening.** `app/routers/schools.py:164`: match
      `metric == "average_score"` exactly and pass subjects through instead of the
      math/bulgarian binary bucket.
      **Done.** The query accepts only the canonical metric and preserves the stored
      subject key. A regression covers a third subject and rejects a misleading
      `school_average` metric.
- [x] **P2.4 Audit and perform the one-time Sofia data refresh.** This is the point at
      which to run the scraping process again: after P1.12–P1.14 and the audit/re-geocode
      tooling below are merged and green, and before Phase 3. P2.2/P2.3 do not change
      extraction, so they do not technically block the refresh, but completing them first
      keeps this as the single release-candidate data run. Do not start a full scrape while
      publish-gate behavior is still changing.
      - [x] Commit `docs/audits.sql` (the plan currently references it, but the file does
            not exist) with repeatable queries for validation coverage/status, duplicate
            and out-of-bounds coordinates, precision coverage, display-name overrides,
            raw gate-failing pricing, and API checks for internal-key leakage.
            **Done.** The psql audit is executable with `ON_ERROR_STOP` and documents the
            separate live-API `curl`/`jq` boundary check.
      - [x] Fix and test the force-regeocode path before using it:
            `scripts/geocode_locations.py --force` previously warned that it would process
            every location but still called `geocode_all_missing()`, so existing coordinates
            were skipped. It must route all locations through the P1.3 write gate and persist
            `geocode_meta.method` / `precision` for accepted and rejected results.
            **Done.** Bulk geocoding can now include existing coordinates, is deterministically
            ordered/limited, defaults the script to `bg`/`sofia`, propagates each school's
            country code, and requires `--all-locations` to remove scope. Focused tests cover
            refresh, metadata persistence, limit, scope exclusion, and country propagation.
      - [x] Make batch `all` operate on one explicit cohort from start to finish. Previously,
            each stage independently reapplied status filters and `--limit`, so a nominal
            20-school pilot could validate, navigate, extract, and summarize different
            schools; extracted/summarized schools could also avoid the recrawl. The batch
            runner now selects once and persists `PipelineRun.config.cohort_school_ids`.
            It keeps the original cohort for accounting, then narrows downstream work to
            URL-validation, navigation, extraction, and data-validation successes so a failed
            URL or crawl cannot publish stale cached content as a fresh result. `--cohort-file` accepts a reviewed,
            repeatable list of IDs and validates country/city/website scope. Automatic
            `--limit` is deterministic but must not be called representative (the current
            lowest IDs are all state kindergartens).
      - [x] Persist LLM usage for cost control. Extraction and summarization stage summaries
            now retain input/output tokens and USD cost; `PipelineRun.metrics.llm_usage`
            aggregates them and the recent-run scoreboard prints cost. Invalid/negative/
            non-finite counters fail closed to zero, and completed-stage usage survives a
            later stage exception. The pilot is the cost probe: calculate
            `(pilot cost / completed cohort schools) × full cohort size × 1.25`; do not start
            the full run until that headroom fits the configured provider limit and the user
            has approved the projection.
      - [x] Take a database backup and capture the pre-run audit + `data-quality` output.
            Current baseline: 540 schools; 25 reports / 515 missing reports; 0 non-empty summaries;
            579 locations / 491 with coordinates / 0 with precision metadata; 10 duplicate
            coordinate groups; 463 display-name candidates / 0 corroborated overrides;
            0 spot checks; 259 scraped prices, all currently clearing source/confidence.
            Store the ignored artifacts under `backend/reports/pre-scrape/<timestamp>/`:
            a custom-format `pg_dump`, its SHA-256, `audits.txt`, `scoreboard.txt`, and the
            reviewed `pilot-cohort.txt`. Confirm the dump is non-empty and `pg_restore -l`
            can read it before continuing.
            **Done 2026-07-13.** Local ignored artifacts are in
            `backend/reports/pre-scrape/20260713T102828Z/`. The 9.2 MB dump passes its
            SHA-256 check and produces a 110-line restore manifest. The audit and corrected
            scoreboard were captured, and `cohort-validation.txt` confirms the reviewed
            18-school stratified cohort is in Sofia scope and every member has a website URL.
      - [x] Run a 10–20 school pilot through the complete website path, explicitly
            including already navigated/extracted schools and forced validation. Inspect
            API payloads, rejected coordinates, pricing, display names, localized fields,
            spot checks, summaries, and the PipelineRun snapshot before scaling up.
            Build a deterministic stratified cohort across school type and education level,
            with private/international pricing cases, then run batch `all` with
            `--cohort-file`, `--include-navigated`, `--include-extracted`, and
            `--force-validate`. Stop after the pilot to review quality and projected cost.
            Batch `--stage all` starts at URL validation and does not run website discovery;
            run `discover-websites` separately first only for missing/stale website records.
            **Completed 2026-07-13 — not approved for scale-up.** Run
            `54da254d-df10-4985-b157-664cd4f0f9ec` processed the reviewed 18-school cohort:
            17 URLs validated, navigated, extracted, and passed deterministic validation;
            one directory/construction-page mismatch was correctly rejected and withheld;
            16 schools were summarized. Extraction used 117,933 input / 7,088 output
            tokens ($0.015411) and summarization used 16,439 input / 4,119 output tokens
            ($0.004936), for $0.020347 recorded usage. The run was correctly `partial`.
            Evidence is stored in ignored local artifacts under
            `backend/reports/pilot/54da254d-df10-4985-b157-664cd4f0f9ec/`.
      - [x] **Fix price semantic validation before another pilot.** The source/confidence
            gate reported 0 failures, but manual inspection found rows crossing the API
            with incorrect categories/periods: tuition labeled as food (school 153), bus
            and learning-support fees labeled as tuition (506), and implausible transport
            / yearly mappings plus mixed old/current fee tables (570). Add tests before
            implementation per the project price-validation rule. Validate category against
            plan/section context, period against source wording, current academic-year
            selection, and duplicate/ambiguous rows; withhold rows that cannot be supported.
            **Done 2026-07-13.** Price evidence matching now considers all matching amounts
            plus plan/section context, supports Bulgarian currency wording, corrects explicit
            bus/activity/materials context, removes older explicit academic years and
            installment/currency duplicates, and fails closed for ambiguous, composite,
            unsupported-service, and clearly stale yearless fee tables. Cached-pilot
            regressions cover the failure patterns and a clean yearless control.
      - [x] **Calibrate spot-check discrepancy semantics before another pilot.** Ten schools
            were checked; 3 were actionable (30%, above the 15% advisory threshold), with
            61 omissions, 7 unsupported claims, and 0 contradictions. Several `unsupported`
            findings actually describe missing/incomplete extraction, while at least one
            potentially unsupported class-size value remained public because it was labeled
            `omission`. Require evidence for unsupported/contradiction, keep genuine omission
            monitoring separate from publish blocking, and regression-test the three pilot
            schools (506, 516, 529) against their cached source pages.
            **Done 2026-07-13.** Spot-check context is deduplicated and category-balanced
            instead of truncated in database order. Direction is derived from concrete
            extracted/source values; every retained finding requires evidence, and omission/
            contradiction quotes must occur in the supplied context. Genuine omissions stay
            monitoring-only while supported actionable findings continue to gate publication.
      - [x] **Include spot-check LLM usage in the PipelineRun cost snapshot.** The recorded
            $0.020347 includes extraction and summarization but excludes the ten capable-model
            spot-check calls, so it is not yet a complete provider-cost probe. Persist their
            token/cost usage and include it in the aggregate and CLI report.
            **Done 2026-07-13.** Each spot check persists tokens, exact provider cost when
            available (tier-price fallback otherwise), and model identity. The Stage 6 batch
            aggregates successful and post-call non-success usage into the existing
            `PipelineRun` usage snapshot and CLI totals.
      - [x] Repeat the same reviewed pilot cohort after the pricing and spot-check fixes.
            Require semantically correct published prices, a calibrated actionable discrepancy
            rate within threshold, complete usage accounting, clean API boundary checks, and
            no unresolved publishable claims before approving the full refresh.
            **Completed 2026-07-14 — not approved for scale-up.** Before the run, `main` was at
            merged PR #45 (`5e82450`), Postgres was healthy and at Alembic head
            `6cb27777e0d2`, the backup checksum/restore manifest were valid, and all 18 reviewed
            cohort IDs were in Sofia scope. The previous false-positive URL for school 103 had
            been correctly cleared; targeted website discovery found `https://dg3sofia.com`,
            which then passed deterministic URL identity validation. The configured models were
            unchanged and available (Gemini 2.5 Flash Lite cheap tier, GPT-4o-mini capable tier,
            with GPT-4o-mini as the extraction fallback). OpenRouter reported no key spend limit,
            about $7.65 balance before the run, and matching live model prices.

            Run `a6d78e0c-e67a-4ac7-b4a4-a3ca48b83582` completed the full website path for all
            18 schools: 18 URL-valid, navigated, extracted, deterministic-validation `ok`, and
            summarized. Extraction used 120,017 input / 8,530 output tokens ($0.015900), and
            summarization used 17,627 input / 4,231 output tokens ($0.005181). Seven of ten
            sampled spot checks completed in the main run; three hit the configured 20-second
            timeout. The same capable model completed targeted rechecks for those three under
            recorded run `8361f509-99fb-4c01-9d98-40f896920e58` with a justified 45-second
            timeout (14,205 input / 1,990 output, $0.003325). Across the main run and rechecks,
            attributed telemetry was 183,780 input / 19,091 output tokens and $0.031800; the
            authoritative provider usage delta was $0.034100, capturing any timeout/provider
            billing not visible to result telemetry. No URL reached the unmetered LLM fallback.
            All ten completed checks had zero actionable contradiction/unsupported findings
            (0%, below 15%) and seven monitoring-only omissions. With 474 current website URLs,
            the required `(cost / 18) × 474 × 1.25` projection is $1.12 using provider charges
            ($1.05 using attributed telemetry). The balance covers it, but `limit=null` means the
            configured-limit precondition is not literally satisfied.

            The structural list/detail/compare boundary check returned `true` with no internal-key
            leakage. Cohort validation coverage was 18/18 and all stored cohort summaries were
            publishable under the current `status=ok` gate. NVO was not run and remained at 5,115
            rows. Ignored evidence is under
            `backend/reports/pilot/a6d78e0c-e67a-4ac7-b4a4-a3ca48b83582/`.

            Manual semantic review nevertheless found release blockers below, so the next full-
            refresh checkbox remains unauthorized.
      - [x] **Fail stale and semantically unsupported pricing closed, then repeat the pilot.**
            School 570 still publishes old 2024/2025 food/activity values beside 2026/2027 fees,
            converts explicitly monthly tuition to yearly, invents yearly periods for unlabeled
            transport, and labels foreign-language books as extracurricular. School 506 publishes
            late-payment penalties as registration fees. Schools 538 and 584 retained stale rows
            when the repeat extraction produced no replacement: 538 includes unsupported activity
            amounts and cites a contact page, while 584 exposes fee tables dated by 2023/2024
            payment deadlines with lost grade/plan semantics. Add price-validation regressions
            before changing extraction, clear superseded scraped rows on a successful no-price
            refresh, and prove stored-row freshness/source semantics rather than only the current
            source/confidence gate.
            **Progress 2026-07-14 (PR #48, merged as `2efaea9`).** Successful no-price
            refreshes now clear superseded scraped rows while provider failures preserve them;
            stale cross-category schedules, late-payment penalties, unsupported optional-service
            periods, and exact duplicates have regression coverage. The repeat confirmed that the
            old 2024/2025 school-570 rows and school-506 late penalties are gone, school 584 has
            zero stale prices, explicit monthly tuition and materials survive, and activities with
            distinct plan names are not collapsed. This remains unchecked because the fresh repeat
            exposed new heading/entity-association failures below.
            **Superseded 2026-07-15.** The merged staleness/penalty/duplicate rules stay; the
            residual heading/entity failures leave the release path via **P2.8** (curated-only
            pricing at launch).
      - [x] **Preserve or supersede actionable spot-check gates safely.** Fresh deterministic
            validation replaces the complete `data_validation` payload, so an unsampled school can
            lose its earlier actionable spot-check withholding. School 529 consequently republishes
            unsupported `class_size=9`; a grade-9 news reference satisfied the current loose numeric
            evidence check. Fix the deterministic class-size evidence rule and define when an old
            spot finding may be cleared. Also raise or otherwise handle the 20-second capable-model
            timeout so sampled coverage does not require an out-of-band retry.
            **Progress 2026-07-14 (PR #48).** Class-size evidence now requires a pupil/class
            relationship, deterministic-only reruns preserve prior spot gates, explicit new checks
            supersede them, summaries fail closed on actionable blocking paths, and the timeout is
            45 seconds. All ten repeat-run spot checks completed. This remains unchecked because
            provenance still exposes rejected `FieldSource.value_text` values, including school
            529's withheld `9 students`, through detail/compare responses.
            **Rescoped 2026-07-15.** The gate-preservation and timeout work is done; the one
            remaining failure (the `value_text` provenance leak) is now tracked as **P2.5a**.
      - [x] **Tighten admission-field semantic validation before publication.** The `ok` reports
            still permit a 2020 procurement deadline (103), navigation labels/links as available
            places or entry requirements (324), a Markdown image URL as an application deadline
            (404), stale 2025/2026 admission text (570), and incomplete deadline fragments (153).
            Add cached-page regressions and withhold unsupported/stale admission children through
            the existing projection gate.
            **Progress 2026-07-14 (PR #48).** Stale-year, procurement, navigation-label,
            Markdown URL/image, incomplete-ending, and unsupported available-place values now fail
            closed in deterministic validation and the API projection. The named 324/404/570
            failures no longer publish. This remains unchecked because the repeat still publishes
            fragmented admission children and a misclassified interview requirement (details
            below), and a Markdown daily-schedule link bypasses the admission-specific filter.
            **Superseded 2026-07-15.** The residual failures are removed from the release path by
            **P2.7** (website-derived admission fields withheld at launch) and **P2.5b** (general
            Markdown sanitizer). The merged PR #48 validation rules stay. Further admission
            semantic work moves to the post-launch enrichment track.
      - [x] **Make summary inputs and outputs obey the publish boundary.** Summarization still passes
            uncorroborated display-name candidates even though API name resolution rejects them,
            trusts the import scope `city=sofia` over the primary location, and has no semantic
            post-generation gate. The repeat produced misidentified schools (153/529), false Sofia
            locations (440/454/460/570), missing-data filler and unsupported negative claims, stale
            or promotional narrative, and unstable proportions. Reuse the corroborated resolved
            identity/location, strengthen filler/unsupported-claim validation, and withhold failed
            summaries before another pilot.
            **Progress 2026-07-14 (PR #48).** Stage 7 now reuses the corroborated public name,
            derives locality from the primary address/in-bounds coordinates instead of import
            scope, and validates short/long output again at persistence. The repeat correctly says
            Svoge, Elin Pelin, Etropole, and Osoitsa for schools 440/454/460/570. This remains
            unchecked because the output gate still accepts the semantic/presentation failures
            documented below.
            **Superseded 2026-07-15.** The merged identity/locality fixes stay; summaries are out
            of launch scope entirely (**P2.6**), so the remaining output-quality gap is no longer
            a release blocker.
      - [x] **Repeat the reviewed 18-school cohort after the PR #48 blocker pass.** Preflight
            used clean `main` at `2efaea9`, healthy Postgres at Alembic head `6cb27777e0d2`, the
            checksum-valid 9.2 MB backup/110-entry restore manifest, and the unchanged reviewed
            cohort. All 18 records were in `bg`/`sofia` scope with website URLs. The configured and
            live-available models remained Gemini 2.5 Flash Lite (`$0.10/$0.40` per million
            input/output tokens) and GPT-4o-mini (`$0.15/$0.60`), with GPT-4o-mini as extraction
            fallback. OpenRouter again reported `limit=null`; pre-run balance was about $7.61.

            Run `9d2c837f-aaf9-4924-a884-899f9cbcf4e0` completed in about 6m39s with zero stage
            failures: 18/18 URL-valid, navigated, extracted, deterministic-validation `ok`, and
            summarized. URL validation used no LLM fallback. Extraction used 118,968 input / 9,482
            output tokens ($0.016399); all ten capable-model spot checks completed at the new
            timeout and used 44,936 / 6,515 ($0.010649); summarization used 14,989 / 3,890
            ($0.004581). Complete attributed usage was 178,893 input / 19,887 output and $0.031629.
            The final provider delta was about $0.033112, leaving about $7.58. For 474 current
            website URLs, `(pilot cost / 18) × 474 × 1.25` projects to $1.09 from provider charges
            ($1.04 from attributed telemetry).

            Spot checks reported zero actionable contradiction/unsupported findings (0%, below
            the 15% advisory threshold), eight monitoring-only omissions, and no failures. NVO was
            not run and remained at 5,115 rows. Ignored evidence is under
            `backend/reports/pilot/9d2c837f-aaf9-4924-a884-899f9cbcf4e0/`.

            **Recommendation: no-go for the full refresh.** Perfect stage/validator counts and zero
            source/confidence gate failures do not establish semantic correctness. Manual stored,
            source-page, list/detail/compare, and summary review found the blockers below.
      - [x] **Fix fresh pricing heading/entity association, then repeat the cohort.** School 538
            publishes nursery tuition EUR 580 and after-hours care EUR 26 as transport; EUR 160 is
            the actual transport fee. School 570 publishes EUR 300 supplies, EUR 1,000 deposit, and
            EUR 850 monthly tuition as registration, plus EUR 180 meals as tuition. School 153
            mixes kindergarten, preschool, gymnasium, and primary-school prices from a combined
            Uwekind page without plan/age applicability. School 506's seven published rows are
            correctly classified, but omitted mandatory capital fees understate the total cost.
            Add fresh-page regressions and reject rows whose heading/entity/plan association cannot
            be retained. The scoreboard's 0/234 gate failures currently misses these errors.
            **Superseded 2026-07-15 (not implemented).** Scraped pricing no longer publishes at
            launch — P2.8 gates the API to curated rows only, which removes these failures from
            the release path without another extraction iteration. The PR #48 pricing regressions
            stay merged. Heading/entity association work moves to the post-launch enrichment
            track, where P2.10's architecture-ceiling verdict makes E1 the prerequisite.
      - [x] **Close provenance and localized-field publish bypasses.** Detail/compare expose raw
            `field_sources[].value_text` even when the corresponding field was rejected (school 529
            publishes `9 students` through provenance while `attributes.class_size` is null; the
            cohort has several analogous guesses). Remove raw values from the parent-facing schema
            or apply the same field-level gate. Also reject/coalesce fragmented admission children:
            school 103 publishes seven split medical phrases, 105 a standalone `Деца`, 153 four
            split interview fragments, and 506 splits self-care text and misclassifies a possible
            placement-test/interview requirement as an application deadline. Apply the general
            projection sanitizer to school 324's Markdown daily-schedule link as well.
            **Rescoped 2026-07-15.** The `value_text` provenance leak and the Markdown sanitizer
            gap are genuine boundary bugs and remain launch blockers — now tracked as **P2.5**.
            The fragmented-admission coalescing (103/105/153/506) is mooted for launch by
            **P2.7** (website-derived admission fields are withheld) and moves to the
            post-launch enrichment track.
      - [x] **Make summary persistence parent-ready, not merely schema-valid.** Manual review passed
            only 6/18 summaries (105, 440, 454, 460, 516, 570). The other 12 include identity/entity
            collisions (153/529), testimonial or marketing prose (506/610/631), unsupported
            negatives (233/584), stale relative claims (404), mistranslation/unstable operational
            details (538), or raw/generic extraction artifacts (103/297/324). Extend deterministic
            input cleanup and semantic output validation; clear/downgrade these failures, then
            inspect every regenerated cohort summary before scale-up.
            **Superseded 2026-07-15 (not implemented).** Generative summaries are out of launch
            scope — **P2.6** withholds them globally at serialization. A 6/18 manual pass rate
            after three fix iterations is a model-capability signal, not a bug list;
            parent-readiness work moves to the post-launch enrichment track. P2.10 later
            established an architecture ceiling, so E1 now precedes further re-extraction.
      - [x] **Set an OpenRouter spend guardrail (or explicitly approve operating without one).**
            The projected refresh is affordable, but the active key reports `limit=null`; record a
            cap with headroom before treating the cost-control precondition as met.
            **P2.13 evidence (2026-07-17; user decision still required):** the active key
            reports usage `$10.47598213`, `limit=null`, and `limit_remaining=null`.
            Recommended headroom is `$5` for the ≈`$0.752620` cheap-tier projection:
            because current usage already exceeds `$5`, set an absolute cumulative key
            limit of about **`$15.50`** if the dashboard retains this usage, or `$5` on a
            reset/new key. Leave this checkbox open until the user records the dashboard
            decision (or explicitly approves operating uncapped).
            **Closed 2026-07-20:** the recovery was explicitly approved with a hard
            combined ceiling of `$1.50`, enforced from the stored `$10.47598213`
            provider baseline. Conservative final accounting is `$0.30838240`; the
            provider interval delta is `$0.29900793`. The provider key itself still
            reports `limit=null`, so this approval applies only to this completed run.
      - [x] If the boundary pilot (P2.9) is closed, P2.10 is closed,
            P2.12/P2.13 are green, and the user has explicitly approved, run the full
            Sofia website refresh once on the **current cheap extraction tier, unchanged**
            with the summarize stage skipped (summaries are out of launch scope — don't
            pay for prose nobody sees). The failed frontier experiment does not delay the
            refresh. The capable spot-check tier is
            a separate refresh-config decision recorded in P2.10(c), not a blocker. Then
            run the corrected force-regeocode process.
            NVO remains independent and must not be refreshed as part of `all` unless a
            separate NVO audit calls for it.
            **Completed by recovery 2026-07-20.** Original run `4749f137…` was marked
            `partial` with interruption evidence. Recovery attempts `483c1e91…`
            (`failed`, cohort preflight only), `585370ca…` (`partial`, navigation
            interrupted before extraction), and `882ff309…` (`partial`, bounded nine
            navigation failures) stayed inside the original 386-school cohort. The final
            run extracted and deterministically validated all 344 navigated schools,
            skipped summarization, and left NVO unchanged at 5,115 rows.
      - [x] Re-run audits and the scoreboard, triage failures, and targeted-rerun only the
            affected schools. **Launch acceptance (revised 2026-07-15 — boundary-only, no
            prose-quality judgment):** 100% validation-report coverage among schools with
            publishable website-derived data and zero such schools without a report; zero
            summaries serialized (P2.6 flag off); zero website-derived admission fields
            serialized (P2.7); only curated pricing serialized, each row with source_url and
            verification date (P2.8); no rejected value reachable via provenance and no raw
            `value_text` in responses (P2.5); no Markdown syntax in published display fields;
            100% precision-metadata coverage for geocoded locations; no unexplained
            duplicate/out-of-bounds coordinates; no internal API keys; pricing gate/scoreboard
            parity; and a representative spot-check sample with all actionable discrepancies
            resolved or withheld.
            **Accepted 2026-07-20 (`llm_calls=0` for the audit battery).** Full BG/EN
            boundary sweep passed `1,066/1,066` requests. Current-report coverage is
            `328/328` for publishable website data with zero fail-open rows; geocoding is
            `392/392` precision-tagged with 88 terminal NULL failures and zero unexplained
            duplicates; pricing predicate/scoreboard/API parity is zero published rows.
            Spot checks found discrepancies on schools 113 and 116 only; the affected
            founded-year/class-size fields are outside launch scope and remain withheld.
            Evidence: `backend/reports/refresh-recovery/20260720T080608Z/acceptance.md`.

### Launch-scope revision (2026-07-15) — sparse, fail-closed launch

**Decision.** Two repeat pilots (`a6d78e0c…`, `9d2c837f…`) passed every structural gate and
every stage, and each still failed manual semantic review with a *new* class of LLM
misjudgment (first category/period errors, then heading/entity association, identity
collisions, promotional prose). That is a long tail, not a converging bug list: the
cheap-tier models cannot be gated into publication-quality semantics across arbitrary
school websites, and manual review of every cohort does not scale to 474 schools. Phase 1
did its job — failures now stop at the reviewer instead of reaching parents. The mistake
was keeping LLM semantic quality as a go-live criterion.

**Launch scope (published):** map + locations, filters, official NVO results,
official/curated `admission_info`, manually curated pricing, registry/corroborated names.
**Out of launch scope (stored internally, never serialized):** generative summaries,
website-derived admission fields, scraped pricing rows, raw provenance values. Once
P2.5–P2.8 land, the public boundary is **frozen** — no further extraction-semantics fixes
enter the release path; that work continues on the post-launch enrichment track (end of
this file), constrained by P2.10's architecture-ceiling verdict.

**Order (closed 2026-07-20):** P2.5 →
P2.6/P2.7/P2.8 (independent, any order) → P2.9 run 1 → P2.11 city-scope repair →
P2.9 rerun (done, run `db4ba90d…` — see P2.9) → **P2.12 release-boundary
hardening** → **P2.10 closed: architecture ceiling; cheap tier selected** →
P2.13 exhaustive no-LLM audit → approved `$1.50` recovery guardrail → one-time cheap-tier
refresh/recovery → force-regeocode → audits/acceptance → **STOP**. Value-model runs are
no longer on the launch path. The release boundary remains fixed and fail-closed; Phase 3
has not started. Before any future city-wide refresh, complete P2.14(a)–(c); those
operational safeguards do not reopen the accepted launch boundary.

- [x] **P2.5 Close the two remaining publish-boundary bugs.**
      (a) **Provenance becomes metadata-only.** Detail/compare responses still expose raw
      `field_sources[].value_text` even when the corresponding field was rejected — run
      `9d2c837f` school 529 serves `9 students` through provenance while
      `attributes.class_size` is correctly withheld. Remove `value_text` (and any other
      value-bearing field) from the parent-facing provenance schema entirely; publish only
      source URL, source type, verification date, and confidence. Do not try to field-gate
      the value — provenance must never be a second route around field validation.
      (b) **General Markdown/URL sanitizer.** Run `9d2c837f` school 324 publishes a Markdown
      daily-schedule link because the Markdown filter is admission-specific. Move it into
      the shared projection layer (`app/utils/school_attributes.py`) so no Markdown
      syntax or bare URL publishes through *any* localized display field.
      *Verify:* API sweep of the 18-school cohort (list/detail/compare, both locales) finds
      no rejected value and no Markdown token anywhere; regression tests for the 529
      provenance case and the 324 schedule-link case.
      **Done (PR #51, merged `c93c8d3`; Codex review clean).** Verified by P2.9 run 1:
      209 provenance rows expose metadata only, school 529 rejected value and school 324
      Markdown link absent from all payloads.
- [x] **P2.6 Take generative summaries out of launch scope.** Add a launch-scope setting
      (e.g. `publish_summaries: bool = False` in `app/config.py`) enforced inside
      `display_gating.summary_is_publishable`, so serialization withholds every stored
      summary regardless of validation status. Keep generation/storage code intact for the
      enrichment track, but the release refresh runs with the summarize stage skipped.
      Check the frontend renders summary-less schools without empty sections or layout
      gaps (card, detail, compare). Do NOT build deterministic text-assembly fallback
      summaries — with admissions and scraped fields also withheld they would be
      near-empty filler.
      *Verify:* with the flag off, no `summary_i18n` content in any list/detail/compare
      response; flag on restores the existing `ok`-only gate; UI regression.
      **Done (PR #51).** Verified by P2.9 run 1: zero summaries serialized.
- [x] **P2.7 Withhold website-derived admission fields.** The localized scraped admission
      fields projected by P1.11 (`entry_requirements`, `application_deadlines`,
      `available_spots`) and every other `extracted.admission` child stop serializing,
      behind the same launch-scope settings group (e.g.
      `publish_website_admission_fields: bool = False`). The official/curated
      `admission_info` projection from P1.15 is untouched and continues to publish. The
      extracted data stays stored in `attributes` for the enrichment track. This removes
      the fragmented-phrase and misclassified-requirement failures (103/105/153/506) from
      the release path.
      *Verify:* cohort responses contain no website-derived admission values in either
      locale; official admission data (points thresholds, status) still renders; tests
      cover flag on/off.
      **Done (PR #51).** Verified by P2.9 run 1: zero website-derived admission fields
      serialized; official admission data unaffected.
- [x] **P2.8 Publish curated pricing only.** Gate `SchoolPricingMixin` (and the scoreboard's
      publishable-pricing predicate — keep parity) so only rows with `source='official'`
      (curated) serialize; `scraped_website` rows stay stored as curation candidates.
      Existing source_url/confidence/error-level gates still apply to curated rows. Add a
      lightweight curation path: a script or documented manual process that records
      school, category, amount, currency, period, age_group, `source_url`, and a
      verification date, writing `source='official'`. Launch slice: the 57 schools that
      currently have scraped rows — surface those rows (school, page URL, extracted values)
      as a human-verification worklist; the full 175 private + 3 international schools are
      post-launch work. Sparse is acceptable; the UI already displays "pricing not
      available".
      *Verify:* no `scraped_website` row in any API response; a curated row renders with
      its source badge; scoreboard counts publishable = curated and matches serialization.
      **Done (PR #51).** Verified by P2.9 run 1: 0/39 stored scraped rows serialized;
      gate/scoreboard/API parity confirmed; curation worklist written to
      `backend/reports/curation/pricing-worklist.md`.
- [x] **P2.9 Boundary-focused pilot (launch configuration).** After P2.5–P2.8, rerun the
      same reviewed 18-school cohort on the **current cheap models** (the launch
      configuration — do not combine with the separate P2.10 frontier experiment). Acceptance is
      boundary-only, per the revised launch-acceptance list above: zero internal/raw/
      rejected-value leakage including provenance, zero summaries, zero website-derived
      admission fields, zero scraped pricing, clean names/locations/filters/NVO/API checks,
      100% eligible validation coverage, complete cost accounting. Explicitly out of
      scope: judging prose or extraction richness. If green, the full refresh may be
      approved (subject to the spend-guardrail item).
      **Run 1 (2026-07-15, after PR #51 `c93c8d3`) — boundary clean; location repairs
      required; rerun after P2.11.** Run `07ffb261-5cb7-42c8-85c9-47a8bb7b81b1`
      ($0.028893; 172,229 input / 17,595 output tokens): every P2.5–P2.8 boundary
      criterion passed — zero summaries, website-derived admission fields, or scraped
      pricing rows serialized; provenance metadata-only including the school 529 rejected
      value; no Markdown including the school 324 link; no internal keys; pricing
      gate/scoreboard parity; 17/17 eligible validation coverage. The failures were
      location-scope, not boundary: schools 440/454/460/570 are Sofia-*province* towns
      (Svoge, Elin Pelin, Etropole, с. Осоица) mislabeled `city='sofia'` — dataset-wide,
      65 sofia-labeled schools have coordinates outside the gate bbox — school 233 (real
      Sofia, ж.к. Дружба) lacks coordinates (76 sofia-labeled schools have none), and
      precision-metadata 0/17 is a sequencing artifact (legacy coordinates predate P1.3;
      P2.11(c) performs the regeocode). Evidence:
      `backend/reports/pilot/07ffb261-5cb7-42c8-85c9-47a8bb7b81b1/acceptance.md`.
      The rerun after P2.11 uses the regenerated cohort, and precision-metadata coverage
      is then fully in scope.
      **Run 2 (2026-07-16, after P2.11 merged `df0a032`) — substantially green; NO-GO
      pending the bounded fixes now tracked as P2.12; no further LLM reruns required
      (P2.12/P2.13 verification is deterministic).** Run
      `db4ba90d-6894-4164-a873-34ee79687fad` ($0.029856; 181,922 input / 17,374 output
      tokens; status `partial`: school 161 URL returned HTTP 503): 17/17 eligible
      validation coverage; zero summaries, website-derived admission fields, or scraped
      pricing serialized (0/27 stored rows pass the launch predicate); provenance
      metadata-only (202 rows; school 529 rejected values absent); no internal API keys;
      pricing gate/scoreboard/API parity; 394/394 Sofia geocoded locations (100%) carry
      precision metadata; 0 Sofia out-of-bounds coordinates; school 233 unresolved with
      persisted `failed`/`nominatim` failure evidence (accepted per the P2.12(d) rule);
      NVO not refreshed. Revised full-refresh projection: 363 eligible schools,
      ≈$0.75 including 25% contingency. FAILs, all bounded and tracked in P2.12:
      (a) school 367 location 897 leaks Markdown link syntax
      (`](https://…index.php)`) into list address text; (b) one unexplained coordinate
      collision (locations 119/1147; the other 7 duplicate groups are explained
      shared-address/campus); (c) 3 of 21 cohort locations unresolved (school 593:
      locations 1143/1145; school 631: location 1188); (d) school 161 transient 503.
      Evidence: `backend/reports/pilot/db4ba90d-6894-4164-a873-34ee79687fad/acceptance.md`.
      P2.9 closes when P2.12(a)–(d) are fixed and re-verified by targeted API/DB checks
      against the stored run — not by another LLM rerun.
      **Closed by PR #54 (2026-07-16), with zero LLM calls.** The stored-run sweep made
      54 list/detail/compare requests across BG/EN headers: 54/54 HTTP 200, zero
      Markdown/bare-URL display hits, and school 367 clean in all six payloads where it
      appeared. Location 119 was corrected from checked-in GeoJSON feature
      `BG_30912207846`; the duplicate audit now has seven explained shared groups and
      zero unexplained groups. Locations 1143/1145/1188 remain safely null with complete
      terminal failure evidence. School 161 returned 503 on both permitted plain HTTP
      retries and remains fail-closed (`failed_validate`, null `website_url`, persistent
      withholding marker). Full backend suite: 834 passed. Codex review fixes additionally
      pin terminal status to deterministic miss/rejection reasons (transient 429/503
      failures remain retryable) and make the audit reuse the production sanitizer so
      bare domains cannot evade the sweep. Deterministic no-address failures record the
      `local_validation` provider, and the audit carries a tracked default cohort plus an
      optional `--cohort-file` override (no dependency on gitignored run artifacts).
- [x] **P2.10 Model-capability experiment — CLOSED 2026-07-17: frontier FAILS;
      architecture ceiling, not model capability (not a launch gate).** Run
      `396dffe5-c905-44e2-8582-1d4c1bcdbd66` pinned extraction to the plain
      `google/gemini-3.1-pro-preview`, Google AI Studio only, provider fallbacks off,
      and `require_parameters=true`, with no prompt tuning. The partial cohort cost
      **$0.841696**; the school-538 extraction-only top-up cost **$0.081542**; exact
      combined provider-accounted spend was **$0.923238**. Ten of 18 extractions
      persisted (nine in the cohort run plus 538). Cohort Stage 6 validation and Stage 7
      summarize were not reached, which is sufficient because the experiment judged the
      blocker taxonomy rather than coverage. All five reviewable cases — 538, 570,
      153-pricing, 105, and 153-admission — failed with output identical or equivalent
      to the cheap tier. The completed verdicts and source-grounded findings are the
      record of decision in
      `backend/reports/pilot/396dffe5-c905-44e2-8582-1d4c1bcdbd66/blocker-review.md`.
      Consequences:
      (a) the full refresh uses the **current cheap extraction tier, unchanged**;
      (b) value-model runs are void until the extraction architecture changes and are
      removed from the launch path;
      (c) upgrading the capable spot-check tier to `deepseek/deepseek-v3.2` remains an
      **open refresh-config decision**, not a blocker. Decide it when configuring the
      refresh; it does not reopen the extraction-model verdict or P2.10.
      The launch scope and frozen publish boundary remain unchanged.
- [x] **P2.11 City-scope repair + import gate, then cohort regeocode.** (From P2.9 run 1.
      Executes between P2.9 run 1 and the P2.9 rerun — see the Order line above.) The
      registry import stamped `city='sofia'` onto Sofia-*province* schools: 65
      sofia-labeled schools have coordinates outside the gate bbox
      (`SOFIA_MUNICIPALITY_BOUNDS` in `app/services/geocoding/bounds.py`) with addresses
      in Botevgrad, Samokov, Pirdop, Svoge, Elin Pelin, Etropole, Dragoman, Godech,
      Zlatitsa, Slivnitsa, and villages; some of the 76 sofia-labeled schools *without*
      coordinates are also province (570, с. Осоица). Their coordinates are mostly
      correct — the label is wrong. **Decision (user-approved 2026-07-15): relabel to the
      real city, don't delete** — these schools leave Sofia MVP scope but stay in the DB
      for the multi-city expansion.
      (a) **Data repair:** derive each school's real city from its address/municipality
      using the GeoJSON admin dataset (read `skills/geocoding/SKILL.md` first — Sofia is
      СТОЛИЧНА, and province/municipality naming has landmines). Classify by
      address/municipality, never by coordinates alone. Sweep BOTH populations: the 65
      out-of-bbox schools and the 76 no-coordinate schools (each is either real Sofia →
      keep and geocode, or province → relabel). Write the classification table to
      `backend/reports/scope-repair/<timestamp>/classification.md` before mutating, and
      store the repair as a reviewable script, not ad-hoc SQL.
      (b) **Import gate (standing rule — a repair may only merge with the gate that makes
      it unnecessary):** the registry adapter derives `city` from the record's own
      municipality/settlement field, never from the import batch scope. Regression: a
      province record imported in a "sofia"-scoped run gets its real city.
      (c) **Cohort regeocode:** after relabeling, run the corrected force-regeocode
      (P2.4 tooling, `scripts/geocode_locations.py`) over Sofia scope so every geocoded
      location carries precision metadata and gaps like school 233 (ж.к. Дружба) resolve;
      the write gate NULLs what it cannot support.
      (d) **Cohort + projection refresh:** regenerate the reviewed 18-school pilot cohort
      with the same stratification (440/454/460/570 leave Sofia scope) and recompute the
      full-refresh cohort size and cost projection (was 474 website URLs; will shrink).
      *Verify:* audits show zero sofia-labeled out-of-bounds coordinates; 100%
      precision-metadata coverage among sofia geocoded locations; school 233 has
      coordinates or a documented rejection; relabeled schools absent from Sofia
      list/map but present in the DB; import regression green. Then rerun P2.9 with the
      regenerated cohort.
      **Done (PR #53, merged `df0a032`).** Verified by P2.9 run 2: 0 Sofia out-of-bounds
      coordinates; 394/394 precision-metadata coverage; school 233 documented-unresolved;
      cohort regenerated (363 full-refresh eligible schools, ≈$0.75 projection).
- [x] **P2.12 Release-boundary hardening (from P2.9 run 2 + 2026-07-16 reviews).** All
      verification is deterministic (targeted tests, API sweeps, DB audits) — no LLM
      cohort reruns.
      - [x] **Run-2 bounded failures (a)–(d): done in PR #54 (2026-07-16).** Shared
        address sanitization, the 119/1147 collision repair, terminal handling for
        1143/1145/1188, and school-161 fail-closed evidence are implemented and covered
        by the P2.9 closure evidence above. Routine geocoding no longer retries terminal
        failures; explicit `force=True` is required. Terminally unresolved schools stay
        in list/search/filter/count results while the existing map coordinate gate omits
        them. Deterministic audit: 54/54 API requests, zero Markdown hits, zero
        unexplained duplicate groups; 834 backend tests passed. The terminal predicate
        is restricted to deterministic outcomes; transient provider errors remain
        automatically retryable. The audit calls the shared production sanitizer and
        therefore covers both scheme URLs and bare domains, and its tracked default
        cohort makes the command runnable without the local P2.9 report directory.
      - [x] **Remaining P2.12 work (e)–(g): complete in PR #56
        (2026-07-16; zero LLM calls).**
        Curated pricing now requires an official source, traceable URL,
        bounded confidence, explicit verifier identity plus timezone-aware verification
        time, a verification date, and a valid positive single amount or ordered range;
        malformed and prose-bearing unverified rows fail closed through the shared API/
        scoreboard predicate. Class size, daily schedules/opening hours, established
        year, and the localized allowlist's other dynamic admission members remain stored
        but are withheld by default behind the launch-scope flags; curated top-level
        values remain publishable. Sparse UI views hide empty sections and unknown-status
        badges, omit the unsupported “standard curriculum” fallback, render absent amenity
        evidence as unavailable rather than “No”, and qualify pricing/admission/map copy.
        Address-less locations remain visible in comparison when they carry usable
        age-group, shift, or distance evidence; a regression test covers all three evidence
        paths and the fully empty case. Verification: 852 backend tests; frontend tests,
        lint, and production build; stored-data audit 54/54 HTTP 200 with zero Markdown,
        dynamic-field, or invalid-pricing hits, zero unexplained duplicate groups,
        terminal-location and school-161 withholding evidence intact, and `llm_calls=0`.
        **Pricing-date decision (2026-07-16; Codex round 4 accepted-and-declined):** retain
        the P2.8 contract: `scraped_at` is the public verification date for curated rows,
        while `pricing_context.human_verification.verified_at` is the authoritative timestamp
        and is required by the publication gate. Extraction times remain preserved on the
        stored `scraped_website` candidate rows. A dedicated column is outside launch scope;
        the response naming can be revisited post-launch without a database migration (P4.8).
      (a) **Fix the school 367 address Markdown leak.** Location 897 serializes
      `](https://60ousvsvkirilimetodii.com/index.php)` in list address text — the P2.5(b)
      sanitizer does not cover address/location display fields. Extend it in the shared
      projection layer and add a regression test for the 367/897 case; re-sweep the
      cohort for Markdown tokens in every published display field.
      (b) **Classify or correct the unexplained coordinate collision** (locations
      119/1147). Either document it as a legitimate shared-address/campus group (like the
      other 7) or repair the wrong coordinate; the audit must end with zero *unexplained*
      duplicate groups.
      (c) **Resolve or safely withhold the unresolved cohort locations** (school 593:
      1143/1145; school 631: 1188). Retry school 161's URL at most twice; if the 503
      persists, withhold its website-derived data via the standing withholding marker and
      record the evidence.
      (d) **Standing rule — accept unresolved coordinates.** A location whose geocode
      remains null WITH recorded failure evidence is an accepted terminal state (school
      233 is the exemplar). Missing coordinates must never trigger another pipeline
      cycle; the UI simply omits the school from the map while keeping it in the list.
      (e) **Harden curated pricing before publishing any row.** The curation path must
      require explicit human-verification metadata (who/when) and a verification date;
      validate amount/range shape (positive amount, sane currency/period, range low ≤
      high); never publish uncurated pricing prose. Extend the P2.8 gate tests to cover
      malformed curated rows failing closed.
      (f) **Withhold remaining high-risk scraped numeric/dynamic fields** unless curated:
      class size, schedules/opening hours, established year, and any similar
      easily-stale or easily-misassociated numeric — same launch-scope settings pattern
      as P2.6/P2.7. Sweep the localized-attributes allowlist for other members of this
      class while there.
      (g) **UI truthfulness pass.** Hide empty sections and unknown-status badges; never
      render missing evidence as a negative claim ("No", "standard curriculum") — absence
      of data is "not available", not "not offered"; temper any UI copy that overstates
      pricing/admission coverage (the launch dataset is sparse by design).
      *Verify:* regression tests for (a)/(e)/(f); cohort API sweep clean on Markdown and
      withheld fields; duplicate-coordinate audit shows zero unexplained groups; UI
      renders sparse schools without fabricated negatives.
- [x] **P2.13 Exhaustive Sofia audit (deterministic, no LLM).** After P2.10's closure,
      in a separate session: run the full
      audit battery over the entire Sofia scope, not just the cohort — API
      boundary sweep (list/detail/compare, both locales) against the launch-acceptance
      list; geocode audits (bounds, precision metadata, duplicates); cached truth-set
      checks against stored extraction runs; scoreboard parity. Zero LLM spend.
      *Verify:* a written audit report under `backend/reports/`; every launch-acceptance
      criterion green or explicitly waived in this file.
      **Then STOP.** With P2.13 green and the OpenRouter spend-cap decision recorded,
      request the user's explicit approval before the one-time full Sofia refresh
      (363 schools; update the projection for the current cheap tier; summarize skipped).
      **Done 2026-07-17 (`llm_calls=0`).** The exhaustive sweep covered all `443`
      Sofia DB-scoped schools (a superset of the cached 363-school refresh cohort):
      `1,066/1,066` BG/EN list/detail/compare requests returned HTTP 200, with zero
      summaries, website-derived admission values, scraped pricing, raw/rejected
      provenance values, Markdown/bare-URL display text, dynamic-field leaks, or
      internal keys. It found and boundedly fixed one fail-open edge: website-derived
      attributes now require a current schema-version-1 validation report before
      publication while curated top-level attributes remain available. Final coverage
      is `14/14` (`100%`) with `0` published without a report; the other stored candidates
      remain withheld pending refresh. Geocoding is green (`394/394` precision metadata,
      zero out-of-bounds, seven explained duplicate groups, zero unexplained, all 86
      terminal failures null with evidence). Cached truth sets `db4ba90d…` and
      `396dffe5…` are present and their known rejected/incorrect cases remain withheld;
      pricing predicate/scoreboard/API parity is exact at zero rows. Evidence:
      `backend/reports/p2-13/20260717T124505Z/acceptance.md`. The spend recommendation is
      recorded above, but its checkbox remains a user decision. **STOP: no refresh run.**

      **Refresh closure 2026-07-20.** The approved one-time refresh was recovered without
      discovery, summarization, NVO work, cohort broadening, or model changes. The final
      deterministic audit is green after bounded publication fixes for list-view NVO,
      contact/prose display-name candidates, coarse Nominatim duplicate points, and the
      display-name audit CLI. Of the fixed 386-school cohort, 344 completed fresh
      navigation/extraction/validation, 9 navigation failures remain withheld by the
      current-report gate, and 33 URL-validation failures remain fail-closed. English-name
      curation remains explicit: 117 website-backed private/international schools lack a
      source-backed published EN identity (44 retain uncorroborated EN candidates); school
      520 is among them. The refreshed pricing worklist is 48 schools / 205 rows, all
      withheld pending human curation. Full evidence is in
      `backend/reports/refresh-recovery/20260720T080608Z/acceptance.md`. **STOP: do not
      begin Phase 3.**

- [ ] **P2.14 Post-refresh operational hardening and bounded curation.** Follow-up from
      the 2026-07-20 recovery. This work does **not** invalidate P2.13 acceptance, widen
      the public boundary, authorize another refresh, or require LLM calls. Items (a)–(c)
      are required before any future city-wide pipeline run; the remaining items are
      independent bounded quality/maintenance work.
      - [x] **(a) Pipeline heartbeat and stale-run terminalization.** Persist a run
            heartbeat while work is live. Add a deterministic command/service that marks
            a stale `RUNNING` run `PARTIAL` when stage evidence exists, otherwise
            `FAILED`, recording the last completed stage, completed/failed school counts,
            timestamps, and the terminalization reason. It must never infer that a live
            process is dead from status alone. Cover live, stale-partial, stale-empty, and
            idempotent rerun cases. **Done locally 2026-07-20:** heartbeat timestamps,
            completed-stage checkpoints, row-locked stale recovery, and the deterministic
            `terminalize-stale-runs` command are covered by all four required cases.
      - [x] **(b) Atomic validation-evidence rollover.** A validation attempt must not
            erase the previously accepted current report merely because replacement work
            has started. Promote a replacement atomically after it is durably written;
            an explicit new validation failure must still withhold immediately. Preserve
            audit history and prove crash/interruption behavior with tests so temporary
            coverage cannot collapse from hundreds of reports to a small completed
            prefix. **Done locally 2026-07-20:** accepted reports survive pending and
            crashed replacement attempts; successful replacements archive and atomically
            promote, while explicit failures persist immediate withholding.
      - [x] **(c) Exact per-run provider cost ledger and hard cap.** Attach provider
            request ID, pipeline/stage/run ID, school ID where applicable, model, token
            counts, provider-reported cost, and timestamp to every billable request.
            Enforce the cap against persisted attributed spend before dispatching the
            next request and fail conservatively when attribution is missing or delayed.
            Reconcile the ledger against provider totals and retain an explicit
            discrepancy field; interval-delta inference should remain an emergency
            fallback only. **Done locally 2026-07-20:** tracked billable runs require a
            cap; concurrent batch calls serialize from durable reservation through exact
            attribution so each next dispatch uses exact persisted spend; missing/delayed
            attribution blocks later dispatch and forces non-success run finalization;
            URL recovery shares the same mandatory-cap boundary; exact request evidence feeds
            run totals; native OpenRouter calls request exact usage metadata; a response that
            crosses the cap is preserved as audit evidence and forces failed finalization; and
            deterministic reconciliation stores the provider discrepancy.
      - [x] **(d) Navigation timeout isolation and telemetry.** Keep bounded navigation
            chunks, but make each school independently terminal. Record the URL/page,
            timeout phase, elapsed time, attempt count, and final reason. Use the nine
            recovery failures (`179, 274, 301, 369, 521, 547, 559, 630, 633`) as the
            regression cohort; do not broaden into discovery or extraction while testing.
            **Done locally 2026-07-20:** timed-out chunks fall back to independently
            terminal school attempts with durable URL/page, phase, elapsed, attempt, and
            reason telemetry; the exact nine-school fixture isolates one terminal failure.
      - [ ] **(e) Bounded launch-data curation.** Work from the recorded queues: 117
            website-backed private/international schools lack a source-backed published
            English identity (44 have uncorroborated candidates; include school 520), and
            48 schools / 205 scraped pricing rows remain withheld. Curation must preserve
            the existing corroboration and official-pricing gates. Schools 113 and 116
            retain their dynamic-field discrepancies unless independently curated.
            Terminal geocodes remain accepted: the 88 NULL locations with durable failure
            evidence are not a retry queue and must not trigger another pipeline cycle.
            **Deliberately deferred 2026-07-20:** no curation or geocode retry was run; the
            recorded 117-name, 48-school/205-price-row, schools 113/116, and 88-terminal-NULL
            populations remain unchanged.
            **Slice 1 complete 2026-07-23; item remains open:** using cached official-site
            evidence only, school 520 was curated as `ЧОУ „Фюжън“` / `Fusion School`
            through the existing two-signal corroboration gate. The identity queue is now
            116 schools; 44 remaining schools retain uncorroborated English candidates.
            Pricing remains 48 schools / 205 withheld rows because no accountable human
            verification metadata was supplied. Schools 113/116 and all 88 accepted
            terminal-NULL geocodes remain untouched. The exhaustive deterministic boundary
            audit passed with zero LLM calls. Evidence:
            `backend/reports/p2-14e/20260723T075840Z/acceptance.md`.
            **Slice 2 complete 2026-07-23; item remains open:** ten additional schools
            were reviewed from cached official-site evidence. Nine identities now publish
            through the unchanged corroboration and website-data gates. School 521's
            `The Beehive` identity is source-backed in storage but remains correctly
            withheld after its recorded navigation failure; the API keeps the mechanical
            fallback and no validation evidence was invented. The effective missing-
            published-identity queue is 107 schools, including 34 uncorroborated English
            candidates plus school 521's corroborated-but-withheld identity. Pricing stays
            at 48 schools / 205 withheld rows; schools 113/116 and all 88 terminal-NULL
            geocodes remain untouched. Focused tests passed (`76`), and the exhaustive
            1,066-request deterministic boundary audit passed with `llm_calls=0`.
            Evidence: `backend/reports/p2-14e/20260723T091402Z/acceptance.md`.
            **Slice 3 complete 2026-07-23; item remains open:** ten more candidates were
            classified offline. Contact labels at schools 153/512/569, the `VISIT SCHOOL`
            navigation label at 633, and the cross-institution Uwekind label at kindergarten
            634 were rejected and removed with private audit metadata. Candidates at
            178/339/404/527/542 were explicitly deferred for insufficient independent cached
            English evidence. The missing-published-identity queue remains 107; stored
            uncorroborated English candidates fell from 34 to 29, including five now marked
            deferred. Pricing stays 48 schools / 205 withheld rows; schools 113/116 and all
            88 terminal-NULL geocodes remain untouched. Focused/API tests passed (`144`),
            the targeted affected-school leak sweep was clean, and the exhaustive
            1,066-request audit passed with `llm_calls=0`. Evidence:
            `backend/reports/p2-14e/20260723T103943Z/acceptance.md`.
            **Identity queue fully classified 2026-07-23; pricing decision required:**
            bounded offline slices classified all 117 recorded identity records: 10
            source-backed identities publish, school 521 is corroborated but remains
            fail-closed, 96 are explicitly deferred for lack of source-backed cached
            English evidence, 10 unsafe contact/navigation/cross-institution candidates
            were rejected, and zero remain unclassified. Mechanical transliterations were
            not relabeled as curated English identities. Pricing remains 48 schools / 205
            withheld rows and now requires an accountable human verifier or an explicit
            launch deferral decision. Schools 113/116 and all 88 terminal-NULL geocodes
            remain untouched. Final identity manifest and evidence:
            `backend/reports/p2-14e/20260723T120938Z/identity-decisions.json` and
            `backend/reports/p2-14e/20260723T120938Z/acceptance.md`.
            **Canonical identity follow-up complete 2026-07-24; pricing remains open:**
            the manually corroborated `The Beehive` identity for school 521 is now
            promoted to canonical `name_i18n.en` with two official-source provenance
            rows. A reusable dry-run-first command requires the same two identity signals,
            manual-review metadata, same-domain official URLs, and conflict-free canonical
            state. Authoritative imports preserve only explicitly promoted locales they
            omit; uncurated legacy EN values are not retained. List, detail, and search now
            agree on `The Beehive`, while the absent Stage 6 report still withholds all
            unrelated website-derived fields. The scoreboard reports 11/11 curated
            identities published, zero blocked, and zero conflicts. Full backend tests
            passed (`897`), and the exhaustive 1,066-request boundary audit passed with
            `llm_calls=0`. Evidence:
            `backend/reports/p2-14e/20260724T083539Z/acceptance.md`.
            **Resolver hardening complete locally 2026-07-24; no records changed:** exact
            official homepage/core-page identities can now pair with compact, digit-bearing,
            or acronym domain matches while generic institution words cannot corroborate a
            domain by themselves. English candidates require English-specific evidence, so a
            valid Bulgarian label cannot publish an invented English translation. Future
            navigation retains title, Open Graph site name, organization JSON-LD, and logo
            accessibility text; OCR remains deliberately excluded. A read-only cached-page
            evaluation accepted schools 527/542 and would replace school 541's candidate with
            `ABC KinderCare Centre`; the other seven remained deferred, including
            `Pythagoras School`. No refresh, provider call, geocode attempt, or persistence was
            performed. Full backend tests passed (`924`). Evidence:
            `backend/reports/p2-14e/20260724T110802Z/acceptance.md`.
            **Host-label correction and fixed benchmark complete 2026-07-27; no
            records changed:** domain corroboration now inspects every meaningful host
            label, so a generic subdomain no longer hides the brand in
            `school.fusion.bg`; platform-hosted school subdomains remain supported. A
            reusable read-only benchmark (`uv run python
            scripts/evaluate_identity_resolver.py`) fixes the evaluation populations at
            11 independently corroborated English identities, 10 known-unsafe labels,
            and 96 deferred records. On 1,599 cached pages the resolver accepts 7/11
            known-good identities, leaks 0/10 known-bad labels, and accepts 2/25 recorded
            deferred English/Latin candidates. Although 89/96 deferred records contain
            some candidate, most are Bulgarian-only and must not be counted as English
            candidate coverage. No LLM, provider, refresh, OCR, geocode, or persistence
            path was used. Full backend tests passed (`930`).
            **Bounded LLM adjudication pilot complete 2026-07-27; no records changed:**
            a read-only adjudicator (`uv run python scripts/pilot_identity_adjudication.py
            --live`) assembles bounded evidence from cached valid official pages plus
            shared-domain sibling institutions and asks one model question per candidate:
            does this English label name *this* institution on its own site? On the fixed
            population (the 4 known-good identities the resolver misses and all 10
            known-bad traps) the final configuration recovered **3/4** missed
            identities — 393, 506, 624 — and leaked **0/10** traps, for 10 calls and
            $0.002. The first run, before review hardening, recovered 4/4.
            **The model alone is not the gate**, in both directions. Across the two
            runs it accepted two traps: `Uwekind International School` for kindergarten
            634, whose cached pages are literally the same shared-domain pages as its
            sibling school 635, and `Discoverer International School` for kindergarten
            589 while quoting text absent from the cached pages (it rejected 589 on the
            second run — the model is not deterministic). For 634 the education-level
            guard was the *only* defense, so page evidence and sibling context cannot
            separate a kindergarten from its sibling school on a shared domain.
            In the other direction, 635's *correct* identity is now withheld because the
            model attached one fabricated quote
            (`уникалното приложение Eurobuddy в Uwekind International School`, present
            in no supplied line) to three genuine ones. The guard is working as designed
            and the trade-off is deliberate: a model that fabricates evidence is not
            trusted for that case, and withholding a correct name costs far less than
            publishing a wrong one. A future reviewer could instead require *at least
            one* verifiable quote and drop the rest; that would recover 635 and tolerate
            fabrication, so it is recorded as an option, not taken. Guards only ever
            reject, an accept additionally requires the candidate to appear verbatim in
            cached pages with verifiable quotes and two same-domain source URLs, and the
            outcome is a recommendation for manual promotion through the unchanged
            `identity_curation` gate — nothing publishes automatically. Four
            contact/Bulgarian-only traps (153, 512, 546, 569) never reach the model.
            No database write, publication, refresh, provider discovery call, OCR, or
            geocode attempt was performed. Final evidence:
            `backend/reports/p2-14f/20260727T112143Z/acceptance.md`; first run:
            `backend/reports/p2-14f/20260727T103912Z/acceptance.md`.
            Review round 1 hardened three gaps: a dispatched request that times out is
            now reported as a call made (failed requests may still bill), an accept with
            no non-empty quote is rejected instead of passing verification vacuously,
            and supporting URLs require the *exact* official host rather than the
            registrable domain, matching `curated_identity_candidate` so a
            same-domain sibling host cannot produce a recommendation that promotion
            would refuse with `source_domain_mismatch`. Re-scoring the recorded verdicts
            under the hardened guards (`--replay-json`, zero new LLM calls) reproduced
            every decision and every supporting-URL count unchanged. Review round 2
            closed three more: candidate and quote matching now preserve token
            boundaries (`Sunny House` no longer "appears" in `Sunny Houses`, which two
            near-matches could otherwise have turned into a promotion recommendation),
            an accept carrying a rejection `reason_code` fails closed, and agent
            construction sits inside the handled block so a missing
            `OPENROUTER_API_KEY` rejects one case instead of aborting the run. A second
            replay again reproduced all 14 decisions and supporting-URL counts
            unchanged. Review round 3 closed two more: quotes are verified against a
            single evidence line rather than the concatenated corpus, so a quote
            fabricated across a line boundary cannot corroborate an accept, and the
            adjudication agent runs with zero retries, because a PydanticAI output
            retry would bill a second provider request while the pilot counted one. A
            third replay again reproduced all 14 decisions unchanged. Review round 5
            closed the last substantive gap: a candidate occurring past a long line's
            200-character truncation counted as supporting provenance while the excerpt
            shown to the model and to a human reviewer omitted the identity. Evidence
            lines are now windowed around the match, and a match that cannot be shown
            within the bound no longer counts. Because that changes the text the model
            sees, the recorded verdicts could no longer be replayed faithfully, so the
            bounded live run was repeated once against the final evidence (10 calls,
            $0.002) — which is where the 3/4 result above comes from.
            *Open decision:* whether a named reviewer promotes the three recommended
            identities. The pilot does not make that call.
      - [x] **(f) One public identity predicate for serialization and search.** Keep
            display-name matching on the same resolved publication path used by API
            serialization. Add a guard test so a future SQL optimization cannot make
            withheld contact/prose/SEO candidates searchable. Apply this rule to any new
            searchable website-derived field.
            **Already satisfied on starting `main` by `0fb2a8a`:** search removed the
            approximate JSON/SQL identity predicate and uses the serialization resolver;
            API guards cover withheld, uncorroborated, prose-like, malformed-evidence,
            and valid corroborated identities.
      - [x] **(g) GitHub Actions runtime maintenance.** Upgrade action versions that still
            target deprecated Node.js 20, then verify backend and frontend jobs on a PR
            and the post-merge `main` workflow. Treat the current forced Node.js 24
            execution warning as maintenance, not a launch-acceptance failure.
            **Done 2026-07-22:** Node.js 24-compatible action pins passed both jobs in PR
            run 254 and post-merge `main` run 29918717740 on squash commit `b69a5c4339`;
            the merge tree exactly matches Codex-reviewed head `afae50d924`.
      *Verify:* focused tests for every changed lifecycle/gate; full backend and frontend
      suites; deterministic cost-reconciliation and validation-interruption fixtures;
      no LLM calls or real refresh; written evidence under `backend/reports/`; update this
      plan with completed items and any deliberately deferred curation counts.
      **Final evidence 2026-07-22:** `backend/reports/p2-14/20260720T124735Z/acceptance.md`;
      backend `890 passed`; frontend lint/build and `7 passed`; migration head
      `5ac75d912125`; zero provider calls; Codex round 4 clean; PR and post-merge
      `main` workflows passed. P2.14(e) remains deliberately open with its recorded
      evidence requirements and unchanged curation queues.

## Phase UX — UI/UX review (2026-09-24)

Source: a UX review done as a UX expert and as four parent personas (kindergarten parent
who thinks in birth years; 7th-grade parent choosing a gymnasium; expat parent comparing
private schools; parent who already knows the school name), with screenshots at 390 / 820 /
1440 px and a scripted back-navigation measurement (list scroll 1500 → 0, selection and map
view lost, skeleton re-shown, ~10 API calls on return). Frontend-only; no response-field or
gate changes. Mike approved (2026-09-24) agent self-merge for these PRs on green CI after
self-review, with at most one Codex review on the larger ones.

- [x] **U1 Correctness fixes.** (a) Compare quick-overview formats EUR prices as BGN
      (`formatCurrency` called without the row currency); all display fallbacks default
      to `'BGN'` although Bulgaria uses EUR since 2026-01-01. (b) Desktop search has no
      result count or sort control (the bar is `lg:hidden`). (c) No language toggle on the
      mobile search page (nav hidden). (d) Name sort is lexicographic ("1, 10, 101") and
      puts stray leading quotes first — use numeric collation. (e) Desktop filter sidebar
      is `w-1/5` (288 px at 1440) and clips the address Search button. (f) Mobile search
      uses `h-screen` (100vh) — use `100dvh`. (g) "12th grade" NVO labels while the exam
      tabs are 4/7/10 — verify and fix.
      **Done 2026-09-24:** compare range uses the headline cohort in EUR
      (`yearlyTuitionRangeEur`, BGN converted at the fixed 1.95583 rate, other currencies
      skipped) and price sort converts too; fallbacks are EUR; count/sort bar on all
      widths; EN | BG switch in the mobile search header; numeric name collation ignoring
      leading quotes; 320 px sidebar; `100dvh`. (g) was a real bug: `nvo_10` used the
      "12th grade" label.
- [x] **U2 Keep search state across detail navigation.** In-memory cache of the school
      list per query; selected school, sort, distance, and list/map view in the URL
      (`replace`); restore the list's scroll position and the map centre/zoom on return
      (skip auto-fit); detail "Back" falls back to `/search` when there is no in-app
      history; drop the duplicate `useSchools` fetch.
      **Done 2026-09-24:** `api/cache.js` (10-min in-memory GET cache, shared in-flight
      requests, failures not cached; also dedupes the second `useSchools` call);
      `useSchools` renders from cache and ignores stale responses; `sort`, `within`,
      `view`, `tab`, `school` in the URL; list scroll + map centre/zoom saved per history
      entry in sessionStorage. Measured: back from a detail page restores scroll 1500 →
      1500, selection, sort and map view with no skeleton and no list refetch. The detail
      "Back" fallback moves to U3 (the detail page is being edited under U6).
- [x] **U3 Desktop detail side panel.** On `lg+`, opening a school shows its detail in a
      panel over the list/map instead of leaving the page; `/schools/:id` stays a
      deep-linkable full page (and is what mobile uses). "Details" becomes a real link.
      **Done 2026-09-24:** `?detail=<id>` on `/search` opens `SchoolDetailPanel` over the
      filters + list (map stays visible and highlights the school); opening pushes a
      history entry (flagged in history state) so browser Back / Escape / "Back to
      results" close it, and a marker click swaps the school. "Details" is a `<Link>`:
      plain click on `lg+` opens the panel, modified clicks and mobile go to the full
      page. `SchoolDetailPage` gained an `embedded` mode, and its Back falls back to the
      last search URL when there is no in-app history (U2 leftover).
- [x] **U4 Search is the home page.** `/` shows the map + list immediately; the
      category / age group / birth-year choice becomes a compact bar on the search page
      (birth year first, per the calendar-year rule), editable at any time instead of a
      locked chip. School-name search on the search page (instant, Latin/Cyrillic/numbers)
      and school-name results open the school's page. Old `/?age_group=...` links keep
      working.
      **Done 2026-09-24:** `/` redirects to `/search` keeping its query; `LandingPage` is
      gone. The search toolbar has an `AgePicker` (birth year + enrolment year first, then
      kindergarten/school group chips with counts, preschool crossover, "show all ages";
      bottom sheet on mobile) and `SchoolNameSearch` (instant list + map filter via `q`,
      dropdown of API matches that opens the school: panel on desktop, page on mobile).
      All ages are shown until a group is picked; a dismissible welcome tip points to the
      birth-year picker. Logo links home; 36 px mobile header controls.
- [x] **U5 Results list and filters.** Compact location control ("Near: address", use my
      location, pick on map) with explicit submit instead of auto-commit on a typing
      pause; hide per-card "Distance unknown" / "Admissions info unavailable" noise;
      smaller cards; filter client-side instead of refetching on every advanced-filter
      click; frequently used filters as chips above the list; sorts by NVO result;
      Sofia-bounded initial map view; 44 px touch targets; drawer Escape/focus/labels;
      softer mobile location prompt; "clear filters" on empty results.
      **Done 2026-09-24:** `LocationControl` ("Near …" chip with my-location / address
      submitted with Enter / pick-on-map, clear, distance select) replaces the sidebar
      block and the auto-geocode-while-typing effect; State/Private chips above the list;
      cards drop "Distance unknown" / "Admissions info unavailable", merge the two meta
      rows and tighten spacing, and are keyboard-focusable; advanced filters run on the
      client (`utils/advancedFilters.js`, verified identical to the API for all 55
      options), so one fetch per age group; "NVO result" sort; the initial map fit skips
      the farthest 5 % of points (`utils/mapFit.js`); filter drawer is a labelled dialog
      with Escape; empty results offer "Clear filters". The landing-driven mobile
      location prompt went away with U4.
- [x] **U6 School detail content.** Key-facts strip at the top ordered by school type
      (price first for private, NVO/admission for state); small map + directions link;
      remove the misleading cross-grade NVO insight; linear (not smoothed) NVO chart
      lines; tappable, normalised phone numbers; official-source link instead of
      "contact school" filler; actions in the header; per-school document title;
      untranslated Bulgarian tags and Title Case scraped labels handled in the EN UI.
      **Done 2026-09-24:** key-facts strip (private: yearly tuition with monthly
      equivalent and currency, languages, grades; state: latest combined NVO vs the
      national average, min. score and shift when present; kindergarten: last-admitted
      points; distance from a saved location); pricing moves right under it; small
      locations map with a Google Maps directions link per location; cross-grade insight
      removed; linear NVO lines and a short mobile legend; `utils/phone.js` normalises
      and dials numbers (`+359`); "contact school" filler hidden, kg.sofia.bg linked for
      state kindergartens; Share/Compare in the hero (the mobile bar yields to the
      CompareBar); `<name> · Sofia Schools` tab title; free-text tags are no longer
      Title-Cased and Bulgarian tags sit under "In Bulgarian" in the EN UI
      (`utils/tags.js`).
- [x] **U7 Compare page.** Usable mobile layout (stacked rows, two schools per view);
      sticky school header while scrolling; compare list stores ids, not stale school
      objects; compare bar fits mobile and explains the 4-school limit; "Add school" keeps
      the search context.
      **Done 2026-09-24:** below `md` each row label is a full-width heading over two
      value columns; 3–4 schools scroll sideways with scroll-snap and ‹ › paging, and the
      sticky school header follows the scroll. Desktop keeps the table, with the header as
      a separate sticky table synced to the body. `localStorage.compareList` keeps only
      `{id, name_i18n, resolved_name_i18n, school_type}` (old arrays trimmed, storage and
      JSON errors guarded); a compare response refreshes names and drops ids that no
      longer exist, with a notice (an all-missing list gets "Clear all" on the error
      page). The bar is one row on mobile, hidden on `/compare`, and says
      "Maximum 4 schools" when full. "Add school" and a history-less "Back" go to
      `sessionStorage.lastSearchUrl` (a `/search` path) or `/search`.
- [x] **U8 Cross-cutting.** Clickable logo and nav link to Compare; "EN | BG" language
      switch; keyboard-reachable cards and non-colour-only status; "About the data" page
      (sources, last updated). Split the largest components as they are touched (P4.1).
      **Done 2026-09-24:** logo links home (U4); nav has "Compare (n)" (2+ schools) and
      "About the data"; EN | BG switch (U1); cards are focusable with Enter/Space (U5) and
      the status dot keeps its text badge. `/about` lists the sources the code actually
      uses (MoE register, kg.sofia.bg, MoE NVO open data, school websites for fees,
      OpenStreetMap/CARTO), the calendar-year rule and what is not published yet. There
      is no per-dataset "last updated" date in the API, so the page does not claim one.
      Removed six unused components/hooks and the strings U1–U5 orphaned. `SearchPage.jsx`
      is ~1,900 lines (from 2,024) with the panel, picker, name search and location
      control extracted; the rest of P4.1 stays open.

### Phase UX follow-ups (2026-09-24)

Found while landing U1–U8 and from Mike's testing. Open items that touch the backend or
the publish boundary wait for Mike.

- [x] **UF1 Preschool "where" defaults to both.** Birth-year selection used to scope the
      preschool year to kindergartens only (149 of 218), hiding the 69 schools until a
      checkbox was ticked. The picker now shows a "Where? Both / At a school / At a
      kindergarten" choice, defaulting to both; picking preschool from the School tab
      starts on "At a school". Same URL params as before.
- [x] **UF2 Changing filters no longer blanks the map.** "Show all ages" blurred the map
      behind a "Loading" overlay for ~4 s. Later loads keep the old markers and list
      (dimmed) with an "Updating…" pill; the list renders 30 cards at a time as it
      scrolls (was all ~440, ~0.6 s per render in dev); `SchoolMap` is memoised; the
      name collator is cached.
- [x] **UF3 "Private" includes international schools.** The API filters the exact type,
      so the Private chip hid the Anglo-American, French and German schools. School type
      is now a client-side filter where Private covers `international` too.
- [x] **UF4 Language-focus filter options are not canonical.** `/schools/filters` returns
      values like "Английски език", "английски", "bg", "Information Technology" beside
      "English". Needs a backend projection/normalisation fix (Mike).
      **Done 2026-09-24:** fixed on the frontend: language-focus values map to canonical keys (`canonicalLanguagePair`, `utils/languages.js`) both per school and for `/schools/filters` options, so spellings merge into one option with a combined count and non-languages ("Information Technology") are dropped; languages with no listed school are hidden. The backend projection still returns raw values.
- [x] **UF5 "Compare all grades" chart mixes exams.** It plots 4th/7th/10th-grade NVO on
      one axis, the same apples-to-oranges issue as the insight removed in U6. Remove it
      or restate it per exam against the national average.
      **Done 2026-09-24:** removed; the per-exam tabs, each against its own national average, remain.
- [ ] **UF6 Kindergarten "last admitted points" ignores the age group** (detail key facts).
      Latent until E6 imports thresholds; handle there.

#### UX review pass 2 (2026-09-24)

A fresh-eyes review of `main` after U1–U8 (Playwright at 390/820/1440, BG and EN, five
parent personas); findings spot-checked against the API and DB. Screenshots were kept in
the session scratchpad only.

**Frontend-only (agent may do):**
- [x] **UF7 Tablet (768–1023 px) shows two headers** and the Map/List tabs do nothing: the
      nav hides below `md`, the mobile header only from `lg`. One breakpoint for both.
      **Done 2026-09-24:** one `lg` breakpoint for the search header and nav; List/Map tabs work below `lg`; a map auto-fitted while hidden is refitted once shown.
- [x] **UF8 Mobile compare bar covers the map popup's Compare / View details buttons.**
      **Done 2026-09-24:** the mobile marker sheet sits above the compare bar.
- [x] **UF9 "Show age groups & shifts" everywhere, but no shift data exists** (0 of 1,663
      `shift` values). Say "Age groups" and drop the clock icons until there is data.
      **Done 2026-09-24:** "Age groups" wording; bullets instead of clock icons.
- [x] **UF10 NVO wording/units disagree:** "national average" vs "national benchmark",
      "+1.5%" deltas next to "+19.7 pp", an unexplained "Trend" column, "(33%)" on tabs,
      "Под ориентира" jargon. One term (national average), deltas in pp everywhere.
      **Done 2026-09-24:** "national average" everywhere (EN/BG), year-on-year change in pp, "vs 5-yr avg" column, tab percentages removed.
- [x] **UF11 Compare colours NVO on fixed 60/75 % cut-offs,** contradicting the detail
      page's "above national average" green; the row doesn't name the grade and "School
      avg" doesn't say "5-year". Colour relative to the national average; label both.
      **Done 2026-09-24:** colour relative to the national average for the same exam and year (±5 pp, as on the detail page); grade, year or "5-year average" and the national figure shown.
- [x] **UF12 СУ (grades 1–12) badged "Гимназия / High School"; ОУ translated "Primary
      School".** Derive the badge from the grades offered / school type in the name.
      **Done 2026-09-24:** `utils/levelLabel.js` derives "Grades 1–12" / "Kindergarten" from the groups offered (cards, detail hero, compare "Offers" row).
- [x] **UF13 Hard-coded "District enrollment / Прием по район"** on every state primary /
      lower-secondary card (`SchoolCard.jsx`), not from data and misleading for Sofia's
      central 1st-grade points system. Remove it.
      **Done 2026-09-24:** removed from cards and the detail page.
- [x] **UF14 Kindergarten parents get no admission signal:** add "Admission by points via
      kg.sofia.bg; thresholds coming later" to state kindergartens; hide NVO and price
      sorts for kindergarten groups.
      **Done 2026-09-24:** state kindergartens show "Admission by points through kg.sofia.bg" (card) and an Admission key fact; NVO sort hidden for kindergarten-only lists.
- [x] **UF15 Mixed-language data:** "Езици на обучение: Bulgarian", "Фокус Bulgarian",
      English tags in the BG UI and Bulgarian / Title-Case tags in EN compare and cards.
      Translate language names via i18n; reuse `utils/tags.js` in cards and compare.
      **Done 2026-09-24:** `utils/languages.js` maps English/Bulgarian/code spellings to translated names everywhere; compare tags use `TagList`.
- [x] **UF16 Counts say "149 училища" for kindergarten groups;** use a neutral or
      kindergarten noun.
      **Done 2026-09-24:** kindergarten lists count kindergartens, all-ages/preschool-both lists count results.
- [x] **UF17 Location chip "До 1, бул. Витоша"** is ambiguous and truncates on phones:
      "Близо до …", and move the distance select into the popover on mobile.
      **Done 2026-09-24:** "Близо до / Near" + street-first short address (`shortAddress`); on phones distance and "clear" live in the popover.
- [x] **UF18 Compare page phones** are not normalised or tappable; reuse `PhoneLinks`.
      **Done 2026-09-24:** compare phones use `PhoneLinks`.
- [x] **UF19 Mobile first screen fits ~1 card;** age picker and search placeholder truncate.
      One scrollable chip row for location/type/sort, one-line welcome tip, shorter
      placeholder.
      **Done 2026-09-24:** two compact header rows (location + sort; type chips + count), one-line welcome tip on phones, "Име или № на училище" placeholder.
- [x] **UF20 Filters buried three levels deep; desktop sidebar mostly empty.** Open
      advanced filters by default; put type/distance/sort in the desktop sidebar.
      **Done 2026-09-24:** advanced filters open by default in the sidebar and drawer.
- [x] **UF21 Card footer toggles wrap onto two lines each on phones.**
      **Done 2026-09-24:** "Age groups" / "Show more" fit on one line after the UF9 rename.
- [x] **UF22 Map popup repeats the grades line;** desktop popup overlaps "Reset view".
      **Done 2026-09-24:** duplicate line removed; "Reset view" moved bottom-right.
- [x] **UF23 "Search in visible map area" offered on the mobile List tab** (map hidden).
      **Done 2026-09-24:** the map-area filter shows in the drawer only on the Map tab.
- [x] **UF24 Contrast:** white on primary-500 badges (~2.5:1) and primary-600 buttons
      (~3.7:1) fail WCAG AA; use primary-700.
      **Done 2026-09-24:** filled buttons/badges use primary-700 (hover 800), state/private/international badges teal-700 / violet-600 / blue-700.
- [x] **UF25 Duplicate `#school-name-search` id** (mobile + desktop copies).
      **Done 2026-09-24:** `useId`-based ids.
- [x] **UF26 Unknown URLs show React Router's developer error page;** add a translated 404.
      **Done 2026-09-24:** translated 404 for unknown URLs and a separate route-error screen.
- [x] **UF27 Footer with About and a "report a data error" link** — use
      `contact@schooldecider.com` (Cloudflare Email Routing to Mike's inbox, live 2026-09-30).
      **Done 2026-09-30:** footer on About, compare, school and 404 pages (not the full-screen
      search map, whose nav already links About): About link and a `mailto:` report link with a
      translated subject.
- [x] **UF28 Monthly tuition is yearly ÷ 12** though most schools bill over 9–10 months;
      drop it or label it.
      **Done 2026-09-24:** the ÷12 monthly figure is gone from key facts and compare.
- [x] **UF29 Small copy issues:** "Tuition / Admission" row shows only tuition; birth-year
      list includes the enrolment year itself; unexplained tab percentages.
      **Done 2026-09-24:** NVO tab percentages removed (UF10). The birth-year list including the enrolment year is correct (a baby born that year can join a nursery group). "Tuition / Admission" stays: state rows fill in once E5/E6 import thresholds.
- [x] **UF30 (P2) Map markers don't distinguish kindergartens from schools** in All ages.
      **Done 2026-09-24:** inner mark on the pin: dot = school, square = kindergarten (colour still state/private); legend explains both.
- [ ] **UF31 (P2) Emoji and SVG icons mixed;** flags stand in for languages.

**Backend / data (wait for Mike):**
- [x] **UF32 Name search misses how parents write names:** "СМГ", "НПМГ", "1 АЕГ",
      "119 СУ", "СУ 119" return nothing (only "119" or a full word works). Strip type
      words and "№", match number + rest, small curated alias table.
      **Done 2026-09-24:** PR #104: query normalisation (№, ordinals, type abbreviations, Latin↔Cyrillic), verified acronym table (СМГ, НПМГ, 1/2 АЕГ, НГДЕК, …), ranking; `schoolMatchesQuery` mirrors it.
- [x] **UF33 Three locations sit on the Sofia centroid** (e.g. ДГ №31 Люлин, id 143), so
      they show as ~0.2 km from the centre. Re-geocode them and treat centroid hits as
      "no coordinates" (no distance) in a guard.
      **Done 2026-09-24:** PR #103: area-level Nominatim results and city-only GeoJSON streets are rejected in the pipeline; the three centroid locations (ids 149, 708, 1173) corrected with evidence and a pg_dump backup.
- [x] **UF34 English names are transliterations** ("Amerikanski Kolezh V Sofia",
      "Chastna Ezikova Gimnaziya…") next to translated ones; "American" does not find the
      American College. Curated EN names for well-known schools + type-word translation.
      **Done 2026-09-24:** PR #107: generated English names translate the school type ("94th Secondary School …", "Kindergarten No. 31 …") and 8 well-known schools have curated English names with evidence (American College of Sofia, First English Language School, Lycée Français de Sofia Victor Hugo, German School Sofia, …; pg_dump backup taken).
- [x] **UF35 Only 54 state kindergartens listed** with no coverage statement; check
      against kg.sofia.bg and state coverage on /about and in counts.
      **Done 2026-09-24:** root cause was the kg.sofia adapter fingerprinting all 501 records on a
      limited first run, so later runs skipped the rest. PR #109 fixes it (unlinked records count as
      changed; only kindergarten/nursery records create schools; re-import never recreates existing
      locations). Import (kg.sofia adapter only, dry runs first, pg_dump backups): state
      kindergartens listed 54 → 299 (200 numbered ДГ, 25 СДЯ); no duplicates; none of the 579
      existing locations moved. Geocoding: 233 of 285 new locations have coordinates (8 wrong-district
      matches cleared). /about states the coverage without exact counts (PR #112).
- [x] **UF36 `/schools` is 2.1 MB uncompressed** (~200 KB gzipped); add `GZipMiddleware`
      or proxy compression, later a slimmer list schema.
      **Done 2026-09-24:** PR #102: `GZipMiddleware` (≥1 KB); the all-ages list goes from 2.1 MB to ~190 KB.
- [x] **UF37 Raw register formatting:** ALL-CAPS names, "ЕООД" suffixes, stray quotes,
      machine-transliterated addresses in the EN UI. A display-name / address cleanup.
      **Done 2026-09-24:** PR #107: display-only cleanup in `app/utils/display_names.py` (recased ALL-CAPS, legal-form suffixes and stray quotes removed, BG „…“ quotes, clean addresses without "гр. София" / "1799 СТОЛИЧНА"); stored data unchanged.
- [x] **UF38 Nominatim district check.** 8 kindergarten points were cleared because Nominatim
      matched a same-named street in another district (e.g. ул. Вършец in Подуяне placed in
      Войнеговци); a future geocoding run will return them unless the provider checks the
      district/neighbourhood against the address.
      **Done 2026-09-25 (with UF41):** Sofia results must lie in the location's known district
      (or the address's район) and name the address's кв./ж.к. (an exact house number in the
      right district may sit in a neighbouring quarter); otherwise `area_mismatch`. 6 of the 8
      now have correct pins (e.g. ул. Вършец → Сухата река, Подуяне).
- [ ] **UF39 kg.sofia duplicate address spellings.** Location 2916 (school 104, same building,
      different spelling) was removed but will be re-added on the next kg.sofia run; normalise
      addresses before comparing. Some "- сграда N" buildings are still separate school rows.
- [x] **UF40 Geocoding contact address** (`GEOCODING_CONTACT_EMAIL`) is Mike's personal email and
      is sent with every Nominatim request; switch it to `contact@schooldecider.com` (live
      2026-09-30; with UF27).
      **Done 2026-09-30:** local `backend/.env` set to `contact@schooldecider.com` (no code
      change; production sets it when the API is deployed in P3.2).
- [x] **UF41 108 Sofia locations have no pin ("No results found").** Many are a
      neighbourhood plus a house number OSM lacks (Maple Bear kindergarten 556: кв. Витоша,
      ул. "Йордан Стубел" № 16). Dropping the neighbourhood is wrong: `Йордан Стубел 16,
      София` matches Бакалов-Стубел 16 in Триадица. Add a street-only fallback that keeps the
      neighbourhood/district, check every result against them (with UF38), and strip
      floor/apartment/parenthetical notes. Re-run the failed locations.
      **Done 2026-09-25:** street-only fallback (only with a neighbourhood or district to check
      against), UF38 check, address cleanup. Forced re-geocode of the 108 + 8 UF38 locations +
      location 150 (dry runs first, pg_dump backup): 59 new pins (55 approximate), incl. Maple
      Bear kindergarten 1102; location 150 (бул. Цар Борис III 41, Красно село) moved from a
      same-named street in Казичене. Sofia locations without a pin 140 → 81. Location 3119
      (ДГ №149, second building) was cleared again: the GeoJSON name fallback returned the main
      building's point. Known recall loss: a street-level match in a neighbouring quarter
      without a known district stays unpinned (кв. Бояна → м. Гърдова глава).
- [x] **UF43 Official municipal points for state schools and kindergartens.** Sofia
      Municipality publishes a point, address and район for each of its schools, kindergartens
      and nurseries (`arcgis.sofia.bg/arcgis/rest/services/School_AllPublic`).
      **Done 2026-09-25:** `scripts/import_sofia_municipal_points.py` (dry-run report first;
      match needs type + number or name words, and agreeing addresses). Applied with pg_dump
      backups: 492 of 560 state locations now use the official point (7 had been >5 km off on
      same-named streets, e.g. 152 ОУ in Илинден instead of Мърчаево); state locations without
      a pin 43 → 8; 191 districts filled. `--fill-districts` set the район of 59 more exact
      pins (incl. private schools) from the municipal boundaries. Tagged
      `coords_source=sofia_municipal`; forced re-geocodes and website map links keep them.
      The 68 unmatched rows (second buildings not in the layer, non-municipal state schools)
      are in the report for review.
- [x] **UF42 Related institutions (kindergarten → school) are separate and sometimes
      mis-linked.** Parents weighing a kindergarten want to know the school it leads to.
      Example: Maple Bear is kindergarten 556 (reg. 2200052, кв. Витоша) and school 596
      (reg. 2200031). The school has two campuses (Boyana, 38 Panoramen Pat, Preschool–Grade
      4; Kambanite, 9 Vitoshki Kambani, Preschool–Grade 7); we only have Boyana, and list
      grades 1–7 with no preschool. 556's website is the school's site
      (`sofia-school.maplebear.bg`; maplebear.bg links the kindergarten to
      `sofia-vitosha.maplebear.bg`), so its scraped pages describe the school; 596 points at
      the national `maplebear.bg`. 25 website domains are shared by 2+ institutions.
      **Approach (Mike, 2026-09-25): fix the process, not the records; nothing Sofia-specific
      (the aim is country-wide and beyond).**
      (a) *Shared-site check after website discovery:* group institutions by registrable
      domain; for a group of 2+, accept a site for an institution only with evidence tying it
      to that institution (site contact address ≈ registry address, education level fits;
      for a brand hub like maplebear.bg that lists campus sites, follow the link whose
      address/city matches). Otherwise withhold the website and its extracted data. The
      identity pilot showed page text alone cannot separate a kindergarten from its sibling
      school on one domain: the education-level guard is the defence.
      (b) *Campuses and age groups from the verified site:* several addresses on the
      contact page become extra locations; stated levels (Grade 0) become age groups.
      Extends the existing primary-address sync in `extractor.py`.
      (c) *Relationship computed, not curated:* within a verified group, same brand,
      kindergarten → school gives `continues_to`; published through the allowlist as one line
      on the detail page (publish boundary: Mike approved the idea, 2026-09-25).
      Start from `scripts/derive_school_groups.py` (read-only group derivation, already
      handles "Maple Bear" vs "Канадско мече") and `_registrable_domain` in
      `app/services/identity_adjudication.py`. Re-run discovery/extraction for affected
      groups with dry runs and backups. No merge: registry IDs, addresses and exam results
      stay separate.
      **(a) done 2026-09-25:** `app/scrapers/shared_site_check.py` runs after website
      discovery, failed-URL recovery and `repair-websites`, and on its own as
      `cli shared-site-check [--dry-run] [--report]`. It keeps a shared site only when
      the site states the registry address, describes the level, and its subdomain/path
      doesn't name the other level. A brand hub is replaced by the one campus subdomain
      (named after the city or a level) that states the address and level. Otherwise the
      site is withheld through the URL-validation state. The site's copied contact
      address and map-link pin are cleared using the validator's strong-path helper; an
      address confirmed by an official point is kept. Applied from the dry run taken after
      UF44 (backup `~/backups/sofia_schools_pre_uf42a_20260925_1525.dump`). 27 shared
      domains: `sites.google.com` is 4 separate sites; the other 26 groups (56
      institutions) → 38 kept, 2 replaced (Maple Bear 556 → `sofia-kindergarten.maplebear.bg`,
      596 → `sofia-school.maplebear.bg`, both `pending` until re-validated), 16 withheld
      (framar directory 121/123/146, softuni 150, uwekind 153/634/635, weda 372/534,
      pberon 537/546, montessori 589, state kindergarten buildings 104/105/108/134).
      Schools with a website 440 → 424; publishable website data 347 → 329. Cleared
      locations: 1079 (534, address only; the GeoJSON pin stays), 1082 (537) and 1091 (546)
      (address and map-link pin; no registry address is stored, so these two have no pin
      now). Known recall losses / follow-ups: 3-letter street names ("Ела") can't
      be matched; campuses on a path of the same domain (`novigradini.com/pbk/`) are not
      followed; stale registry addresses withhold real sites (uwekind moved); withheld
      schools are `failed_validate`, so discovery may find and withhold them again each
      run; 537/546/534 need their registry address recovered.
      **(b) done 2026-09-25/28:** `app/scrapers/campus_sync.py`. Extraction stores campus
      candidates (contact-page street addresses with the city stated; not office/partner,
      other-level or another institution's address; same street with a different number
      is skipped as ambiguous) and each campus's stated level range, only in internal
      `attributes.website_campuses`. Validation creates the locations and age groups
      (mapped through `education_config`) in the transaction that clears the withholding
      marker, then geocodes them through the normal service after the commit. Everything
      is tagged website-derived and deleted by every withhold path (URL invalid/ambiguous,
      shared-site check, strong clear, validation failure); registry rows are never removed.
      Applied through the normal pipeline, one run per school, with
      `EXTRACTION_LLM_TIMEOUT_SECONDS=90` set for those runs only. **596** applied: new Kambanite
      location (ул. Витошки Камбани 9: preschool, 1-4, 5-7; the site says "Preschool
      through Grade 7"; Nominatim pin), preschool added at Boyana (location 1149).
      **556** skipped: nothing to add; its re-extraction was rejected. **525** restored: its
      Каравелов 54 campus is unapplied because re-extracting its prices degraded them.
      **630** excluded: its campus (Хумболт 7) is unapplied. Backups:
      `~/backups/sofia_schools_pre_uf42b_20260925_1613.dump` (first four-school run: an LLM
      timeout made the cost guard refuse every later call, the run fell back to deterministic
      output, so the whole DB was restored; bad state in `..._post_uf42b_failed_20260925_1618.dump`),
      `..._pre_uf42b_rerun_20260925_1622.dump` (same failure, restored),
      `..._pre_uf42b_596_20260925_1627.dump` (restored after the rejected 556 run; that state is in
      `..._pre_restore_uf42b_20260928_0921.dump`), `..._pre_uf42b_596_rerun_20260928.dump`,
      `..._pre_uf42b_596_retry_20260928.dump`, `..._pre_uf42b_525_20260928.dump` (restored;
      pre-restore state in `..._pre_restore525_20260928_0934.dump`).
      Recall losses: no school-wide level ranges (a range counts only in a sentence naming
      the campus); English-only addresses ("16 Jordan Stubel Street") are not parsed.
      Follow-ups: 525 price re-extraction loses periods and registration rows, and files a
      deposit as tuition; 556/596 extraction quality (junk `facilities: ["facilities"]`,
      English listed twice in `language_focus`, the kindergarten loses its distinct name
      and becomes "Maple Bear Sofia"); 630's waldorf.bg prices mix kindergarten and
      school fees; extraction falls back to deterministic output and still promotes when
      LLM calls time out or are refused (decision pending with Mike).
      **Follow-up done 2026-09-28 (#124):** a timed-out extraction call is retried twice
      with a 90 s timeout, and the timeout covers only the provider call, not the wait for the dispatch lock. An
      uncertain call counts at the $0.10 request reserve against the run cap instead of
      refusing the rest of the run. If an extraction call still fails or is refused, the
      school makes no further calls, is rolled back, and the fallback is not promoted.
      Previously published data from the same host keeps its publishable status; anything
      else stays withheld. Re-runs on the launch DB, one school per run, no timeouts or
      uncertain calls: **556** applied (first published prices: €9,200 tuition, meals
      €181.44/month, materials €400/€550, all from the kindergarten site; the name came back
      as 596's "Maple Bear Sofia" and was set to the site's "Maple Bear Kindergarten Sofia"
      by hand). **525** applied (current €530/€350 fees replace stale €560/€350; the
      prepay rates are now installments, not registration rows; Каравелов 54 campus added;
      by hand: monthly on €530 and €20, the €265 deposit refiled as one-time
      registration, the €25 yoga row refiled from tuition to extracurricular). **630**
      applied (Хумболт 7 is the school's address per the contacts page, pinned; €6,150 and
      €6,640 tuition, €800 deposit; by hand: deleted the kindergarten €6,200 tuition, an
      invented €6,200 "catering" tuition row and the €135 weekly guest fee filed as
      registration; the €800 deposit set to one-time). Its registry location 1187 (Горски
      пътник 44) is the kindergarten's building (514) and still lists grades 1–12.
      Backups: `~/backups/sofia_schools_pre_rerun_{556_20260928_1130,525_20260928_1450,630_20260928_1520}.dump`
      (the time in the name is wrong: written 14:43, 14:46, 16:38). Still open:
      extraction quality (UF45); `language_focus` duplicates did not recur.
      **(c) done 2026-09-28:** `app/services/school_relations.py` computes `continues_to` on
      request for the detail endpoint only (`SchoolDetailResponse`; nothing stored, no
      migration); the detail page shows one line linking to the school. Evidence rule: same
      site group (`site_group_key`), each current website has a shared-site check keep or
      replace decision recorded for that same site (a keep is now recorded too; a URL that
      changed site after the decision has no evidence) and is not withheld by URL
      validation, registry names share a brand (`brand`/`shared_brand_key`, moved
      out of `scripts/derive_school_groups.py`; at least 4 characters), kindergarten-level →
      school-level, same city, exactly one candidate school, and the school is itself
      listed. It needs a site that passed the checks, not publishable extracted data: the
      link uses only the URL, registry name/level/city and the target's published name.
      Published: only the target's id, resolved name, school type and level. Launch DB
      (report `backend/reports/uf42c/`): after an applied `cli shared-site-check` run
      (2026-09-28, 17 groups / 36 institutions, all kept, identical to the dry run; backup
      `~/backups/sofia_schools_pre_uf42c_keep_20260928_1030.dump`) 6 links publish:
      556→596 Maple Bear, 515→575 Британика, 587→522 Дружба, 591→592 Никатор, 510→214
      Светлина, 514→630 Waldorf (checked through `GET /schools/{id}`). 9
      near-misses have a withheld kindergarten site (560, 589, 546, 555, 528, 634, 372);
      verifying it would link 589→610, 546→537, 555→594, 634→635 and 372→534, the rest
      have no shared brand. The school side shows no inverse link.
- [x] **UF44 Remaining pin gaps (after UF41/UF43).** 38 Sofia private locations have no pin:
      17 are blocked by a terminal `duplicate_geojson_name_match_different_address` and were
      never tried with the UF41 street/district logic (many ordinary addresses, e.g. ж.к.
      Редута); several sit in a state school's or university's building ("… на ПГЕХ", УАСГ,
      ВТУ) that now has an official point. Retry through Nominatim with dry runs; reuse the
      host building's official point when the address matches. Also guard the GeoJSON
      name-match fallback for schools with several locations (it gave ДГ №149's second
      building the main building's point, UF41); no same-school pins currently share a point
      at different addresses. 8 state locations still have no pin: fix individually with
      evidence (report `backend/reports/municipal-points/`).
      **Done 2026-09-25:** a GeoJSON name match that loses its point is no longer terminal and
      falls through to Nominatim (UF41/UF38 checks) in the same run; for a school with several
      locations a name match needs the register address to be this location's and no sibling
      within 50 m (ДГ №149 / 3119 regression test); a location at an official building's
      street and house number reuses that point (`official_point_same_address`), unless the
      register gives the institution an address none of its locations has (then unchanged:
      1174, 1080); Nominatim pins are exact only for the house number asked for;
      `geocode_locations.py --check-shared-points` (0 pairs after apply). Applied
      `scripts/geocode_pin_gaps_uf44.py` (dry run `backend/reports/uf44/20260925T104215Z/`,
      backup `~/backups/sofia_schools_pre_uf44_20260925_1342.dump`): 42 locations changed.
      Sofia locations without a pin: private 38 → 25, state 8 → 4. 8 wrong name-match pins
      moved to their own address and 2 cleared (1072, 1095); 11 host-building tenants on the
      official point (e.g. 816 in ПГЕХ, 815, 679); 4 state locations from the municipal
      layer with kg.sofia/website evidence (762, 2974, 2979, 3029). Withheld: 1147 (only the
      ж.к. Дружба centroid); state 835 (МО kindergarten), 3070 (ДГ №122), 2922 (СДЯ №37) and
      3120 (ДГ №150): not in the municipal layer, and OSM has none or a point on another
      street; 22 private locations with no or out-of-area Nominatim result (incl. the УАСГ and
      ВТУ tenants 1078, 1117, 836, which have no official point).
      **Correction (same PR, after Codex P1):** a Nominatim pin is exact only for the address's
      own № / street house number, letter included (not a block, "Младост 4" or a postcode);
      a neighbourhood/settlement centre (suburb, quarter, residential, village…) is not a pin;
      a school/kindergarten amenity with a different number ("ДГ №130" for ДГ №128) is
      rejected. Every stored exact Nominatim pin was re-checked through the pipeline, plus the
      3 stored area-centre pins (37 locations; dry run `backend/reports/uf44/20260925T124813Z/`,
      backup `~/backups/sofia_schools_pre_uf44b_20260925_1548.dump`): 17 relabelled
      approximate, 12 moved, 8 withheld (1107/1108/1123 area mismatch; 783, 1137 area centre;
      3035, 3036, 3109 no street-level result). 3086 is now an approximate street pin, no longer
      the other kindergarten's point. Sofia locations without a pin: private 27 → 32 (the
      baseline includes UF42a's cleared 1082/1091), state 4 → 7. Follow-up: 3024, 3112, 3115 keep
      pins the current pipeline does not reproduce (no result / area mismatch).
- [x] **UF45 Price-row and name sanity checks (proposed 2026-09-28, needs Mike's go).**
      Every wrong row fixed by hand in the UF42 re-runs breaks a rule that can be checked
      against the source page text, without a model:
      (1) *Amount near its label:* the amount must appear within a short window of the
      row's own label or plan name (630's "catering" row copied €6,200 from the line above).
      (2) *Level fits the school:* a label naming the other level ("детска градина",
      kindergarten, nursery on a school; grades on a kindergarten) drops the row (630's
      kindergarten tuition). Reuse the shared-site level vocabulary.
      (3) *Period from the text:* a period keyword next to the amount ("месечна",
      "ежемесечно", "/ month", "годишна") sets the period when the model left it null
      (525 €530, €20); an unrepresentable one ("седмична", "/ден") drops the row (630
      €135 weekly).
      (4) *Category words:* "депозит"/deposit is never tuition (525 €265).
      (5) *Name is not a sibling's:* a display name equal to another institution's name in
      the same site group is rejected (556 → "Maple Bear Sofia").
      (6) *Re-extraction regression guard:* when a re-extraction would replace published
      rows and loses stated periods or rows the page still shows, hold the school for
      review instead of publishing.
      Rules (1)–(5) fix or drop single rows (a publish-gate change); (6) is the backstop.
      An LLM judge is not needed for these: the Stage 6 capable-model spot-check already
      runs, but it is monitoring-only. Make it actionable for pricing only if residual
      errors remain after the rules.
      **Step 1 done 2026-09-29 (audit only, no gate or data change):**
      `scripts/audit_price_rows_uf45.py` checks every row and name `GET /schools/{id}`
      publishes against the linked page text (rules 1–5, plus 4b: a tuition row whose
      line says "I вноска"). Launch DB, 77 published rows / 22 schools
      (`backend/reports/uf45/20260929T081716Z/`, with `hand_check.md`): rule 1: 2 hits
      (1 true), rules 2–4: 0, 4b: 3 (all true), rule 5: 6 pairs (1 wrong name, 515; 5 pairs
      are sibling institutions that both show the bare brand). Wrong live rows listed, not fixed: 564
      installments as tuition (2950/2952/2954), 329/2915 label, 531/2946 and 530/2944
      miscategorized as food, 606/2962 programme fee as 9th-grade tuition, 171 invented
      TERM/SEMESTER periods, 515's name.
      **Step 2 (2026-09-29, #127):** Stage 6 validation runs rules 1–4 and 4b from
      `app/scrapers/price_evidence.py` on every scraped row with a valid linked page:
      findings become errors on `pricing[{id}]` (the existing display gate withholds
      them), a stated null period is an auto-fix, a period conflict is a warning. Rule 5
      is narrowed to a kindergarten whose display name is a same-site school's: the
      kindergarten falls back to its registry name. The dry run showed that falling back
      on both sides would take 575's correct "BRITANICA Park School" and give 151/392 the
      same "Проф. д-р Васил Златарски". Rule 6: `_extract_prices` keeps the published rows
      and records `attributes.pricing_hold` when the replacement drops a fee the page
      still shows or loses a period the page does not restate. The scoreboard now also
      counts validation-blocked rows. Dry run (re-validation of 122 schools in a
      rolled-back transaction): withholds 329/2915, 531/2946, 564/2950/2952/2954; 515
      shows "Британика"; nothing else changes. Apply after merge:
      `cli run --stage validate-data --school-id N` for 329, 531, 564, 515 (backup first).
      Not caught by any rule (from the step 1 list): 530/2944, 606/2962, 171's periods, 564/2949.
      Codex review fixes (same PR): rule 1 checks the currency written next to the amount
      and both ends of a range; rule 6 counts rows per amount; the period fill runs before
      the duplicate pass. **Applied 2026-09-29** (deterministic validation only, no
      spot-check calls; backup `~/backups/sofia_schools_pre_uf45_apply_20260929_1236.dump`)
      to 329, 531, 564, 515: published rows 77 → 72, as in the dry run; 329 now publishes
      no price (its €6,600 tuition was never extracted); 515 shows "Британика". Post-apply
      audit `backend/reports/uf45/20260929T093812Z/`: no price-rule hits; 5 name pairs
      remain by design (two schools of one brand, or bare-brand registry names).
      **Hand fixes 2026-09-29** for the rows no rule catches (page evidence; backup
      `~/backups/sofia_schools_pre_uf45_rowfixes_20260929_1454.dump`): 564/2949 one-time →
      yearly ("Годишна такса обучение"); 171/2896, 2897, 2899, 2900 term/semester → yearly
      ("за учебната 2026 - 2027 г."), and 2897/2899/2900's "installments" plan names
      corrected to one-payment wording with the grade band; 530/2944 food → tuition,
      period cleared, "Полудневен престой с обяд"; 606/2962 deleted (the 9th-grade
      "допълнителни програми метаумения" fee filed as tuition; 9th-grade tuition is 2961).
      Published rows 72 → 71; audit clean.
      **329 re-extracted 2026-09-29** (one school, $0.50 cap, 90 s timeout; backup
      `~/backups/sofia_schools_pre_rerun_329_20260929_1458.dump`): clean run, €70
      registration now without the wrong label, €123 theatre course as extracurricular
      (was legacy tuition), but the model again missed the €6,600 tuition. Added by hand
      from the page ("Такси учебна 2026-2027 г. / СТАНДАРТНА ТАКСА / EUR 6600", 6900 in 2 and
      7200 in 4 installments) as row 2999, yearly; backup
      `..._pre_329_tuition_20260929_1459.dump`; deterministic validation ok. Rule 6 now holds
      a re-extraction that drops it.

## Phase 3 — Go live

- [x] **P3.1 Prod config hardening.** Flip `debug` default to `False` in
      `app/config.py`; delete unused `secret_key`; make geocoding raise if
      `nominatim contact email` is still `your-email@example.com`; CORS origins from env.
      **Done 2026-09-23:** `debug` defaults to `False`; `secret_key` removed (never read);
      CORS already came from `ALLOWED_ORIGINS`. The placeholder-email check now lives in
      `nominatim_user_agent()` and also guards the two `cli.py` repair commands that
      called Nominatim directly; the MoE registry adapter keeps its deliberate skip.
- [ ] **P3.2 Deployment and autonomous operation (revised 2026-09-30).** Replaces the
      earlier "single small VPS" item. Work through stages A–E in order; each stage is a
      set of agent-sized PRs. Stage A alone meets the "live" definition above.

      **Goal.** Hands-off operation, built as a showcase: (1) school data refreshes on a
      schedule with an LLM verifier in place of human review; (2) features go from a
      prompt to production through agent build, test, review and UI checks, with no
      human checks. Mike is looped in only for the big changes listed in
      `skills/github-process/SKILL.md` ("Big changes") and when a circuit breaker trips.
      No automation may depend on a human approving routine output. What makes this safe:
      automated gates, circuit breakers, rollback and alerting.

      **Decisions (2026-09-30).** A Codex launch/hosting report was reviewed against the repo:
      - The public app is read-only; data changes only through the pipeline. The school
        data is therefore a *published snapshot*, separate from future user data
        (sign-in, comments), which is the only data needing real production backups.
      - Hybrid hosting: Cloudflare Pages serves the frontend and prerendered school pages;
        one OVHcloud VPS runs FastAPI, Postgres and Caddy in Docker (OVH instead of
        Hetzner since 2026-10-01: Hetzner's cheap tier is unorderable and the next is
        about EUR 12/month; OVH VPS-1 is EUR 4.49 ex VAT, monthly). A fully static build
        (no API) was considered and rejected because writes and a scheduled pipeline are
        planned, and it would mean porting the server-side filters to JS.
      - Not OCI Always Free for production: idle always-on instances can be reclaimed, A1
        capacity is not guaranteed, and it means running on Arm64. OCI stays an option
        for the stateless pipeline worker (stage D) if multi-cloud practice is wanted.
        Reconsidered on 2026-10-01 when the host moved to OVH, and passed over again.
      - Out of scope until a real need appears: Kubernetes, a separate staging VM,
        self-hosted analytics, dashboards-as-code.

      **Stage A — Launch.**
      - [x] Buy the domain (human); Cloudflare account and zone. Unblocks UF27 and UF40.
            **Done 2026-09-30:** `schooldecider.com` bought at Cloudflare Registrar (zone on
            Cloudflare; account has a password and 2FA). Email Routing forwards
            `contact@schooldecider.com` to Mike's inbox (catch-all off). `schooldecider.bg`
            is the planned primary site (Bulgarian at `/`, English under `/en/`), with the
            .com redirecting to it; the .bg is still to be registered via SuperHosting.BG.
      - [x] Language URLs: Bulgarian at `/`, English under `/en/`; reciprocal `hreflang`.
            **Done 2026-10-01:** the URL is the only source of the language
            (`frontend/src/utils/languageUrl.js`): the router runs under a `/en` basename
            for English, the toggle is a plain link to the same page under the other
            prefix, and browser detection and the saved preference are gone (no automatic
            redirect). `html lang` and the `bg`/`en`/`x-default` alternates are set at
            runtime from `VITE_SITE_ORIGIN`; the prerender item moves them into the
            static HTML, adds canonical URLs, and must route `/en/*` explicitly once a
            top-level 404 page ends the host's SPA fallback.
      - [x] Prerender school pages at build time with a per-page title, meta description,
            canonical URL and Open Graph tags; `sitemap.xml`, `robots.txt`, real 404s.
            Descriptive slugs are welcome but not a launch blocker; keep numeric IDs
            stable. State-school pages lead with official NVO results. Pages whose
            content is thin get `noindex` rather than being padded.
            **Done 2026-10-01:** `frontend/scripts/prerender.js` stamps the built
            `index.html` per URL and language (no SSR), reading schools from the public
            API (`PRERENDER_API_URL`). A school page is indexable with NVO results, a
            published price, or a Bulgarian summary of 300+ characters; otherwise
            `noindex, follow` and left out of the sitemap (launch DB: 261 indexable, 424
            thin, no summaries published yet; the 77 published schools outside Sofia keep
            a working page but are `noindex`). `/` and `/en/` 301 to the results page
            (`public/_redirects`, query string kept); unknown URLs get a real 404 in the
            right language. `robots.txt` carries `search=yes, ai-input=yes, ai-train=no,
            use=reference`. Left for later items: CD must run `npm run build:production`
            (fails without the API URL, an https origin, or schools) and rebuild after
            each data publish; Cloudflare's managed robots.txt adds per-crawler training
            opt-outs and "block AI bots" stays off (Terraform); no `og:image` yet.
      - [x] Dockerfile for the API, a production compose file with Caddy (HTTPS), a
            readiness endpoint that checks the DB, container log rotation.
            **Done 2026-10-01:** `backend/Dockerfile` (uv from the lockfile, non-root),
            `docker-compose.prod.yml` (Caddy → API → Postgres; only Caddy publishes
            ports; no Redis/Celery) and the runbook `deploy/README.md`. The browser calls
            the API on its own host, `https://api.<site>` (`VITE_API_BASE`, CORS via
            `ALLOWED_ORIGINS`); the Pages build reads the same URL (`PRERENDER_API_URL`).
            Caddy serves a Cloudflare Origin CA certificate behind the proxied record
            (Full strict); hostname and certificate come from `deploy/.env`. `/ready`
            returns 503 unless the DB answers and `schools` has rows; `/health` stays
            dependency-free. Migrations are an explicit step after the snapshot restore,
            never on API start. Logs: `json-file`, 10 MB × 5 per container. Verified on a
            local run only; nothing is live. Left for later items: the Origin CA
            certificate, limiting 443 to Cloudflare IPs and passing the real client IP
            through Caddy (Terraform); the image is 1.7 GB because it carries the
            scraping dependencies (playwright, scipy, litellm), so move those to a
            dependency group when image pulls start to matter.
      - [x] Terraform: the Cloudflare DNS records, TLS mode, Origin CA certificate, the
            Pages project, and crawler settings; the server and its firewall by script.
            **Done 2026-10-02** (config merged 2026-10-01; steps in `infra/README.md`). Terraform 1.16 with state in
            HCP Terraform (local execution) manages Cloudflare only (`infra/`). The OVH
            VPS is ordered by hand: the OVH provider can order one, but a changed image
            reinstalls it and the SSH key needs an image ID that exists only after the
            order. `deploy/bootstrap.sh` sets the host up (Docker, key-only SSH,
            unattended upgrades) and `deploy/firewall.sh`, reapplied by a systemd unit at
            boot, limits 443 to Cloudflare's IPv4 ranges and closes 80; OVH's edge
            firewall was rejected (IPv4 only, 20 rules, blind to traffic from inside
            OVH). Caddy's ports bind IPv4 only. Caddy takes the client address from
            `CF-Connecting-IP` on connections from Cloudflare and passes it to the API.
            The Origin CA key is made locally and only its signing request reaches
            Terraform. Pages is a Direct Upload project (CD builds and uploads; it cannot
            later become Git-connected). Crawlers: training is `disallow` (robots.txt
            only) through Bot Preference Sync, which replaced managed robots.txt in
            September 2026; nothing is blocked at the edge. The provider cannot yet set
            the sync switch itself (cloudflare/terraform-provider-cloudflare#7385), so it
            is checked in the dashboard. The site launches on `schooldecider.com`; the
            switch to `.bg` is one variable plus the list in `infra/README.md`.
            **2026-10-01:** VPS-1 ordered (Warsaw, Ubuntu 26.04) and `bootstrap.sh` run
            on it: Docker 29.8, firewall service active (443 limited to 15 Cloudflare
            ranges, 80 closed).
            **2026-10-02:** applied to Cloudflare, 10 resources, and a second plan
            reports no changes. `www` redirects to the apex; Email Routing's MX records
            are intact; the origin does not answer on 443 from outside Cloudflare. The
            apex and `api.` answer 522/521 until the frontend and the API stack are
            deployed (next: the certificate onto the host and `deploy/README.md`, then
            the CD item). Bot Preference Sync turned on in the dashboard by Mike.
            **API live 2026-10-02:** code sent to the host as `git archive main`
            (65a91de; the repo is private, so no GitHub credentials on the host), image
            built there, launch-DB snapshot restored (785 schools), all three
            `PUBLISH_*` flags false. `https://api.schooldecider.com/ready` returns
            ready; CORS allows only `https://schooldecider.com`; ports 80 and 443 do
            not answer from outside Cloudflare; Caddy logs the visitor's address as
            `client_ip`. The apex stays 522 until CD uploads the frontend.
      - [x] GitHub Actions CD: build images to GHCR, deploy the API to the VPS and the
            frontend to Pages on merge; post-deploy smoke tests; automatic rollback to
            the previous image tag if they fail.
            **Done 2026-10-02:** `.github/workflows/deploy.yml` tests every push to `main` and,
            unless only docs, skills or Terraform changed, releases. The API image is
            built on the runner and pushed to GHCR; a deploy SSH key that can run only
            `deploy/deploy.sh` on the host pulls it with the job's token, migrates,
            restarts the API and falls back to the previous image if `/ready` fails.
            The site is built with `build:production` and uploaded with wrangler.
            `deploy/smoke.sh` checks both; a failed site step rolls back Pages and the
            API this run deployed (one release). Rollback is image-only, so migrations
            must stay usable by the previous image (`backend/AGENTS.md`); stage B's
            backup-before-migrate is still open. `frontend_only` rebuilds the site
            after a data publish. Left open: the secrets are repository secrets
            (a `production` environment limited to `main` needs GitHub Pro for a
            private repo); the 1.7 GB image is unchanged (dependency split is a
            separate PR). Human setup: `deploy/README.md`, Continuous deployment.
            **First releases 2026-10-02:** the first run (#142) deployed the API and
            uploaded the site, then failed the site smoke test on a single 522 seconds
            after the first Pages deployment went live, and rolled the API back as
            designed. #143 made the site checks retry and fixed a SIGPIPE in
            `smoke.sh`; its release (8c8cc36) was green end to end.
            `https://schooldecider.com` is live and the API runs the GHCR image. The
            hand-built `schooldecider-api:local` image is still on the host.
      - [x] CARTO key restricted to the domain, with visible attribution
            (`VITE_CARTO_API_KEY` is needed in the frontend build). Official NVO source
            credited on the About page.
            **Done 2026-10-06:** Mike restricted the key to `schooldecider.com` in CARTO.
            Checked: a tile request with that referrer returns the map, any other
            referrer (including `localhost`) gets 403, and the live map loads. Local
            development therefore runs without the key (watermarked tiles).
      - [x] External uptime check (free tier) that alerts Mike; Cloudflare Web Analytics
            (cookieless, no custom events yet); a short privacy notice.
            **2026-10-06:** the `Uptime` workflow runs `deploy/smoke.sh` against
            production every 15 minutes and GitHub emails the owner on failure; the About
            page has a Privacy section, linked from the footer. Web Analytics is on for
            `schooldecider.com` (Mike, dashboard, automatic injection): a browser visit
            loads the beacon and posts to `/cdn-cgi/rum`. It is not declared in Terraform.
      - [x] Google Search Console and Bing Webmaster Tools: verify, submit the sitemap,
            inspect representative pages.
            **Done 2026-10-06 (Mike):** Domain property verified through Google's
            Cloudflare integration, sitemap submitted, live URL test passed and indexing
            requested; Bing imported from Search Console.
      - [ ] CD follow-ups (from the CD item, 2026-10-05):
            - [ ] Slim the API image (1.7 GB): move the scraping dependencies out of the
                  runtime set. Not a pure dependency move, because API-path modules
                  (`app/services/school_relations.py`, `app/utils/transliteration.py`, ...)
                  import scraper code or its dependencies; untangle and test first.
            - [ ] After the next API release, remove the hand-built
                  `schooldecider-api:local` image from the host (Mike; the agent has no
                  host shell): `docker image rm schooldecider-api:local`.
            - [x] Deploy secrets are exposed to every branch. **Done 2026-10-06:** the
                  repository is public. `DEPLOY_SSH_KEY`, `DEPLOY_HOST` and
                  `CLOUDFLARE_PAGES_TOKEN` live only in the `production` environment,
                  which is limited to `main` (#156); no repository-level secrets remain.
                  `main` is protected: pull requests, the Backend and Frontend checks,
                  no force push or deletion, admins included. Workflows from outside
                  contributors' forks need approval; secret scanning and push protection
                  are on.

      **Stage B — Agent delivery loop.**
      - [ ] Protect the checks from the agents they check: CODEOWNERS plus branch protection
            on `.github/workflows/`, gate and threshold configs, `AGENTS.md` and `skills/`.
            Changes there are big changes.
            Note (2026-10-05): branch protection and rulesets are not available for a
            private repo on GitHub Free; see the CD follow-up on deploy secrets.
      - [ ] Staging stack on the same VPS (a second compose project with its own DB loaded
            from the published snapshot); PR preview deploys point at it.
      - [ ] Playwright journeys for the core user stories (search and filter, school
            detail, compare, language switch), run in CI against staging.
      - [ ] Reviewer agent on a different model family from the coding agent, run in CI on
            every PR.
      - [ ] UI agent: walks the PR's acceptance criteria on the preview deploy and attaches
            screenshots and a pass/fail result to the PR.
      - [ ] Eyes before autonomy: structured JSON logs with request IDs, frontend error
            tracking (e.g. Sentry free tier) and alerts on 5xx rate and failed deploys.
            These replace a human watching, so they land before auto-merge.
      - [ ] Auto-merge on green CI, reviewer pass and UI pass, unless the PR touches a big
            change, which instead pings Mike with the PR URL.
      - [ ] Migrations: backup before migrate; expand-then-contract only, so rolling back
            code never needs a schema rollback.
      - [ ] Agents deploy only through CI; they get read-only access to logs and metrics
            and no production credentials.
            Note (2026-09-30): during manual setup, agents have broad Cloudflare account
            access through the Cloudflare plugin's OAuth connection. Replace it with scoped
            API tokens (per task, least privilege) in this stage.

      **Stage C — Data releases and backups.**
      - [ ] Versioned published snapshots stored in Cloudflare R2 together with the inputs
            needed to rebuild them. Production loads a named version; rollback loads the
            previous one. Loading must never touch user tables.
            **Partly done 2026-10-07:** production loads a named dump from the backups
            bucket (`gh workflow run deploy.yml -f snapshot=<name>`, `deploy/deploy.sh
            publish`), with a backup and automatic restore on failure; rollback is the same
            command with the previous dump's name. Open: storing the rebuild inputs with
            each version, and keeping user tables out of the load once they exist.
      - [ ] Move the internal (pipeline) DB to the VPS as a separate database from the
            public one; local development works from a copy.
      - [ ] Nightly `pg_dump` of the internal DB (and later user data) to R2 or B2, plus a
            scheduled CI job that restores the latest dump and checks row counts.
      - [ ] Automate the official NVO import: poll for the annual release and auto-release
            when the boundary audit passes.

      **Stage D — Autonomous data refresh.** Cadence: weekly change detection, monthly
      full refresh; NVO annually; kindergarten thresholds per admission round.
      - [ ] Temporary pipeline worker: a scheduled workflow creates a larger server with
            Terraform, runs the pipeline against the internal DB, uploads the candidate
            snapshot and its diff to R2, then destroys the server.
      - [ ] Change detection: fingerprint each school's pages and re-extract only the
            changed ones.
      - [ ] Reference set: the reviewed 18-school cohort with hand-verified values. Every
            run must reproduce them before anything releases.
      - [ ] Verifier agent: a stronger model from a different family than the extractor
            checks every changed field against the stored source text or PDF. A rejected
            change is withheld, and the school keeps its last verified value. Choose the
            model by scoring candidates on the reference set, for accuracy and cost per run.
      - [ ] Circuit breakers that block a release and alert Mike: changes above X% of
            schools, a coverage drop above Y, a failed boundary audit, or a reference-set
            regression.
      - [ ] Enable auto-publish one field family at a time, each only after it meets a
            recorded accuracy bar on the reference set: NVO first, then pricing, then
            website-derived fields after E1.

      **Stage E — Full observability and user features.**
      - [ ] OpenTelemetry on FastAPI and the DB; Grafana Alloy sending VPS, container and
            app telemetry to Grafana Cloud (free tier); p95 latency and resource alerts.
            Data-freshness alerts (last release, failed or overdue runs).
      - [ ] Writes, in increasing order of risk: "report a data error" (UF27); sign-in via
            a hosted provider or email magic links (no home-built passwords) with saved
            schools; questions to a school; public comments last, with LLM moderation and
            a legal (defamation, GDPR) review before launch. Every new user table is
            backed up and covered by the restore test.

- [ ] **P3.3 README as shop window.** Screenshots/GIF, 7-stage pipeline architecture
      diagram, decisions-and-trade-offs section (JSON, calendar-year age logic,
      GeoJSON-first geocoding), test count + CI badge. Move SearXNG troubleshooting
      to `docs/`.
- [x] **P3.4 ARCHITECTURE.md** on the LLM pipeline: structured extraction with
      PydanticAI, validation gates, evidence checks, spot-checking, per-field
      provenance, cost/timeout tuning. This is the portfolio differentiator.
      **2026-10-05:** architecture guide covers the pipeline, publication boundary,
      tradeoffs and limitations; see the publication checklist for operational follow-ups.
- [ ] **P3.5 Prompt-injection guard for summaries**: flag summaries containing URLs,
      phone numbers not in source, or promotional anomalies. *No longer pre-launch*
      (summaries are out of launch scope per the 2026-07-15 revision) — required before
      E2 ever re-enables summary publication.

## Phase 4 — Post-live / portfolio polish (parallelizable, low risk)

- [ ] **P4.1 Decompose `SearchPage.jsx`** (2,049 lines, ~25 useState) into custom hooks
      (`useUserLocation`, `useSchoolFilters`, `useMapSync`) + subcomponents. Then
      `ComparePage.jsx` (1,783) and `SchoolCard.jsx` (1,546) if appetite remains.
- [ ] **P4.2 ruff + mypy** for backend; fix `datetime.utcnow()` deprecations; add to CI.
      Ruff is already enforced in CI; mypy and timestamp cleanup remain open.
- [ ] **P4.3 vitest + testing-library** for frontend filter/age-group logic.
- [ ] **P4.4 Extract repair commands** from `cli.py` (3,453 lines) into
      `app/scrapers/repairs/` modules with unit tests; thin Click wrappers. Delete
      repairs made obsolete by Phase 1 gates.
- [ ] **P4.5 Consolidate LLM stacks.** Pin pydantic-ai, remove `inspect.signature`
      shims in `ai/client.py`, merge the two OpenRouter model builders (keep extractor's).
- [ ] **P4.6 Tests for `/schools/filters`** and `SchoolService.list_schools_filtered`
      attribute matching.
- [ ] **P4.7 TypeScript** for new frontend files (optional; market signal, not product).
- [ ] **P4.8 Pricing verification-date response alias.** Revisit the public `scraped_at`
      name for curated pricing via a response-schema alias; no database migration required.

## Post-launch enrichment track (unblocked by launch; architecture-first after P2.10)

Semantic-richness work moved off the release path by the 2026-07-15 launch-scope
revision. None of this may widen the public boundary without a reviewed pilot.

- [ ] **E1 Extraction re-architecture — FIRST post-launch engineering item; prerequisite
      for ALL re-extraction work.** After the already planned launch refresh, do not run
      any further extraction until this architecture passes its prototype on the reviewed
      18-school cohort. Implement two-pass, entity-scoped extraction: pass 1 maps
      each page into scoped sections carrying `entity/program`, `plan`, source `heading`,
      and `effective_year`; pass 2 extracts each section independently into a schema that
      retains `applies_to`, `payment_plan`, and `source_heading`. Add deterministic
      validation that the source heading agrees with the extracted category and that
      admission requirements are complete sentences; withhold ambiguous sections whole.
      Only after the cohort passes the recorded regressions may re-extraction resume or
      rich scraped pricing/admission data approach publication. Evidence and regression
      corpus: run `396dffe5-c905-44e2-8582-1d4c1bcdbd66` and its completed blocker packet
      (538/570/153 heading/entity/plan association; 105/153 admission classification and
      sentence completeness). The frontier's identical/equivalent failures establish that
      model substitution alone cannot fix these errors.
- [ ] **E2 Generative summaries.** Re-enable behind the P2.6 flag only after E1's
      architecture passes the cohort, deterministic input cleanup + semantic output
      validation cover the 12 documented failure modes (identity collisions, marketing
      prose, unsupported negatives, stale claims, mistranslation, raw artifacts), and a
      full regenerated cohort passes manual parent-readiness review. Bulgarian generation
      quality must be re-tested for any post-architecture model choice. P3.5
      (prompt-injection guard) applies before any summary publishes.
- [ ] **E3 Website-derived admission fields.** Re-enable behind the P2.7 flag after
      E1's entity-scoped extraction and sentence-completeness validation make fragment
      coalescing/classification reliable (103/105/153/506 cases as regressions).
- [ ] **E4 Extend pricing coverage** beyond the launch slice (20 of 175 Sofia private/
      international schools publish evidence-linked prices as of 2026-09-24). Pricing
      publishes on the evidence-link gate plus the tuition plausibility check (PR #86); no
      human curation. Blocked on E1: no re-extraction before it passes. Backlog:
      - **Step 0 (read-only, can run before E1):** survey the 18 extracted schools that
        have a valid `pricing` source page but no pricing rows; record which show a clear
        single tuition fee. That becomes E1's pricing regression set and sizes the gain.
      - Re-extract those 18, plus the 4 refreshed schools with no linked rows.
      - 7 refresh-rejected schools (154, 300, 350, 351, 541, 542, 608): decide
        re-baselining after the pipeline works.
      - 17 deferred: 12 shared-host and 2 path-tenant (574/628) need per-entity page
        scoping; 3 have no website (176, 551, 593).
      - Known gap: the plausibility check only catches implausibly *cheap* tuition. An
        overstated or wrong-programme price (e.g. 606's legacy €7,550 total) still needs
        E1's heading/plan association.
      - **Pricing recall pass 2026-10-08 (PR "pricing pipeline recall").** Traced ten
        private schools with no published price through the pipeline, then all 153 without
        one: 47 had no valid website, 66 had no fee text stored, 40 had fee text but no
        publishable rows. Causes fixed in that PR, each with a regression test:
        (1) the extractor chose its four prompt pages by category and URL only, so a fee
        page with no category never reached the model (568), and two URL spellings of
        one page took two slots; pages are now ranked by the prices they state.
        (2) The crawl is one click deep and skips PDFs; a fee-link follow-up fetches
        same-site fee pages and fee PDFs two hops out, and replaces a crawled fee page
        that lost its fee tab in the rendered DOM (404, 300, 529, 505).
        (3) Batch navigation gave schools each other's pages and websites (crawl4ai 0.9
        `arun_many` with a deep crawl returns one flat page list); found here in the
        sandbox and fixed separately in #198.
        (4) Model price rows were gated by the keyword line signals, which dropped rows
        with no fee word on the line (529), renamed tuition after an unrelated heading
        (404) and read one price per table line (300). This is the 538/570/153 failure
        family E1 describes: model rows are now checked with the `price_evidence`
        primitives instead and keep the model's category.
        (5) Price extraction runs on its own model tier (Claude Haiku 5.5): 63 of 81
        schools with fee text got a publishable tuition against 40 on the cheap tier,
        about $0.12 for all 81. The prompt names the institution's level.
        (6) `recover-failed-urls --school-id` crashed on a lazy load, and a timed-out
        URL-validation model call was terminal (505).
        `scripts/refresh_prices.py` re-reads fees only and prints the published rows
        before and after per school. Sandbox run over the 129 private/international
        schools with a website: 67 would publish a tuition (22 on launch before), no
        published tuition lost, 18 re-extractions held by rule 6.
        **Still open:** entity scoping on shared kindergarten/school sites is the main
        source of wrong rows (565, 587/522, 633, 305), then installment-plan totals
        filed as fees (558), unlabeled amounts (286, 588) and a wrong site (590); Stage 6
        rule 2 reads only `plan_name`, not `age_group`, and knows grade ranges but not
        single grades ("8 клас"). A tuition row with no period is not checked by the
        plausibility gate. Fee tables published as images are not read (555/594). 48
        schools still have no fee text after the crawl; 46 have no valid website
        (634/635 withheld by the shared-site check).
      - **Second pass 2026-10-09 (PR "price scope and fee pictures").**
        (1) *Shared sites:* a fee is scoped by the grades its label names
        (`price_evidence.label_grades`) against the grades the school teaches (its
        locations' age groups): another institution's row is dropped at extraction and
        is an error under Stage 6 rule 2, which now reads the age group as well as the
        plan name and knows single grades, roman and worded grades and stage names.
        Followed fee links are scoped by the path's level word ("/preschool-fees/").
        (2) *Fee pictures:* a fee page that states no price has its fee picture read by
        the pricing model, twice, and used only when both readings hold the same
        numbers; scanned PDFs likewise. The picture is the row's source link (555/594).
        (3) The keyword extractor no longer overrides a model answer of "no fees" (526).
        (4) URL recovery retries a candidate that failed for a passing reason before
        searching again (505 had been given a third party's page).
        (5) The pricing tier moved to GPT-6.1 Sol: read twice, it gave the same tuition
        set on 15 of 16 hard pages, Haiku 5.5 on 10. About $0.02 a school.
        **Still open:** a multi-price table row can still take a neighbour's label
        (301); a picture misread the same way twice would pass.
      - **Shared-site check, superseded registry address (2026-10-09, Mike's call: where
        the site has an updated address, use it).** A member was withheld whenever the
        site did not state its registry address. When the site states addresses and none
        is *any* member's registry address, they cannot tell the members apart: the
        registry is behind the site. Each member the site describes at its level now
        keeps it (`settle_group`, reason `site_states_no_members_registry_address`), and
        extraction gives a single-location school the address the site states for its
        level (the only one on the site, or the only one on a line naming the level);
        the old point stays until the new address geocodes. Also: the check reads the
        text of pages it invalidated itself (up to 180 days old), and one name of a
        three-word street may be an initial. Sandbox dry run over all 18 shared-site
        groups: Uwekind's three institutions (634, 635, 153) and VEDA's two (372, 534)
        are kept; 589 and 150 stay withheld because a sibling's registry address is on
        their site; no member that was kept is withheld.
        **Open:** 635 has two registered locations, so its address is not replaced;
        589's site names a second address no member claims, which may be its new one.
      - **Third pass 2026-10-09 (PR "copied fee lines").** The run-to-run differences
        between models turned out to come from how they were asked, not from reading:
        the prompt had them decide scope, year, discounts and payment plans in the same
        pass, with free-text category and period fields. The pricing call now returns
        `FeeLine` entries (label as written, amount, fixed choices for currency, period,
        kind and role) and code turns them into rows. With that, GPT-6 Luna matched
        GPT-6.1 Sol's tuition amounts at about a seventh of the cost (about $0.50 for all
        Sofia private schools). Fee pictures stay on Sol (a separate `vision` tier):
        Luna's two readings of one table disagreed on digits.
- [ ] **E5 Gymnasium admission thresholds (state schools).** Import the official minimum
      admission scores after 7th grade into `admission_info.historical_min_scores`. Scores
      come from the city-wide NVO ranking, so no address logic is needed, but they are per
      **class profile**, each with its own subject formula: store and display them per
      profile, never as one school-wide "min score". Comes before E6 (smaller, no address
      rules).
- [ ] **E6 Kindergarten points thresholds + admission points calculator.** Import
      last-admitted points per kindergarten, age group and round from kg.sofia.bg into
      `admission_info.historical_thresholds`, and ship them together with the parent-facing
      points calculator (registered-address zone, employment, siblings, etc.). A threshold
      is meaningless without the parent's own score, so neither ships alone. Calculator
      rules need tests first (see `backend/AGENTS.md` → Testing).

## Explicitly deferred (from the reviews' "don't do yet" list)

Human review queue/admin CMS; FieldSource provenance graph rework; switching geocoding
providers; multi-country generalization of gates (hardcode Sofia); rewriting
`extractor_helpers.py` before the golden corpus exists; real-time dashboards.

## Repository publication preparation (2026-10-05)

Code protections and remaining maintainer controls are tracked in
[PUBLICATION_CHECKLIST.md](PUBLICATION_CHECKLIST.md). Local service ports, demo seeding,
locked dependencies, clean-install migrations and deployment backup handling are prepared
for review. Host installation, branch protection and secret scoping remain open; visibility
and the launch database are unchanged.
