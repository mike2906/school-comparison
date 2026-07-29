# Proposal: school site groups

**Status: proposal only.** Nothing in this document has been applied. No schema change,
no migration, no database write, and no pipeline run was performed to produce it. The
groups below are derived read-only from existing rows by
`backend/scripts/derive_school_groups.py`.

## The problem

The MoE registry models one school as several legal entities — a kindergarten `ЕООД`, a
primary `ЕООД`, a gymnasium `ЕООД` — each with its own registry row. The school itself
runs one website, because it is one school.

That mismatch shows up everywhere downstream:

- **40 of the 138** website-backed private/international schools share a domain with a
  sibling entity — 29% of the cohort, across 19 domains.
- **Identity.** Page evidence on a shared site cannot say which entity it names. The
  bounded adjudication pilot (P2.14(e)) failed exactly here: the model accepted
  `Uwekind International School` for kindergarten 634, whose cached pages are the same
  pages as sibling school 635, and only the education-level guard stopped it.
- **Pricing.** The same ambiguity applies to fee tables on a shared site, where a fee
  attached to the wrong sibling misinforms a parent about money. This is the E1
  entity-scoping failure mode, arriving through a different door.
- **Product.** A parent searching `Петър Берон` gets two cards at one address instead of
  one school serving ages 3–14.

## What is proposed

A group relationship — one organisation, many registry entities. **Not** a merge: NVO
results, admission thresholds, and pricing all attach per legal entity and must stay
attached to their own rows. Each entity keeps its ID, level, exam results, and fees; the
group carries what the organisation owns, principally its name.

## Derivation rule

Two schools belong to the same group when all three hold:

1. Same registrable domain (`school.example.bg` and `example.bg` are one domain).
2. One entity's normalised brand contains the other's, after stripping the legal form
   (`ЕООД`, `ООД`, `АД`, `СДРУЖЕНИЕ`) and the institution-type words (`ЧАСТНА`,
   `ДЕТСКА ГРАДИНА`, `УЧИЛИЩЕ`, `ГИМНАЗИЯ`, `ПРОФИЛИРАНА`, …).
3. Either the levels are complementary (a level split) or the brand repeats across
   addresses (named campuses).

Rule 2 is intentionally strict, which is why four groups fall out for human review rather
than being guessed at.

## Result

| Outcome | Domains | Entities |
| --- | --- | --- |
| Derived mechanically | 15 | 31 |
| Judged from site evidence — group | 3 | 7 |
| Judged from site evidence — operator, do not group | 1 | 2 |
| **Total** | **19** | **40** |

## How the site presents each entity

The deciding evidence for a contested group is how the shared site names its own entities.
`derive_school_groups.py` reports this as `presented_as`: the best-matching multi-word
phrase from an entity's registry brand found in the group's combined cached text, in the
original script or transliterated, with its hit count.

The question it answers is **presentation, not ownership** — does the site brand these
entities as one school, or list them as separate offerings?

**Read `not found` carefully.** It means the site does not use that entity's *registry*
brand, which happens for three different reasons: the entity is a legal shell whose public
identity is the group's (school 150), the school brands itself in another language
(`Maple Bear` for `Канадско мече`, `BRITANICA` for `Британика`), or the registry spelling
simply differs from the site's. It is a display-name signal, never a grouping signal, and
it is not evidence against a group.

## Groups derived mechanically (15)

Same brand on one domain, complementary levels or named campuses. These need confirmation,
not judgement.

### `britanica-parkschool.bg` — 2 entities

Signals: shared brand, same address, distinct levels, published EN: `BRITANICA Park School`

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 515 | kindergarten | ЧАСТНА ДЕТСКА ГРАДИНА БРИТАНИКА" ООД | кв. Драгалевци, ул. "Момино венче" № 2 | **not found** |
| 575 | upper sec. | ЧАСТНО СРЕДНО ЕЗИКОВО УЧИЛИЩЕ БРИТАНИКА" ООД | кв. Драгалевци, ул. "Момино венче" № 2 | **not found** |

### `drujba.org` — 2 entities

Signals: shared brand, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 522 | upper sec. | ЧАСТНО СРЕДНО УЧИЛИЩЕ "ДРУЖБА" - СОФИЯ" ЕООД | район Връбница, ж. к. Обеля 2, ул. 106 | **not found** |
| 587 | kindergarten | ЧАСТНА ДЕТСКА ГРАДИНА "ДРУЖБА-СОФИЯ" ЕООД | ж. к. Обеля 2, ул. "106" № 3 | **not found** |

### `e-kestner.eu` — 2 entities

