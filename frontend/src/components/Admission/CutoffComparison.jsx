import { useTranslation } from 'react-i18next'
import { comparableRound, compareWithCutoff } from '../../admission/calculator'
import { CutoffValue, rowLabel } from './CutoffTable'

const TONE = {
  above: 'text-green-800',
  below: 'text-red-800',
  tie: 'text-amber-800',
  sameGroup: 'text-amber-800',
  unsure: 'text-neutral-700',
  everyone: 'text-green-800',
  noPlaces: 'text-neutral-700',
}

/**
 * The result beside this school's last admitted child in its latest ranking, one line
 * per group or building. `yours` is `{ points }` or `{ group, points }`; rows ranked
 * under other rules are marked, since their points don't add up the same way.
 */
function CutoffComparison({ rows, yours, isCurrentRules = () => null }) {
  const { t } = useTranslation()
  const lines = (rows || [])
    .map(row => {
      const round = comparableRound(row)
      return { row, round, verdict: compareWithCutoff(yours, round) }
    })
    .filter(line => line.verdict)
  if (lines.length === 0) return null
  return (
    <div className="mt-4 rounded-lg bg-neutral-50 p-3 text-sm">
      <p className="font-medium text-neutral-900">{t('cutoffs.compare.title')}</p>
      <ul className="mt-2 space-y-2">
        {lines.map(({ row, round, verdict }) => (
          <li key={[row.stage, row.age_group, row.birth_year, row.building].join('|')}>
            <div className="text-neutral-700">
              {rowLabel(row, t)}
              {round.classified_on && ` · ${round.classified_on}`}
              {': '}
              <span className="font-medium text-neutral-900"><CutoffValue round={round} /></span>
            </div>
            <div className={TONE[verdict]}>
              {t(`cutoffs.compare.${verdict}`)}
              {isCurrentRules(row) === false && ` ${t('cutoffs.compare.otherRules')}`}
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-neutral-500">{t('cutoffs.compare.note')}</p>
    </div>
  )
}

export default CutoffComparison
