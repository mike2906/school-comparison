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
    school.attributes?.admission_status ||
    school.attributes?.enrollment_status ||
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
  if (!rawRequirement) return null
  const requirementValue = typeof rawRequirement === 'string'
    ? rawRequirement
    : rawRequirement?.type || rawRequirement?.requirement || rawRequirement?.method

  if (!requirementValue) return null

  const normalized = String(requirementValue).toLowerCase()
  if (normalized.includes('interview')) {
    return { icon: '📝', text: t('schoolCard.admissions.interviewRequired') }
  }
  if (normalized.includes('test') || normalized.includes('exam')) {
    return { icon: '📋', text: t('schoolCard.admissions.testRequired') }
  }
  if (normalized.includes('none') || normalized.includes('no')) {
    return { icon: '✅', text: t('schoolCard.admissions.noEntranceExam') }
  }

  return null
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
  const examResults = school.exam_results || []
  if (examResults.length === 0) return null

  const educationLevel = school.education_level
  const examTypeMap = {
    primary: { examType: 'nvo_4', gradeKey: 'schoolCard.nvo.grade4' },
    lower_secondary: { examType: 'nvo_7', gradeKey: 'schoolCard.nvo.grade7' },
    upper_secondary: { examType: 'nvo_10', gradeKey: 'schoolCard.nvo.grade12' },
  }

  const examConfig = examTypeMap[educationLevel]
  if (!examConfig) return null

  const relevant = examResults.filter(result => result.exam_type === examConfig.examType)
  if (relevant.length === 0) return null

  const subjects = {
    bulgarian: [],
    math: [],
  }

  relevant.forEach(result => {
    const subject = String(result.subject || '').toLowerCase()
    const metric = String(result.metric || '').toLowerCase()
    if (!metric.includes('average')) return
    if (subject.includes('bulgarian')) {
      subjects.bulgarian.push(result)
    } else if (subject.includes('math')) {
      subjects.math.push(result)
    }
  })

  const yearsBulgarian = new Set(subjects.bulgarian.map(item => item.year))
  const yearsMath = new Set(subjects.math.map(item => item.year))

  const hasAverage = yearsBulgarian.size >= 3 && yearsMath.size >= 3

  const computeAverage = (items) => {
    const sorted = [...items].sort((a, b) => b.year - a.year).slice(0, 5)
    const values = sorted.map(item => Number(item.value)).filter(value => !Number.isNaN(value))
    if (values.length === 0) return null
    return {
      average: values.reduce((sum, value) => sum + value, 0) / values.length,
      years: sorted.map(item => item.year),
    }
  }

  const mathData = hasAverage ? computeAverage(subjects.math) : null
  const bgData = hasAverage ? computeAverage(subjects.bulgarian) : null

  const latestMath = subjects.math.sort((a, b) => b.year - a.year)[0]
  const latestBg = subjects.bulgarian.sort((a, b) => b.year - a.year)[0]

  const overallAvg = hasAverage ? (mathData.average + bgData.average) / 2 : null
  const colorClass = overallAvg == null
    ? 'text-neutral-600'
    : overallAvg >= 75
    ? 'text-emerald-500'
    : overallAvg >= 60
    ? 'text-amber-500'
    : 'text-red-500'

  const yearsUsed = hasAverage ? [...new Set([...mathData.years, ...bgData.years])] : []
  const minYear = hasAverage ? Math.min(...yearsUsed) : null
  const maxYear = hasAverage ? Math.max(...yearsUsed) : null

  return {
    gradeLabel: t(examConfig.gradeKey),
    mathAvg: mathData?.average ?? null,
    bgAvg: bgData?.average ?? null,
    latestMath: latestMath ? Number(latestMath.value) : null,
    latestBg: latestBg ? Number(latestBg.value) : null,
    latestYear: Math.max(latestMath?.year || 0, latestBg?.year || 0),
    colorClass,
    minYear,
    maxYear,
    hasAverage,
  }
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
  const specialPrograms = attributes?.special_programs || []

  return {
    meals: Boolean(attributes?.has_canteen || facilities.includes('cafeteria') || specialPrograms.includes('meals_provided')),
    transport: Boolean(facilities.includes('transportation') || attributes?.transportation_available),
    extended: Boolean(hasAfterSchool || specialPrograms.includes('extended_day') || attributes?.after_school_care),
    smallClasses: Boolean(attributes?.class_size && Number(attributes.class_size) < 16),
    accessible: Boolean(attributes?.accessible || facilities.includes('accessible')),
    library: Boolean(facilities.includes('library')),
    computerLab: Boolean(facilities.includes('computer_lab') || facilities.includes('technology_lab')),
    musicRoom: Boolean(facilities.includes('music_room')),
    scienceLab: Boolean(facilities.includes('science_lab')),
    sportsField: Boolean(facilities.includes('sports_field') || facilities.includes('gym')),
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
