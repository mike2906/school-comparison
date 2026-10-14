import { useTranslation } from 'react-i18next'
import { cutoffValue } from '../../admission/calculator'

function rowLabel(row, t) {
  const what = row.stage === 'grade_8'
    ? [row.class_code, row.class_name].filter(Boolean).join(' ')
    : row.stage === 'grade_1'
      ? t('cutoffs.stage.grade_1')
      : row.stage === 'preparatory'
        ? t('cutoffs.stage.preparatory')
        : t(`ageGroups.${row.age_group}`, { defaultValue: row.age_group || '' })
  // A nursery ranks two birth years separately, so each year is its own row.
  const born = row.birth_year && row.stage !== 'grade_8' ? t('cutoffs.bornIn', { year: row.birth_year }) : null
  return [what, born, row.building].filter(Boolean).join(' · ')
}

function CutoffValue({ round }) {
  const { t } = useTranslation()
  const value = cutoffValue(round)
  if (!value) return <span className="text-neutral-400">—</span>
  if (value.kind === 'everyone') return <span>{t('cutoffs.everyone')}</span>
  if (value.kind === 'noPlaces') return <span>{t('cutoffs.noPlaces')}</span>
  if (value.kind === 'score') return <span className="tabular-nums">{value.score}</span>
  if (value.kind === 'quota') {
    return (
      <span className="tabular-nums">
        {t('cutoffs.quotaScores', { boys: value.boys ?? '—', girls: value.girls ?? '—' })}
      </span>
    )
  }
  if (value.kind === 'group') {
    return (
      <span className="tabular-nums">
        {value.points == null
          ? t('cutoffs.groupOnly', { group: value.group })
          : t('cutoffs.groupAndPoints', { group: value.group, points: value.points })}
      </span>
    )
  }
  return <span className="tabular-nums">{t('cutoffs.points', { points: value.points })}</span>
}

function RoundCell({ round }) {
  const { t } = useTranslation()
  if (!round) return <td className="px-2 py-2 text-neutral-400 sm:px-3">—</td>
  return (
    <td className="px-2 py-2 align-top sm:px-3">
      <div className="font-medium text-neutral-900">
        <CutoffValue round={round} />
        {round.tie_at_cutoff && <span className="ml-1 text-xs font-normal text-neutral-500">{t('cutoffs.tie')}</span>}
      </div>
      <div className="text-xs text-neutral-500">
        {/* The source's round names are Bulgarian only and sometimes cut off; the column says which round. */}
        {[round.round_kind === 'current' ? t('cutoffs.currentRound') : null, round.classified_on].filter(Boolean).join(' · ')}
      </div>
    </td>
  )
}

/**
 * The last admitted result of the first and the latest round, per age group, first
 * grade or 8th-grade class, from `admission_info.cutoff_summary`. `isCurrentRules(row)`
 * says whether a row was ranked under the rules the reader is looking at (true, false,
 * or null when unknown); rows under other rules are marked, never hidden.
 */
function CutoffTable({ rows, isCurrentRules = () => null }) {
  const { t } = useTranslation()
  if (!rows || rows.length === 0) return null
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-neutral-500">
          <tr>
            <th className="px-2 py-2 font-medium sm:px-3">{t('cutoffs.what')}</th>
            <th className="px-2 py-2 font-medium sm:px-3">{t('cutoffs.firstRound')}</th>
            <th className="px-2 py-2 font-medium sm:px-3">{t('cutoffs.latestRound')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-100">
          {rows.map(row => {
            const current = isCurrentRules(row)
            const showLatest = row.rounds > 1 && row.latest?.round_id !== row.first?.round_id
            return (
              <tr key={[row.stage, row.age_group, row.birth_year, row.class_code, row.building].join('|')}>
                <td className="px-2 py-2 align-top sm:px-3">
                  <div className="text-neutral-900">{rowLabel(row, t)}</div>
                  <div className="text-xs text-neutral-500">
                    {row.campaign}
                    {row.quota && ` · ${t('cutoffs.quota')}`}
                    {current === false && ` · ${t('cutoffs.otherRules')}`}
                  </div>
                </td>
                <RoundCell round={row.first} />
                {showLatest ? <RoundCell round={row.latest} /> : <td className="px-2 py-2 text-neutral-400 sm:px-3">—</td>}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

export default CutoffTable
