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
   your deploy is green. A change to a result a parent gets (a calculator answer, a fee
   or cut-off for an age, a filter) also adds its parent to
   `frontend/e2e/live/coverage.test.js`, which CI runs on every PR against live data.
4. Run the checks for what you touched (`CONTRIBUTING.md` lists them). Quiet output:
   `uv run pytest -q 2>&1 | tail -3`.
5. Read your own complete diff against the requirement and the invariants. For every check
   or guard you add or move, walk each path through the function (early returns, dry runs,
   error branches) and confirm it fires exactly where it should.
6. Push, open the PR, and review it by size (below). Fix what blocks, push one batch.
7. Merge on green CI once the review is settled (see "Merging").
   The fees eval runs only when a PR is opened, reopened or marked ready; if a later push changed the
   reader's behaviour, re-run it on the final code with the `fees-eval` label first
   (`backend/evals/fees/README.md`).

## Review by size

- **Small PR** (docs, tests, a contained fix or feature that is easy to review in one
  sitting): one pass of your harness's built-in review at medium effort (Claude Code:
  `/code-review medium`; Codex: `/review`).
- **Big PR**: one review by the other vendor's model (`skills/second-opinion/SKILL.md`;
  one `@codex review` on the PR counts, see the end of this file). Big means it changes
  what the API publishes (response fields, gates, allowlists), adds a migration, changes
  how data is extracted or published, changes CI workflows, gate thresholds or the
  accuracy reference set, or is hard to review in one sitting. When unsure, treat it as big.
- **Parent-facing PRs** (step 3): the review also reads the change as a Sofia parent
  would. Flag an answer option that makes no sense for the question ("Not sure" on
  twins), a result that is empty or odd for an ordinary family, and wording only an
  insider understands.
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
- `publish_snapshot.sh` stops when many schools would lose something they show now (fees,
  pins, results...) and raises the issue "Publish stopped: schools would lose data". Publish
  the drop with `PUBLISH_ALLOW_DROPS=1` only when you made it on purpose and can say why;
  otherwise it is a diff you cannot explain: find what wiped the data, or ask Mike.
- Provider spend: about $1 a day is the guide (there is no per-run cap). Tell Mike the
  real spend you expect before a paid run bigger than that, and warn him when a run looks
  well over its estimate.

### Jobs on Mike's PC

Launch-DB runs and publishes happen in a Remote Control session on Mike's PC. A thread
can only start one from a message Mike typed in that thread (a platform rule), so ask
for one message per job, and make it cover the whole job:

- **One go covers the job.** When Mike says go to a job (or it falls under the standing
  permission), that covers every step you described: backup, dry run, checks, the full
  run and the publish. Brief the PC session with the whole job and the rule for each step
  ("if the dry run shows X, carry on to the full run; otherwise stop and report"). Don't
  write "stop and wait for my message" into a brief, and don't ask Mike to repeat a go
  between steps. Stop only for a result you can't explain or an item from "Still goes to
  Mike".
- **Steering a running session.** To change the plan mid-job, send the new instructions
  with the same tool that started the session; it delivers them to the running session.
  The general cross-session message tool is refused for Remote Control sessions. That
  refusal does not mean only Mike's typed text reaches the PC.
- **Cost.** Give Mike the real spend you expect. The `--max-usd` guard books an unpriced
  or in-flight call at a fixed reserve, so pick the guard value yourself with headroom. A
  higher guard is not a new cost decision. Ask Mike again only if the real spend would go
  well over what he saw (about 1.5x, or past the day's guide on a provider he asked to
  spare).
- **Name his consent in the brief.** Quote the go message and its time ("Mike typed 'go
  and publish' at 08:26 UTC"), and list the commands it covers. Mike's own messages in the
  thread are also forwarded to the session.
- **Publish as the plain command:** `backend/scripts/publish_snapshot.sh` takes the publish
  lock itself. Use the `flock` wrapper only when a write and a publish must share one lock.
  If the safety check refuses a routine step, quote its rule, try the plain named command
  once if a wrapper was involved, and then report.

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
