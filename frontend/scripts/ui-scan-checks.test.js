import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDictionaryIndex,
  euroAmounts,
  findImplausiblePrices,
  findJunk,
  findLev,
  findMissingPrice,
  findRawKeys,
  findWrongLanguage,
} from './ui-scan-checks.mjs'

const index = buildDictionaryIndex({
  bg: { ageGroups: { first: 'Първа група' }, pricing: { title: 'Такси', currency: 'EUR' } },
  en: { ageGroups: { first: 'First group' }, pricing: { title: 'Fees', currency: 'EUR' } },
})

test('findRawKeys flags translation keys shown as text, including ones built from data', () => {
  const texts = ['ageGroups.Grade 0', 'Fees: pricing.per.month', 'www.ageGroups.com', 'Първа група']
  assert.deepEqual(findRawKeys(texts, index.namespaces).map(f => f.text), ['ageGroups.Grade 0', 'Fees: pricing.per.month'])
})

test('findJunk flags values leaked from code, not words that contain them', () => {
  const texts = ['NaN', 'Fee: undefined EUR', '[object Object]', 'Hello {{name}}', 'nullable', 'Nancy', 'Annulled']
  assert.deepEqual(findJunk(texts).map(f => f.text), ['NaN', 'Fee: undefined EUR', '[object Object]', 'Hello {{name}}'])
})

test('findLev flags lev prices but not words containing лв', () => {
  assert.deepEqual(findLev(['1 200 лв.', '1200 BGN', 'Цена 300лв', 'Плевен', 'хлв']).map(f => f.text), ['1 200 лв.', '1200 BGN', 'Цена 300лв'])
})

test('euroAmounts reads both locales, ranges and decimals', () => {
  assert.deepEqual(euroAmounts('1 200 €'), [1200])
  assert.deepEqual(euroAmounts('€1,200–€1,500 / month'), [1200, 1500])
  assert.deepEqual(euroAmounts('1,200–1,500 EUR'), [1200, 1500])
  assert.deepEqual(euroAmounts('12,50 €'), [12.5])
  assert.deepEqual(euroAmounts('Grade 1 · 950 EUR'), [950])
  assert.deepEqual(euroAmounts('2025/2026'), [])
})

test('findImplausiblePrices flags amounts outside the sane range', () => {
  assert.deepEqual(findImplausiblePrices(['0 EUR', '€950', '120 000 €']).map(f => f.text), ['0 EUR', '120 000 €'])
})

test('findMissingPrice flags a priced school whose page shows no euro amount', () => {
  const priced = { pricing: [{ amount: 900, currency: 'EUR' }] }
  assert.equal(findMissingPrice(['Fees', 'Price on request'], priced).length, 1)
  assert.equal(findMissingPrice(['€900 / month'], priced).length, 0)
  assert.equal(findMissingPrice(['Fees'], { pricing: [{ amount: null }] }).length, 0)
  assert.equal(findMissingPrice(['Fees'], {}).length, 0)
})

test('findWrongLanguage flags the other language’s UI strings, never shared or short ones', () => {
  assert.deepEqual(findWrongLanguage(['First group', 'Такси', 'EUR'], 'bg', index.foreignStrings).map(f => f.text), ['First group'])
  assert.deepEqual(findWrongLanguage(['Първа група', 'Fees'], 'en', index.foreignStrings).map(f => f.text), ['Първа група'])
})
