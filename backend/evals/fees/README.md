# Fees eval

A regression check for the fees agent's reader: it reads saved pages and is scored against
the blind answer keys of R6 step 3 and step 4 (`docs/accuracy/step3`, `docs/accuracy/step4`).
Saved pages keep the score still while the sites change, so a moved score means the prompt,
schema, checks or model moved it. `scripts/fees_eval.py` says what is scored and how.

- `pages/<school id>.json`: what the finder found for each keyed school, saved with
  `fees-agent --save-pages evals/fees/pages --school-id ... ` (a dry run; writes nothing to the DB).
- `baseline.json`: the averages the check compares with (`--write-baseline --trials 6`).

CI (`.github/workflows/fees-eval.yml`) runs 3 trials (about $0.10) on a PR that touches the reader, its
checks, the publish gate or the model choice, and fails when wrong rows rise more than 6
points or found fees drop more than 9 points below the baseline.
The reader is noisy on identical pages, so smaller moves are not detectable with 3 trials;
run more trials by hand (`--trials 10 --no-check`) to compare two versions closely. A PR that improves the
score on purpose writes a new baseline in the same PR. To try another model, run the
workflow by hand with `model` set (no baseline check), or locally with `MODEL_TIER_PRICING`.

Refresh the pages and the key together, about twice a year: a key read today against pages
saved months ago scores the age gap, not the agent.

Not covered: the finder (needs live sites), the re-read (needs stored rows), the keep rules.
