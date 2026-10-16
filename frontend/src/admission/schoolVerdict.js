/**
 * Saved calculator answers against one school's published cut-offs, for the search list
 * and the school page. Per-school answers the parent never gave are assumed, and the page
 * says so: a kindergarten is their first wish with no sibling there; a first-grade
 * school has no sibling and no preparatory group of theirs. Never a chance or a
 * percentage: only where the parent's points stand against the last admitted child.
 */
import { comparableRound, compareWithCutoff, rowFitsChild, ruleSetFor, sameRules, stageById, summaryRowsFor } from './calculator.js'
import { addressFactsForSchool, districtAt, districtByName, schoolCatchmentIds } from './catchments.js'
import { evaluateGrade1, evaluateKindergarten, KINDERGARTEN_GROUPS, kindergartenAgeGroup } from './inputs.js'
import { usesSofiaKindergartenSystem } from '../utils/admission.js'
import { parseCoordinate } from '../utils/coordinates.js'

// What the list assumes for answers that depend on the school.
export const KINDERGARTEN_ASSUMED = { siblingThere: false, wishRank: 1 }
export const GRADE1_ASSUMED = { siblingAtSchool: false, siblingUnderAgeAtSchool: false, preparatoryGroupHere: false }

// Search-page age groups each saved tab applies to (null: no age chosen, both apply).
export const SEARCH_GROUPS = { kindergarten: KINDERGARTEN_GROUPS, grade_1: ['grade_1_4'] }

function schoolPoint(school) {
  const locations = school?.locations || []
  const location = locations.find(l => l.is_primary && Number.isFinite(parseCoordinate(l.lat))) ||
    locations.find(l => Number.isFinite(parseCoordinate(l.lat)) && Number.isFinite(parseCoordinate(l.lng)))
  return location ? [parseCoordinate(location.lat), parseCoordinate(location.lng)] : null
}

/** A kindergarten's district feature: the one kg.sofia.bg gives, else the polygon its pin is in. */
export function kindergartenDistrictFeature(school, districts) {
  if (!districts) return null
  const point = schoolPoint(school)
  return districtByName(districts, school?.admission_info?.admin_district) ||
    (point ? districtAt(districts, point[0], point[1]) : null)
}

export function kindergartenDistrict(school, districts) {
  return kindergartenDistrictFeature(school, districts)?.properties.code ?? null
}

/**
 * One school's rows against a result: `{ verdict, lines }`, where verdict is one of
 * compareWithCutoff's answers, 'mixed' (buildings or groups disagree) or 'noCutoff'.
 */
export function compareRows(rows, yours, ruleSet) {
  // Only the newest campaign: older years ranked other children, often under other rules.
  const newest = rows.reduce((max, row) => (row.campaign && row.campaign > max ? row.campaign : max), '')
  const lines = rows
    .filter(row => !newest || !row.campaign || row.campaign === newest)
    .map(row => {
      const round = comparableRound(row)
      return { row, round, verdict: compareWithCutoff(yours, round), otherRules: sameRules(row.rule_set, ruleSet) === false }
    })
    .filter(line => line.verdict)
  if (lines.length === 0) return { verdict: 'noCutoff', lines }
  const verdicts = new Set(lines.map(line => line.verdict))
  return { verdict: verdicts.size === 1 ? lines[0].verdict : 'mixed', lines }
}

function kindergartenVerdict(school, entry, { ruleSets, districts, today }, searchGroup) {
  const ageGroup = kindergartenAgeGroup(entry.answers, entry.startYear)
  if (!ageGroup || !KINDERGARTEN_GROUPS.includes(ageGroup)) return null
  // A search for another group is about another child.
  if (searchGroup && searchGroup !== ageGroup) return null
  const summary = school.admission_info?.cutoff_summary || []
  // A school's preparatory group is ranked under the kindergarten rules.
  const applies = usesSofiaKindergartenSystem(school) ||
    (ageGroup === 'preschool' && summary.some(row => row.stage === 'preparatory'))
  if (!applies) return null
  const selected = ruleSetFor(ruleSets, stageById('kindergarten'), entry.startYear, today)
  if (!selected) return null
  const district = kindergartenDistrict(school, districts)
  const result = evaluateKindergarten(selected.ruleSet, entry.answers, entry.startYear, { district, ...KINDERGARTEN_ASSUMED }, today)
  if (!result) return null
  const rows = summaryRowsFor(summary, stageById('kindergarten'))
    .filter(row => rowFitsChild(row, ageGroup, entry.answers.birthYear, entry.startYear))
  const yours = { points: result.total }
  return {
    stage: 'kindergarten',
    yours,
    // Separate paths the cut-off (the general queue) says nothing about.
    socialPriority: result.socialPriority,
    specialPlaces: result.specialPlaces,
    ...compareRows(rows, yours, selected.ruleSet),
  }
}

