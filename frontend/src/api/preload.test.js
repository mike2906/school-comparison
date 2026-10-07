import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { clearJsonCache } from './cache.js'
import {
  fetchAvailableFilters,
  fetchCountryConfig,
  fetchExamAverages,
  fetchSchool,
  fetchSchools,
} from './schools.js'

// The inline script in index.html preloads the page's first API requests. A preload is
// only reused when its URL is exactly the one the app fetches, so run the real script
// against a fake page and compare with the URLs the API wrappers ask for.
const html = readFileSync(new URL('../../index.html', import.meta.url), 'utf8')
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1]

function preloaded(pathname, search = '') {
  const links = []
  const document = {
    createElement: () => ({}),
    head: { appendChild: (link) => links.push(link) },
  }
  new Function('location', 'document', script)({ pathname, search }, document)
  links.forEach((link) => {
    assert.deepEqual(
      { rel: link.rel, as: link.as, crossOrigin: link.crossOrigin },
      { rel: 'preload', as: 'fetch', crossOrigin: 'anonymous' }
    )
  })
  return links.map((link) => link.href).sort()
}

function requested(calls) {
  clearJsonCache()
  const urls = []
  const realFetch = globalThis.fetch
  globalThis.fetch = async (url) => {
    urls.push(url)
    return { ok: true, json: async () => ({}) }
  }
  try {
    calls()
  } finally {
    globalThis.fetch = realFetch
  }
  return urls.sort()
}

// What SearchPage asks for on load (see its useSchools call and effects). The age
// picker's counts are only fetched when it opens, so they are not preloaded.
function searchPageRequests({ ageGroup = null, educationLevel = null, includeCrossover = false } = {}) {
  return requested(() => {
    fetchCountryConfig('bg')
    fetchSchools({
      countryCode: 'bg',
      ageGroup,
      educationLevel: ageGroup === 'preschool' && !includeCrossover ? educationLevel : null,
      includeCrossover,
    })
    fetchAvailableFilters({ countryCode: 'bg' })
    fetchExamAverages({ countryCode: 'bg' })
  })
}

test('the search page preloads exactly what it fetches', () => {
  assert.deepEqual(preloaded('/search'), searchPageRequests())
  assert.deepEqual(preloaded('/en/search'), searchPageRequests())
  assert.deepEqual(
    preloaded('/search', '?age_group=grade_1_4&school_type=private&target_year=2027'),
    searchPageRequests({ ageGroup: 'grade_1_4' })
  )
  // The education level only applies to preschool without crossover.
  assert.deepEqual(
    preloaded('/search', '?age_group=first&education_level=kindergarten'),
    searchPageRequests({ ageGroup: 'first', educationLevel: 'kindergarten' })
  )
  assert.deepEqual(
    preloaded('/search', '?age_group=preschool&education_level=kindergarten'),
    searchPageRequests({ ageGroup: 'preschool', educationLevel: 'kindergarten' })
  )
  assert.deepEqual(
    preloaded('/search', '?age_group=preschool&education_level=primary&include_crossover=true'),
    searchPageRequests({ ageGroup: 'preschool', educationLevel: 'primary', includeCrossover: true })
  )
})

test('a school page preloads the school, the averages and the country', () => {
  const expected = requested(() => {
    fetchCountryConfig('bg')
    fetchSchool('12')
    fetchExamAverages()
  })
  assert.deepEqual(preloaded('/schools/12'), expected)
  assert.deepEqual(preloaded('/en/schools/12'), expected)
})

test('other pages preload only the country config', () => {
  assert.deepEqual(preloaded('/about'), ['/api/countries/bg'])
  assert.deepEqual(preloaded('/schools/school-404'), ['/api/countries/bg'])
})
