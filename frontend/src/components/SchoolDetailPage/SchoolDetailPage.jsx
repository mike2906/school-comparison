import { Suspense, lazy, useEffect, useMemo, useState } from 'react'
import { Link, useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Layout from '../Layout/Layout'
import { fetchSchool, fetchExamAverages } from '../../api/schools'
import { getSchoolName, getAddress, getSummary } from '../../utils/i18n'
import { normalizeSchool } from '../../utils/schoolAttributes'
import { getLastSearchUrl } from '../../utils/searchViewState'
import { examTypeForAgeGroup } from '../../utils/nvo'
import { curatedRequirement, usesSofiaKindergartenSystem } from '../../utils/admission'
import { schoolLevelLabel, schoolTypeLabel } from '../../utils/levelLabel'
import { useCountry } from '../../context/CountryContext'
import { useCompare } from '../../context/CompareContext'
import {
  getStatusInfo,
  parseCoordinate,
  getLastAdmittedPoints,
  getMinNvoScore,
  getAdmissionRequirement,
  getNvoDetail,
  getAmenityFlags,
  getLanguageLabel,
  getOptionLabel,
  normalizeLanguageFocus,
  hexToRgba,
  getExamTypeForEducationLevel,
  getAvailableExamTypes,
  getExamTypeLabel,
} from './helpers'
import KeyFacts from './KeyFacts'
import LocationMap, { directionsUrl } from './LocationMap'
import PricingSection from './PricingSection'
import SchoolActions from './SchoolActions'
import TagList from './TagList'
import PhoneLinks from './PhoneLinks'

// The chart library is large and only schools with exam results need it. If the chunk
// cannot be loaded (a tab that outlived a deploy), the page stays usable without the chart.
const NvoTimelineChart = lazy(() => import('./NvoTimelineChart').catch(() => ({ default: () => null })))

// Official admission system for Sofia municipal kindergartens (the source of the
// kindergarten admission thresholds; see AGENTS.md).
const SOFIA_KINDERGARTEN_ADMISSION_URL = 'https://kg.sofia.bg'

// In the desktop search side panel there is no page chrome around the detail.
function EmbeddedShell({ children }) {
  return <div className="bg-neutral-100 min-h-full">{children}</div>
}

/**
 * A school's detail. Rendered as the `/schools/:id` page, or `embedded` inside the search
 * page's side panel, where "Back" closes the panel instead of leaving the page.
 */
function SchoolDetailPage({ schoolId = null, embedded = false, onClose = null }) {
  const params = useParams()
  const [searchParams] = useSearchParams()
  // The age group the parent searched for: the search URL's own (side panel) or the
  // ?group= the card's link passes to the full page. It picks which NVO exam leads.
  const preferredAgeGroup = searchParams.get('age_group') || searchParams.get('group')
  const id = schoolId ?? params.id
  const navigate = useNavigate()
  const Shell = embedded ? EmbeddedShell : Layout
  const handleBack = () => {
    if (embedded && onClose) {
      onClose()
      return
    }
    // Opened from a shared link: there is no in-app page to go back to.
    if (window.history.state?.idx === 0) {
      navigate(getLastSearchUrl())
      return
    }
    navigate(-1)
  }
  const { t, i18n } = useTranslation()
  const { config: countryConfig } = useCountry()
  const { addToCompare, removeFromCompare, isInCompare, canAddMore, compareList } = useCompare()
  const [rawSchool, setRawSchool] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeExamType, setActiveExamType] = useState(null)
  const [selectedSubjects, setSelectedSubjects] = useState(['math', 'bulgarian'])
  const [examAverages, setExamAverages] = useState(null)

  const formatAmount = (value) => {
    if (value == null || Number.isNaN(value)) return null
    return new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 0 }).format(value)
  }

  useEffect(() => {
    const loadSchool = async () => {
      setLoading(true)
      setError(null)
      try {
        // Load school data first; exam averages are best-effort
        const data = await fetchSchool(id)

        setRawSchool(data)

        try {
          const averagesData = await fetchExamAverages()
          setExamAverages(averagesData)
        } catch (err) {
          setExamAverages(null)
        }

        // Set default active exam type
        const availableTypes = getAvailableExamTypes(data.exam_results)
        if (availableTypes.length > 0) {
          // The searched stage's exam, else the school's own level, else the first available
          const preferredType = examTypeForAgeGroup(preferredAgeGroup)
          const defaultType = preferredType && availableTypes.includes(preferredType)
            ? preferredType
            : getExamTypeForEducationLevel(data.education_level)
          setActiveExamType(
            availableTypes.includes(defaultType) ? defaultType : availableTypes[0]
          )
        }
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    loadSchool()
  }, [id])

  const school = useMemo(
    () => normalizeSchool(rawSchool, i18n.language),
    [rawSchool, i18n.language]
  )

  // Per-school browser tab title; the previous title comes back on leaving the page.
  useEffect(() => {
    if (!school) return undefined
    const previousTitle = document.title
    document.title = `${getSchoolName(school, i18n.language)} · ${t('nav.title')}`
    return () => {
      document.title = previousTitle
    }
  }, [school, i18n.language, t])

  if (loading) {
    return (
      <Shell>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="flex items-center gap-3">
            <svg className="animate-spin h-8 w-8 text-primary-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span className="text-lg text-neutral-600">{t('common.loading')}</span>
          </div>
        </div>
      </Shell>
    )
  }

  if (error || !school) {
    return (
      <Shell>
        <div className="max-w-4xl mx-auto px-6 py-12">
          <div className="text-center">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-red-100 flex items-center justify-center">
              <svg className="w-8 h-8 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-neutral-900 mb-2">{t('schools.notFound')}</h2>
            <p className="text-neutral-600 mb-6">{error || t('schools.notFoundDesc')}</p>
            <button
              onClick={handleBack}
              className="px-6 py-3 bg-primary-700 text-white rounded-lg hover:bg-primary-800 transition-colors"
            >
              {t('common.goBack')}
            </button>
          </div>
        </div>
      </Shell>
    )
  }

  const summary = getSummary(school, i18n.language, 'long')
  const schoolName = getSchoolName(school, i18n.language)
  const statusInfo = getStatusInfo(school, t)
  const inCompare = isInCompare(school.id)
  const attributes = school.attributes || {}
  const pricing = school.pricing || []
  const locations = school.locations || []
  // One entry per institution: the API lists each shared pin.
  const samePlace = (school.same_place || []).filter((other, index, all) => all.findIndex(item => item.id === other.id) === index)
  const samePlaceIds = new Set(samePlace.map(other => other.id))
  const continuedFrom = (school.continued_from || []).filter(kindergarten => !samePlaceIds.has(kindergarten.id))
  const primaryLocation = locations.find(l => l.is_primary) || locations[0]
  const nvoDetail = getNvoDetail(school, t, preferredAgeGroup)
  const availableExamTypes = getAvailableExamTypes(school.exam_results)
  const keyFactsExamType = nvoDetail?.examType || availableExamTypes[availableExamTypes.length - 1] || null
  // The global CompareBar is fixed to the bottom while the compare list is non-empty.
  const compareBarVisible = compareList.length > 0

  // Check if we have various data to display
  const hasExamResults = nvoDetail != null
  const hasPricing = school.school_type !== 'state' && pricing.length > 0
  const hasLanguageInfo = normalizeLanguageFocus(attributes.language_focus).length > 0 || (attributes.languages_of_instruction || []).length > 0
  const hasTeachingApproach = (attributes.teaching_approach || []).length > 0
  const hasAfterSchool = locations.some(loc => (loc.age_group_shifts || []).some(shift => shift.has_organised_groups))
  const amenityFlags = getAmenityFlags(attributes, hasAfterSchool)
  const hasFacilities = Object.values(amenityFlags).some(Boolean) || (attributes.facilities || []).length > 0
  const hasSpecialPrograms = (attributes.special_programs || []).length > 0 || (attributes.activities_offered || []).length > 0
  const hasAtAGlance = Boolean(
    school.num_pupils != null ||
    attributes.class_size != null ||
    attributes.teacher_student_ratio ||
    attributes.school_hours ||
    attributes.established_year
  )

  const statusStyle = statusInfo ? {
    backgroundColor: statusInfo.color,
    boxShadow: `0 0 8px ${hexToRgba(statusInfo.color, 0.4)}`,
  } : null

  const handleCompareClick = () => {
    if (inCompare) {
      removeFromCompare(school.id)
    } else {
      addToCompare(school)
    }
  }

  const handleShare = async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: schoolName,
          text: summary || `${schoolName} - ${schoolTypeLabel(school, t)}`,
          url: window.location.href,
        })
      } catch (err) {
        if (err.name !== 'AbortError') {
          console.error('Share failed:', err)
        }
      }
    } else {
      // Fallback: copy to clipboard
      try {
        await navigator.clipboard.writeText(window.location.href)
        alert(t('compare.shareCopied'))
      } catch (err) {
        console.error('Copy failed:', err)
      }
    }
  }

  const handleSubjectToggle = (subject) => {
    // Must keep at least one subject selected
    if (selectedSubjects.length === 1 && selectedSubjects.includes(subject)) {
      return
    }

    setSelectedSubjects(prev =>
      prev.includes(subject)
        ? prev.filter(s => s !== subject)
        : [...prev, subject]
    )
  }

  return (
    <Shell>
      <div className={`max-w-5xl mx-auto px-4 md:px-6 py-6 md:py-8 ${compareBarVisible ? 'pb-40' : 'pb-24 md:pb-8'}`}>
        {/* Back Button (the side panel has its own close control) */}
        <button
          onClick={handleBack}
          className={`${embedded ? 'hidden' : 'flex'} items-center gap-2 text-sm text-neutral-600 hover:text-neutral-900 mb-6 transition-colors`}
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          {t('common.back')}
        </button>

        {/* Hero Section */}
        <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-10 mb-6">
          <div className="flex items-start gap-3 mb-4">
            {statusInfo && (
              <span
                className={`h-3 w-3 rounded-full mt-2 flex-shrink-0 ${statusInfo.key === 'accepting' ? 'animate-pulse' : ''}`}
                style={statusStyle}
                aria-label={statusInfo.label}
                role="img"
              />
            )}
            <div className="flex-1">
              <h1 className="text-3xl md:text-4xl lg:text-5xl font-bold text-neutral-900 leading-tight">{schoolName}</h1>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 mb-6">
            <span className={`
              px-4 py-2 rounded-lg text-sm font-semibold
              ${school.school_type === 'state'
                ? 'bg-primary-50 text-primary-700 border border-primary-200'
                : school.school_type === 'private'
                ? 'bg-violet-50 text-violet-700 border border-violet-200'
                : 'bg-blue-50 text-blue-700 border border-blue-200'
              }
            `}>
              {schoolTypeLabel(school, t)}
            </span>
            <span className="px-4 py-2 rounded-lg text-sm font-semibold bg-neutral-100 text-neutral-700 border border-neutral-200">
              {schoolLevelLabel(school, t, countryConfig)}
            </span>
            {statusInfo && (
              <span
                className="px-4 py-2 rounded-lg text-sm font-semibold border"
                style={{
                  backgroundColor: hexToRgba(statusInfo.color, 0.1),
                  borderColor: hexToRgba(statusInfo.color, 0.35),
                  color: statusInfo.color,
                }}
              >
                {statusInfo.label}
              </span>
            )}
          </div>

          {/* Computed server-side: a kindergarten and its school next door are one place. */}
          {samePlace.length > 0 && (
            <nav className="flex flex-wrap items-center gap-2 mb-6" aria-label={t('schools.samePlaceHere')}>
              <span className="text-sm text-neutral-600">{t('schools.samePlaceHere')}</span>
              <span
                aria-current="page"
                className="px-3 py-1.5 rounded-full text-sm font-semibold bg-primary-700 text-white"
              >
                {schoolLevelLabel(school, t, countryConfig)}
              </span>
              {samePlace.map(other => (
                <Link
                  key={other.id}
                  to={`/schools/${other.id}`}
                  title={getSchoolName(other, i18n.language)}
                  className="px-3 py-1.5 rounded-full text-sm font-semibold border border-primary-300 text-primary-700 hover:bg-primary-50"
                >
                  {schoolLevelLabel(other, t, countryConfig)}
                </Link>
              ))}
            </nav>
          )}

          {/* Computed server-side: the one school this kindergarten leads to, elsewhere. */}
          {school.continues_to && !samePlaceIds.has(school.continues_to.id) && (
            <p className="text-neutral-700 mb-6">
              {t('schools.continuesTo')}{' '}
              <Link
                to={`/schools/${school.continues_to.id}`}
                className="font-semibold text-primary-700 hover:text-primary-800 underline underline-offset-2"
              >
                {getSchoolName(school.continues_to, i18n.language)}
              </Link>{' '}
              {t('schools.continuesToSchool')}
            </p>
          )}

          {/* ...and the same link from the school's side. */}
          {continuedFrom.length > 0 && (
            <p className="text-neutral-700 mb-6">
              {t('schools.continuedFrom')}{' '}
              {continuedFrom.map((kindergarten, index) => (
                <span key={kindergarten.id}>
                  {index > 0 && ', '}
                  <Link
                    to={`/schools/${kindergarten.id}`}
                    className="font-semibold text-primary-700 hover:text-primary-800 underline underline-offset-2"
                  >
                    {getSchoolName(kindergarten, i18n.language)}
                  </Link>
                </span>
              ))}
            </p>
          )}

          {summary && (
            <div className="prose max-w-none mb-6">
              <p className="text-neutral-700 leading-relaxed text-lg">{summary}</p>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-3">
            {school.website_url && (
              <a
                href={school.website_url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-4 py-2 bg-primary-50 text-primary-700 rounded-lg hover:bg-primary-100 transition-colors border border-primary-200"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                </svg>
                {t('schools.website')}
              </a>
            )}
            <PhoneLinks
              phone={primaryLocation?.phone}
              className="inline-flex items-center gap-2 px-4 py-2 bg-neutral-50 text-neutral-700 rounded-lg hover:bg-neutral-100 transition-colors border border-neutral-200"
            />
            {/* On mobile the actions live in the sticky bottom bar, unless the compare
                bar is showing there. */}
            <div className={`${compareBarVisible ? 'flex' : 'hidden md:flex'} md:ml-auto`}>
              <SchoolActions
                onShare={handleShare}
                onCompare={handleCompareClick}
                inCompare={inCompare}
                canAddMore={canAddMore}
              />
            </div>
          </div>
        </div>

        <KeyFacts school={school} examAverages={examAverages} nvoExamType={keyFactsExamType} />

        {/* Price is what a private-school parent decides on first. */}
        {hasPricing && <PricingSection pricing={pricing} />}

        {/* At a Glance Section */}
        {hasAtAGlance && (
          <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
          <h2 className="text-2xl font-bold text-neutral-900 mb-6">{t('schools.atAGlance')}</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
            {/* Total Students */}
            {school.num_pupils != null && (
              <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                <svg className="w-8 h-8 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z" />
                </svg>
                <div className="text-center">
                  <div className="text-2xl font-bold text-neutral-900">{formatAmount(school.num_pupils)}</div>
                  <div className="text-xs text-neutral-600 mt-1">{t('schools.totalStudents')}</div>
                </div>
              </div>
            )}

            {/* Average Class Size */}
            {attributes.class_size != null && (
              <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                <svg className="w-8 h-8 text-emerald-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
                </svg>
                <div className="text-center">
                  <div className="text-2xl font-bold text-neutral-900">{formatAmount(attributes.class_size)}</div>
                  <div className="text-xs text-neutral-600 mt-1">{t('schools.avgClassSize')}</div>
                </div>
              </div>
            )}

            {/* Teacher:Student Ratio */}
            {attributes.teacher_student_ratio && (
              <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                <svg className="w-8 h-8 text-blue-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
                </svg>
                <div className="text-center">
                  <div className="text-2xl font-bold text-neutral-900">{attributes.teacher_student_ratio}</div>
                  <div className="text-xs text-neutral-600 mt-1">{t('schools.teacherRatio')}</div>
                </div>
              </div>
            )}

            {/* School Hours */}
            {attributes.school_hours && (
              <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                <svg className="w-8 h-8 text-amber-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
                <div className="text-center">
                  <div className="text-sm font-bold text-neutral-900">{attributes.school_hours}</div>
                  <div className="text-xs text-neutral-600 mt-1">{t('schools.schoolHours')}</div>
                </div>
              </div>
            )}

            {/* Languages are in the key-facts strip. */}

            {/* Established Year */}
            {attributes.established_year && (
              <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                <svg className="w-8 h-8 text-teal-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                <div className="text-center">
                  <div className="text-2xl font-bold text-neutral-900">{attributes.established_year}</div>
                  <div className="text-xs text-neutral-600 mt-1">{t('schools.established')}</div>
                </div>
              </div>
            )}
          </div>
          </div>
        )}

        {/* Locations & Admission Requirements */}
        {locations.length > 0 && (
          <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
            <h2 className="text-2xl font-bold text-neutral-900 mb-6">{t('schools.locationsAndEnrollment')}</h2>
            <LocationMap locations={locations} />
            {usesSofiaKindergartenSystem(school) && (
              <p className="text-sm text-neutral-600 mb-6">
                {t('schoolDetail.officialAdmission')}{' '}
                <a
                  href={SOFIA_KINDERGARTEN_ADMISSION_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium text-primary-700 underline hover:text-primary-800"
                >
                  kg.sofia.bg
                </a>
              </p>
            )}
            <div className="space-y-6">
              {locations.map((location, idx) => {
                const address = getAddress(location, i18n.language)
                const ageGroups = Array.isArray(location.age_groups) ? location.age_groups : [location.age_group]
                const validAgeGroups = ageGroups.filter(Boolean)
                const lat = parseCoordinate(location.lat)
                const lng = parseCoordinate(location.lng)
                const hasCoords = Number.isFinite(lat) && Number.isFinite(lng)

                // Get admission requirement for this location
                let admissionText = null
                if (school.school_type === 'state' && school.education_level === 'kindergarten') {
                  const lastAdmitted = getLastAdmittedPoints(school.admission_info, validAgeGroups[0])
                  if (lastAdmitted) {
                    admissionText = t('schoolCard.admissions.lastAdmitted', {
                      points: lastAdmitted.points,
                      year: lastAdmitted.year,
                      pointsLabel: t('admission.points'),
                    })
                  }
                } else if (school.school_type === 'state' && school.education_level === 'upper_secondary') {
                  const minScore = getMinNvoScore(school.admission_info)
                  if (minScore) {
                    admissionText = t('schoolCard.admissions.minScore', {
                      score: minScore.score,
                      year: minScore.year,
                    })
                  }
                } else if (school.school_type === 'private' || school.school_type === 'international') {
                  const requirement = getAdmissionRequirement(
                    curatedRequirement(school.admission_info, i18n.language) || attributes.entry_requirements,
                    t
                  )
                  if (requirement) {
                    admissionText = `${requirement.icon} ${requirement.text}`
                  }
                }

                return (
                  <div key={location.id || idx} className="border-l-4 border-primary-500 pl-6 py-2">
                    <div className="flex items-start justify-between gap-4 mb-3">
                      <div>
                        <h3 className="font-semibold text-lg text-neutral-900 mb-1">
                          {validAgeGroups.map(group => t(`ageGroups.${group}`)).join(', ')}
                        </h3>
                        {location.is_primary && (
                          <span className="inline-block px-2 py-0.5 bg-primary-100 text-primary-700 text-xs font-medium rounded mb-2">
                            {t('schools.primaryLocation')}
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="space-y-3 text-sm">
                      {address && (
                        <div className="flex items-start gap-2 text-neutral-600">
                          <svg className="w-4 h-4 text-neutral-400 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                          </svg>
                          <span>
                            {address}
                            {hasCoords && location.coordinates_approximate && (
                              <span className="block text-xs text-neutral-500 mt-0.5">
                                {t('schoolDetail.approximateLocation')}
                              </span>
                            )}
                          </span>
                        </div>
                      )}

                      {location.phone && (
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                          <PhoneLinks
                            phone={location.phone}
                            className="inline-flex items-center gap-2 text-neutral-600 hover:text-primary-700 [&>svg]:text-neutral-400"
                          />
                        </div>
                      )}

                      {admissionText && (
                        <div className="bg-primary-50 border border-primary-200 rounded-lg p-3 mt-3">
                          <div className="flex items-start gap-2">
                            <span className="text-base">🎯</span>
                            <div>
                              <div className="font-medium text-neutral-900 text-sm mb-1">{t('schools.admissionRequirements')}</div>
                              <div className="text-neutral-700">{admissionText}</div>
                            </div>
                          </div>
                        </div>
                      )}

                      {(location.age_group_shifts || []).length > 0 && (
                        <div className="mt-3 space-y-2">
                          {location.age_group_shifts.map((shift, shiftIdx) => (
                            <div key={shiftIdx} className="flex items-center gap-2 text-neutral-600">
                              <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-primary-500" aria-hidden="true" />
                              <span>
                                {t(`ageGroups.${shift.age_group}`)}
                                {shift.shift ? ` • ${t(`shifts.${shift.shift}`)}` : ''}
                                {shift.has_organised_groups ? ` • ${t('schools.organisedGroups')}` : ''}
                              </span>
                            </div>
                          ))}
                        </div>
                      )}

                      {hasCoords && (
                        <a
                          href={directionsUrl(lat, lng)}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-3 inline-flex items-center gap-2 px-3 py-1.5 text-sm bg-neutral-50 text-neutral-700 rounded-lg hover:bg-neutral-100 transition-colors border border-neutral-200"
                        >
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
                          </svg>
                          {t('schoolDetail.directions')}
                        </a>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Academic Performance (NVO Results) */}
        {hasExamResults && (() => {
          const hasMultipleGrades = availableExamTypes.length > 1

          return (
            <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h2 className="text-2xl font-bold text-neutral-900 mb-2">
                    {t('schools.academicPerformance')}
                  </h2>
                  <div className="text-sm text-neutral-500">
                    {nvoDetail.gradeLabel}
                    {nvoDetail.hasAverage && nvoDetail.minYear && nvoDetail.maxYear && (
                      <> • {t('academicPerformance.basedOnYears', {
                        start: nvoDetail.minYear,
                        end: nvoDetail.maxYear
                      })}</>
                    )}
                  </div>
                </div>

              </div>

              {/* One exam at a time: 4th/7th/10th-grade NVO are different exams, so they
                  are not plotted together; each is compared with its own Sofia schools' average. */}
                {/* Tabs for Multiple Grade Levels */}
                {hasMultipleGrades && (
                  <div className="flex gap-2 mb-6 overflow-x-auto pb-2">
                    {availableExamTypes.map(examType => {
                      const isActive = activeExamType === examType

                      return (
                        <button
                          key={examType}
                          onClick={() => setActiveExamType(examType)}
                          className={`px-4 py-2.5 rounded-lg font-medium whitespace-nowrap transition-all ${
                            isActive
                              ? 'bg-primary-700 text-white shadow-md'
                              : 'bg-neutral-100 text-neutral-700 hover:bg-neutral-200'
                          }`}
                        >
                          {getExamTypeLabel(examType, t)}
                        </button>
                      )
                    })}
                  </div>
                )}

                {/* Single Grade Chart */}
                <Suspense fallback={<div className="h-[400px]" />}>
                  <NvoTimelineChart
                    examResults={school.exam_results}
                    examType={activeExamType || getExamTypeForEducationLevel(school.education_level)}
                    selectedSubjects={selectedSubjects}
                    examAverages={examAverages}
                  />
                </Suspense>

              {/* Subject Filter Toggles (below chart) */}
              <div className="flex items-center justify-center gap-3 mt-6 pt-6 border-t border-neutral-200">
                <span className="text-sm font-medium text-neutral-600">{t('schools.showSubjects')}:</span>
                <div className="flex gap-2">
                  <button
                    onClick={() => handleSubjectToggle('math')}
                    disabled={selectedSubjects.length === 1 && selectedSubjects.includes('math')}
                    className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                      selectedSubjects.includes('math')
                        ? 'bg-blue-600 text-white shadow-sm'
                        : 'bg-neutral-100 text-neutral-500 hover:bg-neutral-200'
                    } ${selectedSubjects.length === 1 && selectedSubjects.includes('math') ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <span className="flex items-center gap-1.5">
                      {selectedSubjects.includes('math') && (
                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                      {t('schoolCard.nvo.subjectMath')}
                    </span>
                  </button>
                  <button
                    onClick={() => handleSubjectToggle('bulgarian')}
                    disabled={selectedSubjects.length === 1 && selectedSubjects.includes('bulgarian')}
                    className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                      selectedSubjects.includes('bulgarian')
                        ? 'bg-purple-600 text-white shadow-sm'
                        : 'bg-neutral-100 text-neutral-500 hover:bg-neutral-200'
                    } ${selectedSubjects.length === 1 && selectedSubjects.includes('bulgarian') ? 'opacity-50 cursor-not-allowed' : ''}`}
                  >
                    <span className="flex items-center gap-1.5">
                      {selectedSubjects.includes('bulgarian') && (
                        <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                        </svg>
                      )}
                      {t('schoolCard.nvo.subjectBulgarian')}
                    </span>
                  </button>
                </div>
              </div>
            </div>
          )
        })()}

        {/* Languages & Teaching Approach */}
        {(hasLanguageInfo || hasTeachingApproach) && (
          <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
            <h2 className="text-2xl font-bold text-neutral-900 mb-6">{t('schools.languagesPrograms')}</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {hasLanguageInfo && (
                <div>
                  <h3 className="text-sm font-semibold text-neutral-500 uppercase tracking-wide mb-3">
                    {t('schools.languagesOfInstruction')}
                  </h3>
                  <ul className="space-y-2">
                    {normalizeLanguageFocus(attributes.language_focus).map((item, idx) => (
                      <li key={idx} className="flex items-center gap-2 text-neutral-700">
                        <span className="text-base">•</span>
                        <span>
                          {getLanguageLabel(item.language, t)}
                          {item.level && item.level !== 'standard' && (
                            <span className="ml-2 text-sm text-neutral-500">({getOptionLabel(item.level, t)})</span>
                          )}
                        </span>
                      </li>
                    ))}
                    {(attributes.languages_of_instruction || [])
                      .filter(lang => !normalizeLanguageFocus(attributes.language_focus).some(item => item.language === lang))
                      .map((lang, idx) => (
                        <li key={`inst-${idx}`} className="flex items-center gap-2 text-neutral-700">
                          <span className="text-base">•</span>
                          <span>{getLanguageLabel(lang, t)}</span>
                        </li>
                      ))}
                  </ul>
                </div>
              )}

              {hasTeachingApproach && (
                <div>
                  <h3 className="text-sm font-semibold text-neutral-500 uppercase tracking-wide mb-3">
                    {t('schools.teachingApproach')}
                  </h3>
                  <ul className="space-y-2">
                    {(attributes.teaching_approach || []).map((approach, idx) => (
                      <li key={idx} className="flex items-center gap-2 text-neutral-700">
                        <span className="text-base">•</span>
                        <span>{getOptionLabel(approach, t)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Facilities & Amenities */}
        {hasFacilities && (
          <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
            <h2 className="text-2xl font-bold text-neutral-900 mb-6">{t('schools.amenitiesFacilities')}</h2>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
              {amenityFlags.meals && (
                <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <span className="text-3xl">🍽️</span>
                  <span className="text-sm text-center text-neutral-700">{t('schoolCard.amenities.cafeteria')}</span>
                </div>
              )}
              {amenityFlags.transport && (
                <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <span className="text-3xl">🚌</span>
                  <span className="text-sm text-center text-neutral-700">{t('schoolCard.amenities.transport')}</span>
                </div>
              )}
              {amenityFlags.extended && (
                <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <span className="text-3xl">⏰</span>
                  <span className="text-sm text-center text-neutral-700">{t('schoolCard.amenities.extended')}</span>
                </div>
              )}
              {amenityFlags.accessible && (
                <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <span className="text-3xl">♿</span>
                  <span className="text-sm text-center text-neutral-700">{t('schoolCard.amenities.accessible')}</span>
                </div>
              )}
              {amenityFlags.library && (
                <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <span className="text-3xl">📚</span>
                  <span className="text-sm text-center text-neutral-700">{t('schoolCard.amenities.library')}</span>
                </div>
              )}
              {amenityFlags.computerLab && (
                <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <span className="text-3xl">💻</span>
                  <span className="text-sm text-center text-neutral-700">{t('schoolCard.amenities.computerLab')}</span>
                </div>
              )}
              {amenityFlags.musicRoom && (
                <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <span className="text-3xl">🎵</span>
                  <span className="text-sm text-center text-neutral-700">{t('schoolCard.amenities.musicRoom')}</span>
                </div>
              )}
              {amenityFlags.scienceLab && (
                <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <span className="text-3xl">🔬</span>
                  <span className="text-sm text-center text-neutral-700">{t('schoolCard.amenities.scienceLab')}</span>
                </div>
              )}
              {amenityFlags.sportsField && (
                <div className="flex flex-col items-center gap-2 p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                  <span className="text-3xl">⚽</span>
                  <span className="text-sm text-center text-neutral-700">{t('schoolCard.amenities.sportsField')}</span>
                </div>
              )}
            </div>

            {(attributes.facilities || []).length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-neutral-500 uppercase tracking-wide mb-3">
                  {t('schools.additionalFacilities')}
                </h3>
                <TagList
                  tags={attributes.facilities}
                  chipClassName="px-3 py-1 bg-neutral-100 text-neutral-700 rounded-lg text-sm border border-neutral-200"
                />
              </div>
            )}
          </div>
        )}

        {/* Special Programs & Activities */}
        {hasSpecialPrograms && (
          <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
            <h2 className="text-2xl font-bold text-neutral-900 mb-6">{t('schools.specialPrograms')}</h2>
            <TagList
              tags={[...(attributes.special_programs || []), ...(attributes.activities_offered || [])]}
              chipClassName="px-4 py-2 bg-primary-50 text-primary-700 rounded-lg text-sm font-medium border border-primary-200"
            />
          </div>
        )}

        {/* Mobile sticky actions (desktop has them in the hero). Hidden while the global
            CompareBar occupies the bottom edge; the hero actions show instead. */}
        {!compareBarVisible && (
          <div className="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t border-neutral-200 px-4 py-3 z-10 shadow-lg">
            <SchoolActions
              compact
              onShare={handleShare}
              onCompare={handleCompareClick}
              inCompare={inCompare}
              canAddMore={canAddMore}
            />
          </div>
        )}
      </div>
    </Shell>
  )
}

export default SchoolDetailPage
