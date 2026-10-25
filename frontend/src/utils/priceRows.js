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

// A fee for pupils a new family's child is not: special educational needs (171's "Ученици със
// СОП"), pupils already at the school (555's returning-children rate), staff's children.
const CLOSED_GROUP_RE = /(?<![\p{L}])соп(?![\p{L}])|специални образователни потребности|special (?:educational )?needs|(?:returning|existing|current) (?:students|pupils|children)|настоящи ученици|деца на (?:служители|учители|персонала)|(?:staff|employees?)['’]?s? children|children of (?:staff|employees)/iu
// The English abbreviation only in capitals: "Sen. school" is not one.
const SEN_RE = /(?<![\p{L}])SEN(?![\p{L}])/u
// Free-text notes count only when they open by naming the group: 594's "For returning students
// with signed contracts…", not "Same fee for new and returning students".
const CLOSED_GROUP_NOTE_RE = /^\s*(?:for |за )?(?:returning|existing|current|настоящи|продължаващи) (?:students|pupils|children|ученици)/iu

/** True when a fee's own words say it is only for a closed group of pupils. */
export function isClosedGroupFee(row) {
  const context = row?.pricing_context || {}
  const words = [row?.plan_name, row?.age_group, context.plan_name_en, context.age_group_en]
  return words.some(text => CLOSED_GROUP_RE.test(String(text || '')) || SEN_RE.test(String(text || '')))
    || CLOSED_GROUP_NOTE_RE.test(String(context.notes || ''))
}

// The terms a price is on when it is not the school's regular price, by the words the fees
// reader is told to put in its name (`docs/AGENT_FEE_EXTRACTION.md`): a promotion (625's
// "промоционална такса за първите 3 месеца", 171's Christmas offer), a discount (505's
// "2nd child (-9%)", 286's second child) or surcharge ("+5%"), a prepaid price (625's
// "Еднократно плащане за 6 месеца", 185's "при предплащане"), a price for paying in parts
// (568's "при разсрочено плащане") or for paying the whole fee at once (220's €250 off).
// Not "при месечно плащане" (185), the regular way to pay, nor 154's "annual subscription",
// its regular contract next to a dearer flexible one.
const PRICE_TERMS = [
  ['promotion', /промоци|(?<![\p{L}])промо(?![\p{L}])|promo(?:tion|tional)?(?![\p{L}])|special offer|специална (?:оферта|цена)|коледн|christmas|early[- ]bird|ранно записване/iu],
  ['discount', /отстъпк|discount|намаление|(?:втор|трет|четвърт)(?:о|ото) дете|(?:second|third|fourth|2nd|3rd|4th) child|sibling|[(\s][-−–+]\s?\d+(?:[.,]\d+)?\s?%/iu],
  // Paid ahead for a stated stretch: "за 6 месеца", "за цяла година", "for 6 months". A
  // monthly fee "платима авансово" (paid at the start of the month) is the regular price.
  ['prepaid', /(?:предплащ|авансов|еднократно|наведнъж|prepa(?:id|y)|in advance|at once)[^/]*?(?:за|for) (?:\d+|цял|целия|една|един|a whole|the whole|a full|the full|one) ?(?:\p{L}+ )?(?:месец|месеца|година|срок|семест|months?|year|term|semester)|(?:за|for) (?:\d+|цял\p{L}*|a whole|the whole) (?:учебна )?(?:месеца|година|months|year)[^/]*?(?:at once|наведнъж|предварително|in advance)/iu],
  ['instalments', /разсроч|deferred|(?<![\p{L}])(?:на|в|in)\s+(?:[2-9]|1\d|две|три|четири|пет|шест|девет|десет|two|three|four|five|six|nine|ten)\s+(?:равни\s+|equal\s+|месечни\s+|monthly\s+)?(?:вноски|instal+ments|payments)|(?<![\p{L}])(?:на|in)\s+(?:вноски|instal+ments)/iu],
  // 199's "Такса / годишно плащане" column beside its monthly fee: the year paid at once.
  ['one_payment', /еднократно плащане|one-time payment|paid in full|(?<![\p{L}])годишно плащане|annual payment/iu],
]

/**
 * The terms a fee's own name puts it on (`promotion`, `discount`, `prepaid`, `instalments`,
 * `one_payment`), or null for the school's regular price.
 */
export function priceTerms(row) {
  const words = [row?.plan_name, row?.pricing_context?.plan_name_en].filter(Boolean).join(' / ')
  if (!words) return null
  const found = PRICE_TERMS.find(([, re]) => re.test(words))
  if (found) return found[0]
  return isOnePayment(row?.plan_name) ? 'one_payment' : null
}

// Care for part of the day: "Полудневен престой", "половин ден", "Half day", a day that ends by
// 14:00 (286's "до 13:00 часа", 330's "8:00 – 13 часа"). Not "целодневно" (full day), nor a
// deadline's "до 12.10".
const HALF_DAY_RE = /полудн|половин ден|half[- ]day|(?<![\p{L}])до обяд|(?<![\p{L}\p{N}])до 1[1-4](?::\d\d|(?:[.]\d\d)?\s*(?:ч|h))|\d[:.]\d\d\s*[–-]\s*1[1-4](?::\d\d|(?:[.]\d\d)?\s*(?:ч|h))/iu
const FULL_DAY_RE = /целодн|цял ден|full[- ]day/iu

/** True for a fee whose own words say it is for half a day. */
export function isHalfDay(row) {
  const words = [row?.plan_name, row?.age_group, row?.pricing_context?.plan_name_en].filter(Boolean)
  // "Целодневна група, обяд до 12 ч." is a full day that names a time.
  return words.some(text => HALF_DAY_RE.test(text)) && !words.some(text => FULL_DAY_RE.test(text))
}

/**
 * The heading a fee goes under: its category, except that a deposit (returned or taken off a
 * later fee) is listed apart from the registration fees it is filed with.
 */
export function priceCategory(row) {
  return row?.category === 'registration' && row?.pricing_context?.deposit === true ? 'deposit' : row?.category
}

// Admission fees (enrolment, application, an entry exam or contest: the `registration` category,
// shown as "Admission fee" so 555's exam fee is not called a registration) are paid once and
// uniform items are bought once, so a missing period is no news.
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
// 555's English "PREPARATORY GROUP | YEAR 2" too.
const PRESCHOOL_NAME_RE = /подготвителн[аи] груп|предучилищн[аи] груп|предучилищен клас|подготвителен клас|(?<![\p{L}])п[гк]у?(?![\p{L}])|pre-?school|preparatory (?:group|class|year)/u

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
 * A school's fee rows split by one age group: `forGroup` holds the rows naming it plus
 * those naming no group we can read (they may well apply, so they are never set aside),
 * `otherAges` the rows that name only other groups. With no group, or one we do not know,
 * every row is `forGroup`.
 */
export function splitRowsForAgeGroup(rows = [], ageGroup) {
  if (!AGE_GROUP_KEYS.has(ageGroup)) return { forGroup: rows, otherAges: [] }
  const forGroup = []
  const otherAges = []
  rows.forEach(row => {
    const keys = rowAgeGroupKeys(row)
    if (keys.size === 0 || keys.has(ageGroup)) forGroup.push(row)
    else otherAges.push(row)
  })
  return { forGroup, otherAges }
}

/**
 * The rows for one age group: those naming it, plus those naming no group we can read.
 * When no row names a group, every row is returned: there is nothing to choose by.
 */
export function rowsForAgeGroup(rows = [], ageGroup) {
  return splitRowsForAgeGroup(rows, ageGroup).forGroup
}
