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
  if (locale) {
    return String(locale).toLowerCase().startsWith('en') ? 'en' : 'bg'
  }
  if (typeof localStorage === 'undefined') return 'bg'
  const raw = localStorage.getItem('language') || 'bg'
  return raw.toLowerCase().startsWith('en') ? 'en' : 'bg'
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

function addLanguageFocusPairs(values, pairs) {
  if (!Array.isArray(values)) return

  values.forEach((value) => {
    if (isObject(value)) {
      const language = value.language
      const level = value.level
      if (language && level) {
        pairs.add(`${language}:${level}`)
      } else if (language) {
        pairs.add(language)
      }
    } else if (typeof value === 'string') {
      pairs.add(value)
    }
  })
}

export function getLanguageFocusPairs(school) {
  const pairs = new Set()
  addLanguageFocusPairs(school?.attributes?.language_focus, pairs)

  const byLocale = isObject(school?.attributes_i18n) ? school.attributes_i18n : {}
  Object.values(byLocale).forEach((attributes) => {
    if (isObject(attributes)) {
      addLanguageFocusPairs(attributes.language_focus, pairs)
    }
  })

  return pairs
}
