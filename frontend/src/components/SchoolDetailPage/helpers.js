import {
  getNvoDetail as getSharedNvoDetail,
  getExamTypeForEducationLevel as getSharedExamTypeForEducationLevel,
  getAvailableExamTypes as getSharedAvailableExamTypes,
  getExamTypeLabel as getSharedExamTypeLabel,
  getLatestScoreForExamType as getSharedLatestScoreForExamType,
} from '../../utils/nvo'
import { getCanonicalAmenityFlags } from '../../utils/schoolAttributes'
import { classifyAdmissionRequirement } from '../../utils/admission'

/**
 * SchoolDetailPage Helper Functions
 * Extracted from SchoolCard.jsx for reuse in detail page
 */

const STATUS_COLORS = {
  accepting: '#10b981',
  waitlist: '#f59e0b',
  full: '#ef4444',
  unknown: '#9ca3af',
}

const SOURCE_BADGE_COLORS = {
  official: 'bg-emerald-100 text-emerald-700 border-emerald-300',
  scraped_website: 'bg-blue-100 text-blue-700 border-blue-300',
  forum: 'bg-amber-100 text-amber-700 border-amber-300',
  not_found: 'bg-neutral-100 text-neutral-600 border-neutral-300',
}

/**
 * Get enrollment/admission status information
 */
export function getStatusInfo(school, t) {
  const rawStatus =
    school.admission_info?.status ||
    ''
  const statusValue = String(rawStatus).toLowerCase()

  if (statusValue.includes('accept') || statusValue.includes('open') || statusValue.includes('available')) {
    return { key: 'accepting', color: STATUS_COLORS.accepting, label: t('schoolCard.status.accepting') }
  }
  if (statusValue.includes('wait')) {
    return { key: 'waitlist', color: STATUS_COLORS.waitlist, label: t('schoolCard.status.waitlist') }
  }
  if (statusValue.includes('full') || statusValue.includes('closed')) {
    return { key: 'full', color: STATUS_COLORS.full, label: t('schoolCard.status.full') }
  }

  return { key: 'unknown', color: STATUS_COLORS.unknown, label: t('schoolCard.status.unknown') }
}

/**
 * Parse and validate coordinate values
 */
