import test from 'node:test'
import assert from 'node:assert/strict'

import { pointsForFit, pickSelectedMarker } from './mapFit.js'

// Stand-in for Leaflet's distanceTo in node tests; only the ordering matters here.
const distance = ([aLat, aLng], [bLat, bLng]) => Math.hypot(aLat - bLat, aLng - bLng)

test('pointsForFit drops the farthest outliers around the city', () => {
  const city = Array.from({ length: 40 }, (_, i) => [42.69 + (i % 5) * 0.01, 23.32 + Math.floor(i / 5) * 0.01])
  const outliers = [[42.5, 23.0], [42.9, 23.8]]
  const kept = pointsForFit([...city, ...outliers], { distance })
  assert.equal(kept.length, 40)
  assert.ok(!kept.some(([lat]) => lat === 42.5 || lat === 42.9))
})

test('pointsForFit keeps everything for small sets', () => {
  const few = [[42.7, 23.3], [42.5, 23.0]]
  assert.deepEqual(pointsForFit(few, { distance }), few)
  assert.deepEqual(pointsForFit([], { distance }), [])
  // No distance function, no trimming.
  const many = Array.from({ length: 30 }, (_, i) => [42 + i, 23])
  assert.deepEqual(pointsForFit(many), many)
})

const schoolPins = [
  { key: '106-106', location: { id: 106, is_primary: true } },
  { key: '106-107', location: { id: 107, is_primary: false } },
]

test('pickSelectedMarker highlights the clicked location, not the primary one', () => {
  assert.equal(pickSelectedMarker(schoolPins, '106-107').key, '106-107')
})

test('pickSelectedMarker falls back to the primary location, then the first pin', () => {
  assert.equal(pickSelectedMarker(schoolPins, null).key, '106-106')
  // A pin clicked for another school, or one filtered out since, is ignored.
  assert.equal(pickSelectedMarker(schoolPins, '531-1076').key, '106-106')
  assert.equal(pickSelectedMarker([schoolPins[1]], null).key, '106-107')
  assert.equal(pickSelectedMarker([], '106-107'), null)
})
