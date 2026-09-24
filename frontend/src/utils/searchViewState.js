/**
 * Search-page view state that must survive opening a school and coming back:
 * sort / distance / list-map view / selected school live in the URL, while the list
 * scroll position and map centre/zoom are kept per history entry in sessionStorage.
 */

export const SORT_VALUES = ['name', 'type', 'price', 'distance', 'nvo']
export const DISTANCE_VALUES = ['any', '2', '5']
export const VIEW_MODES = ['list-map', 'map-only', 'list-only']
export const MOBILE_TABS = ['list', 'map']

export const VIEW_DEFAULTS = {
  sort: 'name',
  within: 'any',
  view: 'list-map',
  tab: 'list',
  q: '',
}

const MAX_QUERY_LENGTH = 100

const oneOf = (value, allowed, fallback) => (allowed.includes(value) ? value : fallback)

/** Read the view params from URLSearchParams, falling back to defaults for bad values. */
export function readViewParams(searchParams) {
  const selected = Number.parseInt(searchParams.get('school') || '', 10)
  return {
    sort: oneOf(searchParams.get('sort'), SORT_VALUES, VIEW_DEFAULTS.sort),
    within: oneOf(searchParams.get('within'), DISTANCE_VALUES, VIEW_DEFAULTS.within),
    view: oneOf(searchParams.get('view'), VIEW_MODES, VIEW_DEFAULTS.view),
    tab: oneOf(searchParams.get('tab'), MOBILE_TABS, VIEW_DEFAULTS.tab),
    school: Number.isFinite(selected) && selected > 0 ? selected : null,
    q: (searchParams.get('q') || '').slice(0, MAX_QUERY_LENGTH),
  }
}

/**
 * Return new URLSearchParams with the view params written (defaults omitted), or null
 * when nothing changed, so callers can skip a no-op navigation.
 */
export function writeViewParams(searchParams, view) {
  const next = new URLSearchParams(searchParams)
  const entries = {
    sort: view.sort === VIEW_DEFAULTS.sort ? null : view.sort,
    within: view.within === VIEW_DEFAULTS.within ? null : view.within,
    view: view.view === VIEW_DEFAULTS.view ? null : view.view,
    tab: view.tab === VIEW_DEFAULTS.tab ? null : view.tab,
    school: view.school ? String(view.school) : null,
    q: view.q ? String(view.q).slice(0, MAX_QUERY_LENGTH) : null,
    // One-shot entry param, consumed on load; never kept in the URL.
    selected_school_id: null,
  }
  Object.entries(entries).forEach(([key, value]) => {
    if (value == null) next.delete(key)
    else next.set(key, value)
  })
  return next.toString() === new URLSearchParams(searchParams).toString() ? null : next
}

const SCROLL_STATE_KEY = 'searchViewState'
const MAX_SAVED_ENTRIES = 20

function readAll(storage) {
  try {
    const saved = JSON.parse(storage.getItem(SCROLL_STATE_KEY) || '[]')
    return Array.isArray(saved) ? saved : []
  } catch {
    // Corrupt value: start over rather than blocking future saves.
    return []
  }
}
export const LAST_SEARCH_URL_KEY = 'lastSearchUrl'

function safeSession() {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

/**
 * Saved scroll/map state, only if it belongs to this history entry. The key alone is not
 * enough: the first entry of every page load is keyed 'default'.
 */
export function readSavedViewState(locationKey, search, storage = safeSession()) {
  if (!storage || !locationKey) return null
  try {
    const saved = readAll(storage).find(entry => entry?.key === locationKey && entry.search === search)
    if (!saved) return null
    return {
      scrollTop: Number.isFinite(saved.scrollTop) ? saved.scrollTop : 0,
      map: saved.map && Array.isArray(saved.map.center) && Number.isFinite(saved.map.zoom) ? saved.map : null,
    }
  } catch {
    return null
  }
}

export function saveViewState(locationKey, { search, scrollTop, map }, storage = safeSession()) {
  if (!storage || !locationKey) return
  try {
    // One record per history entry (newest last), so going back several searches still restores each.
    const others = readAll(storage).filter(entry => !(entry?.key === locationKey && entry.search === search))
    const next = [...others, { key: locationKey, search, scrollTop, map }].slice(-MAX_SAVED_ENTRIES)
    storage.setItem(SCROLL_STATE_KEY, JSON.stringify(next))
  } catch {
    // Storage full or blocked: losing the scroll position is acceptable.
  }
}

export function rememberLastSearchUrl(url, storage = safeSession()) {
  if (!storage) return
  try {
    storage.setItem(LAST_SEARCH_URL_KEY, url)
  } catch {
    // ignore
  }
}

// An in-app /search path only: not '/searching', not '//other.host/search'.
const SEARCH_PATH_RE = /^\/search(?:[/?#]|$)/

/** The last search URL, or '/search' when missing or not an in-app search path. */
export function getLastSearchUrl(storage = safeSession()) {
  try {
    const url = storage?.getItem(LAST_SEARCH_URL_KEY)
    return typeof url === 'string' && SEARCH_PATH_RE.test(url) ? url : '/search'
  } catch {
    return '/search'
  }
}

/** True when the search page shows the detail as a side panel (Tailwind `lg`). */
export function isDesktopViewport() {
  return typeof window !== 'undefined' && window.matchMedia?.('(min-width: 1024px)').matches === true
}

/** A plain left click, i.e. not one the browser should turn into "open in new tab". */
export function isPlainLeftClick(event) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey
}
