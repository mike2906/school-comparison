import test from 'node:test'
import assert from 'node:assert/strict'

import { foldSamePlace } from './samePlace.js'

const kindergarten = { id: 1, same_place: [{ id: 2, location_id: 11, other_location_id: 21 }] }
const school = { id: 2, same_place: [{ id: 1, location_id: 21, other_location_id: 11 }] }
const other = { id: 3 }

test('foldSamePlace shows a place on the card of whichever comes first', () => {
  const { cards, hostById } = foldSamePlace([other, school, kindergarten])
  assert.deepEqual(cards.map(card => card.school.id), [3, 2])
  assert.deepEqual(cards[1].samePlace, [{ school: kindergarten, inList: true }])
  assert.equal(hostById.get(1), 2)
})

test('foldSamePlace names a neighbour the filters hide without folding anything', () => {
  const { cards, hostById } = foldSamePlace([kindergarten])
  assert.deepEqual(cards, [{ school: kindergarten, samePlace: [{ school: kindergarten.same_place[0], inList: false }] }])
  assert.equal(hostById.size, 0)
})

test('foldSamePlace lists each neighbour once when it shares several pins', () => {
  const twoPins = {
    id: 1,
    same_place: [
      { id: 2, location_id: 11, other_location_id: 21 },
      { id: 2, location_id: 12, other_location_id: 21 },
    ],
  }
  const { cards } = foldSamePlace([twoPins, school])
  assert.deepEqual(cards.map(card => [card.school.id, card.samePlace.length]), [[1, 1]])
})
