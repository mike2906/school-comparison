import { gradesNamedIn, rowGradeBand } from './gradeBand.js'
import { priceCategory, pricePeriod, rowAgeGroupKeys } from './priceRows.js'
import { isOptionalFee, requiredExtras, selectPricingCohort, toEur, tuitionRows, isStatedTotal } from './pricing.js'

/**
 * What one year at a school costs for one child, line by line, from the fees the school
 * publishes (docs/YEARLY_COST.md). Pure: the compare page renders it.
 *
 * Nothing is guessed. A monthly fee is never turned into a year (the billed months are not
 * published), a missing line is "not stated", never a typical value, and a total is only
 * "at least" while something it needs cannot be added.
 */

// The stages a parent can pick: kindergarten groups, then the preschool year and grades 1-12.
export const COST_STAGES = [
  'nursery', 'first', 'second', 'third', 'preschool',
  ...Array.from({ length: 12 }, (_, index) => `grade_${index + 1}`),
]

// The needs a parent can add to the total; the rest of the table is always shown.
export const COST_NEEDS = ['food', 'extended_day', 'transport']

const KINDERGARTEN_AGES = { 3: 'first', 4: 'second', 5: 'third', 6: 'preschool' }

/** The stage a child born in `birthYear` is in the September of `startYear`, or null. */
export function stageForChild(birthYear, startYear) {
  if (!Number.isInteger(birthYear) || !Number.isInteger(startYear)) return null
  const age = startYear - birthYear
  if (age < 3) return age >= 0 ? 'nursery' : null
  if (KINDERGARTEN_AGES[age]) return KINDERGARTEN_AGES[age]
  return age <= 18 ? `grade_${Math.min(age - 6, 12)}` : null
}

const BAND_STAGES = { grade_1_4: 'grade_1', grade_5_7: 'grade_5', grade_8_12: 'grade_8' }

/** The stage the search's age group (`age_group`) starts at: grades 1-4 is grade 1. */
export function stageForAgeGroup(ageGroup) {
  if (BAND_STAGES[ageGroup]) return BAND_STAGES[ageGroup]
  return COST_STAGES.includes(ageGroup) ? ageGroup : null
}

/**
 * The stage a cost table starts at: the saved child's when it fits the searched age group (or
 * there is none), else the searched group's first year, else grade 1. A start year already
 * past plans for the coming year.
 */
export function defaultStage({ birthYear, startYear, ageGroup, planningYear }) {
  const child = stageForChild(birthYear, Math.max(startYear ?? planningYear, planningYear))
  const searched = stageForAgeGroup(ageGroup)
  if (child && (!searched || stageAgeGroup(child) === stageAgeGroup(searched))) return child
  return searched || child || 'grade_1'
}

const GRADE_GROUPS = new Set(['grade_1_4', 'grade_5_7', 'grade_8_12'])

/**
 * False when the school's buildings list their age groups and none is the stage's kind: a
 * kindergarten group at a school with no kindergarten, a grade at a kindergarten. Sites list
 * fees for more grades than the register (596), so only the kind is checked, not the grade.
 */
export function offersStage(school, stage) {
  const groups = (school?.locations || []).flatMap(location => location.age_groups || [])
  if (groups.length === 0 || stage === 'preschool') return true
  return KINDERGARTEN_STAGES.has(stage)
    ? groups.some(group => KINDERGARTEN_STAGES.has(group))
    : groups.some(group => GRADE_GROUPS.has(group))
}

// The preschool year is grade 0, as in gradeBand.js.
function stageGrade(stage) {
  if (stage === 'preschool') return 0
  const match = /^grade_(\d+)$/.exec(stage || '')
  return match ? Number(match[1]) : null
}

// The age-group key (src/utils/priceRows.js) a stage falls under.
function stageAgeGroup(stage) {
  const grade = stageGrade(stage)
  if (grade == null || grade === 0) return stage
  if (grade <= 4) return 'grade_1_4'
  return grade <= 7 ? 'grade_5_7' : 'grade_8_12'
}

const KINDERGARTEN_STAGES = new Set(['nursery', 'first', 'second', 'third'])
// At a word's start: "предучилищна" and "preschool" are a kindergarten's words.
const SCHOOL_GROUP_RE = /(?<![\p{L}])(?:училищ|ученици|учениците|school|pupils|students)/iu

