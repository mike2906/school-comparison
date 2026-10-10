import { canonicalLanguagePair } from './languages.js'
import { humanizeTag } from './tags.js'
/**
 * Resolves the locale-specific view of a school's display attributes.
 *
 * The API serves `attributes` (locale-independent) and `attributes_i18n`
 * (`{bg: {...}, en: {...}}`). Merging, deduping and normalizing already happened
 * server-side in `app/utils/school_attributes.py` — the raw `extracted` LLM payload
 * never reaches the browser. All this does is pick the locale.
 */

function isObject(value) {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function getPreferredLocale(locale) {
  return String(locale || '').toLowerCase().startsWith('en') ? 'en' : 'bg'
}

export function normalizeSchoolAttributes(rawAttributes, attributesI18n, locale) {
  const attributes = isObject(rawAttributes) ? rawAttributes : {}
  const byLocale = isObject(attributesI18n) ? attributesI18n : {}
  const localized = byLocale[getPreferredLocale(locale)]

  return isObject(localized) ? { ...attributes, ...localized } : { ...attributes }
}

export function normalizeSchool(school, locale) {
  if (!isObject(school)) return school
  return {
    ...school,
    attributes: normalizeSchoolAttributes(school.attributes, school.attributes_i18n, locale),
  }
}

export function normalizeSchoolList(schools, locale) {
  if (!Array.isArray(schools)) return []
  return schools.map((school) => normalizeSchool(school, locale))
}

/**
 * Return the locale-independent canonical tags used by advanced filters and amenity
 * flags. The fallback keeps older/pre-projection payloads usable when their values were
 * already canonical; current API responses always use `attributes.filter_tags`.
 */
export function getFilterTags(attributes, group) {
  const canonical = attributes?.filter_tags?.[group]
  if (Array.isArray(canonical)) return canonical

  const legacy = attributes?.[group]
  return Array.isArray(legacy) ? legacy : []
}

/**
 * Shared canonical amenity signals for cards, detail, and comparison views.
 */
export function getCanonicalAmenityFlags(attributes, hasAfterSchool = false) {
  const facilityTags = getFilterTags(attributes, 'facilities')
  const programTags = getFilterTags(attributes, 'special_programs')

  return {
    meals: Boolean(
      attributes?.has_canteen ||
      facilityTags.includes('cafeteria') ||
      programTags.includes('meals_provided')
    ),
    transport: facilityTags.includes('transportation'),
    extended: Boolean(
      hasAfterSchool ||
      programTags.includes('extended_day')
    ),
    library: facilityTags.includes('library'),
    computerLab: facilityTags.includes('computer_lab'),
    sportsFacilities: facilityTags.includes('sports_facilities'),
  }
}

/**
 * Return tri-state evidence for claims that the comparison UI previously rendered as
 * Yes/No. `null` means the payload has no evidence either way; a missing positive tag
 * is not evidence that a service is unavailable.
 */
export function getCanonicalAmenityEvidence(attributes, afterSchoolEvidence = null) {
  const flags = getCanonicalAmenityFlags(attributes, afterSchoolEvidence === true)
  const facilities = Array.isArray(attributes?.facilities) ? attributes.facilities : []

  return {
    meals: flags.meals ? true : attributes?.has_canteen === false ? false : null,
    transport: flags.transport ? true : null,
    extended: flags.extended ? true : afterSchoolEvidence === false ? false : null,
    accessibility: facilities.includes('accessible') ? true : null,
  }
}

export function getAfterSchoolEvidence(locations) {
  if (!Array.isArray(locations) || locations.length === 0) return null
  let sawFalse = false
  let sawUnknown = false

  for (const location of locations) {
    const shifts = Array.isArray(location?.age_group_shifts)
      ? location.age_group_shifts
      : []
    if (shifts.length === 0) {
      sawUnknown = true
      continue
    }
    for (const shift of shifts) {
      const value = shift?.has_organised_groups
      if (value === true) return true
      if (value === false) sawFalse = true
      else sawUnknown = true
    }
  }

  return sawFalse && !sawUnknown ? false : null
}

/**
 * Return only location fields that the comparison row can actually display. Keeping this
 * separate from the address prevents an address-less location with useful enrollment,
 * shift, or distance evidence from making the whole locations section look empty.
 */
export function getLocationDisplayEvidence(location, address = null, distance = null) {
  const ageGroups = (Array.isArray(location?.age_groups)
    ? location.age_groups
    : [location?.age_group]
  ).filter(Boolean)
  const shifts = (Array.isArray(location?.age_group_shifts)
    ? location.age_group_shifts
    : []
  ).map(item => item?.shift).filter(Boolean)

  return {
    address: typeof address === 'string' && address.trim() ? address.trim() : null,
    ageGroups,
    shifts,
    distance: Number.isFinite(distance) ? distance : null,
  }
}

export function getAdmissionStatusKey(rawStatus) {
  const status = String(rawStatus || '').trim().toLowerCase()
  // Waitlist and negations first, so "waitlist open" and "no places available" are not "accepting".
  if (status.includes('wait')) return 'waitlist'
  if (status.includes('full') || status.includes('closed') || /\b(not|no)\b/.test(status)) return 'full'
  if (status.includes('accept') || status.includes('open') || status.includes('available')) {
    return 'accepting'
  }
  return null
}

export function hasDisplayEvidence(value) {
  if (value == null) return false
  if (Array.isArray(value)) return value.some(hasDisplayEvidence)
  if (typeof value === 'string') return value.trim().length > 0
  if (typeof value === 'object') return Object.values(value).some(hasDisplayEvidence)
  return true
}

// Canonical pairs ("english:intensive"), so "English", "английски" and "английски език"
// are one filter option and scraped non-languages never become one.
function addLanguageFocusPairs(values, pairs) {
  if (!Array.isArray(values)) return

  values.forEach((value) => {
    let raw = null
    if (isObject(value)) {
      const language = value.language
      const level = value.level
      if (language && level) raw = `${language}:${level}`
      else if (language) raw = language
    } else if (typeof value === 'string') {
      raw = value
    }
    const pair = raw ? canonicalLanguagePair(raw) : null
    if (pair) pairs.add(pair)
  })
}

// The filter counts ask for a school's pairs once per filter option, so keep the answer
// per school object (the list is replaced, never mutated, when it reloads).
const languageFocusPairsBySchool = new WeakMap()

export function getLanguageFocusPairs(school) {
  const cacheable = school !== null && typeof school === 'object'
  const cached = cacheable ? languageFocusPairsBySchool.get(school) : undefined
  if (cached) return cached

  const pairs = new Set()
  addLanguageFocusPairs(school?.attributes?.language_focus, pairs)

  const byLocale = isObject(school?.attributes_i18n) ? school.attributes_i18n : {}
  Object.values(byLocale).forEach((attributes) => {
    if (isObject(attributes)) {
      addLanguageFocusPairs(attributes.language_focus, pairs)
    }
  })

  if (cacheable) languageFocusPairsBySchool.set(school, pairs)
  return pairs
}

const STATUS_COLORS = {
  accepting: '#10b981',
  waitlist: '#f59e0b',
  full: '#ef4444',
}

/** Admission status for display: `{key, color (hex), label}`, or null when unknown. */
export function getStatusInfo(school, t) {
  const key = getAdmissionStatusKey(school?.admission_info?.status)
  if (!key || !STATUS_COLORS[key]) return null
  return { key, color: STATUS_COLORS[key], label: t(`schoolCard.status.${key}`) }
}

/** Label for a filter option or scraped tag: the translation if there is one, else tidied text. */
export function getOptionLabel(option, t) {
  if (!option) return ''
  const key = `advancedFilters.options.${option}`
  // nsSeparator off: scraped text with a colon ("Clubs: English") is not a namespace.
  const translated = t(key, { nsSeparator: false })
  if (translated !== key) return translated
  // Free text scraped from a website: tidy it, but never Title-Case a sentence.
  return humanizeTag(option)
}

/** `language_focus` as `[{language, level}]`; accepts strings like "english:intensive" or objects. */
export function normalizeLanguageFocus(languageFocus = []) {
  const items = Array.isArray(languageFocus) ? languageFocus : [languageFocus]
  const normalized = []
  items.forEach((item) => {
    if (!item) return
    if (typeof item === 'string') {
      const [language, level] = item.split(':')
      normalized.push({ language, level })
      return
    }
    if (typeof item === 'object') {
      normalized.push({ language: item.language, level: item.level })
    }
  })
  return normalized.filter(item => item.language)
}
