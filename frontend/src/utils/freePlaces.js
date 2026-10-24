/**
 * Today's free places in municipal kindergartens and ПГУ (`GET /schools/free-places`,
 * docs/FREE_PLACES.md): per school, one entry per ИСОДЗ group and building with the
 * general queue's `free`, `ranked_not_enrolled`, `capacity` and `enrolled`, plus
 * `free_sop` / `free_chronic` where those queues have a place.
 */

// The API also hides an old snapshot; this covers an answer the browser or edge kept.
export const MAX_AGE_MS = 48 * 60 * 60 * 1000

/** The usable answer, or null when it is missing, empty or too old to show. */
export function usableFreePlaces(data, now = Date.now()) {
  const retrieved = Date.parse(data?.retrieved_at || '')
  if (!data?.schools || Number.isNaN(retrieved) || now - retrieved > MAX_AGE_MS) return null
  return data
}

export function schoolFreePlaces(data, schoolId) {
  return data?.schools?.[String(schoolId)] || []
}

export const campaignOf = startYear => `${startYear}/${startYear + 1}`

/**
 * One child's places at one school: the groups for their birth year in the campaign of
 * `startYear` (a branch building is a group of its own). Null when the school has none,
 * so "no data" never reads as "no places".
 */
export function childFreePlaces(entries, birthYear, startYear) {
  if (!birthYear || !startYear) return null
  const campaign = campaignOf(Number(startYear))
  const groups = (entries || []).filter(entry => entry.birth_year === Number(birthYear) && entry.campaign === campaign)
  if (groups.length === 0) return null
  const sum = key => groups.reduce((total, entry) => total + (entry[key] || 0), 0)
  // Each building is its own choice in ИСОДЗ, so say how many of them have the places.
  const buildings = new Set(groups.map(entry => entry.building || ''))
  const buildingsWithFree = new Set(groups.filter(entry => entry.free > 0).map(entry => entry.building || ''))
  return {
    birthYear: Number(birthYear),
    free: sum('free'),
    rankedNotEnrolled: sum('ranked_not_enrolled'),
    buildings: buildings.size,
    buildingsWithFree: buildingsWithFree.size,
    groups,
  }
}

/** School id -> `childFreePlaces` for every school that has the child's group. */
export function childFreePlacesBySchool(data, birthYear, startYear) {
  const out = new Map()
  if (!data || !birthYear || !startYear) return out
  for (const [id, entries] of Object.entries(data.schools || {})) {
    const summary = childFreePlaces(entries, birthYear, startYear)
    if (summary) out.set(Number(id), summary)
  }
  return out
}

/**
 * Whether the "free place today" filter applies: the parent asked for it and today's
 * snapshot has the child's group somewhere (a birth year and the school year now running).
 * Otherwise the filter is left off rather than emptying the list.
 */
export function freePlacesFilterApplies(filters, bySchool) {
  return Boolean(filters.freePlacesOnly && bySchool.size > 0)
}

/** Whether a school has a free place today in the child's group (`childFreePlacesBySchool`). */
export function hasFreePlaceToday(bySchool, schoolId) {
  return (bySchool.get(schoolId)?.free || 0) > 0
}
