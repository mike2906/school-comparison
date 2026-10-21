import debounce from 'lodash.debounce'

const CACHE_TTL_MS = 30 * 24 * 60 * 60 * 1000
const MIN_REQUEST_INTERVAL_MS = 1000
const DEBOUNCE_MS = 500
const NOMINATIM_BASE_URL = 'https://nominatim.openstreetmap.org/search'
const NOMINATIM_REVERSE_URL = 'https://nominatim.openstreetmap.org/reverse'
const NOMINATIM_USER_AGENT = 'SchoolFinder/1.0 (contact: contact@schooldecider.com)'

let lastRequestAt = 0
let pendingReject = null
// Bumped by cancelGeocode: a lookup that needs a second request stops before sending it.
let generation = 0

const normalizeAddress = (address) => address.trim().toLowerCase()

// v2: entries before the quarter and city checks may hold a wrong town ("Люлин 5").
// The old ones are never read again, so they are dropped once.
try {
  if (!localStorage.getItem('geocodeCacheV2')) {
    const stale = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key?.startsWith('geocode_')) stale.push(key)
    }
    stale.forEach(key => localStorage.removeItem(key))
    localStorage.setItem('geocodeCacheV2', '1')
  }
} catch {
  // No storage (tests, blocked): nothing to drop.
}
const getCacheKey = (address, countryCodes) => `geocode2_${normalizeAddress(address)}_${countryCodes || 'any'}`
const getReverseCacheKey = (lat, lng, countryCodes) => {
  const latKey = Number(lat).toFixed(5)
  const lngKey = Number(lng).toFixed(5)
  return `reverse_${latKey}_${lngKey}_${countryCodes || 'any'}`
}

const readCacheByKey = (key) => {
  try {
    const cached = localStorage.getItem(key)
    if (!cached) return null

    const parsed = JSON.parse(cached)
    if (!parsed?.timestamp) return null

    if (Date.now() - parsed.timestamp > CACHE_TTL_MS) {
      localStorage.removeItem(key)
      return null
    }

    return parsed
  } catch {
    localStorage.removeItem(key)
    return null
  }
}

const writeCache = (address, countryCodes, payload) => {
  try {
    const data = {
      lat: payload.lat,
      lon: payload.lon,
      display_name: payload.display_name,
      timestamp: Date.now(),
    }
    localStorage.setItem(getCacheKey(address, countryCodes), JSON.stringify(data))
  } catch {
    // Ignore storage errors
  }
}

const writeReverseCache = (lat, lng, countryCodes, payload) => {
  try {
    const data = {
      lat,
      lon: lng,
      display_name: payload.display_name,
      timestamp: Date.now(),
    }
    localStorage.setItem(getReverseCacheKey(lat, lng, countryCodes), JSON.stringify(data))
  } catch {
    // Ignore storage errors
  }
}

// Street-type words: an address that starts with one is not a bare quarter name.
const STREET_TYPE_RE = /^(?:ул|улица|бул|булевард|ж\.?\s*к|кв|пл|м|street|str|st|blvd|bul|ul|zh\.?\s*k|kv)(?=[\s.]|$)/iu
// "Люлин 5", "Младост 1А", "Овча купел 2": a name and a small number, nothing else.
const QUARTER_SHAPE_RE = /^\p{L}[\p{L}\s.-]*?\s+\d{1,2}\s*\p{L}?$/u

const quarterKey = (text) => String(text || '')
  .toLowerCase()
  .replace(/^(?:ж\.?\s*к\.?|кв\.?)\s*/u, '')
  .replace(/[\s.-]+/g, ' ')
  .replace(/(\d) (?=\p{L}$)/u, '$1')
  .trim()

/**
 * "Люлин 5" typed alone reads to the map search as street Люлин, number 5 (one is in
 * Ботевград), while Sofia's housing estates are named "ж.к. Люлин 5". For a bare name and
 * number, the estate query that comes first, used only when a quarter of exactly that name
 * comes back.
 */
export function quarterQuery(address) {
  const trimmed = String(address || '').trim()
  // Estate names in OpenStreetMap are Cyrillic, so a Latin name could never match.
  if (!/\p{Script=Cyrillic}/u.test(trimmed)) return null
  if (trimmed.includes(',') || STREET_TYPE_RE.test(trimmed) || !QUARTER_SHAPE_RE.test(trimmed)) return null
  return `ж.к. ${trimmed}`
}

const QUARTER_TYPES = new Set(['quarter', 'suburb', 'neighbourhood', 'residential'])

export function isQuarterMatch(result, address) {
  if (!result) return false
  const kind = result.addresstype || result.type
  return QUARTER_TYPES.has(kind) && quarterKey(result.name) === quarterKey(address)
}

