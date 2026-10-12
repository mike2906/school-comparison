import { compareSchoolNames, getSchoolName } from './i18n.js'

/**
 * The A–Z school list (`/schools`): schools first, then kindergartens, each sorted by name
 * as the app's lists sort. Shared by the page and its prerender so both list the same links.
 */
export function directorySections(schools, language) {
  const entries = (Array.isArray(schools) ? schools : [])
    .map((school) => ({ school, name: getSchoolName(school, language) }))
    .filter((entry) => entry.name)
    .sort((a, b) => compareSchoolNames(a.name, b.name, language))
  return [
    { key: 'schools', entries: entries.filter((entry) => entry.school.education_level !== 'kindergarten') },
    { key: 'kindergartens', entries: entries.filter((entry) => entry.school.education_level === 'kindergarten') },
  ].filter((section) => section.entries.length > 0)
}
