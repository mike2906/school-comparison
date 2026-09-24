import test from 'node:test'
import assert from 'node:assert/strict'

import {
  readViewParams,
  writeViewParams,
  readSavedViewState,
  saveViewState,
  getLastSearchUrl,
  rememberLastSearchUrl,
} from './searchViewState.js'

const memoryStorage = () => {
  const data = new Map()
  return {
    getItem: key => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
  }
}

test('readViewParams validates values and falls back to defaults', () => {
  const view = readViewParams(new URLSearchParams('sort=price&within=5&view=map-only&tab=map&school=42'))
  assert.deepEqual(view, { sort: 'price', within: '5', view: 'map-only', tab: 'map', school: 42 })

  const bad = readViewParams(new URLSearchParams('sort=evil&within=3&view=x&tab=y&school=abc'))
  assert.deepEqual(bad, { sort: 'name', within: 'any', view: 'list-map', tab: 'list', school: null })
})

test('writeViewParams omits defaults, keeps other params, and reports no-ops', () => {
  const base = new URLSearchParams('age_group=first&sort=price')
  const next = writeViewParams(base, { sort: 'name', within: 'any', view: 'list-map', tab: 'list', school: 7 })
  assert.equal(next.toString(), 'age_group=first&school=7')
  assert.equal(writeViewParams(next, readViewParams(next)), null)
})

test('saved view state is only restored for the same history entry', () => {
  const storage = memoryStorage()
  saveViewState('abc', { scrollTop: 1500, map: { center: [42.7, 23.3], zoom: 13 } }, storage)
  assert.deepEqual(readSavedViewState('abc', storage), { scrollTop: 1500, map: { center: [42.7, 23.3], zoom: 13 } })
  assert.equal(readSavedViewState('other', storage), null)
})

test('readSavedViewState tolerates corrupt storage and a missing map view', () => {
  const storage = memoryStorage()
  storage.setItem('searchViewState', '{not json')
  assert.equal(readSavedViewState('abc', storage), null)
  saveViewState('abc', { scrollTop: 10, map: null }, storage)
  assert.deepEqual(readSavedViewState('abc', storage), { scrollTop: 10, map: null })
})

test('getLastSearchUrl only returns search URLs', () => {
  const storage = memoryStorage()
  assert.equal(getLastSearchUrl(storage), '/search')
  rememberLastSearchUrl('/search?age_group=first', storage)
  assert.equal(getLastSearchUrl(storage), '/search?age_group=first')
  rememberLastSearchUrl('https://evil.example', storage)
  assert.equal(getLastSearchUrl(storage), '/search')
  assert.equal(getLastSearchUrl(null), '/search')
})