// 'named' for a row that names the stage, 'any' for one naming no group we can read (it may
// well apply), false for one naming only other groups.
function stageMatch(row, stage) {
  // "Училище", "Ученици в Лозен": a school's fee, not a kindergarten group's (635).
  if (KINDERGARTEN_STAGES.has(stage) && SCHOOL_GROUP_RE.test(row.age_group || '')) return false
  const keys = rowAgeGroupKeys(row)
  if (keys.size === 0) return 'any'
  const grade = stageGrade(stage)
  if (grade == null) return keys.has(stage) ? 'named' : false
  const band = rowGradeBand(row) || gradesNamedIn(row.age_group) || gradesNamedIn(row.plan_name)
  if (grade === 0) return keys.has('preschool') || band?.from === 0 ? 'named' : false
  if (band) return band.from <= grade && grade <= band.to ? 'named' : false
  return keys.has(stageAgeGroup(stage)) ? 'named' : false
}

/**
 * A school's fee rows for one stage: those naming it and those naming no group, except a row
 * naming no group from a fee page whose tuition is only for other groups: 596's secondary-school
 * PDF lists a 200 € application fee that grade 1 does not pay.
 */
export function rowsForStage(rows = [], stage) {
  const otherStagesOnly = new Set()
  const pages = new Map()
  rows.filter(row => row.category === 'tuition' && row.source_url).forEach((row) => {
    pages.set(row.source_url, (pages.get(row.source_url) || false) || Boolean(stageMatch(row, stage)))
  })
  pages.forEach((matches, url) => { if (!matches) otherStagesOnly.add(url) })
  return rows.filter((row) => {
    const match = stageMatch(row, stage)
    return match === 'named' || (match === 'any' && !otherStagesOnly.has(row.source_url))
  })
}

// A fee with no stated period counts once a year only on a page whose tuition is per year,
// and is marked so (`assumed`), unless its notes say per school year (635's bus).
const PER_YEAR_NOTE_RE = /учебна година|за година|годишн|per (?:school )?year|a year|annual|school year/iu
// "150 € per month during the school year" is not a yearly price.
const OTHER_PERIOD_NOTE_RE = /месе[цч]|седмиц|на ден|дневн|month|week|per day|daily|twice|два пъти/iu

function amounts(row) {
  const low = toEur(row.amount_min ?? row.amount ?? row.amount_max, row.currency)
  const high = toEur(row.amount_max ?? row.amount ?? row.amount_min, row.currency)
  return low == null || high == null ? null : { min: low, max: high }
}

// One fee as a line option: its euro amounts and whether a year's figure can be taken from it.
function option(row, { yearlyPage, required = false }) {
  const period = pricePeriod(row)
  const value = amounts(row)
  const notes = [row.pricing_context?.notes, row.plan_name, row.pricing_context?.plan_name_en].filter(Boolean).join(' / ')
  let perYear = null
  if (value && (period === 'yearly' || (period == null && PER_YEAR_NOTE_RE.test(notes) && !OTHER_PERIOD_NOTE_RE.test(notes)))) {
    perYear = { ...value, assumed: false }
  } else if (value && period == null && yearlyPage) {
    perYear = { ...value, assumed: true }
  }
  return { row, period, value, perYear, required }
}

// The same fee read twice (596's food from both of its fee PDFs): a row whose amount lies inside
// another row's stated range, or repeats another row's amount, with the same name and period,
// gives way to it.
function withoutRepeats(options) {
  const name = item => (item.row.plan_name || '').trim().toLocaleLowerCase()
  return options.filter((item, index) => !options.some((other, otherIndex) => {
    if (otherIndex === index || !item.value || !other.value) return false
    if (name(other) !== name(item) || other.period !== item.period) return false
    const inside = other.value.min < other.value.max && item.value.min === item.value.max &&
      other.value.min <= item.value.min && item.value.max <= other.value.max
    const same = otherIndex < index && other.value.min === item.value.min && other.value.max === item.value.max
    return inside || same
  }))
}

// "в две посоки", "Two-way service": with a one-way price beside it, the bus a parent asks about.
const TWO_WAY_RE = /две посоки|двупосоч|двете посоки|two[- ]way|both ways|round[- ]trip/iu

function preferTwoWay(options) {
  const words = item => [item.row.plan_name, item.row.pricing_context?.plan_name_en].filter(Boolean).join(' ')
  const twoWay = options.filter(item => TWO_WAY_RE.test(words(item)))
  return twoWay.length > 0 ? twoWay : options
}

