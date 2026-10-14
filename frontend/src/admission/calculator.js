/**
 * Glue between the rule files, a school's published data and the calculator page: which
 * campaign a parent is asking about, which date durations are counted to, and the
 * cut-offs to show beside a result. Pure functions, so they run under `node --test`.
 */
import { selectRuleSet, statusEventsOn } from './core.js'

/** Calculator tabs: the rule-file stage and the cut-off stages read with it. */
export const STAGES = [
  { id: 'kindergarten', ruleStage: 'kindergarten', city: 'sofia', cutoffStages: ['nursery', 'kindergarten', 'preparatory'] },
  { id: 'grade_1', ruleStage: 'grade_1', city: 'sofia', cutoffStages: ['grade_1'] },
  { id: 'grade_8', ruleStage: 'grade_8', city: 'sofia', cutoffStages: ['grade_8'] },
]

export function stageById(id) {
  return STAGES.find(stage => stage.id === id) || STAGES[0]
}

/**
 * The September a parent is most likely planning for. Sofia's kindergarten and
 * first-grade campaigns run from spring to September, so from October on the next one
 * is the open question.
 */
export function defaultStartYear(now = new Date()) {
  return now.getMonth() >= 9 ? now.getFullYear() + 1 : now.getFullYear()
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
 * which is when Sofia's applications close.
 */
export function measuredOnFor(ruleSet) {
  const schedule = ruleSet?.schedule || {}
  return schedule.applications_close || schedule.first_general_ranking || schedule.first_ranking ||
    `${String(ruleSet?.campaign || '').slice(0, 4)}-05-31`
}

/** The rule set, applicability and legal events in force for one tab and start year. */
export function ruleSetFor(ruleSets, stage, startYear, today) {
  const campaign = `${startYear}/${startYear + 1}`
  const selected = selectRuleSet(ruleSets, { country: 'bg', city: stage.city, stage: stage.ruleStage, campaign })
  if (!selected) return null
  return { ...selected, campaign, events: statusEventsOn(selected.ruleSet, today) }
}

/** `5`, or `3–8` for a range. */
export function formatRange(value, format = n => String(n)) {
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

/** The cut-off a row reports for a round: points, group and points, or a min score. */
export function cutoffValue(round) {
  if (!round) return null
  if (round.min_score != null) return { kind: 'score', score: round.min_score }
  if (round.undersubscribed) return { kind: 'everyone' }
  if (round.last_admitted_group != null) {
    return { kind: 'group', group: round.last_admitted_group, points: round.last_admitted_points ?? null }
  }
  if (round.last_admitted_points != null) return { kind: 'points', points: round.last_admitted_points }
  return null
}

/** A rule file's `{ bg, en }` text in the page language, or a plain string as is. */
export function pickLanguage(text, language) {
  if (!text || typeof text === 'string') return text || ''
  return text[language] || text.bg || text.en || ''
}
