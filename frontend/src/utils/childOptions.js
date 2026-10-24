/**
 * The options page's list (docs/MY_CHILD_OPTIONS.md): schools grouped by chance, the
 * shortest trip first inside a group, and the budget as a filter that says what it hid.
 * Pure, so it runs under `node --test`.
 */
import { CHANCE_GROUPS, chanceGroup } from '../admission/chanceGroup.js'
import { KINDERGARTEN_GROUPS } from '../admission/inputs.js'
import { tuitionSortValue } from './pricing.js'

/**
 * The calculator stage for a child's start, or null when there is none: kindergarten
 * groups, and first grade only in the year the child starts it (the year it turns seven).
 */
export function calculatorStage(ageGroup, birthYear, startYear) {
  if (KINDERGARTEN_GROUPS.includes(ageGroup)) return 'kindergarten'
  if (ageGroup === 'grade_1_4' && startYear - birthYear === 7) return 'grade_1'
  return null
}

/**
 * Whether a saved calculator entry is about this child and start: one for another child
 * (another birth year) or another September is never applied.
 */
export function entryFits(stage, entry, birthYear, startYear) {
  if (!entry || entry.startYear !== startYear) return false
  return stage === 'grade_1' || entry.answers?.birthYear === birthYear
}

/** A private school's tuition per month in euro, or null when its fee has no monthly figure. */
export function monthlyTuition(school, ageGroup) {
  return school.school_type === 'state' ? null : tuitionSortValue(school.pricing, { ageGroup })
}

// The chosen way to travel: by public transport (`tripSeconds` set, null when no trip arrives
// in time), how long before the start of the day the child leaves home; else the drive.
const tripTime = school => (school.tripSeconds !== undefined ? school.tripSeconds : school.driveSeconds) ?? Infinity
const tripOrder = (a, b) => (
  tripTime(a) - tripTime(b) ||
  (a.distance ?? Infinity) - (b.distance ?? Infinity)
)

/**
 * `schools` carry `distance` and `driveSeconds` (or `tripSeconds` by public transport) when a home is set. `verdictsFor(school)`
 * gives its saved verdicts. Returns `{ groups: { likely, close, unknown, below }, overBudget }`,
 * where overBudget counts the private schools whose monthly tuition is above the budget
 * (a fee with no monthly figure is never hidden).
 */
export function buildOptions(schools, { verdictsFor, monthlyBudget = null, ageGroup = null, showOverBudget = false }) {
  const groups = Object.fromEntries(CHANCE_GROUPS.map(group => [group, []]))
  let overBudget = 0
  for (const school of schools) {
    const fee = monthlyTuition(school, ageGroup)
    if (monthlyBudget && fee != null && fee > monthlyBudget) {
      overBudget += 1
      if (!showOverBudget) continue
    }
    groups[chanceGroup(verdictsFor(school))].push(school)
  }
  CHANCE_GROUPS.forEach(group => groups[group].sort(tripOrder))
  return { groups, overBudget }
}
