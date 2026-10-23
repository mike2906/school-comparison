import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { chanceGroup, VERDICT_GROUP } from './chanceGroup.js'

// The verdicts compareWithCutoff documents, plus those compareRows and grade1Verdict add.
function documentedVerdicts() {
  const source = readFileSync(new URL('./calculator.js', import.meta.url), 'utf8')
  const doc = source.slice(0, source.indexOf('export function compareWithCutoff'))
  const comment = doc.slice(doc.lastIndexOf('/**'))
  return [...comment.matchAll(/'(\w+)'/g)].map(match => match[1]).concat(['mixed', 'noCutoff', 'needsAddress'])
}

test('every verdict a school can get has a group', () => {
  const verdicts = documentedVerdicts()
  assert.ok(verdicts.includes('sameGroup') && verdicts.includes('noPlaces'), 'the doc comment was read')
  for (const verdict of verdicts) assert.ok(VERDICT_GROUP[verdict], `no group for '${verdict}'`)
})

test('below only when no other way in is possible', () => {
  assert.equal(chanceGroup([]), 'unknown')
  assert.equal(chanceGroup([{ stage: 'grade_1', verdict: 'below', lines: [] }]), 'below')
  assert.equal(chanceGroup([{ stage: 'grade_1', verdict: 'below', lines: [{ otherRules: true }] }]), 'close')
  assert.equal(chanceGroup([{ stage: 'kindergarten', verdict: 'below', lines: [], socialPriority: null }]), 'close')
  assert.equal(chanceGroup([{ stage: 'kindergarten', verdict: 'below', lines: [], socialPriority: false }]), 'below')
  assert.equal(chanceGroup([{ stage: 'kindergarten', verdict: 'below', lines: [], socialPriority: false, specialPlaces: true }]), 'close')
})

test('the best of several verdicts counts', () => {
  assert.equal(chanceGroup([{ verdict: 'noCutoff', lines: [] }, { stage: 'grade_1', verdict: 'above', lines: [] }]), 'likely')
})

test('mixed buildings take their shared group, else close', () => {
  const lines = (...verdicts) => verdicts.map(verdict => ({ verdict }))
  assert.equal(chanceGroup([{ stage: 'grade_1', verdict: 'mixed', lines: lines('above', 'everyone') }]), 'likely')
  assert.equal(chanceGroup([{ stage: 'grade_1', verdict: 'mixed', lines: lines('below', 'noPlaces') }]), 'below')
  assert.equal(chanceGroup([{ stage: 'grade_1', verdict: 'mixed', lines: lines('above', 'below') }]), 'close')
})
