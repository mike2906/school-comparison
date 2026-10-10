const NO_REQUIREMENT_PATTERNS = [
  /\b(?:none|no (?:entrance )?(?:test|exam|interview)(?: required)?|(?:test|exam|interview) (?:is )?not required|not required|without (?:an? )?(?:test|exam|interview))\b/i,
  /(?:без|няма)\s+(?:(?:входен|приемен|приемния)\s+)?(?:тест|изпит|интервю|събеседване)/i,
  /не\s+се\s+изисква(?:т)?\s+(?:(?:входен|приемен|приемния)\s+)?(?:тест|изпит|интервю|събеседване)/i,
  /(?:тест|изпит|интервю|събеседване)\s+не\s+се\s+изисква/i,
]

const INTERVIEW_PATTERNS = [
  /\binterview\b/i,
  /(?:интервю|събеседване)/i,
]

const TEST_PATTERNS = [
  /\b(?:test|exam)\b/i,
  /(?:тест|изпит)/i,
]

function requirementParts(rawRequirement) {
  if (Array.isArray(rawRequirement)) {
    return rawRequirement.flatMap(requirementParts)
  }
  if (!rawRequirement) return []
  if (typeof rawRequirement === 'object') {
    const value = rawRequirement.type || rawRequirement.requirement || rawRequirement.method
    return value == null ? [] : requirementParts(value)
  }

  return String(rawRequirement)
    .split(/[•;\n]+/)
    .map(value => value.trim())
    .filter(Boolean)
}

function classifyPart(text) {
  if (NO_REQUIREMENT_PATTERNS.some(pattern => pattern.test(text))) return 'none'
  if (INTERVIEW_PATTERNS.some(pattern => pattern.test(text))) return 'interview'
  if (TEST_PATTERNS.some(pattern => pattern.test(text))) return 'test'
  return 'other'
}

/**
 * The curated `admission_info.requirements`. It is free text, so it may be written per
 * language ({ bg, en }); a plain string or list is returned as is.
 */
export function curatedRequirement(admissionInfo, language) {
  const raw = admissionInfo?.requirements
  if (raw && typeof raw === 'object' && !Array.isArray(raw) && (raw.bg || raw.en)) {
    const preferred = language?.startsWith('en') ? 'en' : 'bg'
    return raw[preferred] || raw.bg || raw.en
  }
  return raw
}

export function classifyAdmissionRequirement(rawRequirement) {
  const parts = requirementParts(rawRequirement)
  if (parts.length === 0) return null

  const text = parts.join(' • ')
  const kinds = new Set(parts.map(classifyPart))
  const hasInterview = kinds.has('interview')
  const hasTest = kinds.has('test')

  // A real required step must outrank a separate "no exam" fragment. If both an
  // interview and a test are required (or an unknown step accompanies "none"), keep
  // the full source text rather than hiding one detail behind a single generic label.
  if (hasInterview && hasTest) return { kind: 'other', text }
  if (hasInterview) return { kind: 'interview', text }
  if (hasTest) return { kind: 'test', text }
  if (kinds.size === 1 && kinds.has('none')) return { kind: 'none', text }
  return { kind: 'other', text }
}

/**
 * True when a school's admission runs through Sofia's municipal kindergarten system
 * (kg.sofia.bg). Responses carry no city yet and the app only lists Sofia, so this checks
 * the country; add the city when other Bulgarian cities are published.
 */
export function usesSofiaKindergartenSystem(school) {
  return school?.school_type === 'state' &&
    school?.education_level === 'kindergarten' &&
    school?.country_code === 'bg'
}

const REQUIREMENT_LABELS = {
  interview: { icon: '📝', key: 'schoolCard.admissions.interviewRequired' },
  test: { icon: '📋', key: 'schoolCard.admissions.testRequired' },
  none: { icon: '✅', key: 'schoolCard.admissions.noEntranceExam' },
}

/**
 * A parent-facing label for an entry requirement: a translated phrase for the known kinds
 * (interview, test, none), otherwise the source text as written.
 */
export function getAdmissionRequirement(rawRequirement, t) {
  const requirement = classifyAdmissionRequirement(rawRequirement)
  if (!requirement) return null
  const label = REQUIREMENT_LABELS[requirement.kind]
  if (label) return { kind: requirement.kind, icon: label.icon, text: t(label.key) }
  return { kind: requirement.kind, icon: 'ℹ️', text: requirement.text }
}

function latestByYear(items) {
  return items.reduce((acc, item) => (item.year > acc.year ? item : acc), items[0])
}

function lastRound(rounds = []) {
  if (rounds.length === 0) return null
  return rounds.reduce((acc, item) => (item.round > acc.round ? item : acc), rounds[0])
}

function thresholdsFor(admissionInfo, ageGroup) {
  const thresholds = admissionInfo?.historical_thresholds || []
  return ageGroup ? thresholds.filter(item => item.age_group === ageGroup) : thresholds
}

/**
 * State kindergartens: the points of the last child admitted in the final round of the
 * latest year, for one age group when given.
 */
export function getLastAdmittedPoints(admissionInfo, ageGroup) {
  const relevant = thresholdsFor(admissionInfo, ageGroup)
  if (relevant.length === 0) return null

  const latest = latestByYear(relevant)
  const round = lastRound(latest.rounds)
  if (!round) return null

  return {
    points: round.last_admitted_points,
    year: latest.year,
    round: round.round,
  }
}

/** Final-round admission points per year, newest first. */
export function getPointsHistory(admissionInfo, ageGroup) {
  return thresholdsFor(admissionInfo, ageGroup)
    .map(item => {
      const round = lastRound(item.rounds)
      return round ? { year: item.year, points: round.last_admitted_points } : null
    })
    .filter(Boolean)
    .sort((a, b) => b.year - a.year)
}

/** State gymnasiums: the latest year's minimum admission score. */
export function getMinNvoScore(admissionInfo) {
  const scores = admissionInfo?.historical_min_scores || []
  if (scores.length === 0) return null
  const latest = latestByYear(scores)
  return {
    score: latest.min_score,
    year: latest.year,
  }
}

/** Minimum admission scores per year, newest first. */
export function getMinNvoScores(admissionInfo) {
  const scores = admissionInfo?.historical_min_scores || []
  return scores
    .map(item => ({ year: item.year, score: item.min_score }))
    .sort((a, b) => b.year - a.year)
}
