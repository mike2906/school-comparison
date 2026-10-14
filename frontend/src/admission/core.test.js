import test from 'node:test'
import assert from 'node:assert/strict'

import {
  addRanges,
  bestOf,
  campaignForStartYear,
  choiceRankPoints,
  durationTierPoints,
  isValidSpan,
  flagPoints,
  moreThanYears,
  range,
  ruleVersionKey,
  selectRuleSet,
  statusEventsOn,
  wholeYearsBetween,
} from './core.js'

test('flagPoints turns yes, no and not sure into a range', () => {
  assert.deepEqual(flagPoints(true, 2), range(2))
  assert.deepEqual(flagPoints(false, 2), range(0))
  assert.deepEqual(flagPoints(null, 2), range(0, 2))
  assert.deepEqual(flagPoints(undefined, -1), range(-1, 0))
})

test('bestOf keeps only the best of mutually exclusive criteria', () => {
  assert.deepEqual(bestOf([range(1), range(1), range(0)]), range(1))
  assert.deepEqual(bestOf([range(0), range(0, 1)]), range(0, 1))
  assert.deepEqual(bestOf([]), range(0))
  assert.deepEqual(addRanges(range(1, 2), range(3), range(0, 4)), range(4, 9))
})

test('wholeYearsBetween counts completed years and handles the anniversary', () => {
  assert.equal(wholeYearsBetween('2023-03-19', '2026-03-19'), 3)
  assert.equal(wholeYearsBetween('2023-03-19', '2026-03-18'), 2)
  assert.equal(wholeYearsBetween('2024-02-29', '2025-02-28'), 0)
  assert.equal(wholeYearsBetween('2026-05-01', '2026-04-01'), null)
  assert.equal(wholeYearsBetween(null, '2026-04-01'), null)
})

test('moreThanYears is strict: the anniversary itself is not more than', () => {
  assert.equal(moreThanYears('2023-05-22', '2026-05-22', 3), false)
  assert.equal(moreThanYears('2023-05-22', '2026-05-23', 3), true)
  assert.equal(moreThanYears('2023-05-21', '2026-05-22', 3), true)
})

test('durationTierPoints picks the first tier that applies and spans all tiers when unknown', () => {
  const tiers = [
    { more_than_years: 3, points: 8 },
    { at_least_years: 1, points: 5 },
    { points: 3 },
  ]
  assert.deepEqual(durationTierPoints(tiers, '2020-01-01', '2026-05-22'), range(8))
  assert.deepEqual(durationTierPoints(tiers, '2023-05-22', '2026-05-22'), range(5))
  assert.deepEqual(durationTierPoints(tiers, '2025-05-22', '2026-05-22'), range(5))
  assert.deepEqual(durationTierPoints(tiers, '2025-05-23', '2026-05-22'), range(3))
  assert.deepEqual(durationTierPoints(tiers, null, '2026-05-22'), range(3, 8))
})

test('choiceRankPoints follows the wish order table', () => {
  assert.deepEqual(choiceRankPoints([3, 2, 1], 1), range(3))
  assert.deepEqual(choiceRankPoints([3, 2, 1], 3), range(1))
  assert.deepEqual(choiceRankPoints([3, 2, 1], 4), range(0))
  assert.deepEqual(choiceRankPoints([3, 2, 1], null), range(0, 3))
})

const SETS = [
  { id: 'bg/sofia/kindergarten/2025-2026', country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2025/2026', verified: { status: 'verified' } },
  { id: 'bg/sofia/kindergarten/2026-2027', country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2026/2027', version: '2026-03-12',
    verified: { status: 'verified' },
    status_events: [{ status: 'suspended', from: '2026-04-01', to: '2026-05-07' }] },
  { id: 'bg/sofia/kindergarten/2027-2028', country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2027/2028', verified: { status: 'provisional' } },
  { id: 'bg/grade_8/2026-2027', country: 'bg', city: null, stage: 'grade_8', campaign: '2026/2027', verified: { status: 'verified' } },
]

test('selectRuleSet says how far the rules apply and never passes old rules off as current', () => {
  const exact = selectRuleSet(SETS, { country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2026/2027' })
  assert.equal(exact.ruleSet.campaign, '2026/2027')
  assert.equal(exact.applies, 'verified')

  const provisional = selectRuleSet(SETS, { country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2027/2028' })
  assert.equal(provisional.applies, 'provisional')

  const later = selectRuleSet(SETS, { country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2028/2029' })
  assert.equal(later.ruleSet.campaign, '2027/2028')
  assert.equal(later.applies, 'illustration')

  assert.equal(selectRuleSet(SETS, { country: 'bg', city: 'varna', stage: 'kindergarten', campaign: '2026/2027' }), null)
  assert.equal(selectRuleSet(SETS, { country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2024/2025' }), null)
})

test('selectRuleSet finds national rules for a city', () => {
  const national = selectRuleSet(SETS, { country: 'bg', city: 'sofia', stage: 'grade_8', campaign: '2026/2027' })
  assert.equal(national.ruleSet.id, 'bg/grade_8/2026-2027')
  assert.equal(national.applies, 'verified')
})

test('statusEventsOn and ruleVersionKey', () => {
  const set = SETS[1]
  assert.equal(statusEventsOn(set, '2026-04-20').length, 1)
  assert.equal(statusEventsOn(set, '2026-05-08').length, 0)
  assert.equal(ruleVersionKey(set), 'bg/sofia/kindergarten/2026-2027@2026-03-12')
})

test('impossible, future and leap-day dates', () => {
  assert.equal(isValidSpan('2026-02-30', '2026-05-22'), false)
  assert.equal(isValidSpan('2026-06-01', '2026-05-22'), false)
  assert.equal(isValidSpan('2026-05-22', null), false)
  // 29 February reaches its anniversary on 1 March in a common year.
  assert.equal(wholeYearsBetween('2024-02-29', '2027-02-28'), 2)
  assert.equal(wholeYearsBetween('2024-02-29', '2027-03-01'), 3)
  assert.equal(moreThanYears('2024-02-29', '2027-03-01', 3), false)
  assert.equal(moreThanYears('2024-02-29', '2027-03-02', 3), true)
  const tiers = [{ more_than_years: 3, points: 8 }, { at_least_years: 1, points: 5 }, { points: 3 }]
  assert.deepEqual(durationTierPoints(tiers, '2026-06-01', '2026-05-22'), range(3, 8))
  assert.deepEqual(durationTierPoints(tiers, '2020-01-01', null), range(3, 8))
  assert.deepEqual(durationTierPoints([], '2020-01-01', '2026-05-22'), range(0))
})

test('campaignForStartYear names the school year', () => {
  assert.equal(campaignForStartYear(2026), '2026/2027')
  assert.equal(campaignForStartYear(2099), '2099/2100')
})
