import { gradesNamedIn, rowGradeBand } from './gradeBand.js'

/**
 * Facts a price row states in its own words: how often it is paid and which age groups it
 * is for. The API keeps `period` null when the extractor missed it, but the fee's name
 * often says it ("ГОДИШНА ТАКСА", "Месечната такса", "Таксата за първи срок").
 */

// Each period with the words that state it. A name matching two periods states neither.
// No one-off words: a tuition's "Еднократно плащане" is the year's fee paid at once.
const PERIOD_WORDS = [
  // Not "5-годишни деца" (five-year-olds), "6-месечни бебета" (six-month-olds) or
  // "годишнина" (an anniversary).
  ['yearly', /(?<![\p{L}\p{N}-])(?<!\p{N}\s)годишн(?!ин)|за (?:учебната |цялата )?(?:учебна )?година|(?<![\p{L}-])annual|yearly|per year/u],
  ['monthly', /(?<![\p{L}\p{N}-])(?<!\p{N}\s)(?:еже)?месечн|на месец|monthly|per month/u],
  ['semester', /семест|semester|полугодишн|semi[- ]?annual|bi-?annual/u],
  // "за първи срок", "на срок", "I-ви срок"; never a deadline's "в срок до 30.06" or a
  // length's "за срок от 1 година".
  ['term', /(?:(?<![\p{L}])(?:първия|втория|първи|втори|един|i|ii)(?:-?(?:ви|ри))? |на |за )срок(?![\p{L}])(?!\s+от)|per term|(?<![\p{L}-])term(?:ly)? fee/u],
  ['quarter', /тримесеч|quarter/u],
]

/** The period a fee name states, or null when it states none or more than one. */
export function periodFromLabel(text) {
  const label = String(text || '').toLocaleLowerCase('bg')
  if (!label) return null
  const found = PERIOD_WORDS.filter(([, re]) => re.test(label)).map(([period]) => period)
  return found.length === 1 ? found[0] : null
}

// The whole fee paid at once: "Плащане на 1 вноска", "на една вноска", "in one payment";
// not "1 вноска до 15.09" or "10 вноски, 1 вноска = 500", which are one instalment.
const ONE_PAYMENT_RE = /(?<![\p{L}])(?<!(?:размер|сума|цена|стойност)\s)(?:на|в)\s+(?:1|една)\s+вноска(?![\p{L}])(?!\s+от\s)|еднократна\s+вноска|in (?:one|1|a single) (?:payment|instal+ment)|(?<![\p{L}\p{N}-])(?:1|one|single) instal+ment(?![\p{L}])|single payment/iu
const SEVERAL_INSTALMENTS_RE = /(?<![\p{L}\p{N}])(?:[2-9]|\d{2,}|две|три|четири|пет|шест|седем|осем|девет|десет)\s+вноски|общо|instal+ments/iu

/** True when a fee name says the whole fee is paid in one payment. */
export function isOnePayment(name) {
  return ONE_PAYMENT_RE.test(name || '') && !SEVERAL_INSTALMENTS_RE.test(name || '')
}

/**
 * How often a row is paid: its stored period, else the one its own name states, else null.
 * Tuition paid "на 1 вноска" (in one payment) is the year's fee.
 */
export function pricePeriod(row) {
  const named = periodFromLabel(row?.plan_name)
  if (row?.period || named) return row?.period || named
  if (row?.category === 'tuition' && isOnePayment(row?.plan_name)) return 'yearly'
  return null
}

// Registration fees are paid once and uniform items are bought once, so a missing period is no news.
const ONE_OFF_CATEGORIES = new Set(['registration', 'uniforms'])

/** True when a row's period is unknown and a parent could misread how often it is paid. */
export function periodUnknown(row) {
  return !pricePeriod(row) && !ONE_OFF_CATEGORIES.has(row?.category)
}

