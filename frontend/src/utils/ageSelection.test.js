import test from 'node:test'
import assert from 'node:assert/strict'

import { categoryForGroup, toSearchSelection, preschoolWhere, enrolmentYears, birthYearsFor } from './ageSelection.js'

test('categoryForGroup treats preschool as kindergarten by default', () => {
  assert.equal(categoryForGroup('first'), 'kindergarten')
  assert.equal(categoryForGroup('preschool'), 'kindergarten')
  assert.equal(categoryForGroup('grade_5_7'), 'school')
  assert.equal(categoryForGroup(null), null)
})

test('toSearchSelection: preschool defaults to both, or is scoped by where', () => {
  assert.deepEqual(toSearchSelection({ ageGroup: 'preschool' }),
    { ageGroup: 'preschool', educationLevel: null, includeCrossover: true })
  assert.deepEqual(toSearchSelection({ ageGroup: 'preschool', where: 'school' }),
    { ageGroup: 'preschool', educationLevel: 'primary', includeCrossover: false })
  assert.deepEqual(toSearchSelection({ ageGroup: 'preschool', where: 'kindergarten' }),
    { ageGroup: 'preschool', educationLevel: 'kindergarten', includeCrossover: false })
  assert.deepEqual(toSearchSelection({ ageGroup: 'grade_8_12', where: 'school' }),
    { ageGroup: 'grade_8_12', educationLevel: null, includeCrossover: false })
  assert.deepEqual(toSearchSelection({ ageGroup: null }),
    { ageGroup: null, educationLevel: null, includeCrossover: false })
})

test('preschoolWhere reads the URL-backed filters; old links mean both', () => {
  assert.equal(preschoolWhere({ includeCrossover: true }), 'both')
  assert.equal(preschoolWhere({ educationLevel: 'primary' }), 'school')
  assert.equal(preschoolWhere({ educationLevel: 'kindergarten' }), 'kindergarten')
  assert.equal(preschoolWhere({}), 'both')
})

test('enrolment and birth years follow the calendar-year rule', () => {
  assert.deepEqual(enrolmentYears(new Date('2026-09-24'), 3), [2026, 2027, 2028])
  const births = birthYearsFor(2027)
  assert.equal(births[0], 2027)
  assert.equal(births.at(-1), 2009)
})
