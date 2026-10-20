/**
 * Glue between the rule files, a school's published data and the calculator page: which
 * campaign a parent is asking about, which date durations are counted to, and the
 * cut-offs to show beside a result. Pure functions, so they run under `node --test`.
 */
import { selectRuleSet, statusEventsOn } from './core.js'
import { schoolCatchmentIds } from './catchments.js'
import i18next from 'i18next'
import { usesSofiaKindergartenSystem } from '../utils/admission.js'
import { formatAmount } from '../utils/format.js'

/** Calculator tabs: the rule-file stage and the cut-off stages read with it. */
export const STAGES = [
  { id: 'kindergarten', ruleStage: 'kindergarten', city: 'sofia', cutoffStages: ['nursery', 'kindergarten', 'preparatory'] },
  { id: 'grade_1', ruleStage: 'grade_1', city: 'sofia', cutoffStages: ['grade_1'] },
  { id: 'grade_8', ruleStage: 'grade_8', city: 'sofia', cutoffStages: ['grade_8'] },
]

/**
 * The calculator links on a school's page, in tab order: `{ stage, what }`, where `what`
 * names what the link calculates. A school with a preparatory group (ПГУ) is ranked for
 * it under the kindergarten rules, so it links to the kindergarten tab as `preparatory`
 * as well as to its first-grade or eighth-grade tab.
 */
export function calculatorLinksFor(school) {
  const summary = school?.admission_info?.cutoff_summary || []
  const links = []
  if (usesSofiaKindergartenSystem(school)) links.push({ stage: 'kindergarten', what: 'kindergarten' })
  else if (summary.some(row => row.stage === 'preparatory')) links.push({ stage: 'kindergarten', what: 'preparatory' })
  if (schoolCatchmentIds(school).length > 0) links.push({ stage: 'grade_1', what: 'grade_1' })
  if (summary.some(row => row.stage === 'grade_8')) links.push({ stage: 'grade_8', what: 'grade_8' })
  return links
}

export function stageById(id) {
  return STAGES.find(stage => stage.id === id) || STAGES[0]
}

/**
 * The last date a rule file's schedule says applications or rankings run (Sofia
 * kindergartens keep ranking until `current_rankings_until`), or null without dates.
 */
export function campaignEnd(ruleSet) {
  const schedule = ruleSet?.schedule || {}
  const dates = [
    schedule.applications_close,
    schedule.first_ranking,
    schedule.first_general_ranking,
    ...(schedule.general_rankings || []),
    ...(schedule.rankings || []),
    schedule.current_rankings_until,
  ].filter(Boolean).sort()
  return dates.length ? dates[dates.length - 1] : null
}

/**
 * The September a parent is most likely planning for. With a tab, it is the earliest
 * campaign from this year on that is still taking applications or ranking on `today`
 * (Sofia kindergartens rank for this school year until late November), so the rules
 * shown are the ones a parent applying now is ranked under. Otherwise, from October on
 * the next campaign is the open question.
 */
export function defaultStartYear(now = new Date(), { ruleSets = [], stage = null, today = sofiaToday(now) } = {}) {
  const next = now.getMonth() >= 9 ? now.getFullYear() + 1 : now.getFullYear()
  if (!stage) return next
  for (let year = now.getFullYear(); year < next; year += 1) {
    const campaign = `${year}/${year + 1}`
    // The same file the tab would show, and only if it was written for that campaign.
    const ruleSet = selectRuleSet(ruleSets, { country: 'bg', city: stage.city, stage: stage.ruleStage, campaign })?.ruleSet
    const end = ruleSet?.campaign === campaign ? campaignEnd(ruleSet) : null
    if (end && today <= end) return year
  }
  return next
}

/** Start years to offer: this calendar year's campaign, the default and the one after. */
export function startYearChoices(now = new Date()) {
  const first = now.getFullYear()
  const last = defaultStartYear(now) + 1
  return Array.from({ length: last - first + 1 }, (_, i) => first + i)
}

/**
 * The date a rule set counts address durations to. The rule files give the campaign's
 * schedule; when a file has no usable date the end of May of the start year is used,
 * which is when Sofia's applications close. While a campaign's later rankings are still
 * running (Sofia kindergartens rank every two weeks until `current_rankings_until`), a
 * parent applying now is counted to a ranking after today, so today is used: it is at
 * most one ranking early, never later than the real date.
 */
export function measuredOnFor(ruleSet, today = null) {
  const schedule = ruleSet?.schedule || {}
  const first = schedule.applications_close || schedule.first_general_ranking || schedule.first_ranking ||
    `${String(ruleSet?.campaign || '').slice(0, 4)}-05-31`
  const until = schedule.current_rankings_until
  if (today && until && today > first && today <= until) return today
  return first
}

/** The rule set, applicability and legal events in force for one tab and start year. */
export function ruleSetFor(ruleSets, stage, startYear, today) {
  const campaign = `${startYear}/${startYear + 1}`
  const selected = selectRuleSet(ruleSets, { country: 'bg', city: stage.city, stage: stage.ruleStage, campaign })
  if (!selected) return null
  return { ...selected, campaign, events: statusEventsOn(selected.ruleSet, today) }
}