// Kindergarten group words: "Първа и Втора група", "Първа - трета група", "3-та група",
// "Third group". The fourth group is the preschool year.
const GROUP_ORDER = ['first', 'second', 'third', 'preschool']
const GROUP_ORDINALS = [
  ['first', 'първа|1(?:-?ва)?|i|first'],
  ['second', 'втора|2(?:-?ра)?|ii|second'],
  ['third', 'трета|3(?:-?та)?|iii|third'],
  ['preschool', 'четвърта|4(?:-?та)?|iv|fourth'],
]
const ORDINAL = GROUP_ORDINALS.map(([, words]) => words).join('|')
const GROUP_LIST_RE = new RegExp(
  `(?<![\\p{L}\\p{N}])((?:${ORDINAL})(?:\\s*(?:,|и|and|&|-|–)\\s*(?:${ORDINAL}))*)\\s+(?:възрастов[аи]\\s+)?(група|групи|groups?)(?![\\p{L}])`,
  'gu',
)
const ORDINAL_TOKEN_RE = new RegExp(`(?<![\\p{L}\\p{N}])(${ORDINAL})(?![\\p{L}\\p{N}])|(-|–)`, 'gu')
// "Ясла", "Яслена група"; but "ДГ и ясла" or "Детска ясла и градина" name both, so neither.
const NURSERY_RE = /яслен|nursery|cr[eè]che|(?<![\p{L}])ясл(?:а|и|ата)(?![\p{L}])/u
const BOTH_RE = /ясл(?:а|и)\s*(?:и|\/|,)\s*(?:детск[аи] )?(?:градин|дг(?![\p{L}]))|(?:градин[аи]|(?<![\p{L}])дг)\s*(?:и|\/|,)\s*(?:детска )?ясл|яслена и градинска/u
const PRESCHOOL_NAME_RE = /подготвителн[аи] груп|предучилищн[аи] груп|подготвителен клас|(?<![\p{L}])п[гк]у?(?![\p{L}])|pre-?school/u

const AGE_GROUP_KEYS = new Set([
  'nursery', 'first', 'second', 'third', 'preschool', 'grade_1_4', 'grade_5_7', 'grade_8_12',
])

// School grade bands, as in the country config (grade_1_4 is grades 1-4).
const GRADE_KEYS = [
  ['grade_1_4', 1, 4],
  ['grade_5_7', 5, 7],
  ['grade_8_12', 8, 12],
]

function ordinalKey(word) {
  return GROUP_ORDINALS.find(([, words]) => new RegExp(`^(?:${words})$`, 'u').test(word))?.[0]
}

function groupKeys(text) {
  const keys = new Set()
  const normalized = String(text || '').toLocaleLowerCase('bg')
  if (NURSERY_RE.test(normalized) && !BOTH_RE.test(normalized)) keys.add('nursery')
  if (PRESCHOOL_NAME_RE.test(normalized)) keys.add('preschool')
  for (const match of normalized.matchAll(GROUP_LIST_RE)) {
    // "2 групи" counts groups; only a worded ordinal names them in the plural.
    const plural = match[2] !== 'група' && match[2] !== 'group'
    let previous = null
    let dash = false
    for (const [, word, separator] of match[1].matchAll(ORDINAL_TOKEN_RE)) {
      if (separator) {
        dash = true
        continue
      }
      const key = ordinalKey(word)
      if (!key || (plural && /^\d/.test(word))) {
        dash = false
        continue
      }
      if (dash && previous) {
        // A range: "Първа - трета група" is the second group too.
        const [from, to] = [GROUP_ORDER.indexOf(previous), GROUP_ORDER.indexOf(key)]
        GROUP_ORDER.slice(Math.min(from, to), Math.max(from, to) + 1).forEach(k => keys.add(k))
      } else {
        keys.add(key)
      }
      previous = key
      dash = false
    }
  }
  return keys
}

function gradeKeys(band) {
  const keys = new Set()
  if (!band) return keys
  if (band.from === 0) keys.add('preschool')
  for (const [key, from, to] of GRADE_KEYS) {
    if (band.from <= to && band.to >= from) keys.add(key)
  }
  return keys
}

/**
 * The `ageGroups` keys a row is for ("first", "preschool", "grade_5_7"), read from its age
 * group and plan name. Empty when neither names a group we can read: such a row is not
 * known to be for another group.
 */
export function rowAgeGroupKeys(row) {
  const keys = new Set()
  if (AGE_GROUP_KEYS.has(row?.age_group?.trim())) keys.add(row.age_group.trim())
  const band = rowGradeBand(row) || gradesNamedIn(row?.age_group) || gradesNamedIn(row?.plan_name)
  for (const key of gradeKeys(band)) keys.add(key)
  for (const key of groupKeys(`${row?.age_group || ''} / ${row?.plan_name || ''}`)) keys.add(key)
  return keys
}

/**
 * The rows for one age group: those naming it, plus those naming no group we can read.
 * When no row names a group, every row is returned: there is nothing to choose by.
 */
export function rowsForAgeGroup(rows = [], ageGroup) {
  if (!ageGroup) return rows
  const keyed = rows.map(row => ({ row, keys: rowAgeGroupKeys(row) }))
  if (keyed.every(({ keys }) => keys.size === 0)) return rows
  return keyed.filter(({ keys }) => keys.size === 0 || keys.has(ageGroup)).map(({ row }) => row)
}