export function parseCoordinate(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'string') {
    const normalized = value.replace(',', '.').trim()
    const parsed = Number.parseFloat(normalized)
    return Number.isFinite(parsed) ? parsed : null
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * Get last admitted points for state kindergartens
 */
export function getLastAdmittedPoints(admissionInfo, ageGroup) {
  const thresholds = admissionInfo?.historical_thresholds || []
  if (thresholds.length === 0) return null

  const relevant = ageGroup
    ? thresholds.filter(item => item.age_group === ageGroup)
    : thresholds

  if (relevant.length === 0) return null

  const latest = relevant.reduce((acc, item) => (item.year > acc.year ? item : acc), relevant[0])
  const rounds = latest.rounds || []
  if (rounds.length === 0) return null

  const lastRound = rounds.reduce((acc, item) => (item.round > acc.round ? item : acc), rounds[0])

  return {
    points: lastRound.last_admitted_points,
    year: latest.year,
    round: lastRound.round,
  }
}

/**
 * Get minimum NVO score for state gymnasiums
 */
export function getMinNvoScore(admissionInfo) {
  const scores = admissionInfo?.historical_min_scores || []
  if (scores.length === 0) return null
  const latest = scores.reduce((acc, item) => (item.year > acc.year ? item : acc), scores[0])
  return {
    score: latest.min_score,
    year: latest.year,
  }
}

/**
 * Get admission requirement for private schools
 */
export function getAdmissionRequirement(rawRequirement, t) {
  const requirement = classifyAdmissionRequirement(rawRequirement)
  if (!requirement) return null
  if (requirement.kind === 'interview') {
    return { icon: '📝', text: t('schoolCard.admissions.interviewRequired') }
  }
  if (requirement.kind === 'test') {
    return { icon: '📋', text: t('schoolCard.admissions.testRequired') }
  }
  if (requirement.kind === 'none') {
    return { icon: '✅', text: t('schoolCard.admissions.noEntranceExam') }
  }
  return { icon: 'ℹ️', text: requirement.text }
}

/**
 * Format percentage value
 */
export function formatPercent(value, decimals = 1) {
  if (value == null || Number.isNaN(value)) return null
  return Number(value).toFixed(decimals)
}

/**
 * Get performance style based on score
 */
export function getPerformanceStyle(value) {
  if (value >= 75) return { text: 'text-emerald-500', bg: 'bg-emerald-500' }
  if (value >= 60) return { text: 'text-amber-500', bg: 'bg-amber-500' }
  return { text: 'text-red-500', bg: 'bg-red-500' }
}

/**
 * Get trend information
 */
export function getTrendInfo(latest, average) {
  if (latest == null || average == null) return null
  const diff = latest - average
  if (diff >= 2) return { arrow: '↑', className: 'text-emerald-500', diff }
  if (diff <= -2) return { arrow: '↓', className: 'text-red-500', diff }
  return { arrow: '→', className: 'text-neutral-400', diff }
}

/**
 * Get detailed NVO data for a school
 */
export function getNvoDetail(school, t) {
  return getSharedNvoDetail(school, t)
}

/**
 * Group pricing items by category
 */
export function groupPricingByCategory(pricing) {
  const grouped = {
    tuition: [],
    food: [],
    transport: [],
    activities: [],
    registration: [],
    materials: [],
    other: [],
  }

  pricing.forEach(item => {
    const category = item.category || 'other'
    if (grouped[category]) {
      grouped[category].push(item)
    } else {
      grouped.other.push(item)
    }
  })

  return grouped
}

/**
 * Get source badge color
 */
export function getSourceBadgeColor(source) {
  return SOURCE_BADGE_COLORS[source] || SOURCE_BADGE_COLORS.not_found
}

/**
 * Format currency amount
 */
export function formatCurrency(amount, locale) {
  if (amount == null || Number.isNaN(amount)) return null
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: 0,
  }).format(amount)
}

/**
 * Get amenity flags from attributes
 */
export function getAmenityFlags(attributes, hasAfterSchool) {
  const facilities = attributes?.facilities || []
  const canonical = getCanonicalAmenityFlags(attributes, hasAfterSchool)

  return {
    meals: canonical.meals,
    transport: canonical.transport,
    extended: canonical.extended,
    smallClasses: Boolean(attributes?.class_size && Number(attributes.class_size) < 16),
    accessible: facilities.includes('accessible'),
    library: canonical.library,
    computerLab: Boolean(canonical.computerLab || facilities.includes('technology_lab')),
    musicRoom: Boolean(facilities.includes('music_room')),
    scienceLab: Boolean(facilities.includes('science_lab')),
    sportsField: Boolean(canonical.sportsFacilities || facilities.includes('sports_field') || facilities.includes('gym')),
  }
}

/**
 * Get language label from code
 */
