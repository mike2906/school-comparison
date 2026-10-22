import { intlLocale } from './format.js'

/** The link for a stored source URL, or null unless it is a plain http(s) address. */
export function safeSourceUrl(url) {
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null
  } catch {
    return null
  }
}

/**
 * The link for a school's website: an http(s) address, or a bare host as registers often
 * store it ("www.school.bg" opens https://www.school.bg/). Anything else (javascript:, data:)
 * gets no link.
 */
export function safeWebsiteUrl(url) {
  const value = typeof url === 'string' ? url.trim() : ''
  if (!value) return null
  if (/^[a-z][a-z0-9+.-]*:/i.test(value)) return safeSourceUrl(value)
  return /^[\p{L}\p{N}-]+(\.[\p{L}\p{N}-]+)+([/?#].*)?$/u.test(value) ? safeSourceUrl(`https://${value}`) : null
}

/** A short name for a source page: its site without "www." ("roerich-school.org"). */
export function sourceSiteName(url) {
  const safe = safeSourceUrl(url)
  return safe ? new URL(safe).hostname.replace(/^www\./, '') : null
}

/** The day we checked a price ("17 Sept 2026"), or null when the timestamp is unusable. */
export function formatCheckedDate(value, language) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return new Intl.DateTimeFormat(intlLocale(language), {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date)
}

/**
 * The one source a set of price rows shares, or null when they come from different pages
 * or kinds of source (each row then shows its own).
 *
 * Returns `{ url, source, checkedAt }`: `url` null when no row has a usable link,
 * `checkedAt` the latest time any of the rows was checked.
 */
export function sharedPriceSource(rows = []) {
  if (rows.length === 0) return null
  const urls = new Set(rows.map(row => safeSourceUrl(row.source_url)))
  const kinds = new Set(rows.map(row => row.source || null))
  if (urls.size !== 1 || kinds.size !== 1) return null
  const checkedAt = rows
    .map(row => row.scraped_at)
    .filter(value => value && !Number.isNaN(new Date(value).getTime()))
    .sort((a, b) => new Date(b) - new Date(a))[0] || null
  return { url: [...urls][0], source: [...kinds][0], checkedAt }
}
