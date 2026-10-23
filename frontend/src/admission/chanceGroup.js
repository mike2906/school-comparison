/**
 * The options page's chance groups (docs/MY_CHILD_OPTIONS.md, Revision 3): every verdict
 * schoolVerdict.js can give a school is in exactly one group. Never a percentage: the
 * groups only say where the parent's points stand against the last admitted child.
 */

export const CHANCE_GROUPS = ['likely', 'close', 'unknown', 'below']

export const VERDICT_GROUP = {
  above: 'likely',
  everyone: 'likely',
  tie: 'close',
  unsure: 'close',
  sameGroup: 'close',
  mixed: 'close',
  noCutoff: 'unknown',
  needsAddress: 'unknown',
  below: 'below',
  noPlaces: 'below',
}

// "Below" only when nothing else could get the child in, as the list's "hide clearly
// below" counts it (clearlyBelowIds): a cut-off under other rules, a social priority or
// a special-needs place keeps the school close.
function clearlyBelow(verdict) {
  if (verdict.lines?.some(line => line.otherRules)) return false
  if (verdict.stage === 'kindergarten' && verdict.socialPriority !== false) return false
  return !verdict.specialPlaces
}

function groupOf(verdict) {
  const group = VERDICT_GROUP[verdict.verdict] ?? 'unknown'
  return group === 'below' && !clearlyBelow(verdict) ? 'close' : group
}

/**
 * One school's group from its saved verdicts (savedVerdictsFor); without any (no saved
 * answers, a private school) it is 'unknown'. With several, the best one counts.
 */
export function chanceGroup(verdicts = []) {
  if (verdicts.length === 0) return 'unknown'
  return verdicts.map(groupOf).sort((a, b) => CHANCE_GROUPS.indexOf(a) - CHANCE_GROUPS.indexOf(b))[0]
}
