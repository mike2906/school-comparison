import { test } from 'node:test'
import assert from 'node:assert/strict'
import { arriveBySeconds, clock, linesText, timetableFresh, tripMinutes } from './transitTimes.js'

test('arrive by the shift start for the age group, else 08:00', () => {
  const location = { age_group_shifts: [{ age_group: 'grade_5_7', shift: 'afternoon' }, { age_group: 'grade_1_4', shift: 'morning' }] }
  assert.equal(clock(arriveBySeconds(location, 'grade_5_7')), '13:30')
  assert.equal(clock(arriveBySeconds(location, 'grade_1_4')), '07:30')
  assert.equal(clock(arriveBySeconds(location, 'third')), '08:00')
  assert.equal(clock(arriveBySeconds(null, 'third')), '08:00')
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
