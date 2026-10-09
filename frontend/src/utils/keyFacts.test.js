import test from 'node:test'
import assert from 'node:assert/strict'

import {
  getHeadlineTuition,
  getLatestNvoFact,
  getOfferedAgeGroups,
  getShifts,
  parseSavedLocation,
} from './keyFacts.js'

const row = (overrides) => ({
  category: 'tuition',
  currency: 'EUR',
  period: 'yearly',
  year_status: 'current',
  academic_year_canonical: '2026/2027',
  ...overrides,
})

test('headline tuition annualises the current cohort in euro', () => {
  const fact = getHeadlineTuition([
    row({ amount: 8965 }),
    row({ category: 'registration', period: 'one_time', amount: 75 }),
  ])
  assert.equal(fact.kind, 'yearly')
  assert.equal(fact.min, 8965)
  assert.equal(fact.max, 8965)
  assert.equal(fact.currency, 'EUR')
  assert.equal(fact.academicYear, '2026/2027')
})

test('headline tuition keeps a currency without a fixed euro rate', () => {
  const fact = getHeadlineTuition([row({ currency: 'USD', period: 'monthly', amount: 1000 })])
  assert.deepEqual([fact.kind, fact.min, fact.currency], ['yearly', 12000, 'USD'])
})

test('headline tuition falls back to a fee with an unstated period, then to nothing', () => {
  const unstated = getHeadlineTuition([row({ period: null, amount: 4000 })])
  assert.deepEqual([unstated.kind, unstated.amount, unstated.currency], ['unstated_period', 4000, 'EUR'])
  assert.equal(getHeadlineTuition([row({ category: 'food', amount: 100 })]), null)
  assert.equal(getHeadlineTuition([]), null)
  assert.equal(getHeadlineTuition(undefined), null)
})

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