Signals: shared brand, same address, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 583 | upper sec. | Частна немска гимназия Ерих Кестнер" ООД | ж. к. Люлин 6 | `ерих кестнер` ×165 |
| 584 | lower sec. | Частно основно училище Ерих Кестнер" ООД | ж. к. Люлин 6 | `ерих кестнер` ×165 |

### `eduteh.eu` — 2 entities

Signals: shared brand, same address, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 381 | lower sec. | Частно основно училище Образователни технолигии" ЕООД | район Слатина, СПЗ Слатина-юг, ул. "Ге | **not found** |
| 550 | upper sec. | ЧАСТНА ПРОФИЛИРАНА ГИМНАЗИЯ ОБРАЗОВАТЕЛНИ ТЕХНОЛОГИИ" ЕО | район Слатина, СПЗ Слатина-юг, ул. "Ге | `образователни технологии` ×60 |

### `ezikovsviat.com` — 2 entities

Signals: shared brand, same address, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 213 | upper sec. | ЧАСТНА ПРОФИЛИРАНА ГИМНАЗИЯ С ЧУЖДОЕЗИКОВО ОБУЧЕНИЕ "ЕЗИ | район Оборище, ул. "Триадица" № 5А | `езиков свят` ×160 |
| 215 | lower sec. | ЧАСТНО ОСНОВНО УЧИЛИЩЕ "ЕЗИКОВ СВЯТ" ЕООД | район "Оборище", ул. "Триадица" № 5А | `езиков свят` ×160 |

### `maplebear.bg` — 2 entities

Signals: shared brand, distinct levels, published EN: `Maple Bear`, `Maple Bear Sofia`

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 556 | kindergarten | ЧАСТНА ДЕТСКА ГРАДИНА КАНАДСКО МЕЧЕ" ООД | кв. Витоша, ул. "Йордан Стубел" № 16 | **not found** |
| 596 | lower sec. | Частно основно училище Канадско мече" ООД | ул. "Панорамен път" № 38 | **not found** |

### `montessorischools.bg` — 2 entities

Signals: shared brand, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 589 | kindergarten | ЧАСТНА МОНТЕСОРИ ДЕТСКА ГРАДИНА ОТКРИВАТЕЛ" ООД | ул. "Сири дол" № 15В | **not found** |
| 610 | upper sec. | ЧАСТНО СРЕДНО УЧИЛИЩЕ ОТКРИВАТЕЛ" ЕООД | ул. "Болград" № 6 | `откривател` ×116 |

### `nemo-bg.com` — 2 entities

Signals: shared brand

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 185 | kindergarten | Частна детска градина "НЕМО" ООД | район Витоша, ж. к. Драгалевци, ул. "М | `nemo` ×276 |
| 577 | kindergarten | Частна детска градина НЕМО - Бояна" ООД | ул. "Александър Пушкин" № 63 | **not found** |

### `pberon.com` — 2 entities

Signals: shared brand, same address, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 537 | lower sec. | ЧАСТНО ОСНОВНО УЧИЛИЩЕ "Д-Р ПЕТЪР БЕРОН" ЕООД | гр. София, ул. Флора Кънева №14 | `петър берон` ×87 |
| 546 | kindergarten | ЧАСТНА ДЕТСКА ГРАДИНА "Д-Р ПЕТЪР БЕРОН" ЕООД | гр. София, ул. Флора Кънева №14 | `петър берон` ×87 |

### `ruliceum.org` — 2 entities

Signals: shared brand, same address, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 591 | kindergarten | ЧАСТНА ДЕТСКА ГРАДИНА НИКАТОР" ЕООД | ул. "Проф. д-р Иван Странски" № 15 | `никатор` ×51 |
| 592 | upper sec. | ЧАСТНО СРЕДНО УЧИЛИЩЕ НИКАТОР" ЕООД | ул. "Проф. д-р Иван Странски" № 15 | `никатор` ×51 |

### `svetlina.net` — 2 entities

Signals: shared brand, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 214 | lower sec. | ЧАСТНО ОСНОВНО УЧИЛИЩЕ СВЕТЛИНА" ЕООД | бул. "Симеоновско шосе" № 59 | `svetlina` ×271 |
| 510 | kindergarten | Частна детска градина Светлина" ЕООД | район Студентски, ул. "Йозеф Валдхард" | `svetlina` ×271 |

### `transform.bg` — 3 entities

