import test from 'node:test'
import assert from 'node:assert/strict'

import { LIST_PAGE_SIZE, windowForIndex } from './listWindow.js'

const firstPage = { start: 0, end: LIST_PAGE_SIZE }

test('windowForIndex keeps the window when the card is already rendered', () => {
  assert.equal(windowForIndex(0, firstPage), null)
  assert.equal(windowForIndex(29, firstPage), null)
})

test('windowForIndex ignores a school that is not in the list', () => {
  assert.equal(windowForIndex(-1, firstPage), null)
})

test('windowForIndex extends the window for a card just past its end', () => {
  assert.deepEqual(windowForIndex(35, firstPage), { start: 0, end: 55 })
})

test('windowForIndex renders a page around a far card, not every card up to it', () => {
  const next = windowForIndex(480, firstPage)
  assert.deepEqual(next, { start: 470, end: 500 })
  assert.ok(next.end - next.start <= LIST_PAGE_SIZE)
})

test('windowForIndex moves the window back for a card above it', () => {
  assert.deepEqual(windowForIndex(5, { start: 470, end: 500 }), { start: 0, end: 25 })
  assert.deepEqual(windowForIndex(465, { start: 470, end: 500 }), { start: 455, end: 485 })
})
