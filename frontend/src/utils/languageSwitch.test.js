import test from 'node:test'
import assert from 'node:assert/strict'

import { LANGUAGE_SWITCH_EVENT, prepareLanguageSwitch, readLanguageSwitch, restoreWindowScroll } from './languageSwitch.js'

function memoryStorage() {
  const items = new Map()
  return {
    getItem: (key) => (items.has(key) ? items.get(key) : null),
    setItem: (key, value) => items.set(key, String(value)),
    removeItem: (key) => items.delete(key),
  }
}

// A minimal window/document: listeners, a scroll position and a page height.
function fakePage({ scrollY = 0, pageHeight = 1000, innerHeight = 500 } = {}) {
  const listeners = new Map()
  const page = {
    scrolledTo: [],
    window: {
      scrollY,
      innerHeight,
      dispatchEvent: (event) => (listeners.get(event.type) || []).forEach((listener) => listener(event)),
      addEventListener: (type, listener) => listeners.set(type, [...(listeners.get(type) || []), listener]),
      removeEventListener: (type, listener) => listeners.set(type, (listeners.get(type) || []).filter((l) => l !== listener)),
      scrollTo: (_x, y) => page.scrolledTo.push(y),
    },
    document: { documentElement: { scrollHeight: pageHeight }, body: {} },
    listenerCount: () => [...listeners.values()].reduce((sum, list) => sum + list.length, 0),
  }
  return page
}

function withPage(page, run) {
  globalThis.window = page.window
  globalThis.document = page.document
  try {
    return run()
  } finally {
    delete globalThis.window
    delete globalThis.document
  }
}

test('a language switch lets the page add its state and hands the record on, once', () => {
  const storage = memoryStorage()
  const page = fakePage({ scrollY: 812.4 })
  withPage(page, () => {
    page.window.addEventListener(LANGUAGE_SWITCH_EVENT, (event) => { event.detail.listSchoolId = 7 })
    prepareLanguageSwitch('/search?school_type=state', storage)
  })
  const { at, ...record } = JSON.parse(storage.getItem('languageSwitch'))
  assert.deepEqual(record, { url: '/search?school_type=state', scrollY: 812, listSchoolId: 7 })
  assert.equal(readLanguageSwitch(storage, at + 1000)?.listSchoolId, 7)
  assert.equal(readLanguageSwitch(storage, at + 1000), null)
})

test('a record from a switch that never arrived is ignored', () => {
  const storage = memoryStorage()
  storage.setItem('languageSwitch', JSON.stringify({ url: '/about', scrollY: 400, at: 1000 }))
  assert.equal(readLanguageSwitch(storage, 1000 + 60000), null)
})

test('missing or corrupt storage means no record', () => {
  assert.equal(readLanguageSwitch(null), null)
  const storage = memoryStorage()
  storage.setItem('languageSwitch', '{not json')
  assert.equal(readLanguageSwitch(storage), null)
})

test('the window scroll is restored at once when the page is already tall enough', () => {
  const page = fakePage({ pageHeight: 3000 })
  withPage(page, () => restoreWindowScroll(700))
  assert.deepEqual(page.scrolledTo, [700])
  assert.equal(page.listenerCount(), 0)
})

test('a visitor who scrolls first keeps their own position', () => {
  const page = fakePage({ pageHeight: 800 })
  withPage(page, () => {
    restoreWindowScroll(700)
    page.window.dispatchEvent({ type: 'scroll' })
    page.document.documentElement.scrollHeight = 3000
  })
  assert.deepEqual(page.scrolledTo, [])
  assert.equal(page.listenerCount(), 0)
})

test('cancelling a pending restore removes its listeners', () => {
  const page = fakePage({ pageHeight: 800 })
  withPage(page, () => restoreWindowScroll(700)())
  assert.deepEqual(page.scrolledTo, [])
  assert.equal(page.listenerCount(), 0)
})
