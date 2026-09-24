/**
 * Compare-list persistence helpers.
 *
 * The compare list keeps only what the compare bar needs to draw its pills. The compare page
 * always fetches fresh school data by id, so storing whole school objects only kept stale
 * copies (and a lot of JSON) in localStorage.
 */

export const MAX_COMPARE = 4
export const COMPARE_STORAGE_KEY = 'compareList'

function isValidId(id) {
  return Number.isInteger(id) && id > 0
}

/** The minimal entry stored for one school: enough to render its name and type. */
export function toCompareEntry(school) {
  if (!school || typeof school !== 'object' || !isValidId(school.id)) return null
  const entry = { id: school.id }
  if (school.name_i18n && typeof school.name_i18n === 'object') entry.name_i18n = school.name_i18n
  if (school.resolved_name_i18n && typeof school.resolved_name_i18n === 'object') {
    entry.resolved_name_i18n = school.resolved_name_i18n
  }
  if (typeof school.school_type === 'string') entry.school_type = school.school_type
  return entry
}

/** Trim any stored value (old full-object arrays included) to unique, valid entries. */
export function normalizeCompareList(value) {
  if (!Array.isArray(value)) return []
  const seen = new Set()
  const result = []
  for (const item of value) {
    const entry = toCompareEntry(item)
    if (!entry || seen.has(entry.id)) continue
    seen.add(entry.id)
    result.push(entry)
    if (result.length >= MAX_COMPARE) break
  }
  return result
}

/** Read the stored list; any storage or JSON error yields an empty list. */
export function loadCompareList(storage) {
  try {
    const raw = storage?.getItem(COMPARE_STORAGE_KEY)
    return raw ? normalizeCompareList(JSON.parse(raw)) : []
  } catch {
    return []
  }
}

export function saveCompareList(storage, list) {
  try {
    storage?.setItem(COMPARE_STORAGE_KEY, JSON.stringify(list))
  } catch {
    // Storage can be unavailable or full (private mode); the in-memory list still works.
  }
}

/**
 * Reconcile stored entries with a fresh compare response: refresh names and types of the
 * returned schools and drop requested ids the API no longer returns. Returns the same array
 * when nothing changed, so callers can hand it to setState without triggering a re-render.
 */
export function syncCompareList(list, requestedIds, freshSchools) {
  const requested = new Set(requestedIds)
  const freshById = new Map(
    (freshSchools || []).map(toCompareEntry).filter(Boolean).map(entry => [entry.id, entry])
  )
  const next = []
  for (const entry of list) {
    if (freshById.has(entry.id)) {
      next.push(freshById.get(entry.id))
    } else if (!requested.has(entry.id)) {
      next.push(entry)
    }
  }
  return JSON.stringify(next) === JSON.stringify(list) ? list : next
}

/** Storage accessors themselves can throw (e.g. blocked site data), so read them guarded. */
export function safeLocalStorage() {
  try {
    return window.localStorage
  } catch {
    return null
  }
}
