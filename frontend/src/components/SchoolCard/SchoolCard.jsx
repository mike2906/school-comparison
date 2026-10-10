import { memo, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { isDesktopViewport, isPlainLeftClick } from '../../utils/searchViewState'
import { schoolLevelLabel, schoolTypeLabel } from '../../utils/levelLabel'
import { useCountry } from '../../context/CountryContext'
import { languageLabel } from '../../utils/languages'
import { useCompare } from '../../context/CompareContext'
import { getSchoolName, getAddress } from '../../utils/i18n'
import { formatDistance } from '../../utils/distance'
import { getFocusEmoji } from '../../utils/locationFocus'
import { getNvoDetail as getSharedNvoDetail } from '../../utils/nvo'
import { getCanonicalAmenityFlags, getOptionLabel, getStatusInfo, normalizeLanguageFocus } from '../../utils/schoolAttributes'
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
import {
  formatPriceLabel,
  selectPricingCohort,
  statedTuition,
  YEAR_STATUS,
} from '../../utils/pricing'

const typeColors = {
  state: 'bg-teal-700 text-white',
  private: 'bg-violet-600 text-white',
  international: 'bg-blue-700 text-white',
}

const SHIFT_ICONS = {
  full_day: '☀️',
  morning: '🌅',
  afternoon: '🌙',
}

const LANGUAGE_CODES = {
  bulgarian: 'BG',
  english: 'EN',
  german: 'DE',
  french: 'FR',
  spanish: 'ES',
  russian: 'RU',
}

const LANGUAGE_FLAGS = {
  english: '🇬🇧',
  german: '🇩🇪',
  french: '🇫🇷',
  spanish: '🇪🇸',
  russian: '🇷🇺',
}

const FOCUS_ICON = {
  mathematics: '🎓',
  math: '🎓',
  science: '🎓',
  foreign_languages: '📚',
  language: '📚',
  arts: '🎨',
  humanities: '📖',
  economics: '📊',
  general: '🏫',
}

const APPROACH_ICON = {
  montessori: '🌱',
  waldorf: '📖',
  project_based: '🧩',
}

const AMENITY_PRIORITY = [
  { key: 'meals', icon: '🍽️', labelKey: 'schoolCard.amenities.meals' },
  { key: 'transport', icon: '🚌', labelKey: 'schoolCard.amenities.transport' },
  { key: 'extended', icon: '⏰', labelKey: 'schoolCard.amenities.extended' },
  { key: 'smallClasses', icon: '👥', labelKey: 'schoolCard.amenities.smallClasses' },
  { key: 'accessible', icon: '♿', labelKey: 'schoolCard.amenities.accessible' },
  { key: 'specialPrograms', icon: '🎵', labelKey: 'schoolCard.amenities.specialPrograms' },
]

function getLanguageLabel(language, t) {
  return languageLabel(language, t)
}

function getLanguageCode(language) {
  if (!language) return ''
  const lower = language.toLowerCase()
  return LANGUAGE_CODES[lower] || lower.slice(0, 2).toUpperCase()
}

function getPrimaryFeature(attributes, t) {
  const languageFocus = normalizeLanguageFocus(attributes?.language_focus)
  const languagesOfInstruction = (attributes?.languages_of_instruction || []).map(String)
  const teachingApproach = attributes?.teaching_approach || []
  const specialPrograms = attributes?.special_programs || []
  const specialFocus = attributes?.special_focus
  const hasIbProgram = teachingApproach.includes('ib_program') || specialPrograms.includes('ib_program')

  if (languageFocus.length > 0) {
    const focus = languageFocus[0]
    const languageLabel = getLanguageLabel(focus.language, t)
    let primaryText = languageLabel

    if (focus.level === 'bilingual') {
      const codes = [getLanguageCode(focus.language), 'BG'].filter(Boolean).join('/')
      primaryText = t('schoolCard.feature.bilingual', { languages: codes })
    } else if (focus.level === 'immersion') {
      primaryText = t('schoolCard.feature.languageIntensive', { language: languageLabel })
    } else if (focus.level === 'enrichment') {
      primaryText = t('schoolCard.feature.languageEnrichment', { language: languageLabel })
    } else {
      primaryText = t('schoolCard.feature.languageFocus', { language: languageLabel })
    }

    if (hasIbProgram) {
      primaryText = t('schoolCard.feature.curriculumWithProgram', {
        primary: primaryText,
        program: t('schoolCard.feature.ibProgram'),
      })
    }

    const languageKey = focus.language?.toLowerCase()
    const icon = focus.level === 'bilingual' || languageKey === 'english'
      ? '🌐'
      : LANGUAGE_FLAGS[languageKey] || '🌐'
    return {
      icon,
      text: primaryText,
    }
  }

  if (languagesOfInstruction.length > 1) {
    const codes = languagesOfInstruction
      .map(lang => getLanguageCode(lang))
      .filter(Boolean)
    const orderedCodes = codes.includes('BG')
      ? [...codes.filter(code => code !== 'BG'), 'BG']
      : codes
    if (orderedCodes.length > 1) {
      return {
        icon: '🌐',
        text: t('schoolCard.feature.bilingual', { languages: orderedCodes.join('/') }),
      }
    }
  }

  if (specialFocus) {
    const focusKey = `schoolCard.focus.${specialFocus}`
    const focusText = t(focusKey)
    return {
      icon: FOCUS_ICON[specialFocus] || '🎓',
      text: focusText !== focusKey ? focusText : t('schoolCard.focus.general'),
    }
  }

  if (teachingApproach.length > 0) {
    const approach = teachingApproach.find(item => item !== 'ib_program') || teachingApproach[0]
    const approachKey = `schoolCard.approach.${approach}`
    const approachText = t(approachKey)
    return {
      icon: APPROACH_ICON[approach] || '🌱',
      text: approachText !== approachKey ? approachText : getOptionLabel(approach, t),
    }
  }

  return null
}

function formatCurrency(amount, locale) {
  if (amount == null || Number.isNaN(amount)) return null
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: 0,
  }).format(amount)
}

