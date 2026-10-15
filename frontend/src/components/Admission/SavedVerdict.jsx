import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { formatRange } from '../../admission/calculator'
import { forgetSaved, SAVED_STAGES } from '../../admission/saved'
import { SEARCH_GROUPS } from '../../admission/schoolVerdict'
import { useSavedAdmission } from '../../hooks/useSavedAdmission'
import { CutoffValue, rowLabel } from './CutoffTable'

const TONE = {
  above: 'text-green-800',
  below: 'text-red-800',
  tie: 'text-amber-800',
  sameGroup: 'text-amber-800',
  mixed: 'text-amber-800',
  everyone: 'text-green-800',
}

/** "23 points" or "group 1, 5 points": the parent's own result. */
function Yours({ verdict }) {
  const { t } = useTranslation()
  const { yours } = verdict
  if (!yours) return null
  const text = yours.group
    ? t('savedAdmission.yoursGroup', { group: formatRange(yours.group), points: formatRange(yours.points) })
    : t('savedAdmission.yoursPoints', { points: formatRange(yours.points) })
  return <span className="font-medium text-neutral-900">{text}</span>
}

/**
 * One line on a search card: the saved result, the last admitted child, and where it
 * stands. The list's banner says what was assumed.
 */
export function SavedVerdictLine({ verdict }) {
  const { t } = useTranslation()
  if (verdict.verdict === 'needsAddress') {
    return <span className="text-neutral-600">{t('savedAdmission.verdict.needsAddress')}</span>
  }
  const line = verdict.lines[0]
  // "Everyone got in" and "no places" say it all; the cut-off cell would repeat it.
  const showCutoff = line && !['everyone', 'noPlaces'].includes(verdict.verdict)
  return (
    <span>
      <Yours verdict={verdict} />
      {showCutoff && (
        <>
          {' · '}
          {t('savedAdmission.lastAdmitted')} <CutoffValue round={line.round} />
        </>
      )}
      {' · '}
      <span className={TONE[verdict.verdict] || 'text-neutral-700'}>{t(`savedAdmission.verdict.${verdict.verdict}`)}</span>
      {verdict.lines.some(l => l.otherRules) && <span className="text-neutral-600"> ({t('cutoffs.otherRules')})</span>}
      <PriorityNote verdict={verdict} className="block text-xs text-neutral-600" />
    </span>
  )
}

/** Social priority and SEN / chronic-illness places: admission paths a cut-off says nothing about. */
function PriorityNote({ verdict, className }) {
  const { t } = useTranslation()
  // As on the calculator: null means a "not sure" answer may give priority.
  const priority = verdict.stage === 'kindergarten' && verdict.socialPriority !== false
  if (!priority && !verdict.specialPlaces) return null
  return (
    <span className={className}>
      {priority && t('admissionCalc.kg.socialPriority')}
      {verdict.specialPlaces && ` ${t(`admissionCalc.kg.specialPlaces.${verdict.specialPlaces}`)}`}
    </span>
  )
}

/**
 * On a school page: the saved answers against this school, line by line, with the
 * assumptions, a link to change the answers and one to forget them.
 */
export function SavedVerdictPanel({ verdict, schoolId }) {
  const { t } = useTranslation()
  return (
    <div className="mt-4 rounded-xl border border-primary-200 bg-primary-50 p-4 text-sm" data-testid="saved-verdict">
      <p className="font-medium text-primary-900">{t('savedAdmission.panelTitle')}</p>
      {verdict.verdict === 'needsAddress' || verdict.verdict === 'noCutoff' ? (
        <p className="mt-1 text-neutral-800">
          {verdict.yours && <><Yours verdict={verdict} />{' · '}</>}
          {t(`savedAdmission.verdict.${verdict.verdict}`)}
        </p>
      ) : (
        <>
          <p className="mt-1"><Yours verdict={verdict} /></p>
          <ul className="mt-2 space-y-1">
            {verdict.lines.map(({ row, round, verdict: lineVerdict, otherRules }) => (
              <li key={[row.stage, row.age_group, row.birth_year, row.building].join('|')}>
                <span className="text-neutral-700">
                  {rowLabel(row, t)}
                  {round.classified_on && ` · ${round.classified_on}`}
                  {': '}
                </span>
                <span className="font-medium text-neutral-900"><CutoffValue round={round} /></span>
                {' · '}
                <span className={TONE[lineVerdict] || 'text-neutral-700'}>{t(`savedAdmission.verdict.${lineVerdict}`)}</span>
                {otherRules && <span className="text-neutral-600"> {t('cutoffs.compare.otherRules')}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
      <PriorityNote verdict={verdict} className="mt-2 block text-xs text-neutral-700" />
      <p className="mt-2 text-xs text-neutral-600">{t(`savedAdmission.assumed.${verdict.stage}`)} {t('cutoffs.compare.note')}</p>
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        <Link to={`/admission?stage=${verdict.stage}&school=${schoolId}`} className="font-medium text-primary-700 underline">
          {t('savedAdmission.edit')}
        </Link>
        <button type="button" onClick={() => forgetSaved(verdict.stage)} className="text-neutral-600 underline">
          {t('savedAdmission.forget')}
        </button>
      </div>
    </div>
  )
}

/** Above the search list: whose answers the cards use, what they assume, and a way out. */
export function SavedAdmissionBanner({ ageGroup }) {
  const { t } = useTranslation()
  const saved = useSavedAdmission()
  const stages = SAVED_STAGES.filter(stage => saved[stage] && (!ageGroup || SEARCH_GROUPS[stage].includes(ageGroup)))
  if (stages.length === 0) return null
  return (
    <div className="mx-3 mt-3 rounded-xl border border-primary-200 bg-primary-50 p-3 text-sm text-primary-900" data-testid="saved-admission-banner">
      {stages.map(stage => (
        <div key={stage} className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <p>
            {t(`savedAdmission.banner.${stage}`, { year: saved[stage].answers.birthYear })}{' '}
            <span className="text-primary-900/80">{t(`savedAdmission.assumed.${stage}`)}</span>
          </p>
          <div className="flex gap-3 text-xs">
            <Link to={`/admission?stage=${stage}`} className="font-medium underline">{t('savedAdmission.edit')}</Link>
            <button type="button" onClick={() => forgetSaved(stage)} className="underline">{t('savedAdmission.forget')}</button>
          </div>
        </div>
      ))}
    </div>
  )
}
