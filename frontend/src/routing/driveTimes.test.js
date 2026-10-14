import { test } from 'node:test'
import assert from 'node:assert/strict'
import { rushHourMinutes } from './driveTimes.js'

test('rush-hour minutes scale free-flow time and never show zero', () => {
  assert.equal(rushHourMinutes(600), 17) // 10 min free flow x 1.73
  assert.equal(rushHourMinutes(5), 1)
})

test('unknown or unreachable times have no minutes', () => {
  assert.equal(rushHourMinutes(Infinity), null)
  assert.equal(rushHourMinutes(undefined), null)
})
