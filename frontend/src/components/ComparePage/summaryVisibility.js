import { getSummary } from '../../utils/i18n.js'

export function hasAnySchoolSummary(schools, language) {
  return (schools || []).some((school) => Boolean(getSummary(school, language, 'short')))
}
