function isObject(value) {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function getPreferredLocale(locale) {
  if (locale) {
    return String(locale).toLowerCase().startsWith('en') ? 'en' : 'bg'
  }
  if (typeof localStorage === 'undefined') return 'bg'
  const raw = localStorage.getItem('language') || 'bg'
  return raw.toLowerCase().startsWith('en') ? 'en' : 'bg'
}

function toList(value) {
  if (Array.isArray(value)) return value
  if (value == null) return []
  return [value]
}

function normalizeText(value) {
  if (value == null) return null
  const text = String(value).trim().replace(/\s+/g, ' ')
  return text || null
}

function normalizeLanguageLevel(value) {
  const text = normalizeText(value)
  if (!text) return null
  return text.toLowerCase().replace(/[\s-]+/g, '_')
}

function extractNamedValue(value) {
  if (value == null) return null

  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    const text = normalizeText(value)
    if (!text) return null

    const quotedPair = text.match(/:\s*'([^']+)'/) || text.match(/:\s*"([^"]+)"/)
    if (quotedPair?.[1]) {
      return normalizeText(quotedPair[1])
    }

    const namedPair = text.match(/['"]?(name|title|label|value)['"]?\s*:\s*['"]([^'"]+)['"]/i)
    if (namedPair?.[2]) {
      return normalizeText(namedPair[2])
    }

    if (
      (text.startsWith('{') && text.endsWith('}')) ||
      (text.startsWith('[') && text.endsWith(']'))
    ) {
      return null
    }

    return text
  }

  if (!isObject(value)) return null

  const priorityKeys = ['name', 'title', 'label', 'value', 'program', 'language', 'facility', 'accreditation']
  for (const key of priorityKeys) {
    if (value[key] != null) {
      const extracted = extractNamedValue(value[key])
      if (extracted) return extracted
    }
  }

  for (const candidate of Object.values(value)) {
    const extracted = extractNamedValue(candidate)
    if (extracted) return extracted
  }

  return null
}

function dedupeStrings(items) {
  const seen = new Set()
  const result = []
  items.forEach((item) => {
    const text = normalizeText(item)
    if (!text) return
    const key = text.toLowerCase()
    if (seen.has(key)) return
    seen.add(key)
    result.push(text)
  })
  return result
}

function normalizeFocusEntries(languageFocus) {
  const normalized = []

  toList(languageFocus).forEach((item) => {
    if (item == null) return

    if (typeof item === 'string') {
      const text = normalizeText(item)
      if (!text) return
      if (text.includes(':')) {
        const [languageRaw, levelRaw] = text.split(':', 2)
        const language = normalizeText(languageRaw)
        if (!language) return
        normalized.push({
          language,
          level: normalizeLanguageLevel(levelRaw),
        })
        return
      }
      normalized.push({ language: text, level: null })
      return
    }

    if (isObject(item)) {
      const language = normalizeText(item.language || extractNamedValue(item))
      if (!language) return
      normalized.push({
        language,
        level: normalizeLanguageLevel(item.level),
      })
    }
  })

  const seen = new Set()
  const deduped = []
  normalized.forEach((entry) => {
    const key = `${entry.language.toLowerCase()}::${entry.level || ''}`
    if (seen.has(key)) return
    seen.add(key)
    deduped.push(entry)
  })
  return deduped
}

function parseClassSize(value) {
  if (value == null) return null

  if (typeof value === 'number') {
    return Number.isFinite(value) && value > 0 ? value : null
  }

  if (typeof value === 'string') {
    const normalized = value.replace(',', '.')
    const direct = Number(normalized)
    if (Number.isFinite(direct) && direct > 0) return direct

    const lower = normalized.toLowerCase()
    const hasClassContext = [
      'class', 'classes', 'group', 'groups', 'student', 'students', 'children',
      'клас', 'класове', 'група', 'групи', 'ученик', 'ученици', 'деца'
    ].some(marker => lower.includes(marker))
    if (!hasClassContext) return null

    const match = normalized.match(/(\d+(?:\.\d+)?)/)
    if (!match) return null
    const parsed = Number(match[1])
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null
  }

  if (isObject(value)) {
    return (
      parseClassSize(value.class_size) ||
      parseClassSize(value.average) ||
      parseClassSize(value.size) ||
      parseClassSize(value.max) ||
      parseClassSize(value.value)
    )
  }

  return null
}

function getMergedList(...values) {
  const flattened = values.flatMap(value => toList(value).map(extractNamedValue).filter(Boolean))
  return dedupeStrings(flattened)
}

export function normalizeSchoolAttributes(rawAttributes, locale) {
  const attributes = isObject(rawAttributes) ? rawAttributes : {}
  const extracted = isObject(attributes.extracted) ? attributes.extracted : {}
  const extractedI18n = isObject(attributes.extracted_i18n) ? attributes.extracted_i18n : {}
  const preferredLocale = getPreferredLocale(locale)
  const localizedExtracted = isObject(extractedI18n[preferredLocale])
    ? { ...extracted, ...extractedI18n[preferredLocale] }
    : extracted
  const normalized = { ...attributes }

  const existingFocus = normalizeFocusEntries(attributes.language_focus)
  const extractedFocus = normalizeFocusEntries(localizedExtracted.languages)
  const mergedFocus = [...existingFocus]
  extractedFocus.forEach((item) => mergedFocus.push(item))
  if (mergedFocus.length > 0) {
    normalized.language_focus = normalizeFocusEntries(mergedFocus)
  }

  const mergedLanguages = getMergedList(
    attributes.languages_of_instruction,
    localizedExtracted.languages
  )
  if (mergedLanguages.length > 0) {
    normalized.languages_of_instruction = mergedLanguages
  }

  const mergedFacilities = getMergedList(
    attributes.facilities,
    localizedExtracted.facilities
  )
  if (mergedFacilities.length > 0) {
    normalized.facilities = mergedFacilities
  }

  const mergedSpecialPrograms = getMergedList(
    attributes.special_programs,
    localizedExtracted.programs,
    localizedExtracted.accreditations
  )
  if (mergedSpecialPrograms.length > 0) {
    normalized.special_programs = mergedSpecialPrograms
  }

  const mergedActivities = getMergedList(
    attributes.activities_offered,
    localizedExtracted.extracurricular
  )
  if (mergedActivities.length > 0) {
    normalized.activities_offered = mergedActivities
  }

  const normalizedClassSize = parseClassSize(attributes.class_size)
  const extractedClassSize = parseClassSize(localizedExtracted.class_size)
  if (normalizedClassSize != null) {
    normalized.class_size = normalizedClassSize
  } else if (extractedClassSize != null) {
    normalized.class_size = extractedClassSize
  }

  return normalized
}

export function normalizeSchool(school, locale) {
  if (!isObject(school)) return school
  return {
    ...school,
    attributes: normalizeSchoolAttributes(school.attributes, locale),
  }
}

export function normalizeSchoolList(schools, locale) {
  if (!Array.isArray(schools)) return []
  return schools.map((school) => normalizeSchool(school, locale))
}
