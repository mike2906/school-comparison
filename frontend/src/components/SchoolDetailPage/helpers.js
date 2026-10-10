import { languageLabel } from '../../utils/languages.js'
import {
  getNvoDetail as getSharedNvoDetail,
  getExamTypeForEducationLevel as getSharedExamTypeForEducationLevel,
  getAvailableExamTypes as getSharedAvailableExamTypes,
  getExamTypeLabel as getSharedExamTypeLabel,
  getLatestScoreForExamType as getSharedLatestScoreForExamType,
} from '../../utils/nvo'
import { getAdmissionStatusKey, getCanonicalAmenityFlags } from '../../utils/schoolAttributes'
import { humanizeTag } from '../../utils/tags'

/**
 * SchoolDetailPage Helper Functions
 * Extracted from SchoolCard.jsx for reuse in detail page
 */

const STATUS_COLORS = {
  accepting: '#10b981',
  waitlist: '#f59e0b',
  full: '#ef4444',
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
  const statusKey = getAdmissionStatusKey(school.admission_info?.status)
  if (statusKey === 'accepting') {
    return { key: 'accepting', color: STATUS_COLORS.accepting, label: t('schoolCard.status.accepting') }
  }
  if (statusKey === 'waitlist') {
    return { key: 'waitlist', color: STATUS_COLORS.waitlist, label: t('schoolCard.status.waitlist') }
  }
  if (statusKey === 'full') {
    return { key: 'full', color: STATUS_COLORS.full, label: t('schoolCard.status.full') }
  }
  return null
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
 * Get detailed NVO data for a school
 */
export function getNvoDetail(school, t, ageGroup = null) {
  return getSharedNvoDetail(school, t, { ageGroup })
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
  return languageLabel(language, t)
}

/**
 * Get option label (generic)
 */
export function getOptionLabel(option, t) {
  if (!option) return ''
  const key = `advancedFilters.options.${option}`
  const translated = t(key)
  if (translated !== key) return translated
  // Free text scraped from a website: tidy it, but never Title-Case a sentence.
  return humanizeTag(option)
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
