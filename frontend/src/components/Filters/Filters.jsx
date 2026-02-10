import { useTranslation } from 'react-i18next'
import { calculateAgeGroup } from '../../utils/education'
import { useCountry } from '../../context/CountryContext'
import { getAgeGroupKeys, getAgeGroupLabel } from '../../utils/countryConfig'

const FALLBACK_AGE_GROUPS = [
  'nursery',
  'first',
  'second',
  'third',
  'preschool',
  'grade_1_4',
  'grade_5_7',
  'grade_8_12',
]

const currentYear = new Date().getFullYear()
const YEARS = Array.from({ length: 5 }, (_, i) => currentYear + i)
const BIRTH_YEARS = Array.from({ length: 12 }, (_, i) => currentYear - i - 1)

function Filters({ filters, onFilterChange }) {
  const { t, i18n } = useTranslation()
  const { config } = useCountry()

  const ageGroups = config ? getAgeGroupKeys(config) : FALLBACK_AGE_GROUPS

  const handleTargetYearChange = (e) => {
    const targetYear = parseInt(e.target.value)
    onFilterChange({ targetYear })

    if (filters.birthYear) {
      const ageGroup = calculateAgeGroup(targetYear, filters.birthYear, config)
      onFilterChange({ targetYear, ageGroup })
    }
  }

  const handleBirthYearChange = (e) => {
    const birthYear = e.target.value ? parseInt(e.target.value) : null
    onFilterChange({ birthYear })

    if (birthYear && filters.targetYear) {
      const ageGroup = calculateAgeGroup(filters.targetYear, birthYear, config)
      onFilterChange({ birthYear, ageGroup })
    }
  }

  const handleAgeGroupChange = (e) => {
    const ageGroup = e.target.value || null
    onFilterChange({ ageGroup, birthYear: null })
  }

  const handleClear = () => {
    onFilterChange({
      ageGroup: null,
      targetYear: currentYear + 1,
      birthYear: null,
      schoolType: null,
    })
  }

  const hasActiveFilters = filters.ageGroup || filters.birthYear || filters.schoolType

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-neutral-900">{t('filters.title')}</h2>
        {hasActiveFilters && (
          <button
            onClick={handleClear}
            className="text-sm text-primary-600 hover:text-primary-700 font-medium transition-colors"
          >
            {t('filters.clear')}
          </button>
        )}
      </div>

      {/* Quick age group selection */}
      <div>
        <label className="block text-sm font-medium text-neutral-700 mb-2">
          {t('filters.ageGroup')}
        </label>
        <div className="grid grid-cols-2 gap-2">
          {ageGroups.slice(0, 6).map(group => (
            <button
              key={group}
              onClick={() => onFilterChange({ ageGroup: filters.ageGroup === group ? null : group, birthYear: null })}
              className={`
                px-3 py-2 text-sm rounded-lg border transition-all
                ${filters.ageGroup === group
                  ? 'bg-primary-50 border-primary-500 text-primary-700 font-medium'
                  : 'bg-white border-neutral-200 text-neutral-700 hover:border-neutral-300 hover:bg-neutral-50'
                }
              `}
            >
              {config ? getAgeGroupLabel(config, group, i18n.language) : t(`ageGroups.${group}`)}
            </button>
          ))}
        </div>
        {/* School grades in a separate row */}
        {ageGroups.length > 6 && (
        <div className="grid grid-cols-2 gap-2 mt-2">
          {ageGroups.slice(6).map(group => (
            <button
              key={group}
              onClick={() => onFilterChange({ ageGroup: filters.ageGroup === group ? null : group, birthYear: null })}
              className={`
                px-3 py-2 text-sm rounded-lg border transition-all
                ${filters.ageGroup === group
                  ? 'bg-primary-50 border-primary-500 text-primary-700 font-medium'
                  : 'bg-white border-neutral-200 text-neutral-700 hover:border-neutral-300 hover:bg-neutral-50'
                }
              `}
            >
              {config ? getAgeGroupLabel(config, group, i18n.language) : t(`ageGroups.${group}`)}
            </button>
          ))}
        </div>
        )}
      </div>

      {/* Divider */}
      <div className="relative">
        <div className="absolute inset-0 flex items-center">
          <div className="w-full border-t border-neutral-200" />
        </div>
        <div className="relative flex justify-center text-xs">
          <span className="px-2 bg-white text-neutral-500">{t('filters.orCalculate')}</span>
        </div>
      </div>

      {/* Calculate from birth year */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-sm font-medium text-neutral-700 mb-1.5">
            {t('filters.targetYear')}
          </label>
          <select
            value={filters.targetYear || ''}
            onChange={handleTargetYearChange}
            className="w-full border border-neutral-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-shadow"
          >
            {YEARS.map(year => (
              <option key={year} value={year}>{year}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-neutral-700 mb-1.5">
            {t('filters.birthYear')}
          </label>
          <select
            value={filters.birthYear || ''}
            onChange={handleBirthYearChange}
            className="w-full border border-neutral-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-shadow"
          >
            <option value="">{t('filters.selectBirthYear')}</option>
            {BIRTH_YEARS.map(year => (
              <option key={year} value={year}>{year}</option>
            ))}
          </select>
        </div>
      </div>

      {/* Calculated result */}
      {filters.birthYear && filters.ageGroup && (
        <div className="p-3 bg-primary-50 rounded-lg border border-primary-100">
          <p className="text-sm text-primary-800">
            <span className="font-medium">{t('filters.calculatedGroup')}:</span>{' '}
            {config ? getAgeGroupLabel(config, filters.ageGroup, i18n.language) : t(`ageGroups.${filters.ageGroup}`)}
          </p>
        </div>
      )}

      {/* School type filter */}
      <div>
        <label className="block text-sm font-medium text-neutral-700 mb-2">
          {t('filters.schoolType')}
        </label>
        <div className="grid grid-cols-2 gap-2">
          <button
            onClick={() => onFilterChange({ schoolType: filters.schoolType === 'state' ? null : 'state' })}
            className={`
              px-3 py-2 text-sm rounded-lg border transition-all
              ${filters.schoolType === 'state'
                ? 'bg-primary-50 border-primary-500 text-primary-700 font-medium'
                : 'bg-white border-neutral-200 text-neutral-700 hover:border-neutral-300 hover:bg-neutral-50'
              }
            `}
          >
            {t('schoolTypes.state')}
          </button>
          <button
            onClick={() => onFilterChange({ schoolType: filters.schoolType === 'private' ? null : 'private' })}
            className={`
              px-3 py-2 text-sm rounded-lg border transition-all
              ${filters.schoolType === 'private'
                ? 'bg-primary-50 border-primary-500 text-primary-700 font-medium'
                : 'bg-white border-neutral-200 text-neutral-700 hover:border-neutral-300 hover:bg-neutral-50'
              }
            `}
          >
            {t('schoolTypes.private')}
          </button>
        </div>
      </div>
    </div>
  )
}

export default Filters
