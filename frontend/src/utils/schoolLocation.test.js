import test from 'node:test'
import assert from 'node:assert/strict'

import { getAgeGroupShifts, getLocationTags, getShiftForAgeGroup } from './schoolLocation.js'

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

test('getAgeGroupShifts drops entries without an age group', () => {
  assert.deepEqual(getAgeGroupShifts({ age_group_shifts: [{ age_group: 'a' }, { shift: 'x' }, null] }), [{ age_group: 'a' }])
  assert.deepEqual(getAgeGroupShifts({}), [])
})
