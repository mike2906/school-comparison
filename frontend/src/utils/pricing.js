import { intlLocale } from './format.js'
import { gradeBandLabel, gradeRangeInText, gradesInText, gradesNamedIn, rowGradeBand } from './gradeBand.js'
import { isClosedGroupFee, isHalfDay, isOnePayment, priceCategory, pricePeriod, priceTerms, rowAgeGroupKeys, rowsForAgeGroup } from './priceRows.js'
import { planNameLabel, stageLabel, websiteWords } from './priceLabels.js'

/**
 * Academic-year cohort helpers for pricing display.
 *
 * The API classifies every published price as `current`, `dated_other` or `not_stated`.
 * A headline price and the year badge beside it must always come from the same cohort —
 * mixing them is how a current-year label ends up sitting above last year's fee.
 */

export const YEAR_STATUS = {
  CURRENT: 'current',
  DATED_OTHER: 'dated_other',
  NOT_STATED: 'not_stated',
  // Set here, not by the API: a `dated_other` year that is the one families now enrol for.
  NEXT: 'next',
}

/**
 * The first calendar year of the academic year families are enrolling for on `today`.
 *
 * In September that is the year just starting. From October the year in progress is
 * settled and schools take enrolments for next September, so it is the next one
 * (October 2026 → 2027/2028, March 2027 → 2027/2028).
 */
export function intakeYearStart(today = new Date()) {
  const month = today.getMonth()
  const year = today.getFullYear()
  return month > 8 ? year + 1 : year
}

// The age groups a set of tuition rows names.
function namedGroups(rows) {
  return new Set(rows.flatMap(row => [...rowAgeGroupKeys(row)]))
}

/** True for a `dated_other` row or group whose year is the one families now enrol for. */
function isIntakeYear(entry, today) {
  return entry?.yearStatus === YEAR_STATUS.DATED_OTHER || entry?.year_status === YEAR_STATUS.DATED_OTHER
    ? yearStartOf(entry) === intakeYearStart(today)
    : false
}

/** The first calendar year of a row's or cohort's academic year (2026 for 2026/2027), else -1. */
export function yearStartOf(row) {
  const canonical = row?.academic_year_canonical ?? row?.academicYear
  if (!canonical) return -1
  const start = Number(String(canonical).split('/')[0])
  return Number.isFinite(start) ? start : -1
}

/**
 * Pick the cohort a school's headline pricing should be drawn from.
 *
 * Preference: the year families now enrol for (`intakeYearStart`) when it is not the
 * current one and has whole-fee tuition (for `ageGroup` when given), else the current
 * year, else prices the school published without a year, else the most recent dated
 * year. A year with no tuition (for `ageGroup` when given) gives way to the next one that
 * has some: 593's 2026/27 page prices grades 8–12 only, its undated page grades 1–7.
 * With no age group, the groups only an undated page prices join the chosen year's rows
 * (`otherYears` is then true), so 593's headline is not its grades 8–12 fee alone.
 * `intake: false` skips the first step. Returns null when there is nothing publishable.
 */
export function selectPricingCohort(pricing = [], { ageGroup = null, intake = true, today = new Date() } = {}) {
  if (!Array.isArray(pricing) || pricing.length === 0) return null

  if (intake) {
    const next = pricing.filter(row => isIntakeYear(row, today))
    const nextTuition = tuitionRows(next, ageGroup)
    // With no group chosen, next year leads only when it prices every group this year
    // does: a grade 1 fee for next year must not replace a grades 1–12 range.
    const thisYear = ageGroup ? [] : tuitionRows(pricing.filter(row => row.year_status === YEAR_STATUS.CURRENT))
    const covered = namedGroups(nextTuition)
    if (nextTuition.length > 0 && [...namedGroups(thisYear)].every(key => covered.has(key))) {
      return {
        rows: next,
        academicYear: next[0].academic_year_canonical || next[0].academic_year || null,
        yearStatus: YEAR_STATUS.NEXT,
        otherYears: false,
      }
    }
  }

  const cohorts = yearCohorts(pricing)
  if (cohorts.length === 0) return null
  const chosen = cohorts.find(cohort => tuitionRows(cohort.rows, ageGroup).length > 0) || cohorts[0]
  if (ageGroup) return { ...chosen, otherYears: false }

  // Groups the chosen year does not price, from the school's undated prices. Rows naming no
  // group never join: they may be the chosen year's fee read without its year.
  const chosenTuition = tuitionRows(chosen.rows)
  const covered = namedGroups(chosenTuition)
  const added = []
  // A fee this year that names no group covers every group: nothing joins it.
  if (chosen.yearStatus === YEAR_STATUS.CURRENT && chosenTuition.every(row => rowAgeGroupKeys(row).size > 0)) {
    const undated = cohorts.find(cohort => cohort.yearStatus === YEAR_STATUS.NOT_STATED)
    tuitionRows(undated?.rows || []).forEach(row => {
      const keys = [...rowAgeGroupKeys(row)]
      if (keys.length > 0 && !keys.some(key => covered.has(key))) added.push(row)
    })
  }
  return { ...chosen, rows: [...chosen.rows, ...added], otherYears: added.length > 0 }
}