/** `bounds` is the country config's map bounds, `[[south, west], [north, east]]`. */
export function inBounds(lat, lng, bounds) {
  if (!Array.isArray(bounds) || bounds.length !== 2) return true
  const [[south, west], [north, east]] = bounds
  return lat >= south && lat <= north && lng >= west && lng <= east
}

/** The first result inside the city's bounds, or null when every one is outside. */
export function pickInBounds(results, bounds) {
  return (results || []).find(r => inBounds(parseFloat(r.lat), parseFloat(r.lon), bounds)) || null
}

const waitForSlot = async (startedIn) => {
  const wait = lastRequestAt + MIN_REQUEST_INTERVAL_MS - Date.now()
  if (wait > 0) await new Promise(resolve => setTimeout(resolve, wait))
  if (startedIn !== generation) {
    const error = new Error('Canceled')
    error.code = 'CANCELED'
    throw error
  }
  lastRequestAt = Date.now()
}

const searchRequest = async (text, citySuffix, countryCodes, bounds) => {
  const query = encodeURIComponent(`${text}${citySuffix}`)
  // The viewbox prefers places in the city without excluding the rest.
  const viewbox = Array.isArray(bounds) && bounds.length === 2
    ? `&viewbox=${bounds[0][1]},${bounds[1][0]},${bounds[1][1]},${bounds[0][0]}`
    : ''
  const url = `${NOMINATIM_BASE_URL}?q=${query}&format=jsonv2&limit=5&countrycodes=${countryCodes}${viewbox}`
  const response = await fetch(url, {
    headers: {
      'User-Agent': NOMINATIM_USER_AGENT,
      Accept: 'application/json',
    },
  })

  if (response.status === 429) {
    const error = new Error('Rate limited')
    error.code = 'RATE_LIMIT'
    throw error
  }

  if (!response.ok) {
    const error = new Error('Geocoding failed')
    error.code = 'FAILED'
    throw error
  }

  return (await response.json()) || []
}

const plainSearch = async (text, citySuffix, countryCodes, bounds) => {
  const data = await searchRequest(text, citySuffix, countryCodes, bounds)
  if (data.length === 0) {
    const error = new Error('No results')
    error.code = 'NO_RESULTS'
    throw error
  }
  const result = pickInBounds(data, bounds)
  if (!result) {
    // Every match is outside the city: a wrong guess, not the parent's home.
    const error = new Error('Outside the city')
    error.code = 'OUTSIDE_AREA'
    error.place = data[0].display_name
    throw error
  }
  return result
}

const fetchGeocode = async (address, geocodingConfig = {}) => {
  const countryCodes = geocodingConfig.country_codes || 'bg'
  const citySuffix = geocodingConfig.city_suffix ?? ', Sofia, Bulgaria'
  const bounds = geocodingConfig.bounds
  // `remember: false` keeps the address out of the local cache (the admission address check).
  const remember = geocodingConfig.remember !== false

  const cached = remember ? readCacheByKey(getCacheKey(address, countryCodes)) : null
  if (cached) {
    return {
      lat: cached.lat,
      lng: cached.lon,
      address: cached.display_name,
      source: 'cache',
    }
  }

  const now = Date.now()
  if (now - lastRequestAt < MIN_REQUEST_INTERVAL_MS) {
    const error = new Error('Too many local requests')
    error.code = 'TOO_FAST'
    throw error
  }

  lastRequestAt = now

  const startedIn = generation
  let result = null
  const asQuarter = quarterQuery(address)
  if (asQuarter) {
    // A failed estate request falls through to the plain search.
    const quarters = await searchRequest(asQuarter, citySuffix, countryCodes, bounds).catch(error => {
      if (error?.code === 'RATE_LIMIT') throw error
      return []
    })
    result = quarters.find(r => isQuarterMatch(r, address) && inBounds(parseFloat(r.lat), parseFloat(r.lon), bounds)) || null
    if (!result) await waitForSlot(startedIn)
  }
  if (!result) {
    try {
      result = await plainSearch(address, citySuffix, countryCodes, bounds)
    } catch (error) {
      // `fallback`: a looser query for when the exact one finds nothing in the city
      // (a saved address whose house number OpenStreetMap lacks).
      if (!geocodingConfig.fallback || !['NO_RESULTS', 'OUTSIDE_AREA'].includes(error.code)) throw error
      await waitForSlot(startedIn)
      result = await plainSearch(geocodingConfig.fallback, citySuffix, countryCodes, bounds)
    }
  }

  if (remember) writeCache(address, countryCodes, result)

  return {
    lat: parseFloat(result.lat),
    lng: parseFloat(result.lon),
    address: result.display_name,
    source: 'api',
  }
}

