/**
 * Utility functions for internationalization with JSONB i18n fields
 */

/**
 * Get a value from a JSONB i18n field (e.g. name_i18n, address_i18n, summary_i18n)
 * Falls back to first available value if requested language is not present.
 *
 * @param {object} i18nField - JSONB object e.g. {"bg": "...", "en": "..."}
 * @param {string} language - Current language code
 * @returns {string} The value for the requested language, or fallback
 */
export function getI18nValue(i18nField, language) {
  if (!i18nField || typeof i18nField !== 'object') return ''
  return i18nField[language] || Object.values(i18nField)[0] || ''
}

function getResolvedNameI18n(school) {
  const resolvedName = school?.resolved_name_i18n
  return resolvedName && typeof resolvedName === 'object' ? resolvedName : null
}

function getResolvedAddressI18n(location) {
  const resolvedAddress = location?.resolved_address_i18n
  return resolvedAddress && typeof resolvedAddress === 'object' ? resolvedAddress : null
}

/**
 * Returns the localized school name from name_i18n
 *
 * @param {object} school - School object with name_i18n property
 * @param {string} language - Current language code
 * @returns {string} Localized school name
 */
export function getSchoolName(school, language) {
  // `resolved_name_i18n` already folds in the branded display name server-side.
  return (
    getI18nValue(getResolvedNameI18n(school), language) ||
    getI18nValue(school?.name_i18n, language)
  )
}

/**
 * Returns the localized address from address_i18n
 *
 * @param {object} location - Location object with address_i18n property
 * @param {string} language - Current language code
 * @returns {string} Localized address
 */
export function getAddress(location, language) {
  return getI18nValue(getResolvedAddressI18n(location), language) || getI18nValue(location?.address_i18n, language)
}

/**
 * Returns the localized summary from summary_i18n.
 * Supports both legacy string values and the new { short, long } shape.
 *
 * @param {object} school - School object with summary_i18n property
 * @param {string} language - Current language code
 * @param {'short'|'long'} variant - Preferred summary length
 * @returns {string} Localized summary
 */
export function getSummary(school, language, variant = 'long') {
  const summary = school?.summary_i18n
  if (!summary || typeof summary !== 'object') return ''

  const localized = summary[language] || Object.values(summary)[0]
  if (!localized) return ''

  if (typeof localized === 'string') return localized

  const preferred = localized?.[variant] || localized?.long || localized?.short
  return typeof preferred === 'string' ? preferred : ''
}

// Leading quotes and punctuation are data noise ("''Yagodina") and must not decide order.
const LEADING_NOISE_RE = /^[\s"'“”„«»‘’`.,-]+/

/** Sort key for a school name: leading noise removed so "''Yagodina" sorts under Y. */
export function schoolNameSortKey(name) {
  return String(name || '').replace(LEADING_NOISE_RE, '')
}

/** Compare two display names so numbered schools sort 1, 2, 10, 101 rather than 1, 10, 101, 2. */
export function compareSchoolNames(nameA, nameB, language) {
  return schoolNameSortKey(nameA).localeCompare(schoolNameSortKey(nameB), language, {
    sensitivity: 'base',
    numeric: true,
  })
}

function normalizeForSearch(text) {
  return String(text || '')
    .toLocaleLowerCase()
    .replace(/[\s"'“”„«»‘’`.,()№-]+/g, ' ')
    .trim()
}

/**
 * True when every word of `query` appears in one of the school's names, in any language
 * ("119", "пенчо", "slaveykov"). An empty query matches everything.
 */
export function schoolMatchesQuery(school, query) {
  const tokens = normalizeForSearch(query).split(' ').filter(Boolean)
  if (tokens.length === 0) return true
  const names = [
    ...Object.values(school?.resolved_name_i18n || {}),
    ...Object.values(school?.name_i18n || {}),
  ].map(normalizeForSearch)
  return names.some(name => tokens.every(token => name.includes(token)))
}
