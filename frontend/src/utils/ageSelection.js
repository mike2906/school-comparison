/**
 * The "who is this for" selection on the search page: an age group, optionally derived
 * from birth year + enrolment year (Age Group = Enrolment Year − Birth Year; never exact
 * birth dates), mapped to the search API's age_group / education_level / crossover params.
 */

export const FALLBACK_KINDERGARTEN_GROUPS = ['nursery', 'first', 'second', 'third', 'preschool']
export const FALLBACK_SCHOOL_GROUPS = ['preschool', 'grade_1_4', 'grade_5_7', 'grade_8_12']

/** 'kindergarten' or 'school' for an age group; preschool counts as kindergarten by default. */
export function categoryForGroup(ageGroup, kindergartenGroups = FALLBACK_KINDERGARTEN_GROUPS) {
  if (!ageGroup) return null
  return kindergartenGroups.includes(ageGroup) ? 'kindergarten' : 'school'
}

/**
 * The search params for a picked group. The preschool year exists both in kindergartens
 * and in primary schools, so it is scoped by category unless crossover is requested.
 */
export function toSearchSelection({ ageGroup, category, includeCrossover = false }) {
  if (!ageGroup) {
    return { ageGroup: null, educationLevel: null, includeCrossover: false }
  }
  if (ageGroup !== 'preschool') {
    return { ageGroup, educationLevel: null, includeCrossover: false }
  }
  if (includeCrossover) {
    return { ageGroup, educationLevel: null, includeCrossover: true }
  }
  return {
    ageGroup,
    educationLevel: category === 'school' ? 'primary' : 'kindergarten',
    includeCrossover: false,
  }
}

/** Enrolment years offered: this year's September intake and the next few. */
export function enrolmentYears(now = new Date(), count = 5) {
  const first = now.getFullYear()
  return Array.from({ length: count }, (_, i) => first + i)
}

/** Birth years a child enrolling in `targetYear` can have (0–18 years difference). */
export function birthYearsFor(targetYear, maxAge = 18) {
  return Array.from({ length: maxAge + 1 }, (_, i) => targetYear - i)
}
