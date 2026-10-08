import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import Layout from '../Layout/Layout'
import { useCompare } from '../../context/CompareContext'
import { getLastSearchUrl } from '../../utils/searchViewState'
import { schoolLevelLabel } from '../../utils/levelLabel'
import { useCountry } from '../../context/CountryContext'
import { languageLabel } from '../../utils/languages'
import TagList from '../SchoolDetailPage/TagList'
import PhoneLinks from '../SchoolDetailPage/PhoneLinks'
import { fetchCompare, fetchExamAverages } from '../../api/schools'
import { calculateDistance, formatDistance } from '../../utils/distance'
import { compareSchoolNames, getSchoolName, getAddress, getSummary } from '../../utils/i18n'
import { hasAnySchoolSummary } from './summaryVisibility'
import { getProvenanceSourceKey } from './sourceMetadata'
import {
  getAdmissionStatusKey,
  getAfterSchoolEvidence,
  getCanonicalAmenityEvidence,
  getLocationDisplayEvidence,
  hasDisplayEvidence,
  normalizeSchoolList,
} from '../../utils/schoolAttributes'
import { getBenchmarkComparison, getBenchmarkToneClasses, getNvoDetail as getSharedNvoDetail } from '../../utils/nvo'
import { classifyAdmissionRequirement, curatedRequirement } from '../../utils/admission'
import { yearlyTuitionRangeEur } from '../../utils/pricing'

const SOURCE_BADGE_STYLES = {
  official: 'bg-emerald-50 text-emerald-700',
  official_website: 'bg-emerald-50 text-emerald-700',
  scraped_website: 'bg-teal-50 text-teal-700',
  forum: 'bg-amber-50 text-amber-700',
  community_forum: 'bg-amber-50 text-amber-700',
  parent_submitted: 'bg-violet-50 text-violet-700',
  school_contact: 'bg-cyan-50 text-cyan-700',
  not_found: 'bg-neutral-100 text-neutral-500',
  government: 'bg-blue-50 text-blue-700',
  website: 'bg-cyan-50 text-cyan-700',
  unknown: 'bg-neutral-100 text-neutral-500',
}

const SOURCE_CATEGORY_LABELS = {
  pricing: 'compare.sections.pricing',
  academic: 'compare.sections.academicPerformance',
  admission: 'compare.sections.admissionEnrollment',
  locations: 'compare.sections.locations',
  contact: 'compare.sections.contact',
  schedule: 'compare.labels.schedule',
  practical: 'compare.sections.practicalDetails',
  other: 'compare.sections.sources',
}

function parseIds(value) {
  if (!value) return []
  return [...new Set(
    value
      .split(',')
      .map(part => parseInt(part.trim(), 10))
      .filter(id => Number.isFinite(id) && id > 0)
  )]
}

function formatDate(value, locale) {
  if (!value) return null
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null
  return new Intl.DateTimeFormat(locale, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date)
}

function formatCurrency(amount, locale, currency = 'EUR') {
  if (amount == null || Number.isNaN(amount)) return null
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    maximumFractionDigits: 0,
  }).format(amount)
}

function formatPriceLabel(price, locale, t) {
  const currency = price.currency || 'EUR'
  const min = price.amount_min != null ? formatCurrency(Number(price.amount_min), locale, currency) : null
  const max = price.amount_max != null ? formatCurrency(Number(price.amount_max), locale, currency) : null
  const exact = price.amount != null ? formatCurrency(Number(price.amount), locale, currency) : null

  if (min && max) return `${min}–${max}`
  if (min) return min
  if (max) return max
  if (exact) return exact
  return t('pricing.priceOnRequest')
}

function shortenUrl(url) {
  if (!url) return null
  try {
    const parsed = new URL(url)
    const parts = parsed.pathname.split('/').filter(Boolean)
    const suffix = parts.length > 0 ? `/${parts[0]}` : ''
    return `${parsed.hostname}${suffix}`
  } catch {
    return url
  }
}

function getPrimaryLocation(school) {
  return school.locations?.find(location => location.is_primary) || school.locations?.[0] || null
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

function getLanguageLabel(language, t) {
  return languageLabel(language, t)
}

function getOptionLabel(option, t) {
  if (!option) return ''
  const key = `advancedFilters.options.${option}`
  const translated = t(key)
  if (translated !== key) return translated
  return option.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase())
}

function getStatusInfo(school, t) {
  const statusKey = getAdmissionStatusKey(school.admission_info?.status)
  if (statusKey === 'accepting') {
    return { key: 'accepting', label: t('schoolCard.status.accepting'), color: 'bg-emerald-50 text-emerald-700' }
  }
  if (statusKey === 'waitlist') {
    return { key: 'waitlist', label: t('schoolCard.status.waitlist'), color: 'bg-amber-50 text-amber-700' }
  }
  if (statusKey === 'full') {
    return { key: 'full', label: t('schoolCard.status.full'), color: 'bg-red-50 text-red-700' }
  }
  return null
}

