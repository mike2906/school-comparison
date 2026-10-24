/**
 * The options page's chance groups (docs/MY_CHILD_OPTIONS.md, Revision 3): every verdict
 * schoolVerdict.js can give a school is in exactly one group. Never a percentage: the
 * groups only say where the parent's points stand against the last admitted child.
 */
import { isClearlyBelow } from './schoolVerdict.js'

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

const rank = group => CHANCE_GROUPS.indexOf(group)

// "Below" only when nothing else could get the child in, as the list's "hide clearly
// below" counts it (isClearlyBelow); otherwise close.
const belowOrClose = verdict => (isClearlyBelow({ ...verdict, verdict: 'below' }) ? 'below' : 'close')

function groupOf(verdict) {
  if (verdict.verdict === 'mixed') {
    // Buildings or groups that disagree: their shared group when they all fall in one.
    const groups = new Set((verdict.lines || []).map(line => VERDICT_GROUP[line.verdict] ?? 'unknown'))
    if (groups.size !== 1) return 'close'
    const [group] = groups
    return group === 'below' ? belowOrClose(verdict) : group
  }
  const group = VERDICT_GROUP[verdict.verdict] ?? 'unknown'
  return group === 'below' ? belowOrClose(verdict) : group
}

/**
 * One school's group from its saved verdicts (savedVerdictsFor); without any (no saved
 * answers, a private school) it is 'unknown'. With several, the best one counts.
 */
export function chanceGroup(verdicts = []) {
  if (verdicts.length === 0) return 'unknown'
  return verdicts.map(groupOf).sort((a, b) => rank(a) - rank(b))[0]
}
