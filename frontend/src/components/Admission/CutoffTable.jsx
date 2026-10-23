import { useTranslation } from 'react-i18next'
import { LAST_ADMITTED_KINDS, comparableRound, cutoffText, cutoffValue, formatRankingDay, laterEasierRound, laterHarder, rankingLabel, roundCounts, rowLabel, roundsOf, shortCampaign } from '../../admission/calculator'

// First grade: groups 1–3 live in the school's area (by how long), group 4 outside it.
const GRADE_1_OUTSIDE_GROUP = 4

export function CutoffValue({ round }) {
  const { t, i18n } = useTranslation()
  const text = cutoffText(round, t, i18n.language)
  if (!text) return <span className="text-neutral-400">—</span>
  const kind = cutoffValue(round)?.kind
  return <span className={LAST_ADMITTED_KINDS.has(kind) ? 'tabular-nums' : undefined}>{text}</span>
}

/** "First ranking" for the campaign's main ranking, "Free-place ranking" for a later one. */
function roundKind(round, index, t) {
  if (round.round_kind === 'current') return t('cutoffs.kind.current')
  return index === 0 ? t('cutoffs.kind.first') : t('cutoffs.kind.later', { number: index + 1 })
}

/**
 * The headline of a row: its reference ranking (the main one, or the latest free-place
 * ranking with places when only those are left online), named with its date, and the
 * places and applicants it had. A first-grade group says what it means.
 */
function Headline({ row, rounds }) {
  const { t, i18n } = useTranslation()
  const round = comparableRound(row)
  if (!round) return null
  const day = formatRankingDay(round.classified_on, i18n.language)
  const value = cutoffValue(round)
  // No ranking had a place: say how many, when we have them all.
  if (value?.kind === 'noPlaces' && round.round_kind === 'current' && rounds.length > 1 &&
      rounds.length === row.rounds && rounds.every(r => r.places === 0)) {
    const from = formatRankingDay(rounds[0].classified_on, i18n.language)
    return <div className="font-medium text-neutral-900">{t('cutoffs.ref.noPlacesIn', { count: rounds.length, from, to: day })}</div>
  }
  const label = rankingLabel(row, round, t, i18n.language)
  const counts = roundCounts(round, t)
  return (
    <>
      <div>
        <span className="text-neutral-700">
          {label}
          {/* A number alone reads as a pass mark; it is the last child let in by this ranking. */}
          {LAST_ADMITTED_KINDS.has(value?.kind) ? `, ${t('cutoffs.lastAdmitted')} ` : ': '}
        </span>
        <span className="font-medium text-neutral-900"><CutoffValue round={round} /></span>
        {round.tie_at_cutoff && <span className="text-neutral-600"> {t('cutoffs.tie')}</span>}
      </div>
      {counts && <div className="text-xs text-neutral-500">{counts}</div>}
      {value?.kind === 'group' && (
        <div className="text-xs text-neutral-600">
          {t(value.group >= GRADE_1_OUTSIDE_GROUP ? 'cutoffs.groupMeaning.outside' : 'cutoffs.groupMeaning.inside', { group: value.group })}
        </div>
      )}
    </>
  )
}

/** One line when a later ranking let in a weaker result for a place that came free. */
function Later({ row }) {
  const { t, i18n } = useTranslation()
  const round = laterEasierRound(row)
  if (!round) return null
  return (
    <div className="text-xs text-neutral-600">
      {t('cutoffs.later', { date: formatRankingDay(round.classified_on, i18n.language) })}
      {LAST_ADMITTED_KINDS.has(cutoffValue(round)?.kind) ? `, ${t('cutoffs.lastAdmitted')} ` : ': '}
      <span className="text-neutral-900"><CutoffValue round={round} /></span>
      {round.tie_at_cutoff && ` ${t('cutoffs.tie')}`}
    </div>
  )
}

/** Every ranking of the row, folded away: one line each, never side by side. */
function History({ row, rounds }) {
  const { t, i18n } = useTranslation()
  // A list response has only the first and the latest: no list to show.
  if (!row.history || rounds.length < 2) return null
  return (
    <details className="mt-1 text-xs text-neutral-600">
      <summary className="cursor-pointer select-none text-primary-700 hover:text-primary-800">
        {t('cutoffs.history', { count: rounds.length })}
      </summary>
      {laterHarder(row) && <p className="mt-1 pl-4">{t('cutoffs.historyNote')}</p>}
      <ol className="mt-1 space-y-1 pl-4">
        {rounds.map((round, index) => (
          <li key={round.round_id}>
            {[formatRankingDay(round.classified_on, i18n.language), roundKind(round, index, t), roundCounts(round, t)].filter(Boolean).join(' · ')}
            {': '}
            <span className="text-neutral-900"><CutoffValue round={round} /></span>
            {round.tie_at_cutoff && ` ${t('cutoffs.tie')}`}
          </li>
        ))}
      </ol>
    </details>
  )
}

/**
 * Per age group, first grade or 8th-grade class, from `admission_info.cutoff_summary`
 * (with `history` where the page has it, see `withRoundHistory`): the reference ranking
 * (`comparableRound`) with its places and applicants, one line when a later ranking let in
 * a weaker result, and every ranking folded away. Rankings are never shown side by side: a later one only fills places that came
 * free, so two of them read as a trend they are not. `isCurrentRules(row)` says whether
 * a row was ranked under the rules the reader is looking at (true, false, or null when
 * unknown); rows under other rules are marked, never hidden.
 */
function CutoffTable({ rows, isCurrentRules = () => null }) {
  const { t } = useTranslation()
  if (!rows || rows.length === 0) return null
  // kg.sofia.bg keeps only the current year's later rankings, so say once why a main one is missing.
  const mainMissing = rows.some(row => roundsOf(row)[0]?.round_kind === 'current')
  // "Nobody turned down" beside fewer admitted than applied: the rest took a higher wish.
  const shown = rows.flatMap(row => [comparableRound(row), laterEasierRound(row)]).filter(Boolean)
  const higherWish = shown.some(round => cutoffValue(round)?.kind === 'everyone' && round.applicants_count > round.admitted_count)
  return (
    <div>
      <ul aria-label={t('cutoffs.listLabel')} className="divide-y divide-neutral-100 text-sm">
        {rows.map(row => {
          const current = isCurrentRules(row)
          const rounds = roundsOf(row)
          return (
            <li key={[row.stage, row.age_group, row.birth_year, row.class_code, row.building].join('|')} className="py-3">
              <div className="text-neutral-900">{rowLabel(row, t)}</div>
              <div className="text-xs text-neutral-500">
                {shortCampaign(row.campaign)}
                {row.quota && ` · ${t('cutoffs.quota')}`}
                {current === false && ` · ${t('cutoffs.otherRules')}`}
              </div>
              <div className="mt-1"><Headline row={row} rounds={rounds} /></div>
              <Later row={row} />
              <History row={row} rounds={rounds} />
            </li>
          )
        })}
      </ul>
      {(mainMissing || higherWish) && (
        <ul className="mt-2 space-y-1 text-xs text-neutral-500">
          {higherWish && <li>{t('cutoffs.legend.higherWish')}</li>}
          {mainMissing && <li>{t('cutoffs.legend.mainMissing')}</li>}
        </ul>
      )}
    </div>
  )
}

export default CutoffTable
