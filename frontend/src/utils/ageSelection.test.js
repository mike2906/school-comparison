import test from 'node:test'
import assert from 'node:assert/strict'

import { categoryForGroup, toSearchSelection, enrolmentYears, birthYearsFor } from './ageSelection.js'

test('categoryForGroup treats preschool as kindergarten by default', () => {
  assert.equal(categoryForGroup('first'), 'kindergarten')
  assert.equal(categoryForGroup('preschool'), 'kindergarten')
  assert.equal(categoryForGroup('grade_5_7'), 'school')
  assert.equal(categoryForGroup(null), null)
})

test('toSearchSelection scopes preschool by category unless crossover is on', () => {
  assert.deepEqual(toSearchSelection({ ageGroup: 'preschool', category: 'kindergarten' }),
    { ageGroup: 'preschool', educationLevel: 'kindergarten', includeCrossover: false })
  assert.deepEqual(toSearchSelection({ ageGroup: 'preschool', category: 'school' }),
    { ageGroup: 'preschool', educationLevel: 'primary', includeCrossover: false })
  assert.deepEqual(toSearchSelection({ ageGroup: 'preschool', category: 'school', includeCrossover: true }),
    { ageGroup: 'preschool', educationLevel: null, includeCrossover: true })
  assert.deepEqual(toSearchSelection({ ageGroup: 'grade_8_12', category: 'school', includeCrossover: true }),
    { ageGroup: 'grade_8_12', educationLevel: null, includeCrossover: false })
  assert.deepEqual(toSearchSelection({ ageGroup: null }),
    { ageGroup: null, educationLevel: null, includeCrossover: false })
})

test('enrolment and birth years follow the calendar-year rule', () => {
  assert.deepEqual(enrolmentYears(new Date('2026-09-24'), 3), [2026, 2027, 2028])
  const births = birthYearsFor(2027)
  assert.equal(births[0], 2027)
  assert.equal(births.at(-1), 2009)
})
