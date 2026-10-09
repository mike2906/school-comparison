/**
 * Switching language is a full navigation to the other URL prefix (see LanguageToggle), so
 * whatever the URL does not hold is handed to the next document in one sessionStorage
 * record: the window scroll, and from the search page its map view and the school at the
 * top of the list (by id: the list is sorted by name, and names sort differently per
 * language, so a pixel offset would land on other schools).
 */

export const LANGUAGE_SWITCH_EVENT = 'app:languageswitch'

const HANDOFF_KEY = 'languageSwitch'
// A record older than this belongs to a switch that never arrived (cancelled, offline).
const HANDOFF_MAX_AGE_MS = 30000
// How long the window scroll waits for the page to grow tall enough.
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
  const record = { url: routeUrl, scrollY: Math.round(window.scrollY), at: Date.now() }
  window.dispatchEvent(new CustomEvent(LANGUAGE_SWITCH_EVENT, { detail: record }))
  try {
    storage?.setItem(HANDOFF_KEY, JSON.stringify(record))
  } catch {
    // Storage full or blocked: the new page opens at the top.
  }
}

/** The record a language switch left, removed from storage (null when none, stale or corrupt). */
export function readLanguageSwitch(storage = safeSession(), now = Date.now()) {
  try {
    const record = JSON.parse(storage?.getItem(HANDOFF_KEY) || 'null')
    storage?.removeItem(HANDOFF_KEY)
    const fresh = Number.isFinite(record?.at) && now - record.at >= 0 && now - record.at < HANDOFF_MAX_AGE_MS
    return fresh && typeof record.url === 'string' ? record : null
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
 * Scroll the window to `y` once the page is tall enough (its content loads after mount);
 * at the timeout, as far as the page allows (the other language's page can be shorter).
 * Stops when the visitor scrolls first, or when the returned function is called.
 */
export function restoreWindowScroll(y) {
  const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(() => tryScroll()) : null
  const userInput = ['scroll', 'wheel', 'touchstart', 'keydown']
  const timer = setTimeout(() => {
    stop()
    window.scrollTo(0, Math.min(y, document.documentElement.scrollHeight - window.innerHeight))
  }, RESTORE_TIMEOUT_MS)

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
