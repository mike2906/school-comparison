// Outdoor space per building (API `outdoor_space`, backend app/utils/outdoor_space.py).
// Each building has a `kind`: grounds (outdoor space there), shared (another school's
// grounds), other_building or floor (from the address: yard not known), none (seen on a
// visit) or unknown, and the `source` that says so: visit, website, osm or address.

export const OUTDOOR_KINDS = ['grounds', 'shared', 'other_building', 'floor', 'none', 'unknown']

const text = (item, language) => item?.[language] || item?.bg || ''

// One entry per building, in the school's location order, with the address when the
// school has more than one building.
export function outdoorEntries(school, language) {
  const answers = school?.outdoor_space?.locations
  if (!Array.isArray(answers)) return []
  const locations = Array.isArray(school.locations) ? school.locations : []
  const byId = new Map(locations.map(location => [location.id, location]))
  return answers
    .filter(answer => OUTDOOR_KINDS.includes(answer?.kind))
    .map(answer => {
      const location = byId.get(answer.location_id)
      return {
        locationId: answer.location_id,
        location,
        kind: answer.kind,
        source: answer.source || null,
        checkedOn: typeof answer.checked_on === 'string' ? answer.checked_on.slice(0, 10) : '',
        visitText: text(answer.text_i18n, language),
        facts: websiteFacts(answer.facts, language),
        openM2: Number.isFinite(answer.open_m2) && answer.open_m2 > 0 ? answer.open_m2 : null,
        pitches: answer.pitches || 0,
        playgrounds: answer.playgrounds || 0,
        sharedWithId: answer.shared_with_id ?? null,
        sharedWith: text(answer.shared_with_i18n, language),
      }
    })
}

function websiteFacts(facts, language) {
  if (!Array.isArray(facts)) return []
  return facts
    .map(fact => ({ text: text(fact?.text_i18n, language), sourceUrl: fact?.source_url || null }))
    .filter(fact => fact.text)
}

// Outdoor facts from the website of a school with several buildings (not tied to one).
export function outdoorWebsiteFacts(school, language) {
  return websiteFacts(school?.outdoor_space?.website, language)
}

// Whether any answer rests on OpenStreetMap, whose credit then shows beside them.
export function usesOpenStreetMap(entries) {
  return entries.some(entry => entry.source === 'osm')
}
