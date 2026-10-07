import test from 'node:test'
import assert from 'node:assert/strict'

import { pointsForFit, pickSelectedMarker, pinHasRoom, pinTarget, overlayFitPadding, stackedMarkersByKey } from './mapFit.js'

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
