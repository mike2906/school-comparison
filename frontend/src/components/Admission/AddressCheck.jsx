import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { catchmentSchoolFor, searchStreets } from '../../admission/catchments'
import { loadAddresses, loadDistricts } from '../../admission/loadCatchments'

// The district tells apart streets with the same name (two УЛ.ЗЕМЛЯНЕ, in different
// catchments), so it is always shown.
function streetLabel(street, districtNames) {
  const district = districtNames?.get(street.district)
  const place = [street.settlement === 'ГР.СОФИЯ' ? null : street.settlement, district].filter(Boolean).join(', ')
  return place ? `${street.street} (${place})` : street.street
}

/**
 * Street and number → the official first-grade catchment of that address. The address
 * table loads on first use and the lookup runs in the browser; the address is never
 * sent anywhere, stored, or put in the URL.
 *
 * `onResult` gets `{ street, number, schoolId }` (schoolId null when the table does not
 * list the number) or null while the address is incomplete. The school the address
 * belongs to is named unless it is one of `ownIds` or `nameSchool` is false (the page
 * says so itself).
 */
function AddressCheck({ onResult, catchments, ownIds = [], nameSchool = true }) {
  const { t } = useTranslation()
  const id = useId()
  const [streets, setStreets] = useState(null)
  const [failed, setFailed] = useState(false)
  const [query, setQuery] = useState('')
  const [street, setStreet] = useState(null)
  const [number, setNumber] = useState('')

  const [districtNames, setDistrictNames] = useState(null)

  const start = () => {
    if (streets || failed) return
    loadAddresses().then(data => setStreets(data.streets), () => setFailed(true))
    loadDistricts().then(
      data => setDistrictNames(new Map(data.features.map(f => [f.properties.code, f.properties.name]))),
      () => {}
    )
  }

  const suggestions = streets && !street ? searchStreets(streets, query) : []
  const schoolId = street && number.trim() ? catchmentSchoolFor(street, number) : undefined

  useEffect(() => {
    if (!onResult) return
    onResult(street && number.trim() ? { street, number, schoolId: schoolId ?? null } : null)
  }, [street, number, schoolId])

  const schoolName = nameSchool && schoolId && !ownIds.includes(schoolId) ? catchments?.get(schoolId)?.properties.name : null

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-4">
      <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
        <div className="relative">
          <label htmlFor={`${id}-street`} className="block text-sm font-medium text-neutral-800">
            {t('admissionArea.street')}
          </label>
          <input
            id={`${id}-street`}
            type="text"
            autoComplete="off"
            value={street ? streetLabel(street, districtNames) : query}
            onFocus={start}
            onChange={event => {
              setStreet(null)
              setQuery(event.target.value)
              start()
            }}
            placeholder={t('admissionArea.streetPlaceholder')}
            className="mt-1 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
          />
          {suggestions.length > 0 && (
            <ul className="absolute z-10 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-neutral-200 bg-white shadow-lg">
              {suggestions.map(option => (
                <li key={option.id}>
                  <button
                    type="button"
                    className="w-full px-3 py-2 text-left text-sm hover:bg-neutral-100"
                    onClick={() => setStreet(option)}
                  >
                    {streetLabel(option, districtNames)}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <label htmlFor={`${id}-number`} className="block text-sm font-medium text-neutral-800">
            {t('admissionArea.number')}
          </label>
          <input
            id={`${id}-number`}
            type="text"
            inputMode="text"
            autoComplete="off"
            value={number}
            onChange={event => setNumber(event.target.value)}
            className="mt-1 w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm"
          />
        </div>
      </div>
      {failed && <p className="mt-3 text-sm text-red-700">{t('admissionArea.loadFailed')}</p>}
      {streets === null && !failed && query && <p className="mt-3 text-sm text-neutral-500">{t('common.loading')}</p>}
      {schoolId === null && <p className="mt-3 text-sm text-amber-800">{t('admissionArea.numberNotListed')}</p>}
      {schoolName && (
        <p className="mt-3 text-sm text-neutral-800">
          {t('admissionArea.addressBelongsTo', { school: schoolName })}
        </p>
      )}
      <p className="mt-3 text-xs text-neutral-500">{t('admissionArea.addressPrivacy')}</p>
    </div>
  )
}

export default AddressCheck
