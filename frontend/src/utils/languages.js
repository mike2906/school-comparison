/**
 * Language names as they arrive in school data ("English", "английски език", "bg") mapped
 * to one key, so the UI can show them in the reader's language. Unknown values are shown
 * as given (capitalised), never dropped.
 */
const ALIASES = {
  english: 'english', en: 'english', английски: 'english', английский: 'english',
  bulgarian: 'bulgarian', bg: 'bulgarian', български: 'bulgarian',
  german: 'german', de: 'german', немски: 'german',
  french: 'french', fr: 'french', френски: 'french',
  spanish: 'spanish', es: 'spanish', испански: 'spanish',
  russian: 'russian', ru: 'russian', руски: 'russian',
  italian: 'italian', it: 'italian', италиански: 'italian',
  chinese: 'chinese', zh: 'chinese', китайски: 'chinese',
  japanese: 'japanese', ja: 'japanese', японски: 'japanese',
  turkish: 'turkish', tr: 'turkish', турски: 'turkish',
  greek: 'greek', el: 'greek', гръцки: 'greek',
  arabic: 'arabic', ar: 'arabic', арабски: 'arabic',
  korean: 'korean', ko: 'korean', корейски: 'korean',
  romanian: 'romanian', ro: 'romanian', румънски: 'romanian',
  hebrew: 'hebrew', he: 'hebrew', иврит: 'hebrew',
  danish: 'danish', da: 'danish', датски: 'danish',
  norwegian: 'norwegian', no: 'norwegian', норвежки: 'norwegian',
}

/** The canonical key for a language value, or null when it is not a known language. */
export function languageKey(value) {
  const cleaned = String(value || '')
    .trim()
    .toLocaleLowerCase()
    .replace(/\s+(език|language)$/u, '')
  return ALIASES[cleaned] || null
}

/** The language name in the reader's language (via `languages.<key>`), else the value as given. */
export function languageLabel(value, t) {
  if (!value) return ''
  const key = languageKey(value)
  if (key) return t(`languages.${key}`)
  const text = String(value).trim()
  return text.charAt(0).toUpperCase() + text.slice(1)
}
