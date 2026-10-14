import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { EVALUATORS } from '../../admission/evaluators'
import { formatRange, measuredOnFor } from '../../admission/calculator'
import { calculateAgeGroup } from '../../utils/education'
import { birthYearsFor } from '../../utils/ageSelection'
import { Choice, DateField, Field, INPUT_CLASS, MobileTotal, Section, YesNo } from './fields'
import PointsList from './PointsList'

const KINDERGARTEN_GROUPS = ['nursery', 'first', 'second', 'third', 'preschool']
const SOCIAL_FLAGS = ['family_disability_70', 'unknown_or_restricted_parent', 'foster_or_adopted', 'child_at_risk', 'three_plus_children']

const INITIAL = {
  birthYear: null,
  inSofia: null,
  addressSince: null,
  parentChildSameAddress: null,
  sameDistrict: null,
  homeDistrict: null,
  parentCount: 2,
  parentActivities: [null, null],
  twins: false,
  closeInAge: false,
  nurseryAttended: null,
  parentsDeceased: 'none',
  social: Object.fromEntries(SOCIAL_FLAGS.map(flag => [flag, false])),
  additional: 'none',
  transfer: false,
  siblingThere: null,
  wishRank: 1,
}

/**
 * Sofia municipal nursery, kindergarten and preparatory-group points. Without a
 * kindergarten the district is a yes/no question; with one, the parent picks their own
 * district and it is compared with the kindergarten's.
 */