// What the tuition's own words say about a line: 635's "В таксата не е включена храна", 172's
// "The annual tuition fee includes food". Read from the tuition rows' includes, excludes and notes.
// At a word's start, so "syllabus" is not a bus.
const LINE_WORDS = {
  food: /(?<![\p{L}])(?:храна|хранене|обяд|закуск|food|meals?|lunch|snacks?)/iu,
  materials: /(?<![\p{L}])(?:учебни(?:к|ци)|учебни материали|помагал|тетрадк|textbooks?|books|(?:learning |teaching |educational |school )?materials)/iu,
  transport: /(?<![\p{L}])(?:транспорт|автобус|transport|bus)(?![\p{L}])/iu,
  extended_day: /(?<![\p{L}])(?:занимал|удължен|extended day|after[- ]school)/iu,
}
const NOT_INCLUDED_RE = /не (?:е|са|се) включен|не включва|без (?:храна|транспорт)|not included|excluded|excludes|does not include|doesn't include/iu
const INCLUDED_RE = /включ|includ|covers|покрива/iu

function rowInclusion(row, line) {
  const words = LINE_WORDS[line]
  const context = row.pricing_context || {}
  let state = null
  if ((context.excludes || []).some(text => words.test(text))) state = 'excluded'
  // By clause: "включва учебни материали, но не включва храна" includes one and not the other.
  String(context.notes || '').split(/(?<=[.;!?])\s+|,\s*|\s(?:но|а|but|while)\s/u).forEach((sentence) => {
    if (!words.test(sentence)) return
    if (NOT_INCLUDED_RE.test(sentence)) state = state || 'excluded'
    else if (INCLUDED_RE.test(sentence)) state = 'included'
  })
  if ((context.includes || []).some(text => words.test(text))) state = 'included'
  return state
}

// Included only when every tuition row says so: one band's note is not another band's.
function inclusion(tuition, line) {
  const states = tuition.map(row => rowInclusion(row, line))
  if (states.length > 0 && states.every(state => state === 'included')) return 'included'
  return states.includes('excluded') ? 'excluded' : null
}

function sum(parts) {
  return parts.reduce((total, part) => ({
    min: total.min + part.min,
    max: total.max + part.max,
    assumed: total.assumed || Boolean(part.assumed),
  }), { min: 0, max: 0, assumed: false })
}

// A line's options as one yearly figure: one option, or none when it is included. Several
// options (lunch at one campus or another) are the parent's choice, so no figure.
function lineValue(line) {
  if (line.state === 'included') return { value: { min: 0, max: 0, assumed: false } }
  // Required options are in the required total already.
  const options = line.options.filter(item => !item.required)
  if (options.length === 0) return line.options.length > 0 ? {} : { missing: line.state === 'excluded' ? 'excluded' : 'notStated' }
  if (options.length > 1) return { missing: 'severalOptions' }
  const [only] = options
  return only.perYear ? { value: only.perYear } : { missing: only.value ? 'periodNotYearly' : 'noPrice' }
}

const RECURRING_LINES = ['materials', 'food', 'extended_day', 'transport']
const ONE_OFF_CATEGORIES = new Set(['registration', 'deposit', 'uniforms'])

/**
 * One year at a school for `stage` (a COST_STAGES key). Returns null without fees. Shape:
 * `{ academicYear, yearStatus, tuition: { options, perYear, statedTotal },
 *    lines: { materials, required, food, extended_day, transport }: { state, options, required },
 *    firstYear: { options, deposits, uniforms, value }, optional: [option],
 *    required: { value, missing: [{ line, reason }] } }`
 * where `state` is included | excluded | priced | none and each option is
 * `{ row, period, value: {min, max}, perYear: {min, max, assumed} | null, required }`.
 */
