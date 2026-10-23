import { districtAt, districtLabel, sortedDistricts } from '../admission/catchments.js'
import { kindergartenDistrictFeature } from '../admission/schoolVerdict.js'
import { parseCoordinate } from './coordinates.js'
import { getAddress } from './i18n.js'
import { schoolHighlight } from './schoolHighlights.js'
import { directorySections } from './schoolDirectory.js'

/** A district's URL part, from its transliterated name: `Красна поляна` → `krasna-polyana`. */
export function districtSlug(name) {
  return districtLabel(name, 'en').toLowerCase().replace(/\s+/g, '-')
}

function schoolDistrictNames(school, districts) {
  const names = new Set()
  const own = kindergartenDistrictFeature(school, districts)?.properties.name
  if (own) names.add(own)
  for (const location of Array.isArray(school?.locations) ? school.locations : []) {
    const lat = parseCoordinate(location.lat)
    const lng = parseCoordinate(location.lng)
    const feature = lat != null && lng != null ? districtAt(districts, lat, lng) : null
    if (feature) names.add(feature.properties.name)
  }
  return names
}

/**
 * The listed schools of each Sofia district (`/districts/<slug>`), districts in the page
 * language's alphabetical order, each with the A–Z sections `/schools` uses. A school is
 * in its own district (the calculator's rule: the one its source names, else the one its
 * pin falls in) and in the district of each other building, so a parent finds it where it
 * teaches. Districts with no school are left out.
 */
export function districtGroups(schools, districts, language) {
  const byName = new Map()
  for (const school of Array.isArray(schools) ? schools : []) {
    for (const name of schoolDistrictNames(school, districts)) {
      if (!byName.has(name)) byName.set(name, [])
      byName.get(name).push(school)
    }
  }
  return sortedDistricts(districts, language)
    .map((feature) => feature.properties.name)
    .filter((name) => byName.has(name))
    .map((name) => ({
      slug: districtSlug(name),
      name,
      label: districtLabel(name, language),
      count: byName.get(name).length,
      sections: directorySections(byName.get(name), language),
    }))
}

/**
 * The line under a school in a district list: its headline result, else the address of
 * its building in that district (the main one when none is pinned there).
 */
export function districtListLine(school, t, language, { districts, district } = {}) {
  const locations = Array.isArray(school?.locations) ? school.locations : []
  const here = locations.find((location) => {
    const lat = parseCoordinate(location.lat)
    const lng = parseCoordinate(location.lng)
    return lat != null && lng != null && districtAt(districts, lat, lng)?.properties.name === district
  })
  const primary = locations.find((location) => location.is_primary) || locations[0]
  return schoolHighlight(school, t, language) || getAddress(here || primary, language) || ''
}
