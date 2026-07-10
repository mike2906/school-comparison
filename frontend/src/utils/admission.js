const NO_REQUIREMENT_PATTERNS = [
  /\b(?:none|no (?:entrance )?(?:test|exam|interview)(?: required)?|(?:test|exam|interview) (?:is )?not required|not required|without (?:an? )?(?:test|exam|interview))\b/i,
  /(?:без|няма)\s+(?:входен\s+)?(?:тест|изпит|интервю|събеседване)/i,
  /не\s+се\s+изисква(?:т)?\s+(?:входен\s+)?(?:тест|изпит|интервю|събеседване)/i,
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

function requirementText(rawRequirement) {
  if (Array.isArray(rawRequirement)) {
    const values = rawRequirement.map(value => String(value || '').trim()).filter(Boolean)
    return values.join(' • ')
  }
  if (typeof rawRequirement === 'string') return rawRequirement.trim()
  if (!rawRequirement || typeof rawRequirement !== 'object') return ''

  const value = rawRequirement.type || rawRequirement.requirement || rawRequirement.method
  return value == null ? '' : String(value).trim()
}

export function classifyAdmissionRequirement(rawRequirement) {
  const text = requirementText(rawRequirement)
  if (!text) return null

  // Negative phrases must win over their embedded keyword ("без изпит" is not a
  // required exam). This order also fixes the old broad `includes('no')` matcher.
  if (NO_REQUIREMENT_PATTERNS.some(pattern => pattern.test(text))) {
    return { kind: 'none', text }
  }
  if (INTERVIEW_PATTERNS.some(pattern => pattern.test(text))) {
    return { kind: 'interview', text }
  }
  if (TEST_PATTERNS.some(pattern => pattern.test(text))) {
    return { kind: 'test', text }
  }
  return { kind: 'other', text }
}
