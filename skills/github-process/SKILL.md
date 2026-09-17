---
name: github-process
description: Full PR flow for this repo — branch, checks, PR, approval-gated Codex review, merge. Use when the user says "follow github process" or asks you to open/land a PR.
---

# GitHub Process Skill

Use this when the user says **"follow github process"** or otherwise asks you to open
and land a pull request. Only commit directly to `main` when the user explicitly asks
for that.

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
   invariants, likely edge cases, and existing tests. Fix substantive findings, rerun the
   appropriate checks, then commit only the files that belong to the change.
5. Push the branch and open a PR to `main`.
6. Wait for GitHub Actions.
7. When the implementation, self-review, tests, PR, and CI are ready, tell Mike that the PR
   is ready for independent ChatGPT pre-review and provide the PR URL. **Do not post
   `@codex review` unless Mike explicitly asks.** If Mike requests Codex Review, post one
   `@codex review` comment and poll until Codex has either posted findings or clearly
   completed with no findings. Watch **both** PR reviews and issue comments for author
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
   - **Converge on evidence, not a fixed review count:** continue the review → batch fixes →
     re-review loop while each round identifies actionable correctness, security, or data-
     integrity issues and the fixes are making measurable progress. Merge only when the
     latest PR head has been reviewed, all actionable findings are resolved or explicitly
     accepted, CI is green, and the local audit supports the fixes.
   - **Pause non-converging loops:** stop and ask the user for direction when the same root-
     cause issue survives two consecutive fix attempts, findings conflict, a proposed fix
     materially expands the agreed scope, or a round contains only duplicate, stylistic, or
     speculative feedback. Report the review count, the repeating or disputed findings, the
     evidence from tests/audits, and a concrete recommendation. Do not suppress substantive
     findings merely because several reviews have already run.
8. Follow Mike's direction after independent pre-review. Merge only when CI is green, the
   PR is mergeable, and every review Mike requested is clear or resolved. If Codex Review
   was requested, verify immediately before merging that its latest reviewed commit SHA is
   still the PR head. After merging, verify that the merge commit contains the exact PR-head
   tree and that post-merge `main` CI passes.

## Commit / PR conventions

- End commit messages with:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- End PR bodies with:
  `🤖 Generated with [Claude Code](https://claude.com/claude-code)`

## Detecting when Codex has finished

Codex posts across channels inconsistently:
- A review **with findings**: a formal review (`state=COMMENTED`) plus inline review comments.
- A **clean** `@codex review` result: a plain **issue comment** like "Codex Review: Didn't
  find any major issues." — NOT a new review or a 👍 reaction.

So poll `gh api repos/<owner>/<repo>/issues/<pr>/comments` for `chatgpt-codex-connector[bot]`
in addition to `gh pr view --json reviews`. A poller that only watches reviews/reactions will
miss the clean re-review and time out.
