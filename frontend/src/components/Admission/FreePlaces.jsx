import { useTranslation } from 'react-i18next'
import { formatRankingDay, rowLabel, shortCampaign } from '../../admission/calculator'
import { useFreePlaces } from '../../hooks/useFreePlaces'
import { schoolFreePlaces } from '../../utils/freePlaces'

/** The English building names the cut-off rows carry (translate-texts), by Bulgarian name. */
function englishBuildings(school) {
  return new Map((school.admission_info?.cutoff_summary || [])
    .filter(row => row.building && row.building_en)
    .map(row => [row.building, row.building_en]))
}

/**
 * "Free places today" on a municipal kindergarten's or school's page: per group (and
 * building), the places ИСОДЗ shows as free in the general queue, from the latest
 * snapshot (docs/FREE_PLACES.md). Hidden when there is none or it is too old.
 */
function FreePlaces({ school }) {
  const { t, i18n } = useTranslation()
  // Only schools ИСОДЗ lists (municipal kindergartens, school ПГУ) can have places.
  const data = useFreePlaces((school.admission_info?.kg_sofia_ids || []).length > 0)
  const entries = schoolFreePlaces(data, school.id)
  if (entries.length === 0) return null
  const english = String(i18n.language || '').startsWith('en')
  const englishNames = englishBuildings(school)
  // A school with several buildings: one list per building, under its name.
  const byBuilding = new Map()
  for (const entry of entries) byBuilding.set(entry.building || null, [...(byBuilding.get(entry.building || null) || []), entry])
  const buildings = [...byBuilding.entries()]
  const campaigns = new Set(entries.map(entry => entry.campaign))
  const side = entries.some(entry => entry.free_sop || entry.free_chronic)

  return (
    <section className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6" aria-labelledby="free-places-title">
      <h2 id="free-places-title" className="text-2xl font-bold text-neutral-900 mb-2">{t('freePlaces.title')}</h2>
      <p className="text-sm text-neutral-600">
        {t('freePlaces.intro', { date: formatRankingDay(data.as_of, i18n.language) })}
      </p>
      {buildings.map(([building, rows]) => (
        <div key={building || ''} className="mt-4">
          {building && <h3 className="text-sm font-semibold text-neutral-800">{(english && englishNames.get(building)) || building}</h3>}
          <ul aria-label={t('freePlaces.listLabel')} className="divide-y divide-neutral-100 text-sm">
            {rows.map(entry => (
              <li key={[entry.campaign, entry.birth_year, entry.stage].join('|')} className="flex items-start justify-between gap-4 py-3">
                <div className="min-w-0">
                  <div className="text-neutral-900">{rowLabel({ ...entry, building: null }, t, i18n.language)}</div>
                  <div className="text-xs text-neutral-500">
                    {campaigns.size > 1 && `${shortCampaign(entry.campaign)} · `}
                    {t('freePlaces.capacity', { count: entry.capacity, enrolled: entry.enrolled ?? 0 })}
                    {entry.ranked_not_enrolled > 0 && ` · ${t('freePlaces.rankedNotEnrolled', { count: entry.ranked_not_enrolled })}`}
                  </div>
                  {(entry.free_sop || entry.free_chronic) && (
                    <div className="text-xs text-neutral-500">
                      {[
                        entry.free_sop && t('freePlaces.sop', { count: entry.free_sop }),
                        entry.free_chronic && t('freePlaces.chronic', { count: entry.free_chronic }),
                      ].filter(Boolean).join(' · ')}
                    </div>
                  )}
                </div>
                <div className={`shrink-0 text-right font-semibold tabular-nums ${entry.free > 0 ? 'text-teal-700' : 'text-neutral-500'}`}>
                  {entry.free > 0 ? t('freePlaces.free', { count: entry.free }) : t('freePlaces.full')}
                </div>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <ul className="mt-2 space-y-1 text-xs text-neutral-500">
        <li>{t('freePlaces.howTheyFill')}</li>
        {side && <li>{t('freePlaces.sideQueues')}</li>}
        <li>
          {t('freePlaces.source')}{' '}
          <a href={data.source_url || 'https://kg.sofia.bg/spots'} className="underline hover:text-teal-700" target="_blank" rel="noopener noreferrer">kg.sofia.bg</a>
        </li>
      </ul>
    </section>
  )
}

export default FreePlaces