// The current year, the undated prices and the latest past year, each when there are any,
// in the order a headline prefers them.
function yearCohorts(pricing) {
  const cohorts = []
  const current = pricing.filter(row => row.year_status === YEAR_STATUS.CURRENT)
  if (current.length > 0) {
    cohorts.push({
      rows: current,
      academicYear: current[0].academic_year_canonical || current[0].academic_year || null,
      yearStatus: YEAR_STATUS.CURRENT,
    })
  }
  const notStated = pricing.filter(row => row.year_status === YEAR_STATUS.NOT_STATED)
  if (notStated.length > 0) {
    cohorts.push({ rows: notStated, academicYear: null, yearStatus: YEAR_STATUS.NOT_STATED })
  }
  const dated = pricing.filter(row => row.year_status === YEAR_STATUS.DATED_OTHER)
  if (dated.length > 0) {
    const latestStart = Math.max(...dated.map(yearStartOf))
    const rows = dated.filter(row => yearStartOf(row) === latestStart)
    cohorts.push({
      rows,
      academicYear: rows[0].academic_year_canonical || rows[0].academic_year || null,
      yearStatus: YEAR_STATUS.DATED_OTHER,
    })
  }
  return cohorts
}

/**
 * Group every published price by academic year, newest first, undated last.
 *
 * Used by the detail page so a mixed-year price list is labelled per group instead of
 * inheriting one arbitrary row's year. The year families now enrol for is marked
 * `YEAR_STATUS.NEXT` rather than as another year. A row from a past year that a later year
 * lists again (same category, period, age groups and amount) is dropped.
 */
export function groupPricingByAcademicYear(pricing = [], { today = new Date() } = {}) {
  if (!Array.isArray(pricing) || pricing.length === 0) return []

  const groups = new Map()
  pricing.forEach(row => {
    const key = row.academic_year_canonical || ''
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        academicYear: row.academic_year_canonical || null,
        yearStatus: row.year_status || YEAR_STATUS.NOT_STATED,
        rows: [],
      })
    }
    groups.get(key).rows.push(row)
  })
  groups.forEach(group => {
    if (isIntakeYear(group, today)) group.yearStatus = YEAR_STATUS.NEXT
  })
  // Last year's row for a fee a later year lists again at the same amount (same category,
  // period and age groups) is history, so it is left out: 596's 2025-26 bus fees under the
  // same 2026-27 ones read as two prices. A fee that changed price, or that no later year
  // repeats, still shows under its own year.
  const sameFee = row => {
    const amounts = [row.amount, row.amount_min, row.amount_max]
    if (!amounts.some(value => value != null)) return null
    const ageGroups = [...rowAgeGroupKeys(row)].sort().join(',')
    return [row.category, row.currency, pricePeriod(row) || '', ageGroups, ...amounts].join('|')
  }
  const latest = new Map()
  pricing.forEach(row => {
    const key = sameFee(row)
    if (key) latest.set(key, Math.max(latest.get(key) ?? -1, yearStartOf(row)))
  })
  const repeatedLater = row => {
    const key = sameFee(row)
    return key !== null && row.year_status === YEAR_STATUS.DATED_OTHER && !isIntakeYear(row, today) &&
      latest.get(key) > yearStartOf(row)
  }
  groups.forEach(group => {
    group.rows = group.rows.filter(row => !repeatedLater(row))
    if (group.rows.length === 0) groups.delete(group.key)
  })

  return [...groups.values()].sort((a, b) => {
    if (!a.academicYear) return 1
    if (!b.academicYear) return -1
    return Number(b.academicYear.split('/')[0]) - Number(a.academicYear.split('/')[0])
  })
}

