import test from 'node:test'
import assert from 'node:assert/strict'
import { inBounds, isQuarterMatch, pickInBounds, quarterQuery } from './geocoding.js'

const SOFIA = [[42.5, 23.1], [42.86, 23.6]]

test('quarterQuery tries the housing estate for a bare name and number', () => {
  assert.equal(quarterQuery('Люлин 5'), 'ж.к. Люлин 5')
  assert.equal(quarterQuery('Овча купел 2'), 'ж.к. Овча купел 2')
  assert.equal(quarterQuery('Младост 1А'), 'ж.к. Младост 1А')
  // Already typed, a street, a full address or no number: left to the plain search.
  assert.equal(quarterQuery('ж.к. Люлин 5'), null)
  assert.equal(quarterQuery('жк Люлин 5'), null)
  assert.equal(quarterQuery('ул. Шипка 6'), null)
  assert.equal(quarterQuery('бул. Витоша 15'), null)
  assert.equal(quarterQuery('Шипка 6, София'), null)
  assert.equal(quarterQuery('Младост'), null)
  assert.equal(quarterQuery('Иван Щерев 125'), null)
  assert.equal(quarterQuery('Lyulin 5'), null)
})

test('isQuarterMatch needs a quarter of exactly the typed name', () => {
  const quarter = { name: 'ж.к. Люлин 5', addresstype: 'quarter' }
  assert.equal(isQuarterMatch(quarter, 'Люлин 5'), true)
  assert.equal(isQuarterMatch({ name: 'ж.к. Младост 1А', addresstype: 'suburb' }, 'Младост 1 А'), true)
  // "ж.к. Дружба 2" comes back as the whole of Дружба; a house is not a quarter.
  assert.equal(isQuarterMatch({ name: 'ж.к. Дружба', addresstype: 'suburb' }, 'Дружба 2'), false)
  assert.equal(isQuarterMatch({ name: 'ж.к. Люлин 5', addresstype: 'railway' }, 'Люлин 5'), false)
  assert.equal(isQuarterMatch(undefined, 'Люлин 5'), false)
})

test('pickInBounds skips matches outside the city', () => {
  const botevgrad = { lat: '42.9026', lon: '23.7947' }
  const sofia = { lat: '42.7208', lon: '23.5291' }
  assert.equal(pickInBounds([botevgrad, sofia], SOFIA), sofia)
  assert.equal(pickInBounds([botevgrad], SOFIA), null)
  assert.equal(inBounds(42.9026, 23.7947, undefined), true)
})
