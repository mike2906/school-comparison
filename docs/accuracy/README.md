# Price accuracy (Lane D R4)

`reference_set.json` holds the fees a reviewer read from each sampled school's own website,
without seeing the pipeline's output. `backend/scripts/accuracy_report.py` compares it with
the launch DB's stored website price rows and with what the API publishes.

- Sample: `scripts/accuracy_sample.py --size 30 --seed 20261012`, a seeded random draw from
  the 138 Sofia private and international schools that have a website (launch DB, 2026-10-12).
- Reviewers: five Claude agents with WebFetch on the school's own domain only, no access to
  the repository, the DB or the API. Their minutes are agent minutes, not a person's.
- Match: same category, amount within 1 % (BGN at 1.95583), and the same period or none on
  the row.

## Results, 2026-10-12

| | |
| --- | --- |
| Schools compared | 24 of 30. Six sites block automated reading (robots.txt or failing pages): 522, 554, 587, 592, 603, 616 |
| Sites that state any fee | 13 of 24. 11 state none |
| **Published and wrong** | **7 of 58 published rows (12 %)** |
| **Correct but withheld** | **9 of 60 stored rows that match the site (15 %)** |
| Published without the period the site states | 16 |
| Site fees that are published | 57 of 164 (35 %; an upper bound, one row can cover two equal fees) |
| Reviewer time | 18 agent-minutes for 30 schools; 0.7 per school that states fees |

The seven wrong rows, checked by hand against the reference:

- **Stale amounts (3):** school 538's site now asks 600 / 530 / 185 EUR a month; the published
  rows say 580 / 510 / 160.
- **Wrong category (3):** school 506's learning-support levels (10 000 and 20 000 EUR a year)
  are published as tuition, and its yearly capital fee (3 402 EUR) as a one-time registration fee.
- **Period read differently (1):** school 596's materials fee is "one-time per school year";
  the row says one-time, the reviewer yearly. Borderline.

Misses worth knowing: school 597 states 41 fees and has no stored row; 633 states 13 and
none is published; 301 (ACS) has five matching rows, all withheld.

Re-run from `backend/` against the launch DB:

```bash
uv run python scripts/accuracy_report.py ../docs/accuracy/reference_set.json
```

The reference ages as schools change their fees; re-read the sites before trusting a re-run
more than a few months later.
