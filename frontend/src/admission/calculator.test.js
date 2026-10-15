import test from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  cutoffValue,
  campaignEnd,
  compareWithCutoff,
  defaultStartYear,
  formatRange,
  measuredOnFor,
  pickLanguage,
  ruleSetFor,
  sameRules,
  sofiaToday,
  stageById,
  startYearChoices,
  summaryRowsFor,
} from './calculator.js'

function ruleFiles(dir) {
  return readdirSync(dir).flatMap(name => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? ruleFiles(path) : name.endsWith('.json') ? [path] : []
  })
}
const RULE_SETS = ruleFiles(fileURLToPath(new URL('./rules', import.meta.url)))
  .map(file => JSON.parse(readFileSync(file, 'utf8')))

test('from October the next September is the default start year', () => {
  assert.equal(defaultStartYear(new Date(2026, 8, 30)), 2026)
  assert.equal(defaultStartYear(new Date(2026, 9, 1)), 2027)
  // A tab whose campaign is still ranking stays on it: kindergartens until 27 November.
  const kg = stageById('kindergarten')
  assert.equal(defaultStartYear(new Date(2026, 9, 15), { ruleSets: RULE_SETS, stage: kg, today: '2026-10-15' }), 2026)
  assert.equal(defaultStartYear(new Date(2026, 10, 28), { ruleSets: RULE_SETS, stage: kg, today: '2026-11-28' }), 2027)
  assert.equal(defaultStartYear(new Date(2026, 9, 15), { ruleSets: RULE_SETS, stage: stageById('grade_1'), today: '2026-10-15' }), 2027)
  assert.equal(campaignEnd(RULE_SETS.find(r => r.id === 'bg/sofia/kindergarten/2026-2027')), '2026-11-27')
  assert.deepEqual(startYearChoices(new Date(2026, 9, 14)), [2026, 2027, 2028])
  assert.deepEqual(startYearChoices(new Date(2026, 2, 1)), [2026, 2027])
})

test('durations are counted to the date each stage uses', () => {
  const kindergarten = RULE_SETS.find(r => r.id === 'bg/sofia/kindergarten/2026-2027')
  const grade1 = RULE_SETS.find(r => r.id === 'bg/sofia/grade_1/2026-2027')
  assert.equal(measuredOnFor(kindergarten), kindergarten.schedule.first_general_ranking)
  assert.equal(measuredOnFor(grade1), grade1.schedule.applications_close)
  assert.equal(measuredOnFor({ campaign: '2030/2031', schedule: {} }), '2030-05-31')
  // During the autumn rankings an address is counted to a ranking after today.
  assert.equal(measuredOnFor(kindergarten, '2026-10-15'), '2026-10-15')
  assert.equal(measuredOnFor(kindergarten, '2026-04-01'), kindergarten.schedule.first_general_ranking)
  assert.equal(measuredOnFor(kindergarten, '2026-12-01'), kindergarten.schedule.first_general_ranking)
})

test('each tab finds its rule set, and a later year is only an illustration', () => {
  const kg = ruleSetFor(RULE_SETS, stageById('kindergarten'), 2027, '2026-10-14')
  assert.equal(kg.ruleSet.id, 'bg/sofia/kindergarten/2027-2028')
  assert.equal(kg.applies, 'provisional')
  assert.equal(kg.campaign, '2027/2028')
  assert.deepEqual(kg.events.map(e => e.status), ['suspension_lifted'])

  const g8 = ruleSetFor(RULE_SETS, stageById('grade_8'), 2026, '2026-10-14')
  assert.equal(g8.ruleSet.id, 'bg/grade_8/2026-2027', 'the national rules apply in Sofia')
  assert.equal(g8.applies, 'verified')

  const later = ruleSetFor(RULE_SETS, stageById('grade_1'), 2029, '2026-10-14')
  assert.equal(later.applies, 'illustration')
  assert.equal(later.campaign, '2029/2030')
})

test('an unknown tab falls back to the first one', () => {
  assert.equal(stageById('grade_5').id, 'kindergarten')
})

