import { API_BASE } from './base'
import { cachedJson, peekCachedJson } from './cache'

function buildSchoolsUrl({
  countryCode = 'bg',
  city = 'sofia',
  ageGroup = null,
  schoolType = null,
  educationLevel = null,
  includeCrossover = false,
  languageFocus = [],
  specialPrograms = [],
  facilities = [],
  teachingApproach = [],
} = {}) {
  const params = new URLSearchParams()
  params.set('country_code', countryCode)
  if (city) {
    params.set('city', city)
  }
  if (ageGroup) {
    params.set('age_group', ageGroup)
  }
  if (schoolType) {
    params.set('school_type', schoolType)
  }
  if (educationLevel) {
    params.set('education_level', educationLevel)
  }
  if (includeCrossover) {
    params.set('include_crossover', 'true')
  }
  languageFocus.forEach(value => params.append('language_focus', value))
  specialPrograms.forEach(value => params.append('special_programs', value))
  facilities.forEach(value => params.append('facilities', value))
  teachingApproach.forEach(value => params.append('teaching_approach', value))

  return `${API_BASE}/schools?${params.toString()}`
}

export function fetchSchools(options = {}) {
  return cachedJson(buildSchoolsUrl(options), 'Failed to fetch schools')
}

/** Already-loaded schools for these options, so a revisit can render without a skeleton. */
export function peekSchools(options = {}) {
  return peekCachedJson(buildSchoolsUrl(options))
}

export function fetchAvailableFilters({ countryCode = 'bg', city = 'sofia' } = {}) {
  const params = new URLSearchParams()
  params.set('country_code', countryCode)
  if (city) {
    params.set('city', city)
  }
  return cachedJson(`${API_BASE}/schools/filters?${params.toString()}`, 'Failed to fetch available filters')
}

export function fetchSchool(id) {
  return cachedJson(`${API_BASE}/schools/${id}`, 'Failed to fetch school')
}

export function fetchCompare(ids) {
  const idsParam = ids.join(',')
  return cachedJson(`${API_BASE}/compare?ids=${idsParam}`, 'Failed to fetch comparison')
}

export function fetchSchoolCounts({ countryCode = 'bg', city = 'sofia' } = {}) {
  const params = new URLSearchParams()
  params.set('country_code', countryCode)
  if (city) {
    params.set('city', city)
  }
  return cachedJson(`${API_BASE}/schools/counts?${params.toString()}`, 'Failed to fetch school counts')
}

export function searchSchools(query, { countryCode = 'bg', city = 'sofia' } = {}) {
  const params = new URLSearchParams()
  params.set('q', query)
  params.set('country_code', countryCode)
  if (city) {
    params.set('city', city)
  }

  return cachedJson(`${API_BASE}/schools/search?${params.toString()}`, 'Failed to search schools')
}

export function fetchCountryConfig(code) {
  return cachedJson(`${API_BASE}/countries/${code}`, 'Failed to fetch country config')
}

export function fetchExamAverages({ countryCode = 'bg' } = {}) {
  const params = new URLSearchParams()
  params.set('country_code', countryCode)
  return cachedJson(`${API_BASE}/schools/exam-averages?${params.toString()}`, 'Failed to fetch exam averages')
}

export function peekSchool(id) {
  return peekCachedJson(`${API_BASE}/schools/${id}`)
}
