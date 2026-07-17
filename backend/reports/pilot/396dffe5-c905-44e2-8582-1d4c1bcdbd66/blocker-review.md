# P2.10(a) Gemini ceiling-run blocker review

Run: `396dffe5-c905-44e2-8582-1d4c1bcdbd66`

Comparison baseline: `9d2c837f-aaf9-4924-a884-899f9cbcf4e0`

Model: `google/gemini-3.1-pro-preview` (plain variant)

Route: Google AI Studio only; provider fallbacks disabled; `require_parameters=true`

Prompt changes: none

## Read this first

This packet is **complete enough for the blocker-taxonomy review**, not a blocker-list verdict.
The original cohort process was stopped during extraction to preserve the user-approved `$1.00`
hard session budget. It completed URL validation and navigation for all 18 schools, then
persisted 9 of 18 extractions. After the user's partial-run review, one extraction-only top-up
reused school 538's cached navigation and supplied the sole missing pricing-taxonomy case. The
full cohort's Stage 6 validation and Stage 7 summarization were not reached; that is accepted for
this bounded model-ceiling decision. No other school or model was run.

The manual reviewer should not interpret an unavailable new value as a pass or a fail.

## Run accounting and retry evidence

| Item | Exact result |
|---|---|
| OpenRouter key usage before | `$9.55274413` |
| Original cohort usage after settlement | `$10.39444013` (`$0.841696`) |
| School-538 top-up usage | `$10.39444013` → `$10.47598213` (**`$0.081542`**) |
| Combined provider-accounted ceiling spend | **`$0.923238`** |
| Original `$1.00` budget remaining | `$0.076762` |
| Pipeline / evaluation status | Original pipeline row `PARTIAL`; ceiling evidence **complete enough for blocker-taxonomy review**; verdict pending |
| Stage outcomes | URL validation 18/18; navigation 18/18; original extraction 9/18 persisted; school-538 extraction-only top-up succeeded; cohort Stage 6 and summarize not reached |
| Persisted fresh extractions | Original 9: `105, 153, 404, 454, 460, 516, 570, 584, 631`; top-up: `538` |
| No fresh extraction | `103, 233, 297, 324, 440, 506, 529, 610` |
| Token accounting | Unavailable: the timed-out/interrupted requests did not return OpenRouter usage payloads. This is not recorded as zero. |
| Visible call failures | Four blank `Extraction call failed (PriceExtractionOutput)` warnings before termination; the log did not identify them as typed schema retries versus provider timeouts. |
| School-538 top-up calls | Two Gemini calls; zero model retries; zero hard failures; deterministic validation inside extraction returned `ok` |

The exact provider delta is authoritative for cost. The missing token split is an observed
failure of accounting under interrupted requests; no token estimate has been substituted.

## Pricing association cases

### School 538 — transport / tuition association

| `9d2c837f` stored output | Fresh Gemini stored output (`2026-07-17 11:24:13Z`) |
|---|---|
| `TUITION 510 EUR MONTHLY`; `TRANSPORT 26 EUR MONTHLY`; `TRANSPORT 160 EUR MONTHLY`; `TRANSPORT 580 EUR MONTHLY`; extracurricular rows `13/23/25/25/30/33/33 EUR MONTHLY`. The documented blocker says 26 is after-hours care, 160 is transport, and 580 is nursery tuition. | `TUITION 510 EUR MONTHLY`; `TRANSPORT 26 EUR MONTHLY`; `TRANSPORT 160 EUR MONTHLY`; `TRANSPORT 580 EUR MONTHLY`; extracurricular rows `13/23/25/25/30/33/33 EUR MONTHLY`. All age groups remain null. Fresh confidence is `0.90–0.95`; the `580` row includes diapers, food/drinks, and cosmetics, while remaining categorized as transport. |

The old and fresh tuple sets are identical. This is a factual diff only; the user decides the
transport/tuition-association verdict against the source headings.

Manual verdict: [ ] pass  [x] fail  [ ] not reviewable — identical miscategorization
reproduced: 580 EUR remains TRANSPORT despite the fresh row itself listing diapers,
food/drinks, and cosmetics (nursery tuition per the documented ground truth), at 0.90–0.95
confidence. (User review 2026-07-17.)

### School 570 — registration / deposit / meals headings

