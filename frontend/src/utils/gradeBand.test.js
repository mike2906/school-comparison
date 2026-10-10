import test from 'node:test'
import assert from 'node:assert/strict'

import { gradeBandLabel, gradeRangeInText, parseGradeBand } from './gradeBand.js'

test('parseGradeBand: the grade texts schools write', () => {
  const cases = {
    'ПК': { from: 0, to: 0 },
    'ПГ': { from: 0, to: 0 },
    'VIII - XII клас': { from: 8, to: 12 },
    'ХI клас': { from: 11, to: 11 }, // Cyrillic Х typed for X
    '5. клас': { from: 5, to: 5 },
    '1 - 12 клас': { from: 1, to: 12 },
    'ПК – 12. клас': { from: 0, to: 12 },
    'от 1 до 4 клас': { from: 1, to: 4 },
    'Grades 2 - 4': { from: 2, to: 4 },
    'Grade 1': { from: 1, to: 1 },
  }
  for (const [text, expected] of Object.entries(cases)) assert.deepEqual(parseGradeBand(text), expected, text)
})

test('parseGradeBand: anything more than grades is not read', () => {
  for (const text of [
    'Детска градина',
    '„Образователни ресурси и учебни практики“ (ПК',
    'Годишна такса за 8. клас за учебната 2027/2028 год',
    '13 клас',
    '7 - 3 клас',
    '',
    null,
  ]) assert.equal(parseGradeBand(text), null, String(text))
})

test('gradeRangeInText: a grade range in a plan name, not dates, times or ages', () => {
  assert.deepEqual(gradeRangeInText('PK–7: full payment, enrolment after 20.02.2026'), { from: 0, to: 7 })
  assert.deepEqual(gradeRangeInText('8–12: full payment'), { from: 8, to: 12 })
  assert.deepEqual(gradeRangeInText('Такса 1-4 клас'), { from: 1, to: 4 })
  assert.deepEqual(gradeRangeInText('Grades 1-4'), { from: 1, to: 4 })
  for (const text of [
    'Full payment with Christmas discount (valid for first 7 contracts)',
    '2 x 3490 € by 20.02.2026 - 30.03',
    'Група 3-6 години',
    'Целодневно 8:00-12:00',
    'Plan 1-2',
    null,
  ]) assert.equal(gradeRangeInText(text), null, String(text))
})

test('gradeBandLabel: one label per shape', () => {
  const t = (key, vars) => `${key}${vars ? JSON.stringify(vars) : ''}`
  assert.equal(gradeBandLabel({ from: 0, to: 0 }, t), 'pricing.grades.preschool')
  assert.equal(gradeBandLabel({ from: 0, to: 7 }, t), 'pricing.grades.preschoolTo{"to":7}')
  assert.equal(gradeBandLabel({ from: 5, to: 5 }, t), 'pricing.grades.one{"n":5}')
  assert.equal(gradeBandLabel({ from: 8, to: 12 }, t), 'pricing.grades.range{"from":8,"to":12}')
})