test('cut-offs are read against the rules version they were ranked under', () => {
  const rules = RULE_SETS.find(r => r.id === 'bg/sofia/kindergarten/2027-2028')
  assert.equal(sameRules(`bg/sofia/kindergarten/2026-2027@${rules.version}`, rules), true)
  assert.equal(sameRules('bg/sofia/kindergarten/2025-2026@2024-02-29', rules), false)
  assert.equal(sameRules('bg/sofia/grade_1/2026-2027@2026-03-12', rules), false)
  assert.equal(sameRules(undefined, rules), null)
  assert.equal(sameRules('bg/national/grade_8/2026-2027', rules), null, 'no version: unknown, not different')
})

test('summary rows are picked per tab and age group', () => {
  const summary = [
    { stage: 'nursery', age_group: 'nursery' },
    { stage: 'kindergarten', age_group: 'second' },
    { stage: 'preparatory', age_group: null },
    { stage: 'grade_1' },
    { stage: 'grade_8', class_code: '01234' },
  ]
  assert.equal(summaryRowsFor(summary, stageById('kindergarten')).length, 3)
  assert.deepEqual(summaryRowsFor(summary, stageById('kindergarten'), { ageGroup: 'second' }).map(r => r.stage), ['kindergarten', 'preparatory'])
  assert.deepEqual(summaryRowsFor(summary, stageById('grade_8')).map(r => r.class_code), ['01234'])
  assert.deepEqual(summaryRowsFor(undefined, stageById('grade_1')), [])
})

test('a round reports a score, a group with points, points, or room for everyone', () => {
  assert.deepEqual(cutoffValue({ min_score: 401.5 }), { kind: 'score', score: 401.5 })
  assert.deepEqual(cutoffValue({ min_score: 380, min_score_boys: 380, min_score_girls: 412.25 }), { kind: 'quota', boys: 380, girls: 412.25 })
  assert.deepEqual(cutoffValue({ last_admitted_group: 2, last_admitted_points: 4 }), { kind: 'group', group: 2, points: 4 })
  assert.deepEqual(cutoffValue({ last_admitted_points: 14 }), { kind: 'points', points: 14 })
  assert.deepEqual(cutoffValue({ undersubscribed: true, last_admitted_points: 3 }), { kind: 'everyone' })
  assert.deepEqual(cutoffValue({ places: 0, admitted_count: 0 }), { kind: 'noPlaces' })
  assert.equal(cutoffValue({}), null)
  assert.equal(cutoffValue(undefined), null)
})

test('ranges and texts are formatted for the page', () => {
  assert.equal(formatRange({ min: 5, max: 5 }), '5')
  assert.equal(formatRange({ min: 3, max: 8 }), '3–8')
  assert.equal(pickLanguage({ bg: 'Наредба', en: 'Ordinance' }, 'en'), 'Ordinance')
  assert.equal(pickLanguage({ bg: 'Наредба' }, 'en'), 'Наредба')
  assert.equal(pickLanguage('plain', 'en'), 'plain')
  assert.equal(sofiaToday(new Date('2026-10-14T22:30:00Z')), '2026-10-15', 'Sofia is ahead of UTC')
})

test('a result is compared with the last admitted child', () => {
  const points = (min, max = min) => ({ min, max })
  const round = { last_admitted_points: 16 }
  assert.equal(compareWithCutoff({ points: points(19) }, round), 'above')
  assert.equal(compareWithCutoff({ points: points(12) }, round), 'below')
  assert.equal(compareWithCutoff({ points: points(16) }, round), 'tie')
  assert.equal(compareWithCutoff({ points: points(12, 19) }, round), 'unsure')
  assert.equal(compareWithCutoff({ points: points(3) }, { undersubscribed: true }), 'everyone')
  assert.equal(compareWithCutoff({ points: points(3) }, { places: 0 }), 'noPlaces')
  // First grade: a lower group ranks first; points only matter inside the same group.
  const g1 = { last_admitted_group: 2, last_admitted_points: 4 }
  assert.equal(compareWithCutoff({ group: points(1), points: points(0) }, g1), 'above')
  assert.equal(compareWithCutoff({ group: points(3), points: points(20) }, g1), 'below')
  assert.equal(compareWithCutoff({ group: points(2), points: points(5) }, g1), 'above')
  assert.equal(compareWithCutoff({ group: points(1, 3), points: points(5) }, g1), 'unsure')
  assert.equal(compareWithCutoff({ points: points(10) }, null), null)
})
