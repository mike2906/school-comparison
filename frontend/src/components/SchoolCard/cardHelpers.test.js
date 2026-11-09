import test from 'node:test'
import assert from 'node:assert/strict'

import {
  buildExpandedSections,
  getLocationAgeGroups,
  getScheduleLine,
  hexToRgba,
} from './cardHelpers.js'

test('hexToRgba converts hex with or without #', () => {
  assert.equal(hexToRgba('#ff8000', 0.5), 'rgba(255, 128, 0, 0.5)')
  assert.equal(hexToRgba('000000', 1), 'rgba(0, 0, 0, 1)')
})

test('getLocationAgeGroups prefers the list, falls back to the single group', () => {
  assert.deepEqual(getLocationAgeGroups({ age_groups: ['a', null, 'b'] }), ['a', 'b'])
  assert.deepEqual(getLocationAgeGroups({ age_group: 'c' }), ['c'])
  assert.deepEqual(getLocationAgeGroups(null), [])
})

test('getScheduleLine returns null without a shift', () => {
  const t = key => key
  assert.equal(getScheduleLine(null, {}, t), null)
  assert.equal(getScheduleLine({}, {}, t), null)
})

test('the card lists each group\'s main price and names the other kinds of fee', () => {
  const t = (key, vars) => (vars ? `${key} ${JSON.stringify(vars)}` : key)
  const row = (category, amount, extra = {}) => ({
    category, amount, currency: 'EUR', academic_year: '2026/2027', year_status: 'current',
    academic_year_canonical: '2026/2027', source: 'scraped_website', pricing_context: {}, ...extra,
  })
  // 300 (ESPA): a yearly price per group, its instalments, food, and many activity fees.
  const pricing = [
    row('tuition', 8580, { age_group: 'Първи клас', period: 'yearly' }),
    row('tuition', 4460, { age_group: 'Първи клас', period: 'term', plan_name: 'вноска I срок, до 31.08.2026 г.' }),
    row('food', 1900, { plan_name: 'Еднократно плащане до 31.08.2026 г.' }),
    ...Array.from({ length: 15 }, () => row('extracurricular', 330, { period: 'term' })),
  ]
  const sections = buildExpandedSections({
    school: { school_type: 'private' }, attributes: {}, locations: [], pricing, t, locale: 'en-GB', language: 'en',
  })
  const fees = sections.find(section => section.title === 'schoolCard.sections.tuitionFees').content
  assert.equal(fees.length, 2, fees.join('\n'))
  assert.match(fees[0], /8,580/)
  assert.match(fees[1], /^schoolCard\.otherFees .*pricing\.food, pricing\.extracurricular/)
})
