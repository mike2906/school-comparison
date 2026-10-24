/**
 * One-line summaries of a school's published results (NVO, fees, admission rankings) in
 * plain text, for the prerendered pages and the district lists. Pure functions.
 */
import {
  LAST_ADMITTED_KINDS,
  comparableRound,
  cutoffText,
  cutoffValue,
  rankingLabel,
  roundCounts,
  rowLabel,
  shortCampaign,
} from '../admission/calculator.js'
import { formatPercent } from './nvoDisplay.js'
import {
  getAvailableExamTypes,
  getExamTypeForEducationLevel,
  getExamTypeLabel,
  getNvoSubjectKey,
  isAverageMetric,
} from './nvo.js'
import { statedTuition } from './pricing.js'

/** Latest-year average scores per exam type, lowest grade first. */
export function latestNvoResults(school) {
  const results = Array.isArray(school?.exam_results) ? school.exam_results : []
  return getAvailableExamTypes(results).flatMap((examType) => {
    const rows = results.filter((row) => row.exam_type === examType && isAverageMetric(row.metric))
    const year = Math.max(...rows.map((row) => row.year))
    const scores = {}
    rows
      .filter((row) => row.year === year)
      .forEach((row) => {
        const subject = getNvoSubjectKey(row.subject)
        const value = Number(row.value)
        if (subject && Number.isFinite(value)) scores[subject] = value
      })
    return Object.keys(scores).length > 0 ? [{ examType, year, scores }] : []
  })
}

export function nvoLine(result, t, language) {
  const scores = [
    result.scores.bulgarian != null && t('seo.nvoBulgarian', { value: formatPercent(result.scores.bulgarian, 1, language) }),
    result.scores.math != null && t('seo.nvoMath', { value: formatPercent(result.scores.math, 1, language) }),
  ].filter(Boolean)
  return t('seo.nvoLine', {
    exam: getExamTypeLabel(result.examType, t),
    year: result.year,
    scores: scores.join(', '),
  })
}

export function tuitionLine(school, t) {
  // This year's fee, not next year's: the description has no room to label the year.
  const tuition = statedTuition(school?.pricing, { intake: false })
  // Euro only, in the period the school states (never annualised), and never an older
  // year's fee, which the description has no room to label as such.
  if (!tuition || tuition.currency !== 'EUR' || !tuition.period || tuition.yearStatus === 'dated_other') return ''
  const [min, max] = [tuition.min, tuition.max].map((value) => Math.round(value))
  const period = t(`pricing.per.${tuition.period}`)
  const [one, range] = tuition.total ? ['seo.totalOne', 'seo.totalRange'] : ['seo.tuitionOne', 'seo.tuitionRange']
  return min === max ? t(one, { min, period }) : t(range, { min, max, period })
}

/**
 * The admission rankings in words, one line per group or class, read on the same ranking
 * as the school page's "Who got in before" (`comparableRound`).
 */
export function cutoffLines(school, t, language) {
  const rows = school?.admission_info?.cutoff_summary
  if (!Array.isArray(rows)) return []
  return rows.flatMap((row) => {
    const round = comparableRound(row)
    if (!round) return []
    const value = cutoffText(round, t, language)
    const ranking = rankingLabel(row, round, t, language)
    const result = value
      ? `${ranking}${LAST_ADMITTED_KINDS.has(cutoffValue(round)?.kind) ? `, ${t('cutoffs.lastAdmitted')} ` : ': '}${value}`
      : ranking
    const tie = round.tie_at_cutoff ? ` ${t('cutoffs.tie')}` : ''
    const counts = roundCounts(round, t)
    const label = [shortCampaign(row.campaign), row.quota && t('cutoffs.quota')].filter(Boolean).join(', ')
    return [`${rowLabel(row, t, language)} (${label}): ${result}${tie}${counts ? ` (${counts})` : ''}`]
  })
}

/** A row whose ranking says who got in: a cut-off, or that everyone did. */
export function hasAdmissionResult(row) {
  const kind = cutoffValue(comparableRound(row))?.kind
  return Boolean(kind) && kind !== 'noPlaces'
}

/**
 * The one result a list shows beside a school's name: its 7th-grade NVO (the one Sofia
 * parents compare schools by, and the 8th-grade admission score), else the NVO of the
 * grade it ends with, else its fee. '' when none. Kindergarten rankings are left out:
 * one group's leftover-place ranking would misdescribe the kindergarten in a list.
 */
export function schoolHighlight(school, t, language) {
  const nvo = latestNvoResults(school)
  const levelExam = getExamTypeForEducationLevel(school?.education_level)
  const headline = nvo.find((result) => result.examType === 'nvo_7') ||
    nvo.find((result) => result.examType === levelExam) || nvo[nvo.length - 1]
  return headline ? nvoLine(headline, t, language) : tuitionLine(school, t)
}
