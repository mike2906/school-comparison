import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import Layout from '../Layout/Layout'
import { fetchSchool } from '../../api/schools'
import { getSchoolName } from '../../utils/i18n'
import { RULE_SETS } from '../../admission/ruleSets'
import {
  defaultStartYear,
  ruleSetFor,
  sameRules,
  sofiaToday,
  stageById,
  STAGES,
  startYearChoices,
  summaryRowsFor,
} from '../../admission/calculator'
import { districtAt, districtByName, schoolCatchmentIds } from '../../admission/catchments'
import { loadDistricts } from '../../admission/loadCatchments'
import { getSaved } from '../../admission/saved'
import { getMappableLocations } from '../SchoolDetailPage/LocationMap'
import CutoffTable from '../Admission/CutoffTable'
import RuleSetNotice from './RuleSetNotice'
import KindergartenCalculator from './KindergartenCalculator'
import Grade1Calculator from './Grade1Calculator'
import Grade8Calculator from './Grade8Calculator'
import { Field, INPUT_CLASS } from './fields'

const CALCULATORS = {
  kindergarten: KindergartenCalculator,
  grade_1: Grade1Calculator,
  grade_8: Grade8Calculator,
}

/** The school named in `?school=`, if any. Only its id is in the URL, never an answer. */
function useSchool(id) {
  const [school, setSchool] = useState(null)
  useEffect(() => {
    if (!id) {
      setSchool(null)
      return undefined
    }
    let live = true
    fetchSchool(id).then(data => live && setSchool(data), () => live && setSchool(null))
    return () => {
      live = false
    }
  }, [id])
  return school
}

function useDistricts() {
  const [districts, setDistricts] = useState(null)
  useEffect(() => {
    let live = true
    loadDistricts().then(data => live && setDistricts(data), () => {})
    return () => {
      live = false
    }
  }, [])
  return districts
}

function schoolPoint(school) {
  const points = getMappableLocations(school?.locations)
  const point = points.find(p => p.location.is_primary) || points[0]
  return point ? [point.lat, point.lng] : null
}

/**
 * The admission calculator: kindergarten points, first-grade group and points, and the
 * eighth-grade score, from the rule files in src/admission/rules. Everything is
 * calculated in the browser; no answer is sent or put in the URL, and answers are stored
 * only when the parent saves them on this device (src/admission/saved.js).
 */
