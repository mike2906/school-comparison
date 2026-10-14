# Admission cut-offs

How competitive admission is, per school, round and year: the points of the last child
admitted (kindergarten, nursery, ПГУ), the priority group and points of the last child
admitted (first grade), and the minimum and maximum score per class (8th grade).

## Why an archive

The official sources do not keep history:
- **kg.sofia.bg (ИСОДЗ)** publishes ranked lists for every round and group, but only for the
  current school year. A round not captured while it is online is lost (the May 2026
  general kindergarten rounds already are).
- **РУО София-град** publishes one min/max spreadsheet per 8th-grade round; file names change
  every year and older files survive only in the Wayback Machine.

So we keep our own archive: versioned JSON files on the **`cutoff-data` branch**, written by
`.github/workflows/capture-cutoffs.yml` (GitHub's runners reach kg.sofia.bg, so it needs
nothing on Mike's PC). The archive is the source of truth; the DB copy is derived
from it by the import (below), so a DB rebuild loses nothing.

## Privacy

The lists name children (initials, child number, random number). The capture reduces them
in memory to one aggregate row per queue and stores nothing per child.
`app/services/admission_cutoffs.check_row` refuses rows with per-child fields.

## Layout

`<country>/<city>/<stage>/<campaign-slug>/round-<round_id>.json`, each
`{"meta": {...}, "rows": [...]}`, plus `_state.json` (capture bookkeeping). New cities and
countries are new folders; a new source only has to emit the same rows.

## Row fields

| field | notes |
|---|---|
| country, city | `bg`, `sofia` |
| stage | `nursery`, `kindergarten`, `preparatory` (ПГУ in a school), `grade_1`, `grade_8` |
| campaign | school year the child starts, repo spelling `2026/2027` (`app/utils/academic_year`) |
| rule_set | the rules the round ran under: family, campaign and the date of the rules version in force, e.g. `bg/sofia/kindergarten/2026-2027@2026-03-12` (dates in `kg_sofia_ratings.RULE_VERSIONS`; add one when the council amends the rules) |
| round_id | the source's round id (ИСОДЗ `ratingId`) |
| classified_on | date of the published list (Sofia local date) |
| round_name, round_kind | from the ИСОДЗ calendar: `Първо класиране` (`general`), `Текущо класиране` (`current`); null if the calendar no longer lists the date |
| source, source_id | `kg.sofia.bg` and its institution id (one per building; maps to `schools.attributes.kg_sofia_id(s)`) |
| institution_name, district | as the source names them; district = административен район |
| group_id, group_label, birth_year, age_group | ИСОДЗ group; `age_group` uses the site's keys (`nursery`, `first`, `second`, `third`, `preschool`), null for schools |
| queue | `general`, `sop` (СОП), `chronic` (chronic illness), `social` (not used in Sofia as of 2026) |
| places | places offered in this round (from the waiting list title) |
| applicants_count, admitted_count | children ranked in the round / admitted |
| last_admitted_points | points of the last child admitted; null when nobody was |
| last_admitted_group | first grade only: priority group (1 best) of the last child admitted |
| tie_at_cutoff | the last admission needed the random number |
| not_admitted_at_cutoff | children with the same score who were not admitted |
| undersubscribed | nobody who applied was turned away |
| source_url, retrieved_at | the exact list and when we read it |

8th-grade rows (РУО) add `class_code`, `class_name`, `min_score`, `max_score`.

## Reading cut-offs honestly

A cut-off says who got in at that round, under that round's rules. It is not a promise:
places, applicants and rules change, ties are decided by a random number, and the
current-round cut-offs are about leftover places. Compare points only within one
`rule_set` (Sofia changed address points from 5/4/3 to 8/5/3 in 2026).

## Capture schedule

The workflow runs at 03:15, 16:15 and 21:15 UTC. A run checks the ИСОДЗ calendar and scans
only when a round was published since the last full scan, or once a week. It fetches only
(round, group) lists not yet archived, at two requests a second, and stops at a request
budget so the next run continues. `workflow_dispatch` with `force` scans regardless.
