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

/** A real calendar day as a UTC timestamp, or null (rejects 2026-02-30). */
function parseDay(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '')
  if (!match) return null
  const [y, m, d] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const time = Date.UTC(y, m - 1, d)
  const date = new Date(time)
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== m - 1 || date.getUTCDate() !== d) return null
  return { y, m, d, time }
}

/**
 * The day `years` after `since`. A 29 February start reaches its anniversary on
 * 1 March in a common year (Date.UTC rolls the day over).
 */
function anniversary(since, years) {
  return Date.UTC(since.y + years, since.m - 1, since.d)
}

/** True when both are real days and `sinceIso` is not after `onIso`. */
export function isValidSpan(sinceIso, onIso) {
  const a = parseDay(sinceIso)
  const b = parseDay(onIso)
  return Boolean(a && b && a.time <= b.time)
}

/**
 * Whole years from `sinceIso` to `onIso` (both YYYY-MM-DD), the way a registration
 * duration is counted: 2023-03-19 to 2026-03-19 is 3 years, to 2026-03-18 is 2.
 * Returns null for an invalid date or a start after the end.
 */
export function wholeYearsBetween(sinceIso, onIso) {
  if (!isValidSpan(sinceIso, onIso)) return null
  const a = parseDay(sinceIso)
  const b = parseDay(onIso)
  let years = b.y - a.y
  if (anniversary(a, years) > b.time) years -= 1
  return years
}

/**
 * True when `sinceIso` is strictly more than `years` years before `onIso`: the
 * anniversary itself is exactly `years`, "more than" starts the day after. Null for an
 * invalid span.
 */
export function moreThanYears(sinceIso, onIso, years) {
  if (!isValidSpan(sinceIso, onIso)) return null
  return parseDay(onIso).time > anniversary(parseDay(sinceIso), years)
}

/**
 * The duration tier that applies. `tiers` are ordered best first; each has a test:
 * `more_than_years` (strictly more), `at_least_years`, or neither (the floor).
 * `since` is the date the address was registered (null = unknown), `on` the date the
 * duration is measured at. Returns the matching tier, every tier when `since` is
 * unknown, or none.
 */
export function durationTiers(tiers, since, on) {
  // A missing, impossible or future registration date is treated as unknown.
  if (!isValidSpan(since, on)) return tiers
  for (const tier of tiers) {
    if (tier.more_than_years != null) {
      if (moreThanYears(since, on, tier.more_than_years)) return [tier]
    } else if (tier.at_least_years != null) {
      const years = wholeYearsBetween(since, on)
      if (years != null && years >= tier.at_least_years) return [tier]
    } else {
      return [tier]
    }
  }
  return []
}

/** Points for a duration tier (see durationTiers); unknown → the range of all tiers. */
export function durationTierPoints(tiers, since, on) {
  const matched = durationTiers(tiers, since, on).map(t => t.points)
  if (matched.length === 0) return ZERO
  return range(Math.min(...matched), Math.max(...matched))
}

/** Points for the order of a wish (1 = first choice); unknown → any rank's points. */
export function choiceRankPoints(table, rank) {
  if (rank == null) return range(Math.min(0, ...table), Math.max(0, ...table))
  return range(table[rank - 1] ?? 0)
}

/**
 * How far a rule set can be trusted for the campaign the parent asked about:
 * - `verified`: written for this campaign and checked against the official text;
 * - `provisional`: written for this campaign from the rules in force when checked, but
 *   the campaign's own schedule or amendments are not out yet;
 * - `illustration`: only an older campaign's rules exist. They are shown as an example
 *   and never treated as this campaign's rules.
 */
function applicability(ruleSet, campaign) {
  if (ruleSet.campaign !== campaign) return 'illustration'
  return ruleSet.verified?.status === 'verified' ? 'verified' : 'provisional'
}

/**
 * The rule set for a place and stage and campaign. A city's own rule set wins over a
 * national one (`city: null`). When the campaign has none, the newest earlier one is
 * returned as an illustration, never as this campaign's rules.
 */
export function selectRuleSet(ruleSets, { country, city = null, stage, campaign }) {
  const matching = ruleSets
    .filter(r => r.country === country && r.stage === stage && (r.city == null || r.city === city))
    .sort((a, b) => b.campaign.localeCompare(a.campaign) || (b.city ? 1 : 0) - (a.city ? 1 : 0))
  const chosen = matching.find(r => r.campaign === campaign) || matching.find(r => r.campaign < campaign)
  if (!chosen) return null
  return { ruleSet: chosen, applies: applicability(chosen, campaign) }
}

/** Legal events (suspensions, rulings) that cover a date. */
export function statusEventsOn(ruleSet, onIso) {
  return (ruleSet.status_events || []).filter(e => e.from <= onIso && (!e.to || onIso <= e.to))
}

/**
 * The id a cut-off row stores to say which calculation it was made under: the rule
 * set plus its version (the date of the latest act it encodes).
 */
export function ruleVersionKey(ruleSet) {
  return `${ruleSet.id}@${ruleSet.version}`
}

/**
 * The campaign for a September start year, in the repo's academic-year spelling
 * (`2026/2027`, as backend/app/utils/academic_year.py and the cut-off archive use).
 */
export function campaignForStartYear(year) {
  return `${year}/${year + 1}`
}

/** The September start year of a campaign (`2026/2027` → 2026). */
export function startYearOfCampaign(campaign) {
  return Number(String(campaign).slice(0, 4))
}
