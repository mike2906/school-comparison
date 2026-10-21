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

test('cachedJson asks again while the API restarts', async () => {
  clearJsonCache()
  const answers = [
    () => { throw new TypeError('Failed to fetch') },
    () => ({ ok: false, status: 502, statusText: 'Bad Gateway' }),
    () => okResponse({ id: 1 }),
  ]
  const waits = []
  const fetchImpl = async () => answers.shift()()
  const data = await cachedJson('/d', 'x', { fetchImpl, sleep: async ms => { waits.push(ms) } })
  assert.deepEqual(data, { id: 1 })
  assert.deepEqual(waits, [1000, 2000])
})

test('cachedJson gives up after three retries and keeps the status', async () => {
  clearJsonCache()
  let calls = 0
  const fetchImpl = async () => { calls += 1; return { ok: false, status: 503, statusText: 'Unavailable' } }
  await assert.rejects(
    cachedJson('/e', 'Failed', { fetchImpl, sleep: async () => {} }),
    err => err.status === 503
  )
  assert.equal(calls, 4)
})

test('cachedJson does not retry a 404', async () => {
  clearJsonCache()
  let calls = 0
  const fetchImpl = async () => { calls += 1; return { ok: false, status: 404, statusText: 'Not Found' } }
  await assert.rejects(cachedJson('/f', 'Failed', { fetchImpl, sleep: async () => {} }), err => err.status === 404)
  assert.equal(calls, 1)
})