function hexToRgba(hex, alpha) {
  const normalized = hex.replace('#', '')
  const bigint = parseInt(normalized, 16)
  const r = (bigint >> 16) & 255
  const g = (bigint >> 8) & 255
  const b = bigint & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function getNvoDetail(school, t, ageGroup = null, examType = null) {
  return getSharedNvoDetail(school, t, { requireCompleteSubjects: true, ageGroup, examType })
}

function getAmenityFlags(attributes, hasAfterSchool) {
  const facilities = attributes?.facilities || []
  const specialPrograms = attributes?.special_programs || []
  const canonical = getCanonicalAmenityFlags(attributes, hasAfterSchool)

  return {
    meals: canonical.meals,
    transport: canonical.transport,
    extended: canonical.extended,
    smallClasses: Boolean(attributes?.class_size && Number(attributes.class_size) < 16),
    accessible: facilities.includes('accessible'),
    specialPrograms: Boolean(specialPrograms.length > 0),
  }
}

function getScheduleLine(primaryShiftInfo, attributes, t) {
  const shift = primaryShiftInfo?.shift
  if (!shift) return null
  const shiftLabelKey = `schoolCard.shift.${shift}`
  const shiftLabel = t(shiftLabelKey)
  const label = shiftLabel !== shiftLabelKey ? shiftLabel : t(`shifts.${shift}`)
  const baseText = label
  const hasAfterSchool = getCanonicalAmenityFlags(
    attributes,
    primaryShiftInfo?.has_organised_groups
  ).extended

  return {
    text: hasAfterSchool ? `${baseText} • ${t('schoolCard.afterSchoolCare')}` : baseText,
    icon: SHIFT_ICONS[shift] || '⏰',
  }
}

function getLocationAgeGroups(location) {
  if (!location) return []
  if (Array.isArray(location.age_groups)) return location.age_groups.filter(Boolean)
  if (location.age_group) return [location.age_group]
  return []
}

function getAgeGroupShifts(location) {
  if (!location?.age_group_shifts) return []
  return location.age_group_shifts.filter(item => item && item.age_group)
}

function getLocationTags(location) {
  if (!location?.location_tags) return []
  if (!Array.isArray(location.location_tags)) return []
  return location.location_tags.filter(Boolean)
}

function getShiftForAgeGroup(location, ageGroup) {
  const shifts = getAgeGroupShifts(location)
  if (!shifts.length) return null
  if (ageGroup) {
    const match = shifts.find(item => item.age_group === ageGroup)
    if (match) return match
  }
  return shifts[0] || null
}

function buildExpandedSections({
  school,
  attributes,
  locations,
  pricing,
  t,
  locale,
}) {
  const sections = []

  const facilities = attributes?.facilities || []
  const specialPrograms = attributes?.special_programs || []
  const teachingApproach = attributes?.teaching_approach || []
  const activities = attributes?.activities_offered || []
  const canonicalAmenities = getCanonicalAmenityFlags(attributes)

  const languageFocus = normalizeLanguageFocus(attributes?.language_focus)
  const languagesOfInstruction = attributes?.languages_of_instruction || []

  const primaryLanguage = languageFocus[0]?.language || languagesOfInstruction[0]
  const extraLanguages = [...new Set([
    ...languageFocus.map(item => item.language),
    ...languagesOfInstruction,
  ])].filter(Boolean)

  const programItems = new Set()
  specialPrograms.forEach(item => programItems.add(getOptionLabel(item, t)))
  teachingApproach.forEach(item => programItems.add(getOptionLabel(item, t)))
  activities.forEach(item => programItems.add(getOptionLabel(item, t)))

  if (primaryLanguage || extraLanguages.length > 0 || programItems.size > 0) {
    const lines = []
    if (primaryLanguage) {
      lines.push(`${t('schoolCard.labels.primaryLanguage')}: ${getLanguageLabel(primaryLanguage, t)}`)
    }
    if (extraLanguages.length > 1) {
      const others = extraLanguages
        .filter(language => language !== primaryLanguage)
        .map(language => getLanguageLabel(language, t))
      if (others.length > 0) {
        lines.push(`${t('schoolCard.labels.alsoOffered')}: ${others.join(', ')}`)
      }
    }
    if (programItems.size > 0) {
      lines.push(`${t('schoolCard.labels.specialPrograms')}: ${[...programItems].join(', ')}`)
    }
    sections.push({
      title: t('schoolCard.sections.languagesPrograms'),
      content: lines,
    })
  }

  const amenityLines = []
  if (canonicalAmenities.meals) {
    amenityLines.push(`🍽️ ${t('schoolCard.amenities.meals')}`)
  }
  if (canonicalAmenities.transport) {
    amenityLines.push(`🚌 ${t('schoolCard.amenities.transport')}`)
  }
  if (attributes?.class_size && Number(attributes.class_size) > 0) {
    amenityLines.push(`👥 ${t('schoolCard.amenities.perClass', { count: attributes.class_size })}`)
  } else if (attributes?.class_size && Number(attributes.class_size) < 16) {
    amenityLines.push(`👥 ${t('schoolCard.amenities.smallClasses')}`)
  }
  if (facilities.includes('accessible')) {
    amenityLines.push(`♿ ${t('schoolCard.amenities.accessible')}`)
  }
  if (specialPrograms.length > 0) {
    amenityLines.push(`🎵 ${t('schoolCard.amenities.specialPrograms')}`)
  }

  if (amenityLines.length > 0) {
    sections.push({
      title: t('schoolCard.sections.facilitiesAmenities'),
      content: amenityLines,
    })
  }

  const scheduleLines = locations.flatMap(location => {
    const shifts = getAgeGroupShifts(location).filter(item => item.shift)
    return shifts.map(item => {
      const ageGroupLabel = t(`ageGroups.${item.age_group}`)
      const shiftLabelKey = `schoolCard.shift.${item.shift}`
      const shiftLabel = t(shiftLabelKey)
      const label = shiftLabel !== shiftLabelKey ? shiftLabel : t(`shifts.${item.shift}`)
      const baseText = label
      const afterSchool = item.has_organised_groups ? ` • ${t('schoolCard.afterSchoolCare')}` : ''
      return `${ageGroupLabel}: ${baseText}${afterSchool}`
    })
  })

  const scheduleDetails = [
    ...scheduleLines,
    ...(attributes?.daily_schedule || []),
  ]
  if (scheduleDetails.length > 0) {
    sections.push({
      title: t('schoolCard.sections.scheduleDetails'),
      content: scheduleDetails,
    })
  }

  if (pricing.length > 0 && school.school_type !== 'state') {
    const pricingLines = pricing.map(item => {
      const category = t(`pricing.${item.category}`)
      const periodKey = item.period ? t(`pricing.${item.period}`) : t('pricing.periodNotStated')
      const plan = item.plan_name ? `${t('pricing.plan')}: ${item.plan_name}` : ''
      const year = item.academic_year ? `${t('pricing.academicYear')}: ${item.academic_year}` : ''
      const meta = [plan, year].filter(Boolean).join(' • ')
      const source = item.source ? t(`priceSource.${item.source}`) : ''
      // BGN rows are shown in euro, as on the detail and compare pages.
      const priceLabel = formatPriceLabel(item, locale, t)
      const periodLabel = periodKey ? ` (${periodKey})` : ''
      const metaLabel = meta ? ` • ${meta}` : ''
      const sourceLabel = source ? ` • ${source}` : ''
      return `${category}${periodLabel}${metaLabel}: ${priceLabel}${sourceLabel}`
    }).filter(Boolean)

    if (pricingLines.length > 0) {
      sections.push({
        title: t('schoolCard.sections.tuitionFees'),
        content: pricingLines,
      })
    }
  }

  const contactParts = []
  const primaryLocation = locations.find(loc => loc.is_primary) || locations[0]
  if (primaryLocation?.phone) {
    contactParts.push(`${t('schools.phone')}: ${primaryLocation.phone}`)
  }
  if (school.website_url) {
    contactParts.push(`${t('schools.website')}: ${school.website_url}`)
  }

  if (contactParts.length > 0) {
    sections.push({
      title: t('schoolCard.sections.contact'),
      content: contactParts,
    })
  }

  return sections
}

// Memoized: the search page renders hundreds of cards, and callbacks receive the
// school so the parent can pass stable handlers instead of per-card closures.
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

  const parseCoordinate = (value) => {
    if (value === null || value === undefined) return null
    if (typeof value === 'string') {
      const normalized = value.replace(',', '.').trim()
      const parsed = Number.parseFloat(normalized)
      return Number.isFinite(parsed) ? parsed : null
    }
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }

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
  const pricingCohort = useMemo(() => selectPricingCohort(pricing), [pricing])
  const pricingYearLabel = useMemo(() => {
    if (!pricingCohort) return null
    if (pricingCohort.yearStatus === YEAR_STATUS.NOT_STATED) {
      return t('pricing.yearNotStated')
    }
    return pricingCohort.academicYear
  }, [pricingCohort, t])
  // Only a current-year price wears the highlighted badge, so an older fee is never
  // styled as though it were this year's.
  const pricingYearBadgeClass =
    pricingCohort?.yearStatus === YEAR_STATUS.CURRENT
      ? 'text-teal-700 bg-teal-50 border-teal-200'
      : 'text-neutral-600 bg-neutral-100 border-neutral-300'

  const admissionsInfo = useMemo(() => {
    const locale = i18n.language?.startsWith('bg') ? 'bg-BG' : 'en-US'
    const isPrivate = school.school_type === 'private' || school.school_type === 'international'
    const admissionInfo = school.admission_info || {}

    if (isPrivate) {
      // The fee as the school states it, in euro: never annualised, since schools bill
      // over 9, 10 or 12 months (the detail page and compare show it the same way).
      const tuition = statedTuition(pricing)
      const price = tuition && (formatCurrency(tuition.min, locale) === formatCurrency(tuition.max, locale)
        ? formatCurrency(tuition.min, locale)
        : `${formatCurrency(tuition.min, locale)}–${formatCurrency(tuition.max, locale)}`)
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
  }, [school, pricing, attributes, i18n.language, t, primaryLocation])

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
    locale: i18n.language?.startsWith('bg') ? 'bg-BG' : 'en-US',
  }), [school, attributes, locations, pricing, t, i18n.language])

  const hasExpandedContent = expandedSections.length > 0 || Boolean(nvoDetail)
  const expandedId = `school-details-${school.id}`
  const showScheduleOnMobile = !admissionsInfo
  const distanceValue = typeof school.distance === 'number' ? school.distance : null
  // Only a known distance is shown; the list header prompts for a location once instead.
  const distanceLabel = distanceValue != null
    ? formatDistance(distanceValue, { approximate: school.distanceApproximate })
    : null
  const distanceStyle = distanceValue == null
    ? { backgroundColor: '#f3f4f6', color: '#6b7280' }
    : distanceValue < 2
    ? { backgroundColor: 'rgba(16,185,129,0.1)', color: '#059669' }
    : distanceValue <= 5
    ? { backgroundColor: 'rgba(245,158,11,0.1)', color: '#d97706' }
    : { backgroundColor: 'rgba(239,68,68,0.1)', color: '#dc2626' }
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
        {distanceLabel && (
          <span
            className="inline-flex flex-shrink-0 items-center gap-1 text-xs font-medium px-2 py-0.5 rounded-full"
            style={distanceStyle}
            aria-label={t(
              school.distanceApproximate ? 'schoolCard.aria.distanceApprox' : 'schoolCard.aria.distance',
              { distance: formatDistance(distanceValue) },
            )}
          >
            📍 {distanceLabel}
          </span>
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
        <div className="mt-3 rounded-xl border border-neutral-200 bg-white px-4 py-3 space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <p className="text-sm font-semibold text-neutral-900">
                {school.school_type === 'state' ? t('schools.ageGroupsShifts') : t('schools.locations')}
              </p>
              {flowGroups.length > 1 && (
                <div className="flex flex-wrap items-center gap-1 text-xs text-neutral-500">
                  {flowGroups.map((group, idx) => (
                    <span key={group} className="inline-flex items-center gap-1">
                      <span className="px-2 py-0.5 rounded-md bg-neutral-100 text-neutral-600 font-medium">
                        {t(`ageGroups.${group}`)}
                      </span>
                      {idx < flowGroups.length - 1 && <span className="text-neutral-300">→</span>}
                    </span>
                  ))}
                </div>
              )}
            </div>
            {orderedLocations.length > 1 && (
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  className={`px-2.5 py-1 text-xs font-semibold rounded-full border transition-colors ${
                    canShowOnMap
                      ? 'border-primary-200 text-primary-700 hover:bg-primary-50'
                      : 'border-neutral-200 text-neutral-400 cursor-not-allowed'
                  }`}
                  onClick={handleShowAllLocations}
                  disabled={!canShowOnMap}
                  title={canShowOnMap ? '' : t('schools.mapLocationMissing')}
                >
                  {t('schools.showAllOnMap')}
                </button>
              </div>
            )}
          </div>

          <div className="space-y-2">
            {orderedLocations.map((locationItem, idx) => {
              const locationKey = locationItem.id ?? `${school.id}-${idx}`
              const isFocused = focusedLocationId === locationItem.id
              const addressLabel = getAddress(locationItem, i18n.language)
              const locationGroups = getLocationAgeGroups(locationItem)
              const shiftEntries = getAgeGroupShifts(locationItem)
              const shiftByGroup = new Map(shiftEntries.map(entry => [entry.age_group, entry]))
              const locationTags = getLocationTags(locationItem)
              const hasMultipleLocations = orderedLocations.length > 1
              return (
                <button
                  key={locationKey}
                  type="button"
                  onClick={(event) => handleFocusLocation(event, locationItem.id)}
                  disabled={!hasMultipleLocations}
                  className={`w-full text-left rounded-lg px-3 py-2 transition-colors ${
                    hasMultipleLocations
                      ? isFocused
                        ? 'border border-primary-300 bg-primary-50'
                        : 'border border-neutral-200 bg-white hover:bg-neutral-50'
                      : 'bg-transparent'
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="space-y-1">
                      {addressLabel && (
                        <div className="text-sm font-semibold text-neutral-800">{addressLabel}</div>
                      )}
                      {locationTags.length > 0 && (
                        <div className="flex flex-wrap gap-1.5">
                          {locationTags.map(tag => (
                            <span
                              key={tag}
                              className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600"
                            >
                              {getFocusEmoji(tag) ? `${getFocusEmoji(tag)} ` : ''}
                              {t(`locationTags.${tag}`, { defaultValue: tag })}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="space-y-1">
                        {locationGroups.map(group => {
                          const shiftInfo = shiftByGroup.get(group)
                          return (
                            <div
                              key={group}
                              className="text-xs text-neutral-600"
                            >
                              <span className="font-semibold text-neutral-700">
                                {t(`ageGroups.${group}`)}
                              </span>
                              {shiftInfo?.shift && (
                                <span className="text-neutral-500"> • {t(`shifts.${shiftInfo.shift}`)}</span>
                              )}
                              {shiftInfo?.has_organised_groups && (
                                <span className="text-primary-600"> • {t('schools.organisedGroups')}</span>
                              )}
                            </div>
                          )
                        })}
                      </div>
                    </div>
                    {!Number.isFinite(parseCoordinate(locationItem.lat)) || !Number.isFinite(parseCoordinate(locationItem.lng)) ? (
                      <span className="text-[11px] uppercase tracking-wide text-neutral-400">
                        {t('schools.mapLocationMissing')}
                      </span>
                    ) : (
                      locationItem.is_primary && (
                        <span className="text-[10px] uppercase tracking-wide text-neutral-400">
                          {t('schools.primaryLocation')}
                        </span>
                      )
                    )}
                  </div>
                </button>
              )
            })}
          </div>

          {overlayActive && (
            <div className="flex items-center justify-between text-xs text-neutral-400">
              <span>{t('schools.locationsShownOnMap')}</span>
              <button
                type="button"
                className="text-xs font-semibold text-neutral-500 hover:text-neutral-700"
                onClick={handleClearLocations}
              >
                {t('schools.clearLocations')}
              </button>
            </div>
          )}
        </div>
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
              <div>
                <div className="text-[15px] font-semibold text-neutral-900 border-l-4 border-teal-500 pl-2 mb-2">
                  {t('schoolCard.sections.academicPerformance')}
                </div>
                <div className="text-sm text-neutral-600 mb-3">
                  {t('schoolCard.nvo.latestTitle', { grade: nvoDetail.gradeLabel })}
                </div>
                {nvoDetail.hasAverage ? (
                  <>
                    <div className="text-sm text-neutral-700 font-semibold border-b border-neutral-200 pb-2 grid grid-cols-[1.4fr_1fr_1fr_0.8fr] gap-2">
                      <span>{t('schoolCard.nvo.subjectHeader')}</span>
                      <span className="text-center">{nvoDetail.latestYear || t('schoolCard.nvo.latest')}</span>
                      <span className="text-center">{t('schoolCard.nvo.avgHeader')}</span>
                      <span className="text-center">{t('schoolCard.nvo.trendHeader')}</span>
                    </div>
                    {[
                      {
                        label: t('schoolCard.nvo.subjectMath'),
                        latest: nvoDetail.latestMath,
                        avg: nvoDetail.mathAvg,
                        style: getNvoValueStyle({
                          value: nvoDetail.latestMath ?? nvoDetail.mathAvg,
                          examType: nvoDetail.examType,
                          year: nvoDetail.latestMathYear ?? nvoDetail.latestYear,
                          subjectKey: 'math',
                          examAverages,
                        }),
                      },
                      {
                        label: t('schoolCard.nvo.subjectBulgarian'),
                        latest: nvoDetail.latestBg,
                        avg: nvoDetail.bgAvg,
                        style: getNvoValueStyle({
                          value: nvoDetail.latestBg ?? nvoDetail.bgAvg,
                          examType: nvoDetail.examType,
                          year: nvoDetail.latestBgYear ?? nvoDetail.latestYear,
                          subjectKey: 'bulgarian',
                          examAverages,
                        }),
                      },
                    ].map(item => {
                      const latestValue = item.latest ?? item.avg
                      const trend = item.latest != null ? getTrendInfo(item.latest, item.avg) : null
                      const diff = trend ? Math.abs(trend.diff) : 0
                      return (
                        <div key={item.label} className="grid grid-cols-[1.4fr_1fr_1fr_0.8fr] gap-2 py-2 border-b border-neutral-100 text-sm">
                          <span>{item.label}</span>
                          <span
                            className={`text-center font-mono ${item.style.text} ${item.style.benchmark ? 'cursor-help' : ''}`}
                            title={getBenchmarkTooltip(latestValue, item.style.benchmark, t) || undefined}
                          >
                            {t('academicPerformance.pointsValue', { value: formatPercent(latestValue, 1) })}
                          </span>
                          <span className="text-center font-mono text-neutral-700">
                            {t('academicPerformance.pointsValue', { value: formatPercent(item.avg, 1) })}
                          </span>
                          <span
                            className={`text-center font-mono ${trend ? `${trend.className} cursor-help` : 'text-neutral-400'}`}
                            title={getTrendTooltip(item.latest, item.avg, trend, t) || undefined}
                          >
                            {trend ? `${trend.arrow} ${t('academicPerformance.pointsValue', { value: formatPercent(diff, 1) })}` : '—'}
                          </span>
                        </div>
                      )
                    })}
                    <div className={`mt-2 text-[13px] ${secondaryLineClass}`}>
                      {t('schoolCard.nvo.basedOnYears', { start: nvoDetail.minYear, end: nvoDetail.maxYear })}
                    </div>
                  </>
                ) : (
                  <div className="text-sm text-neutral-700">
                    <div className="flex items-center justify-between border-b border-neutral-100 py-2">
                      <span>{t('schoolCard.nvo.subjectMath')}</span>
                      <span
                        className={`font-mono ${getNvoValueStyle({
                          value: nvoDetail.latestMath,
                          examType: nvoDetail.examType,
                          year: nvoDetail.latestMathYear ?? nvoDetail.latestYear,
                          subjectKey: 'math',
                          examAverages,
                        }).text} ${getNvoValueStyle({
                          value: nvoDetail.latestMath,
                          examType: nvoDetail.examType,
                          year: nvoDetail.latestMathYear ?? nvoDetail.latestYear,
                          subjectKey: 'math',
                          examAverages,
                        }).benchmark ? 'cursor-help' : ''}`}
                        title={getBenchmarkTooltip(
                          nvoDetail.latestMath,
                          getNvoValueStyle({
                            value: nvoDetail.latestMath,
                            examType: nvoDetail.examType,
                            year: nvoDetail.latestMathYear ?? nvoDetail.latestYear,
                            subjectKey: 'math',
                            examAverages,
                          }).benchmark,
                          t
                        ) || undefined}
                      >
                        {t('academicPerformance.pointsValue', { value: formatPercent(nvoDetail.latestMath, 1) })}
                      </span>
                    </div>
                    <div className="flex items-center justify-between py-2">
                      <span>{t('schoolCard.nvo.subjectBulgarian')}</span>
                      <span
                        className={`font-mono ${getNvoValueStyle({
                          value: nvoDetail.latestBg,
                          examType: nvoDetail.examType,
                          year: nvoDetail.latestBgYear ?? nvoDetail.latestYear,
                          subjectKey: 'bulgarian',
                          examAverages,
                        }).text} ${getNvoValueStyle({
                          value: nvoDetail.latestBg,
                          examType: nvoDetail.examType,
                          year: nvoDetail.latestBgYear ?? nvoDetail.latestYear,
                          subjectKey: 'bulgarian',
                          examAverages,
                        }).benchmark ? 'cursor-help' : ''}`}
                        title={getBenchmarkTooltip(
                          nvoDetail.latestBg,
                          getNvoValueStyle({
                            value: nvoDetail.latestBg,
                            examType: nvoDetail.examType,
                            year: nvoDetail.latestBgYear ?? nvoDetail.latestYear,
                            subjectKey: 'bulgarian',
                            examAverages,
                          }).benchmark,
                          t
                        ) || undefined}
                      >
                        {t('academicPerformance.pointsValue', { value: formatPercent(nvoDetail.latestBg, 1) })}
                      </span>
                    </div>
                  </div>
                )}
              </div>
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
