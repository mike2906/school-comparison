/**
 * Pure data helpers behind the school detail page's key-facts strip.
 *
 * Each helper returns null when the fact is not available, so the strip shows only the
 * facts a school actually has.
 */
import { getBenchmarkToneClasses, getNvoSubjectKey, isAverageMetric } from './nvo.js'

/**
 * The latest year's combined (Bulgarian + maths) NVO result for one exam, with the
 * Sofia schools' average for the same subjects and year when it is known.
 *
 * Returns `{ examType, year, value, national, diff, tone, textClass, badgeClass }`
 * (`national`/`diff`/`tone` null without a benchmark) or null without results.
 */
export function getLatestNvoFact(examResults = [], examType, examAverages = null, tolerance = 5) {
  if (!examType || !Array.isArray(examResults)) return null

  const byYear = {}
  examResults.forEach((result) => {
    if (result.exam_type !== examType || !isAverageMetric(result.metric)) return
    const subject = getNvoSubjectKey(result.subject)
    const value = Number(result.value)
    if (!subject || !Number.isFinite(value)) return
    byYear[result.year] = { ...byYear[result.year], [subject]: value }
  })

  const years = Object.keys(byYear).map(Number).sort((a, b) => b - a)
  if (years.length === 0) return null
  const year = years[0]
  const scores = byYear[year]
  const subjects = Object.keys(scores)
  const value = subjects.reduce((sum, key) => sum + scores[key], 0) / subjects.length

  const benchmark = (
    examAverages?.by_year?.[examType]?.[String(year)] ||
    examAverages?.by_year?.[examType]?.[year] ||
    null
  )
  const hasBenchmark = benchmark && subjects.every(key => benchmark[key] != null)
  if (!hasBenchmark) {
    return { examType, year, value, subjects, national: null, diff: null, tone: null }
  }

  const national = subjects.reduce((sum, key) => sum + Number(benchmark[key]), 0) / subjects.length
  const diff = value - national
  const tone = diff >= tolerance ? 'above' : diff <= -tolerance ? 'below' : 'near'
  return { examType, year, value, subjects, national, diff, tone, ...getBenchmarkToneClasses(tone) }
}

/** Distinct age groups across a school's locations, in first-seen order. */
export function getOfferedAgeGroups(locations = []) {
  const seen = new Set()
  const list = Array.isArray(locations) ? locations : []
  list.forEach((location) => {
    const groups = Array.isArray(location?.age_groups) ? location.age_groups : [location?.age_group]
    groups.filter(Boolean).forEach(group => seen.add(group))
  })
  return [...seen]
}

/** Distinct shifts published for a school's locations, e.g. ['morning', 'afternoon']. */
export function getShifts(locations = []) {
  const seen = new Set()
  const list = Array.isArray(locations) ? locations : []
  list.forEach((location) => {
    const entries = Array.isArray(location?.age_group_shifts) ? location.age_group_shifts : []
    entries.forEach((entry) => {
      if (entry?.shift) seen.add(entry.shift)
    })
  })
  return [...seen]
}

/** A `{ lat, lng }` saved by the search page, or null when absent or malformed. */
export function parseSavedLocation(raw) {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    if (typeof parsed?.lat === 'number' && typeof parsed?.lng === 'number') {
      return { lat: parsed.lat, lng: parsed.lng }
    }
  } catch {
    // Malformed storage is treated as no saved location.
  }
  return null
}
