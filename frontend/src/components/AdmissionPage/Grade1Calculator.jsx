import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { EVALUATORS } from '../../admission/evaluators'
import { formatRange, measuredOnFor } from '../../admission/calculator'
import { addressFactsForSchool } from '../../admission/catchments'
import { loadCatchments } from '../../admission/loadCatchments'
import AddressCheck from '../Admission/AddressCheck'
import CutoffComparison from '../Admission/CutoffComparison'
import { DateField, MobileTotal, Section, YesNo } from './fields'
import PointsList from './PointsList'

const EMPTY_ADDRESS = { inCatchment: null, inNeighbouring: null, since: null }

/** The catchment snapshot, when the page has a school with catchment ids. */
function useCatchments(enabled) {
  const [catchments, setCatchments] = useState(null)
  useEffect(() => {
    if (!enabled) return undefined
    let live = true
    loadCatchments().then(data => live && setCatchments(data.byId), () => {})
    return () => {
      live = false
    }
  }, [enabled])
  return catchments
}

/** One of the child's addresses: checked against the school's catchment, or answered. */
function AddressQuestions({ index, value, onChange, school, catchments, measuredOn }) {
  const { t } = useTranslation()
  const ids = school?.catchmentIds || []
  const canCheck = ids.length > 0 && catchments

  const fromCheck = result => {
    if (!result?.schoolId) {
      onChange({ ...value, inCatchment: null, inNeighbouring: null })
      return
    }
    // A merged school can have several catchments; the best one counts.
    const facts = ids.map(id => addressFactsForSchool(catchments, result.schoolId, id))
    onChange({
      ...value,
      inCatchment: facts.some(f => f.inCatchment),
      inNeighbouring: !facts.some(f => f.inCatchment) && facts.some(f => f.inNeighbouring),
    })
  }

  return (
    <div className="space-y-4 rounded-xl border border-neutral-200 p-4">
      <h3 className="font-medium text-neutral-900">{t('admissionCalc.g1.addressN', { n: index + 1 })}</h3>
      {canCheck ? (
        <>
          <AddressCheck catchments={catchments} ownIds={ids} onResult={fromCheck} />
          {value.inCatchment != null && (
            <p className="text-sm text-neutral-800">
              {t(value.inCatchment
                ? 'admissionArea.verdict.inside'
                : value.inNeighbouring ? 'admissionArea.verdict.neighbouring' : 'admissionArea.verdict.outside')}
            </p>
          )}
        </>
      ) : (
        <>
          <YesNo
            label={t('admissionCalc.g1.inCatchment')}
            hint={t('admissionCalc.g1.inCatchmentHint')}
            value={value.inCatchment}
            onChange={v => onChange({ ...value, inCatchment: v })}
          />
          {value.inCatchment !== true && (
            <YesNo
              label={t('admissionCalc.g1.inNeighbouring')}
              value={value.inNeighbouring}
              onChange={v => onChange({ ...value, inNeighbouring: v })}
            />
          )}
        </>
      )}
      {value.inCatchment !== false && (
        <DateField
          label={t('admissionCalc.g1.since')}
          hint={t('admissionCalc.g1.sinceHint', { date: measuredOn })}
          value={value.since}
          max={measuredOn}
          onChange={v => onChange({ ...value, since: v })}
        />
      )}
    </div>
  )
}

/**
 * First grade at a Sofia municipal school: a group (1 is best) for each address the
 * parent could apply with, then extra points inside the group.
 */
