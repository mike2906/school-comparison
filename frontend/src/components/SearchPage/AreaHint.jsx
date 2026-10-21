import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

/**
 * Under an empty list while a name is typed: "Младост" or "Студентски град" in the name
 * search most likely means the area, so the typed text is offered as the location.
 * `active` is false when the name matches schools in the list (the list is then empty only
 * because of other filters such as distance).
 */
function AreaHint({ nameQuery, active, searchAddress, setNameQuery, isGeocoding, locationError }) {
  const { t } = useTranslation()
  const place = nameQuery.trim()
  // Only this button's own failed lookup shows its error here.
  const [failedFor, setFailedFor] = useState(null)
  useEffect(() => setFailedFor(null), [place])
  if (!place || !active) return null
  const search = async () => {
    setFailedFor(null)
    const found = await searchAddress(place)
    // Text typed meanwhile stays.
    if (found) setNameQuery(current => (current.trim() === place ? '' : current))
    // null: replaced by another lookup, whose error is not this button's.
    else if (found === false) setFailedFor(place)
  }
  return (
    <div className="mt-4 text-sm">
      <p className="text-neutral-600">{t('schools.areaHint')}</p>
      <button
        type="button"
        onClick={search}
        disabled={Boolean(isGeocoding)}
        className="mt-2 h-10 rounded-lg bg-primary-700 px-4 font-medium text-white hover:bg-primary-800 disabled:opacity-60"
      >
        {t('schools.searchNear', { place })}
      </button>
      {failedFor === place && locationError && <p className="mt-2 text-red-600">{locationError}</p>}
    </div>
  )
}

export default AreaHint
