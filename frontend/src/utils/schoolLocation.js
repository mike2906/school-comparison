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
