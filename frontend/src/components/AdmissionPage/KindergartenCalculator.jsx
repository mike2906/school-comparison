import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { formatRange, measuredOnFor, rowFitsChild, sofiaToday } from '../../admission/calculator'
import { districtLabel, sortedDistricts } from '../../admission/catchments'
import { evaluateKindergarten, KINDERGARTEN_GROUPS, KINDERGARTEN_INITIAL, kindergartenAgeGroup, SOCIAL_FLAGS } from '../../admission/inputs'
import { childBirthYear, getSaved, saveStage, siblingsWith, withSiblings } from '../../admission/saved'
import { useSavedAdmission } from '../../hooks/useSavedAdmission'
import { birthYearsFor } from '../../utils/ageSelection'
import { Choice, DateField, Field, INPUT_CLASS, MobileTotal, Section, YesNo } from './fields'
import PointsList from './PointsList'
import SaveAnswers, { saveBoxId } from './SaveAnswers'
import NextTierHint from './NextTierHint'
import CutoffComparison from '../Admission/CutoffComparison'
import { savedListFilters, savedListUrl } from '../../admission/schoolVerdict'
import { useFreePlaces } from '../../hooks/useFreePlaces'
import { childFreePlaces, schoolFreePlaces } from '../../utils/freePlaces'

// Answers about one kindergarten: never saved, since the list compares every kindergarten
// (whether one is in the parent's district comes from its own district there).
const PER_PLACE = { siblingThere: null, wishRank: 1, sameDistrict: null }
const withoutPlace = answers => ({ ...answers, ...PER_PLACE })

const kindergartenBirthYears = startYear => birthYearsFor(startYear, 7).filter(year => year < startYear)

/**
 * Sofia municipal nursery, kindergarten and preparatory-group points. Without a
 * kindergarten the district is a yes/no question; with one, the parent picks their own
 * district and it is compared with the kindergarten's.
 */
