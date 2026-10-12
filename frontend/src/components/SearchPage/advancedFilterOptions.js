import { useMemo } from 'react'
import { getLanguageFocusPairs } from '../../utils/schoolAttributes.js'
import { matchesAdvancedFilters } from '../../utils/advancedFilters.js'
import { languageLabel } from '../../utils/languages.js'

export const DEFAULT_ADVANCED_OPTIONS = {
  language_focus_levels: ['immersion', 'bilingual', 'enrichment'],
  special_programs: ['music_program', 'sports_program', 'arts_program', 'extended_day', 'meals_provided'],
  facilities: ['sports_facilities', 'cafeteria', 'library', 'computer_lab', 'transportation'],
  teaching_approach: ['montessori', 'waldorf', 'ib_program', 'project_based'],
}

// Labels for advanced-filter options, language focus languages and levels.
export function filterLabels(t) {
  const formatAdvancedLabel = (key) => t(`advancedFilters.options.${key}`, { defaultValue: key.replace(/_/g, ' ') })
  const formatLanguageLabel = (key) => languageLabel(key, t)
  const formatLanguageLevelLabel = (key) => t(`advancedFilters.levels.${key}`, { defaultValue: key.replace(/_/g, ' ') })
  return { formatAdvancedLabel, formatLanguageLabel, formatLanguageLevelLabel }
}

// Per-option result counts and the language/level choices. Both scan every school, so
// they are computed once per data or filter change, not on every render.
export function useAdvancedFilterOptions({ baseSchools, availableFilters, filters }) {
  const advancedCounts = useMemo(() => {
    const IGNORE_KEY = {
      language_focus: 'languageFocus',
      special_programs: 'specialPrograms',
      facilities: 'facilities',
      teaching_approach: 'teachingApproach',
    }
    const matchesSelected = (school, groupToIgnore) => (
      matchesAdvancedFilters(school, filters, IGNORE_KEY[groupToIgnore])
    )

    const counts = {
      language_focus_pairs: {},
      special_programs: {},
      facilities: {},
      teaching_approach: {},
    }

    const languageOptions = Array.from(new Set([
      ...(availableFilters.language_focus_pairs || []),
    ]))

    languageOptions.forEach(option => {
      counts.language_focus_pairs[option] = baseSchools.filter(school => {
        if (!matchesSelected(school, 'language_focus')) return false
        return getLanguageFocusPairs(school).has(option)
      }).length
    })

    Object.keys(counts)
      .filter(key => key !== 'language_focus_pairs')
      .forEach(groupKey => {
        const options = Array.from(new Set([
          ...(DEFAULT_ADVANCED_OPTIONS[groupKey] || []),
          ...(availableFilters[groupKey] || []),
        ]))

        options.forEach(option => {
          counts[groupKey][option] = baseSchools.filter(school => {
            if (!matchesSelected(school, groupKey)) return false
            const values = school.attributes?.filter_tags?.[groupKey] || []
            return values.includes(option)
          }).length
        })
      })

    return counts
  }, [baseSchools, availableFilters, filters])

  // Scans every school, so compute once per data change rather than on every render
  const languageFilterOptions = useMemo(() => {
    const derivedLanguages = new Set()
    const derivedLevels = new Set()
    const derivedPairs = new Set()

    baseSchools.forEach(school => {
      getLanguageFocusPairs(school).forEach(pair => {
        derivedPairs.add(pair)
        if (pair.includes(':')) {
          const [language, level] = pair.split(':')
          if (language) derivedLanguages.add(language)
          if (level) derivedLevels.add(level)
        }
      })
    })

    const languages = Array.from(new Set([
      ...(availableFilters.language_focus_languages || []),
      ...derivedLanguages,
    ]))

    const levels = Array.from(new Set([
      ...(DEFAULT_ADVANCED_OPTIONS.language_focus_levels || []),
      ...(availableFilters.language_focus_levels || []),
      ...derivedLevels,
    ]))

    const pairs = [
      ...(availableFilters.language_focus_pairs || []),
      ...derivedPairs,
    ]
    const byLanguage = new Map()

    pairs.forEach(pair => {
      const [language, level] = pair.split(':')
      if (!language || !level) return
      if (!byLanguage.has(language)) {
        byLanguage.set(language, new Set())
      }
      byLanguage.get(language).add(level)
    })

    languages.forEach(language => {
      const existingLevels = byLanguage.get(language) || new Set()
      const combined = new Set([...levels, ...existingLevels])
      byLanguage.set(language, combined)
    })

    return { languages, levels, byLanguage }
  }, [baseSchools, availableFilters])

  return { advancedCounts, languageFilterOptions }
}
