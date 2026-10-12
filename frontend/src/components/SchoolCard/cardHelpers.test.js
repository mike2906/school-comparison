import test from 'node:test'
import assert from 'node:assert/strict'

import {
  getLocationAgeGroups,
  getLocationTags,
  getScheduleLine,
  getShiftForAgeGroup,
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

test('getLocationTags ignores non-arrays and empty values', () => {
  assert.deepEqual(getLocationTags({ location_tags: ['x', '', 'y'] }), ['x', 'y'])
  assert.deepEqual(getLocationTags({ location_tags: 'x' }), [])
  assert.deepEqual(getLocationTags({}), [])
})

test('getShiftForAgeGroup matches the group, else the first shift', () => {
  const location = {
    age_group_shifts: [
      { age_group: 'primary', shift: 'morning' },
      { age_group: 'secondary', shift: 'afternoon' },
      { shift: 'ignored' },
    ],
  }
  assert.equal(getShiftForAgeGroup(location, 'secondary').shift, 'afternoon')
  assert.equal(getShiftForAgeGroup(location, 'missing').shift, 'morning')
  assert.equal(getShiftForAgeGroup(location, null).shift, 'morning')
  assert.equal(getShiftForAgeGroup({}, 'primary'), null)
})

test('getScheduleLine returns null without a shift', () => {
  const t = key => key
  assert.equal(getScheduleLine(null, {}, t), null)
  assert.equal(getScheduleLine({}, {}, t), null)
})