// Every category the API sends, and the deposits listed apart from registration fees
// (`priceCategory`), in the order fees are listed (tuition first), then a catch-all.
export const PRICE_CATEGORY_ORDER = [
  'tuition',
  'registration',
  'deposit',
  'food',
  'transport',
  'extended_day',
  'activities',
  'extracurricular',
  'materials',
  'uniforms',
  'camp',
  'other',
]

/** Rows in listing order by category, tuition first; the order within a category kept. */
export function sortByCategory(rows = []) {
  const rank = (row) => {
    const index = PRICE_CATEGORY_ORDER.indexOf(priceCategory(row))
    return index === -1 ? PRICE_CATEGORY_ORDER.length : index
  }
  return rows.map((row, index) => ({ row, index }))
    .sort((a, b) => rank(a.row) - rank(b.row) || a.index - b.index)
    .map(({ row }) => row)
}

const INSTALLMENT_PLAN_NAME_RE = /(\d+\s*(installment|monthly|вноск)|(installment|monthly|вноск)\s*\d+)/i
// One instalment named by its place: "Годишна такса – първа вноска", "II вноска",
// "2-ра вноска", "Вноска № 1". Not "Месечна вноска" or "Еднократна вноска", which are the
// whole fee.
const ONE_INSTALLMENT_RE = /(?<![\p{L}\p{N}])(?:първа|втора|трета|четвърта|последна|(?:i{1,3}|iv|\d{1,2})(?:-?(?:ва|ра|та|ма))?)\s+вноска(?![\p{L}])|вноска\s*(?:№\s*)?(?:i{1,3}|iv|\d{1,2})(?![\p{L}\p{N}])|(?:first|second|third|final) instal+ment/iu

/**
 * True for a row that is one instalment named by its place ("вноска I, до 31.08.26"), so its
 * amount is that instalment's. Not "на 2 вноски", whose amount may be the plan's total.
 */
export function isSingleInstalment(item) {
  return Boolean(item?.plan_name) && !isOnePayment(item.plan_name) && ONE_INSTALLMENT_RE.test(item.plan_name)
}

/** True for a row describing one instalment of a fee rather than the whole fee. */
export function isInstallmentPlan(item) {
  if (!item?.plan_name || isOnePayment(item.plan_name)) return false
  return INSTALLMENT_PLAN_NAME_RE.test(item.plan_name) || ONE_INSTALLMENT_RE.test(item.plan_name)
}

function priceBase(row) {
  if (row?.amount_min != null) return row.amount_min
  if (row?.amount != null) return row.amount
  return row?.amount_max ?? null
}

/**
 * A fee's per-month equivalent, or null when it cannot honestly be expressed per month.
 *
 * Only an explicit monthly, yearly or quarterly period converts. A semester, term or
 * one-off fee, or one whose period the school did not state, has no defensible monthly
 * figure; treating it as monthly would present a €4,725 semester fee as €4,725 a month.
 */
export function monthlyEquivalent(row) {
  const base = priceBase(row)
  if (base == null) return null
  const period = pricePeriod(row)
  if (period === 'monthly') return base
  if (period === 'yearly') return base / 12
  if (period === 'quarter') return base / 3
  return null
}

// Bulgaria adopted the euro on 2026-01-01 at this irrevocably fixed rate.
export const BGN_PER_EUR = 1.95583

/** An amount in euro, or null when the currency has no fixed conversion to euro. */
export function toEur(amount, currency) {
  if (amount == null) return null
  const value = Number(amount)
  if (!Number.isFinite(value)) return null
  if (!currency || currency === 'EUR') return value
  if (currency === 'BGN') return value / BGN_PER_EUR
  return null
}

// The order a headline picks a stated period in: parents budget per month, and a
// monthly fee is the figure most schools publish first.
const HEADLINE_PERIODS = ['monthly', 'quarter', 'term', 'semester', 'yearly']

