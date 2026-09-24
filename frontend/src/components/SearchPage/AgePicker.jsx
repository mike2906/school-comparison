import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useCountry } from '../../context/CountryContext'
import { fetchSchoolCounts } from '../../api/schools'
import { calculateAgeGroup } from '../../utils/education'
import { getAgeGroupLabel, getAgeGroupsByCategory } from '../../utils/countryConfig'
import {
  FALLBACK_KINDERGARTEN_GROUPS,
  FALLBACK_SCHOOL_GROUPS,
  birthYearsFor,
  categoryForGroup,
  enrolmentYears,
  toSearchSelection,
} from '../../utils/ageSelection'

/**
 * "Who is this for": the child's birth year and enrolment year (the natural question,
 * per the calendar-year rule), or a group picked directly. Changes apply immediately.
 */
function AgePicker({
  ageGroup,
  educationLevel,
  includeCrossover,
  targetYear,
  birthYear,
  open,
  onOpenChange,
  onChange,
  compact = false,
  className = '',
}) {
  const { t, i18n } = useTranslation()
  const { config } = useCountry()
  const containerRef = useRef(null)
  const [counts, setCounts] = useState({})

  const kindergartenGroups = useMemo(() => {
    const groups = config ? getAgeGroupsByCategory(config, 'kindergarten') : []
    return groups.length > 0 ? groups : FALLBACK_KINDERGARTEN_GROUPS
  }, [config])
  const schoolGroups = useMemo(() => {
    const groups = config ? getAgeGroupsByCategory(config, 'school') : []
    return groups.length > 0 ? groups : FALLBACK_SCHOOL_GROUPS
  }, [config])

  const selectedCategory = ageGroup === 'preschool' && educationLevel === 'primary'
    ? 'school'
    : categoryForGroup(ageGroup, kindergartenGroups)
  const [category, setCategory] = useState(selectedCategory || 'kindergarten')

  useEffect(() => {
    if (selectedCategory) setCategory(selectedCategory)
  }, [selectedCategory])

  useEffect(() => {
    if (!open) return
    fetchSchoolCounts().then(setCounts).catch(() => setCounts({}))
  }, [open])

  useEffect(() => {
    if (!open) return
    const handlePointer = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) onOpenChange(false)
    }
    const handleKey = (event) => {
      if (event.key === 'Escape') onOpenChange(false)
    }
    document.addEventListener('mousedown', handlePointer)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('mousedown', handlePointer)
      document.removeEventListener('keydown', handleKey)
    }
  }, [open, onOpenChange])

  const groupLabel = (group) => (config ? getAgeGroupLabel(config, group, i18n.language) : t(`ageGroups.${group}`))
  const years = enrolmentYears()
  const effectiveTarget = targetYear || years[1]

  const apply = (next) => {
    const selection = toSearchSelection({
      ageGroup: next.ageGroup,
      category: next.category,
      includeCrossover: next.includeCrossover,
    })
    onChange({
      ...selection,
      targetYear: next.targetYear ?? effectiveTarget,
      birthYear: next.birthYear ?? null,
    })
  }

  const handleBirthYear = (value) => {
    const year = value ? Number.parseInt(value, 10) : null
    if (!year) {
      apply({ ageGroup: null, targetYear: effectiveTarget })
      return
    }
    const group = calculateAgeGroup(effectiveTarget, year, config)
    apply({ ageGroup: group, category: categoryForGroup(group, kindergartenGroups), targetYear: effectiveTarget, birthYear: year })
  }

  const handleTargetYear = (value) => {
    const year = Number.parseInt(value, 10)
    if (birthYear) {
      const group = calculateAgeGroup(year, birthYear, config)
      apply({ ageGroup: group, category: categoryForGroup(group, kindergartenGroups), targetYear: year, birthYear })
    } else {
      apply({ ageGroup, category: selectedCategory, includeCrossover, targetYear: year })
    }
  }

  const handleGroup = (group) => {
    const next = ageGroup === group && selectedCategory === category ? null : group
    apply({ ageGroup: next, category, targetYear: effectiveTarget })
  }

  // Narrow screens show just the group; the birth year is visible when the picker opens.
  const buttonLabel = ageGroup
    ? (birthYear && !compact
      ? t('agePicker.bornSummary', { year: birthYear, group: groupLabel(ageGroup) })
      : groupLabel(ageGroup))
    : t('agePicker.allAges')

  const groups = category === 'kindergarten' ? kindergartenGroups : schoolGroups

  return (
    <div ref={containerRef} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        aria-haspopup="dialog"
        className={`inline-flex h-10 w-full items-center gap-2 rounded-lg border px-3 text-sm font-medium transition-colors ${
          ageGroup
            ? 'border-primary-300 bg-primary-50 text-primary-800'
            : 'border-neutral-300 bg-white text-neutral-800 hover:bg-neutral-50'
        }`}
      >
        <span aria-hidden="true">👶</span>
        <span className="min-w-0 flex-1 truncate text-left">{buttonLabel}</span>
        <svg className={`h-4 w-4 flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
          <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 10.94l3.71-3.7a.75.75 0 111.06 1.06l-4.24 4.24a.75.75 0 01-1.06 0L5.21 8.29a.75.75 0 01.02-1.08z" clipRule="evenodd" />
        </svg>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-[2150] bg-black/40 md:hidden" aria-hidden="true" onClick={() => onOpenChange(false)} />
          <div
            role="dialog"
            aria-label={t('agePicker.title')}
            className="fixed inset-x-0 bottom-0 z-[2200] max-h-[85dvh] overflow-y-auto rounded-t-2xl bg-white p-5 shadow-2xl md:absolute md:inset-x-auto md:bottom-auto md:left-0 md:top-full md:mt-2 md:w-[440px] md:rounded-xl md:border md:border-neutral-200"
          >
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-neutral-900">{t('agePicker.title')}</h2>
              <button
                type="button"
                onClick={() => onOpenChange(false)}
                className="flex h-9 w-9 items-center justify-center rounded-lg text-neutral-500 hover:bg-neutral-100"
                aria-label={t('common.close')}
              >
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Birth year first: it is how parents think, and it avoids guessing group names. */}
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-neutral-700">{t('filters.birthYear')}</span>
                <select
                  value={birthYear || ''}
                  onChange={(event) => handleBirthYear(event.target.value)}
                  className="h-10 w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm"
                >
                  <option value="">{t('filters.selectBirthYear')}</option>
                  {birthYearsFor(effectiveTarget).map(year => (
                    <option key={year} value={year}>{year}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="mb-1 block text-sm font-medium text-neutral-700">{t('filters.targetYear')}</span>
                <select
                  value={effectiveTarget}
                  onChange={(event) => handleTargetYear(event.target.value)}
                  className="h-10 w-full rounded-lg border border-neutral-300 bg-white px-3 text-sm"
                >
                  {years.map(year => (
                    <option key={year} value={year}>{year}</option>
                  ))}
                </select>
              </label>
            </div>
            {birthYear && ageGroup && (
              <p className="mt-2 text-sm text-primary-800">
                {t('agePicker.calculated', { group: groupLabel(ageGroup), year: effectiveTarget })}
              </p>
            )}
            {birthYear && !ageGroup && (
              <p className="mt-2 text-sm text-neutral-600">{t('agePicker.noGroup')}</p>
            )}

            <div className="my-4 flex items-center gap-3 text-xs text-neutral-400">
              <span className="h-px flex-1 bg-neutral-200" />
              {t('agePicker.orPickGroup')}
              <span className="h-px flex-1 bg-neutral-200" />
            </div>

            <div className="mb-3 inline-flex rounded-lg bg-neutral-100 p-0.5" role="tablist">
              {['kindergarten', 'school'].map(value => (
                <button
                  key={value}
                  type="button"
                  role="tab"
                  aria-selected={category === value}
                  onClick={() => setCategory(value)}
                  className={`h-9 rounded-md px-4 text-sm font-medium ${
                    category === value ? 'bg-white text-neutral-900 shadow-sm' : 'text-neutral-600'
                  }`}
                >
                  {value === 'kindergarten' ? t('landing.categoryKindergarten') : t('landing.categorySchool')}
                </button>
              ))}
            </div>

            <div className="grid grid-cols-2 gap-2">
              {groups.map(group => {
                const isActive = ageGroup === group && selectedCategory === category
                return (
                  <button
                    key={group}
                    type="button"
                    onClick={() => handleGroup(group)}
                    aria-pressed={isActive}
                    className={`min-h-[44px] rounded-lg border px-3 py-2 text-left text-sm transition-colors ${
                      isActive
                        ? 'border-primary-500 bg-primary-50 font-medium text-primary-800'
                        : 'border-neutral-200 text-neutral-700 hover:bg-neutral-50'
                    }`}
                  >
                    {groupLabel(group)}
                    {counts[group] !== undefined && (
                      <span className="ml-1 text-xs text-neutral-500">({counts[group]})</span>
                    )}
                  </button>
                )
              })}
            </div>

            {ageGroup === 'preschool' && (
              <label className="mt-3 flex items-start gap-3 rounded-lg bg-neutral-50 p-3 text-sm text-neutral-700">
                <input
                  type="checkbox"
                  checked={includeCrossover}
                  onChange={() => apply({
                    ageGroup,
                    category: selectedCategory,
                    includeCrossover: !includeCrossover,
                    birthYear,
                  })}
                  className="mt-0.5 h-4 w-4 rounded border-neutral-300 text-primary-600"
                />
                {selectedCategory === 'school'
                  ? t('landing.crossoverCheckboxSchool')
                  : t('landing.crossoverCheckboxKindergarten')}
              </label>
            )}

            <div className="mt-4 flex items-center justify-between gap-3 border-t border-neutral-100 pt-4">
              <button
                type="button"
                onClick={() => apply({ ageGroup: null })}
                disabled={!ageGroup}
                className="h-10 px-2 text-sm font-medium text-neutral-600 hover:text-neutral-900 disabled:opacity-40"
              >
                {t('agePicker.showAll')}
              </button>
              <button
                type="button"
                onClick={() => onOpenChange(false)}
                className="h-10 rounded-lg bg-primary-600 px-5 text-sm font-medium text-white hover:bg-primary-700"
              >
                {t('agePicker.done')}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export default AgePicker
