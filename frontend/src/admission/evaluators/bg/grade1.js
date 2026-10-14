/**
 * First grade in Bulgarian municipal schools. The national template (Наредба № 10/2016,
 * чл. 43) is the same in every city: children are placed in four groups by where they
 * live and for how long, groups are filled in order, and only inside an oversubscribed
 * group do the municipality's extra points decide. Each city's rule file sets the
 * durations and the extra points; this evaluator reads them.
 *
 * The answer is a group (1 is best) plus extra points, not a single number. The group
 * depends on the address and the school, so it is worked out per school and per address
 * the parent could apply with.
 */
import { addRanges, bestOf, durationTiers, flagPoints, range, ZERO } from '../../core.js'

/**
 * The group for one address at one school.
 * `address`: { inCatchment: bool|null, since: date|null } where `since` is when the
 * child's address entered this school's catchment (a move inside the same catchment
 * does not reset it).
 */
function groupFor(params, address, context, measuredOn) {
  const rule = params.groups
  if (context.siblingUnderAgeAtSchool === true) return range(rule.sibling_group)
  const outside = rule.outside_group
  const matched = durationTiers(rule.tiers, address?.since, measuredOn)
  // A rule file without a floor tier leaves short durations unmatched: the worst inside group.
  const inside = (matched.length ? matched : rule.tiers.slice(-1)).map(tier => tier.group)
  let groups
  if (address?.inCatchment === true) groups = inside
  else if (address?.inCatchment === false) groups = [outside]
  else groups = [...inside, outside]
  // An unknown sibling answer could still lift the child into the sibling group.
  if (context.siblingUnderAgeAtSchool == null) groups = [...groups, rule.sibling_group]
  return range(Math.min(...groups), Math.max(...groups))
}

function extraItems(params, answers, address, context, group) {
  const points = params.extra_points
  // The neighbouring bonus counts only in group 4; while the group is uncertain it may
  // or may not apply.
  const onlyIn = params.neighbouring_only_in_group
  let neighbouring = ZERO
  if (group.max >= onlyIn && address?.inCatchment !== true) {
    neighbouring = flagPoints(address?.inNeighbouring, points.neighbouring_catchment)
    if (group.min < onlyIn) neighbouring = range(0, neighbouring.max)
  }
  return [
    { id: 'disability_50', points: flagPoints(answers.disability50, points.disability_50) },
    { id: 'parent_deceased', points: flagPoints(answers.parentDeceased, points.parent_deceased) },
    { id: 'sibling_at_school', points: flagPoints(context.siblingAtSchool, points.sibling_at_school) },
    { id: 'preparatory_group_here', points: flagPoints(context.preparatoryGroupHere, points.preparatory_group_here) },
    {
      id: 'large_family_or_twins',
      points: bestOf([
        flagPoints(answers.moreThanTwoChildren, points.more_than_two_children),
        flagPoints(answers.twins, points.twins),
      ]),
    },
    { id: 'foster_unknown_adopted', points: flagPoints(answers.fosterUnknownAdopted, points.foster_unknown_adopted) },
    { id: 'neighbouring_catchment', points: neighbouring },
  ]
}

/**
 * @param {object} params   the rule file's params
 * @param {object} answers  { disability50, parentDeceased, moreThanTwoChildren, twins,
 *                          fosterUnknownAdopted, address: { inCatchment, inNeighbouring, since } }
 * @param {object} context  this school: { siblingUnderAgeAtSchool, siblingAtSchool,
 *                          preparatoryGroupHere }
 * @param {object} options  { measuredOn } — the application date
 */
export function evaluate(params, answers = {}, context = {}, { measuredOn } = {}) {
  if (!measuredOn) throw new Error('evaluate needs options.measuredOn (the application date)')
  const address = answers.address || {}
  const group = groupFor(params, address, context, measuredOn)
  const items = extraItems(params, answers, address, context, group)
  return { group, points: addRanges(...items.map(item => item.points)), items }
}
