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
- [ ] **P1.7 Field-level display gating.** Mirror the summarizer's
      `_blocked_summary_sections` pattern in serialization: a field with an error-level
      issue in the current validation report is excluded from the API response.
      Pricing: hide rows with confidence <0.7 or no `source_url`. Summaries: drop
      `needs_review` from `SUMMARY_ELIGIBLE_VALIDATION_STATUSES` (keep `ok` only).
      *Verify:* tests: school with error-level validation issue on field X → X absent from API.
      - [ ] *From P1.6 review:* the pricing gate threshold already exists as
            `app/services/data_quality.py::PRICING_CONFIDENCE_FLOOR` (= 0.7), written to
            mirror this gate. Import/reuse it here (or move it to a shared location) rather
            than hardcoding `0.7` a second time, so the scoreboard metric and the display
            gate can never diverge.
      - [ ] *From P1.6 review (finding-1 last corner; low priority, bundle here or in any
            future PR):* in `cli._run_discover_batch`, a whole-adapter exception is caught
            per-adapter without incrementing any failure count, so a discover run whose only
            adapter crashes still records `COMPLETED` with all-zero counts. Count adapter
            exceptions as `failed` in the returned `_stage_summary` (per-school failures
            don't exist at the discover level, only whole-adapter ones).
- [ ] **P1.8 Split `location_tags`.** Provenance strings (`source=moe_registry`,
      `source_esri_id=…`) move to `geocode_meta`; `location_tags` stays purely semantic;
      delete the client-side allowlist filter in `frontend/src/utils/locationFocus.js`.
      *Verify:* no provenance strings in API responses; focus tags still render.
- [ ] **P1.9 Controlled vocabulary for `facilities` / `special_programs`** (found while
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
- [ ] **P1.10 BG users see no attributes for English-primary schools.** When a school's
      site is English, `attributes.extracted.<lists>` come back empty and all content
      lands under `extracted_i18n.en`, so the `bg` projection is empty (e.g. school 510:
      `extracted.facilities == []`, `extracted_i18n.en.facilities == ["Medical care", …]`).
      Pre-existing — the old client-side merge behaved identically — but now visible in
      `attributes_i18n.bg`. Fix in `extractor_helpers._build_general_info_i18n` /
      `_pick_primary_text_lang`: always populate the primary-language slot, or translate.
      Related: extraction of `class_size` from `"5 students"` yields `5` (school 510).
      Both are extraction-quality bugs — add fixtures under P1.5.
- [ ] **P1.11 Dead attribute reads in the UI.** The frontend reads 23 `attributes.*`
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

## Phase 2 — User-facing correctness bugs

- [ ] **P2.1 diff=2 age-group gap.** `app/utils/education.py:15-24`: nursery covers
      diff 0–1, `first` starts at 3, so 2-year-olds get zero results.
      `tests/test_education.py:111-116` asserts the bug. Extend nursery to `max_diff: 2`
      (confirm against kg.sofia.bg group definitions), fix test, mirror in
      `countries.education_config` and frontend config.
- [ ] **P2.2 Slim the list endpoint.** Drop `exam_results` and `field_sources` from
      `SchoolListResponse` and remove the corresponding `selectinload`s in
      `app/services/school_service.py:34-41` (detail view fetches them).
- [ ] **P2.3 exam-averages tightening.** `app/routers/schools.py:164`: match
      `metric == "average_score"` exactly and pass subjects through instead of the
      math/bulgarian binary bucket.
- [ ] **P2.4 Run the data audits and re-scrape/repair Sofia.** SQL audits (duplicate
      coords, out-of-bbox, internal keys shipped, validation status distribution,
      display-name overrides, gate-failing pricing) are in the 2026-07-06 review /
      below in `docs/audits.sql` if extracted. After Phase 1 gates are in, re-run the
      pipeline for affected schools so historical bad data is regenerated through gates.

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
