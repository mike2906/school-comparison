import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getHeadlineTuition,
  getLatestNvoFact,
  getOfferedAgeGroups,
  getShifts,
  parseSavedLocation,
} from '../../utils/keyFacts'
import { calculateDistance } from '../../utils/distance'
import { YEAR_STATUS } from '../../utils/pricing'
import {
  getLastAdmittedPoints,
  getMinNvoScore,
  getLanguageLabel,
  normalizeLanguageFocus,
  getExamTypeLabel,
} from './helpers'
import { getMappableLocations } from './LocationMap'

function readSavedLocation() {
  try {
    return parseSavedLocation(window.localStorage.getItem('userLocation'))
  } catch {
    return null
  }
}

function Fact({ label, value, detail, detailClass = 'text-neutral-500' }) {
  return (
    <div className="min-w-0 rounded-xl border border-neutral-200 bg-white px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</div>
      <div className="mt-1 text-base sm:text-lg font-bold leading-snug text-neutral-900 break-words">{value}</div>
      {detail && <div className={`mt-0.5 text-xs ${detailClass}`}>{detail}</div>}
    </div>
  )
}

/**
 * The strip of the few facts a parent decides on, right under the hero. Order depends on
 * the school type (price first for private schools, NVO / admission for state ones), and
 * facts a school does not have are left out rather than shown as "unavailable".
 */
function KeyFacts({ school, examAverages, nvoExamType }) {
  const { t, i18n } = useTranslation()
  const locale = i18n.language?.startsWith('bg') ? 'bg-BG' : 'en-GB'
  const savedLocation = useMemo(readSavedLocation, [])

  const formatNumber = (value, digits = 0) =>
    new Intl.NumberFormat(locale, { maximumFractionDigits: digits }).format(value)

  const attributes = school.attributes || {}
  const locations = school.locations || []
  const isPrivate = school.school_type === 'private' || school.school_type === 'international'
  const isKindergarten = school.education_level === 'kindergarten'

  const facts = {}

  const tuition = isPrivate ? getHeadlineTuition(school.pricing) : null
  if (tuition) {
    const yearDetail = tuition.yearStatus === YEAR_STATUS.NOT_STATED
      ? t('pricing.yearNotStated')
      : tuition.academicYear
    if (tuition.kind === 'yearly') {
      const formatRange = (min, max) => (
        formatNumber(min) === formatNumber(max) ? formatNumber(min) : `${formatNumber(min)}–${formatNumber(max)}`
      )
      facts.tuition = (
        <Fact
          key="tuition"
          label={t('pricing.tuition')}
          value={t('schoolDetail.tuitionPerYear', {
            price: formatRange(tuition.min, tuition.max),
            currency: tuition.currency,
          })}
          // No "per month" figure: yearly ÷ 12 misleads when schools bill over 9–10 months.
          detail={yearDetail}
        />
      )
    } else {
      facts.tuition = (
        <Fact
          key="tuition"
          label={t('pricing.tuition')}
          value={t('schoolDetail.tuitionUnstated', { price: formatNumber(tuition.amount), currency: tuition.currency })}
          detail={[t('pricing.periodNotStated'), yearDetail].filter(Boolean).join(' · ')}
        />
      )
    }
  }

  const languages = [
    ...normalizeLanguageFocus(attributes.language_focus).map(item => item.language),
    ...(attributes.languages_of_instruction || []),
  ]
  const languageLabels = [...new Set(languages.filter(Boolean).map(lang => getLanguageLabel(lang, t)))]
  if (languageLabels.length > 0) {
    facts.languages = (
      <Fact key="languages" label={t('schools.languagesOfInstruction')} value={languageLabels.join(', ')} />
    )
  }

  const ageGroups = getOfferedAgeGroups(locations)
  if (ageGroups.length > 0) {
    facts.grades = (
      <Fact
        key="grades"
        label={t('schoolDetail.groupsAndGrades')}
        value={ageGroups.map(group => t(`ageGroups.${group}`)).join(', ')}
      />
    )
  }

  const nvo = getLatestNvoFact(school.exam_results, nvoExamType, examAverages)
  if (nvo) {
    const toneLabel = nvo.tone ? t(`academicPerformance.${nvo.tone}Benchmark`) : null
    facts.nvo = (
      <Fact
        key="nvo"
        label={t('schoolDetail.nvoLatest', { exam: getExamTypeLabel(nvo.examType, t), year: nvo.year })}
        value={`${formatNumber(nvo.value, 1)}%`}
        detail={nvo.national != null
          ? `${toneLabel} · ${t('schoolDetail.nvoVsNational', { value: formatNumber(nvo.national, 1) })}`
          : null}
        detailClass={nvo.textClass || 'text-neutral-500'}
      />
    )
  }

  const minScore = !isPrivate ? getMinNvoScore(school.admission_info) : null
  if (minScore?.score != null) {
    facts.minScore = (
      <Fact
        key="minScore"
        label={t('schoolDetail.minScore')}
        value={formatNumber(minScore.score, 2)}
        detail={minScore.year ? t('schoolDetail.yearValue', { year: minScore.year }) : null}
      />
    )
  }

  const lastAdmitted = !isPrivate && isKindergarten ? getLastAdmittedPoints(school.admission_info) : null
  if (lastAdmitted?.points != null) {
    facts.lastAdmitted = (
      <Fact
        key="lastAdmitted"
        label={t('schoolDetail.lastAdmitted')}
        value={t('schoolDetail.pointsValue', { points: formatNumber(lastAdmitted.points, 2) })}
        detail={lastAdmitted.year ? t('schoolDetail.yearValue', { year: lastAdmitted.year }) : null}
      />
    )
  } else if (!isPrivate && isKindergarten) {
    // No published thresholds yet: say how admission works instead of leaving a gap.
    facts.lastAdmitted = (
      <Fact
        key="lastAdmitted"
        label={t('schoolDetail.admission')}
        value={t('schoolDetail.kgByPointsShort')}
        detail={t('schoolDetail.kgThresholdsLater')}
      />
    )
  }

  const shifts = getShifts(locations)
  if (shifts.length > 0) {
    facts.shift = (
      <Fact key="shift" label={t('schools.shift')} value={shifts.map(shift => t(`shifts.${shift}`)).join(', ')} />
    )
  }

  if (savedLocation) {
    const distances = getMappableLocations(locations)
      .map(point => calculateDistance(savedLocation.lat, savedLocation.lng, point.lat, point.lng))
      .filter(value => value != null)
    if (distances.length > 0) {
      facts.distance = (
        <Fact
          key="distance"
          label={t('schoolDetail.distance')}
          value={t('schoolDetail.distanceValue', { km: formatNumber(Math.min(...distances), 1) })}
        />
      )
    }
  }

  const order = isPrivate
    ? ['tuition', 'languages', 'grades', 'nvo', 'distance']
    : isKindergarten
      ? ['lastAdmitted', 'distance', 'grades', 'languages']
      : ['nvo', 'minScore', 'shift', 'distance', 'grades', 'languages']
  const shown = order.map(key => facts[key]).filter(Boolean)

  if (shown.length === 0) return null

  return (
    <section aria-label={t('schoolDetail.keyFacts')} className="mb-6">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {shown}
      </div>
    </section>
  )
}

export default KeyFacts
