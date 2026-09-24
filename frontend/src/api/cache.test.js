import test from 'node:test'
import assert from 'node:assert/strict'

import { cachedJson, peekCachedJson, clearJsonCache } from './cache.js'

const okResponse = (data) => ({ ok: true, json: async () => data })

test('cachedJson shares one request and exposes the result to peek', async () => {
  clearJsonCache()
  let calls = 0
  const fetchImpl = async () => { calls += 1; return okResponse({ n: 1 }) }

  assert.equal(peekCachedJson('/a'), undefined)
  const [a, b] = await Promise.all([
    cachedJson('/a', 'x', { fetchImpl }),
    cachedJson('/a', 'x', { fetchImpl }),
  ])
  assert.equal(calls, 1)
  assert.deepEqual(a, { n: 1 })
  assert.equal(a, b)
  assert.deepEqual(peekCachedJson('/a'), { n: 1 })
})

test('cachedJson does not cache failures', async () => {
  clearJsonCache()
  let calls = 0
  const failing = async () => { calls += 1; return { ok: false, statusText: 'Bad' } }
  await assert.rejects(cachedJson('/b', 'Failed', { fetchImpl: failing }), /Failed: Bad/)
  await assert.rejects(cachedJson('/b', 'Failed', { fetchImpl: failing }))
  assert.equal(calls, 2)
  assert.equal(peekCachedJson('/b'), undefined)
})

test('cachedJson refetches after the TTL', async () => {
  clearJsonCache()
  let calls = 0
  let clock = 0
  const now = () => clock
  const fetchImpl = async () => { calls += 1; return okResponse({ calls }) }
  await cachedJson('/c', 'x', { fetchImpl, now })
  clock = 11 * 60 * 1000
  assert.equal(peekCachedJson('/c', { now }), undefined)
  await cachedJson('/c', 'x', { fetchImpl, now })
  assert.equal(calls, 2)
})
