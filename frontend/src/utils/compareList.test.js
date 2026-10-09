import assert from 'node:assert/strict'
import test from 'node:test'

import {
  compareHref,
  loadCompareList,
  normalizeCompareList,
  saveCompareList,
  syncCompareList,
  toCompareEntry,
} from './compareList.js'

function memoryStorage(initial = {}) {
  const data = { ...initial }
  return {
    getItem: key => (key in data ? data[key] : null),
    setItem: (key, value) => { data[key] = String(value) },
    data,
  }
}

const throwingStorage = {
  getItem: () => { throw new Error('SecurityError') },
  setItem: () => { throw new Error('QuotaExceededError') },
}

test('toCompareEntry keeps only the pill fields', () => {
  const entry = toCompareEntry({
    id: 7,
    name_i18n: { bg: 'А', en: 'A' },
    resolved_name_i18n: { en: 'Brand' },
    school_type: 'private',
    locations: [{ lat: 1 }],
    pricing: [{ amount: 1 }],
  })
  assert.deepEqual(entry, {
    id: 7,
    name_i18n: { bg: 'А', en: 'A' },
    resolved_name_i18n: { en: 'Brand' },
    school_type: 'private',
  })
})

test('toCompareEntry rejects entries without a positive integer id', () => {
  assert.equal(toCompareEntry(null), null)
  assert.equal(toCompareEntry({ id: '5' }), null)
  assert.equal(toCompareEntry({ id: 0 }), null)
  assert.equal(toCompareEntry(42), null)
})

test('normalizeCompareList trims old full objects, dedupes and caps at 4', () => {
  const old = [1, 2, 2, 3, 4, 5].map(id => ({ id, name_i18n: { en: `S${id}` }, locations: [] }))
  const list = normalizeCompareList([null, 'x', ...old])
  assert.deepEqual(list.map(item => item.id), [1, 2, 3, 4])
  assert.ok(list.every(item => !('locations' in item)))
  assert.deepEqual(normalizeCompareList({ id: 1 }), [])
})

test('loadCompareList survives corrupted JSON and throwing storage', () => {
  assert.deepEqual(loadCompareList(memoryStorage({ compareList: '{not json' })), [])
  assert.deepEqual(loadCompareList(memoryStorage({ compareList: '"str"' })), [])
  assert.deepEqual(loadCompareList(throwingStorage), [])
  assert.deepEqual(loadCompareList(null), [])
  assert.deepEqual(
    loadCompareList(memoryStorage({ compareList: JSON.stringify([{ id: 3, school_type: 'state', attributes: {} }]) })),
    [{ id: 3, school_type: 'state' }]
  )
})

test('saveCompareList swallows storage errors', () => {
  assert.doesNotThrow(() => saveCompareList(throwingStorage, [{ id: 1 }]))
  const storage = memoryStorage()
  saveCompareList(storage, [{ id: 1 }])
  assert.equal(storage.data.compareList, '[{"id":1}]')
})

test('syncCompareList refreshes returned schools and drops missing requested ids', () => {
  const list = [
    { id: 1, name_i18n: { en: 'Old' } },
    { id: 2, name_i18n: { en: 'Gone' } },
    { id: 9, name_i18n: { en: 'Not requested' } },
  ]
  const next = syncCompareList(list, [1, 2], [{ id: 1, name_i18n: { en: 'New' }, school_type: 'state' }])
  assert.deepEqual(next, [
    { id: 1, name_i18n: { en: 'New' }, school_type: 'state' },
    { id: 9, name_i18n: { en: 'Not requested' } },
  ])
})

test('syncCompareList returns the same array when nothing changed', () => {
  const list = [{ id: 1, name_i18n: { en: 'A' }, school_type: 'state' }]
  const fresh = [{ id: 1, name_i18n: { en: 'A' }, school_type: 'state', locations: [] }]
  assert.equal(syncCompareList(list, [1], fresh), list)
})

test('compareHref carries the searched age group, and only that', () => {
  assert.equal(compareHref('?age_group=grade_8_12&sort=nvo'), '/compare?age_group=grade_8_12')
  assert.equal(compareHref('?sort=name'), '/compare')
  assert.equal(compareHref(''), '/compare')
})
