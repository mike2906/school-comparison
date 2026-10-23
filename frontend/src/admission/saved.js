/**
 * Calculator answers a parent chose to save on this device (docs/ADMISSION_POINTS.md,
 * decision 7). They stay in this browser's localStorage: nothing here sends them
 * anywhere. One entry per calculator tab, plus the home address checked against the
 * official address table, which the catchment maps reuse.
 *
 * Shape: { version, child: { birthYear, startYear? } | null, home: { streetId, number } | null,
 *          homePoint: { lat, lng, label } | null,
 *          kindergarten: { startYear, answers, siblings?, savedOn } | null,
 *          grade_1: { startYear, addresses, answers, siblings?, savedOn } | null }
 * `siblings` maps a school id to that school's sibling answers (see siblingsWith).
 * `child` is the one child the pages plan for, shared by every page that needs the birth
 * year (read it with childBirthYear), so a parent gives it once. `homePoint` is the home on
 * the map, for distances and travel times; `home` is the same home in the official address
 * table, for catchments. Either can exist without the other.
 */

const STORAGE_KEY = 'admissionAnswers'
// The search page's current location (useUserLocation): not part of the saved answers.
export const LOCATION_KEY = 'userLocation'
const VERSION = 1
export const SAVED_STAGES = ['kindergarten', 'grade_1']

const EMPTY = Object.freeze({ version: VERSION, child: null, home: null, homePoint: null, kindergarten: null, grade_1: null })

function storage() {
  try {
    return typeof window === 'undefined' ? null : window.localStorage
  } catch {
    return null
  }
}

/** Parses stored text; anything unreadable or from another version counts as nothing saved. */
export function parseSaved(text) {
  try {
    const value = JSON.parse(text || 'null')
    if (!value || value.version !== VERSION) return EMPTY
    return {
      version: VERSION,
      child: isChild(value.child) ? childFrom(value.child) : legacyChild(value.kindergarten),
      home: isHome(value.home) ? value.home : null,
      homePoint: isPoint(value.homePoint) ? value.homePoint : null,
      kindergarten: isEntry(value.kindergarten) ? value.kindergarten : null,
      grade_1: isEntry(value.grade_1) && Array.isArray(value.grade_1.addresses) ? value.grade_1 : null,
    }
  } catch {
    return EMPTY
  }
}

function isChild(child) {
  return Boolean(child && isBirthYear(child.birthYear))
}

function childFrom({ birthYear, startYear }) {
  return Number.isInteger(startYear) ? { birthYear, startYear } : { birthYear }
}

function isPoint(point) {
  return Boolean(point && Number.isFinite(point.lat) && Number.isFinite(point.lng))
}

function isBirthYear(year) {
  return Number.isInteger(year) && year > 1900
}

// Answers saved before `child` existed: the kindergarten answers held the birth year.
function legacyChild(kindergarten) {
  const birthYear = kindergarten?.answers?.birthYear
  return isBirthYear(birthYear) ? { birthYear } : null
}

function isHome(home) {
  return Boolean(home && typeof home.streetId === 'string' && typeof home.number === 'string')
}

function isEntry(entry) {
  return Boolean(entry && Number.isInteger(entry.startYear) && entry.answers && typeof entry.answers === 'object')
}

let current = null
const listeners = new Set()

// One listener for changes made in another tab, however many components subscribe.
function onStorage(event) {
  // A null key is localStorage.clear() in another tab.
  if (event.key !== STORAGE_KEY && event.key !== null) return
  current = null
  listeners.forEach(listener => listener())
}

function read() {
  if (current == null) current = parseSaved(storage()?.getItem(STORAGE_KEY))
  return current
}

/** Whether the parent has saved anything on this device (their opt-in to keeping more). */
export function hasSaved(saved) {
  return Boolean(saved.child || saved.home || saved.homePoint || SAVED_STAGES.some(stage => saved[stage]))
}

function write(next) {
  current = next
  const empty = !hasSaved(next)
  try {
    if (empty) storage()?.removeItem(STORAGE_KEY)
    else storage()?.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    // Full or blocked storage: the answers live for this page view only.
  }
  listeners.forEach(listener => listener())
}

