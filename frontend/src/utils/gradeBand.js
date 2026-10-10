/**
 * Reading the grades a school's fee text names ("ПК", "VIII - XII клас", "1-7 клас",
 * "Grade 5") so the UI can show our own translated label instead of the website's
 * wording. Preschool (ПК / ПГ / PK, the year before 1st grade) is grade 0.
 */

const ROMAN = { i: 1, ii: 2, iii: 3, iv: 4, v: 5, vi: 6, vii: 7, viii: 8, ix: 9, x: 10, xi: 11, xii: 12 }
const PRESCHOOL = 'пк|пг|pk|подготвителна група|подготвителен клас|pre-?school'
const ITEM = `(${PRESCHOOL}|[ivx]{1,4}|\\d{1,2})\\.?`
const GRADE_WORD = '(?:клас(?:ове)?|кл\\.?|grades?)'
const WHOLE_BAND_RE = new RegExp(
  `^(?:(?:grades?|от)\\s+)?${ITEM}\\s*${GRADE_WORD}?(?:\\s*(?:-|до|to)\\s*${ITEM})?\\s*${GRADE_WORD}?\\.?$`,
  'u',
)
const PRESCHOOL_RE = new RegExp(`^(?:${PRESCHOOL})$`, 'u')
// A range inside free text ("PK–7: full payment"), not part of a date, time or longer number.
const RANGE_IN_TEXT_RE = /(?<![\p{L}\p{N}.:])(пк|пг|pk|[ivx]{1,4}|\d{1,2})\s*-\s*([ivx]{1,4}|\d{1,2})(?![\p{L}\p{N}]|\.\d)/gu
// Bare numbers ("3-6") are grades only when grade words frame them, or when they open the
// plan name as its label ("8–12: full payment"); a kindergarten's "3-6 години" is ages, and
// "Вноски 1-2:" counts instalments.
const GRADE_WORD_RE = new RegExp(GRADE_WORD, 'u')
const GRADES_BEFORE_RE = /(?:grades?|класове|клас)\s*$/u
const GRADES_AFTER_RE = /^\.?\s*(?:клас|кл(?!\p{L})|grades?(?!\p{L}))/u
const LABEL_COLON_RE = /^\.?\s*:/u

function normalize(text) {
  return String(text || '')
    .toLocaleLowerCase('bg')
    .replace(/[–—]/g, '-')
    .replace(/х/g, 'x') // Cyrillic "Х" is often typed for the Roman ten
    .replace(/\s+/g, ' ')
    .trim()
}

function gradeOf(token) {
  if (!token) return null
  if (PRESCHOOL_RE.test(token)) return 0
  if (/^\d+$/.test(token)) {
    const value = Number(token)
    return value <= 12 ? value : null
  }
  return ROMAN[token] ?? null
}

function band(fromToken, toToken) {
  const from = gradeOf(fromToken)
  const to = toToken ? gradeOf(toToken) : from
  if (from == null || to == null || from > to) return null
  return { from, to }
}

/** The grades a whole age-group text names, `{ from, to }`, or null if it says anything else. */
export function parseGradeBand(text) {
  const normalized = normalize(text)
  const match = normalized.match(WHOLE_BAND_RE)
  if (!match) return null
  // A bare "3-6" or "5" is as likely ages as grades: it needs a grade word, a Roman
  // numeral or a preschool name.
  const bare = [match[1], match[2]].every(token => !token || /^\d+$/.test(token))
  if (bare && !GRADE_WORD_RE.test(normalized)) return null
  return band(match[1], match[2])
}

/** Every grade range written inside free text (a plan name), widest span, or null. */
export function gradeRangeInText(text) {
  let found = null
  const normalized = normalize(text)
  for (const match of normalized.matchAll(RANGE_IN_TEXT_RE)) {
    const before = normalized.slice(0, match.index)
    const after = normalized.slice(match.index + match[0].length)
    const framed = PRESCHOOL_RE.test(match[1])
      || GRADES_BEFORE_RE.test(before)
      || GRADES_AFTER_RE.test(after)
      || (match.index === 0 && LABEL_COLON_RE.test(after))
    if (!framed) continue
    const range = band(match[1], match[2])
    if (range && range.from < range.to) {
      found = found
        ? { from: Math.min(found.from, range.from), to: Math.max(found.to, range.to) }
        : range
    }
  }
  return found
}

/**
 * The grades a price row is for: its age group, widened by an overlapping grade range its
 * plan name writes out (171's "PK–7: full payment" filed under "ПК" covers PK to grade 7).
 * Null when the age group names no grades.
 */
export function rowGradeBand(price) {
  const named = gradeRangeInText(price?.plan_name)
  const band = parseGradeBand(price?.age_group)
  if (!band) return null
  if (!named || named.from > band.to + 1 || named.to < band.from - 1) return band
  return { from: Math.min(band.from, named.from), to: Math.max(band.to, named.to) }
}

/** A grade band in the UI language: "Preschool", "Grade 5", "Grades 8–12", "Preschool to grade 7". */
export function gradeBandLabel({ from, to }, t) {
  if (from === 0 && to === 0) return t('pricing.grades.preschool')
  if (from === 0) return t('pricing.grades.preschoolTo', { to })
  if (from === to) return t('pricing.grades.one', { n: from })
  return t('pricing.grades.range', { from, to })
}
