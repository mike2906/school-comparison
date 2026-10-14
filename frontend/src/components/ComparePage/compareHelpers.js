import { languageLabel } from '../../utils/languages.js'
import { intlLocale } from '../../utils/format.js'
import { getNvoDetail as getSharedNvoDetail } from '../../utils/nvo.js'
import { usesSofiaKindergartenSystem } from '../../utils/admission.js'
import { pricePeriod, periodUnknown } from '../../utils/priceRows.js'

export const SOURCE_BADGE_STYLES = {
  official: 'bg-emerald-50 text-emerald-700',
  official_website: 'bg-emerald-50 text-emerald-700',
  scraped_website: 'bg-teal-50 text-teal-700',
  forum: 'bg-amber-50 text-amber-700',
  community_forum: 'bg-amber-50 text-amber-700',
  parent_submitted: 'bg-violet-50 text-violet-700',
  school_contact: 'bg-cyan-50 text-cyan-700',
  not_found: 'bg-neutral-100 text-neutral-500',
  government: 'bg-blue-50 text-blue-700',
  website: 'bg-cyan-50 text-cyan-700',
  unknown: 'bg-neutral-100 text-neutral-500',
}

export const SOURCE_CATEGORY_LABELS = {
  pricing: 'compare.sections.pricing',
  academic: 'compare.sections.academicPerformance',
  admission: 'compare.sections.admissionEnrollment',
  locations: 'compare.sections.locations',
  contact: 'compare.sections.contact',
  schedule: 'compare.labels.schedule',
  practical: 'compare.sections.practicalDetails',
  other: 'compare.sections.sources',
}

export function parseIds(value) {
  if (!value) return []
  return [...new Set(
    value
      .split(',')
      .map(part => parseInt(part.trim(), 10))
      .filter(id => Number.isFinite(id) && id > 0)
  )]
}

export function formatDate(value, locale) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return new Intl.DateTimeFormat(intlLocale(locale), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date)
}

const PRESCHOOL_LEVELS = new Set(['nursery', 'kindergarten'])

// International schools don't sit the NVO and kindergartens and nurseries have no exams,
// so a blank NVO cell there is "not applicable", not missing data. The stored level is a
// single category, so a kindergarten with exam results keeps them.
export function nvoNotApplicable(school) {
  return school.school_type === 'international' ||
    (PRESCHOOL_LEVELS.has(school.education_level) && !(school.exam_results || []).length)
}

/**
 * How a state school admits pupils, for the fee row; null when we can't say. The NVO hint
 * only fits gymnasium entry: an СУ is stored as upper_secondary but admits grade 1 too.
 */
export function stateAdmissionHint(school, t, ageGroup) {
  if (usesSofiaKindergartenSystem(school)) return t('schoolCard.admissions.kgByPoints')
  const gymnasiumEntry = ageGroup ? ageGroup === 'grade_8_12' : school.education_level === 'upper_secondary'
  return gymnasiumEntry ? t('compare.admissionByNvo') : null
}

/** " / month", " (one-time)" or " · period not stated", after a price. */
export function periodSuffix(price, t) {
  const period = pricePeriod(price)
  if (!period) return periodUnknown(price) ? ` · ${t('pricing.periodNotStated')}` : ''
  if (period === 'one_time') return ` (${t('pricing.one_time')})`
  return ` / ${t(`pricing.per.${period}`)}`
}

export const STATUS_BADGE_CLASSES = {
  accepting: 'bg-emerald-50 text-emerald-700',
  waitlist: 'bg-amber-50 text-amber-700',
  full: 'bg-red-50 text-red-700',
}

export function getLanguageLabel(language, t) {
  return languageLabel(language, t)
}

function getLocationAgeGroups(location) {
  if (!location) return []
  if (Array.isArray(location.age_groups)) return location.age_groups.filter(Boolean)
  if (location.age_group) return [location.age_group]
  return []
}

export function getPrimaryAgeGroup(school) {
  const primaryLocation = school.locations?.find(loc => loc.is_primary) || school.locations?.[0]
  return primaryLocation ? getLocationAgeGroups(primaryLocation)[0] : null
}

export function getPrimaryShiftInfo(school) {
  const primaryLocation = school.locations?.find(loc => loc.is_primary) || school.locations?.[0]
  if (!primaryLocation?.age_group_shifts || primaryLocation.age_group_shifts.length === 0) return null
  const primaryGroup = getPrimaryAgeGroup(school)
  if (primaryGroup) {
    const match = primaryLocation.age_group_shifts.find(item => item.age_group === primaryGroup)
    if (match) return match
  }
  return primaryLocation.age_group_shifts[0] || null
}

export function getNvoDetail(school, t, examType) {
  return examType ? getSharedNvoDetail(school, t, { examType }) : null
}

export function normalizeCompareValue(value) {
  if (value == null) return '__null__'
  if (Array.isArray(value)) return JSON.stringify(value.slice().sort())
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value).toLowerCase()
}
