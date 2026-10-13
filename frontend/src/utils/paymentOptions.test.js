import test from 'node:test'
import assert from 'node:assert/strict'

import { paymentOptionTexts } from './paymentOptions.js'

const t = (key, values) => ({
  'pricing.paymentOptionTotal': `${values?.total} in total`,
  'pricing.paymentOptionInParts': `${values?.total} in instalments`,
})[key]
const money = (value) => `${value} EUR`

test('596: each plan reads as its schedule and what it costs in all', () => {
  const price = {
    currency: 'EUR',
    pricing_context: {
      payment_options: [
        { label: 'Plan 2', total: 9150, payments: 2, instalment: 4575 },
        { label: 'Plan 3', payments: 9, instalment: 750, first_payment: 2650 },
      ],
    },
  }
  assert.deepEqual(paymentOptionTexts(price, money, t), [
    '2 × 4575 EUR (9150 EUR in total)',
    '2650 EUR + 9 × 750 EUR (9400 EUR in total)',
  ])
})

test('a printed total with no count of payments is shown as the total in instalments', () => {
  const price = { currency: 'EUR', pricing_context: { payment_options: [{ total: 4600 }] } }
  assert.deepEqual(paymentOptionTexts(price, money, t), ['4600 EUR in instalments'])
})

test('lev amounts are shown in euro, and a row without options has none', () => {
  const price = { currency: 'BGN', pricing_context: { payment_options: [{ payments: 2, instalment: 1955.83 }] } }
  assert.deepEqual(paymentOptionTexts(price, money, t), ['2 × 1000 EUR (2000 EUR in total)'])
  assert.deepEqual(paymentOptionTexts({ pricing_context: null }, money, t), [])
})
