import test from 'node:test'
import assert from 'node:assert/strict'

import { LIST_PAGE_SIZE, windowForIndex, scrollTopToCenter } from './listWindow.js'

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

test('scrollTopToCenter centres a card that is further down the list', () => {
  // List at y=200, 600 tall, scrolled 1000; the card is 3400px below the list top.
  const top = scrollTopToCenter({ scrollTop: 1000, listTop: 200, listHeight: 600, cardTop: 3600, cardHeight: 200 })
  assert.equal(top, 4200)
})

test('scrollTopToCenter scrolls back up to a card above the visible part', () => {
  const top = scrollTopToCenter({ scrollTop: 1000, listTop: 200, listHeight: 600, cardTop: -300, cardHeight: 200 })
  assert.equal(top, 300)
})

test('scrollTopToCenter never scrolls above the top and shows a tall card from its top', () => {
  assert.equal(scrollTopToCenter({ scrollTop: 0, listTop: 200, listHeight: 600, cardTop: 250, cardHeight: 200 }), 0)
  assert.equal(scrollTopToCenter({ scrollTop: 500, listTop: 200, listHeight: 600, cardTop: 900, cardHeight: 900 }), 1200)
})
