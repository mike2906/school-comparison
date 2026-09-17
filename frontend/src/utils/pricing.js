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
