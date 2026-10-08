import test from 'node:test'
import assert from 'node:assert/strict'

import { pointsForFit, fitKeepRatio, fitPadding, pickSelectedMarker, pinHasRoom, pinTarget, overlayFitPadding, stackedMarkersByKey } from './mapFit.js'

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

// iPhone 13 map area with the info sheet covering the bottom 236px.
const phoneMap = { x: 390, y: 551 }
const phoneRoom = { top: 60, side: 20, bottom: 256 }

test('pinHasRoom is false for a pin under the sheet or too close to an edge', () => {
  assert.equal(pinHasRoom({ x: 195, y: 200 }, phoneMap, phoneRoom), true)
  assert.equal(pinHasRoom({ x: 195, y: 335 }, phoneMap, phoneRoom), false)
  assert.equal(pinHasRoom({ x: 195, y: 30 }, phoneMap, phoneRoom), false)
  assert.equal(pinHasRoom({ x: 5, y: 200 }, phoneMap, phoneRoom), false)
})

test('pinTarget centres the pin in the part of the map above the sheet', () => {
  assert.deepEqual(pinTarget(phoneMap, phoneRoom), { x: 195, y: 177.5 })
  // Desktop: the popup opens above the pin, so the pin sits below the middle.
  assert.deepEqual(pinTarget({ x: 900, y: 770 }, { top: 340, side: 170, bottom: 20 }), { x: 450, y: 545 })
})

test('pinTarget falls back to the map centre when nothing is left free', () => {
  assert.deepEqual(pinTarget({ x: 390, y: 300 }, phoneRoom), { x: 195, y: 150 })
})

const fitPad = { top: 70, side: 40, bottom: 24 }

test('overlayFitPadding keeps locations clear of the panel and the sheet', () => {
  assert.deepEqual(overlayFitPadding(phoneMap, { top: 100, bottom: 236 }, fitPad), {
    paddingTopLeft: [40, 170],
    paddingBottomRight: [40, 260],
  })
  assert.deepEqual(overlayFitPadding({ x: 900, y: 770 }, { top: 91, bottom: 0 }, fitPad), {
    paddingTopLeft: [40, 161],
    paddingBottomRight: [40, 24],
  })
})

test('overlayFitPadding gives up when the map is too short, hidden or too narrow', () => {
  // Small phone with the compare bar under the sheet: the padding would exceed the map.
  assert.equal(overlayFitPadding({ x: 375, y: 440 }, { top: 100, bottom: 320 }, fitPad), null)
  assert.equal(overlayFitPadding({ x: 0, y: 0 }, { top: 0, bottom: 0 }, fitPad), null)
  assert.equal(overlayFitPadding({ x: 100, y: 600 }, { top: 0, bottom: 0 }, fitPad), null)
})

const pin = (schoolId, locationId, position) => ({
  key: `${schoolId}-${locationId}`,
  school: { id: schoolId },
  position,
})

test('stackedMarkersByKey lists the other schools at the same position', () => {
  const shared = [42.6996588210137, 23.332254598434336]
  const markers = [
    pin(327, 858, shared),
    pin(329, 860, shared),
    pin(600, 1153, [...shared]),
    pin(1, 1, [42.7, 23.3]),
  ]
  const stacked = stackedMarkersByKey(markers)

  assert.deepEqual(stacked.get('327-858').map(marker => marker.school.id), [329, 600])
  assert.deepEqual(stacked.get('600-1153').map(marker => marker.school.id), [327, 329])
  assert.equal(stacked.has('1-1'), false)
})

test('stackedMarkersByKey ignores a school stacked only on its own locations', () => {
  const markers = [pin(5, 1, [42.7, 23.3]), pin(5, 2, [42.7, 23.3]), pin(6, 3, [42.70002, 23.3])]
  assert.equal(stackedMarkersByKey(markers).size, 0)
})

test('fitKeepRatio leaves out a tenth at the low end and fits the central half of a whole city', () => {
  // Below 20 points pointsForFit trims nothing, whatever the ratio.
  assert.equal(fitKeepRatio(3), 0.9)
  assert.equal(fitKeepRatio(20), 0.9)
  // Sofia, all ages: 710 pins. Fitting 95% of them opened the map as three clusters.
  assert.equal(fitKeepRatio(710), 0.5)
  assert.equal(fitKeepRatio(500), 0.5)
})

test('fitKeepRatio falls evenly between a shortlist and a city', () => {
  assert.ok(Math.abs(fitKeepRatio(260) - 0.7) < 1e-9)
  for (let count = 20; count < 520; count += 1) {
    const step = fitKeepRatio(count) - fitKeepRatio(count + 1)
    assert.ok(step >= 0 && step <= 0.0011, `step at ${count}: ${step}`)
  }
})