export function yearCost(pricing = [], stage, { today = new Date() } = {}) {
  const ageGroup = stageAgeGroup(stage)
  const cohort = selectPricingCohort(pricing, { ageGroup, today })
  if (!cohort) return null
  const rows = rowsForStage(cohort.rows, stage)

  const tuitionFees = tuitionRows(rows, ageGroup)
  const yearlyTuition = tuitionFees.filter(row => pricePeriod(row) === 'yearly')
  const shownTuition = yearlyTuition.length > 0 ? yearlyTuition : tuitionFees
  const yearlyPage = yearlyTuition.length > 0
  // A price on request, or in a currency with no euro rate, has nothing to show here.
  const tuitionOptions = shownTuition.map(row => option(row, { yearlyPage })).filter(item => item.value)
  const tuitionValues = tuitionOptions.map(item => item.perYear).filter(Boolean)
  const tuition = {
    options: tuitionOptions,
    perYear: yearlyPage && tuitionValues.length > 0
      ? { min: Math.min(...tuitionValues.map(v => v.min)), max: Math.max(...tuitionValues.map(v => v.max)), assumed: false }
      : null,
    // A printed total of several fees may already hold the other lines (633).
    statedTotal: shownTuition.length > 0 && shownTuition.every(isStatedTotal),
  }

  const requiredIds = new Set(requiredExtras(rows, ageGroup).map(row => row.id))
  const extras = rows.filter(row => row.category !== 'tuition' && !ONE_OFF_CATEGORIES.has(priceCategory(row)) && amounts(row))
  const isRequired = row => requiredIds.has(row.id) || (row.category === 'materials' && !isOptionalFee(row))
  const byLine = line => extras.filter(row => row.category === line)

  const lines = {}
  RECURRING_LINES.forEach((line) => {
    let options = withoutRepeats(byLine(line).map(row => option(row, { yearlyPage, required: isRequired(row) })))
    if (line === 'transport') options = preferTwoWay(options)
    const included = inclusion(shownTuition, line)
    lines[line] = {
      state: included === 'included' ? 'included' : (options.length > 0 ? 'priced' : (included || 'none')),
      options: included === 'included' ? [] : options,
      required: options.length > 0 && options.every(item => item.required),
    }
  })
  // Other fees every child pays: 635's compulsory club.
  const otherRequired = extras.filter(row => !RECURRING_LINES.includes(row.category) && isRequired(row))
  lines.required = {
    state: otherRequired.length > 0 ? 'priced' : 'none',
    options: otherRequired.map(row => option(row, { yearlyPage, required: true })),
    required: true,
  }
  const optional = extras
    .filter(row => !RECURRING_LINES.includes(row.category) && !isRequired(row))
    .map(row => option(row, { yearlyPage }))

  const oneOffs = rows.filter(row => ONE_OFF_CATEGORIES.has(priceCategory(row)) && amounts(row))
  const firstYearOptions = withoutRepeats(oneOffs.filter(row => priceCategory(row) === 'registration').map(row => option(row, { yearlyPage })))
  const firstYear = {
    options: firstYearOptions,
    deposits: oneOffs.filter(row => priceCategory(row) === 'deposit').map(row => option(row, { yearlyPage })),
    uniforms: oneOffs.filter(row => priceCategory(row) === 'uniforms').map(row => option(row, { yearlyPage })),
    value: firstYearOptions.length > 0 ? sum(firstYearOptions.map(item => item.value)) : null,
  }

  return {
    academicYear: cohort.academicYear,
    yearStatus: cohort.yearStatus,
    tuition,
    lines,
    firstYear,
    optional,
    required: requiredTotal(tuition, lines),
  }
}

// Tuition plus every fee the school says each child pays. Null without a yearly tuition.
function requiredTotal(tuition, lines) {
  if (!tuition.perYear) return null
  if (tuition.statedTotal) return { value: tuition.perYear, missing: [] }
  const parts = [tuition.perYear]
  const missing = []
  ;[...RECURRING_LINES, 'required'].forEach((line) => {
    lines[line].options.filter(item => item.required).forEach((item) => {
      if (item.perYear) parts.push(item.perYear)
      else missing.push({ line, reason: item.value ? 'periodNotYearly' : 'noPrice' })
    })
  })
  return { value: sum(parts), missing }
}

/**
 * The required total plus the lines the parent needs (`needs`, COST_NEEDS keys) that are not
 * already in it. Null without a required total. `missing` names what could not be added.
 */
export function totalWithNeeds(cost, needs = []) {
  if (!cost?.required) return null
  if (cost.tuition.statedTotal) return { ...cost.required, missing: needs.map(line => ({ line, reason: 'inStatedTotal' })) }
  const parts = [cost.required.value]
  const missing = [...cost.required.missing]
  needs.forEach((line) => {
    const entry = cost.lines[line]
    if (!entry) return
    const { value, missing: reason } = lineValue(entry)
    if (value) parts.push(value)
    else if (reason) missing.push({ line, reason })
  })
  return { value: sum(parts), missing }
}
