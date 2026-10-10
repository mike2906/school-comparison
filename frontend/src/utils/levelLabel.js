/**
 * What a school offers, from the age groups at its locations, e.g. "Grades 1–12" or
 * "Kindergarten · Grades 1–4". The stored education_level is a single category, so an
 * СУ teaching grades 1–12 was labelled "High School" and an ОУ "Primary School".
 */
import { getExclusiveAgeGroups, getGradeBands } from './countryConfig.js'

// Bulgaria, used until the country config has loaded.
const FALLBACK_GRADE_BANDS = {
  grade_1_4: [1, 4],
  grade_5_7: [5, 7],
  grade_8_12: [8, 12],
}
const FALLBACK_KINDERGARTEN_GROUPS = ['nursery', 'first', 'second', 'third']

function offeredGroups(school) {
  const groups = new Set()
  ;(school?.locations || []).forEach(location => {
    const list = Array.isArray(location?.age_groups) ? location.age_groups : [location?.age_group]
    list.filter(Boolean).forEach(group => groups.add(group))
  })
  return groups
}

/** Contiguous grade ranges, e.g. [[1, 12]] or [[1, 4], [8, 12]]. */
export function gradeRanges(groups, gradeBands = FALLBACK_GRADE_BANDS) {
  const bands = Object.entries(gradeBands)
    .filter(([key]) => groups.has(key))
    .map(([, band]) => band)
    .sort((a, b) => a[0] - b[0])
  const ranges = []
  bands.forEach(([start, end]) => {
    const last = ranges[ranges.length - 1]
    if (last && last[1] + 1 === start) last[1] = end
    else ranges.push([start, end])
  })
  return ranges
}

/**
 * A short label for what the school offers; falls back to the stored level. Groups and
 * grade numbers come from the country config when it is loaded.
 */
export function schoolLevelLabel(school, t, config = null) {
  const groups = offeredGroups(school)
  const kindergartenGroups = config ? getExclusiveAgeGroups(config, 'kindergarten') : FALLBACK_KINDERGARTEN_GROUPS
  const gradeBands = config ? getGradeBands(config) : FALLBACK_GRADE_BANDS
  const parts = []
  if (kindergartenGroups.some(group => groups.has(group))) {
    parts.push(t('educationLevels.kindergarten'))
  }
  const ranges = gradeRanges(groups, gradeBands)
  if (ranges.length > 0) {
    parts.push(t('levels.grades', {
      range: ranges.map(([start, end]) => (start === end ? `${start}` : `${start}–${end}`)).join(', '),
    }))
  }
  if (parts.length === 0 && groups.has('preschool')) parts.push(t('ageGroups.preschool'))
  if (parts.length === 0) {
    return school?.education_level ? t(`educationLevels.${school.education_level}`) : ''
  }
  return parts.join(' · ')
}

/**
 * "Държавно / Частно" agree with "училище"; a kindergarten or nursery ("градина",
 * "ясла") needs its own forms, and a state one is municipal ("Общинска"). `type` overrides the school's own type
 * (the card and map show international schools as private).
 */
export function schoolTypeLabel(school, t, type = school?.school_type) {
  if (!type) return ''
  return ['kindergarten', 'nursery'].includes(school?.education_level)
    ? t(`schoolTypesKindergarten.${type}`)
    : t(`schoolTypes.${type}`)
}
