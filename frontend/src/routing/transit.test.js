import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { decodeFootGraph, snapToFoot, walkFrom } from './footGraph.js'
import { arriveBy, decodeTimetable, schoolWalks, walkToStops, ACCESS_LIMIT, BOARD_SLACK, CHANGE_SLACK, WALK_ONLY_LIMIT } from './transit.js'
import { buildTimetable } from '../../../tools/transit/build_timetable.mjs'

// Built from tools/transit/tiny.osm (map in that file) and the timetable in
// tools/transit/tiny-gtfs: bus 1 along the main street (S1 07:00, 07:10 ... 07:50, 5 min a
// stop to S3 and S5), tram 2 up the north street (N3 07:12, 07:22 ... to N11 in 6 min). The
// feed asks for 5 min to change from S3 to N3. Wednesday 28 October is a holiday timetable.
const file = readFileSync(new URL('./testdata/tiny.foot', import.meta.url))
const fg = decodeFootGraph(file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength))
const gtfs = fileURLToPath(new URL('../../../tools/transit/tiny-gtfs', import.meta.url))
const node = { 1: [42.700, 23.300], 2: [42.700, 23.305], 3: [42.700, 23.310], 5: [42.700, 23.320], 10: [42.705, 23.310], 11: [42.715, 23.310], 31: [42.705, 23.305] }
const at = (n, dLat = 0) => ({ lat: node[n][0] + dLat, lng: node[n][1] })
const school = { id: 7, locations: [{ lat: 42.7001, lng: 23.3199 }] }
const built = buildTimetable(gtfs, fg, { schools: [school], today: new Date('2026-10-27T10:00:00Z') })
const tt = decodeTimetable(built.file.buffer.slice(built.file.byteOffset, built.file.byteOffset + built.file.byteLength))
const hm = (h, m) => h * 3600 + m * 60

const walkSeconds = (a, b, limit = 3600) => {
  const from = snapToFoot(fg, a.lng, a.lat)
  return walkFrom(fg, from, limit * 10).costTo(snapToFoot(fg, b.lng, b.lat)) / 10
}

function trip(home, dest, deadline, accessLimit = ACCESS_LIMIT) {
  const { snap, stops } = walkToStops(tt, fg, home, accessLimit)
  const walks = schoolWalks(tt, fg, dest)
  const walkSec = walkFrom(fg, snap, WALK_ONLY_LIMIT * 10).costTo(walks.snap) / 10
  return arriveBy(tt, stops, [{ id: 1, egress: walks.stops, deadline, walkSec }]).get(1)
}

test('rejects a file that is not a foot graph', () => {
  assert.throws(() => decodeFootGraph(new ArrayBuffer(64)), /Unknown foot graph format/)
})

test('walks along streets at 4 km/h', () => {
  const s = walkSeconds(at(1), at(5)) // 4 steps of ~410 m
  assert.ok(Math.abs(s - 1476) < 15, `${s} s`)
})

test('ignores one-way streets and never walks a motorway', () => {
  // Up the one-way north street the wrong way is fine; the motorway shortcut is not taken.
  const s = walkSeconds(at(11), at(1))
  assert.ok(Math.abs(s - (1667 + 820) / 1.111) < 30, `${s} s`)
})

test('a fence on a footpath blocks it', () => {
  // From 31 to 2 the footway is shorter but fenced: round by 10 and 3 instead.
  const s = walkSeconds(at(31), at(2))
  assert.ok(Math.abs(s - (410 + 556 + 410) / 1.111) < 20, `${s} s`)
})

test('a walk beyond the limit costs Infinity', () => {
  assert.equal(walkSeconds(at(1), at(5), 600), Infinity)
})

test('pins the first ordinary Tuesday to Thursday, skipping a holiday timetable', () => {
  assert.equal(tt.serviceDate, 20261029)
  assert.equal(tt.validUntil, 20261231)
})

test('leaves in time for the latest bus that arrives by the deadline', () => {
  // Arrive by 07:40 near S5: the 07:20 bus reaches S5 at 07:30 (the 07:30 one only at 07:40,
  // plus the walk). Leave the walk to S1 and BOARD_SLACK before 07:20, to the whole minute.
  const r = trip(at(1, 0.0001), { lat: 42.7001, lng: 23.3199 }, hm(7, 40))
  assert.equal(r.rides, 1)
  assert.equal(r.leave, hm(7, 20 - Math.ceil(BOARD_SLACK / 60) - 1))
  assert.ok(r.arrive > hm(7, 30) && r.arrive < hm(7, 31), `${r.arrive}`)
  assert.deepEqual(r.legs.map(l => tt.routeName[l.route]), ['1'])
  assert.equal(r.legs[0].dep, hm(7, 20))
})

test('changes with the feed\'s transfer time and the change slack', () => {
  // Arrive by 07:35 near N11: tram 07:22 (at N11 07:28). To catch it, reach S3 by
  // 07:22 - 5 min (feed) - CHANGE_SLACK, so the 07:10 bus (at S3 07:15). With a 10 min walk
  // to the first stop (N3 is 12 min away on foot).
  const r = trip(at(1, 0.0001), at(11, -0.0001), hm(7, 35), 600)
  assert.deepEqual(r.legs.map(l => tt.routeName[l.route]), ['1', '2'])
  assert.equal(r.legs[0].dep, hm(7, 10))
  assert.equal(r.legs[1].dep, hm(7, 22))
  assert.ok(r.legs[1].dep - r.legs[0].arr >= 300 + CHANGE_SLACK)
})

test('one ride after a longer walk beats a change', () => {
  // N3 is within the usual 20 min walk: leaving at the same time, the tram alone needs no change.
  const r = trip(at(1, 0.0001), at(11, -0.0001), hm(7, 35))
  assert.deepEqual(r.legs.map(l => tt.routeName[l.route]), ['2'])
})

test('walking all the way wins when it is no slower', () => {
  // S3 to near S5 is ~820 m (~12 min): the bus saves nothing once the waiting is counted.
  const r = trip(at(3, 0.0001), { lat: 42.7001, lng: 23.3199 }, hm(7, 40))
  assert.equal(r.rides, 0)
  assert.equal(r.arrive, hm(7, 40))
  assert.ok(Math.abs(hm(7, 40) - r.leave - 820 / 1.111) < 30)
})

test('with no ride in time, walking is the answer if it is within the walking limit', () => {
  // No tram reaches N11 by 07:05; the ~37 min walk is still under WALK_ONLY_LIMIT.
  const r = trip(at(1, 0.0001), at(11, -0.0001), hm(7, 5))
  assert.equal(r.rides, 0)
  assert.ok(hm(7, 5) - r.leave > 35 * 60 && hm(7, 5) - r.leave < WALK_ONLY_LIMIT)
})

test('school buildings walked at build time match a walk in the browser', () => {
  const known = schoolWalks(tt, fg, school.locations[0])
  const fresh = walkToStops(tt, fg, school.locations[0])
  assert.deepEqual(known.stops.map(([s]) => s).sort(), fresh.stops.map(([s]) => s).sort())
  known.stops.forEach(([s, sec]) => {
    const f = fresh.stops.find(x => x[0] === s)
    assert.ok(Math.abs(f[1] - sec) <= 1)
  })
})