function grade1Verdict(school, entry, { ruleSets, catchments, today }) {
  const ids = schoolCatchmentIds(school)
  if (ids.length === 0) return null
  const address = entry.addresses[0]
  // Only an address checked against the official table says which catchment it is in.
  if (!address?.check?.schoolId) return { stage: 'grade_1', verdict: 'needsAddress', lines: [] }
  if (!catchments) return null
  const selected = ruleSetFor(ruleSets, stageById('grade_1'), entry.startYear, today)
  if (!selected) return null
  const facts = ids.map(id => addressFactsForSchool(catchments, address.check.schoolId, id))
  const inCatchment = facts.some(f => f.inCatchment)
  const result = evaluateGrade1(
    selected.ruleSet,
    entry.answers,
    { inCatchment, inNeighbouring: !inCatchment && facts.some(f => f.inNeighbouring), since: address.since },
    GRADE1_ASSUMED
  )
  const rows = summaryRowsFor(school.admission_info?.cutoff_summary, stageById('grade_1'))
  return { stage: 'grade_1', yours: result, ...compareRows(rows, result, selected.ruleSet) }
}

/**
 * The saved results for one school, one per saved tab that applies to it (a school with a
 * preparatory group and first grade can have both). `ageGroup` is the search page's age
 * filter: a saved tab shows only for the age groups it covers.
 * `data`: { ruleSets, districts, catchments, today }.
 */
export function savedVerdictsFor(school, saved, ageGroup, data) {
  return ['kindergarten', 'grade_1'].flatMap(stage => {
    const entry = saved?.[stage]
    if (!entry || (ageGroup && !SEARCH_GROUPS[stage].includes(ageGroup))) return []
    const verdict = stage === 'kindergarten' ? kindergartenVerdict(school, entry, data, ageGroup) : grade1Verdict(school, entry, data)
    return verdict ? [verdict] : []
  })
}

/**
 * Ids of the schools where every saved result is below the last admitted child. Schools
 * without a cut-off, with a tie, mixed rounds, a cut-off ranked under other rules or a
 * possible priority path stay listed: only a clear "below" counts.
 */
export function clearlyBelowIds(schools, saved, ageGroup, data) {
  const below = verdict => verdict.verdict === 'below' && !verdict.lines.some(line => line.otherRules) &&
    // Social priority and SEN places are separate ways in that the cut-off says nothing about.
    !(verdict.stage === 'kindergarten' && verdict.socialPriority !== false) && !verdict.specialPlaces
  const ids = new Set()
  for (const school of schools) {
    const verdicts = savedVerdictsFor(school, saved, ageGroup, data)
    if (verdicts.length > 0 && verdicts.every(below)) ids.add(school.id)
  }
  return ids
}

/**
 * The search filters that show a saved tab's schools: state schools, its intake year and
 * age group (and the child's birth year for kindergartens). Null when the answers do not
 * give an age group.
 */
export function savedListFilters(stage, entry) {
  if (!entry) return null
  if (stage === 'grade_1') return { schoolType: 'state', targetYear: entry.startYear, ageGroup: 'grade_1_4', birthYear: null }
  const ageGroup = kindergartenAgeGroup(entry.answers, entry.startYear)
  if (!ageGroup || !KINDERGARTEN_GROUPS.includes(ageGroup)) return null
  return { schoolType: 'state', targetYear: entry.startYear, ageGroup, birthYear: entry.answers.birthYear }
}

/** The search URL for savedListFilters. */
export function savedListUrl(filters) {
  return `/search?${new URLSearchParams({
    school_type: filters.schoolType,
    target_year: String(filters.targetYear),
    age_group: filters.ageGroup,
    ...(filters.birthYear ? { birth_year: String(filters.birthYear) } : {}),
  })}`
}

/** Whether a saved tab applies to a search for `ageGroup` (null: any). */
export function savedStageApplies(stage, entry, ageGroup) {
  if (!entry) return false
  if (!ageGroup) return true
  if (stage === 'kindergarten') return ageGroup === kindergartenAgeGroup(entry.answers, entry.startYear)
  return SEARCH_GROUPS[stage].includes(ageGroup)
}
