import assert from 'node:assert/strict'
import test from 'node:test'

import { formatDistance, intlLocale } from './format.js'

test('intlLocale maps the UI language to one Intl locale', () => {
  assert.equal(intlLocale('bg'), 'bg-BG')
  assert.equal(intlLocale('en'), 'en-GB')
  assert.equal(intlLocale(undefined), 'en-GB')
})

test('formatDistance uses the locale decimal separator and marks approximate pins', () => {
  assert.equal(formatDistance(1.234, { language: 'en' }), '1.2 km')
  assert.match(formatDistance(1.234, { language: 'bg' }), /^1,2\s/)
  assert.equal(formatDistance(3, { language: 'en', approximate: true }), '~3.0 km')
  assert.equal(formatDistance(null), null)
  assert.equal(formatDistance(Number.NaN), null)
})
