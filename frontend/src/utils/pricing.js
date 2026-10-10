import { intlLocale } from './format.js'
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
}

function yearStartOf(row) {
  const canonical = row?.academic_year_canonical
  if (!canonical) return -1
  const start = Number(String(canonical).split('/')[0])
  return Number.isFinite(start) ? start : -1
}

/**
 * Pick the single cohort a school's headline pricing should be drawn from.
 *
 * Preference: current year, else prices the school published without a year, else the
 * most recent dated year. Returns null when there is nothing publishable.
 */
export function selectPricingCohort(pricing = []) {
  if (!Array.isArray(pricing) || pricing.length === 0) return null

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
 * inheriting one arbitrary row's year.
 */
export function groupPricingByAcademicYear(pricing = []) {
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

  return [...groups.values()].sort((a, b) => {
    if (!a.academicYear) return 1
    if (!b.academicYear) return -1
    return Number(b.academicYear.split('/')[0]) - Number(a.academicYear.split('/')[0])
  })
}

const INSTALLMENT_PLAN_NAME_RE = /(\d+\s*(installment|monthly|вноск)|(installment|monthly|вноск)\s*\d+)/i

/** True for a row describing one instalment of a fee rather than the whole fee. */
export function isInstallmentPlan(item) {
  return !!(item?.plan_name && INSTALLMENT_PLAN_NAME_RE.test(item.plan_name))
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
  if (row.period === 'monthly') return base
  if (row.period === 'yearly') return base / 12
  if (row.period === 'quarter') return base / 3
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

function tuitionRows(rows) {
  return rows.filter(row => row.category === 'tuition' && !isInstallmentPlan(row))
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

/**
 * The age group a pricing row names, or null when there is nothing worth showing.
 *
 * A price row's `age_group` is the text the school's website used ("Grade 1", "5-7 years"),
 * not one of our `ageGroups` keys, so it is shown as written unless it is a known key.
 * Null when the plan name already carries it ("Grade 0 / Plan 1"), to avoid saying it twice.
 */
export function priceAgeGroupLabel(price, t) {
  const raw = price?.age_group?.trim()
  if (!raw) return null
  const key = `ageGroups.${raw}`
  const translated = t(key)
  if (translated && translated !== key) return translated
  if (price.plan_name?.toLowerCase().includes(raw.toLowerCase())) return null
  return raw
}

/**
 * The tuition a school states for its headline cohort, in the period it states it in.
 *
 * Never annualised: a monthly fee multiplied by 12 overstates the year by up to a fifth
 * at a school that bills 10 months. Picks one stated period: the one of the cheapest fee
 * per month (so the headline agrees with `tuitionSortValue`), else monthly first, then
 * quarter, term, semester, year, else a fee whose period the school did not state
 * (`period: null`). EUR and BGN are shown in euro; another currency stays as it is, never mixed.
 * Returns `{ min, max, currency, period, academicYear, yearStatus }` or null.
 */
export function statedTuition(pricing = []) {
  const cohort = selectPricingCohort(pricing)
  if (!cohort) return null
  const meta = { academicYear: cohort.academicYear, yearStatus: cohort.yearStatus }
  const rows = tuitionRows(cohort.rows)

  const cheapest = rows
    .map(row => ({ row, perMonth: toEur(monthlyEquivalent(row), row.currency) }))
    .filter(entry => entry.perMonth != null)
    .reduce((best, entry) => (best == null || entry.perMonth < best.perMonth ? entry : best), null)
  const periods = [...HEADLINE_PERIODS, null]
  if (cheapest) periods.unshift(cheapest.row.period)

  for (const period of periods) {
    const periodRows = rows.filter(row => (row.period ?? null) === period)
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
export function tuitionSortValue(pricing = []) {
  const cohort = selectPricingCohort(pricing)
  if (!cohort) return null
  const values = tuitionRows(cohort.rows)
    .map(row => toEur(monthlyEquivalent(row), row.currency))
    .filter(value => value != null)
  return values.length > 0 ? Math.min(...values) : null
}