| `9d2c837f` stored output | Fresh Gemini stored output (`2026-07-17 10:54:01Z`) |
|---|---|
| `TUITION`: `180 MONTHLY`, `680 MONTHLY`, `4122 TERM`, `7820 YEARLY`; `REGISTRATION`: `100`, `300`, `850`, `1000` `ONE_TIME`; `MATERIALS 80 YEARLY` (all EUR, no age group). | `TUITION`: `180 MONTHLY`, `680 MONTHLY`, `4122 TERM`, `7820 YEARLY`; `REGISTRATION`: `100`, `300`, `850`, `1000` `ONE_TIME`; `MATERIALS 80 YEARLY` (all EUR, no age group). |

The old and fresh tuple sets are identical. This is a factual diff only; the user decides the
blocker verdict against the source headings.

Manual verdict: [ ] pass  [x] fail  [ ] not reviewable — user review against the live fee
page (2026-07-17): `TUITION 180 MONTHLY` is actually **meals**; `REGISTRATION 850 ONE_TIME`
is actually the **monthly tuition fee (Sept–June)**; `REGISTRATION 1000` is a **deposit**;
`REGISTRATION 300` is **school supplies**; `100` is a trial-day application fee; the "local
families" qualifier on `680 MONTHLY` is lost; annual transport (2750) and regional bus (200)
rows were not captured. `4122 TERM` is an installment-plan variant the schema cannot express.

### School 153 — combined Uwekind page entity / plan association

| `9d2c837f` stored output | Fresh Gemini stored output (`2026-07-17 10:53:36Z`) |
|---|---|
| `TUITION`: `7000`, `7400`, `8100`, `8250` yearly; `FOOD`: `370`, `550`, `1260`, `1450`, `1480` yearly; `REGISTRATION`: `100`, `150`, `500` one-time. All EUR; every age group is null; no entity/plan applicability is retained. | `TUITION`: `7000`, `7400`, `8100`, `8250` yearly; `FOOD`: `370`, `550`, `1260`, `1450`, `1480` yearly; `REGISTRATION`: `100`, `150`, `500` one-time. All EUR; every age group is null; no entity/plan applicability is retained. |

The old and fresh tuple sets are identical. This is a factual diff only; the user decides the
entity-association verdict against the combined source page.

Manual verdict: [ ] pass  [x] fail  [ ] not reviewable — user review against
uwekind.com/admission/fees (2026-07-17): the stored `7000/7400/8100/8250` are
**payment-plan variants** (7000 vs 7400 = same kindergarten year in 2 vs 10 installments;
8100/8250 = preschool *and* school in 2 vs 3 installments), while the headline base fees
**6820 (KG) / 7880 (preschool) / 7880 (school) per year are missing entirely**. No
entity/plan attribution survives; a parent cannot tell which price applies to which program.

## Admission-fragment cases

These are raw stored extraction values. They remained unpublished; see the boundary audit below.

### School 103

| `9d2c837f` stored output | Ceiling-run stored output |
|---|---|
| `entrance_requirements`: `здравно-профилактична карта на детето`; `попълнена от личния лекар`; `изследвания на кръв и урина`; `извършени в едноседмичен срок преди постъпване на детето в детската градина`; `данни от личния лекар`; `че на детето са извършени задължителните имунизации за възрастта`; `при отсъствие за повече от 2 месеца – еднократен отрицателен резултат за чревни паразити`. | **Unavailable.** Navigation completed, but no Gemini extraction persisted; the old extracted value remains in storage. |

Manual verdict: [ ] pass  [ ] fail  [x] not reviewable — no fresh extraction persisted.

### School 105

| `9d2c837f` stored output | Fresh Gemini stored output (`2026-07-17 10:52:31Z`) |
|---|---|
| `entrance_requirements`: `Деца`; `на които не са извършени задължителните имунизации за възрастта не се приемат в ДГ № 4 „Слънчо“.` | `entrance_requirements: []`; `required_documents`: `Заявление за записване`, `Оригинал на удостоверението за раждане на детето`, `Документи за самоличност на родителите/настойника`, `Нова здравна карта за дете с данни за имунизационния статус`, `1. Оригинал на удостоверението за раждане на детето`; `application_steps`: `Подаване на заявление и декларации по образец в основната сграда`, `се извършва само чрез писмено заявление`, `Заявление за преминаване към самостоятелна организация –`, `Заявление за достъп до лични данни –`, `ДГ4 – Заявление за достъп до обществена информация –`, `ДГ4 – Протокол за приемане на устно заявление за достъп до обществена информация –`. |