// A total the school prints of several fees: "Общо" under tuition, activities and a
// development fee (633), "Total Fee for New Students" (506). Not a whole fee's own name
// ("Обща такса за обучение", "Total annual fee"), nor a total paid in instalments.
const STATED_TOTAL_RE = /^\s*(?:общо|обща\s+сума)(?![\p{L}])|^\s*total(?:\s+(?:fees?|cost))?\s*(?:$|[:(,–-]|for\b|per\b)/iu

/** True for a row that is the school's own total of several fees. */
export function isStatedTotal(row) {
  return [row?.plan_name, row?.pricing_context?.plan_name_en].some(text => text && STATED_TOTAL_RE.test(text))
}

// The headline's tuition rows: whole fees (not one instalment) that any new pupil pays, for the
// chosen age group when there is one. A special-needs or returning-pupil rate stays listed.
// Where the school prints a total of its fees for an age group, the total is what that group
// pays: tuition alone would show one line of it (633's 3,140 of 7,850 €).
export function tuitionRows(rows, ageGroup = null) {
  const whole = rows.filter(row => row.category === 'tuition' && !isInstallmentPlan(row) && !isClosedGroupFee(row))
  // A regular price naming the chosen group is that group's; one naming no group gives way
  // to it, as it may be another group's fee read without its group: 271's undated
  // kindergarten fee made grade 1 read "7,200–7,950". The fee list still shows it. A
  // printed total naming no group still covers every group (below).
  const candidates = rowsForAgeGroup(whole, ageGroup)
  const namesGroup = row => ageGroup != null && rowAgeGroupKeys(row).has(ageGroup)
  const forGroup = candidates.some(row => namesGroup(row) && !priceTerms(row))
    ? candidates.filter(row => namesGroup(row) || isStatedTotal(row))
    : candidates
  // A total that names no age group covers every group.
  const totalled = new Set(forGroup.filter(isStatedTotal).map(row => row.age_group || ''))
  const shown = forGroup.filter(row => isStatedTotal(row) || !(totalled.has('') || totalled.has(row.age_group || '')))
  // A price on other terms (a promotion, a discount, prepaid, paid in parts or at once) is
  // not the group's regular price: 625's 3-month promotion set its "from", 568's price for
  // paying in parts its "to". It counts only for a group with no regular price.
  // A regular price naming no group covers every group; a price on other terms naming none
  // gives way to any regular price.
  const group = row => row.age_group?.trim() || ''
  const regular = new Set(shown.filter(row => !priceTerms(row)).map(group))
  const hasRegular = row => regular.has('') || regular.has(group(row)) || (group(row) === '' && regular.size > 0)
  return shown.filter(row => !priceTerms(row) || !hasRegular(row))
}

// The rows a range runs from and to, so it can say what each end is; null for one price
// (to the euro: 454 EUR and 888 BGN show as one price).
function rangeEnds(rows, convert, range) {
  if (Math.round(range.min) === Math.round(range.max)) return null
  const low = rows.filter(row => convert(row.amount_min ?? row.amount ?? row.amount_max, row) === range.min)
  const high = rows.filter(row => convert(row.amount_max ?? row.amount ?? row.amount_min, row) === range.max)
  return low.length > 0 && high.length > 0 ? { low, high } : null
}

function rangeOf(rows, convert) {
  const values = rows.flatMap((row) => {
    const low = row.amount_min ?? row.amount ?? row.amount_max
    const high = row.amount_max ?? row.amount ?? row.amount_min
    return [low, high].map(value => convert(value, row))
  }).filter(value => value != null)
  return values.length > 0 ? { min: Math.min(...values), max: Math.max(...values) } : null
}

/**
 * A price for display: lev amounts in euro (Bulgaria's currency since 2026) at the fixed
 * rate, any other currency as it is. Returns `{ value, currency }`.
 */
export function displayPrice(value, currency) {
  if (currency === 'BGN') return { value: toEur(value, 'BGN'), currency: 'EUR' }
  return { value: value == null ? null : Number(value), currency: currency || 'EUR' }
}

/** An amount as currency text in the given locale, e.g. "€1,200"; null for no amount. */
export function formatCurrency(amount, locale, currency = 'EUR') {
  if (amount == null || Number.isNaN(amount)) return null
  return new Intl.NumberFormat(intlLocale(locale), {
    style: 'currency',
    currency,
    // Both bounds: older browsers throw when only the maximum is below the currency default.
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  }).format(amount)
}

/** One pricing row's amount or range, shown in euro like every other price surface. */
export function formatPriceLabel(price, locale, t) {
  const format = (value) => {
    if (value == null) return null
    const shown = displayPrice(value, price.currency)
    return formatCurrency(shown.value, locale, shown.currency)
  }
  const min = format(price.amount_min)
  const max = format(price.amount_max)
  const exact = format(price.amount)

  if (min && max) return min === max ? min : `${min}–${max}`
  if (min) return min
  if (max) return max
  if (exact) return exact
  return t('pricing.priceOnRequest')
}

// The grades free text names; a stage ("Гимназия", 8-12) narrowed to the grades the plan
// name writes inside it ("Гимназия / 8 – 10. клас" is grades 8-10).
function namedBand(text, planName) {
  const named = gradesNamedIn(text)
  const written = gradesInText(planName)
  if (named && !gradesInText(text) && written && written.from >= named.from && written.to <= named.to) return written
  return named
}

// The age group a row names in the UI language: a known `ageGroups` key, else the grades
// it names ("ПК" → "Preschool"), else the website's own words (`websiteWords`).
function ageGroupName(price, t, language) {
  const raw = price?.age_group?.trim()
  if (!raw) return null
  const key = `ageGroups.${raw}`
  const translated = t(key)
  if (translated && translated !== key) return translated
  const stage = stageLabel(raw, t)
  if (stage) return stage
  // Grades named in free text too: "Гимназия", or Buditel's whole sentence "Годишна такса за
  // 8. клас за учебната …".
  const band = rowGradeBand(price) || namedBand(raw, price.plan_name)
  return band ? gradeBandLabel(band, t) : websiteWords(price, 'age_group', language)
}

/**
 * The age group a pricing row names, or null when there is nothing worth showing.
 *
 * A price row's `age_group` is the text the school's website used ("ПК", "VIII - XII клас"),
 * not one of our `ageGroups` keys. A known key or grades we can read are shown as our own
 * label in the UI language; anything else as written.
 * Null when the plan name already carries it ("Grade 0 / Plan 1", or "8–12: …" for
 * grades 8–12), to avoid saying it twice.
 */
export function priceAgeGroupLabel(price, t, language) {
  const raw = price?.age_group?.trim()
  if (!raw) return null
  const key = `ageGroups.${raw}`
  const translated = t(key)
  if (translated && translated !== key) return translated
  // A plan name that already says it needs no repeat, unless it starts at preschool:
  // "PK"/"ПК" is an abbreviation parents may not know.
  const band = rowGradeBand(price)
  if (band?.from === 0) return ageGroupName(price, t, language)
  // Whole-word match, so "Grade 1" is still shown next to a "Grade 10" plan.
  const escaped = raw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  if (new RegExp(`(^|[^\\p{L}\\p{N}])${escaped}($|[^\\p{L}\\p{N}])`, 'iu').test(price.plan_name || '')) return null
  const named = gradeRangeInText(price.plan_name)
  if (band && named && band.from === named.from && band.to === named.to) return null
  return ageGroupName(price, t, language)
}

/**
 * A category's rows split by the age group each names, so a parent reads the options for
 * one grade band together instead of interleaved with another band's.
 *
 * Returns `[{ label, rows }]` in the order the bands first appear, rows in their own
 * order. Rows naming no age group come first with a null label. With fewer than two
 * bands nothing is split: one group with a null label, and each row keeps its own
 * age-group line.
 */
export function groupRowsByAgeGroup(rows = [], t, language) {
  const bands = new Map()
  const unnamed = []
  rows.forEach(row => {
    // Grouped by the site's own group, so two English wordings of one group stay one band.
    const identity = ageGroupName(row, t)
    if (!identity) {
      unnamed.push(row)
      return
    }
    const label = ageGroupName(row, t, language)
    const key = identity.toLocaleLowerCase()
    if (!bands.has(key)) bands.set(key, { label, rows: [] })
    bands.get(key).rows.push(row)
  })
  if (bands.size < 2) return rows.length > 0 ? [{ label: null, rows }] : []
  return [
    ...(unnamed.length > 0 ? [{ label: null, rows: unnamed }] : []),
    ...bands.values(),
  ]
}

/**
 * The tuition a school states for its headline cohort, in the period it states it in.
 *
 * Never annualised: a monthly fee multiplied by 12 overstates the year by up to a fifth
 * at a school that bills 10 months. Picks one stated period: the one of the cheapest fee
 * per month (so the headline agrees with `tuitionSortValue`), else monthly first, then
 * quarter, term, semester, year, else a fee whose period the school did not state
 * (`period: null`). A row's period is the stored one, else the one its own name states
 * ("ГОДИШНА ТАКСА"), so a fee is not left out of the range for a missed period. With an
 * `ageGroup`, only rows for that group (or naming none) count; null when the school lists
 * tuition only for other groups. EUR and BGN are shown in euro; another currency stays as
 * it is, never mixed.
 * When the headline is next year's fee (`YEAR_STATUS.NEXT`), `currentYear` carries the
 * current year's tuition the same way, or null when there is none.
 * `total` is true when the figure is the school's printed total of several fees, and
 * `optionalIncluded` lists the optional fees that total is known to add up.
 * `ends` holds the rows the range runs from and to (`rangeEndLabels`), null for one price.
 * `otherYears` is true when some groups' fees come from the school's undated prices
 * (`selectPricingCohort`), and `required` lists the extras the school says every child
 * pays on top (`requiredExtras`).
 * Returns `{ min, max, currency, period, ends, academicYear, yearStatus, otherYears,
 * required, total, optionalIncluded, currentYear? }` or null.
 */
export function statedTuition(pricing = [], { ageGroup = null, intake = true, today = new Date() } = {}) {
  const cohort = selectPricingCohort(pricing, { ageGroup, intake, today })
  if (!cohort) return null
  const meta = {
    academicYear: cohort.academicYear,
    yearStatus: cohort.yearStatus,
    otherYears: cohort.otherYears,
    required: requiredExtras(cohort.rows, ageGroup),
  }
  if (cohort.yearStatus === YEAR_STATUS.NEXT) {
    const current = statedTuition(pricing, { ageGroup, intake: false, today })
    meta.currentYear = current?.yearStatus === YEAR_STATUS.CURRENT ? current : null
  }
  const rows = tuitionRows(cohort.rows, ageGroup)

  const cheapest = rows
    .map(row => ({ row, perMonth: toEur(monthlyEquivalent(row), row.currency) }))
    .filter(entry => entry.perMonth != null)
    .reduce((best, entry) => (best == null || entry.perMonth < best.perMonth ? entry : best), null)
  const periods = [...HEADLINE_PERIODS, null]
  if (cheapest) periods.unshift(pricePeriod(cheapest.row))

  for (const period of periods) {
    const periodRows = rows.filter(row => pricePeriod(row) === period)
    if (periodRows.length === 0) continue

    // Totals say so on the page: a parent reads "Total fees", not "Tuition". A printed total
    // may already hold the required extras, so none are named on top of it.
    meta.total = periodRows.every(isStatedTotal)
    if (meta.total) meta.required = []
    meta.optionalIncluded = meta.total ? optionalInTotal(periodRows, cohort.rows) : []
    const toEuro = (value, row) => toEur(value, row.currency)
    const euro = rangeOf(periodRows, toEuro)
    if (euro) return { ...euro, currency: 'EUR', period, ends: rangeEnds(periodRows, toEuro, euro), ...meta }

    // No euro rate: the currency of the cheapest row, never mixed with another.
    const priced = periodRows.filter(row => row.currency && priceBase(row) != null)
    if (priced.length === 0) continue
    const cheapest = priced.reduce((best, row) => (priceBase(row) < priceBase(best) ? row : best))
    const same = priced.filter(row => row.currency === cheapest.currency)
    const own = rangeOf(same, value => value)
    if (own) return { ...own, currency: cheapest.currency, period, ends: rangeEnds(same, value => value, own), ...meta }
  }
  return null
}

// A fee the school marks as optional: 633's "Дарение за развитие ФОТ (препоръчително)".
const OPTIONAL_RE = /препоръч|по желание|доброволн|незадължит|дарени|optional|recommended|voluntary|donation/iu

// True for a row whose own name says paying it is optional.
export function isOptionalFee(row) {
  return [row?.plan_name, row?.pricing_context?.plan_name_en].some(text => text && OPTIONAL_RE.test(text))
}

// The optional fees a printed total adds up, so the headline can say so: 633's 7,850 €
// is tuition, activities and a recommended 785 € donation. Only when the school's total is
// one figure that is the sum, in euro, of the other whole regular fees of its period, so
// the fees are known to be in it; otherwise none.
function optionalInTotal(totals, rows) {
  if (totals.length !== 1) return []
  const [total] = totals
  const totalEur = toEur(total.amount, total.currency)
  if (totalEur == null) return []
  const parts = rows.filter(row => row !== total && !isStatedTotal(row) && row.amount != null &&
    !isInstallmentPlan(row) && !priceTerms(row) && !isClosedGroupFee(row) &&
    pricePeriod(row) === pricePeriod(total) &&
    (!row.age_group || !total.age_group || row.age_group === total.age_group))
  const sum = parts.reduce((value, row) => value + (toEur(row.amount, row.currency) ?? NaN), 0)
  return Math.abs(sum - totalEur) < 1 ? parts.filter(isOptionalFee) : []
}

// A fee the school says every child pays: 634's "Задължителна услуга" food, its materials
// fee "задължителна за всяка учебна година", its "задължителен избор на едно занимание".
// Not a bare "required": "no prepayment required" says nothing about the fee.
const REQUIRED_RE = /задължител|mandatory|compulsory|obligatory/iu
// Nor a payment term: "заплаща се задължително до 5-то число" says when, not whether.
const NOT_REQUIRED_RE = /незадължит|не (?:е|са) задължит|not (?:mandatory|compulsory|obligatory)|задължително (?:до|в срок)/iu
// Paid once (on enrolment, a deposit, a uniform), so not part of what a year costs.
const ONE_OFF = new Set(['registration', 'deposit', 'uniforms'])

/**
 * The extras the school says every child pays on top of tuition (food, materials, a club),
 * so the headline does not read as the whole cost: 634's 6,820 € leaves out 1,410 € of
 * required food. Only by the fee's own words (name or notes), never by guess; enrolment
 * fees and deposits are left out. With `ageGroup`, that group's extras and those naming no
 * group; with none, only those naming no group, as one group's food is not every group's.
 */
export function requiredExtras(rows = [], ageGroup = null) {
  const scoped = ageGroup ? rowsForAgeGroup(rows, ageGroup) : rows.filter(row => rowAgeGroupKeys(row).size === 0)
  return sortByCategory(scoped).filter((row) => {
    if (row.category === 'tuition' || ONE_OFF.has(priceCategory(row)) || priceBase(row) == null) return false
    const context = row.pricing_context || {}
    const words = [row.plan_name, context.plan_name_en, context.notes].filter(Boolean).join(' / ')
    return REQUIRED_RE.test(words) && !NOT_REQUIRED_RE.test(words) && !isOptionalFee(row)
  })
}

// Longer names (a whole sentence copied as the age group) would crowd the fact.
const RANGE_END_LABEL_MAX = 40

/**
 * What each end of a headline range is, so "8,064–9,141" says half day and full day:
 * `{ low, high }` naming the age groups when the two ends differ in it, else the fee names.
 * An end several rows share names them all (568's preparatory group and grades 1–7).
 * Null for one price, or when the ends have no short names that tell them apart.
 */
export function rangeEndLabels(fee, t, language) {
  const { low, high } = fee?.ends || {}
  if (!low?.length || !high?.length) return null
  const names = (rows, name) => {
    const labels = rows.map(row => name(row, t, language))
    return labels.every(Boolean) ? [...new Set(labels)] : null
  }
  for (const name of [ageGroupName, planNameLabel]) {
    const [from, to] = [names(low, name), names(high, name)]
    if (!from || !to) continue
    const lower = new Set(from.map(label => label.toLocaleLowerCase()))
    if (to.some(label => lower.has(label.toLocaleLowerCase()))) continue
    const [lowLabel, highLabel] = [from.join(', '), to.join(', ')]
    if (lowLabel.length > RANGE_END_LABEL_MAX || highLabel.length > RANGE_END_LABEL_MAX) return null
    return { low: lowLabel, high: highLabel }
  }
  return null
}

// Longer fee names ("задължителен избор на едно занимание в рамките на …") give way to the category.
const REQUIRED_NAME_MAX = 30

// The periods `pricing.per` names ("/ month"); a one-off has none.
const PER_PERIODS = new Set(['monthly', 'quarter', 'term', 'semester', 'daily', 'weekly', 'hourly', 'yearly'])

/**
 * The lines under a headline fee: what each end of the range is ("Half day: 8,064 EUR",
 * "Full day: 9,141 EUR"), the optional fees a printed total adds up ("Includes an
 * optional fee: …"), the extras every child pays on top ("Also required: Food 1,410 EUR")
 * and whether some groups' fees state no year. `formatPrice(value, currency)` writes a
 * price as the headline does.
 */
export function feeRangeNotes(fee, t, language, formatPrice) {
  if (!fee) return []
  const ends = rangeEndLabels(fee, t, language)
  const optional = (fee.optionalIncluded || []).map(row => {
    const shown = displayPrice(row.amount, row.currency)
    return t('pricing.includesOptional', {
      name: planNameLabel(row, t, language) || t(`pricing.${priceCategory(row)}`),
      price: formatPrice(shown.value, shown.currency),
    })
  })
  const required = (fee.required || []).map((row) => {
    const shown = displayPrice(priceBase(row), row.currency)
    const price = formatPrice(shown.value, shown.currency)
    const period = pricePeriod(row)
    const amount = PER_PERIODS.has(period) ? `${price} / ${t(`pricing.per.${period}`)}` : price
    // The fee's own name when it is short ("Lunch"), else its category.
    const own = planNameLabel(row, t, language)
    return `${own && own.length <= REQUIRED_NAME_MAX ? own : t(`pricing.${priceCategory(row)}`)} ${amount}`
  })
  return [
    ends ? t('pricing.rangeEnds', {
      low: ends.low,
      lowPrice: formatPrice(fee.min, fee.currency),
      high: ends.high,
      highPrice: formatPrice(fee.max, fee.currency),
    }) : null,
    ...optional,
    required.length > 0 ? t('pricing.alsoRequired', { fees: required.join(' · ') }) : null,
    fee.otherYears ? t('pricing.someYearNotStated') : null,
  ].filter(Boolean)
}

// A half-day price where the same group also has a full-day one: a parent ordering by price
// compares a day's care, and 527's and 542's half-day fees put them first in the kindergarten
// list. A group (or a school) with only a half-day price keeps it.
function withoutHalfDay(rows) {
  // By the groups we can read, not the text: "Полудневна група" and "Целодневна група" are
  // one group's half and full day.
  const group = row => [...rowAgeGroupKeys(row)].sort().join(',')
  const fullDay = new Set(rows.filter(row => !isHalfDay(row)).map(group))
  return rows.filter(row => !isHalfDay(row) || !(fullDay.has(group(row)) || fullDay.has('')))
}

/**
 * A number to order schools by price: the lowest headline tuition per month, in euro, of a
 * full day where the school prices one (`withoutHalfDay`).
 *
 * For sorting only, never shown: putting a yearly and a monthly fee in one order needs a
 * common unit (a yearly fee over 12 months is the same order as a monthly fee times 12, so
 * this compares what a year costs), and the order survives the 10-vs-12-month error that a
 * displayed figure would not. Null when no stated tuition converts.
 */
export function tuitionSortValue(pricing = [], { ageGroup = null, intake = true, today = new Date() } = {}) {
  const cohort = selectPricingCohort(pricing, { ageGroup, intake, today })
  if (!cohort) return null
  // Half days give way only to a full day that converts: a full day priced per semester
  // must not leave the school with no figure at all.
  const converting = tuitionRows(cohort.rows, ageGroup).filter(row => toEur(monthlyEquivalent(row), row.currency) != null)
  const values = withoutHalfDay(converting).map(row => toEur(monthlyEquivalent(row), row.currency))
  if (values.length > 0) return Math.min(...values)
  // Next year's fee has no monthly figure (a term fee): sort by this year's instead.
  return cohort.yearStatus === YEAR_STATUS.NEXT
    ? tuitionSortValue(pricing, { ageGroup, intake: false, today })
    : null
}
