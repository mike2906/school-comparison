import { useTranslation } from 'react-i18next'
import { DEFAULT_ADVANCED_OPTIONS, filterLabels } from './advancedFilterOptions.js'

export function AdvancedFilters({
  filters,
  availableFilters,
  advancedCounts,
  languageFilterOptions,
  onFilterChange: handleFilterChange,
}) {
  const { t } = useTranslation()
  const { formatAdvancedLabel, formatLanguageLabel, formatLanguageLevelLabel } = filterLabels(t)

  const buildOptions = (key) => {
    const serverOptions = availableFilters[key] || []
    const defaults = DEFAULT_ADVANCED_OPTIONS[key] || []
    return Array.from(new Set([...defaults, ...serverOptions]))
  }

  const renderLanguageFocusGroup = () => {
    const { languages, levels, byLanguage } = languageFilterOptions
    if (languages.length === 0) return null

    return (
      <details className="rounded-lg border border-neutral-200 bg-white px-3 py-2">
        <summary className="cursor-pointer text-sm font-medium text-neutral-700 flex items-center justify-between">
          <span className="flex items-center gap-2">
            <svg className="w-4 h-4 text-neutral-500" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 10.94l3.71-3.7a.75.75 0 111.06 1.06l-4.24 4.24a.75.75 0 01-1.06 0L5.21 8.29a.75.75 0 01.02-1.08z" clipRule="evenodd" />
            </svg>
            {t('advancedFilters.languageFocus')}
          </span>
          {filters.languageFocus.length > 0 && (
            <span className="text-xs text-primary-700 bg-primary-50 px-2 py-0.5 rounded-full">
              {filters.languageFocus.length}
            </span>
          )}
        </summary>
        <div className="space-y-3 mt-3">
          {languages.map(language => {
            const availableLevels = Array.from(byLanguage.get(language) || new Set(levels))
            const selectedForLanguage = filters.languageFocus.filter(value => value.startsWith(`${language}:`))
            const allLevelKeys = availableLevels.map(level => `${language}:${level}`)
            const isLanguageChecked = selectedForLanguage.length === allLevelKeys.length && allLevelKeys.length > 0
            const languageCount = allLevelKeys.reduce((sum, key) => {
              return sum + (advancedCounts.language_focus_pairs?.[key] ?? 0)
            }, 0)
            // Languages no listed school offers are noise; keep one if it is selected.
            if (languageCount === 0 && selectedForLanguage.length === 0) return null

            return (
              <details key={language} className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2">
                <summary className="cursor-pointer text-sm font-medium text-neutral-700 flex items-center justify-between">
                  <span className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={isLanguageChecked}
                      onChange={(event) => {
                        const next = isLanguageChecked
                          ? filters.languageFocus.filter(value => !value.startsWith(`${language}:`))
                          : Array.from(new Set([...filters.languageFocus, ...allLevelKeys]))
                        handleFilterChange({ languageFocus: next })
                        const details = event.currentTarget.closest('details')
                        if (details) details.open = true
                      }}
                      className="text-primary-600 focus:ring-primary-500"
                    />
                    {formatLanguageLabel(language)}
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="text-xs text-neutral-400">({languageCount})</span>
                    {selectedForLanguage.length > 0 && (
                      <span className="text-xs text-primary-700 bg-white px-2 py-0.5 rounded-full border border-primary-200">
                        {selectedForLanguage.length}
                      </span>
                    )}
                  </span>
                </summary>
                <div className="space-y-2 mt-3">
                  {availableLevels.map(level => {
                    const option = `${language}:${level}`
                    const isChecked = filters.languageFocus.includes(option)
                    const count = advancedCounts.language_focus_pairs?.[option] ?? 0
                    const isDisabled = count === 0 && !isChecked
                    return (
                      <label
                        key={option}
                        className={`flex items-center gap-2 text-sm ${
                          isDisabled ? 'text-neutral-400' : 'text-neutral-600'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          disabled={isDisabled}
                          onChange={() => {
                            if (isDisabled) return
                            const next = isChecked
                              ? filters.languageFocus.filter(value => value !== option)
                              : [...filters.languageFocus, option]
                            handleFilterChange({ languageFocus: next })
                          }}
                          className="text-primary-600 focus:ring-primary-500 disabled:cursor-not-allowed"
                        />
                        <span className="flex-1">{formatLanguageLevelLabel(level)}</span>
                        <span className="text-xs text-neutral-400">({count})</span>
                      </label>
                    )
                  })}
                </div>
              </details>
            )
          })}
        </div>
      </details>
    )
  }

  const renderGroup = (groupKey, titleKey, selectedValues) => {
    const options = buildOptions(groupKey)
    if (options.length === 0) return null

    const selectedCount = selectedValues.length

    return (
      <details className="rounded-lg border border-neutral-200 bg-white px-3 py-2">
        <summary className="cursor-pointer text-sm font-medium text-neutral-700 flex items-center justify-between">
          <span className="flex items-center gap-2">
            <svg className="w-4 h-4 text-neutral-500" viewBox="0 0 20 20" fill="currentColor">
              <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 10.94l3.71-3.7a.75.75 0 111.06 1.06l-4.24 4.24a.75.75 0 01-1.06 0L5.21 8.29a.75.75 0 01.02-1.08z" clipRule="evenodd" />
            </svg>
            {t(titleKey)}
          </span>
          {selectedCount > 0 && (
            <span className="text-xs text-primary-700 bg-primary-50 px-2 py-0.5 rounded-full">
              {selectedCount}
            </span>
          )}
        </summary>
        <div className="space-y-2 mt-3">
          {options.map(option => {
            const isChecked = selectedValues.includes(option)
            const count = advancedCounts[groupKey]?.[option] ?? 0
            const isDisabled = count === 0 && !isChecked
            return (
              <label
                key={option}
                className={`flex items-center gap-2 text-sm ${
                  isDisabled ? 'text-neutral-400' : 'text-neutral-600'
                }`}
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  disabled={isDisabled}
                  onChange={() => {
                    if (isDisabled) return
                    const next = isChecked
                      ? selectedValues.filter(value => value !== option)
                      : [...selectedValues, option]
                    const update = {
                      [groupKey === 'language_focus' ? 'languageFocus'
                        : groupKey === 'special_programs' ? 'specialPrograms'
                        : groupKey === 'facilities' ? 'facilities'
                        : 'teachingApproach']: next,
                    }
                    handleFilterChange(update)
                  }}
                  className="text-primary-600 focus:ring-primary-500 disabled:cursor-not-allowed"
                />
                <span className="flex-1">{formatAdvancedLabel(option)}</span>
                <span className="text-xs text-neutral-400">({count})</span>
              </label>
            )
          })}
        </div>
      </details>
    )
  }

  return (
    <details open className="rounded-xl border border-neutral-200 bg-white p-4">
      <summary className="cursor-pointer text-sm font-semibold text-neutral-800">
        {t('advancedFilters.title')}
      </summary>
      <div className="mt-4 space-y-4">
        {renderLanguageFocusGroup()}
        {renderGroup('special_programs', 'advancedFilters.specialPrograms', filters.specialPrograms)}
        {renderGroup('facilities', 'advancedFilters.facilities', filters.facilities)}
        {renderGroup('teaching_approach', 'advancedFilters.teachingApproach', filters.teachingApproach)}
        <div>
          <button
            type="button"
            onClick={() => handleFilterChange({
              languageFocus: [],
              specialPrograms: [],
              facilities: [],
              teachingApproach: [],
            })}
            className="text-sm text-neutral-600 hover:text-neutral-800 hover:underline"
          >
            {t('advancedFilters.clear')}
          </button>
        </div>
      </div>
    </details>
  )
}

// Chips for the selected advanced filters (age and school type have their own controls).
export function ActiveFilterChips({ filters, onFilterChange: handleFilterChange }) {
  const { t } = useTranslation()
  const { formatLanguageLabel, formatLanguageLevelLabel, formatAdvancedLabel } = filterLabels(t)

  const advancedFilters = []
  const advancedByLanguage = new Map()

  const addAdvancedChips = (values, groupKey) => {
    values.forEach(value => {
      if (groupKey === 'languageFocus') {
        const [language, level] = value.split(':')
        if (language && level) {
          if (!advancedByLanguage.has(language)) {
            advancedByLanguage.set(language, [])
          }
          advancedByLanguage.get(language).push(level)
        } else if (language) {
          if (!advancedByLanguage.has(language)) {
            advancedByLanguage.set(language, [])
          }
        }
      }
      const label = groupKey === 'languageFocus'
        ? (() => {
            const [language, level] = value.split(':')
            if (language && level) {
              return `${formatLanguageLabel(language)} · ${formatLanguageLevelLabel(level)}`
            }
            return formatLanguageLabel(language || value)
          })()
        : formatAdvancedLabel(value)
      advancedFilters.push({
        key: `${groupKey}-${value}`,
        label,
        onRemove: () => {
          const next = values.filter(item => item !== value)
          if (groupKey === 'languageFocus') {
            handleFilterChange({ languageFocus: next })
          } else if (groupKey === 'specialPrograms') {
            handleFilterChange({ specialPrograms: next })
          } else if (groupKey === 'facilities') {
            handleFilterChange({ facilities: next })
          } else {
            handleFilterChange({ teachingApproach: next })
          }
        },
      })
    })
  }

  addAdvancedChips(filters.languageFocus, 'languageFocus')
  addAdvancedChips(filters.specialPrograms, 'specialPrograms')
  addAdvancedChips(filters.facilities, 'facilities')
  addAdvancedChips(filters.teachingApproach, 'teachingApproach')

  if (advancedFilters.length === 0) return null
  return (
    <div className="flex items-center gap-3">
      <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
        {t('search.activeFilters')}
      </span>
      <div className="flex-1 overflow-x-auto">
        <div className="flex items-center gap-2 min-w-max py-1">
          {[...advancedByLanguage.entries()].map(([language, levels]) => (
            <span
              key={`lang-${language}`}
              className="inline-flex items-center gap-2 bg-primary-700 text-white text-xs px-2.5 py-1 rounded-full"
            >
              <span>{formatLanguageLabel(language)}:</span>
              <span className="flex items-center gap-1">
                {levels.map(level => (
                  <span key={`${language}-${level}`} className="inline-flex items-center gap-1 bg-white/15 px-2 py-0.5 rounded-full">
                    {formatLanguageLevelLabel(level)}
                    <button
                      type="button"
                      onClick={() => {
                        const option = `${language}:${level}`
                        handleFilterChange({
                          languageFocus: filters.languageFocus.filter(value => value !== option),
                        })
                      }}
                      className="w-4 h-4 rounded-full flex items-center justify-center text-white hover:bg-white/20"
                      aria-label={t('common.close')}
                    >
                      ×
                    </button>
                  </span>
                ))}
              </span>
            </span>
          ))}
          {advancedFilters.filter(filter => !filter.key.startsWith('languageFocus-')).map(filter => (
            <span
              key={filter.key}
              className="inline-flex items-center gap-2 bg-primary-700 text-white text-xs px-2.5 py-1 rounded-full"
            >
              {filter.label}
              <button
                type="button"
                onClick={filter.onRemove}
                className="w-6 h-6 -my-1 -mr-1 rounded-full flex items-center justify-center text-white hover:bg-primary-800 transition-colors"
                aria-label={t('common.close')}
              >
                ×
              </button>
            </span>
          ))}
          {advancedFilters.length > 0 && (
            <button
              type="button"
              onClick={() => handleFilterChange({
                languageFocus: [],
                specialPrograms: [],
                facilities: [],
                teachingApproach: [],
              })}
              className="text-xs text-neutral-500 hover:text-neutral-700 hover:underline whitespace-nowrap"
            >
              {t('advancedFilters.clear')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
