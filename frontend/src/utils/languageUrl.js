/**
 * The URL is the source of truth for the UI language: Bulgarian lives at `/`,
 * English under `/en/`.
 *
 * Two kinds of path appear here, and they must not be mixed up:
 * - a raw pathname is what the browser shows (`/en/schools/12`);
 * - a route path is what the router reports under its basename (`/schools/12`),
 *   with the language prefix already removed.
 */

export const DEFAULT_LANGUAGE = 'bg'

const LANGUAGE_PREFIXES = { bg: '', en: '/en' }

export const URL_LANGUAGES = Object.keys(LANGUAGE_PREFIXES)

/** Language of a raw browser pathname. Only a whole first segment counts (`/english` is Bulgarian). */
export function languageFromPath(rawPathname) {
  return /^\/en(\/|$)/.test(rawPathname || '') ? 'en' : DEFAULT_LANGUAGE
}

/** Router basename for a language. */
export function languageBasename(language) {
  return LANGUAGE_PREFIXES[language] || '/'
}

/** Raw pathname of a route path in the given language. */
export function languagePath(routePath, language) {
  const path = routePath && routePath.startsWith('/') ? routePath : `/${routePath || ''}`
  return `${LANGUAGE_PREFIXES[language] ?? ''}${path}`
}

/** Reciprocal hreflang alternates (absolute URLs) for a route path; x-default is Bulgarian. */
export function alternateLinks(routePath, origin) {
  const base = String(origin || '').replace(/\/+$/, '')
  return [
    ...URL_LANGUAGES.map((language) => ({
      hreflang: language,
      href: `${base}${languagePath(routePath, language)}`,
    })),
    { hreflang: 'x-default', href: `${base}${languagePath(routePath, DEFAULT_LANGUAGE)}` },
  ]
}
