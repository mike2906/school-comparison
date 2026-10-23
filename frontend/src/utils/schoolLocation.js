// Per-location age-group shifts and focus tags, as the school card and the map show them.

export function getAgeGroupShifts(location) {
  if (!location?.age_group_shifts) return []
  return location.age_group_shifts.filter(item => item && item.age_group)
}

export function getLocationTags(location) {
  if (!location?.location_tags) return []
  if (!Array.isArray(location.location_tags)) return []
  return location.location_tags.filter(Boolean)
}

// The shift for the chosen age group, else the location's first shift.
export function getShiftForAgeGroup(location, ageGroup) {
  const shifts = getAgeGroupShifts(location)
  if (!shifts.length) return null
  if (ageGroup) {
    const match = shifts.find(item => item.age_group === ageGroup)
    if (match) return match
  }
  return shifts[0] || null
}

/**
 * A school's locations for an age group: those that list it, else all of them (a school
 * that does not say which building takes which age).
 */
export function locationsForAgeGroup(school, ageGroup) {
  const all = school.locations || []
  if (!ageGroup) return all
  const matching = all.filter(location => {
    if (Array.isArray(location.age_groups)) return location.age_groups.includes(ageGroup)
    if (location.age_group) return location.age_group === ageGroup
    return false
  })
  return matching.length ? matching : all
}

/** The building a card names for an age group: the primary one, else the first. */
export function locationForAgeGroup(school, ageGroup) {
  const locations = locationsForAgeGroup(school, ageGroup)
  return locations.find(location => location.is_primary) || locations[0]
}
