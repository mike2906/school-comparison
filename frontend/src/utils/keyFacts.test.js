import test from 'node:test'
import assert from 'node:assert/strict'

import {
  getLatestNvoFact,
  getOfferedAgeGroups,
  getShifts,
  parseSavedLocation,
} from './keyFacts.js'

const nvo = (year, subject, value, examType = 'nvo_7') => ({
  exam_type: examType, metric: 'average_score', year, subject, value,
})

test('latest NVO fact compares the latest combined score to the national average', () => {
  const averages = { by_year: { nvo_7: { 2025: { bulgarian: 60, math: 50 } } } }
  const fact = getLatestNvoFact(
    [nvo(2024, 'math', 10), nvo(2025, 'math', 70), nvo(2025, 'bulgarian', 80), nvo(2025, 'math', 1, 'nvo_4')],
    'nvo_7',
    averages,
  )
  assert.equal(fact.year, 2025)
  assert.equal(fact.value, 75)
  assert.equal(fact.national, 55)
  assert.equal(fact.diff, 20)
  assert.equal(fact.tone, 'above')
})

test('latest NVO fact without a benchmark still reports the score', () => {
  const fact = getLatestNvoFact([nvo(2025, 'math', 40)], 'nvo_7', null)
  assert.deepEqual([fact.value, fact.national, fact.tone], [40, null, null])
  // A single-subject year is labelled by its subject, not as the two-subject average.
  assert.deepEqual(fact.subjects, ['math'])
  assert.equal(getLatestNvoFact([], 'nvo_7', null), null)
  assert.equal(getLatestNvoFact([nvo(2025, 'math', 40)], null, null), null)
})

test('offered age groups and shifts are distinct across locations', () => {
  const locations = [
    { age_groups: ['grade_1_4', 'grade_5_7'], age_group_shifts: [{ shift: 'morning' }, { shift: null }] },
    { age_groups: ['grade_5_7', 'grade_8_12'], age_group_shifts: [{ shift: 'afternoon' }, { shift: 'morning' }] },
  ]
  assert.deepEqual(getOfferedAgeGroups(locations), ['grade_1_4', 'grade_5_7', 'grade_8_12'])
  assert.deepEqual(getShifts(locations), ['morning', 'afternoon'])
  assert.deepEqual(getOfferedAgeGroups(null), [])
})

test('saved location parsing tolerates missing and malformed values', () => {
  assert.deepEqual(parseSavedLocation('{"lat":42.7,"lng":23.3,"address":"x"}'), { lat: 42.7, lng: 23.3 })
  assert.equal(parseSavedLocation(null), null)
  assert.equal(parseSavedLocation('not json'), null)
  assert.equal(parseSavedLocation('{"lat":"42"}'), null)
})
