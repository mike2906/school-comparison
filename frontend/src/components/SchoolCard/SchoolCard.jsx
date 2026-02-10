import { forwardRef, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useCompare } from '../../context/CompareContext'
import { getSchoolName, getAddress } from '../../utils/i18n'
import { formatDistance } from '../../utils/distance'

const typeColors = {
  state: 'bg-teal-500 text-white',
  private: 'bg-violet-500 text-white',
  international: 'bg-blue-500 text-white',
}

const STATUS_COLORS = {
  accepting: '#10b981',
  waitlist: '#f59e0b',
  full: '#ef4444',
  unknown: '#9ca3af',
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
  if (!language) return ''
  const lower = language.toLowerCase()
  const key = `advancedFilters.languages.${lower}`
  const translated = t(key)
  if (translated !== key) return translated
  return lower.charAt(0).toUpperCase() + lower.slice(1)
}

function getLanguageCode(language) {
  if (!language) return ''
  const lower = language.toLowerCase()
  return LANGUAGE_CODES[lower] || lower.slice(0, 2).toUpperCase()
}

function getOptionLabel(option, t) {
  if (!option) return ''
  const key = `advancedFilters.options.${option}`
  const translated = t(key)
  if (translated !== key) return translated
  return option.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

function normalizeLanguageFocus(languageFocus = []) {
  const normalized = []
  const items = Array.isArray(languageFocus) ? languageFocus : [languageFocus]
  items.forEach((item) => {
    if (!item) return
    if (typeof item === 'string') {
      const [language, level] = item.split(':')
      normalized.push({ language, level })
      return
    }
    if (typeof item === 'object') {
      normalized.push({
        language: item.language,
        level: item.level,
      })
    }
  })
  return normalized.filter(item => item.language)
}

function getStatusInfo(school, t) {
  const rawStatus =
    school.admission_info?.status ||
    school.attributes?.admission_status ||
    school.attributes?.enrollment_status ||
    ''
  const statusValue = String(rawStatus).toLowerCase()

  if (statusValue.includes('accept') || statusValue.includes('open') || statusValue.includes('available')) {
    return { key: 'accepting', color: STATUS_COLORS.accepting, label: t('schoolCard.status.accepting') }
  }
  if (statusValue.includes('wait')) {
    return { key: 'waitlist', color: STATUS_COLORS.waitlist, label: t('schoolCard.status.waitlist') }
  }
  if (statusValue.includes('full') || statusValue.includes('closed')) {
    return { key: 'full', color: STATUS_COLORS.full, label: t('schoolCard.status.full') }
  }

  return { key: 'unknown', color: STATUS_COLORS.unknown, label: t('schoolCard.status.unknown') }
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

  return {
    icon: '📘',
    text: t('schoolCard.feature.standardCurriculum'),
  }
}

function formatCurrency(amount, locale) {
  if (amount == null || Number.isNaN(amount)) return null
  return new Intl.NumberFormat(locale, {
    maximumFractionDigits: 0,
  }).format(amount)
}

function getPriceBase(item) {
  if (item?.amount_min != null) return Number(item.amount_min)
  if (item?.amount != null) return Number(item.amount)
  if (item?.amount_max != null) return Number(item.amount_max)
  return null
}

function getYearlyEquivalent(item) {
  const base = getPriceBase(item)
  if (base == null) return null
  if (item.period === 'yearly') return base
  if (item.period === 'monthly') return base * 12
  if (item.period === 'quarter') return base * 4
  return null
}

function getMinTuitionPriceYearly(pricing = []) {
  const tuition = pricing.filter(item => item.category === 'tuition')
  if (tuition.length === 0) return null

  let minValue = null
  let currency = null

  tuition.forEach(item => {
    const yearlyValue = getYearlyEquivalent(item)
    if (yearlyValue == null) return
    if (minValue == null || yearlyValue < minValue) {
      minValue = yearlyValue
      currency = item.currency || 'BGN'
    }
  })

  if (minValue == null) return null
  return { amount: minValue, currency }
}

function getLatestAcademicYear(pricing = []) {
  const years = pricing
    .map(item => item.academic_year)
    .filter(Boolean)
  if (years.length === 0) return null
  const parsed = years
    .map(year => {
      const match = String(year).match(/(\d{4})/)
      return match ? { year, start: Number(match[1]) } : { year, start: -1 }
    })
  parsed.sort((a, b) => b.start - a.start)
  return parsed[0]?.year || null
}

function getAdmissionRequirement(rawRequirement, t) {
  if (!rawRequirement) return null
  const requirementValue = typeof rawRequirement === 'string'
    ? rawRequirement
    : rawRequirement?.type || rawRequirement?.requirement || rawRequirement?.method

  if (!requirementValue) return null

  const normalized = String(requirementValue).toLowerCase()
  if (normalized.includes('interview')) {
    return { icon: '📝', text: t('schoolCard.admissions.interviewRequired') }
  }
  if (normalized.includes('test') || normalized.includes('exam')) {
    return { icon: '📋', text: t('schoolCard.admissions.testRequired') }
  }
  if (normalized.includes('none') || normalized.includes('no')) {
    return { icon: '✅', text: t('schoolCard.admissions.noEntranceExam') }
  }

  return null
}

function getLastAdmittedPoints(admissionInfo, ageGroup) {
  const thresholds = admissionInfo?.historical_thresholds || []
  if (thresholds.length === 0) return null

  const relevant = ageGroup
    ? thresholds.filter(item => item.age_group === ageGroup)
    : thresholds

  if (relevant.length === 0) return null

  const latest = relevant.reduce((acc, item) => (item.year > acc.year ? item : acc), relevant[0])
  const rounds = latest.rounds || []
  if (rounds.length === 0) return null

  const lastRound = rounds.reduce((acc, item) => (item.round > acc.round ? item : acc), rounds[0])

  return {
    points: lastRound.last_admitted_points,
    year: latest.year,
  }
}

function getMinNvoScore(admissionInfo) {
  const scores = admissionInfo?.historical_min_scores || []
  if (scores.length === 0) return null
  const latest = scores.reduce((acc, item) => (item.year > acc.year ? item : acc), scores[0])
  return {
    score: latest.min_score,
    year: latest.year,
  }
}

function formatPercent(value, decimals = 1) {
  if (value == null || Number.isNaN(value)) return null
  return Number(value).toFixed(decimals)
}

function getPerformanceStyle(value) {
  if (value >= 75) return { text: 'text-emerald-500' }
  if (value >= 60) return { text: 'text-amber-500' }
  return { text: 'text-red-500' }
}

function getTrendInfo(latest, average) {
  if (latest == null || average == null) return null
  const diff = latest - average
  if (diff >= 2) return { arrow: '↑', className: 'text-emerald-500', diff }
  if (diff <= -2) return { arrow: '↓', className: 'text-red-500', diff }
  return { arrow: '→', className: 'text-neutral-400', diff }
}

function hexToRgba(hex, alpha) {
  const normalized = hex.replace('#', '')
  const bigint = parseInt(normalized, 16)
  const r = (bigint >> 16) & 255
  const g = (bigint >> 8) & 255
  const b = bigint & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

function getNvoDetail(school, t) {
  const examResults = school.exam_results || []
  if (examResults.length === 0) return null

  const educationLevel = school.education_level
  const examTypeMap = {
    primary: { examType: 'nvo_4', gradeKey: 'schoolCard.nvo.grade4' },
    lower_secondary: { examType: 'nvo_7', gradeKey: 'schoolCard.nvo.grade7' },
    upper_secondary: { examType: 'nvo_10', gradeKey: 'schoolCard.nvo.grade12' },
  }

  const examConfig = examTypeMap[educationLevel]
  if (!examConfig) return null

  const relevant = examResults.filter(result => result.exam_type === examConfig.examType)
  if (relevant.length === 0) return null

  const subjects = {
    bulgarian: [],
    math: [],
  }

  relevant.forEach(result => {
    const subject = String(result.subject || '').toLowerCase()
    const metric = String(result.metric || '').toLowerCase()
    if (!metric.includes('average')) return
    if (subject.includes('bulgarian')) {
      subjects.bulgarian.push(result)
    } else if (subject.includes('math')) {
      subjects.math.push(result)
    }
  })

  const yearsBulgarian = new Set(subjects.bulgarian.map(item => item.year))
  const yearsMath = new Set(subjects.math.map(item => item.year))

  const hasAverage = yearsBulgarian.size >= 3 && yearsMath.size >= 3

  const computeAverage = (items) => {
    const sorted = [...items].sort((a, b) => b.year - a.year).slice(0, 5)
    const values = sorted.map(item => Number(item.value)).filter(value => !Number.isNaN(value))
    if (values.length === 0) return null
    return {
      average: values.reduce((sum, value) => sum + value, 0) / values.length,
      years: sorted.map(item => item.year),
    }
  }

  const mathData = hasAverage ? computeAverage(subjects.math) : null
  const bgData = hasAverage ? computeAverage(subjects.bulgarian) : null

  const latestMath = subjects.math.sort((a, b) => b.year - a.year)[0]
  const latestBg = subjects.bulgarian.sort((a, b) => b.year - a.year)[0]

  const overallAvg = hasAverage ? (mathData.average + bgData.average) / 2 : null
  const colorClass = overallAvg == null
    ? 'text-neutral-600'
    : overallAvg >= 75
    ? 'text-emerald-500'
    : overallAvg >= 60
    ? 'text-amber-500'
    : 'text-red-500'

  const yearsUsed = hasAverage ? [...new Set([...mathData.years, ...bgData.years])] : []
  const minYear = hasAverage ? Math.min(...yearsUsed) : null
  const maxYear = hasAverage ? Math.max(...yearsUsed) : null

  return {
    gradeLabel: t(examConfig.gradeKey),
    mathAvg: mathData?.average ?? null,
    bgAvg: bgData?.average ?? null,
    latestMath: latestMath ? Number(latestMath.value) : null,
    latestBg: latestBg ? Number(latestBg.value) : null,
    latestYear: Math.max(latestMath?.year || 0, latestBg?.year || 0),
    colorClass,
    minYear,
    maxYear,
    hasAverage,
  }
}

function getAmenityFlags(attributes, primaryLocation) {
  const facilities = attributes?.facilities || []
  const specialPrograms = attributes?.special_programs || []

  return {
    meals: Boolean(attributes?.has_canteen || facilities.includes('cafeteria') || specialPrograms.includes('meals_provided')),
    transport: Boolean(facilities.includes('transportation') || attributes?.transportation_available),
    extended: Boolean(primaryLocation?.has_organised_groups || specialPrograms.includes('extended_day') || attributes?.after_school_care),
    smallClasses: Boolean(attributes?.class_size && Number(attributes.class_size) < 16),
    accessible: Boolean(attributes?.accessible || facilities.includes('accessible')),
    specialPrograms: Boolean(specialPrograms.length > 0),
  }
}

function getScheduleLine(primaryLocation, attributes, t) {
  const shift = primaryLocation?.shift
  if (!shift) return null
  const shiftLabelKey = `schoolCard.shift.${shift}`
  const shiftLabel = t(shiftLabelKey)
  const label = shiftLabel !== shiftLabelKey ? shiftLabel : t(`shifts.${shift}`)
  const hours = attributes?.schedule_hours?.[shift]
  const baseText = hours ? `${label} (${hours})` : label
  const hasAfterSchool = Boolean(
    primaryLocation?.has_organised_groups ||
    attributes?.after_school_care ||
    (attributes?.special_programs || []).includes('extended_day')
  )

  return {
    text: hasAfterSchool ? `${baseText} • ${t('schoolCard.afterSchoolCare')}` : baseText,
    icon: SHIFT_ICONS[shift] || '⏰',
  }
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
  if (attributes?.has_canteen || facilities.includes('cafeteria')) {
    amenityLines.push(`🍽️ ${t('schoolCard.amenities.meals')}`)
  }
  if (facilities.includes('transportation') || attributes?.transportation_available) {
    amenityLines.push(`🚌 ${t('schoolCard.amenities.transport')}`)
  }
  if (attributes?.class_size && Number(attributes.class_size) > 0) {
    amenityLines.push(`👥 ${t('schoolCard.amenities.perClass', { count: attributes.class_size })}`)
  } else if (attributes?.class_size && Number(attributes.class_size) < 16) {
    amenityLines.push(`👥 ${t('schoolCard.amenities.smallClasses')}`)
  }
  if (attributes?.accessible || facilities.includes('accessible')) {
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

  const scheduleLines = locations
    .filter(location => location.shift)
    .map(location => {
      const ageGroup = location.age_group ? t(`ageGroups.${location.age_group}`) : ''
      const shiftLabelKey = `schoolCard.shift.${location.shift}`
      const shiftLabel = t(shiftLabelKey)
      const label = shiftLabel !== shiftLabelKey ? shiftLabel : t(`shifts.${location.shift}`)
      const hours = attributes?.schedule_hours?.[location.shift]
      const baseText = hours ? `${label} (${hours})` : label
      const afterSchool = location.has_organised_groups ? ` • ${t('schoolCard.afterSchoolCare')}` : ''
      return ageGroup ? `${ageGroup}: ${baseText}${afterSchool}` : `${baseText}${afterSchool}`
    })

  if (scheduleLines.length > 0) {
    sections.push({
      title: t('schoolCard.sections.scheduleDetails'),
      content: scheduleLines,
    })
  }

  if (pricing.length > 0 && school.school_type !== 'state') {
    const pricingLines = pricing.map(item => {
      const amountMin = item.amount_min != null ? formatCurrency(item.amount_min, locale) : null
      const amountMax = item.amount_max != null ? formatCurrency(item.amount_max, locale) : null
      const amount = item.amount != null ? formatCurrency(item.amount, locale) : null
      const category = t(`pricing.${item.category}`)
      const periodKey = item.period ? t(`pricing.${item.period}`) : ''
      const plan = item.plan_name ? `${t('pricing.plan')}: ${item.plan_name}` : ''
      const year = item.academic_year ? `${t('pricing.academicYear')}: ${item.academic_year}` : ''
      const meta = [plan, year].filter(Boolean).join(' • ')
      const source = item.source ? t(`priceSource.${item.source}`) : ''
      const priceLabel = amountMin && amountMax
        ? `${amountMin}–${amountMax} ${item.currency || 'BGN'}`
        : amount
          ? `${amount} ${item.currency || 'BGN'}`
          : t('pricing.priceOnRequest')
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

const SchoolCard = forwardRef(function SchoolCard(
  {
    school,
    isSelected,
    onClick,
    onHover,
    onHoverEnd,
    location,
    ageGroupOrder = [],
    isLocationsOpen = false,
    locationOverlay = null,
    onToggleLocations,
    onShowAllLocations,
    onFocusLocation,
    onClearLocations,
  },
  ref
) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { addToCompare, removeFromCompare, isInCompare, canAddMore } = useCompare()
  const [isExpanded, setIsExpanded] = useState(false)

  const displayType = school.school_type === 'international' ? 'private' : school.school_type
  const colors = typeColors[displayType] || typeColors.state
  const primaryLocation = location || school.locations?.find(l => l.is_primary) || school.locations?.[0]
  const inCompare = isInCompare(school.id)

  const attributes = school.attributes || {}
  const pricing = school.pricing || []
  const examResults = school.exam_results || []
  const locations = school.locations || []

  const schoolName = getSchoolName(school, i18n.language)
  const address = primaryLocation ? getAddress(primaryLocation, i18n.language) : null

  const overlayActive = locationOverlay?.schoolId === school.id
  const focusedLocationId = overlayActive ? locationOverlay?.focusLocationId : null

  const orderedLocations = useMemo(() => {
    if (!locations.length) return []
    const orderMap = new Map(ageGroupOrder.map((key, idx) => [key, idx]))
    return [...locations].sort((a, b) => {
      const aIndex = orderMap.has(a.age_group) ? orderMap.get(a.age_group) : 999
      const bIndex = orderMap.has(b.age_group) ? orderMap.get(b.age_group) : 999
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
      if (!locationItem.age_group || seen.has(locationItem.age_group)) return
      seen.add(locationItem.age_group)
      groups.push(locationItem.age_group)
    })
    return groups
  }, [orderedLocations])

  const statusInfo = useMemo(() => getStatusInfo(school, t), [school, t])
  const primaryFeature = useMemo(() => getPrimaryFeature(attributes, t), [attributes, t])
  const scheduleLine = useMemo(() => getScheduleLine(primaryLocation, attributes, t), [primaryLocation, attributes, t])
  const pricingYear = useMemo(() => getLatestAcademicYear(pricing), [pricing])

  const admissionsInfo = useMemo(() => {
    const locale = i18n.language?.startsWith('bg') ? 'bg-BG' : 'en-US'
    const isPrivate = school.school_type === 'private' || school.school_type === 'international'
    const admissionInfo = school.admission_info || {}

    if (isPrivate) {
      const yearlyPrice = getMinTuitionPriceYearly(pricing)
      const priceLabel = yearlyPrice?.amount != null
        ? t('schoolCard.admissions.fromPrice', {
          price: formatCurrency(yearlyPrice.amount, locale),
          currency: yearlyPrice.currency || t('pricing.currency'),
          period: t('schoolCard.period.year'),
        })
        : null

      const requirement = getAdmissionRequirement(
        admissionInfo?.requirements || attributes?.entry_requirements || attributes?.admission_requirement,
        t
      )

      if (priceLabel && requirement) {
        return { icon: '💰', text: `${priceLabel} • ${requirement.icon} ${requirement.text}` }
      }
      if (priceLabel) return { icon: '💰', text: priceLabel }
      if (requirement) return { icon: requirement.icon, text: requirement.text }
      return { icon: '💰', text: t('schoolCard.admissions.admissionsUnavailable') }
    }

    if (school.school_type === 'state' && school.education_level === 'kindergarten') {
      const lastAdmitted = getLastAdmittedPoints(admissionInfo, primaryLocation?.age_group)
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
      return { icon: '🎯', text: t('schoolCard.admissions.admissionsUnavailable') }
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
      return { icon: '🎯', text: t('schoolCard.admissions.admissionsUnavailable') }
    }

    if (school.school_type === 'state' && school.education_level === 'lower_secondary') {
      return { icon: '📝', text: t('schoolCard.admissions.districtEnrollment') }
    }

    if (school.school_type === 'state' && school.education_level === 'primary') {
      return { icon: '📝', text: t('schoolCard.admissions.districtEnrollment') }
    }

    return null
  }, [school, pricing, attributes, i18n.language, t, primaryLocation])

  const nvoDetail = useMemo(() => {
    return getNvoDetail(school, t)
  }, [school, t])

  const nvoSummary = useMemo(() => {
    if (!nvoDetail) return null
    const mathValue = nvoDetail.latestMath ?? nvoDetail.mathAvg
    const bgValue = nvoDetail.latestBg ?? nvoDetail.bgAvg
    return {
      gradeLabel: nvoDetail.gradeLabel,
      math: {
        value: mathValue,
        trend: nvoDetail.hasAverage && nvoDetail.latestMath != null
          ? getTrendInfo(nvoDetail.latestMath, nvoDetail.mathAvg)
          : null,
      },
      bulgarian: {
        value: bgValue,
        trend: nvoDetail.hasAverage && nvoDetail.latestBg != null
          ? getTrendInfo(nvoDetail.latestBg, nvoDetail.bgAvg)
          : null,
      },
    }
  }, [nvoDetail, t])

  const amenityItems = useMemo(() => {
    const flags = getAmenityFlags(attributes, primaryLocation)
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
  const distanceLabel = distanceValue != null ? formatDistance(distanceValue) : t('schoolCard.distanceUnknown')
  const distanceStyle = distanceValue == null
    ? { backgroundColor: '#f3f4f6', color: '#6b7280' }
    : distanceValue < 2
    ? { backgroundColor: 'rgba(16,185,129,0.1)', color: '#059669' }
    : distanceValue <= 5
    ? { backgroundColor: 'rgba(245,158,11,0.1)', color: '#d97706' }
    : { backgroundColor: 'rgba(239,68,68,0.1)', color: '#dc2626' }
  const statusStyle = {
    backgroundColor: statusInfo.color,
    boxShadow: `0 0 8px ${hexToRgba(statusInfo.color, 0.4)}`,
  }
  const statusBadgeStyle = {
    backgroundColor: hexToRgba(statusInfo.color, 0.1),
    borderColor: hexToRgba(statusInfo.color, 0.35),
    color: statusInfo.color,
  }
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

  const handleDetailsClick = (e) => {
    e.stopPropagation()
    navigate(`/schools/${school.id}`)
  }

  const handleToggleLocations = (event) => {
    event.stopPropagation()
    onToggleLocations?.()
  }

  const handleShowAllLocations = (event) => {
    event.stopPropagation()
    onShowAllLocations?.()
  }


  const handleFocusLocation = (event, locationId) => {
    event.stopPropagation()
    onFocusLocation?.(locationId)
  }

  const handleClearLocations = (event) => {
    event.stopPropagation()
    onClearLocations?.()
  }

  return (
    <article
      ref={ref}
      onClick={onClick}
      onMouseEnter={onHover}
      onMouseLeave={onHoverEnd}
      className={
        `school-card p-5 max-md:p-4 mx-3 my-3 rounded-[12px] cursor-pointer bg-white border transition-all duration-200 shadow-sm hover:-translate-y-0.5 hover:border-teal-500 hover:shadow-[0_4px_12px_rgba(20,184,166,0.15)] ` +
        (isSelected
          ? 'border-primary-200 border-l-4 border-l-primary-600 bg-primary-50/60 shadow-card-hover'
          : 'border-neutral-200')
      }
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex items-start gap-2">
          <span
            className={`h-2.5 w-2.5 rounded-full ${statusInfo.key === 'accepting' ? 'animate-pulse' : ''}`}
            style={statusStyle}
            aria-label={statusInfo.label}
            role="img"
          />
          <h3 className="text-[18px] font-semibold text-neutral-900 leading-snug max-md:text-[16px]" data-testid="school-name">
            {schoolName}
          </h3>
        </div>
        {distanceLabel && (
          <span
            className="hidden sm:inline-flex flex-shrink-0 items-center gap-1 text-[13px] font-medium px-2.5 py-1 rounded-full"
            style={distanceStyle}
            aria-label={t('schoolCard.aria.distance', { distance: distanceLabel })}
            title={distanceValue == null ? t('schoolCard.distancePrompt') : ''}
          >
            📍 {distanceLabel}
          </span>
        )}
      </div>

      {distanceLabel && (
        <div className="sm:hidden mt-1 text-xs text-neutral-600 flex flex-wrap items-center gap-2">
          <span
            aria-label={t('schoolCard.aria.distance', { distance: distanceLabel })}
            title={distanceValue == null ? t('schoolCard.distancePrompt') : ''}
          >
            📍 {distanceLabel}
          </span>
          <span className="text-neutral-300">•</span>
          <span className={`inline-flex items-center px-2.5 py-1 rounded text-[11px] font-semibold uppercase tracking-[0.08em] ${colors}`}>
            {t(`schoolTypes.${displayType}`)}
          </span>
          <span>{t(`educationLevels.${school.education_level}`)}</span>
          {pricingYear && (
            <>
              <span className="text-neutral-300">•</span>
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium text-teal-700 bg-teal-50 border border-teal-200">
                {pricingYear}
              </span>
            </>
          )}
        </div>
      )}

      <div className="mt-2 hidden sm:flex flex-wrap items-center gap-2">
        <span className={`inline-flex items-center px-2.5 py-1 rounded text-[11px] font-semibold uppercase tracking-[0.08em] ${colors}`}>
          {t(`schoolTypes.${displayType}`)}
        </span>
        <span className="text-xs text-neutral-600">
          {t(`educationLevels.${school.education_level}`)}
        </span>
        {pricingYear && (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium text-teal-700 bg-teal-50 border border-teal-200">
            {pricingYear}
          </span>
        )}
      </div>

      <div className="mt-3 border-t border-neutral-200" />

      <div className="mt-4 grid grid-cols-1 md:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] gap-4 md:gap-4 lg:gap-6">
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
                <span className={`font-medium ${getPerformanceStyle(nvoSummary.math.value).text}`}>
                  {formatPercent(nvoSummary.math.value, 0)}%
                  {nvoSummary.math.trend && (
                    <span className={`ml-1 ${nvoSummary.math.trend.className}`}>{nvoSummary.math.trend.arrow}</span>
                  )}
                </span>
              </div>
              <div className="flex items-center justify-between max-md:hidden">
                <span>{t('schoolCard.nvo.subjectBulgarian')}</span>
                <span className={`font-medium ${getPerformanceStyle(nvoSummary.bulgarian.value).text}`}>
                  {formatPercent(nvoSummary.bulgarian.value, 0)}%
                  {nvoSummary.bulgarian.trend && (
                    <span className={`ml-1 ${nvoSummary.bulgarian.trend.className}`}>{nvoSummary.bulgarian.trend.arrow}</span>
                  )}
                </span>
              </div>
              <div className="text-[13px] text-neutral-700 md:hidden">
                <span className={`font-medium ${getPerformanceStyle(nvoSummary.math.value).text}`}>
                  {t('schoolCard.nvo.subjectMath')}: {formatPercent(nvoSummary.math.value, 0)}%
                  {nvoSummary.math.trend && (
                    <span className={`ml-1 ${nvoSummary.math.trend.className}`}>{nvoSummary.math.trend.arrow}</span>
                  )}
                </span>
                <span className="text-neutral-300 px-2">•</span>
                <span className={`font-medium ${getPerformanceStyle(nvoSummary.bulgarian.value).text}`}>
                  {t('schoolCard.nvo.subjectBulgarian')}: {formatPercent(nvoSummary.bulgarian.value, 0)}%
                  {nvoSummary.bulgarian.trend && (
                    <span className={`ml-1 ${nvoSummary.bulgarian.trend.className}`}>{nvoSummary.bulgarian.trend.arrow}</span>
                  )}
                </span>
              </div>
            </div>
          )}

          <div className="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-[13px] font-medium" style={statusBadgeStyle}>
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: statusInfo.color }} aria-hidden="true" />
            <span>{statusInfo.label}</span>
          </div>
        </div>
      </div>

      {(hasExpandedContent || (school.locations?.length || 0) > 1) && (
        <div className="mt-4 flex items-center justify-between text-sm text-neutral-500">
          {school.locations?.length > 1 ? (
            <button
              type="button"
              className="text-sm text-neutral-600 hover:text-teal-600 hover:underline transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal-500 rounded-sm"
              onClick={handleToggleLocations}
            >
              + {school.locations.length - 1} {t('schools.moreLocations', { count: school.locations.length - 1 })}
            </button>
          ) : (
            <span />
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

      {isLocationsOpen && orderedLocations.length > 1 && (
        <div className="mt-3 rounded-xl border border-neutral-200 bg-white px-4 py-3 space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <p className="text-sm font-semibold text-neutral-900">{t('schools.locations')}</p>
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
          </div>

          <div className="space-y-2">
            {orderedLocations.map((locationItem, idx) => {
              const locationKey = locationItem.id ?? `${school.id}-${idx}`
              const isFocused = focusedLocationId === locationItem.id
              const addressLabel = getAddress(locationItem, i18n.language)
              return (
                <button
                  key={locationKey}
                  type="button"
                  onClick={(event) => handleFocusLocation(event, locationItem.id)}
                  className={`w-full text-left rounded-lg border px-3 py-2 transition-colors ${
                    isFocused
                      ? 'border-primary-300 bg-primary-50'
                      : 'border-neutral-200 bg-white hover:bg-neutral-50'
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      {locationItem.age_group && (
                        <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-neutral-100 text-[11px] font-semibold text-neutral-600">
                          {t(`ageGroups.${locationItem.age_group}`)}
                        </span>
                      )}
                      {locationItem.shift && (
                        <span className="text-xs text-neutral-500">
                          {t(`shifts.${locationItem.shift}`)}
                        </span>
                      )}
                      {locationItem.has_organised_groups && (
                        <span className="text-xs text-neutral-500">
                          {t('schools.organisedGroups')}
                        </span>
                      )}
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
                  {addressLabel && (
                    <p className="mt-1 text-sm text-neutral-700">{addressLabel}</p>
                  )}
                </button>
              )
            })}
          </div>

          {overlayActive && (
            <div className="flex items-center justify-between text-xs text-neutral-400">
              <span>{t('schools.locationFlowHint')}</span>
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
                      { label: t('schoolCard.nvo.subjectMath'), latest: nvoDetail.latestMath, avg: nvoDetail.mathAvg },
                      { label: t('schoolCard.nvo.subjectBulgarian'), latest: nvoDetail.latestBg, avg: nvoDetail.bgAvg },
                    ].map(item => {
                      const latestValue = item.latest ?? item.avg
                      const trend = item.latest != null ? getTrendInfo(item.latest, item.avg) : null
                      const diff = trend ? Math.abs(trend.diff) : 0
                      return (
                        <div key={item.label} className="grid grid-cols-[1.4fr_1fr_1fr_0.8fr] gap-2 py-2 border-b border-neutral-100 text-sm">
                          <span>{item.label}</span>
                          <span className={`text-center font-mono ${getPerformanceStyle(latestValue).text}`}>
                            {formatPercent(latestValue, 1)}%
                          </span>
                          <span className="text-center font-mono text-neutral-700">
                            {formatPercent(item.avg, 1)}%
                          </span>
                          <span className={`text-center font-mono ${trend ? trend.className : 'text-neutral-400'}`}>
                            {trend ? `${trend.arrow} ${formatPercent(diff, 1)}%` : '—'}
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
                      <span className={`font-mono ${getPerformanceStyle(nvoDetail.latestMath ?? 0).text}`}>
                        {formatPercent(nvoDetail.latestMath, 1)}%
                      </span>
                    </div>
                    <div className="flex items-center justify-between py-2">
                      <span>{t('schoolCard.nvo.subjectBulgarian')}</span>
                      <span className={`font-mono ${getPerformanceStyle(nvoDetail.latestBg ?? 0).text}`}>
                        {formatPercent(nvoDetail.latestBg, 1)}%
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

      <div className="flex items-center gap-3 pt-4 mt-4 border-t border-neutral-100">
        <button
          onClick={handleCompareClick}
          disabled={!inCompare && !canAddMore}
          className={
            `flex-1 px-3 py-2 text-sm font-medium rounded-lg transition-colors transition-transform hover:scale-[1.02] ` +
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
        <button
          onClick={handleDetailsClick}
          className="flex-1 px-3 py-2 text-sm font-medium text-white bg-primary-600 hover:bg-primary-700 rounded-lg transition-colors transition-transform hover:scale-[1.02]"
        >
          {t('schools.details')}
        </button>
      </div>

    </article>
  )
})

export default SchoolCard
