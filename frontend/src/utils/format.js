/** The Intl locale for the UI language: Bulgarian, else British English (the app's English). */
export function intlLocale(language) {
  return String(language || '').startsWith('bg') ? 'bg-BG' : 'en-GB'
}

/**
 * "1.2 km" (English) or "1,2 км" (Bulgarian); prefixed with "~" when measured from an
 * approximate (street-level) pin.
 */
export function formatDistance(distanceKm, { approximate = false, language = 'bg' } = {}) {
  if (typeof distanceKm !== 'number' || !Number.isFinite(distanceKm)) return null
  const text = new Intl.NumberFormat(intlLocale(language), {
    style: 'unit',
    unit: 'kilometer',
    unitDisplay: 'short',
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(distanceKm)
  return `${approximate ? '~' : ''}${text}`
}
