# Go-Live Plan — Consolidated from three AI reviews (July 2026)

Sources: full project review + data-quality architecture review (2026-07-06), recruiter
assessment (2026-07-07). This file is the single backlog. Each task is written so a
coding agent (Claude Code or Codex) can execute it in one session with no extra context.

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
            known-bad traps) it recovered 4/4 missed identities — 393, 506, 624, 635 —
            and leaked 0/10 traps, for 10 calls and $0.002.
            **The model alone is not the gate.** It accepted two traps: `Discoverer
            International School` for kindergarten 589 (quoting text absent from the
            cached pages) and `Uwekind International School` for kindergarten 634, whose
            cached pages are literally the same shared-domain pages as its sibling school
            635. Deterministic guards stopped both; for 634 the education-level guard was
            the *only* defense, so page evidence and sibling context cannot separate a
            kindergarten from its sibling school on a shared domain. Guards only ever
            reject, an accept additionally requires the candidate to appear verbatim in
            cached pages with verifiable quotes and two same-domain source URLs, and the
            outcome is a recommendation for manual promotion through the unchanged
            `identity_curation` gate — nothing publishes automatically. Four
            contact/Bulgarian-only traps (153, 512, 546, 569) never reach the model.
            No database write, publication, refresh, provider discovery call, OCR, or
            geocode attempt was performed. Evidence:
            `backend/reports/p2-14f/20260727T103912Z/acceptance.md`.
            *Open decision:* whether a named reviewer promotes the four recommended
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

## Phase 3 — Go live

- [ ] **P3.1 Prod config hardening.** Flip `debug` default to `False` in
      `app/config.py`; delete unused `secret_key`; make geocoding raise if
      `nominatim contact email` is still `your-email@example.com`; CORS origins from env.
- [ ] **P3.2 Deployment.** Single small VPS (Hetzner/Fly.io/Railway) — this scale
      (~500 schools) needs one box: dockerized FastAPI + Postgres + built frontend
      behind Caddy/nginx with HTTPS. Manual task: pick host, domain, DNS, secrets.
      Agents can write the Dockerfile/compose/Caddyfile; a human runs the deploy.
- [ ] **P3.3 README as shop window.** Screenshots/GIF, 7-stage pipeline architecture
      diagram, decisions-and-trade-offs section (JSONB, calendar-year age logic,
      GeoJSON-first geocoding), test count + CI badge. Move SearXNG troubleshooting
      to `docs/`.
- [ ] **P3.4 ARCHITECTURE.md** on the LLM pipeline: structured extraction with
      PydanticAI, validation gates, evidence checks, spot-checking, per-field
      provenance, cost/timeout tuning. This is the portfolio differentiator.
- [ ] **P3.5 Prompt-injection guard for summaries**: flag summaries containing URLs,
      phone numbers not in source, or promotional anomalies. *No longer pre-launch*
      (summaries are out of launch scope per the 2026-07-15 revision) — required before
      E2 ever re-enables summary publication.

## Phase 4 — Post-live / portfolio polish (parallelizable, low risk)

- [ ] **P4.1 Decompose `SearchPage.jsx`** (2,049 lines, ~25 useState) into custom hooks
      (`useUserLocation`, `useSchoolFilters`, `useMapSync`) + subcomponents. Then
      `ComparePage.jsx` (1,783) and `SchoolCard.jsx` (1,546) if appetite remains.
- [ ] **P4.2 ruff + mypy** for backend; fix `datetime.utcnow()` deprecations; add to CI.
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
- [ ] **E4 Finish pricing curation** beyond the launch slice: remaining private/
      international schools (178 total), using scraped rows as verification candidates.

## Explicitly deferred (from the reviews' "don't do yet" list)

Human review queue/admin CMS; FieldSource provenance graph rework; switching geocoding
providers; multi-country generalization of gates (hardcode Sofia); rewriting
`extractor_helpers.py` before the golden corpus exists; real-time dashboards.
