---
name: github-process
description: Full PR flow for this repo — branch, checks, PR, manual Codex review trigger, merge. Use when the user says "follow github process" or asks you to open/land a PR.
---

# GitHub Process Skill

Use this when the user says **"follow github process"** or otherwise asks you to open
and land a pull request. Only commit directly to `main` when the user explicitly asks
for that.

## Steps

1. Create a new `agent/*` branch from `main`.
2. Make a focused change and avoid unrelated files.
3. Run the relevant local checks (`cd backend && uv run pytest`; frontend lint/build if
   the change touches `frontend/`).
4. Commit only the files that belong to the change.
5. Push the branch and open a PR to `main`.
6. Wait for GitHub Actions.
7. Mark the PR ready for review, then **trigger the Codex review manually** by posting a
   `@codex review` comment on the PR. Codex auto-review is intentionally **off** to avoid
   wasting usage on trivial/docs-only PRs, so the review will NOT appear on its own — you
   must request it. For a code change, do not merge just because CI is green: after
   requesting, poll the PR until Codex has either posted findings or clearly completed with
   no findings. Codex signals completion inconsistently — watch **both** the PR reviews AND
   the issue comments for author `chatgpt-codex-connector[bot]` (a clean `@codex review`
   result often comes back as an issue comment like "Didn't find any major issues", not a
   formal review or a 👍). If it posts findings, address them in the PR or explicitly record
   why they are accepted before merging.
   - **Docs-only / trivial PRs:** skip the `@codex review` request to conserve usage; merge
     on green CI once mergeable.
8. If CI is green, the Codex review is clear or resolved (or was intentionally skipped for a
   docs-only PR), and the PR is mergeable, merge it to `main` and delete the branch.

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
