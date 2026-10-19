// School name matching for the results-list filter. Mirrors the backend search rules in
// backend/app/utils/school_search.py (see that file for the reasoning and alias sources);
// backend/tests/test_school_search.py fails when the two tables below drift apart.

// Official Bulgarian transliteration, lowercase only.
// No regex lookbehind or Object.hasOwn here: Safari < 16.4 cannot parse/run them.
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

// A query word for "saint" matches any name holding one as a whole word, checked before
// transliteration so "Ст." (Стара Загора) is not a saint.
export const SAINT_WORDS = [
  'св', 'свети', 'света', 'светите', 'st', 'sts', 'saint', 'saints',
  'sv', 'sveti', 'sveta', 'svetite',
]

// Rewrites applied in order to the query and the transliterated name so common Latin
// spellings match ("Car", "Khristo", "Paisii", "Djon", "Sofia", "UWEKIND"); see backend.
export const LOOSE_SPELLINGS = [
  ['w', 'v'],
  ['j', 'zh'],
  ['kh', 'h'],
  ['tz', 'ts'],
  ['c(?!h)', 'ts'],
  ['y', 'i'],
  ['i+', 'i'],
]
const LOOSE_RES = LOOSE_SPELLINGS.map(([pattern, repl]) => [new RegExp(pattern, 'g'), repl])

/** Lowercase, drop "№"/"No." and punctuation, split digits from letters, drop ordinals. */
export function normalizeSearchText(text) {
  return String(text || '')
    .toLowerCase()
    .replace(/№/g, ' ')
    .replace(/(^|[^a-z])no\.(?=\s*\d)/g, '$1 ')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/(\d)(?=\p{L})/gu, '$1 ')
    .replace(/(\p{L})(?=\d)/gu, '$1 ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/(\d+) (?:[вртмн][аиоя]|о|th|st|nd|rd)(?= |$)/g, '$1')
}

export function transliterate(text) {
  return Array.from(text, ch => (Object.prototype.hasOwnProperty.call(TRANSLIT, ch) ? TRANSLIT[ch] : ch)).join('')
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
  const loose = looseLatin(latin)
  const [normWords, latinWords, looseWords] = [norm, latin, loose].map(text => text.split(' '))
  return {
    norm, latin, loose,
    words: [...normWords, ...latinWords, ...looseWords],
    hasSaint: normWords.some(word => SAINT_WORDS.includes(word)),
    // Each word as [source, transliterated, loose]; the three line up.
    rows: normWords.map((word, i) => [word, latinWords[i], looseWords[i]]),
  }
}

function looseLatin(latin) {
  return LOOSE_RES.reduce((text, [re, repl]) => text.replace(re, repl), latin)
}

// In a longer word "u" may also stand for Ъ (Kushta, Dimitur).
const looseCache = new Map()
function looseForms(latin) {
  let forms = looseCache.get(latin)
  if (!forms) {
    forms = [looseLatin(latin)]
    if (latin.includes('u') && latin.length >= 4) forms.push(looseLatin(latin.split('u').join('a')))
    if (looseCache.size > 1000) looseCache.clear()
    looseCache.set(latin, forms)
  }
  return forms
}

function tokenIn(token, name) {
  const latin = transliterate(token)
  // "St"/"Sv" alone are also the start of Стефан, Свобода...: matched as text instead.
  if (latin.length > 3 && SAINT_WORDS.includes(latin) && name.hasSaint) return true
  if (/^\d+$/.test(token)) return new RegExp(`(^|\\D)${token}(?!\\d)`).test(name.norm)
  const stems = TYPE_LATIN.get(latin)
  if (stems) {
    return name.words.includes(latin) || stems.every(stem => name.latin.includes(stem))
  }
  return name.norm.includes(token) || name.latin.includes(latin) || looseForms(latin).some(form => name.loose.includes(form))
}

function tokensMatch(tokens, names) {
  return names.some(name => name.norm && tokens.every(token => tokenIn(token, name)))
}

