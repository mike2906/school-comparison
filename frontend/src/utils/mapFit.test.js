import test from 'node:test'
import assert from 'node:assert/strict'

import { pointsForFit } from './mapFit.js'

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
