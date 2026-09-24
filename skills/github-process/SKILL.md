---
name: github-process
description: Full PR flow for this repo — branch, checks, PR, approval-gated Codex review, merge. Use when the user says "follow github process" or asks you to open/land a PR.
---

# GitHub Process Skill

Use this when the user says **"follow github process"** or otherwise asks you to open
and land a pull request. This file is the single source for the review rules; `AGENTS.md`
only summarizes the three that must hold before it is read. Only commit directly to `main`
when the user explicitly asks for that.

## Steps

1. Create a new `agent/*` branch from `main`.
2. Make the smallest focused change that satisfies the agreed requirement. If the work
   expands substantially in files, lines, abstractions, or behavioural scope, stop and
   reassess before continuing. Do not turn an operational or single-use requirement into a
   reusable production subsystem without explaining the cost and getting approval; treat
   existing work as sunk cost when a simpler design is available.
3. Run the relevant local checks (`cd backend && uv run pytest`; frontend lint/build if
   the change touches `frontend/`).
4. Adversarially self-review the complete diff against the requirement, repository
   invariants, likely edge cases, and existing tests. For every check, guard, or
   validation you add or move, walk each path through the function (early returns,
   dry-run/preview modes, error branches) and confirm it fires on exactly the paths it
   should. For code changes (not docs-only), also run one tool-assisted review of the diff
   at medium effort if the harness provides one (Claude Code: `/code-review medium`); never
   a higher or multi-agent level unless Mike asks. Fix substantive findings, rerun the
   appropriate checks, then commit only the files that belong to the change.
5. Push the branch and open a PR to `main`.
6. Wait for GitHub Actions.
7. When the implementation, self-review, tests, PR, and CI are ready, tell Mike that the PR
   is ready for independent ChatGPT pre-review and provide the PR URL. **Do not post
   `@codex review` unless Mike explicitly asks.** If Mike requests Codex Review, post one
   `@codex review` comment and wait (one background command, not per-turn polling) until
   Codex has either posted findings or clearly completed with no findings. Watch **both** PR reviews and issue comments for author
   `chatgpt-codex-connector[bot]`.
   - **Docs-only / trivial PRs:** skip the `@codex review` request to conserve usage; merge
     on green CI once mergeable.
   - **One consolidated request at a time:** never request a review after every small fix or
     commit. Batch related fixes, self-review the complete diff again, rerun the appropriate
     tests, and request another review only when the PR is believed to be merge-ready. An
     idle session does not justify another request, and do not repost while a review is in
     flight.
   - **Review stable, consolidated heads:** before the initial request, finish the intended
     implementation, tests, local checks, and supporting evidence. After a review, wait for
     the complete result and group findings by root cause. If a finding would substantially
     expand the agreed design, pause and reconsider whether the implementation scope is
     wrong instead of automatically layering on another fix. Otherwise, fix actionable
     findings together, add proportionate regression tests, run the relevant focused tests
     plus the full applicable suite, and push one consolidated fix batch before re-review.
   - **One Codex review per PR by default.** Codex Review draws from the same usage budget
     as the agents. After the first review, fix real findings and push, but do not request
     another review unless Mike explicitly asks for one.
   - **Triage findings by harm before fixing.** A finding blocks merge only if the change
     would publish wrong data or break correctness, security, or data integrity. For
     extraction heuristics, "returns null where a stated value could have been found" is a
     recall improvement, not a correctness bug: null is the safe fallback. Record such
     findings in a follow-up issue or PR description instead of fixing them in the loop.
   - **Pause non-converging loops:** stop and ask Mike for direction when a second review
     round targets the same function or heuristic, when a fix for one finding causes a new
     finding, when findings conflict, when a proposed fix materially expands the agreed
     scope, or when a round contains only duplicate, stylistic, or speculative feedback.
     Report the review count, the repeating or disputed findings, the test evidence, and a
     concrete recommendation.
8. **Self-merge lane.** The agent may merge its own PR on green CI, without waiting for
   Mike, when the change is only docs, tests, config defaults, or a small bug fix with a
   regression test, and self-review (step 4, including the tool-assisted review for code
   changes) left nothing outstanding. Everything else waits for Mike: the publish boundary
   (response fields, gates, allowlists), migrations, writes to the launch DB, deployment
   or credentials, and new dependencies. When unsure, it waits. Report each self-merge to
   Mike with the PR URL.
   **Exception, one-off launch-DB data fixes:** the agent may apply a small data
   correction (INSERT/UPDATE/DELETE, no schema change) to the local launch DB without
   waiting when it has evidence for the fix (e.g. the source page states the value),
   has taken a `pg_dump` backup in the same session, and has checked the effect with a
   dry run or a `WHERE`-guarded statement that returns the changed rows. Report each fix
   to Mike (what changed, why, backup path). Bulk promotions and anything that changes
   what the publish gates allow still wait for Mike.
9. Otherwise follow Mike's direction after independent pre-review. **When Mike says merge, merge** once
   CI is green and the PR is mergeable. Do not request another review first, even if the
   latest commit has not been reviewed. After merging, verify that the merge commit contains
   the exact PR-head tree and that post-merge `main` CI passes.

## Usage discipline

- **Wait for reviews or CI in one background command** that exits when the
  result arrives (or tell Mike "review requested, ping me when it's back" and stop). Never
  poll across repeated conversation turns: each turn resends the whole context.
- **Test in two tiers:** focused tests while iterating; the full suite once before pushing.
  Always use quiet output (`uv run pytest -q 2>&1 | tail -3`).
- **Start a fresh session per task** (implement → review fixes → merge are separate tasks
  when they span hours); long sessions carry their full history into every turn.
- **No subagents or high reasoning effort for routine edits** (a regex fix plus a test).
  Subagents start cold and rebuild context.

## Commit / PR conventions

- End commit messages and PR bodies with the attribution lines your harness supplies for
  the current session (for Claude Code, the `Co-Authored-By:` trailer naming the model
  actually in use). Don't copy a model name from an older commit or pin one here.

## Detecting when Codex has finished

Codex posts across channels inconsistently:
- A review **with findings**: a formal review (`state=COMMENTED`) plus inline review comments.
- A **clean** `@codex review` result: a plain **issue comment** like "Codex Review: Didn't
  find any major issues." — NOT a new review or a 👍 reaction.

So watch `gh api repos/<owner>/<repo>/issues/<pr>/comments` for `chatgpt-codex-connector[bot]`
in addition to `gh pr view --json reviews`, inside a single background wait loop (see Usage
discipline). A watcher that only checks reviews/reactions will miss the clean re-review.
