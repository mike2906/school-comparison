/**
 * Advanced filters (language focus, programmes, facilities, teaching approach) applied on
 * the client to the already-loaded list, so a checkbox click does not refetch the list.
 * Semantics match the API: any selected value within a group, every group with a selection.
 */
import { getLanguageFocusPairs } from './schoolAttributes.js'

const TAG_GROUPS = {
  specialPrograms: 'special_programs',
  facilities: 'facilities',
  teachingApproach: 'teaching_approach',
}

/**
 * True when `school` passes the selected advanced filters. `ignore` names one group
 * ('languageFocus' or a TAG_GROUPS key) to leave out, for per-option counts.
 */
export function matchesAdvancedFilters(school, selected, ignore = null) {
  const languageFocus = selected.languageFocus || []
  if (ignore !== 'languageFocus' && languageFocus.length > 0) {
    const pairs = getLanguageFocusPairs(school)
    if (!languageFocus.some(value => pairs.has(value))) return false
  }
  return Object.entries(TAG_GROUPS).every(([key, tagGroup]) => {
    if (key === ignore) return true
    const values = selected[key] || []
    if (values.length === 0) return true
    // Canonical advanced-filter tags (P1.9), not the free-text display lists.
    const tags = school.attributes?.filter_tags?.[tagGroup] || []
    return values.some(value => tags.includes(value))
  })
}