export function getLanguageLabel(language, t) {
  if (!language) return ''
  const lower = language.toLowerCase()
  const key = `advancedFilters.languages.${lower}`
  const translated = t(key)
  if (translated !== key) return translated
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

/**
 * Get option label (generic)
 */
export function getOptionLabel(option, t) {
  if (!option) return ''
  const key = `advancedFilters.options.${option}`
  const translated = t(key)
  if (translated !== key) return translated
  return option.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

/**
 * Normalize language focus array
 */
export function normalizeLanguageFocus(languageFocus = []) {
  const normalized = []
  const items = Array.isArray(languageFocus) ? languageFocus : [languageFocus]
  items.forEach((item) => {
    if (!item) return
    if (typeof item === 'string') {
      const [language, level] = item.split(':')
      normalized.push({ language, level })
      return
    }
    if (typeof item === 'object') {
      normalized.push({
        language: item.language,
        level: item.level,
      })
    }
  })
  return normalized.filter(item => item.language)
}

/**
 * Hex color to RGBA
 */
export function hexToRgba(hex, alpha) {
  const normalized = hex.replace('#', '')
  const bigint = parseInt(normalized, 16)
  const r = (bigint >> 16) & 255
  const g = (bigint >> 8) & 255
  const b = bigint & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/**
 * Get exam type based on education level
 */
export function getExamTypeForEducationLevel(educationLevel) {
  return getSharedExamTypeForEducationLevel(educationLevel)
}

/**
 * Get available exam types from exam results
 * Returns them in chronological order (4th → 7th → 10th grade)
 */
export function getAvailableExamTypes(examResults = []) {
  return getSharedAvailableExamTypes(examResults)
}

/**
 * Get exam type label (e.g., "4th Grade NVO")
 */
export function getExamTypeLabel(examType, t) {
  return getSharedExamTypeLabel(examType, t)
}

/**
 * Get latest average score for an exam type
 */
export function getLatestScoreForExamType(examResults = [], examType) {
  return getSharedLatestScoreForExamType(examResults, examType)
}

/**
 * Get line style configuration for multi-grade chart
 */
export function getLineStyleForGrade(examType) {
  const styles = {
    nvo_4: {
      strokeDasharray: '0',  // Solid
      strokeWidth: 2,
      opacity: 0.9
    },
    nvo_7: {
      strokeDasharray: '8 4',  // Dashed
      strokeWidth: 2.5,
      opacity: 0.85
    },
    nvo_10: {
      strokeDasharray: '2 3',  // Dotted
      strokeWidth: 3,
      opacity: 0.8
    },
  }
  return styles[examType] || styles.nvo_7
}

/**
 * Calculate cross-grade comparison insight
 */
export function getCrossGradeInsight(examResults = [], availableExamTypes = [], t) {
  if (availableExamTypes.length < 2) return null

  const scores = {}
  availableExamTypes.forEach(examType => {
    scores[examType] = getLatestScoreForExamType(examResults, examType)
  })

  // Find highest and lowest
  const entries = Object.entries(scores).filter(([_, score]) => score != null)
  if (entries.length < 2) return null

  entries.sort((a, b) => b[1] - a[1]) // Sort by score descending
  const [highestType, highestScore] = entries[0]
  const [lowestType, lowestScore] = entries[entries.length - 1]

  const diff = highestScore - lowestScore

  if (diff < 2) {
    return {
      type: 'consistent',
      message: t ? t('schools.crossGrade.consistent') : 'Scores remain consistent across grade levels',
      icon: '📊'
    }
  }

  // Check if progression is natural (higher grades = higher scores)
  const gradeOrder = { nvo_4: 1, nvo_7: 2, nvo_10: 3 }
  const isProgressive = gradeOrder[highestType] > gradeOrder[lowestType]

  if (isProgressive) {
    return {
      type: 'improving',
      message: t
        ? t('schools.crossGrade.improving', {
            highGrade: getExamTypeLabel(highestType, t),
            lowGrade: getExamTypeLabel(lowestType, t),
            diff: formatPercent(diff, 1)
          })
        : `Students improve as they progress: ${getExamTypeLabel(highestType)} scores ${formatPercent(diff, 1)}% higher than ${getExamTypeLabel(lowestType)}`,
      icon: '📈',
      highestType,
      lowestType,
      diff
    }
  } else {
    return {
      type: 'declining',
      message: t
        ? t('schools.crossGrade.declining', {
            highGrade: getExamTypeLabel(highestType, t),
            lowGrade: getExamTypeLabel(lowestType, t),
            diff: formatPercent(diff, 1)
          })
        : `Performance varies by grade: ${getExamTypeLabel(highestType)} scores ${formatPercent(diff, 1)}% higher than ${getExamTypeLabel(lowestType)}`,
      icon: '📉',
      highestType,
      lowestType,
      diff
    }
  }
}
