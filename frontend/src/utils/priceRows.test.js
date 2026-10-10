import test from 'node:test'
import assert from 'node:assert/strict'

import { periodFromLabel, pricePeriod, rowAgeGroupKeys, rowsForAgeGroup } from './priceRows.js'

test('periodFromLabel: the period a fee name states', () => {
  const cases = {
    'ГОДИШНА ТАКСА': 'yearly',
    'Годишна такса за 8. клас за учебната 2027/2028 година / Плащане на една вноска': 'yearly',
    'Месечната такса за яслена група': 'monthly',
    'Ежемесечно заплащане': 'monthly',
    'Monthly fee': 'monthly',
    'Занималня / Таксата за първи срок': 'term',
    'Такса за семестър': 'semester',
  }
  for (const [label, period] of Object.entries(cases)) assert.equal(periodFromLabel(label), period, label)
})

test('periodFromLabel: ages, one-off payments, two periods or none state no period', () => {
  for (const label of [
    'Куклен театър за 5-годишните деца',
    'Група за 6 месечни бебета',
    'Еднократно плащане', // the year's tuition paid at once
    'Годишна такса, платима месечно',
    'Регистрационна такса',
    '',
    null,
  ]) assert.equal(periodFromLabel(label), null, String(label))
})

test('pricePeriod: a stored period wins over the name', () => {
  assert.equal(pricePeriod({ period: 'monthly', plan_name: 'Годишна такса' }), 'monthly')
  assert.equal(pricePeriod({ period: null, plan_name: 'Годишна такса' }), 'yearly')
  assert.equal(pricePeriod({ period: null, plan_name: 'Full-day Program' }), null)
})

test('rowAgeGroupKeys: kindergarten groups, grades and stored keys', () => {
  const keys = (age_group, plan_name) => [...rowAgeGroupKeys({ age_group, plan_name })].sort()
  assert.deepEqual(keys('Детска градина', 'Детска градина / Първа и Втора група от възраст: 3г. – 4г.'), ['first', 'second'])
  assert.deepEqual(keys(null, 'Трета и Четвърта група'), ['preschool', 'third'])
  assert.deepEqual(keys('1-ва група', null), ['first'])
  assert.deepEqual(keys('Месечната такса за яслена група', null), ['nursery'])
  assert.deepEqual(keys('ПК', null), ['preschool'])
  assert.deepEqual(keys('VIII - XII клас', null), ['grade_8_12'])
  assert.deepEqual(keys('ПК', 'PK–7: full payment'), ['grade_1_4', 'grade_5_7', 'preschool'])
  assert.deepEqual(keys('third', null), ['third'])
  assert.deepEqual(keys(null, 'Група 3-6 години'), [])
  assert.deepEqual(keys(null, 'Full-day Program'), [])
})

test('rowsForAgeGroup: only the chosen group, plus rows naming none', () => {
  const first = { id: 1, age_group: 'Първа група' }
  const second = { id: 2, age_group: 'Втора група' }
  const general = { id: 3, plan_name: 'Целодневно' }
  assert.deepEqual(rowsForAgeGroup([first, second, general], 'first'), [first, general])
  assert.deepEqual(rowsForAgeGroup([first, second], 'third'), [])
  assert.deepEqual(rowsForAgeGroup([first, second], null), [first, second])
  // No row names a group: nothing to choose by.
  assert.deepEqual(rowsForAgeGroup([general], 'third'), [general])
})
