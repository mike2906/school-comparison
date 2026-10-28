import { schoolLevelLabel, schoolTypeLabel } from '../../utils/levelLabel'
import { orderAgeGroups } from '../../utils/ageGroupOrder'
import TagList from '../SchoolDetailPage/TagList'
import PhoneLinks from '../SchoolDetailPage/PhoneLinks'
import { calculateDistance } from '../../utils/distance'
import { formatDistance } from '../../utils/format'
import { getAddress, getSummary } from '../../utils/i18n'
import { hasAnySchoolSummary } from './summaryVisibility'
import { atAGlanceKeysIn, atAGlanceLines, factTopicsIn, getSchoolFacts } from '../../utils/schoolFacts'
import {
  getAfterSchoolEvidence,
  getCanonicalAmenityEvidence,
  getLocationDisplayEvidence,
  getOptionLabel,
  getStatusInfo,
  normalizeLanguageFocus,
} from '../../utils/schoolAttributes'
import {
  curatedRequirement,
  getAdmissionRequirement,
  getLastAdmittedPoints,
  getMinNvoScore,
  getMinNvoScores,
  getPointsHistory,
} from '../../utils/admission'
import {
  formatPercent,
  getBenchmarkTooltip,
  getCombinedBenchmark,
  getNvoValueStyle,
  getTrendInfo,
  getTrendTooltip,
} from '../../utils/nvoDisplay'
import {
  feeRangeNotes,
  formatCurrency,
  formatPriceLabel,
  groupPricingByAcademicYear,
  priceAgeGroupLabel,
  sortByCategory,
  YEAR_STATUS,
} from '../../utils/pricing'
import { planNameBeside, textLang } from '../../utils/priceLabels'
import { priceCategory } from '../../utils/priceRows'
import { safeWebsiteUrl, sourceSiteName } from '../../utils/priceSources'
import {
  formatDate,
  getLanguageLabel,
  getPrimaryAgeGroup,
  getPrimaryShiftInfo,
  nvoNotApplicable,
  periodSuffix,
  STATUS_BADGE_CLASSES,
  stateAdmissionHint,
} from './compareHelpers.js'
import { InlineSource } from './CompareRows'
import { outdoorEntries } from '../../utils/outdoorSpace'


