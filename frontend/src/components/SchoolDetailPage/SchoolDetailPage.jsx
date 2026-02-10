import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Layout from '../Layout/Layout'
import { fetchSchool } from '../../api/schools'
import { getSchoolName, getAddress, getSummary } from '../../utils/i18n'
import { useCompare } from '../../context/CompareContext'
import {
  getStatusInfo,
  parseCoordinate,
  getLastAdmittedPoints,
  getMinNvoScore,
  getAdmissionRequirement,
  formatPercent,
  getPerformanceStyle,
  getTrendInfo,
  getNvoDetail,
  groupPricingByCategory,
  getSourceBadgeColor,
  formatCurrency,
  getAmenityFlags,
  getLanguageLabel,
  getOptionLabel,
  normalizeLanguageFocus,
  hexToRgba,
} from './helpers'

function SchoolDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()
  const { addToCompare, removeFromCompare, isInCompare, canAddMore } = useCompare()
  const [school, setSchool] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const formatAmount = (value) => {
    if (value == null || Number.isNaN(value)) return null
    return new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 0 }).format(value)
  }

  useEffect(() => {
    const loadSchool = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await fetchSchool(id)
        setSchool(data)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    loadSchool()
  }, [id])

  if (loading) {
    return (
      <Layout>
        <div className="flex items-center justify-center min-h-[60vh]">
          <div className="flex items-center gap-3">
            <svg className="animate-spin h-8 w-8 text-primary-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span className="text-lg text-neutral-600">{t('common.loading')}</span>
          </div>
        </div>
      </Layout>
    )
  }

  if (error || !school) {
    return (
      <Layout>
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
              onClick={() => navigate(-1)}
              className="px-6 py-3 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
            >
              {t('common.goBack')}
            </button>
          </div>
        </div>
      </Layout>
    )
  }

  const summary = getSummary(school, i18n.language, 'long')
  const schoolName = getSchoolName(school, i18n.language)
  const statusInfo = getStatusInfo(school, t)
  const inCompare = isInCompare(school.id)
  const attributes = school.attributes || {}
  const pricing = school.pricing || []
  const locations = school.locations || []
  const primaryLocation = locations.find(l => l.is_primary) || locations[0]
  const nvoDetail = getNvoDetail(school, t)

  // Check if we have various data to display
  const hasExamResults = nvoDetail != null
  const hasPricing = school.school_type !== 'state' && pricing.length > 0
  const hasLanguageInfo = normalizeLanguageFocus(attributes.language_focus).length > 0 || (attributes.languages_of_instruction || []).length > 0
  const hasTeachingApproach = (attributes.teaching_approach || []).length > 0
  const hasAfterSchool = locations.some(loc => (loc.age_group_shifts || []).some(shift => shift.has_organised_groups))
  const amenityFlags = getAmenityFlags(attributes, hasAfterSchool)
  const hasFacilities = Object.values(amenityFlags).some(Boolean) || (attributes.facilities || []).length > 0
  const hasSpecialPrograms = (attributes.special_programs || []).length > 0 || (attributes.activities_offered || []).length > 0

  const statusStyle = {
    backgroundColor: statusInfo.color,
    boxShadow: `0 0 8px ${hexToRgba(statusInfo.color, 0.4)}`,
  }

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
          text: summary || `${schoolName} - ${t(`schoolTypes.${school.school_type}`)}`,
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

  return (
    <Layout>
      <div className="max-w-5xl mx-auto px-4 md:px-6 py-6 md:py-8 pb-24 md:pb-8">
        {/* Back Button */}
        <button
          onClick={() => navigate(-1)}
          className="flex items-center gap-2 text-sm text-neutral-600 hover:text-neutral-900 mb-6 transition-colors"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          {t('common.back')}
        </button>

        {/* Hero Section */}
        <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-10 mb-6">
          <div className="flex items-start gap-3 mb-4">
            <span
              className={`h-3 w-3 rounded-full mt-2 flex-shrink-0 ${statusInfo.key === 'accepting' ? 'animate-pulse' : ''}`}
              style={statusStyle}
              aria-label={statusInfo.label}
              role="img"
            />
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
              {t(`schoolTypes.${school.school_type}`)}
            </span>
            <span className="px-4 py-2 rounded-lg text-sm font-semibold bg-neutral-100 text-neutral-700 border border-neutral-200">
              {t(`educationLevels.${school.education_level}`)}
            </span>
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
          </div>

          {summary && (
            <div className="prose max-w-none mb-6">
              <p className="text-neutral-700 leading-relaxed text-lg">{summary}</p>
            </div>
          )}

          <div className="flex flex-wrap gap-3">
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
            {primaryLocation?.phone && (
              <a
                href={`tel:${primaryLocation.phone}`}
                className="inline-flex items-center gap-2 px-4 py-2 bg-neutral-50 text-neutral-700 rounded-lg hover:bg-neutral-100 transition-colors border border-neutral-200"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
                </svg>
                {primaryLocation.phone}
              </a>
            )}
          </div>
        </div>

        {/* Locations & Admission Requirements */}
        {locations.length > 0 && (
          <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
            <h2 className="text-2xl font-bold text-neutral-900 mb-6">{t('schools.locationsAndEnrollment')}</h2>
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
                    school.admission_info?.requirements || attributes.entry_requirements || attributes.admission_requirement,
                    t
                  )
                  if (requirement) {
                    admissionText = `${requirement.icon} ${requirement.text}`
                  }
                } else if (school.school_type === 'state' && (school.education_level === 'primary' || school.education_level === 'lower_secondary')) {
                  admissionText = t('schoolCard.admissions.districtEnrollment')
                }

                if (!admissionText) {
                  admissionText = t('schools.contactForDetails')
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
                          <span>{address}</span>
                        </div>
                      )}

                      {location.phone && (
                        <div className="flex items-center gap-2 text-neutral-600">
                          <svg className="w-4 h-4 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 5a2 2 0 012-2h3.28a1 1 0 01.948.684l1.498 4.493a1 1 0 01-.502 1.21l-2.257 1.13a11.042 11.042 0 005.516 5.516l1.13-2.257a1 1 0 011.21-.502l4.493 1.498a1 1 0 01.684.949V19a2 2 0 01-2 2h-1C9.716 21 3 14.284 3 6V5z" />
                          </svg>
                          <span>{location.phone}</span>
                        </div>
                      )}

                      <div className="bg-primary-50 border border-primary-200 rounded-lg p-3 mt-3">
                        <div className="flex items-start gap-2">
                          <span className="text-base">🎯</span>
                          <div>
                            <div className="font-medium text-neutral-900 text-sm mb-1">{t('schools.admissionRequirements')}</div>
                            <div className="text-neutral-700">{admissionText}</div>
                          </div>
                        </div>
                      </div>

                      {(location.age_group_shifts || []).length > 0 && (
                        <div className="mt-3 space-y-2">
                          {location.age_group_shifts.map((shift, shiftIdx) => (
                            <div key={shiftIdx} className="flex items-center gap-2 text-neutral-600">
                              <svg className="w-4 h-4 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                              </svg>
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
                        <button
                          onClick={() => window.open(`https://www.google.com/maps/search/?api=1&query=${lat},${lng}`, '_blank')}
                          className="mt-3 inline-flex items-center gap-2 px-3 py-1.5 text-sm bg-neutral-50 text-neutral-700 rounded-lg hover:bg-neutral-100 transition-colors border border-neutral-200"
                        >
                          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 20l-5.447-2.724A1 1 0 013 16.382V5.618a1 1 0 011.447-.894L9 7m0 13l6-3m-6 3V7m6 10l4.553 2.276A1 1 0 0021 18.382V7.618a1 1 0 00-.553-.894L15 4m0 13V4m0 0L9 7" />
                          </svg>
                          {t('schools.viewOnMap')}
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Academic Performance (NVO Results) */}
        {hasExamResults && (
          <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
            <h2 className="text-2xl font-bold text-neutral-900 mb-2">{t('schools.academicPerformance')}</h2>
            <div className="text-sm text-neutral-500 mb-6">
              {nvoDetail.gradeLabel}
              {nvoDetail.hasAverage && nvoDetail.minYear && nvoDetail.maxYear && (
                <> • {t('academicPerformance.basedOnYears', { start: nvoDetail.minYear, end: nvoDetail.maxYear })}</>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Math Card */}
              {nvoDetail.latestMath != null && (
                <div className="bg-neutral-50 rounded-xl p-6 border border-neutral-200">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-lg font-semibold text-neutral-900">{t('schoolCard.nvo.subjectMath')}</h3>
                    {nvoDetail.hasAverage && nvoDetail.latestMath != null && (
                      <span className={`text-sm font-medium ${getTrendInfo(nvoDetail.latestMath, nvoDetail.mathAvg)?.className || ''}`}>
                        {getTrendInfo(nvoDetail.latestMath, nvoDetail.mathAvg)?.arrow || ''}
                      </span>
                    )}
                  </div>
                  <div className={`text-4xl font-bold mb-2 ${getPerformanceStyle(nvoDetail.latestMath).text}`}>
                    {formatPercent(nvoDetail.latestMath, 1)}%
                  </div>
                  <div className="w-full bg-neutral-200 rounded-full h-2 mb-3">
                    <div
                      className={`h-2 rounded-full ${getPerformanceStyle(nvoDetail.latestMath).bg}`}
                      style={{ width: `${Math.min(nvoDetail.latestMath, 100)}%` }}
                    />
                  </div>
                  {nvoDetail.hasAverage && nvoDetail.mathAvg != null && (
                    <div className="text-sm text-neutral-600">
                      {t('academicPerformance.yearAverage', { year: '3-5' })}: {formatPercent(nvoDetail.mathAvg, 1)}%
                      {nvoDetail.latestMath != null && (
                        <span className={`ml-2 font-medium ${getTrendInfo(nvoDetail.latestMath, nvoDetail.mathAvg)?.className || ''}`}>
                          {getTrendInfo(nvoDetail.latestMath, nvoDetail.mathAvg) &&
                            `${getTrendInfo(nvoDetail.latestMath, nvoDetail.mathAvg).diff > 0 ? '+' : ''}${formatPercent(getTrendInfo(nvoDetail.latestMath, nvoDetail.mathAvg).diff, 1)}%`
                          }
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* Bulgarian Card */}
              {nvoDetail.latestBg != null && (
                <div className="bg-neutral-50 rounded-xl p-6 border border-neutral-200">
                  <div className="flex items-center justify-between mb-3">
                    <h3 className="text-lg font-semibold text-neutral-900">{t('schoolCard.nvo.subjectBulgarian')}</h3>
                    {nvoDetail.hasAverage && nvoDetail.latestBg != null && (
                      <span className={`text-sm font-medium ${getTrendInfo(nvoDetail.latestBg, nvoDetail.bgAvg)?.className || ''}`}>
                        {getTrendInfo(nvoDetail.latestBg, nvoDetail.bgAvg)?.arrow || ''}
                      </span>
                    )}
                  </div>
                  <div className={`text-4xl font-bold mb-2 ${getPerformanceStyle(nvoDetail.latestBg).text}`}>
                    {formatPercent(nvoDetail.latestBg, 1)}%
                  </div>
                  <div className="w-full bg-neutral-200 rounded-full h-2 mb-3">
                    <div
                      className={`h-2 rounded-full ${getPerformanceStyle(nvoDetail.latestBg).bg}`}
                      style={{ width: `${Math.min(nvoDetail.latestBg, 100)}%` }}
                    />
                  </div>
                  {nvoDetail.hasAverage && nvoDetail.bgAvg != null && (
                    <div className="text-sm text-neutral-600">
                      {t('academicPerformance.yearAverage', { year: '3-5' })}: {formatPercent(nvoDetail.bgAvg, 1)}%
                      {nvoDetail.latestBg != null && (
                        <span className={`ml-2 font-medium ${getTrendInfo(nvoDetail.latestBg, nvoDetail.bgAvg)?.className || ''}`}>
                          {getTrendInfo(nvoDetail.latestBg, nvoDetail.bgAvg) &&
                            `${getTrendInfo(nvoDetail.latestBg, nvoDetail.bgAvg).diff > 0 ? '+' : ''}${formatPercent(getTrendInfo(nvoDetail.latestBg, nvoDetail.bgAvg).diff, 1)}%`
                          }
                        </span>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Enhanced Pricing */}
        {hasPricing && (
          <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
            <div className="flex items-center justify-between mb-6">
              <h2 className="text-2xl font-bold text-neutral-900">{t('pricing.title')}</h2>
              {pricing[0]?.academic_year && (
                <span className="text-sm px-3 py-1 bg-teal-50 text-teal-700 border border-teal-200 rounded-lg">
                  {pricing[0].academic_year}
                </span>
              )}
            </div>

            <div className="space-y-6">
              {Object.entries(groupPricingByCategory(pricing))
                .filter(([_, items]) => items.length > 0)
                .map(([category, items]) => (
                  <div key={category}>
                    <h3 className="text-sm font-semibold text-neutral-500 uppercase tracking-wide mb-3">
                      {t(`pricing.${category}`)}
                    </h3>
                    <div className="space-y-3">
                      {items.map((price, idx) => {
                        const amountText = price.amount_min != null || price.amount_max != null
                          ? `${price.amount_min != null ? formatAmount(price.amount_min) : ''}${price.amount_min != null && price.amount_max != null ? '–' : ''}${price.amount_max != null ? formatAmount(price.amount_max) : ''} ${price.currency || 'BGN'}`
                          : price.amount != null
                          ? `${formatAmount(price.amount)} ${price.currency || 'BGN'}`
                          : t('pricing.priceOnRequest')

                        return (
                          <div key={idx} className="flex items-center justify-between p-4 bg-neutral-50 rounded-lg border border-neutral-200">
                            <div className="flex-1">
                              <div className="font-medium text-neutral-900">
                                {price.plan_name || t(`pricing.${price.category}`)}
                              </div>
                              {price.age_group && (
                                <div className="text-sm text-neutral-500">{t(`ageGroups.${price.age_group}`)}</div>
                              )}
                            </div>
                            <div className="text-right flex items-center gap-3">
                              <div>
                                <div className="text-lg font-bold text-neutral-900">{amountText}</div>
                                <div className="text-sm text-neutral-500">{t(`pricing.${price.period}`)}</div>
                              </div>
                              {price.source && (
                                <span className={`px-2 py-1 text-xs font-medium rounded border ${getSourceBadgeColor(price.source)}`}>
                                  {t(`priceSource.${price.source}`)}
                                </span>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                ))}
            </div>
          </div>
        )}

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
                <div className="flex flex-wrap gap-2">
                  {attributes.facilities.map((facility, idx) => (
                    <span key={idx} className="px-3 py-1 bg-neutral-100 text-neutral-700 rounded-lg text-sm border border-neutral-200">
                      {getOptionLabel(facility, t)}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Special Programs & Activities */}
        {hasSpecialPrograms && (
          <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
            <h2 className="text-2xl font-bold text-neutral-900 mb-6">{t('schools.specialPrograms')}</h2>
            <div className="flex flex-wrap gap-2">
              {[...(attributes.special_programs || []), ...(attributes.activities_offered || [])].map((program, idx) => (
                <span key={idx} className="px-4 py-2 bg-primary-50 text-primary-700 rounded-lg text-sm font-medium border border-primary-200">
                  {getOptionLabel(program, t)}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Sticky Action Footer */}
        <div className="fixed bottom-0 left-0 right-0 md:static bg-white border-t border-neutral-200 px-4 py-3 md:py-0 md:bg-transparent md:border-0 md:mt-8 z-10 shadow-lg md:shadow-none">
          <div className="max-w-5xl mx-auto flex gap-3">
            <button
              onClick={handleShare}
              className="flex-1 md:flex-none px-4 py-3 text-sm font-medium text-neutral-700 bg-neutral-100 hover:bg-neutral-200 rounded-lg transition-colors border border-neutral-300"
            >
              <span className="hidden md:inline">{t('schools.share')}</span>
              <span className="md:hidden">
                <svg className="w-5 h-5 mx-auto" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8.684 13.342C8.886 12.938 9 12.482 9 12c0-.482-.114-.938-.316-1.342m0 2.684a3 3 0 110-2.684m0 2.684l6.632 3.316m-6.632-6l6.632-3.316m0 0a3 3 0 105.367-2.684 3 3 0 00-5.367 2.684zm0 9.316a3 3 0 105.368 2.684 3 3 0 00-5.368-2.684z" />
                </svg>
              </span>
            </button>
            <button
              onClick={handleCompareClick}
              disabled={!inCompare && !canAddMore}
              className={`flex-1 px-4 py-3 text-sm font-medium rounded-lg transition-colors ${
                inCompare
                  ? 'bg-primary-100 text-primary-700 border-2 border-primary-500 hover:bg-primary-200'
                  : !canAddMore
                  ? 'bg-neutral-100 text-neutral-400 cursor-not-allowed border border-neutral-300'
                  : 'bg-primary-50 text-primary-700 hover:bg-primary-100 border border-primary-300'
              }`}
            >
              {inCompare ? (
                <span className="flex items-center justify-center gap-1">
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span className="hidden md:inline">{t('schools.comparing')}</span>
                  <span className="md:hidden">✓</span>
                </span>
              ) : (
                t('schools.compare')
              )}
            </button>
          </div>
        </div>
      </div>
    </Layout>
  )
}

export default SchoolDetailPage
