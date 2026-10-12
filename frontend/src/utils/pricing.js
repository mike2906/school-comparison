import { intlLocale } from './format.js'
import { gradeBandLabel, gradeRangeInText, gradesInText, gradesNamedIn, rowGradeBand } from './gradeBand.js'
import { isOnePayment, pricePeriod, rowAgeGroupKeys, rowsForAgeGroup } from './priceRows.js'
import { stageLabel } from './priceLabels.js'

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

function yearStartOf(row) {
  const canonical = row?.academic_year_canonical ?? row?.academicYear
  if (!canonical) return -1
  const start = Number(String(canonical).split('/')[0])
  return Number.isFinite(start) ? start : -1
}

/**
 * Pick the single cohort a school's headline pricing should be drawn from.
 *
 * Preference: the year families now enrol for (`intakeYearStart`) when it is not the
 * current one and has whole-fee tuition (for `ageGroup` when given), else the current
 * year, else prices the school published without a year, else the most recent dated
 * year. `intake: false` skips the first step. Returns null when there is nothing
 * publishable.
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
      }
    }
  }

  const current = pricing.filter(row => row.year_status === YEAR_STATUS.CURRENT)
  if (current.length > 0) {
    return {
      rows: current,
      academicYear: current[0].academic_year_canonical || current[0].academic_year || null,
      yearStatus: YEAR_STATUS.CURRENT,
    }
  }

  const notStated = pricing.filter(row => row.year_status === YEAR_STATUS.NOT_STATED)
  if (notStated.length > 0) {
    return { rows: notStated, academicYear: null, yearStatus: YEAR_STATUS.NOT_STATED }
  }

  const dated = pricing.filter(row => row.year_status === YEAR_STATUS.DATED_OTHER)
  if (dated.length === 0) return null

  const latestStart = Math.max(...dated.map(yearStartOf))
  const rows = dated.filter(row => yearStartOf(row) === latestStart)
  return {
    rows,
    academicYear: rows[0].academic_year_canonical || rows[0].academic_year || null,
    yearStatus: YEAR_STATUS.DATED_OTHER,
  }
}

/**
 * Group every published price by academic year, newest first, undated last.
 *
 * Used by the detail page so a mixed-year price list is labelled per group instead of
 * inheriting one arbitrary row's year. The year families now enrol for is marked
 * `YEAR_STATUS.NEXT` rather than as another year.
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

  return [...groups.values()].sort((a, b) => {
    if (!a.academicYear) return 1
    if (!b.academicYear) return -1
    return Number(b.academicYear.split('/')[0]) - Number(a.academicYear.split('/')[0])
  })
}

// Every category the API sends, in the order fees are listed (tuition first), then a
// catch-all.
export const PRICE_CATEGORY_ORDER = [
  'tuition',
  'registration',
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
    const index = PRICE_CATEGORY_ORDER.indexOf(row.category)
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

// The headline's tuition rows: whole fees (not one instalment), for the chosen age group
// when there is one.
function tuitionRows(rows, ageGroup = null) {
  return rowsForAgeGroup(rows.filter(row => row.category === 'tuition' && !isInstallmentPlan(row)), ageGroup)
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
// it names ("ПК" → "Preschool"), else the website's own words.
function ageGroupName(price, t) {
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
  return band ? gradeBandLabel(band, t) : raw
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
export function priceAgeGroupLabel(price, t) {
  const raw = price?.age_group?.trim()
  if (!raw) return null
  const key = `ageGroups.${raw}`
  const translated = t(key)
  if (translated && translated !== key) return translated
  // A plan name that already says it needs no repeat, unless it starts at preschool:
  // "PK"/"ПК" is an abbreviation parents may not know.
  const band = rowGradeBand(price)
  if (band?.from === 0) return ageGroupName(price, t)
  // Whole-word match, so "Grade 1" is still shown next to a "Grade 10" plan.
  const escaped = raw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  if (new RegExp(`(^|[^\\p{L}\\p{N}])${escaped}($|[^\\p{L}\\p{N}])`, 'iu').test(price.plan_name || '')) return null
  const named = gradeRangeInText(price.plan_name)
  if (band && named && band.from === named.from && band.to === named.to) return null
  return ageGroupName(price, t)
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
export function groupRowsByAgeGroup(rows = [], t) {
  const bands = new Map()
  const unnamed = []
  rows.forEach(row => {
    const label = ageGroupName(row, t)
    if (!label) {
      unnamed.push(row)
      return
    }
    const key = label.toLocaleLowerCase()
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
 * Returns `{ min, max, currency, period, academicYear, yearStatus, currentYear? }` or null.
 */
export function statedTuition(pricing = [], { ageGroup = null, intake = true, today = new Date() } = {}) {
  const cohort = selectPricingCohort(pricing, { ageGroup, intake, today })
  if (!cohort) return null
  const meta = { academicYear: cohort.academicYear, yearStatus: cohort.yearStatus }
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

    const euro = rangeOf(periodRows, (value, row) => toEur(value, row.currency))
    if (euro) return { ...euro, currency: 'EUR', period, ...meta }

    // No euro rate: the currency of the cheapest row, never mixed with another.
    const priced = periodRows.filter(row => row.currency && priceBase(row) != null)
    if (priced.length === 0) continue
    const cheapest = priced.reduce((best, row) => (priceBase(row) < priceBase(best) ? row : best))
    const own = rangeOf(priced.filter(row => row.currency === cheapest.currency), value => value)
    if (own) return { ...own, currency: cheapest.currency, period, ...meta }
  }
  return null
}

/**
 * A number to order schools by price: the lowest headline tuition per month, in euro.
 *
 * For sorting only, never shown: putting a yearly and a monthly fee in one order needs a
 * common unit, and the order survives the 10-vs-12-month error that a displayed figure
 * would not. Null when no stated tuition converts.
 */
export function tuitionSortValue(pricing = [], { ageGroup = null, intake = true, today = new Date() } = {}) {
  const cohort = selectPricingCohort(pricing, { ageGroup, intake, today })
  if (!cohort) return null
  const values = tuitionRows(cohort.rows, ageGroup)
    .map(row => toEur(monthlyEquivalent(row), row.currency))
    .filter(value => value != null)
  if (values.length > 0) return Math.min(...values)
  // Next year's fee has no monthly figure (a term fee): sort by this year's instead.
  return cohort.yearStatus === YEAR_STATUS.NEXT
    ? tuitionSortValue(pricing, { ageGroup, intake: false, today })
    : null
}
