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
