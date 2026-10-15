import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatRange, measuredOnFor } from '../../admission/calculator'
import { addressFactsForSchool } from '../../admission/catchments'
import { EMPTY_ADDRESS, evaluateGrade1, GRADE1_INITIAL } from '../../admission/inputs'
import { loadCatchments } from '../../admission/loadCatchments'
import { getSaved, saveStage } from '../../admission/saved'
import { useSavedAdmission } from '../../hooks/useSavedAdmission'
import AddressCheck from '../Admission/AddressCheck'
import CutoffComparison from '../Admission/CutoffComparison'
import { DateField, MobileTotal, Section, YesNo } from './fields'
import PointsList from './PointsList'
import { savedListFilters, savedListUrl } from '../../admission/schoolVerdict'
import { useCatchmentSchoolName } from '../../hooks/useCatchmentSchoolName'
import SaveAnswers from './SaveAnswers'
import NextTierHint from './NextTierHint'

// Answers about one school: never saved, since the list compares every school.
const PER_PLACE = { siblingAtSchool: null, siblingYoung: null, preparatoryGroupHere: null }
const withoutPlace = answers => ({ ...answers, ...PER_PLACE })
// A checked address is re-checked against whichever school is on the page.
const restoredAddress = address => ({ ...EMPTY_ADDRESS, check: address.check ?? null, since: address.since ?? null })

/** The catchment snapshot (it names the catchment of a checked address). */
function useCatchments() {
  const [catchments, setCatchments] = useState(null)
  useEffect(() => {
    let live = true
    loadCatchments().then(data => live && setCatchments(data.byId), () => {})
    return () => {
      live = false
    }
  }, [])
  return catchments
}

/**
 * One of the child's addresses: checked against the official address table, or
 * answered. With a school on the page the check is about that school; without one, the
 * result is for the school whose catchment the address is in.
 */
function AddressQuestions({ index, value, onChange, school, catchments, measuredOn, tiers }) {
  const { t } = useTranslation()
  const ids = school?.catchmentIds || []
  // The address as last checked, so a saved one comes back filled in.
  const [checked] = useState(() => (value.check ? { streetId: value.check.streetId, number: value.check.number } : null))

  const fromCheck = result => {
    if (!result?.schoolId) {
      onChange(prev => ({ ...prev, check: null, ...(prev.check ? { inCatchment: null, inNeighbouring: null } : {}) }))
      return
    }
    const check = { streetId: result.streetId, number: result.number, schoolId: result.schoolId }
    if (ids.length === 0) {
      onChange(prev => ({ ...prev, check, inCatchment: true, inNeighbouring: false }))
      return
    }
    // A merged school can have several catchments; the best one counts.
    const facts = ids.map(id => addressFactsForSchool(catchments, result.schoolId, id))
    onChange(prev => ({
      ...prev,
      check,
      inCatchment: facts.some(f => f.inCatchment),
      inNeighbouring: !facts.some(f => f.inCatchment) && facts.some(f => f.inNeighbouring),
    }))
  }
  const checkedHere = Boolean(value.check)
  const catchmentSchoolName = useCatchmentSchoolName(value.check?.schoolId, catchments?.get(value.check?.schoolId)?.properties.name)

  return (
    <div className="space-y-4 rounded-xl border border-neutral-200 p-4">
      <h3 className="font-medium text-neutral-900">{t('admissionCalc.g1.addressN', { n: index + 1 })}</h3>
      {catchments && (
        <AddressCheck
          catchments={catchments}
          ownIds={ids}
          nameSchool={ids.length > 0}
          onResult={fromCheck}
          value={checked}
        />
      )}
      {checkedHere && ids.length === 0 && (
        <p className="text-sm text-neutral-800">
          {t('admissionCalc.g1.forCatchmentSchool', { school: catchmentSchoolName || '' })}
        </p>
      )}
      {checkedHere && ids.length > 0 && value.inCatchment != null && (
        <p className="text-sm text-neutral-800">
          {t(value.inCatchment
            ? 'admissionArea.verdict.inside'
            : value.inNeighbouring ? 'admissionArea.verdict.neighbouring' : 'admissionArea.verdict.outside')}
        </p>
      )}
      {!checkedHere && (
        <>
          <YesNo
            label={t('admissionCalc.g1.inCatchment')}
            hint={t('admissionCalc.g1.inCatchmentHint')}
            value={value.inCatchment}
            onChange={v => onChange(prev => ({ ...prev, inCatchment: v }))}
          />
          {value.inCatchment !== true && (
            <YesNo
              label={t('admissionCalc.g1.inNeighbouring')}
              value={value.inNeighbouring}
              onChange={v => onChange(prev => ({ ...prev, inNeighbouring: v }))}
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
          onChange={v => onChange(prev => ({ ...prev, since: v }))}
        />
      )}
      {value.inCatchment === true && <NextTierHint kind="g1" tiers={tiers} since={value.since} measuredOn={measuredOn} />}
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
  const catchments = useCatchments()
  const saved = useSavedAdmission().grade_1

  const [addresses, setAddresses] = useState(() => {
    const entry = getSaved().grade_1
    return entry?.addresses?.length ? entry.addresses.slice(0, 2).map(restoredAddress) : [EMPTY_ADDRESS]
  })
  const [a, setA] = useState(() => {
    const entry = getSaved().grade_1
    return entry ? { ...GRADE1_INITIAL, ...withoutPlace(entry.answers) } : GRADE1_INITIAL
  })
  const set = (key, value) => setA(prev => ({ ...prev, [key]: value }))
  const updateAddress = (i, update) => setAddresses(prev => prev.map((old, j) => (j === i ? update(old) : old)))

  const context = {
    siblingAtSchool: a.siblingAtSchool,
    siblingUnderAgeAtSchool: a.siblingAtSchool === false ? false : a.siblingAtSchool === true ? a.siblingYoung : null,
    preparatoryGroupHere: a.preparatoryGroupHere,
  }
  const evaluate = address => evaluateGrade1(ruleSet, a, address, context)
  const results = addresses.map(evaluate)

  // Only what the list can reuse: the checked address and dates, not yes/no answers
  // about whichever school was on the page.
  const toSave = () => ({ startYear, addresses: addresses.map(restoredAddress), answers: withoutPlace(a) })
  const home = addresses[0]?.check ? { streetId: addresses[0].check.streetId, number: addresses[0].check.number } : undefined
  const save = () => saveStage('grade_1', toSave(), { home })
  const dirty = !saved || JSON.stringify({ ...toSave(), savedOn: saved.savedOn }) !== JSON.stringify(saved)
  const listUrl = savedListUrl(savedListFilters('grade_1', { startYear, answers: a }))

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="space-y-6">
        <Section title={t('admissionCalc.g1.addressTitle')} intro={t('admissionCalc.g1.addressIntro')}>
          {addresses.map((address, i) => (
            <AddressQuestions
              key={i}
              index={i}
              value={address}
              onChange={update => updateAddress(i, update)}
              school={school}
              catchments={catchments}
              measuredOn={measuredOn}
              // A young sibling at the school is group 1 however long the address.
              tiers={context.siblingUnderAgeAtSchool === true ? null : params.groups.tiers}
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
        <SaveAnswers
          stage="grade_1"
          onSave={save}
          dirty={dirty}
          listUrl={listUrl}
          blockedReason={addresses[0]?.check ? null : t('savedAdmission.needsAddressToSave')}
        />
      </aside>
    </div>
  )
}

export default Grade1Calculator
