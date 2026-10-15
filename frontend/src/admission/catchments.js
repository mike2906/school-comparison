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

// The same words as a parent types them in Latin ("ul. Ivan Shterev", "blvd Vitosha").
const LATIN_STREET_PREFIX = /^(?:ul|bul|blvd|boulevard|str|street|zh\.?\s?k|jk|kv)\.?\s+/i

function streetKey(name) {
  return searchKey(String(name || '').replace(STREET_PREFIX, '').replace(LATIN_STREET_PREFIX, ''))
}

const TYPE_WORDS = { ул: 'ул.', бул: 'бул.', пл: 'пл.', жк: 'ж.к.', кв: 'кв.', м: 'м.', вз: 'в.з.', пр: 'пр.', гр: 'гр.', с: 'с.' }

function transliterate(text) {
  return Array.from(text, ch => {
    const lower = ch.toLowerCase()
    const latin = LATIN[lower]
    if (latin == null) return ch
    return ch === lower ? latin : latin.charAt(0).toUpperCase() + latin.slice(1)
  }).join('')
}

/**
 * An address-table name as a parent reads it: `УЛ.ИВАН ЩЕРЕВ` → `ул. Иван Щерев`, or in
 * English `ul. Ivan Shterev` (transliterated, as the street signs are).
 */
