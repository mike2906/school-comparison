import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate, useSearchParams } from 'react-router-dom'
import Layout from '../Layout/Layout'
import { useCompare } from '../../context/CompareContext'
import { getLastSearchUrl } from '../../utils/searchViewState'
import { useCountry } from '../../context/CountryContext'
import { fetchCompare, fetchExamAverages } from '../../api/schools'
import { calculateDistance } from '../../utils/distance'
import { pinnedLocation } from '../../utils/coordinates'
import { compareSchoolNames, getSchoolName } from '../../utils/i18n'
import { hasDisplayEvidence, normalizeSchoolList } from '../../utils/schoolAttributes'
import { comparisonExamType, examGradeLabel, getAvailableExamTypes } from '../../utils/nvo'
import { statedTuition, tuitionSortValue } from '../../utils/pricing'
import { hasDisplayableAdmissionInfo } from '../../utils/admission'
import { readSavedLocation } from '../../utils/keyFacts'
import { defaultStage, yearCost } from '../../utils/yearCost'
import { planningYear } from '../../utils/ageSelection'
import { saveCostNeeds } from '../../admission/saved'
import { useSavedAdmission } from '../../hooks/useSavedAdmission'

import { buildCompareSections } from './compareSections'
import { buildYearCostSection } from './yearCostSection'
import {
  getNvoDetail,
  nvoNotApplicable,
  parseIds,
  SOURCE_CATEGORY_LABELS,
} from './compareHelpers.js'
import {
  FragmentSection,
  MobileCompareRow,
  SchoolHeader,
  SourcesPanel,
  SourcesSectionRow,
  SourcesToggle,
} from './CompareRows'


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
  const saved = useSavedAdmission()
  const defaultCostStage = defaultStage({
    birthYear: saved.child?.birthYear,
    startYear: saved.child?.startYear,
    ageGroup: selectedAgeGroup,
    planningYear: planningYear(),
  })
  const [chosenCostStage, setCostStage] = useState(null)
  const costStage = chosenCostStage || defaultCostStage
  const costNeeds = useMemo(() => saved.costNeeds || [], [saved.costNeeds])
  const toggleCostNeed = need => saveCostNeeds(costNeeds.includes(need) ? costNeeds.filter(item => item !== need) : [...costNeeds, need])

  useEffect(() => {
    setUserLocation(readSavedLocation())
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

  // One exam for every column: different exams' results are not comparable.
  const nvoExamType = useMemo(
    () => comparisonExamType(localizedSchools.filter(school => !nvoNotApplicable(school)), selectedAgeGroup),
    [localizedSchools, selectedAgeGroup]
  )
  const nvoGrade = examGradeLabel(nvoExamType || 'nvo_7', t)

  const metricsById = useMemo(() => {
    const map = new Map()
    localizedSchools.forEach((school) => {
      const distanceLocation = pinnedLocation(school.locations)
      const distance = userLocation && distanceLocation
        ? calculateDistance(userLocation.lat, userLocation.lng, distanceLocation.lat, distanceLocation.lng)
        : null
      const pricingRange = statedTuition(school.pricing, { ageGroup: selectedAgeGroup })
      // State schools charge no tuition, so they sort as the cheapest, matching "Безплатно".
      const priceSort = tuitionSortValue(school.pricing, { ageGroup: selectedAgeGroup }) ?? (school.school_type === 'state' ? 0 : null)
      const nvoDetail = nvoNotApplicable(school) ? null : getNvoDetail(school, t, nvoExamType)
      const overallLatest = nvoDetail?.latestCombined ?? null
      const overallAvg = nvoDetail?.schoolAverageCombined ?? null

      map.set(school.id, {
        distance,
        pricingRange,
        priceSort,
        nvoDetail,
        overallLatest,
        overallAvg,
      })
    })
    return map
  }, [localizedSchools, t, userLocation, nvoExamType, selectedAgeGroup])

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
        const priceA = metricsById.get(a.id)?.priceSort
        const priceB = metricsById.get(b.id)?.priceSort
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

  const sections = useMemo(() => buildCompareSections({
    t,
    language: i18n.language,
    metricsById,
    userLocation,
    schools,
    selectedAgeGroup,
    examAverages,
    nvoGrade,
    countryConfig,
  }), [t, i18n.language, metricsById, userLocation, schools, selectedAgeGroup, examAverages, nvoGrade, countryConfig])

  const costById = useMemo(
    () => new Map(schools.map(school => [school.id, yearCost(school.pricing, costStage)])),
    [schools, costStage]
  )

  // The yearly cost table goes before the fee list, when some school publishes fees.
  const allSections = useMemo(() => {
    if (!schools.some(school => school.school_type !== 'state' && (school.pricing || []).length > 0)) return sections
    const costSection = buildYearCostSection({
      t,
      language: i18n.language,
      schools,
      costById,
      stage: costStage,
      needs: costNeeds,
      onStageChange: setCostStage,
      onToggleNeed: toggleCostNeed,
    })
    const at = sections.findIndex(section => section.key === 'pricing')
    return at < 0 ? [...sections, costSection] : [...sections.slice(0, at), costSection, ...sections.slice(at)]
  }, [sections, schools, costById, costStage, costNeeds, t, i18n.language])

  const visibleSections = useMemo(() => allSections
    .map(section => ({
      ...section,
      rows: section.rows.filter(row => sortedSchools.some(school => (
        hasDisplayEvidence(row.getCompare?.(school))
      ))),
    }))
    .filter(section => section.rows.length > 0), [allSections, sortedSchools])

  const completenessById = useMemo(() => {
    const map = new Map()
    schools.forEach((school) => {
      const expected = new Set(['locations', 'schedule', 'contact', 'admission'])
      if (!nvoNotApplicable(school)) {
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
      // The academic section shows NVO only; ДЗИ rows alone would leave it empty.
      if (getAvailableExamTypes(school.exam_results || []).length > 0) {
        present.add('academic')
      }
      const hasAdmission = Boolean(
        hasDisplayableAdmissionInfo(school.admission_info) ||
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
                      key={`${section.key}-${row.key ?? row.label}`}
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

export default ComparePage
