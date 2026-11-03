# Backlog

Open items carried over from the July–October go-live plan, which is now in
[`archive/GO_LIVE_PLAN.md`](archive/GO_LIVE_PLAN.md) (with its history of what went wrong
and how it was fixed; an item's full notes are in its section there). None were re-checked
when they were carried over (2026-11-03), and several were probably done since, so check
an item is still open before starting it. Code-structure work is in
[`TECH_DEBT_PLAN.md`](TECH_DEBT_PLAN.md); each feature's status is in its own doc.

## Product and data

| Item | What | Archive ref |
| --- | --- | --- |
| Launch-data curation | 117 private schools without a source-backed English name, 48 schools / 205 withheld price rows (from July; the fees agent has changed both since), schools 113/116 discrepancies. | P2.14 (e) |
| Kindergarten points by age | "Last admitted points" in the key facts may ignore the age group. Check against the cut-off work. | UF6 |
| Icons | Emoji and SVG icons are mixed, and flags stand in for languages. | UF31 |
| kg.sofia addresses | Duplicate spellings of one building come back on each kg.sofia run. Normalise addresses before comparing. | UF39 |
| School site groups | Proposal only, never applied: [`archive/PROPOSAL_SCHOOL_GROUPS.md`](archive/PROPOSAL_SCHOOL_GROUPS.md). | — |
| README | Screenshots, an architecture diagram and a decisions section. | P3.3 |

## Code

| Item | What | Archive ref |
| --- | --- | --- |
| Type checks | mypy in CI; replace the 7 `datetime.utcnow` uses in `backend/app`. | P4.2 |
| LLM clients | Merge the two OpenRouter model builders and drop the `inspect.signature` shim. | P4.5 |
| Filter tests | Tests for `/schools/filters` and `list_schools_filtered` attribute matching. | P4.6 |
| Fee date name | Expose the public `scraped_at` on fees under a clearer response alias. | P4.8 |
| Display-name dedupe | `_refine_display_identity_label` repeats `helpers._normalize_display_name_case`. | P1 (line ~126) |

## Operations (P3.2 follow-ups)

Several of these exist in another form now (live flows, PR previews, Codex reviews,
backups in `publish_snapshot.sh`): check before building.

- Slim the 1.7 GB API image (scraping dependencies out of the runtime set); remove the
  old `schooldecider-api:local` image from the host (Mike).
- CODEOWNERS and branch protection on workflows, gate configs, `AGENTS.md` and `skills/`.
- Staging stack for data changes (also `TECH_DEBT_PLAN.md`, E2); journeys run against it.
- UI agent that walks a PR's acceptance criteria on the preview; auto-merge on green CI,
  review and UI pass, except big changes.
- Structured logs with request IDs, frontend error tracking, alerts on 5xx and failed
  deploys; OpenTelemetry and data-freshness alerts.
- Agents deploy only through CI, with read-only logs and no production credentials.
- Versioned published snapshots in R2 with rollback; internal DB on the VPS; nightly
  `pg_dump` with a restore check.
- Automate the yearly NVO import; a temporary pipeline worker for big runs.
- Change detection (re-read only changed pages); reference set and verifier agent before
  auto-publishing each field family; circuit breakers that block a release.
- User writes, in order of risk: data-error reports, sign-in with saved schools, questions,
  comments.

Closed when carried over: E5/E6 (cut-offs and the points calculator are live), E1–E4
(replaced by the fees agent and school facts), P4.1/P4.4 (in `TECH_DEBT_PLAN.md`), P4.3
(frontend tests use `node --test`), P4.7 (TypeScript, optional) and P3.5 (only needed if
generated summaries return).
