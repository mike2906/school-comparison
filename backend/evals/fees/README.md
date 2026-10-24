# Fees eval

A regression check for the fees agent's reader: it reads saved pages and is scored against
the blind answer keys of R6 step 3 and step 4 (`docs/accuracy/step3`, `docs/accuracy/step4`)
and the targeted keys in `docs/accuracy/cases` (one school's known problem each; 596: the extras
the reader dropped run to run).
Saved pages keep the score still while the sites change, so a moved score means the prompt,
schema, checks or model moved it. `scripts/fees_eval.py` says what is scored and how.

- `pages/<school id>.json`: what the finder found for each keyed school, saved with
  `fees-agent --save-pages evals/fees/pages --school-id ... ` (a dry run; writes nothing to the DB).
- `baseline.json`: the averages the check compares with (`--write-baseline --trials 6`).

CI (`.github/workflows/fees-eval.yml`) runs 3 trials (about $0.15) on a PR that touches the reader, its
checks, the publish gate or the model choice, and fails when wrong rows rise more than 6
points or found fees drop more than 9 points below the baseline. It runs when the PR is opened, reopened
or marked ready for review, not on every push. After the last push that changes the reader's
behaviour (prompt, schema, checks, gate, model), run it once more on the final code by adding
the `fees-eval` label (remove it first if it is already on): `gh pr edit <n> --remove-label
fees-eval; gh pr edit <n> --add-label fees-eval`. Pushes that only fix tests or wording don't need it.
PR runs are capped at 3 a day (UTC), about $0.50 (OpenRouter is pay-as-you-go and the eval emptied it on 2026-10-22): past that the check
fails without calling the model, and the label (removed, then added) runs it the next day. Group reader changes into fewer PRs where you can;
each PR's run costs the same.
The reader is noisy on identical pages, so smaller moves are not detectable with 3 trials;
run more trials by hand (`--trials 10 --no-check`) to compare two versions closely. A PR that improves the
score on purpose writes a new baseline in the same PR. To try another model, run the
workflow by hand with `model` set (no baseline check), or locally with `MODEL_TIER_PRICING`.

Refresh the pages and the key together, about twice a year: a key read today against pages
saved months ago scores the age gap, not the agent.

The report also gives a main-price rate: of the schools whose key lists tuition, the share where
the read found at least one tuition fee (does a parent see the price at all). `--rescore <report>`
works it out for a report saved before it existed, with no model calls.

Not covered: the finder (needs live sites), the re-read (needs stored rows), the keep rules.