const fitted = (points) => pointsForFit(points, { distance, keepRatio: fitKeepRatio(points.length) })

test('a handful of schools are all fitted, outliers included', () => {
  const shortlist = Array.from({ length: 18 }, (_, i) => [42.69 + (i % 5) * 0.01, 23.32 + Math.floor(i / 5) * 0.01])
  const points = [...shortlist, [42.5, 23.0]]
  assert.deepEqual(fitted(points), points)
})

test('one more result adds at most one point to the fit, and never removes one', () => {
  const line = (length) => Array.from({ length }, (_, i) => [42.4 + i * 0.001, 23.3])
  for (let count = 1; count < 720; count += 1) {
    const step = fitted(line(count + 1)).length - fitted(line(count)).length
    assert.ok(step === 0 || step === 1, `step at ${count}: ${step}`)
  }
})

// Private schools in Sofia for grades 8-12 (/search?school_type=private&age_group=grade_8_12),
// from the public API on 2026-10-08, rounded to 4 decimals. Most are within 7 km of the
// middle; five are 9 to 15 km out, west and south-east of the city.
const PRIVATE_GRADE_8_12 = [
  [42.6705, 23.3483], [42.6503, 23.3356], [42.7101, 23.187], [42.6433, 23.3361], [42.649, 23.3301],
  [42.6693, 23.3553], [42.6524, 23.3341], [42.6971, 23.321], [42.6738, 23.3131], [42.6458, 23.2721],
  [42.6491, 23.3367], [42.6988, 23.3258], [42.697, 23.3223], [42.7068, 23.1433], [42.6663, 23.2536],
  [42.6663, 23.2536], [42.6333, 23.3681], [42.6739, 23.2978], [42.6371, 23.3679], [42.6763, 23.2939],
  [42.6335, 23.3168], [42.6141, 23.396], [42.6955, 23.3279], [42.7011, 23.2833], [42.6814, 23.2899],
  [42.6665, 23.3231], [42.6639, 23.3942], [42.6726, 23.3162], [42.6785, 23.357], [42.614, 23.4609],
  [42.6153, 23.444], [42.6788, 23.3238], [42.6602, 23.2463], [42.6362, 23.3679], [42.6934, 23.3101],
  [42.6475, 23.2959], [42.6997, 23.3323], [42.6796, 23.3093], [42.6796, 23.3093], [42.7117, 23.2536],
  [42.6383, 23.3708], [42.7138, 23.2699], [42.6922, 23.2813], [42.6919, 23.3599], [42.6275, 23.3105],
  [42.6822, 23.368], [42.6817, 23.3136], [42.6474, 23.3564], [42.7218, 23.3052],
]

const span = (points, axis) => {
  const values = points.map(point => point[axis])
  return Math.max(...values) - Math.min(...values)
}

test('a mid-size result set is fitted to where most of its pins are', () => {
  // Fitting 95% of them kept three of the five outliers: zoom 10 on a 390px wide phone.
  const before = pointsForFit(PRIVATE_GRADE_8_12, { distance, keepRatio: 0.95 })
  const now = fitted(PRIVATE_GRADE_8_12)
  assert.equal(before.length, 47)
  assert.equal(now.length, 43)
  // The fit is not much more than half as wide (degrees of longitude), a zoom level closer.
  assert.ok(span(before, 1) > 0.25, `before: ${span(before, 1)}`)
  assert.ok(span(now, 1) < 0.15, `now: ${span(now, 1)}`)
  assert.ok(now.every(([, lng]) => lng > 23.24 && lng < 23.4))
})

test('pointsForFit with the city ratio keeps the central half', () => {
  const points = Array.from({ length: 600 }, (_, i) => [42.4 + i * 0.001, 23.3])
  const kept = pointsForFit(points, { distance, keepRatio: fitKeepRatio(points.length) })
  assert.equal(kept.length, 300)
  const lats = kept.map(([lat]) => lat)
  assert.ok(Math.min(...lats) > 42.54 && Math.max(...lats) < 42.86)
})

test('fitPadding leaves less room on a phone than on a large map', () => {
  assert.equal(fitPadding({ x: 390, y: 551 }), 31)
  assert.equal(fitPadding({ x: 544, y: 770 }), 44)
  assert.equal(fitPadding({ x: 1100, y: 770 }), 60)
  assert.equal(fitPadding({ x: 200, y: 200 }), 24)
  // A hidden map (mobile List tab) has no size yet.
  assert.equal(fitPadding({ x: 0, y: 0 }), 24)
})
