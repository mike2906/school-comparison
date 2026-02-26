import { useState, useEffect, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { calculateAgeGroup } from '../../utils/education'
import { searchSchools, fetchSchoolCounts } from '../../api/schools'
import { useCountry } from '../../context/CountryContext'
import { getAgeGroupsByCategory, getAgeGroupLabel } from '../../utils/countryConfig'
import { getSchoolName } from '../../utils/i18n'
import { normalizeSchoolList } from '../../utils/schoolAttributes'
import LanguageToggle from '../LanguageToggle/LanguageToggle'

const FALLBACK_KINDERGARTEN_GROUPS = ['nursery', 'first', 'second', 'third', 'preschool']
const FALLBACK_SCHOOL_GROUPS = ['preschool', 'grade_1_4', 'grade_5_7', 'grade_8_12']

const SCHOOL_TYPES = [
  { value: 'state', labelKey: 'schoolTypes.state' },
  { value: 'private', labelKey: 'schoolTypes.private' },
]

const currentYear = new Date().getFullYear()
const YEARS = Array.from({ length: 5 }, (_, i) => currentYear + i)
const MAX_CHILD_AGE = 18

function LandingPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { config, countryCode } = useCountry()

  const kindergartenAgeGroups = config ? getAgeGroupsByCategory(config, 'kindergarten') : FALLBACK_KINDERGARTEN_GROUPS
  const schoolAgeGroups = config ? getAgeGroupsByCategory(config, 'school') : FALLBACK_SCHOOL_GROUPS

  // Read filters from URL params (when navigating back from search)
  const ageGroupParam = searchParams.get('age_group')
  const schoolTypeParam = searchParams.get('school_type')
  const educationLevelParam = searchParams.get('education_level')
  const includeCrossoverParam = searchParams.get('include_crossover') === 'true'
  const targetYearParam = searchParams.get('target_year')

  // Determine category from education_level or age_group
  const kindergartenGroups = ['nursery', 'first', 'second', 'third', 'preschool']
  const initialCategory = educationLevelParam === 'kindergarten' || educationLevelParam === 'nursery'
    ? 'kindergarten'
    : educationLevelParam === 'primary' || educationLevelParam === 'lower_secondary' || educationLevelParam === 'upper_secondary'
    ? 'school'
    : ageGroupParam && kindergartenGroups.includes(ageGroupParam)
    ? 'kindergarten'
    : ageGroupParam
    ? 'school'
    : null

  const [filters, setFilters] = useState({
    category: initialCategory,
    ageGroup: ageGroupParam || null,
    targetYear: targetYearParam ? parseInt(targetYearParam) : currentYear + 1,
    birthYear: null,
    schoolType: schoolTypeParam || null,
    includeCrossover: includeCrossoverParam,
  })

  const [isCalculatorOpen, setIsCalculatorOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [isSearching, setIsSearching] = useState(false)
  const [showSearchDropdown, setShowSearchDropdown] = useState(false)
  const [schoolCounts, setSchoolCounts] = useState({})
  const searchRef = useRef(null)

  const birthYears = useMemo(() => {
    const baseYear = filters.targetYear || currentYear + 1
    return Array.from({ length: MAX_CHILD_AGE + 1 }, (_, i) => baseYear - i)
  }, [filters.targetYear])

  const localizedSearchResults = useMemo(
    () => normalizeSchoolList(searchResults, i18n.language),
    [searchResults, i18n.language]
  )

  // Fetch school counts on mount
  useEffect(() => {
    const loadCounts = async () => {
      try {
        const counts = await fetchSchoolCounts()
        setSchoolCounts(counts)
      } catch (err) {
        console.error('Failed to load school counts:', err)
      }
    }
    loadCounts()
  }, [])

  // Handle search with debounce
  useEffect(() => {
    if (searchQuery.length < 2) {
      setSearchResults([])
      setShowSearchDropdown(false)
      return
    }

    const timer = setTimeout(async () => {
      setIsSearching(true)
      try {
        const results = await searchSchools(searchQuery)
        setSearchResults(results)
        setShowSearchDropdown(true)
      } catch (err) {
        console.error('Search failed:', err)
        setSearchResults([])
      } finally {
        setIsSearching(false)
      }
    }, 300)

    return () => clearTimeout(timer)
  }, [searchQuery])

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (searchRef.current && !searchRef.current.contains(event.target)) {
        setShowSearchDropdown(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleCategorySelect = (category) => {
    setFilters(prev => ({
      ...prev,
      category: prev.category === category ? null : category,
      ageGroup: null,
      includeCrossover: false,
      birthYear: null,
    }))
  }

  const handleAgeGroupSelect = (group) => {
    setFilters(prev => ({
      ...prev,
      ageGroup: prev.ageGroup === group ? null : group,
      includeCrossover: false,
      birthYear: null,
    }))
    // Close calculator when manual selection is used
    if (isCalculatorOpen) {
      setIsCalculatorOpen(false)
    }
  }

  const handleCrossoverToggle = () => {
    setFilters(prev => ({
      ...prev,
      includeCrossover: !prev.includeCrossover,
    }))
  }

  const handleSchoolTypeSelect = (type) => {
    setFilters(prev => ({
      ...prev,
      schoolType: prev.schoolType === type ? null : type,
    }))
  }

  const handleTargetYearChange = (e) => {
    const targetYear = parseInt(e.target.value)
    setFilters(prev => ({ ...prev, targetYear }))

    if (filters.birthYear) {
      const ageGroup = calculateAgeGroup(targetYear, filters.birthYear, config)
      setFilters(prev => ({ ...prev, targetYear, ageGroup }))
    }
  }

  const handleBirthYearChange = (e) => {
    const birthYear = e.target.value ? parseInt(e.target.value) : null
    setFilters(prev => ({ ...prev, birthYear }))

    if (birthYear && filters.targetYear) {
      const ageGroup = calculateAgeGroup(filters.targetYear, birthYear, config)
      // Determine category based on calculated age group
      let category = filters.category
      if (ageGroup && !category) {
        category = kindergartenAgeGroups.includes(ageGroup) ? 'kindergarten' : 'school'
      }
      setFilters(prev => ({ ...prev, birthYear, ageGroup, category }))
    }
  }

  const toggleCalculator = () => {
    setIsCalculatorOpen(prev => !prev)
    // Clear birth year when closing calculator
    if (isCalculatorOpen) {
      setFilters(prev => ({ ...prev, birthYear: null }))
    }
  }

  const handleSearchSelect = (school) => {
    setShowSearchDropdown(false)
    setSearchQuery('')
    navigate(`/schools/${school.id}`)
  }

  const handleUseCalculatedGroup = () => {
    // The age group is already set by the calculator
    // Just close the calculator and clear birth year
    setIsCalculatorOpen(false)
    setFilters(prev => ({ ...prev, birthYear: null }))
  }

  const getEducationLevel = () => {
    if (!filters.category || !filters.ageGroup) return null

    if (filters.category === 'kindergarten') {
      if (filters.ageGroup === 'nursery') return 'nursery'
      return 'kindergarten'
    } else {
      // school category
      // Keep preschool scoped to primary unless crossover is explicitly enabled.
      // For grade bands, age_group is the source of truth and education_level can
      // become stale/inconsistent with imported data.
      if (filters.ageGroup === 'preschool') return 'primary'
      return null
    }
  }

  const handleSearch = () => {
    const params = new URLSearchParams()
    if (filters.ageGroup) {
      params.set('age_group', filters.ageGroup)
    }
    if (filters.schoolType) {
      params.set('school_type', filters.schoolType)
    }
    if (filters.targetYear) {
      params.set('target_year', filters.targetYear.toString())
    }

    const educationLevel = getEducationLevel()
    if (educationLevel) {
      params.set('education_level', educationLevel)
    }
    if (filters.includeCrossover) {
      params.set('include_crossover', 'true')
    }

    const storedLocation = localStorage.getItem('userLocation')
    const hasStoredLocation = Boolean(storedLocation)
    if (window.innerWidth < 768 && !hasStoredLocation) {
      params.set('prompt_location', '1')
    }

    navigate(`/search?${params.toString()}`)
  }

  const hasRequiredFilters = filters.ageGroup || filters.schoolType

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-50 via-white to-primary-50">
      {/* Navigation */}
      <nav className="bg-white/80 backdrop-blur-sm border-b border-neutral-200 sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-6">
          <div className="flex justify-between items-center h-16">
            {/* Logo */}
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-primary-600 flex items-center justify-center shadow-sm">
                <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
                </svg>
              </div>
              <div>
                <h1 className="text-lg font-semibold text-neutral-900 leading-tight">
                  {t('nav.title')}
                </h1>
                <p className="text-xs text-neutral-500 leading-tight">
                  {t('nav.subtitle')}
                </p>
              </div>
            </div>

            {/* Language toggle */}
            <LanguageToggle />
          </div>
        </div>
      </nav>

      {/* Main content */}
      <main className="max-w-3xl mx-auto px-6 py-12 md:py-20">
        {/* Hero text */}
        <div className="text-center mb-12">
          <h2 className="text-3xl md:text-4xl font-bold text-neutral-900 mb-4">
            {t('landing.title')}
          </h2>
          <p className="text-lg text-neutral-600 max-w-xl mx-auto">
            {t('landing.subtitle')}
          </p>
        </div>

        {/* Search by name */}
        <div className="mb-8" ref={searchRef}>
          <div className="relative">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('landing.searchPlaceholder')}
              className="w-full px-5 py-4 pr-12 rounded-xl border-2 border-neutral-200 focus:border-primary-500 focus:ring-2 focus:ring-primary-100 transition-all text-neutral-900 placeholder:text-neutral-400"
            />
            <div className="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none">
              {isSearching ? (
                <div className="w-5 h-5 border-2 border-neutral-300 border-t-primary-500 rounded-full animate-spin" />
              ) : (
                <svg className="w-5 h-5 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                </svg>
              )}
            </div>

            {/* Search dropdown */}
            {showSearchDropdown && localizedSearchResults.length > 0 && (
              <div className="absolute top-full left-0 right-0 mt-2 bg-white rounded-xl border-2 border-neutral-200 shadow-xl overflow-hidden z-10 max-h-96 overflow-y-auto">
                {localizedSearchResults.map(school => (
                  <button
                    key={school.id}
                    onClick={() => handleSearchSelect(school)}
                    className="w-full px-5 py-3 text-left hover:bg-neutral-50 transition-colors border-b border-neutral-100 last:border-b-0"
                  >
                    <div className="font-medium text-neutral-900">{getSchoolName(school, i18n.language)}</div>
                    <div className="text-sm text-neutral-500 mt-0.5">
                      {t(`educationLevels.${school.education_level}`)} • {t(`schoolTypes.${school.school_type}`)}
                    </div>
                  </button>
                ))}
              </div>
            )}

            {showSearchDropdown && localizedSearchResults.length === 0 && searchQuery.length >= 2 && !isSearching && (
              <div className="absolute top-full left-0 right-0 mt-2 bg-white rounded-xl border-2 border-neutral-200 shadow-xl p-4 z-10">
                <p className="text-sm text-neutral-500 text-center">{t('landing.noSearchResults')}</p>
              </div>
            )}
          </div>
        </div>

        {/* Visual separator */}
        <div className="relative mb-8">
          <div className="absolute inset-0 flex items-center">
            <div className="w-full border-t border-neutral-300" />
          </div>
          <div className="relative flex justify-center text-sm">
            <span className="px-4 bg-gradient-to-br from-primary-50 via-white to-primary-50 text-neutral-500">
              {t('landing.orBrowseByCategory')}
            </span>
          </div>
        </div>

        {/* Filter card */}
        <div className="bg-white rounded-2xl shadow-xl border border-neutral-200 overflow-hidden">
          {/* Step 1: Category Selection */}
          <div className="p-6 md:p-8 border-b border-neutral-100">
            <label className="block text-sm font-semibold text-neutral-900 mb-4">
              {t('landing.selectCategory')}
            </label>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <button
                onClick={() => handleCategorySelect('kindergarten')}
                className={`
                  p-6 rounded-xl border-2 transition-all text-left
                  ${filters.category === 'kindergarten'
                    ? 'bg-primary-50 border-primary-500 shadow-md'
                    : 'bg-white border-neutral-200 hover:border-neutral-300 hover:bg-neutral-50'
                  }
                `}
              >
                <div className="flex items-center gap-3 mb-2">
                  <div className="text-3xl">🎨</div>
                  <div>
                    <h3 className={`text-lg font-semibold ${filters.category === 'kindergarten' ? 'text-primary-700' : 'text-neutral-900'}`}>
                      {t('landing.categoryKindergarten')}
                    </h3>
                    <p className="text-sm text-neutral-500">
                      {t('landing.categoryKindergartenSubtitle')}
                    </p>
                  </div>
                </div>
              </button>

              <button
                onClick={() => handleCategorySelect('school')}
                className={`
                  p-6 rounded-xl border-2 transition-all text-left
                  ${filters.category === 'school'
                    ? 'bg-primary-50 border-primary-500 shadow-md'
                    : 'bg-white border-neutral-200 hover:border-neutral-300 hover:bg-neutral-50'
                  }
                `}
              >
                <div className="flex items-center gap-3 mb-2">
                  <div className="text-3xl">📚</div>
                  <div>
                    <h3 className={`text-lg font-semibold ${filters.category === 'school' ? 'text-primary-700' : 'text-neutral-900'}`}>
                      {t('landing.categorySchool')}
                    </h3>
                    <p className="text-sm text-neutral-500">
                      {t('landing.categorySchoolSubtitle')}
                    </p>
                  </div>
                </div>
              </button>
            </div>
          </div>

          {/* Step 2: Age Group Selection (shown after category is selected) */}
          {filters.category && (
            <div className={`p-6 md:p-8 border-b border-neutral-100 transition-opacity ${isCalculatorOpen ? 'opacity-40' : ''}`}>
              <label className="block text-sm font-semibold text-neutral-900 mb-4">
                {t('landing.selectAgeGroup')}
              </label>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {(filters.category === 'kindergarten' ? kindergartenAgeGroups : schoolAgeGroups).map(group => (
                  <button
                    key={group}
                    onClick={() => handleAgeGroupSelect(group)}
                    disabled={isCalculatorOpen}
                    className={`
                      px-4 py-3 text-sm rounded-xl border-2 transition-all text-center
                      ${isCalculatorOpen ? 'cursor-not-allowed' : ''}
                      ${filters.ageGroup === group
                        ? 'bg-primary-50 border-primary-500 text-primary-700 font-medium'
                        : 'bg-white border-neutral-200 text-neutral-700 hover:border-neutral-300 hover:bg-neutral-50'
                      }
                    `}
                  >
                    <div>{config ? getAgeGroupLabel(config, group, i18n.language) : t(`ageGroups.${group}`)}</div>
                    {schoolCounts[group] !== undefined && (
                      <div className="text-xs mt-0.5 opacity-70">
                        ({schoolCounts[group]})
                      </div>
                    )}
                  </button>
                ))}
              </div>

              {/* Crossover checkbox (shown only for preschool age and not when calculator is active) */}
              {filters.ageGroup === 'preschool' && !isCalculatorOpen && !filters.birthYear && (
                <div className="mt-4 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <label className="flex items-start gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={filters.includeCrossover}
                      onChange={handleCrossoverToggle}
                      className="mt-0.5 w-4 h-4 text-primary-600 border-neutral-300 rounded focus:ring-primary-500"
                    />
                    <span className="text-sm text-neutral-700">
                      {filters.category === 'kindergarten'
                        ? t('landing.crossoverCheckboxKindergarten')
                        : t('landing.crossoverCheckboxSchool')
                      }
                    </span>
                  </label>
                </div>
              )}
            </div>
          )}

          {/* Calculate from birth year - Collapsible Accordion */}
          <div className="border-b border-neutral-100">
            {/* Accordion Header */}
            <button
              onClick={toggleCalculator}
              className="w-full p-6 md:p-8 bg-neutral-50/50 hover:bg-neutral-100/50 transition-colors text-left flex items-center justify-between"
            >
              <span className="text-sm text-neutral-600">
                {t('landing.calculatorToggle')}
              </span>
              <svg
                className={`w-5 h-5 text-neutral-500 transition-transform ${isCalculatorOpen ? 'rotate-180' : ''}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {/* Accordion Content */}
            {isCalculatorOpen && (
              <div className="p-6 md:p-8 bg-neutral-50/50 border-t border-neutral-200">
                <div className="grid grid-cols-2 gap-4 max-w-md mx-auto">
                  <div>
                    <label className="block text-sm font-medium text-neutral-700 mb-1.5">
                      {t('filters.targetYear')}
                    </label>
                    <select
                      value={filters.targetYear || ''}
                      onChange={handleTargetYearChange}
                      className="w-full border border-neutral-300 rounded-lg px-3 py-2.5 text-sm bg-white focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-shadow"
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
                      className="w-full border border-neutral-300 rounded-lg px-3 py-2.5 text-sm bg-white focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-shadow"
                    >
                      <option value="">{t('filters.selectBirthYear')}</option>
                      {birthYears.map(year => (
                        <option key={year} value={year}>{year}</option>
                      ))}
                    </select>
                  </div>
                </div>
                {filters.birthYear && filters.ageGroup && (
                  <div className="mt-4 p-4 bg-primary-50 rounded-lg border border-primary-100 max-w-md mx-auto">
                    <p className="text-sm text-primary-800 text-center mb-3">
                      <span className="font-medium">{t('filters.calculatedGroup')}:</span>{' '}
                      {config ? getAgeGroupLabel(config, filters.ageGroup, i18n.language) : t(`ageGroups.${filters.ageGroup}`)}
                    </p>
                    <button
                      onClick={handleUseCalculatedGroup}
                      className="w-full py-2 px-4 bg-primary-600 text-white text-sm font-medium rounded-lg hover:bg-primary-700 transition-colors"
                    >
                      {t('landing.useThisSelection')}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* School Type Selection */}
          <div className="p-6 md:p-8 border-b border-neutral-100">
            <label className="block text-sm font-semibold text-neutral-900 mb-4">
              {t('landing.selectSchoolType')}
            </label>
            <div className="flex flex-wrap gap-3">
              {SCHOOL_TYPES.map(({ value, labelKey }) => (
                <button
                  key={value}
                  onClick={() => handleSchoolTypeSelect(value)}
                  className={`
                    px-6 py-3 text-sm rounded-xl border-2 transition-all
                    ${filters.schoolType === value
                      ? 'bg-primary-50 border-primary-500 text-primary-700 font-medium'
                      : 'bg-white border-neutral-200 text-neutral-700 hover:border-neutral-300 hover:bg-neutral-50'
                    }
                  `}
                >
                  {t(labelKey)}
                </button>
              ))}
            </div>
          </div>

          {/* Search button */}
          <div className="p-6 md:p-8 bg-neutral-50">
            <button
              onClick={handleSearch}
              disabled={!hasRequiredFilters}
              className={`
                w-full py-4 rounded-xl text-lg font-semibold transition-all
                ${hasRequiredFilters
                  ? 'bg-primary-600 text-white hover:bg-primary-700 shadow-lg hover:shadow-xl transform hover:-translate-y-0.5'
                  : 'bg-neutral-300 text-neutral-500 cursor-not-allowed'
                }
              `}
            >
              {t('landing.searchButton')}
            </button>
            {!hasRequiredFilters && (
              <p className="text-center text-sm text-neutral-500 mt-3">
                {t('landing.selectFiltersHint')}
              </p>
            )}
          </div>
        </div>

        {/* Features */}
        <div className="mt-12 grid grid-cols-1 md:grid-cols-3 gap-6">
          <FeatureCard
            icon="🗺️"
            title={t('landing.featureMapTitle')}
            description={t('landing.featureMapDesc')}
          />
          <FeatureCard
            icon="📊"
            title={t('landing.featureCompareTitle')}
            description={t('landing.featureCompareDesc')}
          />
          <FeatureCard
            icon="📈"
            title={t('landing.featureResultsTitle')}
            description={t('landing.featureResultsDesc')}
          />
        </div>
      </main>
    </div>
  )
}

function FeatureCard({ icon, title, description }) {
  return (
    <div className="text-center p-6">
      <div className="text-4xl mb-3">{icon}</div>
      <h3 className="font-semibold text-neutral-900 mb-2">{title}</h3>
      <p className="text-sm text-neutral-600">{description}</p>
    </div>
  )
}

export default LandingPage
