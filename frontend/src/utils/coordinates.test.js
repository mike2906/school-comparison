import assert from 'node:assert/strict'
import test from 'node:test'

import { parseCoordinate, pinnedLocation } from './coordinates.js'

test('parseCoordinate reads numbers and comma-decimal strings', () => {
  assert.equal(parseCoordinate(42.69), 42.69)
  assert.equal(parseCoordinate('42,69'), 42.69)
  assert.equal(parseCoordinate(' 23.32 '), 23.32)
  assert.equal(parseCoordinate(null), null)
  assert.equal(parseCoordinate(undefined), null)
  assert.equal(parseCoordinate('n/a'), null)
  assert.equal(parseCoordinate(Number.NaN), null)
})

test('pinnedLocation prefers a pinned primary, then any pinned location', () => {
  const primaryUnpinned = { id: 1, is_primary: true, lat: null, lng: null }
  const branch = { id: 2, is_primary: false, lat: 42.7, lng: 23.3 }
  const pinnedPrimary = { id: 3, is_primary: true, lat: '42,69', lng: 23.32 }
  assert.equal(pinnedLocation([primaryUnpinned, branch]), branch)
  assert.equal(pinnedLocation([branch, pinnedPrimary]), pinnedPrimary)
  assert.equal(pinnedLocation([primaryUnpinned]), null)
  assert.equal(pinnedLocation(undefined), null)
})
