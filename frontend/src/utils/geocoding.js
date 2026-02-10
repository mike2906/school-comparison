import debounce from 'lodash.debounce'

const CACHE_TTL_MS = 30 * 24 * 60 * 60 * 1000
const MIN_REQUEST_INTERVAL_MS = 1000
const DEBOUNCE_MS = 500
const NOMINATIM_BASE_URL = 'https://nominatim.openstreetmap.org/search'
// TODO: Update the contact email to a role-based address before production release.
const NOMINATIM_USER_AGENT = 'SchoolFinder/1.0 (contact: mike.ruse90@gmail.com)'

let lastRequestAt = 0
let pendingReject = null

const normalizeAddress = (address) => address.trim().toLowerCase()

const getCacheKey = (address, countryCodes) => `geocode_${normalizeAddress(address)}_${countryCodes || 'any'}`

const readCache = (address, countryCodes) => {
  try {
    const cached = localStorage.getItem(getCacheKey(address, countryCodes))
    if (!cached) return null

    const parsed = JSON.parse(cached)
    if (!parsed?.timestamp) return null

    if (Date.now() - parsed.timestamp > CACHE_TTL_MS) {
      localStorage.removeItem(getCacheKey(address, countryCodes))
      return null
    }

    return parsed
  } catch {
    localStorage.removeItem(getCacheKey(address, countryCodes))
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

const fetchGeocode = async (address, geocodingConfig = {}) => {
  const countryCodes = geocodingConfig.country_codes || 'bg'
  const citySuffix = geocodingConfig.city_suffix || ', Sofia, Bulgaria'

  const cached = readCache(address, countryCodes)
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

  const query = encodeURIComponent(`${address}${citySuffix}`)
  const url = `${NOMINATIM_BASE_URL}?q=${query}&format=json&limit=1&countrycodes=${countryCodes}`
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

  const data = await response.json()
  if (!data || data.length === 0) {
    const error = new Error('No results')
    error.code = 'NO_RESULTS'
    throw error
  }

  const result = data[0]
  writeCache(address, countryCodes, result)

  return {
    lat: parseFloat(result.lat),
    lng: parseFloat(result.lon),
    address: result.display_name,
    source: 'api',
  }
}

const debouncedFetch = debounce((address, geocodingConfig, resolve, reject) => {
  fetchGeocode(address, geocodingConfig)
    .then(resolve)
    .catch(reject)
}, DEBOUNCE_MS)

export const geocodeAddress = (address, geocodingConfig = {}) => {
  const trimmed = address.trim()
  if (!trimmed) {
    const error = new Error('Empty address')
    error.code = 'EMPTY'
    return Promise.reject(error)
  }

  const countryCodes = geocodingConfig.country_codes || 'bg'
  const cached = readCache(trimmed, countryCodes)
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
    debouncedFetch(trimmed, geocodingConfig, (result) => {
      pendingReject = null
      resolve(result)
    }, (error) => {
      pendingReject = null
      reject(error)
    })
  })
}

export const cancelGeocode = () => {
  debouncedFetch.cancel()
  if (pendingReject) {
    const error = new Error('Canceled')
    error.code = 'CANCELED'
    pendingReject(error)
    pendingReject = null
  }
}
