/**
 * A child's admission calendar: every date a parent acts on from nursery to eighth grade,
 * for one birth year (docs/ADMISSION_CALENDAR.md). The dates come from the rule files'
 * `schedule.milestones`; a campaign whose dates are not out yet gets last known
 * campaign's dates moved to its year and is marked estimated. Pure, so the page and the
 * build's .ics feeds (scripts/calendars.js) list the same dates and run under `node --test`.
 */

/**
 * The steps of a Sofia child's path, in order. `offset` is the September start year minus
 * the birth year (root AGENTS.md: age groups use calendar years). Nursery, kindergarten
 * and preparatory groups at schools (ПГУ) are ranked together under one ordinance, so
 * they read the kindergarten rule file's dates.
 */
export const CALENDAR_STEPS = [
  // Nursery groups take two birth years, so a child can apply in either September.
  { id: 'nursery', ruleStage: 'kindergarten', city: 'sofia', offset: 1, optional: true },
  { id: 'nursery', ruleStage: 'kindergarten', city: 'sofia', offset: 2, optional: true },
  { id: 'kindergarten', ruleStage: 'kindergarten', city: 'sofia', offset: 3 },
  { id: 'preparatory', ruleStage: 'kindergarten', city: 'sofia', offset: 5, optional: true },
  { id: 'grade_1', ruleStage: 'grade_1', city: 'sofia', offset: 7 },
  { id: 'grade_8', ruleStage: 'grade_8', city: null, offset: 14 },
]

/** Milestones a parent must act by: the calendar marks them and the feed reminds. */
export const DEADLINES = new Set(['applications_lock', 'applications', 'enrol'])

const DAY_MS = 86_400_000

function isoToUtc(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return Date.UTC(y, m - 1, d)
}

function utcToIso(ms) {
  return new Date(ms).toISOString().slice(0, 10)
}

/** Days from `a` to `b`, both ISO dates. */
function daysBetween(a, b) {
  return Math.round((isoToUtc(b) - isoToUtc(a)) / DAY_MS)
}

/**
 * `iso` moved `years` on, the way the next campaign's dates are estimated from the last
 * one's: to the same day of the month (`by: 'date'`, where the rules name calendar dates)
 * or to the nearest same weekday (`'weekday'`: a Friday ranking stays a Friday).
 */
export function shiftToYear(iso, years, by = 'weekday') {
  if (!years) return iso
  const [y, m, d] = iso.split('-').map(Number)
  const day = Math.min(d, new Date(Date.UTC(y + years, m, 0)).getUTCDate())
  const target = Date.UTC(y + years, m - 1, day)
  if (by === 'date') return utcToIso(target)
  let diff = new Date(isoToUtc(iso)).getUTCDay() - new Date(target).getUTCDay()
  if (diff > 3) diff -= 7
  if (diff < -3) diff += 7
  return utcToIso(target + diff * DAY_MS)
}

function campaignOf(startYear) {
  return `${startYear}/${startYear + 1}`
}

function startYearOf(campaign) {
  return Number(String(campaign).slice(0, 4))
}

function filesFor(ruleSets, step) {
  return ruleSets.filter(r => r.country === 'bg' && r.stage === step.ruleStage && (r.city ?? null) === step.city)
}

/**
 * The dates of one step's campaign: the campaign's own milestones when its file has them,
 * otherwise the latest official ones moved to that year (estimated). Null when no file
 * of the stage has milestones.
 */
export function campaignDates(ruleSets, step, startYear) {
  const files = filesFor(ruleSets, step)
  const own = files.find(r => r.campaign === campaignOf(startYear))
  if (own?.schedule?.milestones?.length) {
    return {
      milestones: own.schedule.milestones,
      estimated: Boolean(own.schedule.estimated),
      publishedBy: own.schedule.published_by ?? null,
      ruleSet: own,
    }
  }
  const base = files
    .filter(r => r.schedule?.milestones?.length && !r.schedule.estimated && startYearOf(r.campaign) < startYear)
    .sort((a, b) => b.campaign.localeCompare(a.campaign))[0]
  if (!base) return null
  const years = startYear - startYearOf(base.campaign)
  const by = base.schedule.estimate_by
  return {
    milestones: base.schedule.milestones.map(m => {
      const date = shiftToYear(m.date, years, by)
      return { ...m, date, ...(m.end ? { end: utcToIso(isoToUtc(date) + daysBetween(m.date, m.end) * DAY_MS) } : {}) }
    }),
    estimated: true,
    publishedBy: base.schedule.published_by ?? null,
    // The campaign's own file, when there is one, still says how far its rules are checked.
    ruleSet: own ?? null,
  }
}

/** The last day a milestone covers. */
export function milestoneEnd(milestone) {
  return milestone.end || milestone.date
}

/**
 * Every step of a child born in `birthYear` whose campaign has not ended on `today`
 * (ISO date, Sofia): `{ step, startYear, estimated, rulesStatus, publishedBy, milestones }`,
 * in date order, without the ones already over (and an estimated campaign only up to its
 * first round). `rulesStatus` is the campaign file's `verified.status`, or null when the
 * campaign has no file yet (its rules are not written down either). An estimated campaign
 * whose official dates are due by a known day gets a `schedule_due` milestone that day.
 */
export function childCalendar(ruleSets, birthYear, today) {
  const steps = []
  for (const step of CALENDAR_STEPS) {
    const startYear = birthYear + step.offset
    const dates = campaignDates(ruleSets, step, startYear)
    if (!dates) continue
    // Estimated later rounds would only fill a calendar with guesses: an estimated
    // campaign shows its first round, and the rest arrive with the official dates.
    // A campaign still running shows what is left of it.
    const milestones = dates.milestones.filter(m => (!dates.estimated || (m.round ?? 1) === 1) && milestoneEnd(m) >= today)
    const due = dates.estimated && dates.publishedBy ? `${startYear}-${dates.publishedBy}` : null
    if (due && due >= today) milestones.unshift({ key: 'schedule_due', date: due })
    if (milestones.length === 0) continue
    steps.push({
      step,
      startYear,
      estimated: dates.estimated,
      rulesStatus: dates.ruleSet?.verified?.status ?? null,
      publishedBy: dates.publishedBy,
      officialUrl: dates.ruleSet?.official_system_url ?? null,
      milestones: milestones.sort((a, b) => a.date.localeCompare(b.date)),
    })
  }
  return steps
}

/** Birth years with something still to come on `today`: from 8th-graders to next year's babies. */
export function calendarBirthYears(ruleSets, today) {
  const year = Number(today.slice(0, 4))
  const years = []
  for (let birthYear = year + 1; birthYear >= year - 14; birthYear -= 1) {
    if (childCalendar(ruleSets, birthYear, today).length > 0) years.push(birthYear)
  }
  return years
}

/** The feed's path for a birth year, without the language prefix. */
export function feedPath(birthYear) {
  return `/calendar/sofia-born-${birthYear}.ics`
}