/** `5`, or `3–8` for a range, in the page language's style ("412,5" on the Bulgarian page). */
export function formatRange(value, format = n => formatAmount(n, i18next.language, 2)) {
  if (!value) return ''
  return value.min === value.max ? format(value.min) : `${format(value.min)}–${format(value.max)}`
}

/** Today's date in Sofia, as YYYY-MM-DD, for comparing with rule-file dates. */
export function sofiaToday(now = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Sofia' }).format(now)
}

/** The rules part of a cut-off's `rule_set` (`bg/sofia/grade_1/2026-2027@2026-03-12`). */
export function sameRules(cutoffRuleSet, ruleSet) {
  if (!cutoffRuleSet || !ruleSet) return null
  const [id, version] = String(cutoffRuleSet).split('@')
  // Without a version the row does not say which rules it was ranked under.
  if (!version) return null
  // Different campaigns of one family share the calculation when the same act is in force.
  const family = id.split('/').slice(0, -1).join('/')
  const ownFamily = ruleSet.id.split('/').slice(0, -1).join('/')
  return family === ownFamily && version === ruleSet.version
}

/**
 * The summary rows (`admission_info.cutoff_summary`) for one tab: kindergarten rows for
 * the child's age group, every first-grade row, every 8th-grade class.
 */
export function summaryRowsFor(summary, stage, { ageGroup = null } = {}) {
  return (summary || []).filter(row =>
    stage.cutoffStages.includes(row.stage) && (ageGroup == null || row.age_group == null || row.age_group === ageGroup)
  )
}

/**
 * Whether a kindergarten row ranked children of this child's age: the same age group and,
 * when the row names one, a birth year as far before its campaign as the child's is
 * before `startYear` (nursery groups take two birth years). A row from an earlier
 * campaign is last year's cut-off for the same group, not another child's.
 */
export function rowFitsChild(row, ageGroup, birthYear, startYear) {
  if (row.age_group !== ageGroup) return false
  if (row.birth_year == null) return true
  const campaignStart = Number.parseInt(String(row.campaign || '').slice(0, 4), 10)
  if (!Number.isFinite(campaignStart)) return row.birth_year === birthYear
  // A later campaign's ranking is not a cut-off this child could have met.
  return campaignStart <= startYear && campaignStart - row.birth_year === startYear - birthYear
}

/** The cut-off a row reports for a round: points, group and points, or a min score. */
export function cutoffValue(round) {
  if (!round) return null
  // Classes with gender quotas rank boys and girls separately, each with its own minimum.
  if (round.min_score_boys != null || round.min_score_girls != null) {
    return { kind: 'quota', boys: round.min_score_boys ?? null, girls: round.min_score_girls ?? null }
  }
  if (round.min_score != null) return { kind: 'score', score: round.min_score }
  if (round.places === 0) return { kind: 'noPlaces' }
  if (round.undersubscribed) return { kind: 'everyone' }
  if (round.last_admitted_group != null) {
    return { kind: 'group', group: round.last_admitted_group, points: round.last_admitted_points ?? null }
  }
  if (round.last_admitted_points != null) return { kind: 'points', points: round.last_admitted_points }
  return null
}

/** `2026/2027` → `2026/27`, as parents write a school year. */
export function shortCampaign(campaign) {
  const match = /^(\d{4})\/\d{2}(\d{2})$/.exec(String(campaign || ''))
  return match ? `${match[1]}/${match[2]}` : campaign || ''
}

const DAY_FORMATS = new Map()

/** A day (YYYY-MM-DD) in the reader's language: `2 October 2026`, `2 октомври 2026 г.`. */
export function formatRankingDay(iso, language) {
  if (!iso) return ''
  const date = new Date(`${String(iso).slice(0, 10)}T12:00:00Z`)
  if (Number.isNaN(date.getTime())) return String(iso)
  const locale = String(language || '').startsWith('bg') ? 'bg-BG' : 'en-GB'
  if (!DAY_FORMATS.has(locale)) {
    DAY_FORMATS.set(locale, new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }))
  }
  return DAY_FORMATS.get(locale).format(date)
}

/** Which ranking a compared cut-off comes from: `2026/27 · 2 Oct 2026`. */
export function rankingWhen(row, round, language) {
  return [shortCampaign(row?.campaign), formatRankingDay(round?.classified_on, language)].filter(Boolean).join(' · ')
}

/**
 * A summary row's rankings in date order. On a school page the summary rows carry
 * `history` (every general-queue ranking, see `withRoundHistory`); the list response has
 * only the first and the latest.
 */
export function roundsOf(row) {
  if (row?.history?.length) return row.history
  const rounds = [row?.first, row?.rounds > 1 ? row?.latest : null].filter(Boolean)
  return rounds.length === 2 && rounds[0].round_id === rounds[1].round_id ? [rounds[0]] : rounds
}

