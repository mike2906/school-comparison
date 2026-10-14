import { memo, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { isDesktopViewport, isPlainLeftClick } from '../../utils/searchViewState'
import { schoolLevelLabel, schoolTypeLabel } from '../../utils/levelLabel'
import { useCountry } from '../../context/CountryContext'
import { useCompare } from '../../context/CompareContext'
import { getSchoolName, getAddress } from '../../utils/i18n'
import { formatAmount, formatDistance, intlLocale } from '../../utils/format'
import { rushHourMinutes } from '../../routing/driveTimes'
import { getStatusInfo } from '../../utils/schoolAttributes'
import {
  curatedRequirement,
  getAdmissionRequirement,
  getLastAdmittedPoints,
  getMinNvoScore,
  usesSofiaKindergartenSystem,
} from '../../utils/admission'
import {
  formatPercent,
  getBenchmarkTooltip,
  getNvoValueStyle,
  getTrendInfo,
  getTrendTooltip,
} from '../../utils/nvoDisplay'
import { selectPricingCohort, statedTuition, YEAR_STATUS } from '../../utils/pricing'

import {
  AMENITY_PRIORITY,
  buildExpandedSections,
  getAmenityFlags,
  getLocationAgeGroups,
  getNvoDetail,
  getPrimaryFeature,
  getScheduleLine,
  hexToRgba,
  typeColors,
} from './cardHelpers'
import { getShiftForAgeGroup } from '../../utils/schoolLocation'
import { parseCoordinate } from '../../utils/coordinates'
import LocationsPanel from './LocationsPanel'
import NvoDetailTable from './NvoDetailTable'

const SchoolCard = memo(function SchoolCard({
  school,
  isSelected,
  onClick,
  onHover,
  onHoverEnd,
  location,
  activeAgeGroup = null,
  nvoExamType = null,
  ageGroupOrder = [],
  isLocationsOpen = false,
  locationOverlay = null,
  onToggleLocations,
  onShowAllLocations,
  onFocusLocation,
  onClearLocations,
  onOpenDetails,
  examAverages = null,
  samePlace = [],
}) {
  const { t, i18n } = useTranslation()
  const { config: countryConfig } = useCountry()
  const { addToCompare, removeFromCompare, isInCompare, canAddMore } = useCompare()
  const [isExpanded, setIsExpanded] = useState(false)

  const displayType = school.school_type === 'international' ? 'private' : school.school_type
  const colors = typeColors[displayType] || typeColors.state
  const primaryLocation = location || school.locations?.find(l => l.is_primary) || school.locations?.[0]
  const inCompare = isInCompare(school.id)

  const attributes = school.attributes || {}
  const pricing = school.pricing || []
  const locations = school.locations || []

  const schoolName = getSchoolName(school, i18n.language)
  const address = primaryLocation ? getAddress(primaryLocation, i18n.language) : null

  const overlayActive = locationOverlay?.schoolId === school.id
  const focusedLocationId = overlayActive ? locationOverlay?.focusLocationId : null

  const orderedLocations = useMemo(() => {
    if (!locations.length) return []
    const orderMap = new Map(ageGroupOrder.map((key, idx) => [key, idx]))
    return [...locations].sort((a, b) => {
      const aGroups = getLocationAgeGroups(a)
      const bGroups = getLocationAgeGroups(b)
      const aIndex = aGroups.length ? Math.min(...aGroups.map(group => orderMap.get(group) ?? 999)) : 999
      const bIndex = bGroups.length ? Math.min(...bGroups.map(group => orderMap.get(group) ?? 999)) : 999
      if (aIndex !== bIndex) return aIndex - bIndex
      if (a.is_primary !== b.is_primary) return a.is_primary ? -1 : 1
      if (a.id != null && b.id != null) return a.id - b.id
      return 0
    })
  }, [locations, ageGroupOrder])

  const validMapLocations = useMemo(() => {
    return orderedLocations.filter(loc => {
      const lat = parseCoordinate(loc?.lat)
      const lng = parseCoordinate(loc?.lng)
      return Number.isFinite(lat) && Number.isFinite(lng)
    })
  }, [orderedLocations])
  const canShowOnMap = validMapLocations.length > 0

  const flowGroups = useMemo(() => {
    const seen = new Set()
    const groups = []
    orderedLocations.forEach(locationItem => {
      const locationGroups = getLocationAgeGroups(locationItem)
      locationGroups.forEach(group => {
        if (seen.has(group)) return
        seen.add(group)
        groups.push(group)
      })
    })
    return groups
  }, [orderedLocations])

  const primaryAgeGroup = primaryLocation ? getLocationAgeGroups(primaryLocation)[0] : null

  const primaryShiftInfo = useMemo(
    () => getShiftForAgeGroup(primaryLocation, activeAgeGroup),
    [primaryLocation, activeAgeGroup]
  )
  const statusInfo = useMemo(() => getStatusInfo(school, t), [school, t])
  const primaryFeature = useMemo(() => getPrimaryFeature(attributes, t), [attributes, t])
  const scheduleLine = useMemo(
    () => getScheduleLine(primaryShiftInfo, attributes, t),
    [primaryShiftInfo, attributes, t]
  )
  // Badge and headline price must come from the same cohort, or a current-year label
  // can end up above a previous year's fee.
  const pricingCohort = useMemo(
    () => selectPricingCohort(pricing, { ageGroup: activeAgeGroup }),
    [pricing, activeAgeGroup]
  )
  const pricingYearLabel = useMemo(() => {
    if (!pricingCohort) return null
    if (pricingCohort.yearStatus === YEAR_STATUS.NOT_STATED) {
      return t('pricing.yearNotStated')
    }
    return pricingCohort.academicYear
  }, [pricingCohort, t])
  // Only a current-year price, or next year's for families enrolling now, wears the
  // highlighted badge, so an older fee is never styled as though it were this year's.
  const pricingYearBadgeClass =
    pricingCohort?.yearStatus === YEAR_STATUS.CURRENT || pricingCohort?.yearStatus === YEAR_STATUS.NEXT
      ? 'text-teal-700 bg-teal-50 border-teal-200'
      : 'text-neutral-600 bg-neutral-100 border-neutral-300'

  const admissionsInfo = useMemo(() => {
    const locale = intlLocale(i18n.language)
    const isPrivate = school.school_type === 'private' || school.school_type === 'international'
    const admissionInfo = school.admission_info || {}

    if (isPrivate) {
      // The fee as the school states it, in euro: never annualised, since schools bill
      // over 9, 10 or 12 months (the detail page and compare show it the same way).
      // The chosen age group's fee, never another group's.
      const tuition = statedTuition(pricing, { ageGroup: activeAgeGroup })
      const price = tuition && (formatAmount(tuition.min, locale) === formatAmount(tuition.max, locale)
        ? formatAmount(tuition.min, locale)
        : `${formatAmount(tuition.min, locale)}–${formatAmount(tuition.max, locale)}`)
      const priceLabel = !tuition
        ? null
        : tuition.period
          ? t('pricing.pricePerPeriod', { price, currency: tuition.currency, period: t(`pricing.per.${tuition.period}`) })
          : t('schoolCard.admissions.priceUnstatedPeriod', { price, currency: tuition.currency })

      const requirement = getAdmissionRequirement(
        curatedRequirement(admissionInfo, i18n.language) || attributes?.entry_requirements,
        t
      )

      if (priceLabel && requirement) {
        return { icon: '💰', text: `${priceLabel} • ${requirement.icon} ${requirement.text}` }
      }
      if (priceLabel) return { icon: '💰', text: priceLabel }
      if (requirement) return { icon: requirement.icon, text: requirement.text }
      return null
    }

    if (school.school_type === 'state' && school.education_level === 'kindergarten') {
      const lastAdmitted = getLastAdmittedPoints(admissionInfo, primaryAgeGroup)
      if (lastAdmitted) {
        return {
          icon: '🎯',
          text: t('schoolCard.admissions.lastAdmitted', {
            points: lastAdmitted.points,
            year: lastAdmitted.year,
            pointsLabel: t('admission.points'),
          }),
        }
      }
      // No published thresholds yet: say how admission works rather than nothing.
      return usesSofiaKindergartenSystem(school)
        ? { icon: '🎯', text: t('schoolCard.admissions.kgByPoints') }
        : null
    }

    if (school.school_type === 'state' && school.education_level === 'upper_secondary') {
      const minScore = getMinNvoScore(admissionInfo)
      if (minScore) {
        return {
          icon: '🎯',
          text: t('schoolCard.admissions.minScore', {
          score: minScore.score,
          year: minScore.year,
        }),
        }
      }
      return null
    }

    return null
  }, [school, pricing, attributes, i18n.language, t, primaryLocation, activeAgeGroup])

  const nvoDetail = useMemo(() => {
    // Sorted by NVO: only the exam the list is ranked by, so no card shows a score from a
    // different exam. Otherwise the searched stage's exam (e.g. 7th grade for grades 5–7),
    // else the school's own.
    return nvoExamType ? getNvoDetail(school, t, null, nvoExamType) : getNvoDetail(school, t, activeAgeGroup)
  }, [school, t, activeAgeGroup, nvoExamType])

  const nvoSummary = useMemo(() => {
    if (!nvoDetail) return null
    const mathValue = nvoDetail.latestMath ?? nvoDetail.mathAvg
    const bgValue = nvoDetail.latestBg ?? nvoDetail.bgAvg
    const mathStyle = getNvoValueStyle({
      value: mathValue,
      examType: nvoDetail.examType,
      year: nvoDetail.latestMathYear ?? nvoDetail.latestYear,
      subjectKey: 'math',
      examAverages,
    })
    const bulgarianStyle = getNvoValueStyle({
      value: bgValue,
      examType: nvoDetail.examType,
      year: nvoDetail.latestBgYear ?? nvoDetail.latestYear,
      subjectKey: 'bulgarian',
      examAverages,
    })

    return {
      gradeLabel: nvoDetail.gradeLabel,
      math: {
        value: mathValue,
        style: mathStyle,
        trend: nvoDetail.hasAverage && nvoDetail.latestMath != null
          ? getTrendInfo(nvoDetail.latestMath, nvoDetail.mathAvg)
          : null,
      },
      bulgarian: {
        value: bgValue,
        style: bulgarianStyle,
        trend: nvoDetail.hasAverage && nvoDetail.latestBg != null
          ? getTrendInfo(nvoDetail.latestBg, nvoDetail.bgAvg)
          : null,
      },
    }
  }, [examAverages, nvoDetail, t])

  const amenityItems = useMemo(() => {
    const flags = getAmenityFlags(attributes, primaryShiftInfo?.has_organised_groups)
    return AMENITY_PRIORITY
      .filter(item => flags[item.key])
      .slice(0, 4)
      .map(item => {
        if (item.key === 'smallClasses' && attributes?.class_size) {
          return {
            icon: item.icon,
            label: t('schoolCard.amenities.perClass', { count: attributes.class_size }),
            key: item.key,
          }
        }
        return {
          icon: item.icon,
          label: t(item.labelKey),
          key: item.key,
        }
      })
  }, [attributes, primaryLocation, t])

  const expandedSections = useMemo(() => buildExpandedSections({
    school,
    attributes,
    locations,
    pricing,
    t,
    locale: intlLocale(i18n.language),
  }), [school, attributes, locations, pricing, t, i18n.language])

  const hasExpandedContent = expandedSections.length > 0 || Boolean(nvoDetail)
  const expandedId = `school-details-${school.id}`
  const showScheduleOnMobile = !admissionsInfo
  const distanceValue = typeof school.distance === 'number' ? school.distance : null
  // Only a known distance is shown; the list header prompts for a location once instead.
  const distanceLabel = distanceValue != null
    ? formatDistance(distanceValue, { language: i18n.language, t })
    : null
  const distanceStyle = distanceValue == null
    ? { backgroundColor: '#f3f4f6', color: '#6b7280' }
    : distanceValue < 2
    ? { backgroundColor: 'rgba(16,185,129,0.1)', color: '#059669' }
    : distanceValue <= 5
    ? { backgroundColor: 'rgba(245,158,11,0.1)', color: '#d97706' }
    : { backgroundColor: 'rgba(239,68,68,0.1)', color: '#dc2626' }
  const driveMinutes = rushHourMinutes(school.driveSeconds)
  const statusStyle = statusInfo ? {
    backgroundColor: statusInfo.color,
    boxShadow: `0 0 8px ${hexToRgba(statusInfo.color, 0.4)}`,
  } : null
  const statusBadgeStyle = statusInfo ? {
    backgroundColor: hexToRgba(statusInfo.color, 0.1),
    borderColor: hexToRgba(statusInfo.color, 0.35),
    color: statusInfo.color,
  } : null
  const featureLineClass = 'flex items-center gap-1.5 text-sm max-md:text-[13px] text-neutral-700 leading-relaxed px-2 -mx-2 py-1 rounded transition-all hover:bg-teal-50/60 hover:pl-4'
  const secondaryLineClass = 'text-[13px] text-neutral-500'

  const handleCompareClick = (e) => {
    e.stopPropagation()
    if (inCompare) {
      removeFromCompare(school.id)
    } else {
      addToCompare(school)
    }
  }

  // A real link, so middle-click / ctrl-click open the full page in a new tab. A plain
  // click on desktop opens the side panel instead of leaving the results.
  const handleDetailsClick = (e) => {
    e.stopPropagation()
    if (onOpenDetails && isPlainLeftClick(e) && isDesktopViewport()) {
      e.preventDefault()
      onOpenDetails(school)
    }
  }

  const handleToggleLocations = (event) => {
    event.stopPropagation()
    onToggleLocations?.(school)
  }

  const handleShowAllLocations = (event) => {
    event.stopPropagation()
    onShowAllLocations?.(school.id)
  }


  const handleFocusLocation = (event, locationId) => {
    event.stopPropagation()
    onFocusLocation?.(school.id, locationId)
  }

  const handleClearLocations = (event) => {
    event.stopPropagation()
    onClearLocations?.()
  }

  return (
    <article
      data-school-id={school.id}
      onClick={() => onClick?.(school)}
      onKeyDown={(event) => {
        if (event.target !== event.currentTarget) return
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onClick?.(school)
        }
      }}
      tabIndex={0}
      aria-current={isSelected ? 'true' : undefined}
      onMouseEnter={() => onHover?.(school)}
      onMouseLeave={onHoverEnd}
      className={
        `school-card p-4 mx-3 my-2 rounded-[12px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary-500 cursor-pointer bg-white border transition-all duration-200 shadow-sm hover:-translate-y-0.5 hover:border-teal-500 hover:shadow-[0_4px_12px_rgba(20,184,166,0.15)] ` +
        (isSelected
          ? 'border-primary-200 border-l-4 border-l-primary-600 bg-primary-50/60 shadow-card-hover'
          : 'border-neutral-200')
      }
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2">
          {statusInfo && (
            <span
              className={`h-2.5 w-2.5 rounded-full ${statusInfo.key === 'accepting' ? 'animate-pulse' : ''}`}
              style={statusStyle}
              aria-label={statusInfo.label}
              role="img"
            />
          )}
          <h3 className="text-[17px] font-semibold text-neutral-900 leading-snug max-md:text-[16px]" data-testid="school-name">
            {schoolName}
          </h3>
        </div>
        {(distanceLabel || driveMinutes != null) && (
          <div className="flex flex-shrink-0 flex-col items-end gap-1">
            {distanceLabel && (
              <span
                className="inline-flex flex-shrink-0 items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full"
                style={distanceStyle}
                aria-label={t('schoolCard.aria.distance', { distance: distanceLabel })}
              >
                📍 {distanceLabel}
              </span>
            )}
            {driveMinutes != null && (
              <span
                className="inline-flex flex-shrink-0 items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full bg-neutral-100 text-neutral-700"
                aria-label={t('driveTime.aria', { minutes: driveMinutes })}
                title={t('driveTime.aria', { minutes: driveMinutes })}
              >
                🚗 {t('driveTime.minutes', { minutes: driveMinutes })}
              </span>
            )}
          </div>
        )}
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center px-2.5 py-1 rounded text-[11px] font-semibold uppercase tracking-[0.08em] ${colors}`}>
          {schoolTypeLabel(school, t, displayType)}
        </span>
        <span className="text-xs text-neutral-600">
          {schoolLevelLabel(school, t, countryConfig)}
        </span>
        {samePlace.length > 0 && (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border border-teal-200 bg-teal-50 text-teal-800">
            {t('schoolCard.samePlaceBadge')}
          </span>
        )}
        {pricingYearLabel && (
          <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium border ${pricingYearBadgeClass}`}>
            {pricingYearLabel}
          </span>
        )}
      </div>

      <div className="mt-3 grid grid-cols-1 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-2 md:gap-4">
        <div className="space-y-2">
          {address && (
            <div className="flex items-start gap-2 text-sm text-neutral-600">
              <svg className="w-4 h-4 text-neutral-400 mt-0.5 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              <p className="leading-snug">
                {address}
              </p>
            </div>
          )}

          {primaryFeature && (
            <div className={featureLineClass}>
              <span className="text-[16px]" aria-hidden="true">{primaryFeature.icon}</span>
              <span>{primaryFeature.text}</span>
            </div>
          )}

          {scheduleLine && (
            <div className={`${featureLineClass} ${showScheduleOnMobile ? 'flex' : 'hidden md:flex'}`}>
              <span className="text-[16px]" aria-hidden="true">{scheduleLine.icon}</span>
              <span>{scheduleLine.text}</span>
            </div>
          )}

          {amenityItems.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-2">
              {amenityItems.map((item, index) => (
                <span
                  key={item.key}
                  className={`inline-flex items-center gap-1 rounded-md border border-teal-200/70 bg-teal-50/70 px-2.5 py-1 text-[12px] font-medium text-neutral-700 ${index >= 3 ? 'max-md:hidden' : ''}`}
                >
                  <span aria-hidden="true">{item.icon}</span>
                  <span>{item.label}</span>
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-3 text-sm text-neutral-700">
          {admissionsInfo && (
            <div className="space-y-1">
              <div className="flex items-start gap-2">
                <span className="text-[16px]" aria-hidden="true">{admissionsInfo.icon}</span>
                <span>{admissionsInfo.text}</span>
              </div>
            </div>
          )}

          {nvoSummary && (
            <div className="space-y-1">
              <div className="text-sm font-medium text-neutral-800">📊 {t('schoolCard.nvo.latestTitle', { grade: nvoSummary.gradeLabel })}</div>
              <div className="flex items-center justify-between max-md:hidden">
                <span>{t('schoolCard.nvo.subjectMath')}</span>
                <span
                  className={`font-medium ${nvoSummary.math.style.text} ${nvoSummary.math.style.benchmark ? 'cursor-help' : ''}`}
                  title={getBenchmarkTooltip(nvoSummary.math.value, nvoSummary.math.style.benchmark, t) || undefined}
                >
                  {t('academicPerformance.pointsValue', { value: formatPercent(nvoSummary.math.value, 0) })}
                  {nvoSummary.math.trend && (
                    <span
                      className={`ml-1 ${nvoSummary.math.trend.className} cursor-help`}
                      title={getTrendTooltip(nvoDetail.latestMath, nvoDetail.mathAvg, nvoSummary.math.trend, t) || undefined}
                    >
                      {nvoSummary.math.trend.arrow}
                    </span>
                  )}
                </span>
              </div>
              <div className="flex items-center justify-between max-md:hidden">
                <span>{t('schoolCard.nvo.subjectBulgarian')}</span>
                <span
                  className={`font-medium ${nvoSummary.bulgarian.style.text} ${nvoSummary.bulgarian.style.benchmark ? 'cursor-help' : ''}`}
                  title={getBenchmarkTooltip(nvoSummary.bulgarian.value, nvoSummary.bulgarian.style.benchmark, t) || undefined}
                >
                  {t('academicPerformance.pointsValue', { value: formatPercent(nvoSummary.bulgarian.value, 0) })}
                  {nvoSummary.bulgarian.trend && (
                    <span
                      className={`ml-1 ${nvoSummary.bulgarian.trend.className} cursor-help`}
                      title={getTrendTooltip(nvoDetail.latestBg, nvoDetail.bgAvg, nvoSummary.bulgarian.trend, t) || undefined}
                    >
                      {nvoSummary.bulgarian.trend.arrow}
                    </span>
                  )}
                </span>
              </div>
              <div className="text-[13px] text-neutral-700 md:hidden">
                <span
                  className={`font-medium ${nvoSummary.math.style.text} ${nvoSummary.math.style.benchmark ? 'cursor-help' : ''}`}
                  title={getBenchmarkTooltip(nvoSummary.math.value, nvoSummary.math.style.benchmark, t) || undefined}
                >
                  {t('schoolCard.nvo.subjectMath')}: {t('academicPerformance.pointsValue', { value: formatPercent(nvoSummary.math.value, 0) })}
                  {nvoSummary.math.trend && (
                    <span
                      className={`ml-1 ${nvoSummary.math.trend.className} cursor-help`}
                      title={getTrendTooltip(nvoDetail.latestMath, nvoDetail.mathAvg, nvoSummary.math.trend, t) || undefined}
                    >
                      {nvoSummary.math.trend.arrow}
                    </span>
                  )}
                </span>
                <span className="text-neutral-300 px-2">•</span>
                <span
                  className={`font-medium ${nvoSummary.bulgarian.style.text} ${nvoSummary.bulgarian.style.benchmark ? 'cursor-help' : ''}`}
                  title={getBenchmarkTooltip(nvoSummary.bulgarian.value, nvoSummary.bulgarian.style.benchmark, t) || undefined}
                >
                  {t('schoolCard.nvo.subjectBulgarian')}: {t('academicPerformance.pointsValue', { value: formatPercent(nvoSummary.bulgarian.value, 0) })}
                  {nvoSummary.bulgarian.trend && (
                    <span
                      className={`ml-1 ${nvoSummary.bulgarian.trend.className} cursor-help`}
                      title={getTrendTooltip(nvoDetail.latestBg, nvoDetail.bgAvg, nvoSummary.bulgarian.trend, t) || undefined}
                    >
                      {nvoSummary.bulgarian.trend.arrow}
                    </span>
                  )}
                </span>
              </div>
            </div>
          )}

          {statusInfo && (
            <div className="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-[13px] font-medium" style={statusBadgeStyle}>
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: statusInfo.color }} aria-hidden="true" />
              <span>{statusInfo.label}</span>
            </div>
          )}
        </div>
      </div>

      {(() => {
        const hasMultipleAgeGroupShifts = (school.locations || [])
          .some(location => (location.age_group_shifts || []).length > 1)
        const hasMultipleLocations = (school.locations?.length || 0) > 1
        return hasExpandedContent || hasMultipleLocations || hasMultipleAgeGroupShifts
      })() && (
        <div className="mt-3 flex items-center justify-between text-sm text-neutral-500">
          {(school.locations?.length || 0) > 1 ? (
            <button
              type="button"
              className="text-sm text-neutral-600 hover:text-teal-600 hover:underline transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-500 rounded-sm"
              onClick={handleToggleLocations}
            >
              + {school.locations.length - 1} {t('schools.moreLocations', { count: school.locations.length - 1 })}
            </button>
          ) : (
            ((school.locations || []).some(location => (location.age_group_shifts || []).length > 1)) ? (
              <button
                type="button"
                className="text-sm text-neutral-600 hover:text-teal-600 hover:underline transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-500 rounded-sm"
                onClick={handleToggleLocations}
              >
                {t('schools.showAgeGroupsShifts')}
              </button>
            ) : (
              <span />
            )
          )}
          {hasExpandedContent && (
            <button
              type="button"
              className="text-sm text-neutral-600 hover:text-teal-600 hover:underline transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-500 rounded-sm"
              onClick={(event) => {
                event.stopPropagation()
                setIsExpanded(prev => !prev)
              }}
              aria-expanded={isExpanded}
              aria-controls={expandedId}
            >
              {isExpanded ? t('schoolCard.showLess') : t('schoolCard.showMore')} {isExpanded ? '▲' : '▼'}
            </button>
          )}
        </div>
      )}

      {isLocationsOpen && (orderedLocations.length > 1 || (orderedLocations[0]?.age_group_shifts || []).length > 1) && (
        <LocationsPanel
          school={school}
          orderedLocations={orderedLocations}
          flowGroups={flowGroups}
          canShowOnMap={canShowOnMap}
          focusedLocationId={focusedLocationId}
          overlayActive={overlayActive}
          handleShowAllLocations={handleShowAllLocations}
          handleFocusLocation={handleFocusLocation}
          handleClearLocations={handleClearLocations}
        />
      )}

      {hasExpandedContent && (
        <div
          id={expandedId}
          className={
            `overflow-hidden transition-all duration-300 ` +
            (isExpanded ? 'max-h-[1600px] opacity-100 mt-4' : 'max-h-0 opacity-0')
          }
        >
          <div className="rounded-b-lg border-t border-neutral-200 bg-neutral-50 px-4 py-4 space-y-4">
            {nvoDetail && (
              <NvoDetailTable nvoDetail={nvoDetail} examAverages={examAverages} secondaryLineClass={secondaryLineClass} />
            )}

            {expandedSections.map((section, index) => (
              <div key={section.title} className={index === 0 && !nvoDetail ? '' : ''}>
                <div className="text-[15px] font-semibold text-neutral-900 border-l-4 border-teal-500 pl-2 mb-2">
                  {section.title}
                </div>
                {Array.isArray(section.content) ? (
                  <ul className="space-y-1 text-sm text-neutral-600">
                    {section.content.map(line => (
                      <li key={line}>{line}</li>
                    ))}
                  </ul>
                ) : (
                  <div className="text-sm text-neutral-600">{section.content}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {samePlace.length > 0 && (
        <div className="mt-3 rounded-lg border border-teal-200/70 bg-teal-50/40 px-3 py-2">
          <p className="text-xs text-neutral-500">{t('map.samePlace')}</p>
          <ul className="mt-1 space-y-1">
            {samePlace.map(({ school: member }) => (
              <li key={member.id} className="text-sm">
                <Link
                  to={activeAgeGroup ? `/schools/${member.id}?group=${encodeURIComponent(activeAgeGroup)}` : `/schools/${member.id}`}
                  onClick={(event) => {
                    event.stopPropagation()
                    if (onOpenDetails && isPlainLeftClick(event) && isDesktopViewport()) {
                      event.preventDefault()
                      onOpenDetails(member)
                    }
                  }}
                  className="font-medium text-primary-700 hover:text-primary-800 hover:underline"
                >
                  {getSchoolName(member, i18n.language)}
                </Link>
                <span className="text-neutral-500">{' · '}{schoolLevelLabel(member, t, countryConfig)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex items-center gap-3 pt-3 mt-3 border-t border-neutral-100">
        <button
          onClick={handleCompareClick}
          disabled={!inCompare && !canAddMore}
          className={
            `flex-1 min-h-[44px] md:min-h-0 px-3 py-2 text-sm font-medium rounded-lg transition-colors transition-transform hover:scale-[1.02] ` +
            (inCompare
              ? 'bg-primary-100 text-primary-700 border-2 border-primary-500 hover:bg-primary-200'
              : !canAddMore
              ? 'bg-neutral-100 text-neutral-400 cursor-not-allowed'
              : 'bg-neutral-100 text-neutral-700 hover:bg-neutral-200')
          }
        >
          {inCompare ? (
            <span className="flex items-center justify-center gap-1">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
              {t('schools.comparing')}
            </span>
          ) : (
            t('schools.compare')
          )}
        </button>
        <Link
          to={activeAgeGroup ? `/schools/${school.id}?group=${encodeURIComponent(activeAgeGroup)}` : `/schools/${school.id}`}
          onClick={handleDetailsClick}
          className="flex-1 min-h-[44px] md:min-h-0 inline-flex items-center justify-center px-3 py-2 text-center text-sm font-medium text-white bg-primary-700 hover:bg-primary-800 rounded-lg transition-colors transition-transform hover:scale-[1.02]"
        >
          {t('schools.details')}
        </Link>
      </div>

    </article>
  )
})

export default SchoolCard
