import { Fragment, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  getLatestNvoFact,
  getOfferedAgeGroups,
  getShifts,
  readSavedLocation,
} from '../../utils/keyFacts'
import { calculateDistance } from '../../utils/distance'
import { formatAmount, formatKm, intlLocale } from '../../utils/format'
import { statedTuition, YEAR_STATUS } from '../../utils/pricing'
import { getLastAdmittedPoints, getMinNvoScore, hasKindergartenCutoffs } from '../../utils/admission'
import {
  getLanguageLabel,
  getExamTypeLabel,
} from './helpers'
import { normalizeLanguageFocus } from '../../utils/schoolAttributes'
import { getMappableLocations } from './LocationMap'
import { orderAgeGroups } from '../../utils/ageGroupOrder'
import { useCountry } from '../../context/CountryContext'
import { usesSofiaKindergartenSystem } from '../../utils/admission'
import { openDayDate, openDaysLink, upcomingOpenDays } from '../../utils/schoolFacts'
import { useSavedVerdicts } from '../../hooks/useSavedAdmission'
import { SavedVerdictLine, SavedVerdictWord } from '../Admission/SavedVerdict'

function Fact({ label, value, detail, note, detailClass = 'text-neutral-500' }) {
  return (
    <div className="min-w-0 rounded-xl border border-neutral-200 bg-white px-4 py-3">
      <div className="text-balance text-xs font-medium uppercase tracking-wide text-neutral-500">{label}</div>
      <div className="mt-1 text-base sm:text-lg font-bold leading-snug text-neutral-900 break-words">{value}</div>
      {detail && <div className={`mt-0.5 text-xs ${detailClass}`}>{detail}</div>}
      {note && <div className="mt-1 text-xs text-neutral-600">{note}</div>}
    </div>
  )
}

// A list value wraps between its items, not inside one ("8-12" on one line, "клас" on the next).
const listValue = (items) => items.map((item, index) => (
  <Fragment key={item}>
    <span className="inline-block">{item}{index < items.length - 1 && ','}</span>{' '}
  </Fragment>
))

/**
 * The strip of the few facts a parent decides on, right under the hero. Order depends on
 * the school type (price first for private schools, NVO / admission for state ones), and
 * facts a school does not have are left out rather than shown as "unavailable".
 */
