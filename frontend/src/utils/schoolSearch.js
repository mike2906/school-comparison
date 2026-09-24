// School name matching for the results-list filter. Mirrors the backend search rules in
// backend/app/utils/school_search.py (see that file for the reasoning and alias sources);
// backend/tests/test_school_search.py fails when the two tables below drift apart.

// Official Bulgarian transliteration, lowercase only.
const TRANSLIT = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ж: 'zh', з: 'z',
  и: 'i', й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p',
  р: 'r', с: 's', т: 't', у: 'u', ф: 'f', х: 'h', ц: 'ts', ч: 'ch',
  ш: 'sh', щ: 'sht', ъ: 'a', ь: 'y', ю: 'yu', я: 'ya',
}

// School-type abbreviation -> word stems it stands for.
export const TYPE_ABBREVIATIONS = {
  'су': ['средн', 'училищ'],
  'соу': ['средн', 'училищ'],
  'оу': ['основн', 'училищ'],
  'ну': ['начал', 'училищ'],
  'дг': ['детск', 'градин'],
  'пг': ['гимназ'],
  'сеу': ['средн', 'езиков', 'училищ'],
  'ег': ['езиков', 'гимназ'],
  'пег': ['езиков', 'гимназ'],
  'аег': ['английск', 'гимназ'],
  'нег': ['немск', 'гимназ'],
  'фег': ['френск', 'гимназ'],
  'иег': ['испанск', 'гимназ'],
  'чсу': ['частн', 'средн', 'училищ'],
  'чоу': ['частн', 'основн', 'училищ'],
  'чну': ['частн', 'начал', 'училищ'],
  'чдг': ['частн', 'детск', 'градин'],
}

// Curated acronym -> normalised substring of the registry name (name_i18n.bg).
export const SCHOOL_NAME_ALIASES = {
  'смг': 'софийска математическа гимназия',
  'нпмг': 'национална природо математическа гимназия',
  '1 аег': 'първа английска езикова гимназия',
  'i аег': 'първа английска езикова гимназия',
  'първа аег': 'първа английска езикова гимназия',
  '2 аег': 'втора английска езикова гимназия',
  'ii аег': 'втора английска езикова гимназия',
  'втора аег': 'втора английска езикова гимназия',
  'нгдек': 'национална гимназия за древни езици и култури',
  'нфсг': 'национална финансово стопанска гимназия',
  'нтбг': 'национална търговско банкова гимназия',
  'спге': 'софийска професионална гимназия по електроника',
  'туес': 'технологично училище електронни системи',
  'нму': 'национално музикално училище',
  'нгпи': 'национална гимназия за приложни изкуства',
  'нуии': 'национално училище за изящни изкуства',
  'acs': 'американски колеж',
}

/** Lowercase, drop "№"/"No." and punctuation, split digits from letters, drop ordinals. */
export function normalizeSearchText(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/№/g, ' ')
    .replace(/(?<![a-z])no\.(?=\s*\d)/g, ' ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/(\d)(?=\p{L})/gu, '$1 ')
    .replace(/(\p{L})(?=\d)/gu, '$1 ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/(\d+) (?:[вртмн][аиоя]|о|th|st|nd|rd)(?= |$)/g, '$1')
}

export function transliterate(text) {
  return Array.from(text, ch => (Object.hasOwn(TRANSLIT, ch) ? TRANSLIT[ch] : ch)).join('')
}

const TYPE_LATIN = new Map(
  Object.entries(TYPE_ABBREVIATIONS).map(([key, stems]) => [transliterate(key), stems.map(transliterate)])
)
const ALIASES_LATIN = Object.entries(SCHOOL_NAME_ALIASES).map(
  ([alias, target]) => [transliterate(alias).split(' '), normalizeSearchText(target)]
)

function prepareName(value) {
  const norm = normalizeSearchText(value)
  const latin = transliterate(norm)
  return { norm, latin, words: [...norm.split(' '), ...latin.split(' ')] }
}

function tokenIn(token, name) {
  const latin = transliterate(token)
  if (/^\d+$/.test(token)) return new RegExp(`(?<!\\d)${token}(?!\\d)`).test(name.norm)
  const stems = TYPE_LATIN.get(latin)
  if (stems) {
    return name.words.includes(latin) || stems.every(stem => name.latin.includes(stem))
  }
  return name.norm.includes(token) || name.latin.includes(latin)
}

function tokensMatch(tokens, names) {
  return names.some(name => name.norm && tokens.every(token => tokenIn(token, name)))
}

/**
 * True when the query matches one of the school's names in any language ("119 СУ",
 * "СУ 119", "№119", "СМГ", "sofiyska"). An empty query matches everything.
 */
export function schoolMatchesQuery(school, query) {
  const normalized = normalizeSearchText(query)
  if (!normalized) return true
  const tokens = normalized.split(' ')
  const names = [
    ...Object.values(school?.resolved_name_i18n || {}),
    ...Object.values(school?.name_i18n || {}),
  ].map(prepareName)
  if (tokensMatch(tokens, names)) return true

  const registry = normalizeSearchText(school?.name_i18n?.bg)
  const latinTokens = tokens.map(transliterate)
  return ALIASES_LATIN.some(([aliasTokens, target]) => {
    if (!registry.includes(target)) return false
    for (let start = 0; start + aliasTokens.length <= tokens.length; start++) {
      if (aliasTokens.every((t, i) => latinTokens[start + i] === t)) {
        const rest = [...tokens.slice(0, start), ...tokens.slice(start + aliasTokens.length)]
        if (rest.length === 0 || tokensMatch(rest, names)) return true
      }
    }
    return false
  })
}
