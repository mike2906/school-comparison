import i18next from 'i18next'
import { useTranslation } from 'react-i18next'
import { comparableRound, cutoffValue, formatRankingDay, laterEasierRound, roundsOf, shortCampaign } from '../../admission/calculator'
import { formatAmount } from '../../utils/format'

// First grade: groups 1–3 live in the school's area (by how long), group 4 outside it.
const GRADE_1_OUTSIDE_GROUP = 4
const LAST_ADMITTED_KINDS = new Set(['score', 'points', 'group', 'quota'])

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
  if (!value) {
    // Places to fill, and nobody on the list was admitted to them.
    if (round?.places > 0 && round.admitted_count === 0) return <span>{t('cutoffs.noneAdmitted')}</span>
    return <span className="text-neutral-400">—</span>
  }
  if (value.kind === 'everyone') return <span>{round.applicants_count === 0 ? t('cutoffs.noApplicants') : t('cutoffs.everyone')}</span>
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

/** The size of a round in a few words: "66 places · 110 applicants · 66 admitted". */
function roundCounts(round, t) {
  return [
    round.places > 0 ? t('cutoffs.places', { count: round.places }) : null,
    round.places > 0 && round.applicants_count != null ? t('cutoffs.applicants', { count: round.applicants_count }) : null,
    round.places > 0 && round.admitted_count != null ? t('cutoffs.admitted', { count: round.admitted_count }) : null,
  ].filter(Boolean).join(' · ')
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
  const label = round.round_kind === 'current'
    ? t('cutoffs.ref.free', { date: day })
    : t(round === row.first ? 'cutoffs.ref.main' : 'cutoffs.ref.other', { date: day })
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
      {/* Read in date order, a later, higher cut-off looks like the bar went up (2026-10-21 trial: 364, 377). */}
      <p className="mt-1 pl-4">{t('cutoffs.historyNote')}</p>
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
