import test from 'node:test'
import assert from 'node:assert/strict'

import { buildOptions, calculatorStage, entryFits } from './childOptions.js'

const monthly = amount => [{ id: 1, category: 'tuition', amount, currency: 'EUR', period: 'monthly', year_status: 'current' }]

test('the calculator applies to kindergarten ages and the first-grade year only', () => {
  assert.equal(calculatorStage('second', 2023, 2027), 'kindergarten')
  assert.equal(calculatorStage('grade_1_4', 2020, 2027), 'grade_1')
  assert.equal(calculatorStage('grade_1_4', 2019, 2027), null, 'second grade')
  assert.equal(calculatorStage('grade_8_12', 2013, 2027), null)
})

test('saved answers for another child or September are not applied', () => {
  const entry = { startYear: 2027, answers: { birthYear: 2023 } }
  assert.ok(entryFits('kindergarten', entry, 2023, 2027))
  assert.ok(!entryFits('kindergarten', entry, 2022, 2027))
  assert.ok(!entryFits('kindergarten', entry, 2023, 2028))
  assert.ok(entryFits('grade_1', { startYear: 2027, answers: {} }, 2020, 2027))
})

test('schools are grouped by chance, nearest trip first, and the budget hides only fees above it', () => {
  const schools = [
    { id: 1, school_type: 'state', driveSeconds: 900, distance: 3 },
    { id: 2, school_type: 'state', driveSeconds: 300, distance: 1 },
    { id: 3, school_type: 'state', driveSeconds: null, distance: 0.5 },
    { id: 4, school_type: 'private', pricing: monthly(900), driveSeconds: 100 },
    { id: 5, school_type: 'private', pricing: monthly(400), driveSeconds: 200 },
    { id: 6, school_type: 'private', pricing: [], driveSeconds: 50 },
  ]
  const verdicts = { 1: [{ stage: 'grade_1', verdict: 'above', lines: [] }], 2: [{ stage: 'grade_1', verdict: 'above', lines: [] }], 3: [{ stage: 'grade_1', verdict: 'below', lines: [] }] }
  const { groups, overBudget } = buildOptions(schools, { verdictsFor: school => verdicts[school.id] || [], monthlyBudget: 500 })
  assert.deepEqual(groups.likely.map(s => s.id), [2, 1])
  assert.deepEqual(groups.below.map(s => s.id), [3])
  assert.deepEqual(groups.unknown.map(s => s.id), [6, 5], 'a fee with no monthly figure stays')
  assert.equal(overBudget, 1)
  const all = buildOptions(schools, { verdictsFor: () => [], monthlyBudget: 500, showOverBudget: true })
  assert.equal(all.groups.unknown.length, 6)
})

test('by public transport the earliest leaving goes last, whatever the drive', () => {
  const schools = [
    { id: 1, school_type: 'state', driveSeconds: 300, tripSeconds: 2400 },
    { id: 2, school_type: 'state', driveSeconds: 900, tripSeconds: 1200 },
    { id: 3, school_type: 'state', driveSeconds: 100, tripSeconds: null, distance: 9 },
  ]
  const { groups } = buildOptions(schools, { verdictsFor: () => [] })
  assert.deepEqual(groups.unknown.map(s => s.id), [2, 1, 3], 'no trip in time sorts after every trip')
})

test('two schools the cards show at the same drive minutes go nearest first', () => {
  // Lozenets, 2026-10-26: 107 OU at 0.6 km was listed above 122 OU at 0.1 km, both "~4 min".
  const schools = [
    { id: 107, school_type: 'state', driveSeconds: 130, distance: 0.6 },
    { id: 122, school_type: 'state', driveSeconds: 145, distance: 0.1 },
  ]
  const { groups } = buildOptions(schools, { verdictsFor: () => [] })
  assert.deepEqual(groups.unknown.map(s => s.id), [122, 107])
})
