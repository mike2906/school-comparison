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

/** Where the preschool year is offered. It exists both in kindergartens and in schools. */
export const PRESCHOOL_WHERE = ['both', 'school', 'kindergarten']

/** The current preschool "where" from the URL-backed filters; old links default to both. */
export function preschoolWhere({ educationLevel, includeCrossover }) {
  if (includeCrossover) return 'both'
  if (educationLevel === 'primary') return 'school'
  if (educationLevel === 'kindergarten') return 'kindergarten'
  return 'both'
}

/**
 * The search params for a picked group. For preschool, `where` scopes it to schools
 * (education_level=primary), kindergartens, or both (include_crossover), the default:
 * a parent entering a birth year should see every option rather than half of them.
 */
export function toSearchSelection({ ageGroup, where = 'both' }) {
  if (!ageGroup) {
    return { ageGroup: null, educationLevel: null, includeCrossover: false }
  }
  if (ageGroup !== 'preschool') {
    return { ageGroup, educationLevel: null, includeCrossover: false }
  }
  if (where === 'school') {
    return { ageGroup, educationLevel: 'primary', includeCrossover: false }
  }
  if (where === 'kindergarten') {
    return { ageGroup, educationLevel: 'kindergarten', includeCrossover: false }
  }
  return { ageGroup, educationLevel: null, includeCrossover: true }
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
