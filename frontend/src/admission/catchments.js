/**
 * Official first-grade catchments and districts (Sofia), read from the static snapshot
 * that backend/scripts/build_sofia_catchments.py writes to public/catchments/. Which
 * catchment an address belongs to comes from the official address table, never from
 * the polygons: those are simplified and only drawn. Everything runs in the browser;
 * a parent's address never leaves the page.
 */

const LATIN = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y', к: 'k',
  л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'h',
  ц: 'ts', ч: 'ch', ш: 'sh', щ: 'sht', ъ: 'a', ь: 'y', ю: 'yu', я: 'ya',
}

// Street-type words the address table puts in front of the name (УЛ.АВИЦЕНА).
const STREET_PREFIX = /^(?:ул|бул|пл|ж\.?к|жк|кв|м|в\.?з|пр|алея|местност)\.?\s*/i

/** Lower-case Latin with only letters, digits and single spaces, for matching. */
export function searchKey(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/[а-ъьюя]/g, ch => LATIN[ch] ?? ch)
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function streetKey(name) {
  return searchKey(String(name || '').replace(STREET_PREFIX, ''))
}

/** Where a campaign's snapshot lives. */
export function catchmentUrl(campaign, file, { country = 'bg', city = 'sofia' } = {}) {
  return `/catchments/${country}/${city}/${campaign.replace('/', '-')}/${file}`
}

/** Builds the lookup structures once per loaded address file. */
export function indexAddresses(addresses) {
  return addresses.streets.map(([settlementCode, streetCode, settlement, street, district, numbersBySchool]) => {
    const numbers = new Map()
    for (const [school, list] of Object.entries(numbersBySchool)) {
      for (const number of list.split(',')) numbers.set(number, school)
    }
    return {
      id: `${settlementCode}:${streetCode}`,
      settlement,
      street,
      district,
      key: streetKey(street),
      numbers,
    }
  })
}

/** Streets matching what the parent typed, best first. */
export function searchStreets(streets, query, limit = 8) {
  const key = streetKey(query)
  if (String(query || '').trim().length < 2 || !key) return []
  const ranked = []
  for (const street of streets) {
    const rank = street.key.startsWith(key) ? 0 : street.key.includes(` ${key}`) ? 1 : street.key.includes(key) ? 2 : -1
    if (rank >= 0) ranked.push({ rank, street })
  }
  ranked.sort((a, b) => a.rank - b.rank || a.street.key.length - b.street.key.length || a.street.key.localeCompare(b.street.key))
  return ranked.slice(0, limit).map(entry => entry.street)
}

const LATIN_NUMBER_LETTERS = { a: 'А', b: 'Б', v: 'В', g: 'Г', d: 'Д', e: 'Е', z: 'З', i: 'И', k: 'К' }

/** `12 a` → `12А`, `012б` → `12Б`: the spelling the address table uses. */
export function normalizeHouseNumber(raw) {
  const compact = String(raw || '').replace(/\s+/g, '').toUpperCase()
  const match = /^0*(\d+)(.*)$/.exec(compact)
  if (!match) return null
  const suffix = match[2].replace(/[A-Z]/g, ch => LATIN_NUMBER_LETTERS[ch.toLowerCase()] ?? ch)
  return `${match[1]}${suffix}`
}

/** The catchment school id of an address, or null when the table does not list it. */
export function catchmentSchoolFor(street, rawNumber) {
  const number = normalizeHouseNumber(rawNumber)
  if (!street || !number) return null
  return street.numbers.get(number) ?? null
}

/** Catchment features keyed by school id. */
export function indexCatchments(collection) {
  return new Map(collection.features.map(feature => [feature.properties.id, feature]))
}

/**
 * The catchments whose addresses count as "neighbouring" for `schoolId` (+5 points in
 * group 4): those that list it among their neighbours. Adjacency is not always
 * symmetric, so it is always read in this direction.
 */
export function neighbouringCatchments(catchments, schoolId) {
  return [...catchments.values()].filter(feature => feature.properties.neighbours.includes(schoolId))
}

/**
 * What an address means for one school's first-grade admission: inside its catchment,
 * in a neighbouring one, or neither. `addressSchoolId` is the address's own catchment.
 */
export function addressFactsForSchool(catchments, addressSchoolId, schoolId) {
  if (!addressSchoolId) return { inCatchment: null, inNeighbouring: null }
  const own = catchments.get(addressSchoolId)
  return {
    inCatchment: addressSchoolId === schoolId,
    inNeighbouring: addressSchoolId !== schoolId && Boolean(own?.properties.neighbours.includes(schoolId)),
  }
}

/** The first school GIS id a school response carries, if any. */
export function schoolCatchmentIds(school) {
  return school?.admission_info?.catchment_ids?.['bg-sofia-gis'] || []
}

function ringContains(ring, lng, lat) {
  let inside = false
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]
    const [xj, yj] = ring[j]
    if ((yi > lat) !== (yj > lat) && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside
  }
  return inside
}

function polygonContains(polygon, lng, lat) {
  return ringContains(polygon[0], lng, lat) && !polygon.slice(1).some(hole => ringContains(hole, lng, lat))
}

/** Whether a GeoJSON Polygon or MultiPolygon contains a point. */
export function geometryContains(geometry, lat, lng) {
  if (geometry.type === 'Polygon') return polygonContains(geometry.coordinates, lng, lat)
  if (geometry.type === 'MultiPolygon') return geometry.coordinates.some(polygon => polygonContains(polygon, lng, lat))
  return false
}

/** The district feature a point falls in (a fallback when the source names none). */
export function districtAt(districts, lat, lng) {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null
  return districts.features.find(feature => geometryContains(feature.geometry, lat, lng)) ?? null
}

/** The district feature with this name ("Младост", "Студентски"), ignoring case and spelling. */
export function districtByName(districts, name) {
  const key = searchKey(name)
  if (!key) return null
  return districts.features.find(feature => {
    const own = searchKey(feature.properties.name)
    return own === key || own.startsWith(`${key} `) || key.startsWith(`${own} `)
  }) ?? null
}
