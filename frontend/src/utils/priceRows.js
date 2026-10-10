import { gradeRangeInText, rowGradeBand } from './gradeBand.js'

/**
 * Facts a price row states in its own words: how often it is paid and which age groups it
 * is for. The API keeps `period` null when the extractor missed it, but the fee's name
 * often says it ("ГОДИШНА ТАКСА", "Месечната такса", "Таксата за първи срок").
 */

// Each period with the words that state it. A name matching two periods states neither.
// No one-off words: a tuition's "Еднократно плащане" is the year's fee paid at once.
const PERIOD_WORDS = [
  // Not "5-годишни деца" (five-year-olds) or "6-месечни бебета" (six-month-olds).
  ['yearly', /(?<![\p{L}\p{N}-])(?<!\p{N}\s)годишн|за (?:учебната |цялата )?(?:учебна )?година|annual|yearly|per year/u],
  ['monthly', /(?<![\p{L}\p{N}-])(?<!\p{N}\s)(?:еже)?месечн|на месец|monthly|per month/u],
  ['semester', /семест|semester/u],
  ['term', /(?:^|[^\p{L}])(?:срок|срока)(?![\p{L}])|за първи срок|за втори срок|per term|\bterm\b/u],
  ['quarter', /тримесеч|quarter/u],
]

/** The period a fee name states, or null when it states none or more than one. */
export function periodFromLabel(text) {
  const label = String(text || '').toLocaleLowerCase('bg')
  if (!label) return null
  const found = PERIOD_WORDS.filter(([, re]) => re.test(label)).map(([period]) => period)
  return found.length === 1 ? found[0] : null
}

/** How often a row is paid: its stored period, else the one its own name states, else null. */
export function pricePeriod(row) {
  return row?.period || periodFromLabel(row?.plan_name) || null
}

// Kindergarten group words: "Първа и Втора група", "3-та група", "Third group".
const GROUP_ORDINALS = [
  ['first', 'първа|1(?:-?ва)?|i|first'],
  ['second', 'втора|2(?:-?ра)?|ii|second'],
  ['third', 'трета|3(?:-?та)?|iii|third'],
  ['preschool', 'четвърта|4(?:-?та)?|iv|fourth'],
]
const ORDINAL = GROUP_ORDINALS.map(([, words]) => words).join('|')
const GROUP_LIST_RE = new RegExp(
  `(?<![\\p{L}\\p{N}])((?:${ORDINAL})(?:\\s*(?:,|и|and|&|-|–)\\s*(?:${ORDINAL}))*)\\s+(?:възрастов[аи]\\s+)?(?:група|групи|groups?)(?![\\p{L}])`,
  'gu',
)
const ORDINAL_RE = new RegExp(`(?<![\\p{L}\\p{N}])(?:${ORDINAL})(?![\\p{L}\\p{N}])`, 'gu')
const NURSERY_RE = /ясла|яслен|nursery|cr[eè]che/u

const AGE_GROUP_KEYS = new Set([
  'nursery', 'first', 'second', 'third', 'preschool', 'grade_1_4', 'grade_5_7', 'grade_8_12',
])

// School grade bands, as in the country config (grade_1_4 is grades 1-4).
const GRADE_KEYS = [
  ['grade_1_4', 1, 4],
  ['grade_5_7', 5, 7],
  ['grade_8_12', 8, 12],
]

function groupKeys(text) {
  const keys = new Set()
  const normalized = String(text || '').toLocaleLowerCase('bg')
  if (NURSERY_RE.test(normalized)) keys.add('nursery')
  for (const match of normalized.matchAll(GROUP_LIST_RE)) {
    for (const [word] of match[1].matchAll(ORDINAL_RE)) {
      const entry = GROUP_ORDINALS.find(([, words]) => new RegExp(`^(?:${words})$`, 'u').test(word))
      if (entry) keys.add(entry[0])
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
  const band = rowGradeBand(row) || gradeRangeInText(row?.plan_name)
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
