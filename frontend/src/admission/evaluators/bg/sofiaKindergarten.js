/**
 * Sofia municipal nurseries, kindergartens and preparatory groups (Наредба за прием на
 * деца в общинските самостоятелни детски ясли, детски градини и в подготвителните групи
 * в общинските училища, чл. 14, 18 and 19). Reads the parameters of a rule file under
 * rules/bg/sofia/kindergarten/.
 *
 * Every criterion of чл. 18 adds to one total, together with the wish-order points of
 * чл. 19 (the published lists show one total). Priority and capacity are reported
 * separately, never folded into the total: social criteria give priority in the first
 * ranking for up to 30% of the places (чл. 14 ал. 5), and SEN / chronic illness have
 * per-group limits (чл. 14 ал. 6 and 9). Ties are broken by a random number (чл. 20).
 *
 * A range's maximum assumes every unknown answer is favourable, so it can be higher than
 * any real family reaches; the page says "up to".
 *
 * Points that depend on the kindergarten (district, sibling there, wish order) are in
 * `context`; everything else comes from the family's `answers`.
 */
import {
  addRanges,
  bestOf,
  choiceRankPoints,
  durationTierPoints,
  flagPoints,
  range,
  ZERO,
} from '../../core.js'

/** The point values of a parameter table, skipping labels such as `article`. */
function pointValues(table) {
  return Object.values(table).filter(value => typeof value === 'number')
}

function parentActivityPoints(params, parents) {
  const table = params.parent_activity
  const best = Math.max(...pointValues(table))
  // No parents given yet: one or two parents, each possibly earning the points.
  if (!parents || parents.length === 0) return range(0, 2 * best)
  return addRanges(
    ...(parents || []).map(parent => {
      const activity = parent?.activity
      if (activity == null) return range(0, best)
      return range(table[activity] ?? 0)
    })
  )
}

/** Address in Sofia, by how long it has not changed (т. 1). */
function addressDurationPoints(params, answers, measuredOn) {
  const rule = params.address_duration
  if (answers.inSofia === false) return ZERO
  const usesParentAddress = rule.parent_address_age_groups.includes(answers.ageGroup)
  // Nursery and first group: one parent's address, which must also be the child's.
  if (usesParentAddress && answers.parentChildSameAddress === false) return ZERO
  const tiers = durationTierPoints(rule.tiers, answers.addressSince, measuredOn)
  const certain = answers.inSofia === true && (!usesParentAddress || answers.parentChildSameAddress === true)
  return certain ? tiers : range(0, tiers.max)
}

/** Address in the kindergarten's district (т. 2). */
function sameDistrictPoints(params, answers, context) {
  const points = params.same_district.points
  if (answers.inSofia === false) return ZERO
  const known = answers.homeDistrict != null && context.district != null
  if (known && answers.homeDistrict !== context.district) return ZERO
  if (known && answers.inSofia === true) return range(points)
  return range(0, points)
}

function nurseryPoints(params, answers, campaign) {
  const rule = params.nursery_attendance
  if (!rule.age_groups.includes(answers.ageGroup)) return ZERO
  if (rule.last_campaign && campaign > rule.last_campaign) return ZERO
  return flagPoints(answers.nurseryAttended, rule.points)
}

function siblingPoints(params, answers, context) {
  const rule = params.siblings
  return bestOf([
    flagPoints(context.siblingThere, rule.same_institution),
    flagPoints(answers.twins, rule.twins),
    flagPoints(answers.closeInAge, rule.close_in_age),
  ])
}

function socialItems(params, answers) {
  const rule = params.social
  const social = answers.social || {}
  const deceased = social.parentsDeceased
  const deceasedPoints = deceased == null
    ? range(0, Math.max(...pointValues(rule.parents_deceased)))
    : range(rule.parents_deceased[deceased] ?? 0)
  return [
    { id: 'parents_deceased', points: deceasedPoints },
    ...['family_disability_70', 'unknown_or_restricted_parent', 'foster_or_adopted', 'child_at_risk', 'three_plus_children']
      .map(id => ({ id, points: flagPoints(social[id], rule[id]) })),
  ]
}

function additionalPoints(params, answers) {
  const table = params.additional
  if (answers.additional == null) return range(0, Math.max(...pointValues(table)))
  return range(table[answers.additional] ?? 0)
}

function wishPoints(params, answers, context) {
  const base = choiceRankPoints(params.wish_order, context.wishRank)
  if (answers.transfer === false || base.max === 0) return base
  const penalty = params.transfer_wish_penalty
  const reduced = range(Math.max(0, base.min - penalty), Math.max(0, base.max - penalty))
  if (answers.transfer === true) return reduced
  return range(reduced.min, base.max)
}

/**
 * @param {object} params   the rule file's params
 * @param {object} answers  the family's answers (see docs/ADMISSION_POINTS.md)
 * @param {object} context  this kindergarten: { district, siblingThere, wishRank }
 * @param {object} options  { campaign, measuredOn } — the ranking date the address
 *                          duration is counted to
 */
export function evaluate(params, answers = {}, context = {}, { campaign, measuredOn } = {}) {
  // Both decide points (address duration, the nursery point's end), so a caller that
  // forgets them must fail rather than quietly award or widen points.
  if (!campaign || !measuredOn) throw new Error('evaluate needs options.campaign and options.measuredOn')
  const social = socialItems(params, answers)
  const items = [
    { id: 'address_duration', group: 'general', points: addressDurationPoints(params, answers, measuredOn) },
    { id: 'same_district', group: 'general', points: sameDistrictPoints(params, answers, context) },
    { id: 'parent_activity', group: 'general', points: parentActivityPoints(params, answers.parents) },
    { id: 'nursery_attendance', group: 'general', points: nurseryPoints(params, answers, campaign) },
    { id: 'siblings', group: 'general', points: siblingPoints(params, answers, context) },
    ...social.map(item => ({ ...item, group: 'social' })),
    { id: 'additional', group: 'additional', points: additionalPoints(params, answers) },
    { id: 'wish_order', group: 'wish', points: wishPoints(params, answers, context) },
  ]
  const socialTotal = addRanges(...social.map(item => item.points))
  return {
    total: addRanges(...items.map(item => item.points)),
    items,
    // Not a score: social criteria also give priority in the first ranking for the new
    // school year, for at most 30% of the announced places (чл. 14 ал. 5).
    socialPriority: socialTotal.min > 0 ? true : socialTotal.max > 0 ? null : false,
    // Not a score either: SEN has 2 announced places per group in the first ranking, plus
    // a possible third by referral (чл. 14 ал. 6); chronic illness is capped at 3 children
    // per group (чл. 14 ал. 9).
    specialPlaces: answers.additional && answers.additional !== 'none' ? answers.additional : null,
  }
}
