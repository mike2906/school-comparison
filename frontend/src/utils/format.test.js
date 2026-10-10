import assert from 'node:assert/strict'
import test from 'node:test'

import { formatDistance, formatKm, intlLocale } from './format.js'

test('intlLocale maps the UI language to one Intl locale', () => {
  assert.equal(intlLocale('bg'), 'bg-BG')
  assert.equal(intlLocale('en'), 'en-GB')
  assert.equal(intlLocale(undefined), 'en-GB')
})

test('formatDistance formats the number by locale and the unit through i18n', () => {
  const t = (key, { km }) => (key.endsWith('Approx') ? `~${km} км` : `${km} км`)
  assert.equal(formatKm(1.234, 'en'), '1.2')
  assert.equal(formatKm(2, 'bg'), '2,0')
  assert.equal(formatDistance(1.234, { language: 'bg', t }), '1,2 км')
  assert.equal(formatDistance(3, { language: 'en', approximate: true, t }), '~3.0 км')
  assert.equal(formatDistance(null, { language: 'en', t }), null)
  assert.equal(formatDistance(Number.NaN, { language: 'en', t }), null)
})
