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

/**
 * The lowest tuition amount among rows whose period the school did not state.
 *
 * A headline fallback only: such a price is shown as-is and labelled "period not
 * stated", never converted to a yearly or monthly figure. Returns null when there is
 * no such row.
 */
export function lowestUnstatedPeriodTuition(rows = []) {
  const candidates = (Array.isArray(rows) ? rows : [])
    .filter(row => row.category === 'tuition' && row.period == null && !isInstallmentPlan(row))
    .map(row => ({ amount: priceBase(row), currency: row.currency }))
    .filter(entry => entry.amount != null)
  if (candidates.length === 0) return null
  return candidates.reduce((best, entry) => (entry.amount < best.amount ? entry : best))
}
