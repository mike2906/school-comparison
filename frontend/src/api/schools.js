const API_BASE = '/api'

export async function fetchSchools({
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

  const url = `${API_BASE}/schools?${params.toString()}`

  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`Failed to fetch schools: ${response.statusText}`)
  }

  return response.json()
}

export async function fetchAvailableFilters({ countryCode = 'bg', city = 'sofia' } = {}) {
  const params = new URLSearchParams()
  params.set('country_code', countryCode)
  if (city) {
    params.set('city', city)
  }
  const response = await fetch(`${API_BASE}/schools/filters?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`Failed to fetch available filters: ${response.statusText}`)
  }

  return response.json()
}

export async function fetchSchool(id) {
  const response = await fetch(`${API_BASE}/schools/${id}`)
  if (!response.ok) {
    throw new Error(`Failed to fetch school: ${response.statusText}`)
  }

  return response.json()
}

export async function fetchCompare(ids) {
  const idsParam = ids.join(',')
  const response = await fetch(`${API_BASE}/compare?ids=${idsParam}`)
  if (!response.ok) {
    throw new Error(`Failed to fetch comparison: ${response.statusText}`)
  }

  return response.json()
}

export async function fetchSchoolCounts({ countryCode = 'bg', city = 'sofia' } = {}) {
  const params = new URLSearchParams()
  params.set('country_code', countryCode)
  if (city) {
    params.set('city', city)
  }
  const response = await fetch(`${API_BASE}/schools/counts?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`Failed to fetch school counts: ${response.statusText}`)
  }

  return response.json()
}

export async function searchSchools(query, { countryCode = 'bg', city = 'sofia' } = {}) {
  const params = new URLSearchParams()
  params.set('q', query)
  params.set('country_code', countryCode)
  if (city) {
    params.set('city', city)
  }

  const response = await fetch(`${API_BASE}/schools/search?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`Failed to search schools: ${response.statusText}`)
  }

  return response.json()
}

export async function fetchCountryConfig(code) {
  const response = await fetch(`${API_BASE}/countries/${code}`)
  if (!response.ok) {
    throw new Error(`Failed to fetch country config: ${response.statusText}`)
  }

  return response.json()
}

export async function fetchExamAverages({ countryCode = 'bg' } = {}) {
  const params = new URLSearchParams()
  params.set('country_code', countryCode)
  const response = await fetch(`${API_BASE}/schools/exam-averages?${params.toString()}`)
  if (!response.ok) {
    throw new Error(`Failed to fetch exam averages: ${response.statusText}`)
  }

  return response.json()
}
