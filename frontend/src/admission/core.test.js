import test from 'node:test'
import assert from 'node:assert/strict'

import {
  addRanges,
  bestOf,
  campaignForStartYear,
  choiceRankPoints,
  durationTierPoints,
  flagPoints,
  moreThanYears,
  range,
  revisionOn,
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
  { id: 'bg/sofia/kindergarten/2025-26', country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2025-26' },
  { id: 'bg/sofia/kindergarten/2026-27', country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2026-27',
    revisions: [{ id: '2026-03-12', in_force_from: '2026-03-19' }, { id: '2026-06-01', in_force_from: '2026-06-10' }],
    status_events: [{ status: 'suspended', from: '2026-04-14', to: '2026-05-07' }] },
  { id: 'bg/grade_8/2026-27', country: 'bg', city: null, stage: 'grade_8', campaign: '2026-27' },
]

test('selectRuleSet never carries rules forward silently', () => {
  const exact = selectRuleSet(SETS, { country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2026-27' })
  assert.equal(exact.ruleSet.campaign, '2026-27')
  assert.equal(exact.confirmed, true)

  const later = selectRuleSet(SETS, { country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2027-28' })
  assert.equal(later.ruleSet.campaign, '2026-27')
  assert.equal(later.confirmed, false)

  assert.equal(selectRuleSet(SETS, { country: 'bg', city: 'varna', stage: 'kindergarten', campaign: '2026-27' }), null)
  assert.equal(selectRuleSet(SETS, { country: 'bg', city: 'sofia', stage: 'kindergarten', campaign: '2024-25' }), null)
  assert.equal(selectRuleSet(SETS, { country: 'bg', city: null, stage: 'grade_8', campaign: '2026-27' }).confirmed, true)
})

test('revisionOn and statusEventsOn read the dated history', () => {
  const set = SETS[1]
  assert.equal(revisionOn(set, '2026-03-18'), null)
  assert.equal(revisionOn(set, '2026-03-19').id, '2026-03-12')
  assert.equal(revisionOn(set, '2026-07-01').id, '2026-06-01')
  assert.equal(statusEventsOn(set, '2026-04-20').length, 1)
  assert.equal(statusEventsOn(set, '2026-05-08').length, 0)
  assert.equal(ruleVersionKey(set, revisionOn(set, '2026-05-22')), 'bg/sofia/kindergarten/2026-27@2026-03-12')
})

test('campaignForStartYear names the school year', () => {
  assert.equal(campaignForStartYear(2026), '2026-27')
  assert.equal(campaignForStartYear(2099), '2099-00')
})