// The comparison table's rows, grouped into sections. Each row renders a cell per
// school (getValue) and a plain value used to spot differences (getCompare).
export function buildCompareSections({
  t,
  language,
  metricsById,
  userLocation,
  schools,
  selectedAgeGroup,
  examAverages,
  nvoGrade,
  countryConfig,
}) {
  // Same tag handling as the school page: no Title Case, Bulgarian tags grouped in EN.
  const makeTags = (items = []) => {
    if (!items.length) return null
    // TagList handles text only; numbers (e.g. spots) are shown as text, not dropped.
    const tags = items.filter(item => item != null && item !== '').map(item => String(item))
    return <TagList tags={tags} chipClassName="rounded bg-neutral-100 px-2 py-0.5 text-xs text-neutral-700" />
  }

  const renderPlaceholder = (labelKey = 'compare.notAvailable') => (
    <span className="text-neutral-400 text-sm">{t(labelKey)}</span>
  )

    const amenityEvidenceForSchool = (school) => getCanonicalAmenityEvidence(
      school.attributes,
      getAfterSchoolEvidence(school.locations)
    )
    const renderBinaryEvidence = (value) => value == null ? renderPlaceholder() : (
      <span className={`text-sm ${value ? 'text-emerald-600 font-medium' : 'text-neutral-500'}`}>
        {value ? t('common.yes') : t('common.no')}
      </span>
    )
    const languageForSchool = (school) => {
      const attributes = school.attributes || {}
      const focus = normalizeLanguageFocus(attributes.language_focus)
      const languages = new Set()
      ;(attributes.languages_of_instruction || []).forEach(lang => {
        if (lang) languages.add(getLanguageLabel(lang, t))
      })
      focus.forEach(item => {
        if (item?.language) languages.add(getLanguageLabel(item.language, t))
      })
      return [...languages]
    }

    const overviewRows = [
      {
        label: t('compare.labels.type'),
        getValue: (school) => (
          <span className="text-sm text-neutral-700">{schoolTypeLabel(school, t)}</span>
        ),
        getCompare: (school) => school.school_type,
      },
      ...(hasAnySchoolSummary(schools, language)
        ? [{
            label: t('compare.summary'),
            getValue: (school) => {
              const summary = getSummary(school, language, 'short')
              return summary
                ? <span className="text-sm text-neutral-700">{summary}</span>
                : renderPlaceholder()
            },
            getCompare: (school) => getSummary(school, language, 'short') || null,
          }]
        : []),
      {
        label: t('compare.labels.educationLevel'),
        getValue: (school) => (
          <span className="text-sm text-neutral-700">{schoolLevelLabel(school, t, countryConfig)}</span>
        ),
        getCompare: (school) => schoolLevelLabel(school, t, countryConfig),
      },
      {
        label: t('compare.labels.tuitionOrAdmission'),
        getValue: (school) => {
          const pricingRange = metricsById.get(school.id)?.pricingRange
          const primaryAgeGroup = getPrimaryAgeGroup(school)
          const lastAdmitted = getLastAdmittedPoints(school.admission_info, primaryAgeGroup)
          const minScore = getMinNvoScore(school.admission_info)

          if (school.school_type !== 'state' && pricingRange) {
            const formatted = (fee) => (fee.min === fee.max
              ? formatCurrency(fee.min, language, fee.currency)
              : `${formatCurrency(fee.min, language, fee.currency)}–${formatCurrency(fee.max, language, fee.currency)}`
            ) + periodSuffix({ period: fee.period, category: 'tuition' }, t)
            // As the school states it, never annualised (schools bill over 9–12 months).
            // Next year's fee leads when families now enrol for it; this year's follows.
            const current = pricingRange.currentYear
            return (
              <div>
                <div className="text-sm font-semibold text-neutral-900">
                  {formatted(pricingRange)}
                </div>
                <div className="text-xs text-neutral-500">
                  {[
                    t(pricingRange.total ? 'pricing.totalFees' : 'pricing.tuition'),
                    pricingRange.academicYear,
                    pricingRange.yearStatus === YEAR_STATUS.DATED_OTHER ? t('pricing.notCurrentYear') : null,
                    pricingRange.yearStatus === YEAR_STATUS.NEXT ? t('pricing.nextYear') : null,
                  ].filter(Boolean).join(' · ')}
                </div>
                {feeRangeNotes(pricingRange, t, language, (value, currency) => formatCurrency(value, language, currency)).map((line, index) => (
                  <div key={index} className="text-xs text-neutral-500">{line}</div>
                ))}
                {current && (
                  <div className="text-xs text-neutral-500">
                    {t('pricing.yearPrice', { year: current.academicYear, price: formatted(current) })}
                  </div>
                )}
              </div>
            )
          }

          if (school.school_type === 'state' && lastAdmitted) {
            return (
              <div>
                <div className="text-sm font-semibold text-neutral-900">{lastAdmitted.points}</div>
                <div className="text-xs text-neutral-500">{t('compare.labels.pointsLastYear', { year: lastAdmitted.year })}</div>
              </div>
            )
          }

          if (school.school_type === 'state' && minScore) {
            return (
              <div>
                <div className="text-sm font-semibold text-neutral-900">{minScore.score}</div>
                <div className="text-xs text-neutral-500">{t('compare.labels.minScoreYear', { year: minScore.year })}</div>
              </div>
            )
          }

          if (school.school_type === 'state') {
            // State schools and kindergartens charge no fee; say so, and how admission works.
            const hint = stateAdmissionHint(school, t, selectedAgeGroup)
            return (
              <div>
                <div className="text-sm font-semibold text-neutral-900">{t('compare.free')}</div>
                {hint && <div className="text-xs text-neutral-500">{hint}</div>}
              </div>
            )
          }

          return renderPlaceholder('compare.notAvailable')
        },
        getCompare: (school) => {
          if (school.school_type !== 'state') {
            const metrics = metricsById.get(school.id)
            return metrics?.priceSort ?? metrics?.pricingRange?.min ?? null
          }
          const primaryAgeGroup = getPrimaryAgeGroup(school)
          const lastAdmitted = getLastAdmittedPoints(school.admission_info, primaryAgeGroup)
          const minScore = getMinNvoScore(school.admission_info)
          return lastAdmitted?.points ?? minScore?.score ?? 'free'
        },
      },
      {
        label: t('compare.labels.nvoAverage', { grade: nvoGrade }),
        getValue: (school) => {
          if (nvoNotApplicable(school)) {
            return renderPlaceholder('compare.notApplicable')
          }
          const metrics = metricsById.get(school.id)
          const detail = metrics?.nvoDetail
          const isLatest = metrics?.overallLatest != null
          const value = metrics?.overallLatest ?? metrics?.overallAvg
          if (value == null) return renderPlaceholder()
          const year = isLatest ? detail?.latestCombinedYear : null
          const benchmark = isLatest ? getCombinedBenchmark(detail, year, value, examAverages) : null
          return (
            <div>
              <div className={`text-sm font-semibold ${benchmark?.textClass || 'text-neutral-900'}`}>{t('academicPerformance.pointsValue', { value: formatPercent(value, 1) })}</div>
              <div className="text-xs text-neutral-500">
                {isLatest ? year : t('compare.labels.fiveYearAverage', { start: detail?.minYear, end: detail?.maxYear })}
              </div>
              {benchmark && (
                <div className="text-xs text-neutral-500">
                  {t('academicPerformance.nationalBenchmarkValue', { value: formatPercent(benchmark.nationalValue, 1) })}
                </div>
              )}
            </div>
          )
        },
        getCompare: (school) => metricsById.get(school.id)?.overallLatest ?? metricsById.get(school.id)?.overallAvg ?? null,
      },
      {
        label: t('compare.labels.schedule'),
        getValue: (school) => {
          const shiftInfo = getPrimaryShiftInfo(school)
          const shift = shiftInfo?.shift
          if (!shift) return renderPlaceholder()
          const shiftLabelKey = `schoolCard.shift.${shift}`
          const shiftLabel = t(shiftLabelKey)
          const label = shiftLabel !== shiftLabelKey ? shiftLabel : t(`shifts.${shift}`)
          return (
            <div className="text-sm text-neutral-700">
              {label}
            </div>
          )
        },
        getCompare: (school) => getPrimaryShiftInfo(school)?.shift || null,
      },
      {
        label: t('compare.labels.distance'),
        getValue: (school) => {
          const metrics = metricsById.get(school.id)
          const distance = metrics?.distance
          if (!userLocation) {
            return renderPlaceholder('compare.distanceNotSet')
          }
          return distance != null
            ? (
              <span className="text-sm text-neutral-700">
                {formatDistance(distance, { language: language, t })}
              </span>
            )
            : renderPlaceholder()
        },
        getCompare: (school) => metricsById.get(school.id)?.distance ?? null,
      },
      {
        label: t('compare.labels.languages'),
        getValue: (school) => {
          const items = languageForSchool(school)
          return items.length > 0 ? makeTags(items) : renderPlaceholder()
        },
        getCompare: (school) => languageForSchool(school),
      },
      {
        label: t('compare.labels.enrollmentStatus'),
        getValue: (school) => {
          const status = getStatusInfo(school, t)
          return status ? (
            <span className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${STATUS_BADGE_CLASSES[status.key]}`}>
              {status.label}
            </span>
          ) : renderPlaceholder()
        },
        getCompare: (school) => getStatusInfo(school, t)?.key || null,
      },
    ]

    const academicRows = [
      {
        label: t('compare.labels.nvoMath', { grade: nvoGrade }),
        getValue: (school) => {
          if (nvoNotApplicable(school)) return renderPlaceholder('compare.notApplicable')
          const detail = metricsById.get(school.id)?.nvoDetail
          if (!detail?.latestMath && !detail?.mathAvg) return renderPlaceholder()
          const value = detail.latestMath ?? detail.mathAvg
          const style = getNvoValueStyle({
            value,
            examType: detail.examType,
            year: detail.latestMathYear ?? detail.latestYear,
            subjectKey: 'math',
            examAverages,
          })
          const trend = detail.latestMath != null && detail.mathAvg != null
            ? getTrendInfo(detail.latestMath, detail.mathAvg)
            : null
          return (
            <div>
              <div className="flex items-center gap-2">
                <span
                  className={`text-sm font-semibold ${style.text} ${style.benchmark ? 'cursor-help' : ''}`}
                  title={getBenchmarkTooltip(value, style.benchmark, t) || undefined}
                >
                  {t('academicPerformance.pointsValue', { value: formatPercent(value, 1) })}
                </span>
                {trend ? (
                  <span
                    className={`text-xs ${trend.className} cursor-help`}
                    title={getTrendTooltip(detail.latestMath, detail.mathAvg, trend, t) || undefined}
                  >
                    {trend.arrow}
                  </span>
                ) : null}
              </div>
              {detail.mathAvg != null && detail.latestMath != null && (
                <div className="text-xs text-neutral-500">
                  {t('compare.labels.avgValue', { value: formatPercent(detail.mathAvg, 1), start: detail.minYear, end: detail.maxYear })}
                </div>
              )}
              <InlineSource
                badgeKey="government"
                badgeLabel={t('compare.sourceTypes.government')}
                url={(school.exam_results || []).find(result => result.exam_type === detail.examType && String(result.subject || '').toLowerCase().includes('math'))?.source_url}
                dateLabel={formatDate((school.exam_results || []).find(result => result.exam_type === detail.examType && String(result.subject || '').toLowerCase().includes('math'))?.scraped_at, language)}
                t={t}
              />
            </div>
          )
        },
        getCompare: (school) => metricsById.get(school.id)?.nvoDetail?.latestMath ?? metricsById.get(school.id)?.nvoDetail?.mathAvg ?? null,
      },
      {
        label: t('compare.labels.nvoBulgarian', { grade: nvoGrade }),
        getValue: (school) => {
          if (nvoNotApplicable(school)) return renderPlaceholder('compare.notApplicable')
          const detail = metricsById.get(school.id)?.nvoDetail
          if (!detail?.latestBg && !detail?.bgAvg) return renderPlaceholder()
          const value = detail.latestBg ?? detail.bgAvg
          const style = getNvoValueStyle({
            value,
            examType: detail.examType,
            year: detail.latestBgYear ?? detail.latestYear,
            subjectKey: 'bulgarian',
            examAverages,
          })
          const trend = detail.latestBg != null && detail.bgAvg != null
            ? getTrendInfo(detail.latestBg, detail.bgAvg)
            : null
          return (
            <div>
              <div className="flex items-center gap-2">
                <span
                  className={`text-sm font-semibold ${style.text} ${style.benchmark ? 'cursor-help' : ''}`}
                  title={getBenchmarkTooltip(value, style.benchmark, t) || undefined}
                >
                  {t('academicPerformance.pointsValue', { value: formatPercent(value, 1) })}
                </span>
                {trend ? (
                  <span
                    className={`text-xs ${trend.className} cursor-help`}
                    title={getTrendTooltip(detail.latestBg, detail.bgAvg, trend, t) || undefined}
                  >
                    {trend.arrow}
                  </span>
                ) : null}
              </div>
              {detail.bgAvg != null && detail.latestBg != null && (
                <div className="text-xs text-neutral-500">
                  {t('compare.labels.avgValue', { value: formatPercent(detail.bgAvg, 1), start: detail.minYear, end: detail.maxYear })}
                </div>
              )}
              <InlineSource
                badgeKey="government"
                badgeLabel={t('compare.sourceTypes.government')}
                url={(school.exam_results || []).find(result => result.exam_type === detail.examType && String(result.subject || '').toLowerCase().includes('bulgarian'))?.source_url}
                dateLabel={formatDate((school.exam_results || []).find(result => result.exam_type === detail.examType && String(result.subject || '').toLowerCase().includes('bulgarian'))?.scraped_at, language)}
                t={t}
              />
            </div>
          )
        },
        getCompare: (school) => metricsById.get(school.id)?.nvoDetail?.latestBg ?? metricsById.get(school.id)?.nvoDetail?.bgAvg ?? null,
      },
      {
        label: t('compare.labels.classSize'),
        getValue: (school) => {
          const value = school.attributes?.class_size
          return value ? <span className="text-sm text-neutral-700">{value}</span> : renderPlaceholder()
        },
        getCompare: (school) => school.attributes?.class_size ?? null,
      },
      {
        label: t('compare.labels.teachingApproach'),
        getValue: (school) => {
          const items = (school.attributes?.teaching_approach || []).map(item => getOptionLabel(item, t))
          return items.length > 0 ? makeTags(items) : renderPlaceholder()
        },
        getCompare: (school) => school.attributes?.teaching_approach || [],
      },
      {
        label: t('compare.labels.specialPrograms'),
        getValue: (school) => {
          const items = (school.attributes?.special_programs || []).map(item => getOptionLabel(item, t))
          return items.length > 0 ? makeTags(items) : renderPlaceholder()
        },
        getCompare: (school) => school.attributes?.special_programs || [],
      },
    ]

    const practicalRows = [
      {
        label: t('compare.labels.afterSchoolCare'),
        getValue: (school) => renderBinaryEvidence(amenityEvidenceForSchool(school).extended),
        getCompare: (school) => amenityEvidenceForSchool(school).extended,
      },
      {
        label: t('compare.labels.meals'),
        getValue: (school) => renderBinaryEvidence(amenityEvidenceForSchool(school).meals),
        getCompare: (school) => amenityEvidenceForSchool(school).meals,
      },
      {
        label: t('compare.labels.transport'),
        getValue: (school) => renderBinaryEvidence(amenityEvidenceForSchool(school).transport),
        getCompare: (school) => amenityEvidenceForSchool(school).transport,
      },
      {
        label: t('compare.labels.facilities'),
        getValue: (school) => {
          const items = (school.attributes?.facilities || []).map(item => getOptionLabel(item, t))
          return items.length > 0 ? makeTags(items) : renderPlaceholder()
        },
        getCompare: (school) => school.attributes?.facilities || [],
      },
      {
        label: t('outdoor.title'),
        getValue: (school) => {
          const entries = outdoorEntries(school, language)
          if (entries.length === 0) return renderPlaceholder()
          return (
            <div className="space-y-1">
              {entries.map(entry => (
                <div key={entry.locationId} className="text-sm text-neutral-700">
                  {t(`outdoor.compare.${entry.kind}`)}
                </div>
              ))}
            </div>
          )
        },
        getCompare: (school) => outdoorEntries(school, language).map(entry => entry.kind).join(','),
      },
      {
        label: t('compare.labels.accessibility'),
        getValue: (school) => renderBinaryEvidence(amenityEvidenceForSchool(school).accessibility),
        getCompare: (school) => amenityEvidenceForSchool(school).accessibility,
      },
    ]

    const admissionRows = [
      {
        label: t('compare.labels.entryRequirements'),
        getValue: (school) => {
          const requirement = getAdmissionRequirement(
            curatedRequirement(school.admission_info, language) ||
            school.attributes?.entry_requirements,
            t
          )
          return requirement ? (
            <span className="text-sm text-neutral-700">{requirement.text}</span>
          ) : renderPlaceholder()
        },
        getCompare: (school) => curatedRequirement(school.admission_info, language) || school.attributes?.entry_requirements || null,
      },
      {
        label: t('compare.labels.pointsThreshold'),
        getValue: (school) => {
          if (school.school_type !== 'state') return renderPlaceholder('compare.notApplicable')
          const primaryAgeGroup = getPrimaryAgeGroup(school)
          const history = getPointsHistory(school.admission_info, primaryAgeGroup).slice(0, 3)
          if (history.length === 0) return renderPlaceholder()
          return (
            <div className="space-y-1 text-sm text-neutral-700">
              {history.map(item => (
                <div key={item.year} className="flex justify-between gap-3">
                  <span>{item.year}</span>
                  <span className="font-medium">{item.points}</span>
                </div>
              ))}
            </div>
          )
        },
        getCompare: (school) => {
          const primaryAgeGroup = getPrimaryAgeGroup(school)
          const history = getPointsHistory(school.admission_info, primaryAgeGroup)
          return history.length > 0 ? history[0].points : null
        },
      },
      {
        label: t('compare.labels.minNvoScore'),
        getValue: (school) => {
          if (school.school_type !== 'state') return renderPlaceholder('compare.notApplicable')
          const history = getMinNvoScores(school.admission_info).slice(0, 3)
          if (history.length === 0) return renderPlaceholder()
          return (
            <div className="space-y-1 text-sm text-neutral-700">
              {history.map(item => (
                <div key={item.year} className="flex justify-between gap-3">
                  <span>{item.year}</span>
                  <span className="font-medium">{item.score}</span>
                </div>
              ))}
            </div>
          )
        },
        getCompare: (school) => getMinNvoScore(school.admission_info)?.score ?? null,
      },
      {
        label: t('compare.labels.applicationDeadline'),
        getValue: (school) => {
          const deadlines = school.attributes?.application_deadlines || []
          const deadline = school.admission_info?.deadline
          return deadline
            ? <span className="text-sm text-neutral-700">{deadline}</span>
            : deadlines.length > 0 ? makeTags(deadlines) : renderPlaceholder()
        },
        getCompare: (school) => school.admission_info?.deadline ||
          (school.attributes?.application_deadlines?.length > 0 ? school.attributes.application_deadlines : null),
      },
      {
        label: t('compare.labels.spotsAvailable'),
        getValue: (school) => {
          const extractedSpots = school.attributes?.available_spots || []
          const spots = school.admission_info?.spots_available
          return spots != null
            ? <span className="text-sm text-neutral-700">{spots}</span>
            : extractedSpots.length > 0 ? makeTags(extractedSpots) : renderPlaceholder()
        },
        getCompare: (school) => school.admission_info?.spots_available ??
          (school.attributes?.available_spots?.length > 0 ? school.attributes.available_spots : null),
      },
      {
        label: t('compare.labels.eligibleAgeGroups'),
        getValue: (school) => {
          const ageGroups = orderAgeGroups([...new Set((school.locations || [])
            .flatMap(location => (Array.isArray(location.age_groups) ? location.age_groups : [location.age_group]))
            .filter(Boolean))], countryConfig)
          if (ageGroups.length === 0) return renderPlaceholder()
          // The age group the parent searched for is highlighted.
          return (
            <div className="flex flex-wrap gap-1">
              {ageGroups.map(group => (
                <span
                  key={group}
                  className={`rounded px-2 py-0.5 text-xs ${
                    selectedAgeGroup && group === selectedAgeGroup
                      ? 'bg-primary-700 text-white'
                      : 'bg-neutral-100 text-neutral-700'
                  }`}
                >
                  {t(`ageGroups.${group}`)}
                </span>
              ))}
            </div>
          )
        },
        getCompare: (school) => (school.locations || [])
          .flatMap(location => (Array.isArray(location.age_groups) ? location.age_groups : [location.age_group]))
          .filter(Boolean),
      },
    ]

    const pricingRows = [
      {
        label: t('compare.labels.pricingOverview'),
        getValue: (school) => {
          if (!school.pricing || school.pricing.length === 0) {
            return school.school_type === 'state'
              ? <span className="text-sm text-neutral-700">{t('compare.free')}</span>
              : renderPlaceholder('compare.notAvailable')
          }


          // Grouped by academic year like the school page, so last year's fee is never
          // read as this year's.
          return (
            <div className="space-y-4">
              {groupPricingByAcademicYear(school.pricing).map(group => (
                <div key={group.key} className="space-y-3">
                  <div className="text-xs font-semibold text-neutral-500">
                    {group.academicYear || t('pricing.yearNotStated')}
                    {group.yearStatus === YEAR_STATUS.DATED_OTHER ? ` · ${t('pricing.notCurrentYear')}` : ''}
                    {group.yearStatus === YEAR_STATUS.NEXT ? ` · ${t('pricing.nextYear')}` : ''}
                  </div>
                  {sortByCategory(group.rows).map((price) => (
                    <div key={price.id} className="space-y-1">
                      <div className="text-sm font-medium text-neutral-900">
                        {t(`pricing.${priceCategory(price)}`)}
                        {[priceAgeGroupLabel(price, t, language)].filter(Boolean).map(label => ` • ${label}`)}
                        {[planNameBeside(price, t, priceAgeGroupLabel(price, t, language), language)].filter(Boolean).map(name => (
                          <span key="plan" lang={textLang(name, language)}>{` • ${name}`}</span>
                        ))}
                        {!group.academicYear && price.academic_year ? ` • ${price.academic_year}` : ''}
                      </div>
                      <div className="text-sm text-neutral-700">
                        {formatPriceLabel(price, language, t)}
                        {periodSuffix(price, t)}
                      </div>
                      <InlineSource
                        badgeKey={price.source}
                        badgeLabel={t(`priceSource.${price.source}`)}
                        url={price.source_url}
                        dateLabel={formatDate(price.scraped_at, language)}
                        t={t}
                      />
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )
        },
        getCompare: (school) => (school.pricing || []).map(price => price.id),
      },
    ]

    const locationRows = [
      {
        label: t('schools.locations'),
        getValue: (school) => {
          if (!school.locations || school.locations.length === 0) return renderPlaceholder()
          return (
            <div className="space-y-2">
              {school.locations.map((location, idx) => {
                const distance = userLocation && location.lat && location.lng
                  ? calculateDistance(userLocation.lat, userLocation.lng, location.lat, location.lng)
                  : null
                const shiftInfo = (location.age_group_shifts || []).find(item => item?.shift)
                return (
                  <div key={`${location.id || idx}`} className="text-sm text-neutral-700">
                    <div className="font-medium text-neutral-900">
                      {orderAgeGroups((Array.isArray(location.age_groups) ? location.age_groups : [location.age_group])
                        .filter(Boolean), countryConfig)
                        .map(group => t(`ageGroups.${group}`))
                        .join(', ')}
                    </div>
                    <div>{getAddress(location, language)}</div>
                    <div className="text-xs text-neutral-500">
                      {shiftInfo?.shift ? t(`schoolCard.shift.${shiftInfo.shift}`) : null}
                      {distance != null
                        ? ` • ${formatDistance(distance, { language: language, t })}`
                        : ''}
                    </div>
                  </div>
                )
              })}
            </div>
          )
        },
        getCompare: (school) => (school.locations || []).map((location) => {
          const distance = userLocation && location.lat && location.lng
            ? calculateDistance(userLocation.lat, userLocation.lng, location.lat, location.lng)
            : null
          return getLocationDisplayEvidence(
            location,
            getAddress(location, language),
            distance
          )
        }),
      },
    ]

    const contactRows = [
      {
        label: t('schools.website'),
        getValue: (school) => safeWebsiteUrl(school.website_url) ? (
          <a
            href={safeWebsiteUrl(school.website_url)}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary-600 hover:text-primary-700 text-sm underline"
          >
            {sourceSiteName(safeWebsiteUrl(school.website_url)) || school.website_url}
          </a>
        ) : renderPlaceholder(),
        getCompare: (school) => school.website_url || null,
      },
      {
        label: t('schools.phone'),
        getValue: (school) => {
          const phones = [...new Set((school.locations || []).map(location => location.phone).filter(Boolean))]
          if (phones.length === 0) return renderPlaceholder()
          return (
            <div className="space-y-1">
              {phones.map((phone) => (
                <div key={phone} className="flex flex-col gap-1 text-sm">
                  <PhoneLinks phone={phone} className="inline-flex items-center gap-1.5 text-primary-700 hover:underline" />
                </div>
              ))}
            </div>
          )
        },
        getCompare: (school) => (school.locations || []).map(location => location.phone).filter(Boolean),
      },
    ]

    // One row per topic any compared school has a key fact for; facts link their page.
    const factsBySchool = new Map(schools.map(school => [school.id, getSchoolFacts(school, language, { keyOnly: true })]))
    const topicFacts = (school, topic) => (factsBySchool.get(school.id) || []).filter(fact => fact.topic === topic)
    // The facts' labels summed up (admission, curriculum, bus areas ...), one row a kind.
    const glanceLine = (school, key) => atAGlanceLines(school, language, t).find(line => line.key === key)
    const glanceValue = (school, key) => glanceLine(school, key)?.value || null
    const glanceRows = atAGlanceKeysIn(schools, language, t).map(key => ({
      label: t(`schoolFacts.glance.${key}`),
      getValue: (school) => {
        const line = glanceLine(school, key)
        if (!line) return renderPlaceholder()
        return line.href ? (
          <span>
            {line.value}{' '}
            <a href={line.href} target="_blank" rel="noopener noreferrer nofollow" className="text-primary-700 underline underline-offset-2">
              {line.linkText}
            </a>
          </span>
        ) : line.value
      },
      getCompare: (school) => glanceValue(school, key),
    }))
    const factRows = factTopicsIn(schools, language).map(topic => ({
      label: t(`schoolFacts.topics.${topic}`),
      getValue: (school) => {
        const facts = topicFacts(school, topic)
        return facts.length > 0 ? (
          <ul className="space-y-1">
            {facts.map((fact, index) => (
              <li key={index} className="text-sm text-neutral-700">
                {fact.text}{' '}
                <a href={fact.sourceUrl} target="_blank" rel="noopener noreferrer nofollow" className="text-primary-700 underline underline-offset-2">
                  {t('schoolFacts.source')}
                </a>
              </li>
            ))}
          </ul>
        ) : renderPlaceholder()
      },
      getCompare: (school) => topicFacts(school, topic).map(fact => fact.text).join('|') || null,
    }))

    return [
      { key: 'overview', title: t('compare.sections.quickOverview'), rows: overviewRows },
      { key: 'academic', title: t('compare.sections.academicPerformance'), rows: academicRows },
      { key: 'practical', title: t('compare.sections.practicalDetails'), rows: practicalRows },
      { key: 'websiteFacts', title: t('schoolFacts.title'), rows: [...glanceRows, ...factRows] },
      { key: 'admission', title: t('compare.sections.admissionEnrollment'), rows: admissionRows },
      { key: 'pricing', title: t('compare.sections.pricing'), rows: pricingRows },
      { key: 'locations', title: t('compare.sections.locations'), rows: locationRows },
      { key: 'contact', title: t('compare.sections.contact'), rows: contactRows },
    ]
}
