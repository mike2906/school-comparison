# 7th to 8th grade admission (state plan, Sofia-grad): notes

Retrieved 2026-10-14.

## Legal formula: `naredba10_art57_59.txt`

Source: Ciela consolidated Наредба № 10 / 2016, чл. 55–60 plus чл. 48 ал. 1.

**Score for most classes (чл. 57 ал. 1):** бал = НВО points (БЕЛ and math, max 100 each) + two subject grades from the 7th-grade certificate converted to points.
- The two subjects are chosen by the school's pedagogical council.
- Grade-to-points scale (Наредба 11): 6=50, 5=39, 4=26, 3=15.
- The maximum бал is **500**.

**Weighting variants (чл. 57 ал. 3).** The pedagogical council picks one:
1. 2×БЕЛ + 2×Мат
2. 2×one + 1×other
3. 3×one + 1×other
4. 2×one + 1×other + 1×additional НВО subject
5. БЕЛ + Мат + 2 additional НВО subjects
6. БЕЛ + Мат + 1 additional
7. БЕЛ + Мат once each

Additions and special cases:
- **Olympiads (ал. 2, 5, 6):** olympiad or competition points scaled to 100 may be added, only with variants 2, 6 or 7. With variant 7 they are doubled.
- **Missed НВО (ал. 8):** a missed НВО counts as 0.
- **Olympiad winners (ал. 10):** national olympiad podium finishers get the maximum score instead of the НВО score.
- **Ability exams (чл. 58):** for art, music and sport classes, бал = НВО БЕЛ + НВО Мат + **2× ability exam** + two grades.
- **Ties (чл. 59):** ties are broken by the НВО sum, then the sum of the two subject points, then the average of the 7th-grade раздел А grades. Students still tied are all admitted over the plan.
- **Over the plan (чл. 60):** twins and orphans are admitted over the plan.
- The 2025 amendments (ДВ 79/2025) add integrated-test НВО wording.

**Sofia 2026:** RUO said the foreign-language НВО is not used for any Sofia class. Only НПМГ used the olympiad option.

## Files

**`grade8_minmax_2026.csv`:** 1584 rows, all four 2026 rounds stacked. Columns:

| Column | Meaning |
|---|---|
| `round` | 1–4 |
| `as_of` | date of the RUO справка |
| `school_code` | НЕИСПУО code |
| `school_name` | school name |
| `class_code` | 5-digit class code, zero-padded |
| `class_name` | class (profile/profession) name |
| `min_total`, `min_male`, `min_female` | minimum бал: overall, boys, girls |
| `max_total`, `max_male`, `max_female` | maximum бал: overall, boys, girls |

How to read the CSV:
- **0 means nobody was placed** in that class in that round. From round 2 on, the figures cover only students newly placed in that stage.
- The male/female split exists because some classes have gender quotas. In the xlsx files, quota classes are coloured blue.

**`grade8_2026/` originals:** RUO "СПРАВКА за минималния и максималния бал по паралелки в РУО СОФИЯ-ГРАД" xlsx files. They were downloaded from the mirror danybon.com/wp-content/uploads/2026/07|08/ because ruo.mon.bg pages are behind a Cloudflare challenge.

| File | Round | As of |
|---|---|---|
| `min_max_1klas_2026.xlsx` | 1 | 14.07.2026 |
| `min_max_2klas_2026_.xlsx` | 2 | 21.07.2026 |
| `min_max_3_klasirane_2026.xlsx` | 3 | 29.07.2026 |
| `min_max_4kl_2026.xlsx` | 4 | 05.08.2026 |

- For round 2, the corrected file is used. Danybon's first round-2 upload, `min_max_2klas_2026.xlsx`, duplicated round 1 and is not included.
- RUO page for round 2: https://ruo.mon.bg/sofia-grad/student-admission/spravka-za-minimalniya-i-maksimalniya-bal-po-paralelki-v-ruo-sofiya-grad-kam-data-21-07-2026-g/

**`grade8_2026/proekt_dpp_2026_2027_dnevna_dualna.pdf`:** the **draft** (March 2026) state admission plan from RUO Sofia-grad, https://ruo.mon.bg/sofia-grad/files/sites/2/2026/03/proekt_dpp_2026_2027_dnevna_dualna.pdf
- It lists schools and classes (code, profile/profession, form, years, number of classes and places, languages).
- It has **no scoring formulas**.
- The final plan was approved by заповед РД01-387/30.04.2026.

## Not obtained

- **Per-class scoring formula list, so no `grade8_plan_2026.csv`.** The file is "Списък на паралелките с ДПП в VIII клас 2026/2027 ... с кодове и балообразуване", 30.06.2026. It is linked from https://ruo.mon.bg/sofia-grad/student-admission/vazhno-za-kandidat-gimnazistite-v-sofiya-i-tehnite-roditeli. ruo.mon.bg HTML pages, the WP REST API and directory listings all return a Cloudflare 403. Direct `/files/...` URLs do work, so a human can copy the file URL from a browser and download it with curl.
- **2025 minimum and maximum scores:** not downloaded, for lack of time. Danybon has a 2024 round-2 page and RUO has 2025 pages.