Signals: shared brand

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 533 | lower sec. | ЧАСТНО ОСНОВНО УЧИЛИЩЕ ПРОГРЕСИВНО ОБРАЗОВАНИЕ 3 - СОФИЯ | бул. "Христо Смирненски" № 1, УАСГ, бл | `прогресивно образование` ×14 |
| 606 | upper sec. | ЧАСТНА ПРОФИЛИРАНА ГИМНАЗИЯ "ПРОГРЕСИВНО ОБРАЗОВАНИЕ - С | ул. "Нишава" № 107 | `прогресивно образование` ×14 |
| 633 | lower sec. | ЧАСТНО ОСНОВНО УЧИЛИЩЕ "ПРОГРЕСИВНО ОБРАЗОВАНИЕ" ЕООД | ул. "Нишава" № 107 | `прогресивно образование` ×14 |

### `waldorf.bg` — 2 entities

Signals: shared brand, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 514 | kindergarten | Частна детска градина "Професор Николай Райнов" ЕООД | район Лозенец, ул. "Горски пътник" № 4 | `николай райнов` ×86 |
| 630 | upper sec. | СДРУЖЕНИЕ "ЧАСТНО СРЕДНО УЧИЛИЩЕ "ПРОФЕСОР НИКОЛАЙ РАЙНО | ул. "Горски пътник" № 44 | `николай райнов` ×86 |

### `wedaschule.com` — 2 entities

Signals: shared brand, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 372 | kindergarten | ЧАСТНА ДЕТСКА ГРАДИНА ВЕДА" ООД | район Илинден, ул. "Кукуш" № 2 | `веда` ×203 |
| 534 | upper sec. | ЧАСТНО СРЕДНО УЧИЛИЩЕ С НЕМСКИ ЕЗИК ВЕДА" ООД | ул. „Иван Мърквичка“ № 1 1220 гр. Софи | `немски език` ×72 |

### `zlatarskischool.org` — 2 entities

Signals: shared brand, distinct levels, published EN: `Zlatarski International School`

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 151 | lower sec. | ЧАСТНО ОСНОВНО УЧИЛИЩЕ "Проф. д-р Васил Златарски" ЕООД | район Студетски, бул. "Св. Климент Охр | **not found** |
| 392 | upper sec. | ЧАСТНА ЕЗИКОВА ГИМНАЗИЯ "Проф. д-р Васил Златарски" ЕООД | район Студентски, бул. "Св. Климент Ох | **not found** |

## Groups judged from site evidence (4)

### `novigradini.com` — 2 entities

Signals: none

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 574 | kindergarten | ЧАСТНА ДЕТСКА ГРАДИНА АНИКА" ООД | ж.к. Младост 4, бул. "Александър Малин | `аника` ×154 |
| 628 | kindergarten | ЧАСТНА ДЕТСКА ГРАДИНА - ПРИКАЗКА БЕЗ КРАЙ 1" ЕООД | ж.к. Люлин-9, бл. 964 | `приказка без край` ×112 |

**Recommendation: do NOT group.** Both brands are separately and heavily named
(`аника` ×154, `приказка без край` ×112) — two schools in different neighbourhoods
(Младост 4 and Люлин 9) on a generic operator domain meaning "new kindergartens", whose
root page lists them side by side with separate opening hours. Shared ownership, not
shared identity; neither name may propagate. If linked at all, it should be a weaker
*operator* relation used for page scoping and provenance, never for names.

### `softuni.bg` — 2 entities

Signals: none

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 150 | upper sec. | ЧАСТНА ПРОФЕСИОНАЛНА ГИМНАЗИЯ ПО ПРОГРАМИРАНЕ И РОБОТИКА | район Слатина, ул. "Кадемлия" № 15 | **not found** |
| 595 | upper sec. | Частна професионална гимназия по дигитални науки СофтУни | ж. к. Младост 2, бул. "Александър Мали | `софтуни будител` ×158 |

**Recommendation: group. High confidence — and it inverts an assumption.** Both entities
sit on `buditel.softuni.bg`, which names `софтуни будител` ×158 and **never names school
150's registry brand at all**. (The word `програмиране` occurs 97 times on this
programming school's site without ever naming the entity, which is why the signal matches
multi-word brands only.) So `ЧПГ по програмиране и роботика "Стив Джобс"` is a legal shell
and `СофтУни БУДИТЕЛ` is the public identity — here the group name is *more* correct for
display than the entity's own registry name, which the current display logic treats as the
trustworthy fallback.

### `tzarsimeon.bg` — 2 entities

Signals: same address, distinct levels

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 558 | lower sec. | ЧАСТНО ОСНОВНО УЧИЛИЩЕ ЦАР СИМЕОН ВЕЛИКИ" ЕООД | ул. "Св. Св. Кирил и Методий" № 66 | `цар симеон велики` ×123 |
| 565 | upper sec. | ЧАСТНА ГИМНАЗИЯ ПО ПРИРОДНИ НАУКИ И ПРЕДПРИЕМАЧЕСТВО "АС | ул. "Св. Св. Кирил и Методий" № 66 | `асен йорданов` ×22 |