function KindergartenCalculator({ selected, startYear, kindergarten, districts }) {
  const { t } = useTranslation()
  const [a, setA] = useState(INITIAL)
  const set = (key, value) => setA(prev => ({ ...prev, [key]: value }))
  const { ruleSet } = selected
  const params = ruleSet.params

  const ageGroup = a.birthYear ? calculateAgeGroup(startYear, a.birthYear) : null
  const inKindergarten = ageGroup == null || KINDERGARTEN_GROUPS.includes(ageGroup)
  const usesParentAddress = ageGroup == null || params.address_duration.parent_address_age_groups.includes(ageGroup)
  const nurseryApplies = ageGroup == null || params.nursery_attendance.age_groups.includes(ageGroup)
  const measuredOn = measuredOnFor(ruleSet)

  const knownDistrict = kindergarten?.districtCode ?? null
  const context = {
    district: knownDistrict ?? (a.sameDistrict === true ? 'home' : a.sameDistrict === false ? 'elsewhere' : null),
    siblingThere: a.siblingThere,
    wishRank: a.wishRank,
  }
  const answers = {
    ageGroup,
    inSofia: a.inSofia,
    addressSince: a.addressSince,
    parentChildSameAddress: a.parentChildSameAddress,
    homeDistrict: knownDistrict ? a.homeDistrict : (a.sameDistrict == null ? null : 'home'),
    parents: a.parentActivities.slice(0, a.parentCount).map(activity => ({ activity })),
    twins: a.twins,
    closeInAge: a.closeInAge,
    nurseryAttended: a.nurseryAttended,
    social: { parentsDeceased: a.parentsDeceased, ...a.social },
    additional: a.additional,
    transfer: a.transfer,
  }
  const result = inKindergarten
    ? EVALUATORS[ruleSet.evaluator].evaluate(params, answers, context, { campaign: ruleSet.campaign, measuredOn })
    : null

  const yearOptions = birthYearsFor(startYear, 7).filter(year => year < startYear)
  const activityOptions = [
    { value: 'working', label: t('admissionCalc.kg.activity.working') },
    { value: 'student', label: t('admissionCalc.kg.activity.student') },
    { value: 'none', label: t('admissionCalc.kg.activity.none') },
    { value: null, label: t('admissionCalc.notSure') },
  ]

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="space-y-6">
        <Section title={t('admissionCalc.kg.childTitle')}>
          <Field label={t('admissionCalc.kg.birthYear')} hint={t('admissionCalc.kg.birthYearHint', { year: startYear })}>
            {id => (
              <select
                id={id}
                value={a.birthYear ?? ''}
                onChange={event => set('birthYear', event.target.value ? Number(event.target.value) : null)}
                className={INPUT_CLASS}
              >
                <option value="">{t('admissionCalc.choose')}</option>
                {yearOptions.map(year => <option key={year} value={year}>{year}</option>)}
              </select>
            )}
          </Field>
          {ageGroup && inKindergarten && (
            <p className="text-sm text-neutral-700">{t('admissionCalc.kg.group', { group: t(`ageGroups.${ageGroup}`) })}</p>
          )}
          {!inKindergarten && <p className="text-sm text-amber-800">{t('admissionCalc.kg.notKindergartenAge')}</p>}
        </Section>

        {inKindergarten && (
          <>
            <Section
              title={t('admissionCalc.kg.addressTitle')}
              intro={t(usesParentAddress ? 'admissionCalc.kg.addressParent' : 'admissionCalc.kg.addressChild')}
            >
              <YesNo label={t('admissionCalc.kg.inSofia')} value={a.inSofia} onChange={v => set('inSofia', v)} />
              {usesParentAddress && (
                <YesNo
                  label={t('admissionCalc.kg.sameAddress')}
                  value={a.parentChildSameAddress}
                  onChange={v => set('parentChildSameAddress', v)}
                />
              )}
              <DateField
                label={t('admissionCalc.kg.since')}
                hint={t('admissionCalc.kg.sinceHint', { date: measuredOn })}
                value={a.addressSince}
                max={measuredOn}
                onChange={v => set('addressSince', v)}
              />
              {knownDistrict ? (
                <Field label={t('admissionCalc.kg.homeDistrict')} hint={t('admissionCalc.kg.homeDistrictHint', { district: kindergarten.districtName })}>
                  {id => (
                    <select
                      id={id}
                      value={a.homeDistrict ?? ''}
                      onChange={event => set('homeDistrict', event.target.value || null)}
                      className={INPUT_CLASS}
                    >
                      <option value="">{t('admissionCalc.notSure')}</option>
                      {(districts?.features || []).map(f => (
                        <option key={f.properties.code} value={f.properties.code}>{f.properties.name}</option>
                      ))}
                    </select>
                  )}
                </Field>
              ) : (
                <YesNo label={t('admissionCalc.kg.sameDistrict')} value={a.sameDistrict} onChange={v => set('sameDistrict', v)} />
              )}
            </Section>

            <Section title={t('admissionCalc.kg.familyTitle')}>
              <Choice
                label={t('admissionCalc.kg.parentCount')}
                value={a.parentCount}
                onChange={v => set('parentCount', v)}
                options={[
                  { value: 1, label: t('admissionCalc.kg.oneParent') },
                  { value: 2, label: t('admissionCalc.kg.twoParents') },
                ]}
              />
              {Array.from({ length: a.parentCount }, (_, i) => (
                <Choice
                  key={i}
                  label={t('admissionCalc.kg.parentActivity', { n: i + 1 })}
                  hint={i === 0 ? t('admissionCalc.kg.parentActivityHint') : undefined}
                  value={a.parentActivities[i]}
                  onChange={v => set('parentActivities', a.parentActivities.map((old, j) => (j === i ? v : old)))}
                  options={activityOptions}
                />
              ))}
              {nurseryApplies && (
                <YesNo
                  label={t('admissionCalc.kg.nursery')}
                  hint={t('admissionCalc.kg.nurseryHint')}
                  value={a.nurseryAttended}
                  onChange={v => set('nurseryAttended', v)}
                />
              )}
              <YesNo label={t('admissionCalc.kg.twins')} value={a.twins} onChange={v => set('twins', v)} />
              <YesNo label={t('admissionCalc.kg.closeInAge')} value={a.closeInAge} onChange={v => set('closeInAge', v)} />
            </Section>

            <Section title={t('admissionCalc.kg.socialTitle')} intro={t('admissionCalc.privateAnswers')}>
              <Choice
                label={t('admissionCalc.kg.parentsDeceased')}
                value={a.parentsDeceased}
                onChange={v => set('parentsDeceased', v)}
                options={[
                  { value: 'none', label: t('admissionCalc.no') },
                  { value: 'one', label: t('admissionCalc.kg.deceasedOne') },
                  { value: 'both', label: t('admissionCalc.kg.deceasedBoth') },
                  { value: null, label: t('admissionCalc.notSure') },
                ]}
              />
              {SOCIAL_FLAGS.map(flag => (
                <YesNo
                  key={flag}
                  label={t(`admissionCalc.kg.social.${flag}`)}
                  value={a.social[flag]}
                  onChange={v => set('social', { ...a.social, [flag]: v })}
                />
              ))}
              <Choice
                label={t('admissionCalc.kg.additional')}
                value={a.additional}
                onChange={v => set('additional', v)}
                options={[
                  { value: 'none', label: t('admissionCalc.no') },
                  { value: 'sen', label: t('admissionCalc.kg.sen') },
                  { value: 'chronic', label: t('admissionCalc.kg.chronic') },
                  { value: null, label: t('admissionCalc.notSure') },
                ]}
              />
            </Section>

            <Section
              title={kindergarten ? t('admissionCalc.kg.thisKindergarten', { name: kindergarten.name }) : t('admissionCalc.kg.perKindergartenTitle')}
              intro={t('admissionCalc.kg.perKindergartenIntro')}
            >
              <YesNo label={t('admissionCalc.kg.siblingThere')} value={a.siblingThere} onChange={v => set('siblingThere', v)} />
              <Choice
                label={t('admissionCalc.kg.wishRank')}
                hint={t('admissionCalc.kg.wishRankHint')}
                value={a.wishRank}
                onChange={v => set('wishRank', v)}
                options={[
                  ...params.wish_order.map((_, i) => ({ value: i + 1, label: t('admissionCalc.kg.wish', { n: i + 1 }) })),
                  { value: params.wish_order.length + 1, label: t('admissionCalc.kg.wishLater') },
                ]}
              />
              <YesNo label={t('admissionCalc.kg.transfer')} value={a.transfer} onChange={v => set('transfer', v)} />
            </Section>
            {result && (
              <MobileTotal>
                {t('admissionCalc.kg.total')}: <strong className="tabular-nums">{formatRange(result.total)}</strong>
              </MobileTotal>
            )}
          </>
        )}
      </div>

      {result && (
        <aside className="lg:sticky lg:top-20 lg:self-start">
          <div className="rounded-2xl border border-neutral-200 bg-white p-5" aria-live="polite">
            <p className="text-sm text-neutral-600">{t('admissionCalc.kg.total')}</p>
            <p className="mt-1 text-4xl font-bold tabular-nums text-neutral-900">{formatRange(result.total)}</p>
            {result.total.min !== result.total.max && (
              <p className="mt-1 text-xs text-amber-800">{t('admissionCalc.rangeNote')}</p>
            )}
            <div className="mt-4">
              <PointsList items={result.items} />
            </div>
            {result.socialPriority !== false && (
              <p className="mt-4 rounded-lg bg-neutral-50 p-3 text-xs text-neutral-700">{t('admissionCalc.kg.socialPriority')}</p>
            )}
            {result.specialPlaces && (
              <p className="mt-3 rounded-lg bg-neutral-50 p-3 text-xs text-neutral-700">
                {t(`admissionCalc.kg.specialPlaces.${result.specialPlaces}`)}
              </p>
            )}
            <p className="mt-4 text-xs text-neutral-500">{t('admissionCalc.kg.tieBreak')}</p>
          </div>
        </aside>
      )}
    </div>
  )
}

export default KindergartenCalculator