const fetchReverseGeocode = async (lat, lng, geocodingConfig = {}, { details = false } = {}) => {
  const countryCodes = geocodingConfig.country_codes || 'bg'
  const cached = details ? null : readCacheByKey(getReverseCacheKey(lat, lng, countryCodes))
  if (cached) {
    return {
      lat: cached.lat,
      lng: cached.lon,
      address: cached.display_name,
      source: 'cache',
    }
  }

  const now = Date.now()
  if (now - lastRequestAt < MIN_REQUEST_INTERVAL_MS) {
    const error = new Error('Too many local requests')
    error.code = 'TOO_FAST'
    throw error
  }

  lastRequestAt = now

  const url = `${NOMINATIM_REVERSE_URL}?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lng)}&format=json&zoom=18&addressdetails=${details ? 1 : 0}&accept-language=${countryCodes}`
  const response = await fetch(url, {
    headers: {
      'User-Agent': NOMINATIM_USER_AGENT,
      Accept: 'application/json',
    },
  })

  if (response.status === 429) {
    const error = new Error('Rate limited')
    error.code = 'RATE_LIMIT'
    throw error
  }

  if (!response.ok) {
    const error = new Error('Reverse geocoding failed')
    error.code = 'FAILED'
    throw error
  }

  const data = await response.json()
  if (!data?.display_name) {
    const error = new Error('No results')
    error.code = 'NO_RESULTS'
    throw error
  }

  if (details) return { road: data.address?.road || null, houseNumber: data.address?.house_number || null }
  writeReverseCache(lat, lng, countryCodes, data)

  return {
    lat: parseFloat(lat),
    lng: parseFloat(lng),
    address: data.display_name,
    source: 'api',
  }
}

const debouncedFetch = debounce((address, geocodingConfig, resolve, reject) => {
  fetchGeocode(address, geocodingConfig)
    .then(resolve)
    .catch(reject)
}, DEBOUNCE_MS)

export const geocodeAddress = (address, geocodingConfig = {}) => {
  // A new lookup replaces any earlier one, including its pending second request.
  generation += 1
  const trimmed = address.trim()
  if (!trimmed) {
    const error = new Error('Empty address')
    error.code = 'EMPTY'
    return Promise.reject(error)
  }

  const countryCodes = geocodingConfig.country_codes || 'bg'
  const cached = geocodingConfig.remember === false ? null : readCacheByKey(getCacheKey(trimmed, countryCodes))
  if (cached) {
    debouncedFetch.cancel()
    if (pendingReject) {
      const error = new Error('Canceled')
      error.code = 'CANCELED'
      pendingReject(error)
      pendingReject = null
    }
    return Promise.resolve({
      lat: cached.lat,
      lng: cached.lon,
      address: cached.display_name,
      source: 'cache',
    })
  }

  return new Promise((resolve, reject) => {
    if (pendingReject) {
      const error = new Error('Canceled')
      error.code = 'CANCELED'
      pendingReject(error)
    }
    pendingReject = reject
    // A superseded lookup that settles late must not clear the newer one's handle.
    const settle = () => {
      if (pendingReject === reject) pendingReject = null
    }
    debouncedFetch(trimmed, geocodingConfig, (result) => {
      settle()
      resolve(result)
    }, (error) => {
      settle()
      reject(error)
    })
  })
}

export const cancelGeocode = () => {
  generation += 1
  debouncedFetch.cancel()
  if (pendingReject) {
    const error = new Error('Canceled')
    error.code = 'CANCELED'
    pendingReject(error)
    pendingReject = null
  }
}

export const reverseGeocode = async (lat, lng, geocodingConfig = {}) => {
  const latNumber = Number(lat)
  const lngNumber = Number(lng)
  if (!Number.isFinite(latNumber) || !Number.isFinite(lngNumber)) {
    const error = new Error('Invalid coordinates')
    error.code = 'INVALID'
    throw error
  }

  const countryCodes = geocodingConfig.country_codes || 'bg'
  const cached = readCacheByKey(getReverseCacheKey(latNumber, lngNumber, countryCodes))
  if (cached) {
    return {
      lat: cached.lat,
      lng: cached.lon,
      address: cached.display_name,
      source: 'cache',
    }
  }

  return fetchReverseGeocode(latNumber, lngNumber, geocodingConfig)
}

/**
 * The street and house number at a point, for matching against an official address list.
 * Not cached: it is only called when the parent asks to use their current location.
 * Returns `{ road, houseNumber }` (either may be missing).
 */
export const reverseGeocodeStreet = (lat, lng, geocodingConfig = {}) =>
  fetchReverseGeocode(lat, lng, geocodingConfig, { details: true })
