import test from 'node:test'
import assert from 'node:assert/strict'

import {
  readViewParams,
  writeViewParams,
  readSavedViewState,
  saveViewState,
  getLastSearchUrl,
  rememberLastSearchUrl,
  shortAddress,
  closedDetailSchoolId,
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
  assert.deepEqual(view, { sort: 'price', within: '5', view: 'map-only', tab: 'map', school: 42, q: '' })

  const bad = readViewParams(new URLSearchParams('sort=evil&within=3&view=x&tab=y&school=abc'))
  assert.deepEqual(bad, { sort: 'name', within: 'any', view: 'list-map', tab: 'list', school: null, q: '' })
})

test('writeViewParams omits defaults, keeps other params, and reports no-ops', () => {
  const base = new URLSearchParams('age_group=first&sort=price')
  const next = writeViewParams(base, { sort: 'name', within: 'any', view: 'list-map', tab: 'list', school: 7 })
  assert.equal(next.toString(), 'age_group=first&school=7')
  assert.equal(writeViewParams(next, readViewParams(next)), null)
  const entry = writeViewParams(new URLSearchParams('selected_school_id=5'), readViewParams(new URLSearchParams('')))
  assert.equal(entry.toString(), '')
})

test('saved view state is only restored for the same history entry', () => {
  const storage = memoryStorage()
  saveViewState('abc', { search: '?age_group=first', scrollTop: 1500, map: { center: [42.7, 23.3], zoom: 13 } }, storage)
  assert.deepEqual(readSavedViewState('abc', '?age_group=first', storage), { scrollTop: 1500, listStart: 0, map: { center: [42.7, 23.3], zoom: 13 } })
  assert.equal(readSavedViewState('other', '?age_group=first', storage), null)
  // Every page load's first entry is keyed 'default': a different URL must not restore.
  assert.equal(readSavedViewState('abc', '?age_group=grade_8_12', storage), null)
})

test('readSavedViewState tolerates corrupt storage and a missing map view', () => {
  const storage = memoryStorage()
  storage.setItem('searchViewState', '{not json')
  assert.equal(readSavedViewState('abc', '', storage), null)
  saveViewState('abc', { search: '', scrollTop: 10, map: null }, storage)
  assert.deepEqual(readSavedViewState('abc', '', storage), { scrollTop: 10, listStart: 0, map: null })
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

test('view state is kept per history entry, capped to recent entries', () => {
  const storage = memoryStorage()
  saveViewState('first', { search: '?a=1', scrollTop: 100, map: null }, storage)
  saveViewState('second', { search: '?a=2', scrollTop: 200, map: null }, storage)
  assert.equal(readSavedViewState('first', '?a=1', storage).scrollTop, 100)
  assert.equal(readSavedViewState('second', '?a=2', storage).scrollTop, 200)
  saveViewState('first', { search: '?a=1', scrollTop: 150, map: null }, storage)
  assert.equal(readSavedViewState('first', '?a=1', storage).scrollTop, 150)
  for (let i = 0; i < 25; i += 1) saveViewState(`k${i}`, { search: '', scrollTop: i, map: null }, storage)
  assert.equal(readSavedViewState('first', '?a=1', storage), null)
  assert.equal(readSavedViewState('k24', '', storage).scrollTop, 24)
})

test('getLastSearchUrl rejects look-alike and off-site paths', () => {
  const storage = memoryStorage()
  for (const bad of ['//evil.example/search', '/searching', 'https://evil.example/search']) {
    rememberLastSearchUrl(bad, storage)
    assert.equal(getLastSearchUrl(storage), '/search')
  }
  rememberLastSearchUrl('/search', storage)
  assert.equal(getLastSearchUrl(storage), '/search')
  const throwing = { getItem: () => { throw new Error('SecurityError') } }
  assert.equal(getLastSearchUrl(throwing), '/search')
})

test('the name query round-trips through the URL', () => {
  const next = writeViewParams(new URLSearchParams('age_group=first'), { ...readViewParams(new URLSearchParams('')), q: 'пенчо' })
  assert.equal(readViewParams(next).q, 'пенчо')
  const cleared = writeViewParams(next, { ...readViewParams(next), q: '' })
  assert.equal(cleared.has('q'), false)
})

test('shortAddress puts a leading house number after the street', () => {
  assert.equal(shortAddress('1, бул. Витоша, Център, София'), 'бул. Витоша 1')
  assert.equal(shortAddress('ул. Раковски 12, София'), 'ул. Раковски 12')
  assert.equal(shortAddress('Current location'), 'Current location')
  assert.equal(shortAddress(''), '')
})

test('the first rendered card is saved with the scroll position', () => {
  const storage = memoryStorage()
  saveViewState('abc', { search: '', scrollTop: 900, listStart: 470, map: null }, storage)
  assert.equal(readSavedViewState('abc', '', storage).listStart, 470)
})

test('closedDetailSchoolId names the school whose panel was closed', () => {
  assert.equal(closedDetailSchoolId('?school=396&detail=384', '?school=396'), 384)
  assert.equal(closedDetailSchoolId('?age_group=first&detail=384', '?age_group=first'), 384)
  assert.equal(closedDetailSchoolId('?detail=384&view=map-only', '?view=map-only&school=12'), 384)
})

test('closedDetailSchoolId ignores other URL changes', () => {
  // Back from a shared detail link to a different search.
  assert.equal(closedDetailSchoolId('?detail=384', '?age_group=first&school=12'), null)
  // The panel switched school, or was never open.
  assert.equal(closedDetailSchoolId('?detail=384', '?detail=396'), null)
  assert.equal(closedDetailSchoolId('?school=396', '?school=12'), null)
  assert.equal(closedDetailSchoolId('?detail=abc', ''), null)
})