/**
 * The ranking a row is read and compared on, the same on a list card and a school page:
 * the campaign's main (first) ranking when we have it, since most places go there and
 * later rankings only fill places that came free. kg.sofia.bg keeps only the later
 * free-place rankings, so for those rows (and a main ranking with no places) it is the
 * latest one that had places, else the first that had, else the latest (no place came
 * free).
 */
export function comparableRound(row) {
  const first = row?.first
  const latest = row?.rounds > 1 && row?.latest ? row.latest : null
  if (!first || !latest) return first || latest
  // A main ranking with places to fill; one without says nothing a later ranking can't.
  if (first.round_kind !== 'current' && first.places !== 0) return first
  if (latest.places !== 0) return latest
  return first.places !== 0 ? first : latest
}

/** Lower is easier: a higher group, then fewer points or a lower score. */
function easeKey(value) {
  if (value.kind === 'group') return [-value.group, value.points ?? 0]
  if (value.kind === 'score') return [value.score, 0]
  if (value.kind === 'points') return [value.points, 0]
  return null
}

function easier(a, b) {
  return a[0] < b[0] || (a[0] === b[0] && a[1] < b[1])
}

/**
 * A ranking after the main one that admitted a child with a weaker result than the main
 * ranking's last admitted (a place that came free), or that admitted everyone: worth one
 * line under the headline. Null when there is none, or the row has no main ranking.
 * Reads `history` where the page has it; a list's summary has only the latest.
 */
export function laterEasierRound(row) {
  const main = comparableRound(row)
  if (!main || main !== row.first || main.round_kind === 'current') return null
  const mainValue = cutoffValue(main)
  const mainKey = mainValue && easeKey(mainValue)
  if (!mainKey) return null
  let best = null
  let bestKey = mainKey
  for (const round of roundsOf(row).slice(1)) {
    const value = cutoffValue(round)
    if (value?.kind === 'everyone' && round.admitted_count > 0) return round
    // Points, a group or a score: only a result of the same kind can be weaker.
    const key = value?.kind === mainValue.kind ? easeKey(value) : null
    if (key && easier(key, bestKey)) {
      best = round
      bestKey = key
    }
  }
  return best
}

/**
 * The summary rows with `history`: every general-queue ranking of the row's campaign,
 * oldest first, from `admission_info.cutoffs` (the school page has it; lists don't).
 */
export function withRoundHistory(admissionInfo) {
  const summary = admissionInfo?.cutoff_summary || []
  const all = admissionInfo?.cutoffs || []
  if (all.length === 0) return summary
  const key = row => [row.stage, row.campaign, row.age_group, row.birth_year, row.class_code, row.building].map(v => v ?? '').join('|')
  const byKey = new Map()
  for (const round of all) {
    if (round.queue !== 'general') continue
    const k = key(round)
    if (!byKey.has(k)) byKey.set(k, [])
    byKey.get(k).push(round)
  }
  return summary.map(row => {
    const history = (byKey.get(key(row)) || [])
      .slice()
      .sort((a, b) => String(a.classified_on || '').localeCompare(String(b.classified_on || '')) || a.round_id - b.round_id)
    return history.length > 0 ? { ...row, history } : row
  })
}

/** 1 when a range is wholly above `value`, -1 wholly below, 0 when exactly it, else null. */
function rangeVersus(range, value) {
  if (range.min > value) return 1
  if (range.max < value) return -1
  return range.min === value && range.max === value ? 0 : null
}

/**
 * How a calculated result stands against a round's last admitted child: 'above',
 * 'below', 'tie' (ties are drawn by lot), 'unsure' (the result's range spans the
 * cut-off), 'sameGroup' (first grade, same group, no points listed), 'everyone' (all
 * applicants got in), 'noPlaces', or null when they can't be
 * compared. `yours` is `{ points }` for kindergartens or `{ group, points }` for first
 * grade, where a lower group ranks first and points only order children within a group.
 */
export function compareWithCutoff(yours, round) {
  const value = cutoffValue(round)
  if (!value || !yours?.points) return null
  if (value.kind === 'everyone' || value.kind === 'noPlaces') return value.kind
  let versus
  if (value.kind === 'points') {
    // A first-grade row without its group can't be compared: the group ranks first.
    if (yours.group) return null
    versus = rangeVersus(yours.points, value.points)
  } else if (value.kind === 'group' && yours.group) {
    const group = rangeVersus(yours.group, value.group)
    if (group == null) return 'unsure'
    if (group !== 0) return group < 0 ? 'above' : 'below'
    // Same group, and the list gives no points to compare within it.
    if (value.points == null) return 'sameGroup'
    versus = rangeVersus(yours.points, value.points)
  } else {
    return null
  }
  return versus === 1 ? 'above' : versus === -1 ? 'below' : versus === 0 ? 'tie' : 'unsure'
}

/** A rule file's `{ bg, en }` text in the page language, or a plain string as is. */
export function pickLanguage(text, language) {
  if (!text || typeof text === 'string') return text || ''
  return text[language] || text.bg || text.en || ''
}
