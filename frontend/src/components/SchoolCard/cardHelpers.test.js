import test from 'node:test'
import assert from 'node:assert/strict'

import {
  getLocationAgeGroups,
  getScheduleLine,
  hexToRgba,
} from './cardHelpers.js'

test('hexToRgba converts hex with or without #', () => {
  assert.equal(hexToRgba('#ff8000', 0.5), 'rgba(255, 128, 0, 0.5)')
  assert.equal(hexToRgba('000000', 1), 'rgba(0, 0, 0, 1)')
})

test('getLocationAgeGroups prefers the list, falls back to the single group', () => {
  assert.deepEqual(getLocationAgeGroups({ age_groups: ['a', null, 'b'] }), ['a', 'b'])
  assert.deepEqual(getLocationAgeGroups({ age_group: 'c' }), ['c'])
  assert.deepEqual(getLocationAgeGroups(null), [])
})

test('getScheduleLine returns null without a shift', () => {
  const t = key => key
  assert.equal(getScheduleLine(null, {}, t), null)
  assert.equal(getScheduleLine({}, {}, t), null)
})
