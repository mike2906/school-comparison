import { planningYear } from './ageSelection.js'
import { canonicalLanguagePair, languageKey } from './languages.js'

export const uniqueCanonical = (values, canonical) => (
  [...new Set((values || []).map(canonical).filter(Boolean))]
)

// Filters are read from the URL, and re-read whenever it changes: the search page has its
// own history entries (the detail panel), so Back can bring back an older filter set.
export function readFiltersFromUrl(params) {
  const getParamList = (key) => {
    const values = params.getAll(key)
    if (values.length > 0) return values
    const csv = params.get(key)
    return csv ? csv.split(',').map(value => value.trim()).filter(Boolean) : []
  }
  const type = params.get('school_type')
  const targetYearParam = params.get('target_year')
  return {
    ageGroup: params.get('age_group'),
    targetYear: targetYearParam ? parseInt(targetYearParam) : planningYear(),
    birthYear: Number.parseInt(params.get('birth_year') || '', 10) || null,
    schoolType: type === 'international' ? 'private' : type,
    educationLevel: params.get('education_level'),
    includeCrossover: params.get('include_crossover') === 'true',
    // Canonical, so links saved with older spellings ('English:intensive') still match.
    languageFocus: uniqueCanonical(getParamList('language_focus'), canonicalLanguagePair),
    specialPrograms: getParamList('special_programs'),
    facilities: getParamList('facilities'),
    teachingApproach: getParamList('teaching_approach'),
  }
}

// The filters after a change: choosing an age group other than preschool, or including
// crossover schools, drops the preschool education level.
export function nextFilterState(filters, newFilters) {
  const nextFilters = { ...filters, ...newFilters }
  if (newFilters.ageGroup !== undefined) {
    if (nextFilters.ageGroup !== 'preschool') {
      nextFilters.educationLevel = null
      nextFilters.includeCrossover = false
    } else if (nextFilters.includeCrossover) {
      nextFilters.educationLevel = null
    }
  }
  if (newFilters.includeCrossover !== undefined && nextFilters.includeCrossover) {
    nextFilters.educationLevel = null
  }
  return nextFilters
}

// The search URL's params after a change. Only the filters in the change are rewritten;
// the education level follows the resulting filters.
export function filterSearchParams(searchParams, newFilters, nextFilters) {
  const params = new URLSearchParams(searchParams)
  if (newFilters.ageGroup !== undefined) {
    if (newFilters.ageGroup) {
      params.set('age_group', newFilters.ageGroup)
    } else {
      params.delete('age_group')
    }
  }
  if (newFilters.schoolType !== undefined) {
    if (newFilters.schoolType) {
      params.set('school_type', newFilters.schoolType)
    } else {
      params.delete('school_type')
    }
  }
  if (newFilters.targetYear !== undefined) {
    if (newFilters.targetYear) {
      params.set('target_year', String(newFilters.targetYear))
    } else {
      params.delete('target_year')
    }
  }
  if (newFilters.birthYear !== undefined) {
    if (newFilters.birthYear) {
      params.set('birth_year', String(newFilters.birthYear))
    } else {
      params.delete('birth_year')
    }
  }
  if (newFilters.includeCrossover !== undefined) {
    if (newFilters.includeCrossover) {
      params.set('include_crossover', 'true')
    } else {
      params.delete('include_crossover')
    }
  }
  if (nextFilters.ageGroup === 'preschool' && !nextFilters.includeCrossover && nextFilters.educationLevel) {
    params.set('education_level', nextFilters.educationLevel)
  } else {
    params.delete('education_level')
  }
  if (newFilters.languageFocus !== undefined) {
    params.delete('language_focus')
    newFilters.languageFocus.forEach(value => params.append('language_focus', value))
  }
  if (newFilters.specialPrograms !== undefined) {
    params.delete('special_programs')
    newFilters.specialPrograms.forEach(value => params.append('special_programs', value))
  }
  if (newFilters.facilities !== undefined) {
    params.delete('facilities')
    newFilters.facilities.forEach(value => params.append('facilities', value))
  }
  if (newFilters.teachingApproach !== undefined) {
    params.delete('teaching_approach')
    newFilters.teachingApproach.forEach(value => params.append('teaching_approach', value))
  }
  return params
}

export const EMPTY_AVAILABLE_FILTERS = {
  language_focus_pairs: [],
  language_focus_languages: [],
  language_focus_levels: [],
  special_programs: [],
  facilities: [],
  teaching_approach: [],
}

export function normalizeAvailableFilters(data) {
  return {
    // Canonical keys, so "English" / "английски" / "Английски език" are one option.
    language_focus_pairs: uniqueCanonical(data.language_focus_pairs, canonicalLanguagePair),
    language_focus_languages: uniqueCanonical(data.language_focus_languages, languageKey),
    language_focus_levels: data.language_focus_levels || [],
    special_programs: data.special_programs || [],
    facilities: data.facilities || [],
    teaching_approach: data.teaching_approach || [],
  }
}