function getAdmissionRequirement(rawRequirement, t) {
  const requirement = classifyAdmissionRequirement(rawRequirement)
  if (!requirement) return null
  if (requirement.kind === 'interview') {
    return t('schoolCard.admissions.interviewRequired')
  }
  if (requirement.kind === 'test') {
    return t('schoolCard.admissions.testRequired')
  }
  if (requirement.kind === 'none') {
    return t('schoolCard.admissions.noEntranceExam')
  }
  return requirement.text
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

function getPointsHistory(admissionInfo, ageGroup) {
  const thresholds = admissionInfo?.historical_thresholds || []
  if (thresholds.length === 0) return []

  const relevant = ageGroup
    ? thresholds.filter(item => item.age_group === ageGroup)
    : thresholds

  return relevant
    .map(item => {
      const rounds = item.rounds || []
      const lastRound = rounds.reduce((acc, round) => (round.round > acc.round ? round : acc), rounds[0])
      return lastRound ? { year: item.year, points: lastRound.last_admitted_points } : null
    })
    .filter(Boolean)
    .sort((a, b) => b.year - a.year)
}

function getLocationAgeGroups(location) {
  if (!location) return []
  if (Array.isArray(location.age_groups)) return location.age_groups.filter(Boolean)
  if (location.age_group) return [location.age_group]
  return []
}

function getPrimaryAgeGroup(school) {
  const primaryLocation = school.locations?.find(loc => loc.is_primary) || school.locations?.[0]
  return primaryLocation ? getLocationAgeGroups(primaryLocation)[0] : null
}

function getPrimaryShiftInfo(school) {
  const primaryLocation = school.locations?.find(loc => loc.is_primary) || school.locations?.[0]
  if (!primaryLocation?.age_group_shifts || primaryLocation.age_group_shifts.length === 0) return null
  const primaryGroup = getPrimaryAgeGroup(school)
  if (primaryGroup) {
    const match = primaryLocation.age_group_shifts.find(item => item.age_group === primaryGroup)
    if (match) return match
  }
  return primaryLocation.age_group_shifts[0] || null
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

function getMinNvoScores(admissionInfo) {
  const scores = admissionInfo?.historical_min_scores || []
  return scores
    .map(item => ({ year: item.year, score: item.min_score }))
    .sort((a, b) => b.year - a.year)
}

function formatPercent(value, decimals = 1) {
  if (value == null || Number.isNaN(value)) return null
  return Number(value).toFixed(decimals)
}

function getPerformanceStyle(value) {
  if (value == null) return { text: 'text-neutral-600' }
  if (value >= 75) return { text: 'text-emerald-500' }
  if (value >= 60) return { text: 'text-amber-500' }
  return { text: 'text-red-500' }
}

// Combined (Bulgarian + maths) result against the national average for the same exam and
// year, with the same ±5 pp tolerance as the detail page, so the colours agree.
function getCombinedBenchmark(nvoDetail, year, value, examAverages) {
  const national = examAverages?.by_year?.[nvoDetail?.examType]?.[String(year)]
  if (value == null || national?.math == null || national?.bulgarian == null) return null
  const nationalValue = (Number(national.math) + Number(national.bulgarian)) / 2
  const diff = value - nationalValue
  const tone = diff >= 5 ? 'above' : diff <= -5 ? 'below' : 'near'
  return { nationalValue, ...getBenchmarkToneClasses(tone) }
}

function getNvoValueStyle({ value, examType, year, subjectKey, examAverages }) {
  const benchmark = getBenchmarkComparison({
    examType,
    year,
    subjectKey,
    value,
    examAverages,
  })

  if (benchmark) {
    return { text: benchmark.textClass, benchmark }
  }

  return { text: getPerformanceStyle(value).text, benchmark: null }
}

function getBenchmarkTooltip(value, benchmark, t) {
  if (value == null || !benchmark) return null

  const diff = Math.abs(benchmark.diff).toFixed(1)
  const valueText = formatPercent(value, 1)
  const benchmarkText = formatPercent(benchmark.benchmarkValue, 1)

  if (benchmark.tone === 'above') {
    return t('academicPerformance.tooltipBenchmarkAbove', {
      value: valueText,
      diff,
      benchmark: benchmarkText,
    })
  }
  if (benchmark.tone === 'below') {
    return t('academicPerformance.tooltipBenchmarkBelow', {
      value: valueText,
      diff,
      benchmark: benchmarkText,
    })
  }

  return t('academicPerformance.tooltipBenchmarkNear', {
    value: valueText,
    diff,
    benchmark: benchmarkText,
  })
}

function getTrendTooltip(latest, average, trend, t) {
  if (latest == null || average == null || !trend) return null

  const diff = Math.abs(trend.diff).toFixed(1)
  const latestText = formatPercent(latest, 1)
  const averageText = formatPercent(average, 1)

  if (trend.arrow === '↑') {
    return t('academicPerformance.tooltipTrendUp', {
      latest: latestText,
      average: averageText,
      diff,
    })
  }
  if (trend.arrow === '↓') {
    return t('academicPerformance.tooltipTrendDown', {
      latest: latestText,
      average: averageText,
      diff,
    })
  }

  return t('academicPerformance.tooltipTrendFlat', {
    latest: latestText,
    average: averageText,
    diff,
  })
}

function getTrendInfo(latest, average) {
  if (latest == null || average == null) return null
  const diff = latest - average
  if (diff >= 2) return { arrow: '↑', className: 'text-emerald-500', diff }
  if (diff <= -2) return { arrow: '↓', className: 'text-red-500', diff }
  return { arrow: '→', className: 'text-neutral-400', diff }
}

function getNvoDetail(school, t, ageGroup = null) {
  return getSharedNvoDetail(school, t, { ageGroup })
}

function normalizeCompareValue(value) {
  if (value == null) return '__null__'
  if (Array.isArray(value)) return JSON.stringify(value.slice().sort())
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value).toLowerCase()
}

function InlineSource({ badgeKey, badgeLabel, url, displayUrl, dateLabel, t }) {
  if (!badgeLabel && !url && !dateLabel) return null

  const finalDisplay = displayUrl || shortenUrl(url)
  return (
    <div className="mt-1 flex flex-wrap items-center gap-1 text-[12px] text-neutral-500">
      {badgeLabel && (
        <span className={`inline-flex items-center rounded px-1.5 py-0.5 font-medium ${SOURCE_BADGE_STYLES[badgeKey] || SOURCE_BADGE_STYLES.unknown}`}>
          {badgeLabel}
        </span>
      )}
      {dateLabel && <span>{dateLabel}</span>}
      {finalDisplay && (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-teal-700 hover:text-teal-800 underline"
          aria-label={t('compare.opensInNewWindow')}
        >
          {finalDisplay}
          <span aria-hidden="true">↗</span>
        </a>
      )}
    </div>
  )
}

function ComparePage() {
  const { t, i18n } = useTranslation()
  const { config: countryConfig } = useCountry()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const { compareList, removeFromCompare, clearCompare, syncCompareList, maxCompare } = useCompare()
  const [schools, setSchools] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [missingCount, setMissingCount] = useState(0)
  const [mobileStart, setMobileStart] = useState(0)
  const loadedRef = useRef({ requested: new Set(), byId: new Map() })
  const desktopBodyRef = useRef(null)
  const desktopHeaderRef = useRef(null)
  const mobileBodyRef = useRef(null)
  const mobileHeaderRef = useRef(null)
  const [highlightDiffs, setHighlightDiffs] = useState(false)
  const [sortBy, setSortBy] = useState('name')
  const [shareStatus, setShareStatus] = useState('')
  const [userLocation, setUserLocation] = useState(null)
  const [sourcesOpen, setSourcesOpen] = useState(false)
  const [examAverages, setExamAverages] = useState(null)
  const queryKey = searchParams.get('ids') ? 'ids' : (searchParams.get('schools') ? 'schools' : null)
  const queryIds = useMemo(() => parseIds(queryKey ? searchParams.get(queryKey) : ''), [queryKey, searchParams])
  const ids = useMemo(() => (queryIds.length > 0 ? queryIds : compareList.map(s => s.id)), [queryIds, compareList])
  const selectedAgeGroup = searchParams.get('age_group')

  useEffect(() => {
    const saved = localStorage.getItem('userLocation')
    if (!saved) return
    try {
      const parsed = JSON.parse(saved)
      if (typeof parsed?.lat === 'number' && typeof parsed?.lng === 'number') {
        setUserLocation(parsed)
      }
    } catch {
      // ignore
    }
  }, [])

  useEffect(() => {
    // One school left because the others no longer exist: stay and explain (missingCount).
    if (ids.length === 0 || (ids.length === 1 && missingCount === 0)) {
      // Replace, so Back does not land on a compare page that redirects again.
      navigate(getLastSearchUrl(), { replace: true })
      return
    }

    // Removing a school only narrows the set: reuse the data already loaded.
    const { requested, byId } = loadedRef.current
    if (ids.every(id => requested.has(id))) {
      setSchools(ids.map(id => byId.get(id)).filter(Boolean))
      setLoading(false)
      return
    }

    let cancelled = false
    const loadSchools = async () => {
      setLoading(true)
      setError(null)
      try {
        const data = await fetchCompare(ids)
        if (cancelled) return
        const loadedById = new Map(data.map(item => [item.id, item]))
        loadedRef.current = { requested: new Set(ids), byId: loadedById }
        const ordered = ids.map(id => loadedById.get(id)).filter(Boolean)
        setSchools(ordered)
        setMissingCount(ids.length - ordered.length)
        // Refresh the compare bar's stored names and drop ids that no longer exist.
        syncCompareList(ids, data)
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadSchools()
    return () => {
      cancelled = true
    }
  }, [ids, navigate, syncCompareList, missingCount])

  useEffect(() => {
    let isMounted = true

    fetchExamAverages()
      .then((data) => {
        if (!isMounted) return
        setExamAverages(data)
      })
      .catch(() => {
        if (!isMounted) return
        setExamAverages(null)
      })

    return () => {
      isMounted = false
    }
  }, [])

  const localizedSchools = useMemo(
    () => normalizeSchoolList(schools, i18n.language),
    [schools, i18n.language]
  )

  const metricsById = useMemo(() => {
    const map = new Map()
    localizedSchools.forEach((school) => {
      const primaryLocation = getPrimaryLocation(school)
      const distance = userLocation && primaryLocation?.lat && primaryLocation?.lng
        ? calculateDistance(userLocation.lat, userLocation.lng, primaryLocation.lat, primaryLocation.lng)
        : null
      const pricingRange = yearlyTuitionRangeEur(school.pricing)
      const nvoDetail = school.school_type === 'international' ? null : getNvoDetail(school, t, selectedAgeGroup)
      const overallLatest = nvoDetail?.latestCombined ?? null
      const overallAvg = nvoDetail?.schoolAverageCombined ?? null

      map.set(school.id, {
        distance,
        distanceApproximate: Boolean(primaryLocation?.coordinates_approximate),
        pricingRange,
        nvoDetail,
        overallLatest,
        overallAvg,
      })
    })
    return map
  }, [localizedSchools, t, userLocation, selectedAgeGroup])

  const sortedSchools = useMemo(() => {
    const list = [...localizedSchools]

    list.sort((a, b) => {
      if (sortBy === 'name') {
        const nameA = getSchoolName(a, i18n.language)
        const nameB = getSchoolName(b, i18n.language)
        return compareSchoolNames(nameA, nameB, i18n.language)
      }

      if (sortBy === 'distance') {
        const distA = metricsById.get(a.id)?.distance
        const distB = metricsById.get(b.id)?.distance
        if (distA == null && distB == null) return 0
        if (distA == null) return 1
        if (distB == null) return -1
        return distA - distB
      }

      if (sortBy === 'price') {
        const priceA = metricsById.get(a.id)?.pricingRange?.min
        const priceB = metricsById.get(b.id)?.pricingRange?.min
        if (priceA == null && priceB == null) return 0
        if (priceA == null) return 1
        if (priceB == null) return -1
        return priceA - priceB
      }

      if (sortBy === 'nvo') {
        const nvoA = metricsById.get(a.id)?.overallLatest ?? metricsById.get(a.id)?.overallAvg
        const nvoB = metricsById.get(b.id)?.overallLatest ?? metricsById.get(b.id)?.overallAvg
        if (nvoA == null && nvoB == null) return 0
        if (nvoA == null) return 1
        if (nvoB == null) return -1
        return nvoB - nvoA
      }

      return 0
    })

    return list
  }, [localizedSchools, sortBy, i18n.language, metricsById])

  const handleRemove = (schoolId) => {
    setMissingCount(0)
    removeFromCompare(schoolId)
    const next = ids.filter(id => id !== schoolId)
    if (queryKey) {
      const params = new URLSearchParams(searchParams)
      if (next.length > 0) {
        params.set(queryKey, next.join(','))
      } else {
        params.delete(queryKey)
      }
      setSearchParams(params, { replace: true })
    }
  }

  const handleClear = () => {
    clearCompare()
    if (queryKey) {
      const params = new URLSearchParams(searchParams)
      params.delete(queryKey)
      setSearchParams(params, { replace: true })
    }
  }

  const searchReturnPath = () => getLastSearchUrl()

  // With no in-app history (a shared link), navigate(-1) would leave the site.
  const handleBack = () => {
    if ((window.history.state?.idx ?? 0) > 0) {
      navigate(-1)
    } else {
      navigate(searchReturnPath())
    }
  }

  const syncHeaderScroll = (body, header) => {
    if (body && header && header.scrollLeft !== body.scrollLeft) {
      header.scrollLeft = body.scrollLeft
    }
  }

  const handleMobileScroll = () => {
    const body = mobileBodyRef.current
    if (!body) return
    syncHeaderScroll(body, mobileHeaderRef.current)
    const columnWidth = body.clientWidth / 2
    if (columnWidth > 0) setMobileStart(Math.round(body.scrollLeft / columnWidth))
  }

  const scrollMobileBy = (direction) => {
    const body = mobileBodyRef.current
    if (!body) return
    body.scrollBy({ left: direction * (body.clientWidth / 2), behavior: 'smooth' })
  }

  const handleShare = async () => {
    if (ids.length === 0) return
    const url = new URL(window.location.href)
    url.searchParams.set('ids', ids.join(','))

    try {
      await navigator.clipboard.writeText(url.toString())
      setShareStatus(t('compare.shareCopied'))
    } catch {
      setShareStatus(t('compare.shareFailed'))
    }

    setTimeout(() => setShareStatus(''), 2000)
  }

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

  const sections = useMemo(() => {
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
      focus.forEach(item => {
        if (item?.language) languages.add(getLanguageLabel(item.language, t))
      })
      ;(attributes.languages_of_instruction || []).forEach(lang => {
        if (lang) languages.add(getLanguageLabel(lang, t))
      })
      return [...languages]
    }

    const overviewRows = [
      {
        label: t('compare.labels.type'),
        getValue: (school) => (
          <span className="text-sm text-neutral-700">{t(`schoolTypes.${school.school_type}`)}</span>
        ),
        getCompare: (school) => school.school_type,
      },
      ...(hasAnySchoolSummary(schools, i18n.language)
        ? [{
            label: t('compare.summary'),
            getValue: (school) => {
              const summary = getSummary(school, i18n.language, 'short')
              return summary
                ? <span className="text-sm text-neutral-700">{summary}</span>
                : renderPlaceholder()
            },
            getCompare: (school) => getSummary(school, i18n.language, 'short') || null,
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
            const formatted = pricingRange.min === pricingRange.max
              ? formatCurrency(pricingRange.min, i18n.language, pricingRange.currency)
              : `${formatCurrency(pricingRange.min, i18n.language, pricingRange.currency)} - ${formatCurrency(pricingRange.max, i18n.language, pricingRange.currency)}`
            return (
              <div>
                <div className="text-sm font-semibold text-neutral-900">{formatted}</div>
                <div className="text-xs text-neutral-500">{t('compare.labels.tuitionYearly')}</div>
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

          return renderPlaceholder(school.school_type === 'international' ? 'compare.notApplicable' : 'compare.notAvailable')
        },
        getCompare: (school) => {
          if (school.school_type !== 'state') {
            return metricsById.get(school.id)?.pricingRange?.min ?? null
          }
          const primaryAgeGroup = getPrimaryAgeGroup(school)
          const lastAdmitted = getLastAdmittedPoints(school.admission_info, primaryAgeGroup)
          const minScore = getMinNvoScore(school.admission_info)
          return lastAdmitted?.points ?? minScore?.score ?? null
        },
      },
      {
        label: t('compare.labels.nvoAverage'),
        getValue: (school) => {
          if (school.school_type === 'international') {
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
              <div className={`text-sm font-semibold ${benchmark?.textClass || 'text-neutral-900'}`}>{formatPercent(value, 1)}%</div>
              <div className="text-xs text-neutral-500">
                {[
                  detail?.gradeLabel,
                  isLatest ? year : t('compare.labels.fiveYearAverage'),
                ].filter(Boolean).join(' · ')}
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
                {formatDistance(distance, { approximate: metrics.distanceApproximate })}
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
            <span className={`inline-flex rounded px-2 py-0.5 text-xs font-medium ${status.color}`}>
              {status.label}
            </span>
          ) : renderPlaceholder()
        },
        getCompare: (school) => getStatusInfo(school, t)?.key || null,
      },
    ]

    const academicRows = [
      {
        label: t('compare.labels.nvoMath'),
        getValue: (school) => {
          if (school.school_type === 'international') return renderPlaceholder('compare.notApplicable')
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
                  {formatPercent(value, 1)}%
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
                  {t('compare.labels.avgValue', { value: formatPercent(detail.mathAvg, 1) })}
                </div>
              )}
              <InlineSource
                badgeKey="government"
                badgeLabel={t('compare.sourceTypes.government')}
                url={(school.exam_results || []).find(result => result.exam_type === detail.examType && String(result.subject || '').toLowerCase().includes('math'))?.source_url}
                dateLabel={formatDate((school.exam_results || []).find(result => result.exam_type === detail.examType && String(result.subject || '').toLowerCase().includes('math'))?.scraped_at, i18n.language)}
                t={t}
              />
            </div>
          )
        },
        getCompare: (school) => metricsById.get(school.id)?.nvoDetail?.latestMath ?? metricsById.get(school.id)?.nvoDetail?.mathAvg ?? null,
      },
      {
        label: t('compare.labels.nvoBulgarian'),
        getValue: (school) => {
          if (school.school_type === 'international') return renderPlaceholder('compare.notApplicable')
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
                  {formatPercent(value, 1)}%
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
                  {t('compare.labels.avgValue', { value: formatPercent(detail.bgAvg, 1) })}
                </div>
              )}
              <InlineSource
                badgeKey="government"
                badgeLabel={t('compare.sourceTypes.government')}
                url={(school.exam_results || []).find(result => result.exam_type === detail.examType && String(result.subject || '').toLowerCase().includes('bulgarian'))?.source_url}
                dateLabel={formatDate((school.exam_results || []).find(result => result.exam_type === detail.examType && String(result.subject || '').toLowerCase().includes('bulgarian'))?.scraped_at, i18n.language)}
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
            curatedRequirement(school.admission_info, i18n.language) ||
            school.attributes?.entry_requirements,
            t
          )
          return requirement ? (
            <span className="text-sm text-neutral-700">{requirement}</span>
          ) : renderPlaceholder()
        },
        getCompare: (school) => curatedRequirement(school.admission_info, i18n.language) || school.attributes?.entry_requirements || null,
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
          const ageGroups = [...new Set((school.locations || [])
            .flatMap(location => (Array.isArray(location.age_groups) ? location.age_groups : [location.age_group]))
            .filter(Boolean))]
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
            return renderPlaceholder(school.school_type === 'state' ? 'compare.notApplicable' : 'compare.notAvailable')
          }


          return (
            <div className="space-y-3">
              {school.pricing.map((price) => (
                <div key={price.id} className="space-y-1">
                  <div className="text-sm font-medium text-neutral-900">
                    {t(`pricing.${price.category}`)}
                    {price.age_group ? ` • ${t(`ageGroups.${price.age_group}`)}` : ''}
                    {price.plan_name ? ` • ${price.plan_name}` : ''}
                    {price.academic_year ? ` • ${price.academic_year}` : ''}
                  </div>
                  <div className="text-sm text-neutral-700">
                    {formatPriceLabel(price, i18n.language, t)}
                    {` / ${price.period ? t(`pricing.${price.period}`) : t('pricing.periodNotStated')}`}
                  </div>
                  <InlineSource
                    badgeKey={price.source}
                    badgeLabel={t(`priceSource.${price.source}`)}
                    url={price.source_url}
                    dateLabel={formatDate(price.scraped_at, i18n.language)}
                    t={t}
                  />
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
                      {(Array.isArray(location.age_groups) ? location.age_groups : [location.age_group])
                        .filter(Boolean)
                        .map(group => t(`ageGroups.${group}`))
                        .join(', ')}
                    </div>
                    <div>{getAddress(location, i18n.language)}</div>
                    <div className="text-xs text-neutral-500">
                      {shiftInfo?.shift ? t(`schoolCard.shift.${shiftInfo.shift}`) : null}
                      {distance != null
                        ? ` • ${formatDistance(distance, { approximate: location.coordinates_approximate })}`
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
            getAddress(location, i18n.language),
            distance
          )
        }),
      },
    ]

    const contactRows = [
      {
        label: t('schools.website'),
        getValue: (school) => school.website_url ? (
          <a
            href={school.website_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary-600 hover:text-primary-700 text-sm underline"
          >
            {shortenUrl(school.website_url)}
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

    return [
      { key: 'overview', title: t('compare.sections.quickOverview'), rows: overviewRows },
      { key: 'academic', title: t('compare.sections.academicPerformance'), rows: academicRows },
      { key: 'practical', title: t('compare.sections.practicalDetails'), rows: practicalRows },
      { key: 'admission', title: t('compare.sections.admissionEnrollment'), rows: admissionRows },
      { key: 'pricing', title: t('compare.sections.pricing'), rows: pricingRows },
      { key: 'locations', title: t('compare.sections.locations'), rows: locationRows },
      { key: 'contact', title: t('compare.sections.contact'), rows: contactRows },
    ]
  }, [t, i18n.language, metricsById, userLocation, schools, selectedAgeGroup, examAverages])

  const visibleSections = useMemo(() => sections
    .map(section => ({
      ...section,
      rows: section.rows.filter(row => sortedSchools.some(school => (
        hasDisplayEvidence(row.getCompare?.(school))
      ))),
    }))
    .filter(section => section.rows.length > 0), [sections, sortedSchools])

  const completenessById = useMemo(() => {
    const map = new Map()
    schools.forEach((school) => {
      const expected = new Set(['locations', 'schedule', 'contact', 'admission'])
      if (school.school_type !== 'international') {
        expected.add('academic')
      }
      if (school.school_type !== 'state') {
        expected.add('pricing')
      }

      const present = new Set()
      if ((school.locations || []).length > 0) {
        present.add('locations')
      }
      const hasSchedule = (school.locations || []).some(location => (location.age_group_shifts || []).some(item => item.shift)) ||
        Boolean(school.attributes?.school_hours || school.attributes?.daily_schedule?.length)
      if (hasSchedule) {
        present.add('schedule')
      }
      const hasContact = Boolean(
        school.website_url ||
        (school.locations || []).some(location => location.phone)
      )
      if (hasContact) {
        present.add('contact')
      }
      if ((school.exam_results || []).length > 0) {
        present.add('academic')
      }
      const hasAdmission = Boolean(
        (school.admission_info && Object.keys(school.admission_info).length > 0) ||
        school.attributes?.entry_requirements?.length
      )
      if (hasAdmission) {
        present.add('admission')
      }
      if ((school.pricing || []).length > 0) {
        present.add('pricing')
      }

      const total = expected.size
      const presentExpected = [...expected].filter(category => present.has(category))
      const score = presentExpected.length
      const ratio = total > 0 ? score / total : 0

      const presentLabels = presentExpected.map(category => t(SOURCE_CATEGORY_LABELS[category] || 'compare.sections.sources'))
      const missingLabels = [...expected]
        .filter(category => !present.has(category))
        .map(category => t(SOURCE_CATEGORY_LABELS[category] || 'compare.sections.sources'))

      map.set(school.id, { score, total, ratio, presentLabels, missingLabels })
    })
    return map
  }, [schools, t])

  // Keep the sticky headers aligned when columns change (removal, sort) and the body clamps.
  useLayoutEffect(() => {
    syncHeaderScroll(desktopBodyRef.current, desktopHeaderRef.current)
    handleMobileScroll()
  })

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

  if (error) {
    return (
      <Layout>
        <div className="max-w-4xl mx-auto px-6 py-12">
          <div className="text-center">
            <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-red-100 flex items-center justify-center">
              <svg className="w-8 h-8 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-neutral-900 mb-2">{t('common.error')}</h2>
            <p className="text-neutral-600 mb-6">{error}</p>
            <div className="flex flex-wrap justify-center gap-3">
              <button
                type="button"
                onClick={handleBack}
                className="px-6 py-3 bg-primary-700 text-white rounded-lg hover:bg-primary-800 transition-colors"
              >
                {t('common.goBack')}
              </button>
              {/* Recovery when every stored school is gone (the API answers 404). */}
              <button
                type="button"
                onClick={handleClear}
                className="px-6 py-3 text-red-600 border border-red-100 rounded-lg hover:bg-red-50 transition-colors"
              >
                {t('compare.clearAll')}
              </button>
            </div>
          </div>
        </div>
      </Layout>
    )
  }

  const columnCount = sortedSchools.length
  const isFull = ids.length >= maxCompare
  // Desktop: header and body are separate tables with the same fixed column widths, so the
  // header can stick to the viewport while the body scrolls sideways.
  const desktopTableStyle = { minWidth: `calc(14rem + ${columnCount} * 240px)` }
  const desktopColgroup = (
    <colgroup>
      <col style={{ width: '14rem' }} />
      {sortedSchools.map(school => <col key={school.id} />)}
    </colgroup>
  )
  // Mobile: two schools per screen width; labels span the visible width as row headings.
  const mobileColumns = Math.max(columnCount, 2)
  const mobileInnerStyle = { width: `${mobileColumns * 50}%` }
  const mobileGridStyle = { gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }
  const mobileLabelStyle = { width: `${200 / mobileColumns}%` }
  const mobileLastStart = Math.max(columnCount - 2, 0)
  const mobileFirstVisible = Math.min(mobileStart, mobileLastStart)

  return (
    <Layout>
      <div className="min-h-screen bg-neutral-50 pb-8">
        <div className="bg-white border-b border-neutral-200">
          <div className="max-w-7xl mx-auto px-4 py-3 md:px-6 md:py-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
              <div className="flex items-center gap-4">
                <button
                  type="button"
                  onClick={handleBack}
                  className="flex items-center gap-2 text-sm text-neutral-600 hover:text-neutral-900 transition-colors"
                >
                  <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
                  </svg>
                  {t('common.back')}
                </button>
                <div className="min-w-0">
                  <h1 className="text-xl md:text-2xl font-bold text-neutral-900">{t('compare.title')}</h1>
                  <p className="text-sm text-neutral-500">{t('compare.comparingCount', { count: columnCount })}</p>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2 md:gap-3">
                <label className="flex items-center gap-2 text-sm text-neutral-700">
                  <input
                    type="checkbox"
                    checked={highlightDiffs}
                    onChange={(event) => setHighlightDiffs(event.target.checked)}
                    className="h-4 w-4 rounded border-neutral-300 text-primary-600 focus:ring-primary-500"
                  />
                  {t('compare.highlightDifferences')}
                </label>
                <select
                  value={sortBy}
                  onChange={(event) => setSortBy(event.target.value)}
                  aria-label={t('sorting.sortBy')}
                  className="text-sm border border-neutral-200 rounded-md px-3 py-2 bg-white"
                >
                  <option value="name">{t('compare.sort.name')}</option>
                  <option value="distance" disabled={!userLocation}>{t('compare.sort.distance')}</option>
                  <option value="price">{t('compare.sort.price')}</option>
                  <option value="nvo">{t('compare.sort.nvo')}</option>
                </select>
                <button
                  type="button"
                  onClick={() => navigate(searchReturnPath())}
                  disabled={isFull}
                  title={isFull ? t('compare.maxReached', { max: maxCompare }) : undefined}
                  className="px-3 py-2 text-sm font-medium text-primary-600 border border-primary-600 rounded-md hover:bg-primary-50 transition-colors disabled:cursor-not-allowed disabled:border-neutral-200 disabled:text-neutral-400 disabled:hover:bg-transparent"
                >
                  {t('compare.addSchool')}
                </button>
                <button
                  type="button"
                  onClick={handleShare}
                  className="px-3 py-2 text-sm font-medium text-neutral-700 border border-neutral-200 rounded-md hover:bg-neutral-50 transition-colors"
                >
                  {t('compare.share')}
                </button>
                <button
                  type="button"
                  onClick={handleClear}
                  className="px-3 py-2 text-sm font-medium text-red-600 border border-red-100 rounded-md hover:bg-red-50 transition-colors"
                >
                  {t('compare.clearAll')}
                </button>
                {isFull && <span className="text-xs text-neutral-500">{t('compare.maxReached', { max: maxCompare })}</span>}
                {shareStatus && <span className="text-xs text-neutral-500" aria-live="polite">{shareStatus}</span>}
              </div>
            </div>
          </div>
        </div>

        {missingCount > 0 && (
          <div className="max-w-7xl mx-auto px-4 pt-3 md:px-6">
            <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">{t('compare.someNotFound')}</p>
          </div>
        )}

        {/* Mobile (< md): stacked rows, two schools per view, header scrolls with the values. */}
        <div className="md:hidden pt-3">
          <div className="sticky top-16 z-20 border-y border-neutral-200 bg-white shadow-sm">
            {columnCount > 2 && (
              <div className="flex items-center justify-between px-3 pt-2 text-xs text-neutral-500">
                <button
                  type="button"
                  onClick={() => scrollMobileBy(-1)}
                  disabled={mobileFirstVisible === 0}
                  aria-label={t('compare.previousSchools')}
                  className="rounded-md border border-neutral-200 px-3 py-1 text-base leading-none text-neutral-700 disabled:opacity-30"
                >
                  <span aria-hidden="true">‹</span>
                </button>
                <span aria-live="polite">
                  {t('compare.visibleRange', {
                    from: mobileFirstVisible + 1,
                    to: Math.min(mobileFirstVisible + 2, columnCount),
                    total: columnCount,
                  })}
                </span>
                <button
                  type="button"
                  onClick={() => scrollMobileBy(1)}
                  disabled={mobileFirstVisible >= mobileLastStart}
                  aria-label={t('compare.nextSchools')}
                  className="rounded-md border border-neutral-200 px-3 py-1 text-base leading-none text-neutral-700 disabled:opacity-30"
                >
                  <span aria-hidden="true">›</span>
                </button>
              </div>
            )}
            <div ref={mobileHeaderRef} className="overflow-hidden">
              <div className="grid" style={{ ...mobileInnerStyle, ...mobileGridStyle }}>
                {sortedSchools.map((school, idx) => (
                  <div key={school.id} className={`min-w-0 p-3 ${idx > 0 ? 'border-l border-neutral-200' : ''}`}>
                    <SchoolHeader school={school} onRemove={handleRemove} t={t} language={i18n.language} compact />
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div
            ref={mobileBodyRef}
            onScroll={handleMobileScroll}
            className="overflow-x-auto overscroll-x-contain snap-x snap-mandatory bg-white"
          >
            <div style={mobileInnerStyle}>
              {visibleSections.map(section => (
                <section key={section.key}>
                  <h2
                    className="sticky left-0 bg-primary-700 px-3 py-2 text-sm font-semibold text-white"
                    style={mobileLabelStyle}
                  >
                    {section.title}
                  </h2>
                  {section.rows.map(row => (
                    <MobileCompareRow
                      key={`${section.key}-${row.label}`}
                      label={row.label}
                      highlightDiffs={highlightDiffs}
                      labelStyle={mobileLabelStyle}
                      gridStyle={mobileGridStyle}
                      cells={sortedSchools.map(school => ({
                        content: row.getValue(school),
                        compareValue: row.getCompare ? row.getCompare(school) : row.getValue(school),
                      }))}
                    />
                  ))}
                </section>
              ))}
              <div
                className="sticky left-0 flex items-center justify-between gap-3 bg-neutral-100 px-3 py-2 text-sm font-semibold text-neutral-700"
                style={mobileLabelStyle}
              >
                <span>{t('compare.sections.sources')}</span>
                <SourcesToggle open={sourcesOpen} onToggle={() => setSourcesOpen(prev => !prev)} t={t} />
              </div>
              {sourcesOpen && (
                <div className="grid border-b border-neutral-200" style={mobileGridStyle}>
                  {sortedSchools.map((school, idx) => (
                    <div key={school.id} className={`min-w-0 break-words p-3 ${idx > 0 ? 'border-l border-neutral-200' : ''}`}>
                      <SourcesPanel
                        school={school}
                        t={t}
                        locale={i18n.language}
                        completeness={completenessById.get(school.id)}
                        open={sourcesOpen}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Desktop (md+): table with a sticky school header. */}
        <div className="hidden md:block max-w-7xl mx-auto px-6 py-6">
          <div
            ref={desktopHeaderRef}
            className="sticky top-16 z-20 overflow-hidden bg-neutral-50 shadow-[0_6px_8px_-6px_rgba(0,0,0,0.2)]"
          >
            <table className="w-full table-fixed border-collapse" style={desktopTableStyle}>
              {desktopColgroup}
              <thead>
                <tr>
                  <th className="sticky left-0 z-10 bg-neutral-50 p-3" aria-hidden="true"></th>
                  {sortedSchools.map(school => (
                    <th key={school.id} className="bg-white p-3 border border-neutral-200 align-top text-left font-normal">
                      <SchoolHeader school={school} onRemove={handleRemove} t={t} language={i18n.language} />
                    </th>
                  ))}
                </tr>
              </thead>
            </table>
          </div>
          <div
            ref={desktopBodyRef}
            onScroll={() => syncHeaderScroll(desktopBodyRef.current, desktopHeaderRef.current)}
            className="overflow-x-auto"
          >
            <table className="w-full table-fixed border-collapse" style={desktopTableStyle}>
              {desktopColgroup}
              <tbody>
                {visibleSections.map((section) => (
                  <FragmentSection
                    key={section.key}
                    section={section}
                    schools={sortedSchools}
                    highlightDiffs={highlightDiffs}
                  />
                ))}

                <SourcesSectionRow
                  label={t('compare.sections.sources')}
                  colSpan={columnCount + 1}
                  open={sourcesOpen}
                  onToggle={() => setSourcesOpen(prev => !prev)}
                  t={t}
                />
                {sourcesOpen && (
                  <tr>
                    <td className="sticky left-0 z-10 bg-neutral-100 p-4 font-semibold text-neutral-900 border border-neutral-200">
                      {t('compare.labels.sources')}
                    </td>
                    {sortedSchools.map((school) => (
                      <td key={school.id} className="bg-white p-4 border border-neutral-200 align-top break-words">
                        <SourcesPanel
                          school={school}
                          t={t}
                          locale={i18n.language}
                          completeness={completenessById.get(school.id)}
                          open={sourcesOpen}
                        />
                      </td>
                    ))}
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Layout>
  )
}

function SchoolHeader({ school, onRemove, t, language, compact = false }) {
  const { config: countryConfig } = useCountry()
  const name = getSchoolName(school, language)
  return (
    <div className={compact ? 'space-y-1.5' : 'space-y-2'}>
      <div className="flex items-start justify-between gap-1">
        <Link
          to={`/schools/${school.id}`}
          title={name}
          className={`min-w-0 font-bold text-neutral-900 hover:text-primary-700 hover:underline line-clamp-2 ${
            compact ? 'text-sm leading-snug' : 'text-base leading-snug'
          }`}
        >
          {name}
        </Link>
        <button
          type="button"
          onClick={() => onRemove(school.id)}
          className="flex-shrink-0 -mr-1 -mt-1 p-1.5 hover:bg-neutral-100 rounded transition-colors"
          aria-label={`${t('compare.remove')}: ${name}`}
        >
          <svg className="w-4 h-4 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
          school.school_type === 'state'
            ? 'bg-primary-50 text-primary-700'
            : 'bg-violet-50 text-violet-700'
        }`}>
          {t(`schoolTypes.${school.school_type}`)}
        </span>
        {!compact && school.education_level && (
          <span className="px-2 py-0.5 rounded text-xs bg-neutral-100 text-neutral-700">
            {schoolLevelLabel(school, t, countryConfig)}
          </span>
        )}
      </div>
      {!compact && (
        <Link
          to={`/schools/${school.id}`}
          className="inline-block text-xs font-medium text-primary-600 hover:text-primary-700 hover:underline"
        >
          {t('schools.viewDetails')}
        </Link>
      )}
    </div>
  )
}

function getDiffClasses(cells, highlightDiffs) {
  if (!highlightDiffs) return cells.map(() => 'bg-white')
  const normalized = cells.map(cell => normalizeCompareValue(cell.compareValue))
  const allSame = normalized.every(value => value === normalized[0])
  return cells.map(() => (allSame ? 'bg-white opacity-50' : 'bg-primary-50'))
}

function renderCellContent(content) {
  return typeof content === 'string'
    ? <p className="text-sm text-neutral-700">{content || '—'}</p>
    : content
}

function MobileCompareRow({ label, cells, highlightDiffs, labelStyle, gridStyle }) {
  const diffClasses = getDiffClasses(cells, highlightDiffs)
  return (
    <div className="border-b border-neutral-200">
      <h3
        className="sticky left-0 bg-neutral-100 px-3 py-1.5 text-xs font-semibold text-neutral-700"
        style={labelStyle}
      >
        {label}
      </h3>
      <div className="grid" style={gridStyle}>
        {cells.map((cell, idx) => (
          <div
            key={idx}
            className={`min-w-0 snap-start break-words px-3 py-2.5 ${idx > 0 ? 'border-l border-neutral-200' : ''} ${diffClasses[idx]}`}
          >
            {renderCellContent(cell.content)}
          </div>
        ))}
      </div>
    </div>
  )
}

function FragmentSection({ section, schools, highlightDiffs }) {
  return (
    <>
      <SectionRow label={section.title} colSpan={schools.length + 1} />
      {section.rows.map((row) => (
        <CompareRow
          key={`${section.key}-${row.label}`}
          label={row.label}
          highlightDiffs={highlightDiffs}
          cells={schools.map((school) => ({
            content: row.getValue(school),
            compareValue: row.getCompare ? row.getCompare(school) : row.getValue(school),
          }))}
        />
      ))}
    </>
  )
}

function SectionRow({ label, colSpan, tone = 'primary' }) {
  const classes = tone === 'muted'
    ? 'bg-neutral-100 text-neutral-700 border-neutral-200'
    : 'bg-primary-700 text-white border-primary-700'

  return (
    <tr>
      <th
        colSpan={colSpan}
        className={`text-left text-sm font-semibold px-4 py-2 border ${classes}`}
      >
        {label}
      </th>
    </tr>
  )
}

function SourcesSectionRow({ label, colSpan, open, onToggle, t }) {
  return (
    <tr>
      <th
        colSpan={colSpan}
        className="bg-neutral-100 text-neutral-700 text-left text-sm font-semibold px-4 py-2 border border-neutral-200"
      >
        <div className="flex items-center justify-between gap-3">
          <span>{label}</span>
          <SourcesToggle open={open} onToggle={onToggle} t={t} />
        </div>
      </th>
    </tr>
  )
}

function SourcesToggle({ open, onToggle, t }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="inline-flex items-center gap-2 text-xs font-medium text-neutral-600 hover:text-neutral-800"
    >
      <span>{open ? t('compare.hideSources') : t('compare.showSources')}</span>
      <svg
        className={`h-3 w-3 transition-transform ${open ? 'rotate-180' : ''}`}
        viewBox="0 0 20 20"
        fill="currentColor"
        aria-hidden="true"
      >
        <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.94a.75.75 0 111.08 1.04l-4.24 4.5a.75.75 0 01-1.08 0l-4.24-4.5a.75.75 0 01.02-1.06z" clipRule="evenodd" />
      </svg>
    </button>
  )
}

function CompareRow({ label, cells, highlightDiffs }) {
  const diffClasses = getDiffClasses(cells, highlightDiffs)

  return (
    <tr>
      <td className="sticky left-0 z-10 bg-neutral-100 p-4 font-semibold text-neutral-900 border border-neutral-200">
        {label}
      </td>
      {cells.map((cell, idx) => (
        <td key={idx} className={`p-4 border border-neutral-200 align-top break-words ${diffClasses[idx]}`}>
          {renderCellContent(cell.content)}
        </td>
      ))}
    </tr>
  )
}

function SourcesPanel({ school, t, locale, completeness, open }) {
  const fieldSources = school.field_sources || []

  const presentText = completeness?.presentLabels?.length
    ? completeness.presentLabels.join(', ')
    : null
  const missingText = completeness?.missingLabels?.length
    ? completeness.missingLabels.join(', ')
    : null

  return (
    <div className="space-y-2">
      <div className="relative inline-flex items-center gap-2 text-xs font-medium text-neutral-600 group">
        <span>{t('compare.dataCompletenessShort', { score: completeness?.score ?? 0, total: completeness?.total ?? 0 })}</span>
        <span className="inline-flex h-4 w-4 items-center justify-center rounded-full border border-neutral-300 text-[10px] text-neutral-500">
          i
        </span>
        <div className="pointer-events-none absolute left-0 top-full z-20 mt-2 w-64 rounded-md bg-neutral-900 px-2 py-1.5 text-[11px] text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
          {presentText ? (
            <>
              <div>{t('compare.dataCompletenessHas')}: {presentText}</div>
              {missingText ? <div className="mt-1">{t('compare.dataCompletenessMissing')}: {missingText}</div> : null}
            </>
          ) : (
            <div>{t('compare.dataCompletenessTooltipEmpty')}</div>
          )}
        </div>
      </div>

      {open && fieldSources.length > 0 ? (
        <div className="space-y-3 text-sm text-neutral-700">
          <div>
            <div className="text-xs font-semibold uppercase text-neutral-500">
              {t(SOURCE_CATEGORY_LABELS.other)}
            </div>
            <ul className="mt-1 space-y-1">
              {fieldSources.map((item, index) => (
                <li key={getProvenanceSourceKey(item, index)}>
                  <InlineSource
                    badgeKey={item.source_type}
                    badgeLabel={t(`compare.sourceTypes.${item.source_type}`)}
                    url={item.source_url}
                    dateLabel={formatDate(item.last_verified, locale)}
                    t={t}
                  />
                </li>
              ))}
            </ul>
          </div>

          <div className="text-xs text-neutral-500">
            {t('compare.lastUpdated', { date: formatDate(school.updated_at, locale) || t('compare.notAvailable') })}
          </div>
        </div>
      ) : null}
    </div>
  )
}

export default ComparePage
