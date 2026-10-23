/**
 * Calculator answers a parent chose to save on this device (docs/ADMISSION_POINTS.md,
 * decision 7). They stay in this browser's localStorage: nothing here sends them
 * anywhere. One entry per calculator tab, plus the home address checked against the
 * official address table, which the catchment maps reuse.
 *
 * Shape: { version, child: { birthYear, startYear? } | null, home: { streetId, number } | null,
 *          homePoint: { lat, lng, label, fromHome? } | null,
 *          preferences: { languages: [key], monthlyBudget: number | null } | null,
 *          kindergarten: { startYear, answers, siblings?, savedOn } | null,
 *          grade_1: { startYear, addresses, answers, siblings?, savedOn } | null }
 * `siblings` maps a school id to that school's sibling answers (see siblingsWith).
 * `child` is the one child the pages plan for, shared by every page that needs the birth
 * year (read it with childBirthYear), so a parent gives it once. `homePoint` is the home on
 * the map, for distances and travel times; `home` is the same home in the official address
 * table, for catchments. Either can exist without the other; a point found from `home` names
 * it in `fromHome` and goes when that address changes or is forgotten.
 */

const STORAGE_KEY = 'admissionAnswers'
// The search page's current location (useUserLocation): not part of the saved answers.
export const LOCATION_KEY = 'userLocation'
const VERSION = 1
export const SAVED_STAGES = ['kindergarten', 'grade_1']

const EMPTY = Object.freeze({ version: VERSION, child: null, home: null, homePoint: null, preferences: null, kindergarten: null, grade_1: null })

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
      preferences: preferencesFrom(value.preferences),
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

// The options page's choices: languages (src/utils/languages.js keys) and a top tuition
// per month in euro. Anything else is dropped.
function preferencesFrom(value) {
  if (!value || typeof value !== 'object') return null
  const languages = Array.isArray(value.languages) ? value.languages.filter(key => typeof key === 'string') : []
  const monthlyBudget = Number.isFinite(value.monthlyBudget) && value.monthlyBudget > 0 ? value.monthlyBudget : null
  return languages.length > 0 || monthlyBudget ? { languages, monthlyBudget } : null
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
  return Boolean(saved.child || saved.home || saved.homePoint || saved.preferences || SAVED_STAGES.some(stage => saved[stage]))
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
  if (home !== undefined) Object.assign(next, withoutStalePoint(next, { ...next, home }))
  const birthYear = entry.answers?.birthYear
  // Another birth year is another plan: its start year is not this one's.
  if (isBirthYear(birthYear) && next.child?.birthYear !== birthYear) next.child = { birthYear }
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
  const saved = read()
  const keep = startYear === undefined && saved.child?.birthYear === birthYear ? saved.child.startYear : startYear
  write({ ...saved, child: childFrom({ birthYear, startYear: keep }) })
}

/** Saves the options page's languages and budget (see preferencesFrom), or forgets them. */
export function savePreferences(preferences) {
  write({ ...read(), preferences: preferencesFrom(preferences) })
}

/** `home` as one string, to tie a point found from it to it. */
export const homeKey = home => (home ? `${home.streetId}|${home.number}` : null)

/**
 * Saves the home's map point ({ lat, lng, label, fromHome? }), or forgets it with null.
 * `fromHome` is homeKey(home) when the point was looked up from the saved address.
 */
export function saveHomePoint(point) {
  if (point !== null && !isPoint(point)) return
  const homePoint = point && {
    lat: point.lat,
    lng: point.lng,
    label: point.label || null,
    ...(point.fromHome ? { fromHome: point.fromHome } : {}),
  }
  write({ ...read(), homePoint })
}

// A new home address replaces the home point, wherever it came from (the newest home
// wins); forgetting the address takes only a point that was looked up from it.
function withoutStalePoint(before, next) {
  const point = next.homePoint
  if (!point) return next
  const key = homeKey(next.home)
  const stale = key
    ? key !== homeKey(before.home) && point.fromHome !== key
    : Boolean(point.fromHome)
  return stale ? { ...next, homePoint: null } : next
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
  const before = read()
  write(withoutStalePoint(before, { ...before, home }))
}

/**
 * Forgets one tab's answers, or everything (the child too) without a stage. First-grade
 * answers hold the home address too, so forgetting them forgets it. Forgetting the last
 * saved tab forgets the child as well, so no "forget" leaves a birth year behind.
 */
export function forgetSaved(stage = null) {
  if (!stage) {
    // The search page's last location too: "forget" leaves no place behind.
    writeLocation(null)
    return write(EMPTY)
  }
  const before = read()
  const next = withoutStalePoint(before, { ...before, [stage]: null, ...(stage === 'grade_1' ? { home: null } : {}) })
  if (SAVED_STAGES.every(other => !next[other])) next.child = null
  write(next)
}

/**
 * The search page's current location ({ lat, lng, address, ... } as it saved it), or null.
 * Not part of the saved answers: the list keeps it for the next visit by itself.
 */
export function readLocation() {
  try {
    const parsed = JSON.parse(storage()?.getItem(LOCATION_KEY) || 'null')
    return isPoint(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** Keeps (or with null, removes) the search page's current location. */
export function writeLocation(location) {
  try {
    if (location) storage()?.setItem(LOCATION_KEY, JSON.stringify(location))
    else storage()?.removeItem(LOCATION_KEY)
  } catch {
    // Full or blocked storage: the location lives for this page view only.
  }
}

/**
 * Where trips start, as `{ lat, lng, label, isHome }`: the location the parent last set on
 * the search page (it may be somewhere other than home on purpose), else the saved home,
 * else null.
 */
export function tripOrigin(saved = read()) {
  const location = readLocation()
  if (location) return { lat: location.lat, lng: location.lng, label: location.address || null, isHome: samePoint(location, saved.homePoint) }
  return saved.homePoint ? { ...saved.homePoint, isHome: true } : null
}

export const samePoint = (a, b) => Boolean(a && b && a.lat === b.lat && a.lng === b.lng)

/** Tests only: drop the in-memory copy. */
export function resetSavedForTests() {
  current = null
}
