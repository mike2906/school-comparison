import i18next from 'i18next'
import { useTranslation } from 'react-i18next'
import { cutoffValue, formatRankingDay, shortCampaign } from '../../admission/calculator'
import { formatAmount } from '../../utils/format'

export function rowLabel(row, t, language = i18next.language) {
  // The ministry's class and building names are Bulgarian; the API adds English ones it has.
  const english = String(language || '').startsWith('en')
  const className = (english && row.class_name_en) || row.class_name
  const building = (english && row.building_en) || row.building
  // An 8th-grade class by its name; the ministry's code only when it has none.
  const what = row.stage === 'grade_8'
    ? `${t('cutoffs.stage.grade_8')}: ${[className, row.class_code && (className ? `(${row.class_code})` : row.class_code)].filter(Boolean).join(' ')}`
    : row.stage === 'grade_1'
      ? t('cutoffs.stage.grade_1')
      : row.stage === 'preparatory'
        ? t('cutoffs.stage.preparatory')
        : t(`ageGroups.${row.age_group}`, { defaultValue: row.age_group || '' })
  // A nursery ranks two birth years separately, so each year is its own row.
  const born = row.birth_year && row.stage !== 'grade_8' ? t('cutoffs.bornIn', { year: row.birth_year }) : null
  return [what, born, building].filter(Boolean).join(' · ')
}

export function CutoffValue({ round }) {
  const { t, i18n } = useTranslation()
  const value = cutoffValue(round)
  const number = n => (n == null ? '—' : formatAmount(n, i18n.language, 2))
  if (!value) return <span className="text-neutral-400">—</span>
  if (value.kind === 'everyone') return <span>{t('cutoffs.everyone')}</span>
  if (value.kind === 'noPlaces') return <span>{t('cutoffs.noPlaces')}</span>
  if (value.kind === 'score') return <span className="tabular-nums">{t('cutoffs.score', { score: number(value.score) })}</span>
  if (value.kind === 'quota') {
    return (
      <span className="tabular-nums">
        {t('cutoffs.quotaScores', { boys: number(value.boys), girls: number(value.girls) })}
      </span>
    )
  }
  if (value.kind === 'group') {
    return (
      <span className="tabular-nums">
        {value.points == null
          ? t('cutoffs.groupOnly', { group: value.group })
          : value.points === 0
            ? t('cutoffs.groupNoPoints', { group: value.group })
            : t('cutoffs.groupAndPoints', { group: value.group, points: number(value.points) })}
      </span>
    )
  }
  return <span className="tabular-nums">{t('cutoffs.points', { points: number(value.points) })}</span>
}

function RoundCell({ round }) {
  const { t, i18n } = useTranslation()
  if (!round) return <td className="px-2 py-2 text-neutral-400 sm:px-3">—</td>
  return (
    <td className="px-2 py-2 align-top sm:px-3">
      <div className="font-medium text-neutral-900">
        <CutoffValue round={round} />
        {round.tie_at_cutoff && <span className="ml-1 text-xs font-normal text-neutral-500">{t('cutoffs.tie')}</span>}
      </div>
      {/* The source's round names are Bulgarian only and sometimes cut off; the column says which round.
          The places it ranked show why "Everyone admitted" can follow a full class (333: 1 place in September). */}
      <div className="text-xs text-neutral-500">
        {[
          round.round_kind === 'current' ? t('cutoffs.currentRound') : null,
          round.places > 0 ? t('cutoffs.places', { count: round.places }) : null,
          formatRankingDay(round.classified_on, i18n.language),
        ].filter(Boolean).join(' · ')}
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
  // Explain the cells a parent can't read on their own, once each, only when they appear.
  const shown = rows.flatMap(row => [row.first, showLatestRound(row) ? row.latest : null]).filter(Boolean)
  const legend = [
    // Two rankings side by side read as one result unless it is said they are separate.
    (rows.some(showLatestRound) || shown.some(round => round.round_kind === 'current')) && 'rounds',
    shown.some(round => round.round_kind === 'current') && 'current',
    shown.some(round => cutoffValue(round)?.kind === 'everyone') && 'everyone',
    shown.some(round => cutoffValue(round)?.kind === 'noPlaces') && 'noPlaces',
  ].filter(Boolean)
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
            const showLatest = showLatestRound(row)
            return (
              <tr key={[row.stage, row.age_group, row.birth_year, row.class_code, row.building].join('|')}>
                <td className="px-2 py-2 align-top sm:px-3">
                  <div className="text-neutral-900">{rowLabel(row, t)}</div>
                  <div className="text-xs text-neutral-500">
                    {shortCampaign(row.campaign)}
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
      {legend.length > 0 && (
        <ul className="mt-2 space-y-1 px-2 text-xs text-neutral-500 sm:px-3">
          {legend.map(key => <li key={key}>{t(`cutoffs.legend.${key}`)}</li>)}
        </ul>
      )}
    </div>
  )
}

function showLatestRound(row) {
  return row.rounds > 1 && row.latest?.round_id !== row.first?.round_id
}

export default CutoffTable