export function displayPlaceName(raw, language) {
  const text = String(raw || '').trim()
  const typed = /^(ул|бул|пл|ж\.?к|кв|м|в\.?з|пр|гр|с)\.\s*/i.exec(text)
  const name = text
    .slice(typed ? typed[0].length : 0)
    .toLowerCase()
    // Capitals start words, and the second half of a double name (Ал. Батенберг, Горна-Баня),
    // but not an ordinal ending (239-та).
    .replace(/(^|[\s(.]|(?<=\p{L})-)(\p{L})/gu, (_, before, ch) => before + ch.toUpperCase())
  const type = typed ? TYPE_WORDS[typed[1].toLowerCase().replace(/\./g, '')] : null
  const shown = type ? `${type} ${name}` : name
  return String(language || '').startsWith('bg') ? shown : transliterate(shown)
}

/** A street with its place: `ул. Земляне (кв. Суходол)`; the district tells same-named streets apart. */
export function streetDisplayName(street, language, districtName = null) {
  // `ГР.СОФИЯ,КВ.СЕСЛАВЦИ`: the quarter is what tells streets apart, not the city.
  const [town, quarter] = String(street.settlement || '').split(',')
  const place = [
    quarter ? displayPlaceName(quarter, language) : town !== 'ГР.СОФИЯ' ? displayPlaceName(town, language) : null,
    districtName ? districtLabel(districtName, language) : null,
  ].filter(Boolean).join(', ')
  const name = displayPlaceName(street.street, language)
  return place ? `${name} (${place})` : name
}

/**
 * One typed address split into street and house number: `Иван Щерев 5` →
 * `{ street: 'Иван Щерев', number: '5' }`. Without a trailing number, all of it is the street.
 */
export function splitAddress(text) {
  const value = String(text || '').trim()
  const match = /^(.*?\S)[\s,]+(?:№\s*|no\.?\s*)?(\d+\s?[a-zа-я]?)$/iu.exec(value)
  if (!match || !/\p{L}/u.test(match[1])) return { street: value, number: '' }
  return { street: match[1].replace(/[\s,]+$/, ''), number: match[2].replace(/\s+/g, '') }
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

/**
 * Streets matching what the parent typed, best first. Within a match quality, streets
 * in the city come first, then its outer quarters (ГР.СОФИЯ,КВ.…), then same-named
 * streets in Sofia's other towns and villages.
 */
export function searchStreets(streets, query, limit = 12) {
  const key = streetKey(query)
  if (String(query || '').trim().length < 2 || !key) return []
  const ranked = []
  for (const street of streets) {
    const rank = street.key.startsWith(key) ? 0 : street.key.includes(` ${key}`) ? 1 : street.key.includes(key) ? 2 : -1
    if (rank >= 0) ranked.push({ rank, street })
  }
  const outsideCity = street => (street.settlement === 'ГР.СОФИЯ' ? 0 : street.settlement?.startsWith('ГР.СОФИЯ') ? 1 : 2)
  ranked.sort((a, b) =>
    a.rank - b.rank ||
    outsideCity(a.street) - outsideCity(b.street) ||
    a.street.key.length - b.street.key.length ||
    a.street.key.localeCompare(b.street.key))
  return ranked.slice(0, limit).map(entry => entry.street)
}

const LATIN_NUMBER_LETTERS = { a: 'А', b: 'Б', v: 'В', g: 'Г', d: 'Д', e: 'Е', z: 'З', i: 'И', k: 'К' }

/**
 * The address table's street and number as a map-search query: `УЛ.ИВАН ЩЕРЕВ` and `5`
 * in ГР.СОФИЯ become `ул. Иван Щерев 5, София, Bulgaria`. Only used when the parent asks
 * to see the address on the map, since that sends it to a search service.
 */
export function mapSearchQuery(street, number) {
  const words = text => text.toLowerCase().replace(/(^|[\s(-])(\p{L})/gu, (_, before, ch) => before + ch.toUpperCase())
  // Only a dotted type word is a prefix here (МАРИЦА is a name, М.МАРИЦА a locality).
  const typed = String(street.street || '').match(/^(ул|бул|пл|ж\.?к|кв|м|в\.?з|пр|алея)\.\s*/i)
  const name = String(street.street || '').slice(typed ? typed[0].length : 0).trim()
  const prefix = typed ? `${typed[1].toLowerCase()}.` : '.'
  // `ГР.СОФИЯ,КВ.СЕСЛАВЦИ` keeps its quarter: many street names repeat across quarters.
  const [town, quarter] = String(street.settlement || '').split(',')
  const settlement = [quarter && `кв. ${words(quarter.replace(/^КВ\./, ''))}`, words(town.replace(/^(?:ГР|С)\./, ''))]
    .filter(Boolean).join(', ')
  return `${prefix === '.' ? '' : `${prefix} `}${words(name)} ${normalizeHouseNumber(number) || number}, ${settlement}, Bulgaria`
}

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

/** A district's name for the page: as published in Bulgarian, transliterated otherwise. */
export function districtLabel(name, language) {
  if (!name || String(language || '').startsWith('bg')) return name || ''
  return String(name)
    .toLowerCase()
    .split(/(\s+|-)/)
    .map(part => Array.from(part, ch => LATIN[ch] ?? ch).join(''))
    .map(part => part.charAt(0).toUpperCase() + part.slice(1))
    .join('')
}

/** District features in alphabetical order of their label in the page language. */
export function sortedDistricts(districts, language) {
  return [...(districts?.features || [])].sort((a, b) =>
    districtLabel(a.properties.name, language).localeCompare(districtLabel(b.properties.name, language), language)
  )
}

// The full words OpenStreetMap uses where the address table abbreviates (улица → УЛ.).
const LONG_STREET_TYPE = /^(?:улица|булевард|площад|жилищен комплекс|квартал)\s+/i

/**
 * The address-table street for a road name and house number found at a point (from
 * OpenStreetMap). Streets of the same name are told apart by which catchment polygon
 * holds the point. Null when nothing matches exactly: the caller then asks the parent.
 */
export function streetForLocation(streets, catchments, { road, houseNumber }, lat, lng) {
  if (!road || !houseNumber) return null
  const name = String(road).replace(LONG_STREET_TYPE, '')
  const key = streetKey(name)
  const candidates = searchStreets(streets, name, 50).filter(street => street.key === key && catchmentSchoolFor(street, houseNumber))
  if (candidates.length <= 1) return candidates[0] ? { street: candidates[0], number: normalizeHouseNumber(houseNumber) } : null
  const inside = candidates.filter(street => {
    const geometry = catchments?.get(catchmentSchoolFor(street, houseNumber))?.geometry
    return geometry && geometryContains(geometry, lat, lng)
  })
  return inside.length === 1 ? { street: inside[0], number: normalizeHouseNumber(houseNumber) } : null
}

/**
 * Where a point falls against a school's areas, from the simplified polygons: 'inside',
 * 'neighbouring' or 'outside'. Only a hint for a pin on the map; the official answer
 * comes from the address table.
 */
export function areaAtPoint(own, neighbours, lat, lng) {
  if (own.some(feature => geometryContains(feature.geometry, lat, lng))) return 'inside'
  if (neighbours.some(feature => geometryContains(feature.geometry, lat, lng))) return 'neighbouring'
  return 'outside'
}