function AdmissionPage() {
  const { t, i18n } = useTranslation()
  const [params, setParams] = useSearchParams()
  const stage = stageById(params.get('stage'))
  const school = useSchool(params.get('school'))
  const districts = useDistricts()
  const today = sofiaToday()
  // Each tab opens on the year of the answers saved on this device, else on the campaign
  // a parent applying today is ranked under, until the parent picks another year.
  const savedYear = id => getSaved()[id]?.startYear ?? null
  const [chosenYear, setChosenYear] = useState(() => savedYear(stage.id))
  const startYear = chosenYear ?? defaultStartYear(new Date(), { ruleSets: RULE_SETS, stage, today })

  useEffect(() => {
    const previous = document.title
    document.title = `${t('admissionCalc.title')} · ${t('nav.title')}`
    return () => {
      document.title = previous
    }
  }, [t])

  const selected = useMemo(() => ruleSetFor(RULE_SETS, stage, startYear, today), [stage, startYear, today])

  const schoolContext = useMemo(() => {
    if (!school) return null
    const info = school.admission_info || {}
    const point = schoolPoint(school)
    const district = districts
      ? districtByName(districts, info.admin_district) || (point ? districtAt(districts, point[0], point[1]) : null)
      : null
    return {
      id: school.id,
      name: getSchoolName(school, i18n.language),
      catchmentIds: schoolCatchmentIds(school),
      districtCode: district?.properties.code ?? null,
      districtName: district?.properties.name ?? null,
      cutoffSummary: info.cutoff_summary || [],
    }
  }, [school, districts, i18n.language])

  const cutoffRows = schoolContext ? summaryRowsFor(schoolContext.cutoffSummary, stage) : []
  const Calculator = CALCULATORS[stage.id]

  // However the tab changes (a click, a link, Back), it opens on its own default year.
  useEffect(() => setChosenYear(savedYear(stage.id)), [stage.id])

  const chooseStage = id => {
    const next = new URLSearchParams(params)
    next.set('stage', id)
    setParams(next, { replace: true })
  }
  // From the first-grade note: keep the school only when it has a preparatory group,
  // otherwise the kindergarten tab would read as if it had one.
  const openPreparatory = () => {
    const next = new URLSearchParams(params)
    next.set('stage', 'kindergarten')
    if (!schoolContext?.cutoffSummary.some(row => row.stage === 'preparatory')) next.delete('school')
    setParams(next, { replace: true })
  }
  const clearSchool = () => {
    const next = new URLSearchParams(params)
    next.delete('school')
    setParams(next, { replace: true })
  }

  return (
    <Layout>
      <div className="mx-auto max-w-5xl px-4 py-8 md:px-6 md:py-10">
        <h1 className="text-3xl font-bold text-neutral-900">{t('admissionCalc.title')}</h1>
        <p className="mt-2 max-w-3xl text-neutral-700">{t('admissionCalc.intro')}</p>
        <p className="mt-2 max-w-3xl text-sm text-neutral-600">{t('admissionCalc.privacy')}</p>

        <div className="mt-6 flex flex-wrap gap-2" role="tablist">
          {STAGES.map(option => (
            <button
              key={option.id}
              type="button"
              role="tab"
              aria-selected={option.id === stage.id}
              onClick={() => chooseStage(option.id)}
              className={`whitespace-nowrap rounded-lg px-4 py-2 text-sm font-medium ${
                option.id === stage.id ? 'bg-primary-700 text-white' : 'bg-white text-neutral-700 border border-neutral-200 hover:bg-neutral-50'
              }`}
            >
              {t(`admissionCalc.stages.${option.id}`)}
            </button>
          ))}
        </div>
        {stage.id === 'grade_1' && (
          <p className="mt-3 rounded-lg bg-neutral-50 p-3 text-sm text-neutral-700">
            {t('admissionCalc.preparatoryNote')}{' '}
            <button type="button" onClick={openPreparatory} className="font-medium text-primary-700 underline">
              {t('admissionCalc.preparatoryLink')}
            </button>
          </p>
        )}
        <p className="mt-2 text-xs text-neutral-500">{t('admissionCalc.grade5Note')}</p>

        <div className="mt-6 flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <Field label={t('admissionCalc.startYear')}>
            {id => (
              <select
                id={id}
                value={startYear}
                onChange={event => setChosenYear(Number(event.target.value))}
                className={INPUT_CLASS}
              >
                {startYearChoices().map(year => (
                  <option key={year} value={year}>{t('admissionCalc.startYearOption', { year, next: year + 1 })}</option>
                ))}
              </select>
            )}
          </Field>
          {schoolContext && (
            <div className="text-sm text-neutral-700">
              {t('admissionCalc.forSchool')}{' '}
              <Link to={`/schools/${schoolContext.id}`} className="font-medium text-primary-700 underline">{schoolContext.name}</Link>
              {' · '}
              <button type="button" onClick={clearSchool} className="text-neutral-600 underline">{t('admissionCalc.anySchool')}</button>
            </div>
          )}
        </div>

        {selected ? (
          <>
            <div className="mt-4">
              <RuleSetNotice selected={selected} today={today} />
            </div>
            <div className="mt-6">
              <Calculator
                key={stage.id}
                selected={selected}
                startYear={startYear}
                school={schoolContext}
                kindergarten={schoolContext}
                districts={districts}
                cutoffRows={cutoffRows}
                isCurrentRules={row => sameRules(row.rule_set, selected?.ruleSet)}
              />
            </div>
          </>
        ) : (
          <p className="mt-6 rounded-xl border border-neutral-200 bg-white p-4 text-sm text-neutral-700">{t('admissionCalc.noRules')}</p>
        )}

        {schoolContext && (
          <section className="mt-8 rounded-2xl border border-neutral-200 bg-white p-5 md:p-6">
            <h2 className="text-lg font-semibold text-neutral-900">{t('cutoffs.title')}</h2>
            <p className="mt-1 text-sm text-neutral-600">{t('cutoffs.intro')}</p>
            <div className="mt-4">
              {cutoffRows.length > 0
                ? <CutoffTable rows={cutoffRows} isCurrentRules={row => sameRules(row.rule_set, selected?.ruleSet)} />
                : <p className="text-sm text-neutral-600">{t('cutoffs.none')}</p>}
            </div>
          </section>
        )}

        <p className="mt-8 text-xs text-neutral-500">{t('admissionCalc.disclaimer')}</p>
      </div>
    </Layout>
  )
}

export default AdmissionPage
