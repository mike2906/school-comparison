import { useTranslation } from 'react-i18next'

/**
 * Under an empty list while a name is typed: "Младост" or "Студентски град" in the name
 * search most likely means the area, so the typed text is offered as the location.
 */
function AreaHint({ nameQuery, onSearchNear, isGeocoding, locationError }) {
  const { t } = useTranslation()
  const place = nameQuery.trim()
  if (!place) return null
  return (
    <div className="mt-4 text-sm">
      <p className="text-neutral-600">{t('schools.areaHint')}</p>
      <button
        type="button"
        onClick={onSearchNear}
        disabled={isGeocoding}
        className="mt-2 h-10 rounded-lg bg-primary-700 px-4 font-medium text-white hover:bg-primary-800 disabled:opacity-60"
      >
        {t('schools.searchNear', { place })}
      </button>
      {locationError && <p className="mt-2 text-red-600">{locationError}</p>}
    </div>
  )
}

export default AreaHint
