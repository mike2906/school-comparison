/**
 * Text checks for scripts/ui-scan.js, kept free of the browser so they can be unit tested.
 * Each check takes the visible strings of one rendered page and returns findings
 * `{ check, text }`. Layout checks need the browser and live in the scan itself.
 */

function leaves(node, out = []) {
  for (const value of Object.values(node)) {
    if (value && typeof value === 'object') leaves(value, out)
    else out.push(String(value))
  }
  return out
}

/**
 * What the checks need from the translation files: the top-level namespaces (to spot raw
 * keys) and, per language, the UI strings only the other language uses (to spot a
 * Bulgarian label on an English page and the reverse).
 */
export function buildDictionaryIndex(dictionaries) {
  const valuesByLang = Object.fromEntries(
    Object.entries(dictionaries).map(([lang, dict]) => [lang, new Set(leaves(dict).map(v => v.trim()))]),
  )
  const foreignStrings = {}
  for (const lang of Object.keys(dictionaries)) {
    const own = valuesByLang[lang]
    const foreign = new Set()
    for (const [other, values] of Object.entries(valuesByLang)) {
      if (other === lang) continue
      for (const value of values) {
        // Short or templated strings ("EUR", "{{price}} {{currency}}") prove nothing.
        if (value.length < 4 || value.includes('{{') || !/\p{L}{3}/u.test(value)) continue
        if (!own.has(value)) foreign.add(value)
      }
    }
    foreignStrings[lang] = foreign
  }
  const namespaces = Object.keys(Object.values(dictionaries)[0])
  return { namespaces, foreignStrings }
}

/** "ageGroups.Grade 0", "schoolCard.status.full": a translation key shown as text. */
export function findRawKeys(texts, namespaces) {
  // Not after "/", "@" or ".": "https://search.bg" and "info@map.com" are not keys.
  const pattern = new RegExp(`(?:^|[^\\w./@])(?:${namespaces.join('|')})\\.[A-Za-z0-9_]`)
  return texts.filter(text => pattern.test(text)).map(text => ({ check: 'raw-translation-key', text }))
}

const JUNK_RE = /(?:^|[^\p{L}\d_])(?:NaN|undefined|null|Infinity)(?![\p{L}\d_])|\[object \w+\]|\{\{|\}\}/u

/** Values that leaked from code: NaN, undefined, null, [object Object], {{placeholder}}. */
export function findJunk(texts) {
  return texts.filter(text => JUNK_RE.test(text)).map(text => ({ check: 'placeholder-junk', text }))
}

// "BGN" or the lev sign "лв" / "лв." standing alone, not inside a word.
const LEV_RE = /\bBGN\b|(?:^|[^\p{L}])лв(?![\p{L}])/iu

/** Bulgaria has used the euro since 2026; every price on the site is shown in euro. */
export function findLev(texts) {
  return texts.filter(text => LEV_RE.test(text)).map(text => ({ check: 'price-in-lev', text }))
}

// Grouped thousands ("1 200", "1,200", "1.200") or plain digits, with optional decimals.
const AMOUNT = String.raw`(?:\d{1,3}(?:[\s.,]\d{3})+|\d+)(?:[.,]\d{1,2})?`
const EURO_AMOUNTS_RE = new RegExp(String.raw`(${AMOUNT}(?:\s*[–-]\s*${AMOUNT})?)\s*(?:€|EUR\b)|€\s*(${AMOUNT}(?:\s*[–-]\s*€?\s*${AMOUNT})?)`, 'gu')

/** Parse "1,200", "1 200", "1.200", "12,50" or "12.50" to a number. */
export function parseAmount(raw) {
  const text = raw.replace(/[\s]/g, '')
  // A separator followed by exactly two digits at the end is a decimal mark.
  const decimal = text.match(/^(.*?)[.,](\d{1,2})$/)
  const whole = (decimal ? decimal[1] : text).replace(/[.,]/g, '')
  if (!/^\d+$/.test(whole)) return null
  return Number(decimal ? `${whole}.${decimal[2]}` : whole)
}

/** Every euro amount shown in one string. */
export function euroAmounts(text) {
  const amounts = []
  for (const match of text.matchAll(EURO_AMOUNTS_RE)) {
    for (const part of (match[1] || match[2]).split(/[–-]/)) {
      const value = parseAmount(part.replace('€', '').trim())
      if (value != null) amounts.push(value)
    }
  }
  return amounts
}

// No fee in Sofia is below €5 or above €50,000 per period; outside that, a unit or
// conversion is wrong.
export const PRICE_RANGE = { min: 5, max: 50_000 }

export function findImplausiblePrices(texts) {
  return texts
    .filter(text => euroAmounts(text).some(value => value < PRICE_RANGE.min || value > PRICE_RANGE.max))
    .map(text => ({ check: 'implausible-price', text }))
}

/**
 * The API sent a numeric euro or lev price for a private school but the page shows no
 * euro amount. State schools show no fees, and other currencies are shown as they are.
 */
export function findMissingPrice(texts, schoolPayload) {
  if (schoolPayload?.school_type === 'state') return []
  const rows = Array.isArray(schoolPayload?.pricing) ? schoolPayload.pricing : []
  const priced = rows.some(row =>
    ['EUR', 'BGN', undefined, null].includes(row.currency) &&
    [row.amount, row.amount_min, row.amount_max].some(v => v != null && Number.isFinite(Number(v))))
  if (!priced) return []
  if (texts.some(text => euroAmounts(text).length > 0)) return []
  return [{ check: 'price-not-shown', text: `${rows.length} pricing row(s) in the API response, no euro amount on the page` }]
}

/**
 * A UI label that only the other language uses, e.g. "Такса" on an English page. Text the
 * page marks with another `lang` (a school's own Bulgarian tags) is shown that way on purpose.
 */
export function findWrongLanguage(texts, lang, foreignStrings, otherLanguageTexts = new Set()) {
  const foreign = foreignStrings[lang] || new Set()
  return texts
    .filter(text => foreign.has(text.trim()) && !otherLanguageTexts.has(text))
    .map(text => ({ check: 'wrong-language', text }))
}

export function textFindings(texts, { lang, index, schoolPayload, otherLanguageTexts }) {
  return [
    ...findRawKeys(texts, index.namespaces),
    ...findJunk(texts),
    ...findLev(texts),
    ...findImplausiblePrices(texts),
    ...findWrongLanguage(texts, lang, index.foreignStrings, otherLanguageTexts),
    ...(schoolPayload ? findMissingPrice(texts, schoolPayload) : []),
  ]
}