function wordStarts(token, [norm, latinWord, looseWord]) {
  const latin = transliterate(token)
  if (SAINT_WORDS.includes(latin) && SAINT_WORDS.includes(norm)) return true
  if (/^\d+$/.test(token)) return norm === token
  return norm.startsWith(token) || latinWord.startsWith(latin) || looseForms(latin).some(form => looseWord.startsWith(form))
}

// Like tokenIn, but plain words must start a word of the name.
function tokenStartsWord(token, name) {
  const latin = transliterate(token)
  if (latin.length > 3 && SAINT_WORDS.includes(latin) && name.hasSaint) return true
  if (/^\d+$/.test(token) || TYPE_LATIN.has(latin)) return tokenIn(token, name)
  const starts = [token, latin, ...looseForms(latin)]
  return name.words.some(word => starts.some(start => word.startsWith(start)))
}

// The query's words, in order, start consecutive words of the name.
function isPhrase(tokens, name) {
  for (let start = 0; start + tokens.length <= name.rows.length; start++) {
    if (tokens.every((token, i) => wordStarts(token, name.rows[start + i]))) return true
  }
  return false
}

// How well the query fits, lower is better; mirrors rank_school in the backend.
export const MATCH_RANK = { alias: 0, number: 1, phrase: 2, prefix: 3, contains: 4, none: 5 }

function tokensRank(tokens, names) {
  const firstNumber = tokens.find(token => /^\d+$/.test(token))
  let best = MATCH_RANK.none
  for (const name of names) {
    if (!name.norm || !tokens.every(token => tokenIn(token, name))) continue
    let rank = MATCH_RANK.contains
    if (firstNumber && name.norm.match(/\d+/)?.[0] === firstNumber) rank = MATCH_RANK.number
    else if (isPhrase(tokens, name)) rank = MATCH_RANK.phrase
    else if (tokens.every(token => tokenStartsWord(token, name))) rank = MATCH_RANK.prefix
    best = Math.min(best, rank)
  }
  return best
}

/**
 * How well the query matches one of the school's names in any language ("119 СУ",
 * "СУ 119", "№119", "СМГ", "sofiyska"): a MATCH_RANK value, MATCH_RANK.none when it
 * does not match. An empty query matches everything.
 */
export function schoolMatchRank(school, query) {
  const normalized = normalizeSearchText(query)
  if (!normalized) return MATCH_RANK.contains
  const tokens = normalized.split(' ')
  const names = [
    ...Object.values(school?.resolved_name_i18n || {}),
    ...Object.values(school?.name_i18n || {}),
  ].map(prepareName)

  const registry = normalizeSearchText(school?.name_i18n?.bg)
  const latinTokens = tokens.map(transliterate)
  const aliased = ALIASES_LATIN.some(([aliasTokens, target]) => {
    if (!registry.includes(target)) return false
    for (let start = 0; start + aliasTokens.length <= tokens.length; start++) {
      if (aliasTokens.every((t, i) => latinTokens[start + i] === t)) {
        const rest = [...tokens.slice(0, start), ...tokens.slice(start + aliasTokens.length)]
        if (rest.length === 0 || tokensMatch(rest, names)) return true
      }
    }
    return false
  })
  return aliased ? MATCH_RANK.alias : tokensRank(tokens, names)
}

/** True when the query matches one of the school's names. An empty query matches everything. */
export function schoolMatchesQuery(school, query) {
  return schoolMatchRank(school, query) < MATCH_RANK.none
}

/**
 * Schools a name query keeps: those whose names match in the browser or, when none does,
 * those the API's search found (`hits`, a set of ids), which knows more ways to write a
 * name (a brand only in the web address).
 */
export function filterByName(schools, query, hits) {
  const matched = schools.filter(school => schoolMatchesQuery(school, query))
  return matched.length || !hits.size ? matched : schools.filter(school => hits.has(school.id))
}

/** Schools ordered by how well their names fit the query; `compareTies` orders equal fits. */
export function sortByNameMatch(schools, query, compareTies) {
  const ranks = new Map(schools.map(school => [school.id, schoolMatchRank(school, query)]))
  return [...schools].sort((a, b) => ranks.get(a.id) - ranks.get(b.id) || compareTies(a, b))
}
