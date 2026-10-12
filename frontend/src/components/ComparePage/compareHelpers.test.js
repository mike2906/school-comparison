import assert from 'node:assert/strict'
import test from 'node:test'

import { normalizeCompareValue, nvoNotApplicable, parseIds } from './compareHelpers.js'

test('parseIds keeps unique positive integers in order', () => {
  assert.deepEqual(parseIds('3, 1,3,abc,-2,0,7'), [3, 1, 7])
  assert.deepEqual(parseIds(''), [])
  assert.deepEqual(parseIds(null), [])
})

test('normalizeCompareValue ignores case and array order', () => {
  assert.equal(normalizeCompareValue('Yes'), normalizeCompareValue('yes'))
  assert.equal(normalizeCompareValue(['b', 'a']), normalizeCompareValue(['a', 'b']))
})

test('nvoNotApplicable covers international schools and kindergartens without results', () => {
  assert.equal(nvoNotApplicable({ school_type: 'international' }), true)
  assert.equal(nvoNotApplicable({ school_type: 'state', education_level: 'kindergarten' }), true)
  assert.equal(nvoNotApplicable({ school_type: 'state', education_level: 'kindergarten', exam_results: [{}] }), false)
  assert.equal(nvoNotApplicable({ school_type: 'state', education_level: 'primary' }), false)
})
