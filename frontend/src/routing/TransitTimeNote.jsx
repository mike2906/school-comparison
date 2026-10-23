import { useTranslation } from 'react-i18next'
import { serviceDay } from './transitTimes'
import { intlLocale } from '../utils/format'

/** One line under the list: what the public transport times assume, and the data credits. */
function TransitTimeNote({ status, serviceDate }) {
  const { t, i18n } = useTranslation()
  if (status === 'idle') return null
  if (status === 'loading') return <p className="text-xs text-neutral-500">{t('transitTime.loading')}</p>
  if (status === 'outside') return <p className="text-xs text-neutral-500">{t('transitTime.outside')}</p>
  if (status === 'unavailable') return <p className="text-xs text-neutral-500">{t('transitTime.unavailable')}</p>
  const day = serviceDate
    ? new Date(`${serviceDay(serviceDate)}T12:00:00Z`).toLocaleDateString(intlLocale(i18n.language), { weekday: 'long', day: 'numeric', month: 'long', timeZone: 'UTC' })
    : ''
  return (
    <p className="text-xs text-neutral-500">
      {t('transitTime.note', { day })}{' '}
      <a href="https://www.sofiatraffic.bg/" target="_blank" rel="noopener noreferrer" className="underline hover:text-neutral-700">
        {t('transitTime.timetableCredit')}
      </a>
      {', '}
      <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer" className="underline hover:text-neutral-700">
        {t('transitTime.licence')}
      </a>
      {'; '}
      <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer" className="underline hover:text-neutral-700">
        {t('transitTime.walkCredit')}
      </a>
    </p>
  )
}

export default TransitTimeNote