function Grade1Calculator({ selected, startYear, school, cutoffRows = [], isCurrentRules }) {
  const { t } = useTranslation()
  const { ruleSet } = selected
  const params = ruleSet.params
  const measuredOn = measuredOnFor(ruleSet)
  const catchments = useCatchments(Boolean(school?.catchmentIds?.length))

  const [addresses, setAddresses] = useState([EMPTY_ADDRESS])
  const [a, setA] = useState({
    siblingAtSchool: null,
    siblingYoung: null,
    preparatoryGroupHere: null,
    disability50: false,
    parentDeceased: false,
    moreThanTwoChildren: null,
    twins: false,
    fosterUnknownAdopted: false,
  })
  const set = (key, value) => setA(prev => ({ ...prev, [key]: value }))

  const context = {
    siblingAtSchool: a.siblingAtSchool,
    siblingUnderAgeAtSchool: a.siblingAtSchool === false ? false : a.siblingAtSchool === true ? a.siblingYoung : null,
    preparatoryGroupHere: a.preparatoryGroupHere,
  }
  const evaluate = address => EVALUATORS[ruleSet.evaluator].evaluate(
    params,
    {
      address,
      disability50: a.disability50,
      parentDeceased: a.parentDeceased,
      moreThanTwoChildren: a.moreThanTwoChildren,
      twins: a.twins,
      fosterUnknownAdopted: a.fosterUnknownAdopted,
    },
    context,
    { measuredOn }
  )
  const results = addresses.map(evaluate)

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="space-y-6">
        <Section title={t('admissionCalc.g1.addressTitle')} intro={t('admissionCalc.g1.addressIntro')}>
          {addresses.map((address, i) => (
            <AddressQuestions
              key={i}
              index={i}
              value={address}
              onChange={next => setAddresses(addresses.map((old, j) => (j === i ? next : old)))}
              school={school}
              catchments={catchments}
              measuredOn={measuredOn}
            />
          ))}
          {addresses.length === 1 ? (
            <button
              type="button"
              onClick={() => setAddresses([...addresses, EMPTY_ADDRESS])}
              className="text-sm font-medium text-primary-700 underline"
            >
              {t('admissionCalc.g1.addSecond')}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setAddresses(addresses.slice(0, 1))}
              className="text-sm text-neutral-600 underline"
            >
              {t('admissionCalc.g1.removeSecond')}
            </button>
          )}
        </Section>

        <Section
          title={school ? t('admissionCalc.g1.thisSchool', { name: school.name }) : t('admissionCalc.g1.perSchoolTitle')}
          intro={t('admissionCalc.g1.perSchoolIntro')}
        >
          <YesNo label={t('admissionCalc.g1.siblingAtSchool')} value={a.siblingAtSchool} onChange={v => set('siblingAtSchool', v)} />
          {a.siblingAtSchool === true && (
            <YesNo
              label={t('admissionCalc.g1.siblingYoung', { max: params.groups.sibling_max_age, year: startYear })}
              value={a.siblingYoung}
              onChange={v => set('siblingYoung', v)}
            />
          )}
          <YesNo label={t('admissionCalc.g1.preparatoryGroupHere')} value={a.preparatoryGroupHere} onChange={v => set('preparatoryGroupHere', v)} />
        </Section>

        <Section title={t('admissionCalc.g1.familyTitle')} intro={t('admissionCalc.privateAnswers')}>
          <YesNo label={t('admissionCalc.g1.moreThanTwoChildren')} value={a.moreThanTwoChildren} onChange={v => set('moreThanTwoChildren', v)} />
          <YesNo label={t('admissionCalc.g1.twins')} value={a.twins} onChange={v => set('twins', v)} />
          <YesNo label={t('admissionCalc.g1.disability50')} value={a.disability50} onChange={v => set('disability50', v)} />
          <YesNo label={t('admissionCalc.g1.parentDeceased')} value={a.parentDeceased} onChange={v => set('parentDeceased', v)} />
          <YesNo label={t('admissionCalc.g1.fosterUnknownAdopted')} value={a.fosterUnknownAdopted} onChange={v => set('fosterUnknownAdopted', v)} />
        </Section>
        <MobileTotal>
          {results.map((result, i) => (
            <span key={i} className="mr-4 inline-block">
              {results.length > 1 && `${t('admissionCalc.g1.addressN', { n: i + 1 })}: `}
              {t('admissionCalc.g1.group')} <strong className="tabular-nums">{formatRange(result.group)}</strong>
              {' · '}
              {t('admissionCalc.g1.points')} <strong className="tabular-nums">{formatRange(result.points)}</strong>
            </span>
          ))}
        </MobileTotal>
      </div>

      <aside className="space-y-4 lg:sticky lg:top-20 lg:self-start" aria-live="polite">
        {results.map((result, i) => (
          <div key={i} className="rounded-2xl border border-neutral-200 bg-white p-5">
            {results.length > 1 && <p className="text-sm font-medium text-neutral-900">{t('admissionCalc.g1.addressN', { n: i + 1 })}</p>}
            <div className="mt-1 flex items-end gap-6">
              <div>
                <p className="text-sm text-neutral-600">{t('admissionCalc.g1.group')}</p>
                <p className="text-4xl font-bold tabular-nums text-neutral-900">{formatRange(result.group)}</p>
              </div>
              <div>
                <p className="text-sm text-neutral-600">{t('admissionCalc.g1.points')}</p>
                <p className="text-4xl font-bold tabular-nums text-neutral-900">{formatRange(result.points)}</p>
              </div>
            </div>
            {(result.group.min !== result.group.max || result.points.min !== result.points.max) && (
              <p className="mt-1 text-xs text-amber-800">{t('admissionCalc.rangeNote')}</p>
            )}
            <div className="mt-4">
              <PointsList items={result.items} />
            </div>
            <CutoffComparison rows={cutoffRows} yours={result} isCurrentRules={isCurrentRules} />
          </div>
        ))}
        <p className="text-xs text-neutral-600">{t('admissionCalc.g1.groupNote')}</p>
        {results.length > 1 && <p className="text-xs text-neutral-600">{t('admissionCalc.g1.lockNote')}</p>}
      </aside>
    </div>
  )
}

export default Grade1Calculator
