/**
 * Switching language is a full navigation to the other URL prefix (see LanguageToggle), so
 * whatever the URL does not hold is handed to the next document in one sessionStorage
 * record: the window scroll, and from the search page its map view and the school at the
 * top of the list (by id: the list is sorted by name, and names sort differently per
 * language, so a pixel offset would land on other schools).
 */

export const LANGUAGE_SWITCH_EVENT = 'app:languageswitch'

const HANDOFF_KEY = 'languageSwitch'
// Give up restoring the window scroll once the page has had this long to load.
const RESTORE_TIMEOUT_MS = 5000

function safeSession() {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

/**
 * Just before leaving for the other language. `routeUrl` is the route path plus search;
 * the open page adds its own state to `event.detail` (see SearchPage).
 */
export function prepareLanguageSwitch(routeUrl, storage = safeSession()) {
  const record = { url: routeUrl, scrollY: Math.round(window.scrollY) }
  window.dispatchEvent(new CustomEvent(LANGUAGE_SWITCH_EVENT, { detail: record }))
  try {
    storage?.setItem(HANDOFF_KEY, JSON.stringify(record))
  } catch {
    // Storage full or blocked: the new page opens at the top.
  }
}

/** The record a language switch left, removed from storage (null when none or corrupt). */
export function readLanguageSwitch(storage = safeSession()) {
  try {
    const record = JSON.parse(storage?.getItem(HANDOFF_KEY) || 'null')
    storage?.removeItem(HANDOFF_KEY)
    return record && typeof record.url === 'string' ? record : null
  } catch {
    return null
  }
}

// Read once per document, so every reader (and React's StrictMode re-runs) sees it.
let arrival

/**
 * The language switch that opened this document, for its first history entry only (React
 * Router keys that one 'default'; later visits to the same URL are ordinary navigations).
 */
export function languageSwitchArrival({ key, pathname, search }) {
  if (arrival === undefined) arrival = readLanguageSwitch()
  if (key !== 'default' || !arrival || arrival.url !== `${pathname}${search}`) return null
  return arrival
}

/**
 * Scroll the window to `y` once the page is tall enough (its content loads after mount).
 * Stops when the visitor scrolls first, after the timeout, or when the returned function
 * is called.
 */
export function restoreWindowScroll(y) {
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => tryScroll()) : null
  const userInput = ['scroll', 'wheel', 'touchstart', 'keydown']
  const timer = setTimeout(() => stop(), RESTORE_TIMEOUT_MS)

  function stop() {
    clearTimeout(timer)
    observer?.disconnect()
    userInput.forEach((type) => window.removeEventListener(type, stop))
  }

  function tryScroll() {
    if (document.documentElement.scrollHeight - window.innerHeight < y) return
    stop()
    window.scrollTo(0, y)
  }

  userInput.forEach((type) => window.addEventListener(type, stop, { passive: true }))
  observer?.observe(document.body)
  tryScroll()
  return stop
}
