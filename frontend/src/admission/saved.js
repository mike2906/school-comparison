/**
 * Calculator answers a parent chose to save on this device (docs/ADMISSION_POINTS.md,
 * decision 7). They stay in this browser's localStorage: nothing here sends them
 * anywhere. One entry per calculator tab, plus the home address checked against the
 * official address table, which the catchment maps reuse.
 *
 * Shape: { version, child: { birthYear } | null, home: { streetId, number } | null,
 *          kindergarten: { startYear, answers, siblings?, savedOn } | null,
 *          grade_1: { startYear, addresses, answers, siblings?, savedOn } | null }
 * `siblings` maps a school id to that school's sibling answers (see siblingsWith).
 * `child` is the one child the answers are for, shared by every page that needs the birth
 * year (read it with childBirthYear), so a parent gives it once.
 */

const STORAGE_KEY = 'admissionAnswers'
const VERSION = 1
export const SAVED_STAGES = ['kindergarten', 'grade_1']

const EMPTY = Object.freeze({ version: VERSION, child: null, home: null, kindergarten: null, grade_1: null })

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
      child: isChild(value.child) ? { birthYear: value.child.birthYear } : legacyChild(value.kindergarten),
      home: isHome(value.home) ? value.home : null,
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

function write(next) {
  current = next
  const empty = !next.child && !next.home && SAVED_STAGES.every(stage => !next[stage])
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

/**
 * Saves the child's birth year. Saved calculator answers keep their own: a family may have
 * saved kindergarten answers for a younger child and first-grade ones for an older one.
 */
export function saveChild({ birthYear }) {
  if (!isBirthYear(birthYear)) return
  write({ ...read(), child: { birthYear } })
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
  if (!stage) return write(EMPTY)
  const next = { ...read(), [stage]: null, ...(stage === 'grade_1' ? { home: null } : {}) }
  if (SAVED_STAGES.every(other => !next[other])) next.child = null
  write(next)
}

/** Tests only: drop the in-memory copy. */
export function resetSavedForTests() {
  current = null
}
