import assert from 'node:assert/strict'
import test from 'node:test'

import { alternateLinks, languageBasename, languageFromPath, languagePath } from './languageUrl.js'

test('languageFromPath reads english only from a whole /en segment', () => {
  assert.equal(languageFromPath('/en'), 'en')
  assert.equal(languageFromPath('/en/'), 'en')
  assert.equal(languageFromPath('/en/schools/12'), 'en')
})

test('languageFromPath treats everything else as bulgarian', () => {
  assert.equal(languageFromPath('/'), 'bg')
  assert.equal(languageFromPath('/search'), 'bg')
  assert.equal(languageFromPath('/schools/12'), 'bg')
  assert.equal(languageFromPath('/english-school'), 'bg')
  assert.equal(languageFromPath('/enrol'), 'bg')
  assert.equal(languageFromPath('/schools/en'), 'bg')
  assert.equal(languageFromPath('/EN/search'), 'bg')
  assert.equal(languageFromPath(''), 'bg')
  assert.equal(languageFromPath(undefined), 'bg')
})

test('languageBasename is the router basename for each language', () => {
  assert.equal(languageBasename('bg'), '/')
  assert.equal(languageBasename('en'), '/en')
})

test('languagePath prefixes a route path for the target language', () => {
  assert.equal(languagePath('/schools/12', 'en'), '/en/schools/12')
  assert.equal(languagePath('/schools/12', 'bg'), '/schools/12')
  assert.equal(languagePath('/', 'en'), '/en/')
  assert.equal(languagePath('/', 'bg'), '/')
  assert.equal(languagePath('', 'en'), '/en/')
})

test('languagePath takes a route path, not a raw pathname', () => {
  // Under the /en basename the router reports "/search" for the raw "/en/search".
  // Feeding it the route path round-trips; a raw pathname would be prefixed twice.
  const raw = '/en/search'
  const routePath = '/search'
  assert.equal(languageFromPath(raw), 'en')
  assert.equal(languageFromPath(routePath), 'bg')
  assert.equal(languagePath(routePath, 'en'), raw)
  assert.equal(languagePath(routePath, 'bg'), '/search')
  assert.equal(languagePath(raw, 'en'), '/en/en/search')
})

test('alternateLinks are reciprocal: the same set for either language of a page', () => {
  assert.deepEqual(alternateLinks('/schools/12', 'https://example.bg'), [
    { hreflang: 'bg', href: 'https://example.bg/schools/12' },
    { hreflang: 'en', href: 'https://example.bg/en/schools/12' },
    { hreflang: 'x-default', href: 'https://example.bg/schools/12' },
  ])
})

test('alternateLinks tolerate a trailing slash on the origin', () => {
  assert.deepEqual(
    alternateLinks('/', 'https://example.bg/').map((link) => link.href),
    ['https://example.bg/', 'https://example.bg/en/', 'https://example.bg/']
  )
})