**Recommendation: group. High confidence.** Both entities are configured to the same root
URL, both names appear on that one site (`цар симеон велики` ×123, `асен йорданов` ×22),
and they share an address — a basic school and a gymnasium on one campus. The gymnasium
keeps its own name: the group must not overwrite `Асен Йорданов`.

### `uwekind.com` — 3 entities

Signals: distinct levels, published EN: `Uwekind International School`

| ID | Level | Registry name | Address | Registry brand on site |
| --- | --- | --- | --- | --- |
| 153 | primary | ЧАСТНО НАЧАЛНО УЧИЛИЩЕ ЛОЗЕН ЕООД | СОФИЯ | БОЯНА | УЛ.ДЕЯН ГЬОРГОВ №4, 16 | `лозен` ×35 |
| 634 | kindergarten | ЧАСТНА НЕМСКА ДЕТСКА ГРАДИНА УВЕКИНД" ООД | ул. "Крум Попов" № 69 | **not found** |
| 635 | upper sec. | ЧАСТНО СРЕДНО УЧИЛИЩЕ УВЕКИНД" ООД | с. Лозен, ул. "Лозен парк" № 1 | `увекинд` ×186 |

**Recommendation: group. Medium-high confidence.** All three entities point at the same
site, and a school does not publish itself on another organisation's domain.
The evidence also shows *why this group needs rule 2 below*: the German kindergarten's own
brand appears nowhere (`НЕМСКА УВЕКИНД` not found) while the group brand dominates
(`увекинд` ×186). An unqualified inheritance rule would hand the kindergarten
`Uwekind International School` — the exact error the adjudication pilot caught.
*Weakness in the evidence:* the 35 hits for `лозен` cannot separate the place from the
brand, because the secondary's campus is in с. Лозен. School 153's address is also stored
as `СОФИЯ | БОЯНА | УЛ.ДЕЯН ГЬОРГОВ №4, 1616 СТОЛИЧНА` — pipe-separated and upper-cased,
reading as a garbled scrape, and a school named `Лозен` registered in Boyana is worth
checking on its own.
## Two rules these cases establish

1. **Presentation, not ownership, decides a group.** A group shares an identity; an
   operator merely shares a landlord. Only a group may let a name propagate.
   `novigradini.com` is the case this protects.
2. **A group name is a fallback, never an override, and never crosses a level boundary.**
   An entity the site names keeps its own name (`Асен Йорданов`). An entity the site never
   names inherits the group *brand* qualified by its own level — school 150 becomes
   `СофтУни БУДИТЕЛ`, while kindergarten 634 becomes Uwekind's kindergarten and **never**
   `Uwekind International School`. An inherited name whose institution word contradicts the
   entity's level must be rejected. That is the education-level guard from the adjudication
   pilot, restated where it belongs: as a property of the group model rather than a
   heuristic each field re-derives.

## Data-quality findings surfaced on the way

- **`eduteh.eu` holds a registry typo.** School 381 is recorded as
  `Образователни технолигии`; school 550 as `Образователни технологии`. Same brand, same
  address, one misspelling — which is why the strict brand rule could not join them, and
  why 381's brand shows as `not found` on its own site. Grouped above on the corrected
  reading; the registry value itself is left untouched.
- **School 150's `website_url` points at a subpage**, `buditel.softuni.bg/teachers/`,
  where the subdomain root is the school's own homepage and is already cached and valid.
  Schools 608 and 628 also carry subpage URLs, but those are correct: both are
  entity-specific pages on a multi-entity site.
- **School 153's address looks garbled** (see the `uwekind.com` note), independent of any
  grouping decision.

## What a group would pay for

- **Names.** The organisation carries the English name where the site publishes one;
  entity rows inherit it under rule 2 rather than each re-deriving it from ambiguous
  shared pages.
- **Pricing.** Fees extracted from a shared site scope to the group, and level-specific
  rows attach to the entity whose level they name. Without this, every shared-domain fee
  table is a coin flip between siblings — the E1 entity-scoping failure arriving through a
  different door, where a wrong number misinforms a parent about money.
- **Product.** Grouped results show one school with an age range, which is what a parent
  is looking for.

## Explicit non-goals

- No merging of registry rows, and no change to how NVO results, admission thresholds, or
  pricing attach to their entities.
- No new publication path. A group name would still have to pass the existing corroboration
  gate and `identity_curation` before anything reaches the API.
- No inference of a group from shared *hosting* alone.

## How to check this

```bash
cd backend && uv run python scripts/derive_school_groups.py
```

Read-only: it opens a session, reads schools, locations, and cached valid pages, and prints
JSON. It performs no write, no refresh, no provider call, and no geocode.
