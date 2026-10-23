import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import Layout from '../Layout/Layout'
import SchoolCard from '../SchoolCard/SchoolCard'
import SchoolCardSkeleton from '../SchoolCard/SchoolCardSkeleton'
import DriveTimeNote from '../../routing/DriveTimeNote'
import { useDriveTimes } from '../../routing/useDriveTimes'
import { useCountry } from '../../context/CountryContext'
import { useSchools } from '../../hooks/useSchools'
import { useSavedAdmission, useVerdictData } from '../../hooks/useSavedAdmission'
import { useFilterData } from '../SearchPage/useSearchFilters'
import {
  forgetSaved,
  hasSaved,
  saveChild,
  saveHomePoint,
  savePreferences,
} from '../../admission/saved'
import { CHANCE_GROUPS } from '../../admission/chanceGroup'
import { savedVerdictsFor } from '../../admission/schoolVerdict'
import { sofiaToday } from '../../admission/calculator'
import { buildOptions, calculatorStage, entryFits } from '../../utils/childOptions'
import { birthYearsFor, enrolmentYears, planningYear, toSearchSelection } from '../../utils/ageSelection'
import { calculateAgeGroup } from '../../utils/education'
import { calculateDistance } from '../../utils/distance'
import { pinnedLocation } from '../../utils/coordinates'
import { getAgeGroupLabel } from '../../utils/countryConfig'
import { geocodeAddress } from '../../utils/geocoding'
import { languageKey, languageLabel } from '../../utils/languages'
import { schoolPath } from '../../utils/schoolPath'

const EMPTY_LIST = []
// Shown first; the rest behind "more languages". The most common foreign languages in Sofia schools.
const MAIN_LANGUAGES = ['english', 'german', 'french', 'spanish', 'italian', 'russian']
// Cards per group before "show more": a whole age group is hundreds of cards.
const PAGE = 20

function locationsForAgeGroup(school, ageGroup) {
  const all = school.locations || []
  const matching = all.filter(location => (
    Array.isArray(location.age_groups) ? location.age_groups.includes(ageGroup) : location.age_group === ageGroup
  ))
  return matching.length ? matching : all
}

/**
 * "My child's options" (docs/MY_CHILD_OPTIONS.md): the child's birth year, start year,
 * home, languages and budget, given once and shared with the calculator and the list, and
 * every school for that start grouped by chance, the shortest trip first. Nothing is kept
 * until the parent saves on this device.
 */
function MyChildPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { config, countryCode } = useCountry()
  const saved = useSavedAdmission()
  const isSaved = hasSaved(saved)

  // The page's own copy: the saved profile when there is one, kept in step with it.
  const [draft, setDraft] = useState(() => draftFrom(saved))
  useEffect(() => {
    if (isSaved) setDraft(draftFrom(saved))
  }, [saved, isSaved])
  const update = changes => {
    const next = { ...draft, ...changes }
    setDraft(next)
    if (isSaved) persist(next)
  }

  const { birthYear, startYear, homePoint, languages, monthlyBudget } = draft
  const ageGroup = birthYear ? calculateAgeGroup(startYear, birthYear, config) : null
  const selection = toSearchSelection({ ageGroup, where: 'both' })
  const { availableFilters } = useFilterData(countryCode)
  const languageOptions = useMemo(() => (
    [...new Set((availableFilters.language_focus_languages || []).map(languageKey).filter(Boolean))]
  ), [availableFilters])
  // A language counts at any level, as the list's language filter does.
  const languagePairs = useMemo(() => (availableFilters.language_focus_pairs || [])
    .filter(pair => languages.includes(languageKey(pair.split(':')[0]))), [availableFilters, languages])

  const all = useSchools(ageGroup, null, selection.educationLevel, selection.includeCrossover, EMPTY_LIST, EMPTY_LIST, EMPTY_LIST, EMPTY_LIST, countryCode)
  const withLanguage = useSchools(ageGroup, null, selection.educationLevel, selection.includeCrossover, languagePairs, EMPTY_LIST, EMPTY_LIST, EMPTY_LIST, countryCode)
  const filtering = languagePairs.length > 0
  const { schools, loading, error } = filtering ? withLanguage : all
  const hiddenByLanguage = filtering && !all.loading && !withLanguage.loading ? all.schools.length - withLanguage.schools.length : 0

  const placed = useMemo(() => schools.map(school => {
    const distanceLocation = pinnedLocation(locationsForAgeGroup(school, ageGroup))
    const distance = homePoint && distanceLocation
      ? calculateDistance(homePoint.lat, homePoint.lng, distanceLocation.lat, distanceLocation.lng)
      : null
    return { ...school, distance, distanceLocation }
  }), [schools, homePoint, ageGroup])
  const driveTimes = useDriveTimes(homePoint, placed)
  const withTrips = useMemo(() => (driveTimes.seconds.size === 0
    ? placed
    : placed.map(school => ({ ...school, driveSeconds: driveTimes.seconds.get(school.id) ?? null }))), [placed, driveTimes.seconds])

  // Saved calculator answers count only when they are about this child and September.
  const stage = birthYear ? calculatorStage(ageGroup, birthYear, startYear) : null
  const entry = stage ? saved[stage] : null
  const fits = entryFits(stage, entry, birthYear, startYear)
  const verdictSaved = useMemo(() => (fits ? { ...saved, [stage === 'kindergarten' ? 'grade_1' : 'kindergarten']: null } : null), [fits, saved, stage])
  const verdictData = useVerdictData(Boolean(fits && stage === 'kindergarten'), Boolean(fits && stage === 'grade_1'))
  const [showOverBudget, setShowOverBudget] = useState(false)
  const options = useMemo(() => buildOptions(withTrips, {
    verdictsFor: school => (verdictSaved && verdictData ? savedVerdictsFor(school, verdictSaved, ageGroup, { ...verdictData, today: sofiaToday() }) : []),
    monthlyBudget,
    ageGroup,
    showOverBudget,
  }), [withTrips, verdictSaved, verdictData, ageGroup, monthlyBudget, showOverBudget])
  const [openBelow, setOpenBelow] = useState(false)
  const [shown, setShown] = useState({})
  // A saved profile opens as one summary line; the form is a tap away.
  const [editing, setEditing] = useState(() => !(isSaved && saved.child))
  const [allLanguages, setAllLanguages] = useState(false)
  const visibleLanguages = allLanguages
    ? languageOptions
    : languageOptions.filter(key => MAIN_LANGUAGES.includes(key) || languages.includes(key))

  useEffect(() => {
    const previous = document.title
    document.title = `${t('myChild.title')} · ${t('nav.title')}`
    return () => {
      document.title = previous
    }
  }, [t])

  const groupLabel = group => (config ? getAgeGroupLabel(config, group, i18n.language) : t(`ageGroups.${group}`))
  const searchUrl = `/search?${new URLSearchParams({
    target_year: String(startYear),
    ...(birthYear ? { birth_year: String(birthYear) } : {}),
    ...(ageGroup ? { age_group: ageGroup, include_crossover: String(selection.includeCrossover) } : {}),
  })}`

  return (
    <Layout>
      <div className="mx-auto max-w-3xl px-4 py-6">
        <h1 className="text-2xl font-semibold text-neutral-900">{t('myChild.title')}</h1>
        <p className="mt-1 text-sm text-neutral-600">{t('myChild.intro')}</p>

        {!editing ? (
          <section className="mt-5 rounded-xl border border-neutral-200 bg-white p-4 text-sm text-neutral-800" aria-label={t('myChild.profile')}>
            <p>
              {[
                t('myChild.summaryChild', { birthYear, startYear }),
                ageGroup && groupLabel(ageGroup),
                homePoint ? `${t('location.home')}: ${homePoint.label || t('location.home')}` : t('myChild.summaryNoHome'),
                languages.length > 0 && languages.map(key => languageLabel(key, t)).join(', '),
                monthlyBudget && t('myChild.summaryBudget', { amount: monthlyBudget }),
              ].filter(Boolean).join(' · ')}
            </p>
            <button type="button" onClick={() => setEditing(true)} className="mt-2 font-medium text-primary-700 underline">{t('myChild.changeProfile')}</button>
          </section>
        ) : (
        <section className="mt-5 space-y-4 rounded-xl border border-neutral-200 bg-white p-4" aria-label={t('myChild.profile')}>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-neutral-800">{t('filters.birthYear')}</span>
              <select
                id="my-child-birth-year"
                value={birthYear ?? ''}
                onChange={event => update({ birthYear: event.target.value ? Number(event.target.value) : null })}
                className="h-11 w-full rounded-lg border border-neutral-300 bg-white px-2"
              >
                <option value="">{t('myChild.choose')}</option>
                {birthYearsFor(startYear, 19).filter(year => year < startYear).map(year => <option key={year} value={year}>{year}</option>)}
              </select>
            </label>
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-neutral-800">{t('myChild.startYear')}</span>
              <select
                id="my-child-start-year"
                value={startYear}
                onChange={event => update({ startYear: Number(event.target.value) })}
                className="h-11 w-full rounded-lg border border-neutral-300 bg-white px-2"
              >
                {enrolmentYears().map(year => <option key={year} value={year}>{t('admissionCalc.startYearOption', { year, next: year + 1 })}</option>)}
              </select>
            </label>
          </div>
          {ageGroup && <p className="text-sm text-neutral-700">{t('myChild.group', { group: groupLabel(ageGroup) })}</p>}

          <HomeField homePoint={homePoint} onChange={point => update({ homePoint: point })} />

          <fieldset>
            <legend className="mb-1 text-sm font-medium text-neutral-800">{t('myChild.languages')}</legend>
            <div className="flex flex-wrap gap-2">
              {visibleLanguages.map(key => {
                const on = languages.includes(key)
                return (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={on}
                    onClick={() => update({ languages: on ? languages.filter(other => other !== key) : [...languages, key] })}
                    className={`h-9 rounded-full border px-3 text-sm ${on ? 'border-primary-600 bg-primary-50 text-primary-800' : 'border-neutral-300 text-neutral-700'}`}
                  >
                    {languageLabel(key, t)}
                  </button>
                )
              })}
              {!allLanguages && languageOptions.length > visibleLanguages.length && (
                <button type="button" onClick={() => setAllLanguages(true)} className="h-9 px-1 text-sm text-primary-700 underline">
                  {t('myChild.moreLanguages')}
                </button>
              )}
            </div>
            <p className="mt-1 text-xs text-neutral-500">{t('myChild.languagesNote')}</p>
          </fieldset>

          <label className="block text-sm">
            <span className="mb-1 block font-medium text-neutral-800">{t('myChild.budget')}</span>
            <input
              id="my-child-budget"
              type="number"
              inputMode="numeric"
              min="0"
              step="50"
              value={monthlyBudget ?? ''}
              placeholder={t('myChild.noLimit')}
              onChange={event => update({ monthlyBudget: Number(event.target.value) > 0 ? Number(event.target.value) : null })}
              className="h-11 w-full rounded-lg border border-neutral-300 px-2 sm:w-48"
            />
            <span className="mt-1 block text-xs text-neutral-500">{t('myChild.budgetNote')}</span>
          </label>

          <div className="border-t border-neutral-100 pt-3 text-sm">
            {isSaved ? (
              <p className="text-neutral-700">
                {t('myChild.saved')}{' '}
                {birthYear && <button type="button" onClick={() => setEditing(false)} className="mr-3 font-medium text-primary-700 underline">{t('myChild.done')}</button>}
                <button type="button" onClick={() => { forgetSaved(); setDraft(draftFrom({})) }} className="text-neutral-600 underline">{t('myChild.forget')}</button>
              </p>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => {
                    persist(draft)
                    setEditing(false)
                  }}
                  disabled={!birthYear}
                  className="w-full rounded-lg bg-primary-700 px-4 py-2 font-medium text-white hover:bg-primary-800 disabled:opacity-60 sm:w-auto"
                >
                  {t('myChild.save')}
                </button>
                <p className="mt-1 text-xs text-neutral-500">{t('myChild.saveNote')}</p>
              </>
            )}
          </div>
        </section>
        )}

        {!birthYear ? (
          <p className="mt-6 text-neutral-700">{t('myChild.needBirthYear')}</p>
        ) : (
          <section className="mt-6" aria-label={t('myChild.results')}>
            {stage && !fits && (
              <p className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-900">
                {t(entry ? 'myChild.answersOther' : 'myChild.answersMissing')}{' '}
                <Link to={`/admission?stage=${stage}`} className="font-medium underline">{t('myChild.answersLink')}</Link>
              </p>
            )}
            {!stage && <p className="mb-4 text-sm text-neutral-600">{t('myChild.noCalculator')}</p>}
            {!homePoint && <p className="mb-4 text-sm text-neutral-600">{t('myChild.noHome')}</p>}
            {homePoint && <DriveTimeNote status={driveTimes.status} />}

            <p className="mb-3 text-sm text-neutral-600">
              {hiddenByLanguage > 0 && <>{t('myChild.hiddenLanguage', { count: hiddenByLanguage })}{' '}</>}
              {options.overBudget > 0 && (
                <>
                  {t(showOverBudget ? 'myChild.overBudgetShown' : 'myChild.overBudget', { count: options.overBudget })}{' '}
                  <button type="button" className="underline" onClick={() => setShowOverBudget(!showOverBudget)}>
                    {t(showOverBudget ? 'myChild.hideThem' : 'myChild.showThem')}
                  </button>{' '}
                </>
              )}
              <Link to={searchUrl} className="font-medium text-primary-700 underline">{t('myChild.seeOnMap')}</Link>
            </p>

            {error && <p className="text-red-700">{t('common.error')}</p>}
            {loading ? (
              <div className="space-y-3">{[0, 1, 2].map(i => <SchoolCardSkeleton key={i} />)}</div>
            ) : (
              CHANCE_GROUPS.map(group => {
                const list = options.groups[group]
                if (list.length === 0) return null
                const collapsed = group === 'below' && !openBelow
                return (
                  <div key={group} className="mb-6" data-chance-group={group}>
                    <h2 className="text-lg font-semibold text-neutral-900">
                      {t(`myChild.group_${group}`)} <span className="text-sm font-normal text-neutral-500">({list.length})</span>
                    </h2>
                    <p className="mb-2 text-sm text-neutral-600">{t(`myChild.groupNote_${group}`)}</p>
                    {collapsed ? (
                      <button type="button" className="text-sm font-medium text-primary-700 underline" onClick={() => setOpenBelow(true)}>
                        {t('myChild.showThem')}
                      </button>
                    ) : (
                      <div className="space-y-3">
                        {list.slice(0, shown[group] || PAGE).map(school => (
                          <SchoolCard
                            key={school.id}
                            school={school}
                            location={locationsForAgeGroup(school, ageGroup).find(l => l.is_primary) || locationsForAgeGroup(school, ageGroup)[0]}
                            activeAgeGroup={ageGroup}
                            targetYear={startYear}
                            onClick={() => navigate(schoolPath(school.id, ageGroup, startYear))}
                          />
                        ))}
                        {list.length > (shown[group] || PAGE) && (
                          <button
                            type="button"
                            onClick={() => setShown({ ...shown, [group]: (shown[group] || PAGE) + PAGE })}
                            className="h-11 w-full rounded-lg border border-neutral-300 text-sm font-medium text-neutral-800"
                          >
                            {t('myChild.showMore', { count: Math.min(PAGE, list.length - (shown[group] || PAGE)) })}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </section>
        )}
      </div>
    </Layout>
  )
}

function draftFrom(saved) {
  return {
    birthYear: saved.child?.birthYear ?? null,
    startYear: saved.child?.startYear ?? planningYear(),
    homePoint: saved.homePoint ?? null,
    languages: saved.preferences?.languages ?? [],
    monthlyBudget: saved.preferences?.monthlyBudget ?? null,
  }
}

function persist(draft) {
  if (draft.birthYear) saveChild({ birthYear: draft.birthYear, startYear: draft.startYear })
  saveHomePoint(draft.homePoint)
  savePreferences({ languages: draft.languages, monthlyBudget: draft.monthlyBudget })
}

/**
 * The home: shown when set; else an address to look up on OpenStreetMap (only when the
 * parent presses search, as on the list) or the device's location.
 */
function HomeField({ homePoint, onChange }) {
  const { t } = useTranslation()
  const { config } = useCountry()
  const [text, setText] = useState('')
  const [state, setState] = useState('idle')
  const geocodingConfig = { ...config?.map_config?.geocoding, bounds: config?.map_config?.bounds }

  if (homePoint) {
    return (
      <p className="text-sm text-neutral-800">
        <span className="font-medium">{t('location.home')}:</span> {homePoint.label || t('location.home')}{' '}
        <button type="button" onClick={() => onChange(null)} className="text-neutral-600 underline">{t('myChild.changeHome')}</button>
      </p>
    )
  }
  const search = async event => {
    event.preventDefault()
    if (!text.trim()) return
    setState('looking')
    try {
      const result = await geocodeAddress(text.trim(), geocodingConfig)
      onChange({ lat: result.lat, lng: result.lng, label: result.address })
      setState('idle')
    } catch {
      setState('notFound')
    }
  }
  const locate = () => {
    if (!navigator.geolocation) return setState('notFound')
    setState('looking')
    navigator.geolocation.getCurrentPosition(
      position => {
        onChange({ lat: position.coords.latitude, lng: position.coords.longitude, label: null })
        setState('idle')
      },
      () => setState('notFound'),
      { enableHighAccuracy: true, timeout: 10000 }
    )
  }
  return (
    <div className="text-sm">
      <span className="mb-1 block font-medium text-neutral-800">{t('location.home')}</span>
      <form className="flex gap-2" onSubmit={search}>
        <input
          id="my-child-address"
          value={text}
          onChange={event => setText(event.target.value)}
          placeholder={t('location.addressPlaceholder')}
          className="h-11 min-w-0 flex-1 rounded-lg border border-neutral-300 px-2"
        />
        <button type="submit" disabled={state === 'looking'} className="h-11 rounded-lg border border-neutral-300 px-3 font-medium">{t('common.search')}</button>
      </form>
      <button type="button" onClick={locate} className="mt-2 text-primary-700 underline">{t('location.useMyLocation')}</button>
      {state === 'notFound' && <p className="mt-1 text-red-700">{t('location.errorNotFound')}</p>}
      <p className="mt-1 text-xs text-neutral-500">{t('myChild.homeNote')}</p>
    </div>
  )
}

export default MyChildPage