/** For useSyncExternalStore. */
export function subscribeSaved(listener) {
  if (listeners.size === 0 && typeof window !== 'undefined') window.addEventListener('storage', onStorage)
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0 && typeof window !== 'undefined') window.removeEventListener('storage', onStorage)
  }
}

export function getSaved() {
  return read()
}

export function saveStage(stage, entry, { home } = {}) {
  const next = { ...read(), [stage]: { ...entry, savedOn: new Date().toISOString().slice(0, 10) } }
  if (home !== undefined) next.home = home
  const birthYear = entry.answers?.birthYear
  if (isBirthYear(birthYear)) next.child = { ...next.child, birthYear }
  write(next)
}

/**
 * The child's birth year as the parent gave it, or null. Never guessed from first-grade
 * answers: a child may start first grade at six or eight.
 */
export function childBirthYear(saved = read()) {
  return saved.child?.birthYear ?? null
}

/** The September the parent plans for, when they chose one, or null. */
export function childStartYear(saved = read()) {
  return saved.child?.startYear ?? null
}

/**
 * Saves the child's birth year, and the start year when given (a new birth year without
 * one drops the old start year). Saved calculator answers keep their own: a family may
 * have saved kindergarten answers for a younger child and first-grade ones for an older one.
 */
export function saveChild({ birthYear, startYear }) {
  if (!isBirthYear(birthYear)) return
  write({ ...read(), child: childFrom({ birthYear, startYear }) })
}

/** Saves the home's map point ({ lat, lng, label }), or forgets it with null. */
export function saveHomePoint(point) {
  if (point !== null && !isPoint(point)) return
  write({ ...read(), homePoint: point && { lat: point.lat, lng: point.lng, label: point.label || null } })
}

/**
 * The schools where a sibling attends, from answers given on that school's own calculator
 * page: the list counts a sibling only there. `entry` is the saved entry; its siblings
 * stand for its own start year only (a sibling's age is checked against it). With a school
 * on the page, `place` is its sibling answers for a yes and null otherwise (forgets it).
 */
export function siblingsWith(entry, startYear, schoolId, place) {
  const next = { ...(entry?.startYear === startYear ? entry.siblings : null) }
  if (schoolId == null) return next
  if (place) next[schoolId] = place
  else delete next[schoolId]
  return next
}

/** `entry` with its siblings, left out when there are none. */
export function withSiblings(entry, siblings) {
  return Object.keys(siblings).length > 0 ? { ...entry, siblings } : entry
}

export function saveHome(home) {
  write({ ...read(), home })
}

/**
 * Forgets one tab's answers, or everything (the child too) without a stage. First-grade
 * answers hold the home address too, so forgetting them forgets it. Forgetting the last
 * saved tab forgets the child as well, so no "forget" leaves a birth year behind.
 */
export function forgetSaved(stage = null) {
  if (!stage) {
    // The search page's last location too: "forget" leaves no place behind.
    try {
      storage()?.removeItem(LOCATION_KEY)
    } catch {
      // Blocked storage holds nothing to forget.
    }
    return write(EMPTY)
  }
  const next = { ...read(), [stage]: null, ...(stage === 'grade_1' ? { home: null } : {}) }
  if (SAVED_STAGES.every(other => !next[other])) next.child = null
  write(next)
}

/**
 * Where trips start: the saved home on the map, else the search page's last location, else
 * null. `{ lat, lng, label, isHome }`.
 */
export function tripOrigin(saved = read()) {
  if (saved.homePoint) return { ...saved.homePoint, isHome: true }
  try {
    const parsed = JSON.parse(storage()?.getItem(LOCATION_KEY) || 'null')
    if (isPoint(parsed)) return { lat: parsed.lat, lng: parsed.lng, label: parsed.address || null, isHome: false }
  } catch {
    // Unreadable or blocked: no location.
  }
  return null
}

/** Tests only: drop the in-memory copy. */
export function resetSavedForTests() {
  current = null
}