function KeyFacts({ school, examAverages, nvoExamType, ageGroup = null }) {
  const { t, i18n } = useTranslation()
  const { config: countryConfig } = useCountry()
  const locale = intlLocale(i18n.language)
  const savedLocation = useMemo(readSavedLocation, [])

  const formatNumber = (value, digits = 0) => formatAmount(value, locale, digits)

  const attributes = school.attributes || {}
  const locations = school.locations || []
  const isPrivate = school.school_type === 'private' || school.school_type === 'international'
  const isKindergarten = school.education_level === 'kindergarten'

  const facts = {}

  // Saved calculator answers against this school's last cut-off lead the strip: on a
  // phone the full comparison is several screens down.
  // No age group, as in the full comparison further down, so the two always agree.
  const comparable = useSavedVerdicts(isPrivate ? null : school)
    .find(verdict => !['needsAddress', 'noCutoff'].includes(verdict.verdict))
  if (comparable) {
    facts.saved = (
      <Fact
        key="saved"
        // Kindergarten answers on a school page are for its preparatory group.
        label={t('savedAdmission.factLabel', {
          stage: t(`savedAdmission.stageName.${comparable.stage === 'grade_1' ? 'grade_1' : isKindergarten ? 'kindergarten' : 'preparatory'}`),
        })}
        value={<SavedVerdictWord verdict={comparable} />}
        detail={<SavedVerdictLine verdict={comparable} hideVerdict />}
        detailClass="text-neutral-700"
        note={t(`savedAdmission.assumed.${comparable.stage}`)}
      />
    )
  }

  // The searched age group's fee when the parent came from a search for one.
  const tuition = isPrivate ? statedTuition(school.pricing, { ageGroup }) : null
  if (tuition) {
    const yearDetail = tuition.yearStatus === YEAR_STATUS.NOT_STATED
      ? t('pricing.yearNotStated')
      : tuition.yearStatus === YEAR_STATUS.DATED_OTHER
        ? `${tuition.academicYear} · ${t('pricing.notCurrentYear')}`
        : tuition.yearStatus === YEAR_STATUS.NEXT
          ? `${tuition.academicYear} · ${t('pricing.nextYear')}`
          : tuition.academicYear
    // The fee in the period the school states it in: never annualised, since schools
    // bill over 9, 10 or 12 months.
    const priceText = (fee) => {
      const price = formatNumber(fee.min) === formatNumber(fee.max)
        ? formatNumber(fee.min)
        : `${formatNumber(fee.min)}–${formatNumber(fee.max)}`
      return fee.period
        ? t('pricing.pricePerPeriod', { price, currency: fee.currency, period: t(`pricing.per.${fee.period}`) })
        : t('pricing.priceOnly', { price, currency: fee.currency })
    }
    // Next year's fee leads (it is what a family enrolling now pays); this year's follows.
    const currentYear = tuition.currentYear
      ? [
          t('pricing.yearPrice', { year: tuition.currentYear.academicYear, price: priceText(tuition.currentYear) }),
          tuition.currentYear.period ? null : t('pricing.periodNotStated'),
        ].filter(Boolean).join(' · ')
      : null
    facts.tuition = (
      <Fact
        key="tuition"
        label={t(tuition.total ? 'pricing.totalFees' : 'pricing.tuition')}
        value={priceText(tuition)}
        detail={[tuition.period ? null : t('pricing.periodNotStated'), yearDetail].filter(Boolean).join(' · ')}
        note={currentYear}
      />
    )
  }

  // Languages taught in come first; a language focus is not a language of instruction,
  // so the fact carries the general "Languages" label.
  const languages = [
    ...(attributes.languages_of_instruction || []),
    ...normalizeLanguageFocus(attributes.language_focus).map(item => item.language),
  ]
  const languageLabels = [...new Set(languages.filter(Boolean).map(lang => getLanguageLabel(lang, t)))]
  if (languageLabels.length > 0) {
    facts.languages = (
      <Fact key="languages" label={t('schools.languages')} value={listValue(languageLabels)} />
    )
  }

  // Youngest first, as in the locations list below.
  const ageGroups = orderAgeGroups(getOfferedAgeGroups(locations), countryConfig)
  if (ageGroups.length > 0) {
    facts.grades = (
      <Fact
        key="grades"
        label={t('schoolDetail.groupsAndGrades')}
        value={listValue(ageGroups.map(group => t(`ageGroups.${group}`)))}
      />
    )
  }

  const nvo = getLatestNvoFact(school.exam_results, nvoExamType, examAverages)
  if (nvo) {
    const toneLabel = nvo.tone ? t(`academicPerformance.${nvo.tone}Benchmark`) : null
    facts.nvo = (
      <Fact
        key="nvo"
        label={nvo.subjects.length === 1
          ? t('schoolDetail.nvoLatestSubject', {
            exam: getExamTypeLabel(nvo.examType, t),
            year: nvo.year,
            subject: t(nvo.subjects[0] === 'math' ? 'schoolCard.nvo.subjectMath' : 'schoolCard.nvo.subjectBulgarian'),
          })
          : t('schoolDetail.nvoLatest', { exam: getExamTypeLabel(nvo.examType, t), year: nvo.year })}
        value={t('academicPerformance.pointsValue', { value: formatNumber(nvo.value, 1) })}
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

  // The saved result already shows the last admitted child's points.
  const lastAdmitted = !isPrivate && isKindergarten && !comparable ? getLastAdmittedPoints(school.admission_info) : null
  if (lastAdmitted?.points != null) {
    facts.lastAdmitted = (
      <Fact
        key="lastAdmitted"
        label={t('schoolDetail.lastAdmitted')}
        value={t('schoolDetail.pointsValue', { points: formatNumber(lastAdmitted.points, 2) })}
        detail={lastAdmitted.year ? t('schoolDetail.yearValue', { year: lastAdmitted.year }) : null}
      />
    )
  } else if (!comparable && usesSofiaKindergartenSystem(school)) {
    // No published thresholds yet: say how admission works instead of leaving a gap.
    facts.lastAdmitted = (
      <Fact
        key="lastAdmitted"
        label={t('schoolDetail.admission')}
        value={t('schoolDetail.kgByPointsShort')}
        detail={t(hasKindergartenCutoffs(school.admission_info) ? 'schoolDetail.kgThresholdsBelow' : 'schoolDetail.kgThresholdsLater')}
      />
    )
  }

  // The next open day leads the strip's second place: it is the one fact with a date a
  // parent can miss. The rest are listed with the website facts further down.
  const openDays = upcomingOpenDays(school)
  if (openDays.length > 0) {
    const link = openDaysLink(openDays)
    facts.openDay = (
      <Fact
        key="openDay"
        label={t('schoolDetail.nextOpenDay')}
        value={openDayDate(openDays[0].date, i18n.language)}
        detail={link && (
          <a href={link.href} target="_blank" rel="noopener noreferrer nofollow" className="text-primary-700 underline underline-offset-2">
            {t(link.register ? 'schoolFacts.glance.register' : 'schoolFacts.source')}
          </a>
        )}
        note={openDays.length > 1 ? t('schoolDetail.moreOpenDays', { count: openDays.length - 1 }) : null}
      />
    )
  }

  const shifts = getShifts(locations)
  if (shifts.length > 0) {
    facts.shift = (
      <Fact key="shift" label={t('schools.shift')} value={listValue(shifts.map(shift => t(`shifts.${shift}`)))} />
    )
  }

  if (savedLocation) {
    const nearest = getMappableLocations(locations)
      .map(point => calculateDistance(savedLocation.lat, savedLocation.lng, point.lat, point.lng))
      .filter(km => km != null)
      .sort((a, b) => a - b)[0]
    if (nearest != null) {
      facts.distance = (
        <Fact
          key="distance"
          label={t('schoolDetail.distance')}
          value={t('schoolDetail.distanceValue', { km: formatKm(nearest, i18n.language) })}
        />
      )
    }
  }

  const order = isPrivate
    ? ['tuition', 'openDay', 'languages', 'grades', 'nvo', 'distance']
    : isKindergarten
      ? ['saved', 'lastAdmitted', 'openDay', 'distance', 'grades', 'languages']
      : ['saved', 'nvo', 'openDay', 'minScore', 'shift', 'distance', 'grades', 'languages']
  const shown = order.map(key => facts[key]).filter(Boolean)

  if (shown.length === 0) return null

  return (
    <section aria-label={t('schoolDetail.keyFacts')} className="mb-6">
      {/* As many columns as fit the strip's own width, not the viewport's: in the search
          page's side panel it is half as wide as on the school page. Text is larger from
          `sm` up, so the cards are wider there. */}
      <div className="grid gap-3 grid-cols-[repeat(auto-fill,minmax(9.6rem,1fr))] sm:grid-cols-[repeat(auto-fill,minmax(13.5rem,1fr))]">
        {shown}
      </div>
    </section>
  )
}

export default KeyFacts
