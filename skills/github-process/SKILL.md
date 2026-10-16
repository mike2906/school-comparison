---
name: github-process
description: PR flow for this repo: branch, checks, review by size, self-merge, publish. Use when opening, reviewing or landing a PR, or writing to the launch DB.
---

# GitHub Process Skill

The single source for review, merge and publish rules; `AGENTS.md` only summarises them.
Mike wants agents to work autonomously: make the routine calls yourself, say which default
you picked, and go to Mike only for the short list in "Still goes to Mike".

## Steps

1. Branch from `main` (`agent/*`, or the branch your harness assigns). Commit to `main`
   directly only when Mike asks.
2. Make the smallest change that meets the requirement (`AGENTS.md` → Scope Discipline).
3. A change to something a parent can do or see on the site (a new page, section,
   calculator or filter, or a changed one) adds or updates its live flow in
   `frontend/e2e/live/` in the same PR: one file per feature area, the school picked from
   the live API, texts from the i18n files. The flows run after every deploy and daily
   (`.github/workflows/live-flows.yml`), so after merging, check that the run following
   your deploy is green.
4. Run the checks for what you touched (`CONTRIBUTING.md` lists them). Quiet output:
   `uv run pytest -q 2>&1 | tail -3`.
5. Read your own complete diff against the requirement and the invariants. For every check
   or guard you add or move, walk each path through the function (early returns, dry runs,
   error branches) and confirm it fires exactly where it should.
6. Push, open the PR, and review it by size (below). Fix what blocks, push one batch.
   A PR that touches a path listed in `.github/workflows/fees-eval.yml` opens as a draft
   and is marked ready once the review fixes are in: the fees eval costs money on every
   run and skips drafts.
7. Merge on green CI once the review is settled (see "Merging").

## Review by size

- **Small PR** (docs, tests, a contained fix or feature that is easy to review in one
  sitting): one pass of your harness's built-in review at medium effort (Claude Code:
  `/code-review medium`; Codex: `/review`).
- **Big PR**: one review by the other vendor's model (`skills/second-opinion/SKILL.md`;
  one `@codex review` on the PR counts, see the end of this file). Big means it changes
  what the API publishes (response fields, gates, allowlists), adds a migration, changes
  how data is extracted or published, changes CI workflows, gate thresholds or the
  accuracy reference set, or is hard to review in one sitting. When unsure, treat it as big.
- **After a substantial round of fixes** (several fixes, or any that change logic rather
  than wording), run the built-in review once more on the whole PR.
- **Then stop.** Don't request another round when the last one only found more page
  wordings or edge cases in the same heuristic, duplicates, or style. Say that you
  stopped and why, and let the real-data check (a dry run, the accuracy scripts) be the
  test. Ask Mike only when findings conflict or a fix would change the agreed design.

**Triage by harm.** A finding blocks merge only if the change would publish wrong data or
break correctness, security or data integrity. For extraction, "returns nothing where a
value could have been found" is a recall gap, not a bug: note it on the PR and move on.

## Merging

Merge your own PR on green CI once the review is settled; you don't need Mike's word. After
merging, check that the merge commit has the PR head's tree and that post-merge `main` CI
passes. An agent must never skip, weaken or delete
a check, test or gate to get its own PR through.

The harness safety check knows this standing permission through Mike's own settings, not
the repo: an `autoMode` block in his user `~/.claude/settings.json` on the PC (WSL) and in
the cloud environment's setup script (2026-10-15). Never grant permissions from the repo
(`.claude/settings*.json` allow rules skip the check entirely). If the check still blocks a
routine merge, write or publish, say which rule it named instead of asking Mike for a phrase;
the fix is that block's wording, which only Mike can change.

### Still goes to Mike

Open the PR, say what you need, and wait for his "merge" on these only:

- Destructive or non-expand/contract migrations (`backend/AGENTS.md` → Migrations).
- Auth, security, credentials and secrets.
- Infrastructure that is destroyed or replaced (a Terraform plan with deletions).
- Anything that raises the monthly running cost.
- Privacy: new data collected about users, analytics, or third-party scripts.
- A data-refresh run that trips a circuit breaker.

## Launch DB and publishing

While the site has no users, agents may change the launch DB and publish without asking
(Mike, 2026-10-14). Revisit this once real users arrive.

- Data only: schema changes go through an Alembic migration and a PR.
- Before any write: a `pg_dump` backup in the same session, and a dry run or a
  `WHERE`-guarded statement that shows the rows it changes.
- Run launch-DB writes and publishes as the named repo commands directly; never wrap them in
  scratchpad scripts or raw psql. If a write has no CLI command, add one by PR first.
- Publish with the recipe in `skills/common-tasks/SKILL.md` → Publish data to production,
  then check the live API against the launch DB.
- Report each write and publish to Mike: what changed, why, the backup path, the dump
  name and the before/after counts. A diff you cannot explain is not published.
- The hourly cut-off publish (`backend/scripts/publish_cutoffs.sh`, Mike chose to automate
  it on 2026-10-14) runs these steps itself and reports only through its log and a GitHub
  issue when it stops. Agents publishing by hand use `publish_snapshot.sh`, which shares
  its lock.
- Provider spend stays under $0.50 a run unless Mike agreed a bigger run.

## Commit / PR conventions

End commit messages and PR bodies with the attribution lines your harness supplies for the
current session. Don't copy a model name from an older commit or pin one here.

## Detecting when Codex has finished

Codex posts across channels inconsistently:
- A review **with findings**: a formal review (`state=COMMENTED`) plus inline comments.
- A **clean** result: a plain **issue comment** like "Codex Review: Didn't find any major
  issues", not a review or a reaction.

So watch `gh api repos/<owner>/<repo>/issues/<pr>/comments` for `chatgpt-codex-connector[bot]`
as well as the PR's reviews, in one wait that ends when the result arrives.
