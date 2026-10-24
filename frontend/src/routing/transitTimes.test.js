import { test } from 'node:test'
import assert from 'node:assert/strict'
import { arrivalTarget, clock, linesText, timetableFresh, tripMinutes } from './transitTimes.js'

test('arrive by the start the school gives for the age group, else its shift, else 08:00', () => {
  const school = { education_level: 'secondary', start_times: { grade_1_4: '07:30', grade_8_12: '08:20' } }
  const location = { age_group_shifts: [{ age_group: 'grade_5_7', shift: 'afternoon' }] }
  const at = (...args) => { const target = arrivalTarget(...args); return [clock(target.seconds), target.known] }
  assert.deepEqual(at(school, location, 'grade_1_4'), ['07:30', true])
  assert.deepEqual(at(school, location, 'grade_5_7'), ['13:30', true])
  assert.deepEqual(at({ education_level: 'secondary' }, null, 'grade_1_4'), ['08:00', false])
  // A kindergarten's children arrive over the morning: no start to know.
  assert.deepEqual(at({ education_level: 'kindergarten' }, null, 'preschool'), ['08:00', null])
  assert.deepEqual(at(null, null, 'third'), ['08:00', null])
})

test('a trip in whole minutes', () => {
  assert.equal(tripMinutes({ leave: 7 * 3600, arrive: 7 * 3600 + 29 * 60 + 40 }), 30)
  assert.equal(tripMinutes(null), null)
})

test('names the lines, a metro line by its own name', () => {
  const t = key => ({ 'transitTime.mode.bus': 'bus', 'transitTime.mode.tram': 'tram', 'transitTime.then': ', then ' })[key]
  assert.equal(linesText([{ name: '94', type: 3 }, { name: 'M2', type: 1 }, { name: '5', type: 0 }], t), 'bus 94, then M2, then tram 5')
})

test('an old or expired timetable is not shown', () => {
  const today = new Date('2026-11-10T09:00:00Z')
  assert.ok(timetableFresh(20261027, 20271023, today))
  assert.ok(!timetableFresh(20260601, 20271023, today), 'not rebuilt for months')
  assert.ok(!timetableFresh(20261027, 20261109, today), 'the feed ran out')
})
