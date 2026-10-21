import { languageLabel } from '../../utils/languages.js'
import { getNvoDetail as getSharedNvoDetail } from '../../utils/nvo.js'
import { getCanonicalAmenityFlags, getOptionLabel, normalizeLanguageFocus } from '../../utils/schoolAttributes.js'
import { formatPriceLabel } from '../../utils/pricing.js'
import { planNameLabel } from '../../utils/priceLabels.js'
import { priceCategory, pricePeriod } from '../../utils/priceRows.js'
import { parsePhones } from '../../utils/phone.js'
import { getAgeGroupShifts } from '../../utils/schoolLocation.js'


export const typeColors = {
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

export const AMENITY_PRIORITY = [
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

export function getPrimaryFeature(attributes, t) {
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

export function hexToRgba(hex, alpha) {
  const normalized = hex.replace('#', '')
  const bigint = parseInt(normalized, 16)
  const r = (bigint >> 16) & 255
  const g = (bigint >> 8) & 255
  const b = bigint & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

export function getNvoDetail(school, t, ageGroup = null, examType = null) {
  return getSharedNvoDetail(school, t, { requireCompleteSubjects: true, ageGroup, examType })
}

export function getAmenityFlags(attributes, hasAfterSchool) {
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

export function getScheduleLine(primaryShiftInfo, attributes, t) {
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

export function getLocationAgeGroups(location) {
  if (!location) return []
  if (Array.isArray(location.age_groups)) return location.age_groups.filter(Boolean)
  if (location.age_group) return [location.age_group]
  return []
}

export function buildExpandedSections({
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

  // Only a language of instruction is the primary language; a language focus is offered.
  const primaryLanguage = languagesOfInstruction[0]
  const extraLanguages = [...new Set([
    ...languagesOfInstruction,
    ...languageFocus.map(item => item.language),
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
    const others = extraLanguages
      .filter(language => language !== primaryLanguage)
      .map(language => getLanguageLabel(language, t))
    if (others.length > 0) {
      const label = primaryLanguage ? t('schoolCard.labels.alsoOffered') : t('schoolCard.sections.languages')
      lines.push(`${label}: ${others.join(', ')}`)
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
      const category = t(`pricing.${priceCategory(item)}`)
      const period = pricePeriod(item)
      const periodKey = period ? t(`pricing.${period}`) : t('pricing.periodNotStated')
      const planName = planNameLabel(item, t, locale)
      const plan = planName ? `${t('pricing.plan')}: ${planName}` : ''
      const year = item.academic_year ? `${t('pricing.academicYear')}: ${item.academic_year}` : ''
      const meta = [plan, year].filter(Boolean).join(' • ')
      const source = item.source ? t(`priceSource.${item.source}`) : ''
      // BGN rows are converted to euro, as on the detail and compare pages.
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
  // Normalised as on the school page: the register glues an area code to mobiles ("02/0898…").
  const phones = parsePhones(primaryLocation?.phone).map(phone => phone.display)
  if (phones.length > 0) {
    contactParts.push(`${t('schools.phone')}: ${phones.join(', ')}`)
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
