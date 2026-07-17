# P2.10(a) Gemini ceiling-run blocker review

Run: `396dffe5-c905-44e2-8582-1d4c1bcdbd66`

Comparison baseline: `9d2c837f-aaf9-4924-a884-899f9cbcf4e0`

Model: `google/gemini-3.1-pro-preview` (plain variant)

Route: Google AI Studio only; provider fallbacks disabled; `require_parameters=true`

Prompt changes: none

## Read this first

This is a **partial review packet**, not a blocker-list verdict. The run was stopped during
extraction to preserve the user-approved `$1.00` hard session budget. It completed URL
validation and navigation for all 18 schools, then persisted 9 of 18 extractions. Stage 6
validation and Stage 7 summarization were not reached. No second run and no other model were
used.

The manual reviewer should not interpret an unavailable new value as a pass or a fail.

## Run accounting and retry evidence

| Item | Exact result |
|---|---|
| OpenRouter key usage before | `$9.55274413` |
| OpenRouter key usage after settlement | `$10.39444013` |
| Provider-accounted run cost | **`$0.841696`** |
| Budget remaining | `$0.158304` |
| Pipeline status | `PARTIAL` |
| Stage outcomes | URL validation 18/18; navigation 18/18; extraction 9/18 persisted; validation not reached; summarization not reached |
| Persisted fresh extractions | `105, 153, 404, 454, 460, 516, 570, 584, 631` |
| No fresh extraction | `103, 233, 297, 324, 440, 506, 529, 538, 610` |
| Token accounting | Unavailable: the timed-out/interrupted requests did not return OpenRouter usage payloads. This is not recorded as zero. |
| Visible call failures | Four blank `Extraction call failed (PriceExtractionOutput)` warnings before termination; the log did not identify them as typed schema retries versus provider timeouts. |

The exact provider delta is authoritative for cost. The missing token split is an observed
failure of accounting under interrupted requests; no token estimate has been substituted.

## Pricing association cases

### School 538 — transport / tuition association

| `9d2c837f` stored output | Ceiling-run stored output |
|---|---|
| `TUITION 510 EUR MONTHLY`; `TRANSPORT 26 EUR MONTHLY`; `TRANSPORT 160 EUR MONTHLY`; `TRANSPORT 580 EUR MONTHLY`; extracurricular rows `13/23/25/25/30/33/33 EUR MONTHLY`. The documented blocker says 26 is after-hours care, 160 is transport, and 580 is nursery tuition. | **Unavailable.** School 538 finished navigation but did not persist a Gemini extraction. Its database rows still carry the later cheap-tier timestamp `2026-07-15 14:36:02Z`, so they are not ceiling-run evidence. |

Manual verdict: [ ] pass  [ ] fail  [ ] not reviewable

### School 570 — registration / deposit / meals headings

| `9d2c837f` stored output | Fresh Gemini stored output (`2026-07-17 10:54:01Z`) |
|---|---|
| `TUITION`: `180 MONTHLY`, `680 MONTHLY`, `4122 TERM`, `7820 YEARLY`; `REGISTRATION`: `100`, `300`, `850`, `1000` `ONE_TIME`; `MATERIALS 80 YEARLY` (all EUR, no age group). | `TUITION`: `180 MONTHLY`, `680 MONTHLY`, `4122 TERM`, `7820 YEARLY`; `REGISTRATION`: `100`, `300`, `850`, `1000` `ONE_TIME`; `MATERIALS 80 YEARLY` (all EUR, no age group). |

The old and fresh tuple sets are identical. This is a factual diff only; the user decides the
blocker verdict against the source headings.

Manual verdict: [ ] pass  [ ] fail  [ ] not reviewable

### School 153 — combined Uwekind page entity / plan association

| `9d2c837f` stored output | Fresh Gemini stored output (`2026-07-17 10:53:36Z`) |
|---|---|
| `TUITION`: `7000`, `7400`, `8100`, `8250` yearly; `FOOD`: `370`, `550`, `1260`, `1450`, `1480` yearly; `REGISTRATION`: `100`, `150`, `500` one-time. All EUR; every age group is null; no entity/plan applicability is retained. | `TUITION`: `7000`, `7400`, `8100`, `8250` yearly; `FOOD`: `370`, `550`, `1260`, `1450`, `1480` yearly; `REGISTRATION`: `100`, `150`, `500` one-time. All EUR; every age group is null; no entity/plan applicability is retained. |

