import { useTranslation } from 'react-i18next'

/** One line under the location: what the drive times mean, and the road data credit. */
function DriveTimeNote({ status }) {
  const { t } = useTranslation()
  if (status === 'idle') return null
  if (status === 'loading') return <p className="text-xs text-neutral-500">{t('driveTime.loading')}</p>
  if (status === 'outside') return <p className="text-xs text-neutral-500">{t('driveTime.outside')}</p>
  if (status === 'unavailable') return <p className="text-xs text-neutral-500">{t('driveTime.unavailable')}</p>
  return (
    <p className="text-xs text-neutral-500">
      {t('driveTime.note')}{' '}
      <a
        href="https://www.openstreetmap.org/copyright"
        target="_blank"
        rel="noopener noreferrer"
        className="underline hover:text-neutral-700"
      >
        {t('driveTime.attribution')}
      </a>
    </p>
  )
}

export default DriveTimeNote