function KindergartenCalculator({ selected, startYear, kindergarten, districts, cutoffRows = [], isCurrentRules }) {
  const { t, i18n } = useTranslation()
  const [a, setA] = useState(() => {
    const saved = getSaved().kindergarten
    if (saved) return { ...KINDERGARTEN_INITIAL, ...withoutPlace(saved.answers), ...saved.siblings?.[kindergarten?.id] }
    // The child's birth year given on another page, so the parent does not give it twice.
    const birthYear = childBirthYear()
    return { ...KINDERGARTEN_INITIAL, birthYear: kindergartenBirthYears(startYear).includes(birthYear) ? birthYear : null }
  })
  const saved = useSavedAdmission().kindergarten
  const freePlacesData = useFreePlaces(Boolean(kindergarten?.id))
  const set = (key, value) => setA(prev => ({ ...prev, [key]: value }))
  // The kindergarten loads after the form: put back the sibling saved for it, and clear
  // an answer given for another one.
  const placeId = kindergarten?.id ?? null
  const placeRef = useRef(placeId)
  useEffect(() => {
    const before = placeRef.current
    placeRef.current = placeId
    if (placeId == null || before === placeId) return
    if (getSaved().kindergarten?.siblings?.[placeId]) setA(prev => ({ ...prev, siblingThere: true }))
    else if (before != null) setA(prev => ({ ...prev, siblingThere: null }))
  }, [placeId])
  const { ruleSet } = selected
  const params = ruleSet.params

  const ageGroup = kindergartenAgeGroup(a, startYear)
  const inKindergarten = ageGroup == null || KINDERGARTEN_GROUPS.includes(ageGroup)
  const usesParentAddress = ageGroup == null || params.address_duration.parent_address_age_groups.includes(ageGroup)
  const nurseryApplies = ageGroup == null || params.nursery_attendance.age_groups.includes(ageGroup)
  const today = sofiaToday()
  const measuredOn = measuredOnFor(ruleSet, today)

  const knownDistrict = kindergarten?.districtCode ?? null
  const result = inKindergarten
    ? evaluateKindergarten(ruleSet, a, startYear, { district: knownDistrict, siblingThere: a.siblingThere, wishRank: a.wishRank }, today)
    : null
  // A sibling answered for this kindergarten is kept for it, so the list counts it there.
  const siblings = siblingsWith(saved, startYear, placeId, a.siblingThere === true ? { siblingThere: true } : null)
  const save = () => saveStage('kindergarten', withSiblings({ startYear, answers: withoutPlace(a) }, siblings))
  const dirty = !saved || saved.startYear !== startYear ||
    JSON.stringify({ ...KINDERGARTEN_INITIAL, ...saved.answers }) !== JSON.stringify(withoutPlace(a)) ||
    JSON.stringify(saved.siblings || {}) !== JSON.stringify(siblings)
  const listFilters = savedListFilters('kindergarten', { startYear, answers: a })
  const listUrl = listFilters ? savedListUrl(listFilters) : '/search'

  const yearOptions = kindergartenBirthYears(startYear)
  const activityOptions = [
    { value: 'working', label: t('admissionCalc.kg.activity.working') },
    { value: 'student', label: t('admissionCalc.kg.activity.student') },
    { value: 'none', label: t('admissionCalc.kg.activity.none') },
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
              <YesNo unsure label={t('admissionCalc.kg.inSofia')} value={a.inSofia} onChange={v => set('inSofia', v)} />
              {usesParentAddress && (
                <YesNo
                  label={t('admissionCalc.kg.sameAddress')}
                  value={a.parentChildSameAddress}
                  onChange={v => set('parentChildSameAddress', v)}
                />
              )}
              <DateField
                label={t('admissionCalc.kg.since')}
                hint={t(measuredOn === today ? 'admissionCalc.kg.sinceHintToday' : 'admissionCalc.kg.sinceHint', { date: measuredOn })}
                value={a.addressSince}
                max={measuredOn}
                onChange={v => set('addressSince', v)}
              />
              {a.inSofia !== false && !(usesParentAddress && a.parentChildSameAddress === false) && (
                <NextTierHint kind="kg" tiers={params.address_duration.tiers} since={a.addressSince} measuredOn={measuredOn} />
              )}
              <Field
                label={t('admissionCalc.kg.homeDistrict')}
                hint={knownDistrict
                  ? t('admissionCalc.kg.homeDistrictHint', { district: districtLabel(kindergarten.districtName, i18n.language) })
                  : t('admissionCalc.kg.homeDistrictListHint')}
              >
                {id => (
                  <select
                    id={id}
                    value={a.homeDistrict ?? ''}
                    onChange={event => set('homeDistrict', event.target.value || null)}
                    className={INPUT_CLASS}
                  >
                    <option value="">{t('admissionCalc.choose')}</option>
                    {sortedDistricts(districts, i18n.language).map(f => (
                      <option key={f.properties.code} value={f.properties.code}>{districtLabel(f.properties.name, i18n.language)}</option>
                    ))}
                  </select>
                )}
              </Field>
              {!knownDistrict && (
                <YesNo unsure label={t('admissionCalc.kg.sameDistrict')} value={a.sameDistrict} onChange={v => set('sameDistrict', v)} />
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
                ]}
              />
            </Section>

            <Section
              title={kindergarten ? t('admissionCalc.kg.thisKindergarten', { name: kindergarten.name }) : t('admissionCalc.kg.perKindergartenTitle')}
              intro={t('admissionCalc.kg.perKindergartenIntro')}
            >
              <YesNo unsure label={t('admissionCalc.kg.siblingThere')} value={a.siblingThere} onChange={v => set('siblingThere', v)} />
              {!kindergarten && a.siblingThere === true && <p className="text-xs text-neutral-600">{t('admissionCalc.kg.siblingListHint')}</p>}
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
              <MobileTotal saveId={ageGroup && dirty ? saveBoxId('kindergarten') : null}>
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
            {ageGroup && (
              <CutoffComparison
                // Only this child's age group and birth-year slot in it: others rank other children.
                rows={cutoffRows.filter(row => rowFitsChild(row, ageGroup, a.birthYear, startYear))}
                yours={{ points: result.total }}
                isCurrentRules={isCurrentRules}
              />
            )}
            {(() => {
              // Today's places in this child's group, for the year being calculated.
              const places = kindergarten && childFreePlaces(schoolFreePlaces(freePlacesData, kindergarten.id), a.birthYear, startYear)
              if (!places) return null
              return (
                <p className="mt-4 rounded-lg bg-neutral-50 p-3 text-xs text-neutral-700" data-testid="calculator-free-places">
                  <strong className={places.free > 0 ? 'text-teal-800' : 'text-neutral-800'}>
                    {places.free > 0
                      ? t('freePlaces.child', { count: places.free, year: places.birthYear }) +
                    (places.buildings > 1 ? ` ${t('freePlaces.inBuildings', { count: places.buildingsWithFree, total: places.buildings })}` : '')
                      : t('freePlaces.childFull', { year: places.birthYear })}
                  </strong>
                  {places.rankedNotEnrolled > 0 && ` · ${t('freePlaces.childRanked', { count: places.rankedNotEnrolled })}`}
                  <span className="mt-1 block">{t('freePlaces.calculatorNote')}</span>
                </p>
              )
            })()}
            {result.socialPriority !== false && (
              <p className="mt-4 rounded-lg bg-neutral-50 p-3 text-xs text-neutral-700">{t('admissionCalc.kg.socialPriority')}</p>
            )}
            {result.specialPlaces && (
              <p className="mt-3 rounded-lg bg-neutral-50 p-3 text-xs text-neutral-700">
                {t(`admissionCalc.kg.specialPlaces.${result.specialPlaces}`)}
              </p>
            )}
            <p className="mt-4 text-xs text-neutral-500">{t('admissionCalc.kg.tieBreak')}</p>
            {ageGroup && <SaveAnswers stage="kindergarten" onSave={save} dirty={dirty} listUrl={listUrl} />}
          </div>
        </aside>
      )}
    </div>
  )
}

export default KindergartenCalculator
