import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { childFreePlaces, childFreePlacesBySchool, schoolFreePlaces, usableFreePlaces } from './freePlaces.js'

const entry = (birthYear, free, extra = {}) => ({
  birth_year: birthYear, campaign: '2026/2027', stage: 'kindergarten', free, ranked_not_enrolled: 1, capacity: 26, enrolled: 25 - free, ...extra,
})

describe('free places', () => {
  const now = Date.parse('2026-10-23T18:00:00Z')

  it('drops a missing or old answer', () => {
    assert.equal(usableFreePlaces(null, now), null)
    assert.equal(usableFreePlaces({ schools: {}, retrieved_at: '2026-10-21T17:00:00+00:00' }, now), null)
    const fresh = { schools: { 1: [] }, retrieved_at: '2026-10-23T04:20:00+00:00' }
    assert.equal(usableFreePlaces(fresh, now), fresh)
  })

  it('reads one school by id', () => {
    assert.equal(schoolFreePlaces({ schools: { 12: [entry(2023, 2)] } }, 12).length, 1)
    assert.deepEqual(schoolFreePlaces({ schools: {} }, 12), [])
  })

  it('sums the child birth year across buildings in the right campaign only', () => {
    const entries = [entry(2023, 2), entry(2023, 1, { building: 'филиал' }), entry(2022, 5), entry(2023, 9, { campaign: '2027/2028' })]
    const now = childFreePlaces(entries, 2023, 2026)
    assert.equal(now.free, 3)
    assert.equal(now.rankedNotEnrolled, 2)
    assert.equal(now.buildings, 2)
    assert.equal(now.buildingsWithFree, 2)
    assert.equal(childFreePlaces([entry(2023, 0), entry(2023, 4, { building: 'филиал' })], 2023, 2026).buildingsWithFree, 1)
    assert.equal(childFreePlaces(entries, 2023, 2027).free, 9)
  })

  it('says nothing, rather than zero, when the school has no such group', () => {
    assert.equal(childFreePlaces([entry(2022, 5)], 2023, 2026), null)
    assert.equal(childFreePlaces([entry(2023, 0)], 2023, 2026).free, 0)
    assert.equal(childFreePlaces([entry(2023, 1)], null, 2026), null)
  })

  it('maps every school with the child group', () => {
    const map = childFreePlacesBySchool({ schools: { 1: [entry(2023, 2)], 2: [entry(2022, 1)] } }, 2023, 2026)
    assert.deepEqual([...map.keys()], [1])
    assert.equal(map.get(1).birthYear, 2023)
    assert.equal(map.get(1).free, 2)
    assert.equal(childFreePlacesBySchool(null, 2023, 2026).size, 0)
  })
})
