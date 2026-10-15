import test from 'node:test'
import assert from 'node:assert/strict'

import { periodFromLabel, periodUnknown, pricePeriod, rowAgeGroupKeys, rowsForAgeGroup, splitRowsForAgeGroup } from './priceRows.js'

test('periodFromLabel: the period a fee name states', () => {
  const cases = {
    'ГОДИШНА ТАКСА': 'yearly',
    'Годишна такса за 8. клас за учебната 2027/2028 година / Плащане на една вноска': 'yearly',
    'Месечната такса за яслена група': 'monthly',
    'Ежемесечно заплащане': 'monthly',
    'Monthly fee': 'monthly',
    'Занималня / Таксата за първи срок': 'term',
    'Такса за семестър': 'semester',
    'Semi-annual payment': 'semester',
    'Annual fee': 'yearly',
    'Такса на срок': 'term',
    'Такса за I срок': 'term',
    'Такса за срок': 'term',
    'Първи срок': 'term',
    'I-ви срок': 'term',
    'за първия срок': 'term',
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
    'Еднократно плащане при записване в срок до 30.06', // a deadline, not a term
    'Записване в срок',
    'Long-term discount',
    'Записване за срок от 1 година',
    'Договор на срок от 1 година',
    'Отстъпка по случай годишнината на училището',
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
  // A range of groups includes the ones between.
  assert.deepEqual(keys(null, 'Първа - трета група'), ['first', 'second', 'third'])
  assert.deepEqual(keys(null, '1-3 група'), ['first', 'second', 'third'])
  // The preschool year by its official names.
  assert.deepEqual(keys('Предучилищна група', null), ['preschool'])
  assert.deepEqual(keys(null, 'Такса ПГ'), ['preschool'])
  // "Ясла" in the name of a place that is also a kindergarten names no one group.
  assert.deepEqual(keys('ДГ и ясла „Звездица“', 'Целодневна'), [])
  assert.deepEqual(keys('Детска ясла и градина', null), [])
  for (const both of ['Ясли и ДГ', 'ДГ / ясла', 'Ясла/ДГ', 'ясла, градина', 'Яслена и градинска група']) {
    assert.deepEqual(keys(both, null), [], both)
  }
  assert.deepEqual(keys('Частна Детска Ясла ,,Йор Кидс“', null), ['nursery'])
  assert.deepEqual(keys('Детска градина „Слънце“ / Яслена група', null), ['nursery'])
  assert.deepEqual(keys('ДГ „Слънце“', 'Ясла'), ['nursery'])
  // "ПГ-6 г." is the preschool year for six-year-olds, not preschool to grade 6.
  assert.deepEqual(keys(null, 'Такса ПГ-6 г.'), ['preschool'])
  // Grades written in the plan name or a free-text age group (Codex review).
  assert.deepEqual(keys(null, 'Grade 1 / annual tuition'), ['grade_1_4'])
  assert.deepEqual(keys(null, '8. клас'), ['grade_8_12'])
  assert.deepEqual(keys(null, 'Ученици от 4 до 7 клас'), ['grade_1_4', 'grade_5_7'])
  assert.deepEqual(keys('Годишна такса за 8. клас за учебната 2026/2027 год', null), ['grade_8_12'])
  // A count of groups is not a group.
  assert.deepEqual(keys(null, '2 групи по 12 деца'), [])
  assert.deepEqual(keys(null, 'Програма за 3 групи'), [])
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

test('splitRowsForAgeGroup: fees naming only other ages set aside, fees naming none kept', () => {
  const grades1to4 = { id: 1, category: 'tuition', age_group: '1-4 клас' }
  const grades5to7 = { id: 2, category: 'tuition', age_group: 'V - VII клас' }
  const grades1to7 = { id: 3, category: 'tuition', plan_name: 'Годишна такса 1-7 клас' }
  const registration = { id: 4, category: 'registration', plan_name: 'Такса записване' }
  const rows = [grades1to4, grades5to7, grades1to7, registration]
  assert.deepEqual(splitRowsForAgeGroup(rows, 'grade_5_7'), {
    forGroup: [grades5to7, grades1to7, registration],
    otherAges: [grades1to4],
  })
  // Nothing for the age: everything is another age's.
  assert.deepEqual(splitRowsForAgeGroup([grades1to4], 'grade_8_12'), { forGroup: [], otherAges: [grades1to4] })
  // No age, or a ?group= we do not know, sets nothing aside.
  assert.deepEqual(splitRowsForAgeGroup(rows, null), { forGroup: rows, otherAges: [] })
  assert.deepEqual(splitRowsForAgeGroup(rows, 'grade_9'), { forGroup: rows, otherAges: [] })
})

test('pricePeriod: tuition paid in one payment is yearly; other fees are not', () => {
  assert.equal(pricePeriod({ category: 'tuition', plan_name: 'Гимназия / Плащане на 1 вноска', period: null }), 'yearly')
  assert.equal(pricePeriod({ category: 'registration', plan_name: 'Плащане на 1 вноска', period: null }), null)
  assert.equal(pricePeriod({ category: 'tuition', plan_name: 'Месечна такса / Плащане на една вноска', period: null }), 'monthly')
})

test('rowAgeGroupKeys: school stages and ordinal grades in a name', () => {
  const keys = (age_group, plan_name) => [...rowAgeGroupKeys({ age_group, plan_name })].sort()
  assert.deepEqual(keys(null, 'Гимназия'), ['grade_8_12'])
  assert.deepEqual(keys('Начален и Прогимназиален етап', null), ['grade_1_4', 'grade_5_7'])
  assert.deepEqual(keys(null, 'Прогимназиален етап'), ['grade_5_7'])
  assert.deepEqual(keys(null, 'Предучилищен и от 1-ви до 7-ми клас'), ['grade_1_4', 'grade_5_7', 'preschool'])
  assert.deepEqual(keys(null, 'Такса 5-ти клас'), ['grade_5_7'])
  assert.deepEqual(keys(null, '1st grade'), ['grade_1_4'])
})

test('rowAgeGroupKeys: a school named "гимназия", "начално плащане" or "предучилищно образование" names no group', () => {
  const keys = (age_group, plan_name) => [...rowAgeGroupKeys({ age_group, plan_name })].sort()
  assert.deepEqual(keys(null, 'Частна гимназия "Св. Климент" — годишна такса'), [])
  assert.deepEqual(keys('СУ и гимназия', null), [])
  assert.deepEqual(keys('Частна профилирана гимназия', null), [])
  assert.deepEqual(keys(null, 'Месечна такса за предучилищно образование'), [])
  assert.deepEqual(keys(null, 'Начално плащане при записване'), [])
  assert.deepEqual(keys(null, 'Гимназия / 8 – 10. клас / Плащане на 1 вноска'), ['grade_8_12'])
  assert.deepEqual(keys(null, 'ПГУ от 15.09.2026г. / Годишна такса'), ['preschool'])
})

test('periodUnknown: not for registration fees or uniform items, which are paid once by nature', () => {
  assert.equal(periodUnknown({ category: 'registration', plan_name: 'такса кандидатстване', period: null }), false)
  assert.equal(periodUnknown({ category: 'uniforms', plan_name: 'Тениска с къс ръкав', period: null }), false)
  assert.equal(periodUnknown({ category: 'food', plan_name: 'Храна обяд', period: null }), true)
  assert.equal(periodUnknown({ category: 'food', plan_name: 'Месечна такса храна', period: null }), false)
})
