/**
 * City-agnostic helpers for admission rules. Nothing here knows a city's rules: the
 * evaluators in ./evaluators read their own parameters and use these for the arithmetic
 * every rule set shares. See docs/ADMISSION_POINTS.md.
 *
 * Unknown answers: every answer may be `null` (the parent chose "not sure"). Points are
 * then a range `{ min, max }` covering every possible answer, so the result can say
 * "8 to 12 points" instead of guessing.
 */

/** A points range. `range(3)` is exactly 3. */
export function range(min, max = min) {
  return { min, max }
}

export const ZERO = range(0)

export function addRanges(...ranges) {
  return ranges.reduce((acc, r) => range(acc.min + r.min, acc.max + r.max), ZERO)
}

export function isExact(r) {
  return r.min === r.max
}

/** A yes/no criterion worth `points`: yes → points, no → 0, unknown → 0 to points. */
export function flagPoints(answer, points) {
  if (answer === true) return range(points)
  if (answer === false) return ZERO
  return range(Math.min(0, points), Math.max(0, points))
}

/**
 * Only the best one of several criteria counts ("only one of sibling, twin or close
 * sibling"). Each part is a range; the result is the best of them.
 */
export function bestOf(parts) {
  if (parts.length === 0) return ZERO
  return range(Math.max(...parts.map(p => p.min)), Math.max(...parts.map(p => p.max)))
}

/** Clamp a range to an upper limit ("at most 4 points from this section"). */
export function capRange(r, cap) {
  return range(Math.min(r.min, cap), Math.min(r.max, cap))
}

function parseDay(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '')
  if (!match) return null
  return { y: Number(match[1]), m: Number(match[2]), d: Number(match[3]) }
}

/**
 * Whole years from `sinceIso` to `onIso` (both YYYY-MM-DD), the way a registration
 * duration is counted: 2023-03-19 to 2026-03-19 is 3 years, to 2026-03-18 is 2.
 * Returns null when either date is missing or the order is reversed.
 */
export function wholeYearsBetween(sinceIso, onIso) {
  const a = parseDay(sinceIso)
  const b = parseDay(onIso)
  if (!a || !b) return null
  let years = b.y - a.y
  if (b.m < a.m || (b.m === a.m && b.d < a.d)) years -= 1
  return years < 0 ? null : years
}

/** True when `sinceIso` is strictly more than `years` whole years before `onIso`. */
export function moreThanYears(sinceIso, onIso, years) {
  const a = parseDay(sinceIso)
  const b = parseDay(onIso)
  if (!a || !b) return null
  // The anniversary itself is exactly `years`; "more than" starts the day after.
  const anniversary = `${String(a.y + years).padStart(4, '0')}-${String(a.m).padStart(2, '0')}-${String(a.d).padStart(2, '0')}`
  return onIso > anniversary
}

/**
 * Points for a duration tier. `tiers` are ordered best first; each has `points` and a
 * test: `more_than_years` (strictly more), `at_least_years`, or neither (the floor).
 * `since` is a date or null (unknown), `on` the date the duration is measured at.
 */
export function durationTierPoints(tiers, since, on) {
  const all = tiers.map(t => t.points)
  if (!since) return range(Math.min(...all), Math.max(...all))
  for (const tier of tiers) {
    if (tier.more_than_years != null) {
      if (moreThanYears(since, on, tier.more_than_years)) return range(tier.points)
    } else if (tier.at_least_years != null) {
      const years = wholeYearsBetween(since, on)
      if (years != null && years >= tier.at_least_years) return range(tier.points)
    } else {
      return range(tier.points)
    }
  }
  return ZERO
}

/** Points for the order of a wish (1 = first choice); unknown → any rank's points. */
export function choiceRankPoints(table, rank) {
  if (rank == null) return range(Math.min(0, ...table), Math.max(0, ...table))
  return range(table[rank - 1] ?? 0)
}

/**
 * The rule set for a campaign, or the newest earlier one when that campaign has none
 * yet. Rules are never assumed to carry over: `confirmed` is false in that case and the
 * page says so.
 */
export function selectRuleSet(ruleSets, { country, city, stage, campaign }) {
  const matching = ruleSets
    .filter(r => r.country === country && (r.city ?? null) === (city ?? null) && r.stage === stage)
    .sort((a, b) => b.campaign.localeCompare(a.campaign))
  const exact = matching.find(r => r.campaign === campaign)
  if (exact) return { ruleSet: exact, confirmed: true }
  const earlier = matching.find(r => r.campaign < campaign)
  if (earlier) return { ruleSet: earlier, confirmed: false }
  return null
}

/** The revision in force on a date (the last one whose `in_force_from` has passed). */
export function revisionOn(ruleSet, onIso) {
  const revisions = [...(ruleSet.revisions || [])].sort((a, b) => a.in_force_from.localeCompare(b.in_force_from))
  let current = null
  for (const revision of revisions) {
    if (revision.in_force_from <= onIso) current = revision
  }
  return current
}

/** Suspensions or other status events covering a date. */
export function statusEventsOn(ruleSet, onIso) {
  return (ruleSet.status_events || []).filter(e => e.from <= onIso && (!e.to || onIso <= e.to))
}

/** The id a cut-off row stores to say which rules it was made under. */
export function ruleVersionKey(ruleSet, revision) {
  return revision ? `${ruleSet.id}@${revision.id}` : ruleSet.id
}

/** Campaign `2026-27` for a September start year of 2026. */
export function campaignForStartYear(year) {
  return `${year}-${String((year + 1) % 100).padStart(2, '0')}`
}

/** The September start year of a campaign (`2026-27` → 2026). */
export function startYearOfCampaign(campaign) {
  return Number(String(campaign).slice(0, 4))
}
