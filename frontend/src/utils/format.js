/** The Intl locale for the UI language: Bulgarian, else British English (the app's English). */
export function intlLocale(language) {
  return String(language || '').startsWith('bg') ? 'bg-BG' : 'en-GB'
}

/** A plain localised number ("1 200" / "1,200"); null for no value. */
export function formatAmount(value, language, digits = 0) {
  if (value == null || Number.isNaN(value)) return null
  return new Intl.NumberFormat(intlLocale(language), { maximumFractionDigits: digits }).format(value)
}

const kmFormatters = new Map()

/** A distance in km as a localised number with one decimal: "1.2" (English), "1,2" (Bulgarian). */
export function formatKm(distanceKm, language) {
  if (typeof distanceKm !== 'number' || !Number.isFinite(distanceKm)) return null
  const locale = intlLocale(language)
  if (!kmFormatters.has(locale)) {
    kmFormatters.set(locale, new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }))
  }
  return kmFormatters.get(locale).format(distanceKm)
}

/**
 * "1.2 km" / "1,2 км" through the `schoolDetail.distanceValue` i18n keys; "~" when
 * measured from an approximate (street-level) pin.
 */
export function formatDistance(distanceKm, { approximate = false, language, t }) {
  const km = formatKm(distanceKm, language)
  if (km == null) return null
  return t(approximate ? 'schoolDetail.distanceValueApprox' : 'schoolDetail.distanceValue', { km })
}
