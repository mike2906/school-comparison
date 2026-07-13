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
- [ ] **P2.4 Audit and perform the one-time Sofia data refresh.** This is the point at
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
      - [ ] Repeat the same reviewed pilot cohort after the pricing and spot-check fixes.
            Require semantically correct published prices, a calibrated actionable discrepancy
            rate within threshold, complete usage accounting, clean API boundary checks, and
            no unresolved publishable claims before approving the full refresh.
      - [ ] If the pilot is clean, run the full Sofia website refresh once, then run the
            corrected force-regeocode process. NVO remains independent and must not be
            refreshed as part of `all` unless a separate NVO audit calls for it.
      - [ ] Re-run audits and the scoreboard, triage failures, and targeted-rerun only the
            affected schools. Launch acceptance: 100% validation-report coverage among
            schools with publishable website-derived data and zero such schools without a report; summaries
            published only for `ok`; 100% precision-metadata coverage for geocoded locations;
            no unexplained duplicate/out-of-bounds coordinates; no internal API keys;
            pricing gate/scoreboard parity; and a representative spot-check sample with all
            actionable discrepancies resolved or withheld.

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
- [ ] **P3.5 Prompt-injection guard for summaries** (pre-launch): flag summaries
      containing URLs, phone numbers not in source, or promotional anomalies.

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

## Explicitly deferred (from the reviews' "don't do yet" list)

Human review queue/admin CMS; FieldSource provenance graph rework; switching geocoding
providers; multi-country generalization of gates (hardcode Sofia); rewriting
`extractor_helpers.py` before the golden corpus exists; real-time dashboards.