Manual verdict: [ ] pass  [x] fail  [ ] not reviewable — structure improved over the old
garbled negative (real `required_documents` list), but `application_steps` misfiles GDPR
data-access request forms and dangling fragments (`Заявление за достъп до лични данни –`)
as enrollment steps, and duplicates a birth-certificate entry. (User review 2026-07-17.)

### School 153

| `9d2c837f` stored output | Fresh Gemini stored output (`2026-07-17 10:53:36Z`) |
|---|---|
| `entrance_requirements`: `Сесията включва три етапа - задачи в писмен вид`; `интервю и изпращане на обратна връзка за кандидата.`; `Интервю – комуникативни и социални умения`; `мотивация за учене.` | The same four `entrance_requirements` fragments. Additionally, `application_steps` stores `Административна такса за кандидатстване за прием` and `Такса кандидатстване:`. |

Manual verdict: [ ] pass  [x] fail  [ ] not reviewable — identical mid-sentence fragments
reproduced, plus new dangling artifacts (a bare `Такса кандидатстване:` label stored as an
application step). (User review 2026-07-17.)

### School 506

| `9d2c837f` stored output | Ceiling-run stored output |
|---|---|
| `entrance_requirements`: `Children must be able to provide self-care independently (e.g. toileting`; `changing clothes)`; `application_deadlines`: `The admission procedure may require placement tests and/or interviews.` | **Unavailable.** Navigation completed, but no Gemini extraction persisted; the old extracted value remains in storage. |

Manual verdict: [ ] pass  [ ] fail  [x] not reviewable — no fresh extraction persisted.

## Summary identity and tone

The full old summaries are preserved in
[`9d2c837f/api-summary-admission.txt`](../9d2c837f-aaf9-4924-a884-899f9cbcf4e0/api-summary-admission.txt).
The prior manual blocker groups were:

| `9d2c837f` blocker group / stored examples | Ceiling-run stored output |
|---|---|
| Identity/entity collisions: 153, 529 | **No Gemini summaries generated.** |
| Testimonial/marketing prose: 506 (`There are never any two days...`), 610 (`подхранва вътрешния интелект...`), 631 (`сигурна и любяща среда...`) | **No Gemini summaries generated.** |
| Unsupported negatives: 233 (`no minimum score thresholds`), 584 | **No Gemini summaries generated.** |
| Stale relative claims: 404 | **No Gemini summaries generated.** |
| Mistranslation / unstable operational details: 538 | **No Gemini summaries generated.** |
| Raw/generic artifacts: 103, 297, 324 (`!▪️ Педагогически съветник...`) | **No Gemini summaries generated.** |

Extraction cleared `summary_i18n` for the ten freshly extracted schools
(`105,153,404,454,460,516,538,570,584,631`). The other eight retain pre-run summaries and are not
Gemini evidence. Because Stage 7 was never reached, summary identity/tone is not reviewable from
this run.

Manual verdict: [ ] pass  [ ] fail  [x] not reviewable — Stage 7 never ran. Moot for the
ceiling verdict: summaries are out of launch scope, and the reviewable extraction cases
already decide the architecture-ceiling question.

## Frozen publish-boundary evidence

Both deterministic checks were rerun after the school-538 top-up and made 54 requests (list,
detail, and compare, BG and EN), with zero LLM calls:

- tracked stored-run audit: 54/54 HTTP 200, `passed=true`, no withheld-pricing leaks;
- explicit payload scan: zero serialized summaries, zero website-derived admission values, and
  zero scraped-pricing rows.

See [`boundary-audit.json`](boundary-audit.json) and
[`payload-boundary-audit.json`](payload-boundary-audit.json).

## User decision

- Blocker-list judgment: [ ] frontier passes  [x] frontier fails  [ ] run is insufficient
  — all five reviewable blocker cases (538, 570, 153-pricing, 105, 153-admission) fail with
  output identical or equivalent to the cheap tier. **Verdict: architecture ceiling, not a
  model-capability ceiling.** (User review, 2026-07-17.)
- Value-model candidates approved for a later run: **none** — with frontier output identical
  to the cheap tier on the blocker cases, a cheaper model cannot do better than "also
  identical"; the value question is void until the extraction architecture changes (E1).
- Extraction model selected for the full refresh: **current cheap tier, unchanged.**

No value-model run or P2.13 work was started. The school-538 top-up is the final permitted model
call for this ceiling evaluation.
