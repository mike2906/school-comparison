import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { CONTACT_EMAIL } from '../Layout/Layout'
import { formatAmount } from '../../utils/format'
import { getAddress } from '../../utils/i18n'
import { outdoorEntries, outdoorWebsiteFacts, usesOpenStreetMap } from '../../utils/outdoorSpace'

const linkClass = 'text-primary-700 hover:text-primary-800 underline underline-offset-2'

// "Outdoor space": per building, what a visit, the school's website, OpenStreetMap or the
// register address shows, with its source. Sources only ever add what they show: a school
// none of them covers reads "Not known", with a way to send us a photo.
function OutdoorSpace({ school }) {
  const { t, i18n } = useTranslation()
  const language = i18n.language
  const entries = outdoorEntries(school, language)
  if (entries.length === 0) return null
  const websiteFacts = outdoorWebsiteFacts(school, language)
  const several = entries.length > 1
  const name = school.resolved_name_i18n?.[language] || school.resolved_name_i18n?.bg || ''
  const photoHref = `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(t('outdoor.photoSubject', { name }))}`

  return (
    <div className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6" data-testid="outdoor-space">
      <h2 className="text-2xl font-bold text-neutral-900 mb-4">{t('outdoor.title')}</h2>
      <ul className="space-y-4">
        {entries.map(entry => (
          <li key={entry.locationId}>
            {several && entry.location && (
              <div className="text-xs font-medium uppercase tracking-wide text-neutral-500 mb-1">
                {getAddress(entry.location, language)}
              </div>
            )}
            <OutdoorAnswer entry={entry} photoHref={photoHref} />
          </li>
        ))}
      </ul>
      {websiteFacts.length > 0 && (
        <div className="mt-5">
          <p className="text-sm font-medium text-neutral-700 mb-1">{t('outdoor.websiteSays')}</p>
          <FactList facts={websiteFacts} />
        </div>
      )}
      {usesOpenStreetMap(entries) && (
        <p className="mt-5 text-xs text-neutral-500">
          {t('outdoor.osmCredit')}{' '}
          <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer" className={linkClass}>
            © OpenStreetMap
          </a>
        </p>
      )}
    </div>
  )
}

function OutdoorAnswer({ entry, photoHref }) {
  const { t } = useTranslation()
  const sourceNote = text => <p className="text-sm text-neutral-500 mt-1">{text}</p>

  if (entry.kind === 'grounds' && entry.source === 'visit') {
    return (
      <>
        <p className="text-neutral-800">{entry.visitText || t('outdoor.kinds.grounds')}</p>
        <MapDetails entry={entry} />
        {sourceNote(t('outdoor.sources.visit', { date: entry.checkedOn }))}
      </>
    )
  }
  if (entry.kind === 'grounds' && entry.source === 'website') {
    return (
      <>
        <p className="text-neutral-800">{t('outdoor.websiteSays')}</p>
        <FactList facts={entry.facts} />
        <MapDetails entry={entry} />
      </>
    )
  }
  if (entry.kind === 'grounds') {
    return (
      <>
        <p className="text-neutral-800">{t('outdoor.kinds.grounds')}</p>
        <MapDetails entry={entry} />
        {sourceNote(t('outdoor.sources.osm'))}
      </>
    )
  }
  if (entry.kind === 'shared') {
    const other = entry.sharedWithId ? (
      <Link to={`/schools/${entry.sharedWithId}`} className={linkClass}>{entry.sharedWith}</Link>
    ) : entry.sharedWith
    return (
      <>
        <p className="text-neutral-800">
          {t('outdoor.sharedWith')} {other}. {t('outdoor.sharedUseUnknown')}
        </p>
        {sourceNote(t('outdoor.sources.osm'))}
      </>
    )
  }
  if (entry.kind === 'other_building' || entry.kind === 'floor') {
    return (
      <>
        <p className="text-neutral-800">{t(`outdoor.kinds.${entry.kind}`)}</p>
        {sourceNote(t('outdoor.sources.address'))}
      </>
    )
  }
  if (entry.kind === 'none') {
    return (
      <>
        <p className="text-neutral-800">{entry.visitText || t('outdoor.kinds.none')}</p>
        {sourceNote(t('outdoor.sources.visit', { date: entry.checkedOn }))}
      </>
    )
  }
  return (
    <p className="text-neutral-800">
      {t('outdoor.kinds.unknown')}{' '}
      <a href={photoHref} className={linkClass}>{t('outdoor.sendPhoto')}</a>
    </p>
  )
}

// What the mapped plot adds: its open ground, pitches and playgrounds.
function MapDetails({ entry }) {
  const { t, i18n } = useTranslation()
  const parts = []
  if (entry.openM2) parts.push(t('outdoor.openGround', { area: formatAmount(entry.openM2, i18n.language) }))
  if (entry.pitches) parts.push(t('outdoor.pitches', { count: entry.pitches }))
  if (entry.playgrounds) parts.push(t('outdoor.playgrounds', { count: entry.playgrounds }))
  if (parts.length === 0) return null
  return (
    <p className="text-neutral-700 mt-1">
      {entry.source !== 'osm' && `${t('outdoor.onTheMap')} `}
      {parts.join(', ')}
    </p>
  )
}

function FactList({ facts }) {
  const { t } = useTranslation()
  return (
    <ul className="space-y-1 mt-1">
      {facts.map((fact, index) => (
        <li key={index} className="text-neutral-800">
          {fact.text}{' '}
          {fact.sourceUrl && (
            <a href={fact.sourceUrl} target="_blank" rel="noopener noreferrer nofollow" className={`text-sm whitespace-nowrap ${linkClass}`}>
              {t('schoolFacts.source')}
            </a>
          )}
        </li>
      ))}
    </ul>
  )
}

export default OutdoorSpace
