import assert from 'node:assert/strict'
import test from 'node:test'

import { parseCoordinate } from './coordinates.js'

test('parseCoordinate reads numbers and comma-decimal strings', () => {
  assert.equal(parseCoordinate(42.69), 42.69)
  assert.equal(parseCoordinate('42,69'), 42.69)
  assert.equal(parseCoordinate(' 23.32 '), 23.32)
  assert.equal(parseCoordinate(null), null)
  assert.equal(parseCoordinate(undefined), null)
  assert.equal(parseCoordinate('n/a'), null)
  assert.equal(parseCoordinate(Number.NaN), null)
})
