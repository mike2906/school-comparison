/**
 * What a school offers, from the age groups at its locations, e.g. "Grades 1–12" or
 * "Kindergarten · Grades 1–4". The stored education_level is a single category, so an
 * СУ teaching grades 1–12 was labelled "High School" and an ОУ "Primary School".
 */
const GRADE_BANDS = {
  grade_1_4: [1, 4],
  grade_5_7: [5, 7],
  grade_8_12: [8, 12],
}
const KINDERGARTEN_GROUPS = ['nursery', 'first', 'second', 'third']

function offeredGroups(school) {
  const groups = new Set()
  ;(school?.locations || []).forEach(location => {
    const list = Array.isArray(location?.age_groups) ? location.age_groups : [location?.age_group]
    list.filter(Boolean).forEach(group => groups.add(group))
  })
  return groups
}

/** Contiguous grade ranges, e.g. [[1, 12]] or [[1, 4], [8, 12]]. */
export function gradeRanges(groups) {
  const bands = Object.entries(GRADE_BANDS)
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

/** A short label for what the school offers; falls back to the stored level. */
export function schoolLevelLabel(school, t) {
  const groups = offeredGroups(school)
  const parts = []
  if (KINDERGARTEN_GROUPS.some(group => groups.has(group))) {
    parts.push(t('educationLevels.kindergarten'))
  }
  const ranges = gradeRanges(groups)
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
