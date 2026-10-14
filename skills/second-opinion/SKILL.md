---
name: second-opinion
description: Ask the other vendor's model (Claude asks Codex, Codex asks Claude) to challenge a big PR or decision. Use before a plan, design or research conclusion is acted on, for big PRs, and before anything hard to undo.
---

# Second Opinion Skill

A model from another vendor catches what the author's model is blind to. Ask it for:

- big PRs (`skills/github-process/SKILL.md` → Review by size);
- a plan or design before building it, and the conclusion of a research thread
  (e.g. the admission points rules) before planning on it;
- a switch-over, a deletion, or anything else hard to undo.

Not for routine edits, small PRs or reversible experiments: those just go ahead.

## How

- **Claude asks Codex**, on the latest Sol model:
  `codex exec --skip-git-repo-check -s read-only -m <latest Sol id> "<prompt>"`
- **Codex asks Claude**, on the latest Opus:
  `claude -p --model opus "<prompt>"` (the `opus` alias always means the newest Opus).
- For a PR, `codex review` (CLI) or one `@codex review` comment on the PR (runs on GitHub,
  so it works from anywhere) does the same job.

Give the other model the question, the evidence (file paths, numbers, the PR or doc, the
repo checkout it can read) and your recommendation, and ask it to find what is wrong
rather than to agree. Keep it read-only.

Weigh the answer on its evidence. Report both views and your decision in one place (the
PR or the thread). It is advice, not a vote: one round, then decide.

## Where the CLIs run

Only on Mike's PC, in WSL (`~/sofiaschoolcomparison`), logged in to Mike's subscriptions,
so a call costs nothing extra. A cloud session reaches them through a Remote Control
session on that PC. If the PC is offline, carry on with reversible steps and get the
second opinion before the step that is hard to undo.
