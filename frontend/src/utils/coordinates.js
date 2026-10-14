/** A latitude or longitude as a number; accepts "42,69" strings. Null when missing or invalid. */
export function parseCoordinate(value) {
  if (value === null || value === undefined) return null
  if (typeof value === 'string') {
    const normalized = value.replace(',', '.').trim()
    const parsed = Number.parseFloat(normalized)
    return Number.isFinite(parsed) ? parsed : null
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

/**
 * The location to measure a distance from: a pinned one, the primary first. Null when none
 * has a pin (the API sends no pin for a street- or area-level guess).
 */
export function pinnedLocation(locations = []) {
  const pinned = (locations || []).filter(
    location => parseCoordinate(location?.lat) != null && parseCoordinate(location?.lng) != null,
  )
  return pinned.find(location => location.is_primary) || pinned[0] || null
}
