# Adjudication rules (R6 step 1 rescore)

Revised 2026-10-13 after a second review: a count of payments no longer implies a semester
(applied by `rescore.py`, which clears a "semester" the page does not state), fees of a sibling
school on a shared site are left out (`corrections.json`), and an older year's fees are left
out only when the site states that category for 2026/27 or later. Frozen from here: the
validation run on untouched schools uses these rules unchanged.

You are rebuilding, from the school's own web pages, the list of fees the site states, and judging a
shuffled list of candidate fee rows (from two different extractors, unlabelled) against it.

## Site fees
A site fee is one amount (or stated range) the site states as a charge, for one group/grade, plan and
academic year. List every one you find on the fee/admissions pages (including PDFs linked from them).
For each: category, amount (or amount_min/amount_max), currency, period, group (grade/age group as
stated), plan (payment plan/option name as stated), year (academic year as stated), url, and a short
verbatim quote (<= 25 words) containing the amount.
- Categories: tuition, food, transport, activities, registration, materials, extended_day, uniforms,
  extracurricular, camp. `activities` = included programme items; `extracurricular` = optional
  classes, clubs, sports charged on top. Exam / application / entry-test / enrolment fees and
  deposits are `registration` (there is no deposit category; a deposit filed as tuition is wrong).
- Period = how often THAT amount is charged:
  - monthly / quarter / term / semester as stated;
  - yearly = charged once per school year (incl. "еднократна такса" for the school year, and a full
    year's tuition paid in one payment, e.g. "еднократно плащане" option);
  - one_time = charged once ever (enrolment, entry exam, deposit);
  - instalments: each printed instalment amount is its own site fee. Its period is the one the
    site states for it ("8 месечни вноски" -> monthly; "на тримесечие" -> quarter). A count of
    payments ("2 x", "на две вноски") does not state a period, so the correct period is null,
    and any period given to it is wrong_period. A one-off first or last instalment that differs
    from the rest has period null too.
    Example: "1 x 2992 + 8 x 1051" under a "Месечно плащане" heading gives 2992 (period null)
    and 1051 (monthly).
  - A yearly total is a site fee only if the site prints it (or states it in words). A total computed
    by adding instalments is not a site fee.
  - If the site gives no frequency for an amount, the correct period is null.

## Verdicts for each candidate row (exactly one)
- correct: matches a site fee on amount (range must match as a range), currency, category, and
  period (equal, or both null). The row's label/note must not contradict the site fee's
  group/plan/year. A row with no label is judged on amount/category/period only; then add
  "group_unstated": true.
- missing_period: as correct, but the row's period is null while the site states one.
- wrong_period, wrong_category, wrong_amount (amount not a site fee at all, incl. computed totals and
  ranges built from two different fees), wrong_group_plan_year (amount is a site fee, but the label
  or note ties it to a different group/plan/year), wrong_currency.
- duplicate: matches the same site fee as an earlier candidate in the list that is equally or more
  correct (keep the better one as the match, mark the other duplicate).
- stale: the amount is a fee for an academic year older than the one the site currently shows.
- unresolved: you cannot decide (page unreachable now, or genuinely ambiguous). Say why.
Give every candidate: verdict, matched site fee id (or null), one-line reason.

## Output
Write one JSON file per school to the given path:
{"school_id": N, "pages_read": [urls], "fetch_failed": [urls], "site_states_no_fees": bool,
 "site_fees": [{"fid": "f1", "category", "amount", "amount_min", "amount_max", "currency",
   "period", "group", "plan", "year", "url", "quote"}],
 "candidates": [{"oid", "verdict", "fid", "reason", "group_unstated": bool}],
 "notes": "anything surprising"}
Be literal and conservative. Do not guess numbers you cannot see on a page. Fetch evidence fresh
with WebFetch; ask WebFetch for the full verbatim fee text (all amounts with their labels), not a
summary. Follow fee/admissions/price links (такси, цени, прием, финансови условия) and PDFs.