The old and fresh tuple sets are identical. This is a factual diff only; the user decides the
entity-association verdict against the combined source page.

Manual verdict: [ ] pass  [ ] fail  [ ] not reviewable

## Admission-fragment cases

These are raw stored extraction values. They remained unpublished; see the boundary audit below.

### School 103

| `9d2c837f` stored output | Ceiling-run stored output |
|---|---|
| `entrance_requirements`: `здравно-профилактична карта на детето`; `попълнена от личния лекар`; `изследвания на кръв и урина`; `извършени в едноседмичен срок преди постъпване на детето в детската градина`; `данни от личния лекар`; `че на детето са извършени задължителните имунизации за възрастта`; `при отсъствие за повече от 2 месеца – еднократен отрицателен резултат за чревни паразити`. | **Unavailable.** Navigation completed, but no Gemini extraction persisted; the old extracted value remains in storage. |

Manual verdict: [ ] pass  [ ] fail  [ ] not reviewable

### School 105

| `9d2c837f` stored output | Fresh Gemini stored output (`2026-07-17 10:52:31Z`) |
|---|---|
| `entrance_requirements`: `Деца`; `на които не са извършени задължителните имунизации за възрастта не се приемат в ДГ № 4 „Слънчо“.` | `entrance_requirements: []`; `required_documents`: `Заявление за записване`, `Оригинал на удостоверението за раждане на детето`, `Документи за самоличност на родителите/настойника`, `Нова здравна карта за дете с данни за имунизационния статус`, `1. Оригинал на удостоверението за раждане на детето`; `application_steps`: `Подаване на заявление и декларации по образец в основната сграда`, `се извършва само чрез писмено заявление`, `Заявление за преминаване към самостоятелна организация –`, `Заявление за достъп до лични данни –`, `ДГ4 – Заявление за достъп до обществена информация –`, `ДГ4 – Протокол за приемане на устно заявление за достъп до обществена информация –`. |

Manual verdict: [ ] pass  [ ] fail  [ ] not reviewable

### School 153

| `9d2c837f` stored output | Fresh Gemini stored output (`2026-07-17 10:53:36Z`) |
|---|---|
| `entrance_requirements`: `Сесията включва три етапа - задачи в писмен вид`; `интервю и изпращане на обратна връзка за кандидата.`; `Интервю – комуникативни и социални умения`; `мотивация за учене.` | The same four `entrance_requirements` fragments. Additionally, `application_steps` stores `Административна такса за кандидатстване за прием` and `Такса кандидатстване:`. |

Manual verdict: [ ] pass  [ ] fail  [ ] not reviewable

### School 506

| `9d2c837f` stored output | Ceiling-run stored output |
|---|---|
| `entrance_requirements`: `Children must be able to provide self-care independently (e.g. toileting`; `changing clothes)`; `application_deadlines`: `The admission procedure may require placement tests and/or interviews.` | **Unavailable.** Navigation completed, but no Gemini extraction persisted; the old extracted value remains in storage. |

Manual verdict: [ ] pass  [ ] fail  [ ] not reviewable

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

Extraction cleared `summary_i18n` for the nine freshly extracted schools
(`105,153,404,454,460,516,570,584,631`). The other nine retain pre-run summaries and are not
Gemini evidence. Because Stage 7 was never reached, summary identity/tone is not reviewable from
this run.

Manual verdict: [ ] pass  [ ] fail  [ ] not reviewable

## Frozen publish-boundary evidence

Both deterministic checks made 54 requests (list, detail, and compare, BG and EN), with zero LLM
calls:

- tracked stored-run audit: 54/54 HTTP 200, `passed=true`, no withheld-pricing leaks;
- explicit payload scan: zero serialized summaries, zero website-derived admission values, and
  zero scraped-pricing rows.

See [`boundary-audit.json`](boundary-audit.json) and
[`payload-boundary-audit.json`](payload-boundary-audit.json).

## User decision

- Blocker-list judgment: [ ] frontier passes  [ ] frontier fails  [ ] run is insufficient
- Value-model candidates approved for a later run: ____________________
- Extraction model selected for the full refresh: ____________________

No value-model run or P2.13 work was started in this session.
