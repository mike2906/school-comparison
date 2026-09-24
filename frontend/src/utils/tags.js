/**
 * Display helpers for scraped facility / programme tags.
 *
 * Tags are either canonical snake_case keys ("science_lab") or free text copied from a
 * school website ("Cambridge English", "обновена материална база"). Free text is never
 * machine-translated; it is only tidied for display.
 */

const CYRILLIC = /[Ѐ-ӿ]/g
const LATIN = /[A-Za-z]/g

// A capitalised function word is the tell-tale of Title Case. Capitalised content words
// alone are not: "Cambridge English" or "International Baccalaureate" are proper names.
const TITLE_CASE_FUNCTION_WORDS = new Set([
  'And', 'Or', 'Of', 'The', 'For', 'With', 'In', 'On', 'At', 'To', 'By', 'From', 'A', 'An',
])

/** True for a canonical tag key such as "science_lab". */
export function isTagKey(value) {
  return /^[a-z0-9]+(?:_[a-z0-9]+)*$/.test(String(value || ''))
}

/**
 * Turn Title Case English into sentence case: "Excursions And Extracurricular Activities"
 * becomes "Excursions and extracurricular activities". Only words written Capitalised
 * (not ALL CAPS such as IB or STEM) after the first word are lowercased, and only when the
 * label is evidently Title Case, so proper names like "Cambridge English" are kept.
 */
export function toSentenceCase(label) {
  const text = String(label ?? '')
  const words = text.split(/\s+/).filter(Boolean)
  const isTitleCase = words.slice(1).some(word => TITLE_CASE_FUNCTION_WORDS.has(word))
  if (!isTitleCase) return text

  let seenFirstWord = false
  return text.replace(/\S+/g, (word) => {
    if (!seenFirstWord) {
      seenFirstWord = true
      return word
    }
    return /^[A-Z][a-z]+$/.test(word) ? word.toLowerCase() : word
  })
}

/** A readable label for a tag that has no translation key. */
export function humanizeTag(value) {
  const text = String(value ?? '').trim()
  if (!text) return ''
  const readable = isTagKey(text) ? text.replace(/_/g, ' ') : toSentenceCase(text)
  return readable.charAt(0).toUpperCase() + readable.slice(1)
}

/** True when a tag is written mostly in Cyrillic (i.e. it is Bulgarian text). */
export function isCyrillicTag(value) {
  const text = String(value ?? '')
  const cyrillic = (text.match(CYRILLIC) || []).length
  const latin = (text.match(LATIN) || []).length
  return cyrillic > 0 && cyrillic >= latin
}

/**
 * Split tags for display. In a non-Bulgarian UI, Bulgarian-only tags are returned
 * separately so they can be grouped under an "In Bulgarian" label instead of mixed in.
 */
export function splitTagsByLanguage(tags = [], language = 'bg') {
  const list = Array.isArray(tags) ? tags.filter(tag => typeof tag === 'string' && tag.trim()) : []
  if (String(language || '').startsWith('bg')) {
    return { main: list, bulgarian: [] }
  }
  return {
    main: list.filter(tag => !isCyrillicTag(tag)),
    bulgarian: list.filter(isCyrillicTag),
  }
}
