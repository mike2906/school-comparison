import test from 'node:test'
import assert from 'node:assert/strict'

import { feeDiscountLines } from './feeDiscounts.js'

const sibling = {
  kind: 'sibling',
  text_i18n: { bg: '5% отстъпка за второ дете', en: '5% off for a second child' },
  source_url: 'https://school.example/fees',
}

test('each discount reads in the page language and links its page', () => {
  assert.deepEqual(feeDiscountLines([sibling], 'en'), [
    { key: 0, text: '5% off for a second child', url: 'https://school.example/fees' },
  ])
  assert.equal(feeDiscountLines([sibling], 'bg')[0].text, '5% отстъпка за второ дете')
})

test('a missing sentence falls back to the other language', () => {
  const onlyEnglish = { ...sibling, text_i18n: { bg: ' ', en: '10% off for a third child' } }
  assert.equal(feeDiscountLines([onlyEnglish], 'bg')[0].text, '10% off for a third child')
})

test('nothing usable gives no lines, and an unsafe link is dropped', () => {
  assert.deepEqual(feeDiscountLines(undefined, 'bg'), [])
  assert.deepEqual(feeDiscountLines([{ ...sibling, text_i18n: {} }], 'bg'), [])
  assert.equal(feeDiscountLines([{ ...sibling, source_url: 'javascript:alert(1)' }], 'en')[0].url, null)
})
