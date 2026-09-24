import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

const DISTANCE_OPTIONS = ['any', '2', '5']

function Spinner() {
  return (
    <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24" aria-hidden="true">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  )
}

/**
 * "Near: <address>" above the results. Setting a location (my location, an address
 * submitted with Enter / Search, or a point picked on the map) adds distances, the
 * nearest-first sort and the distance filter.
 */
function LocationControl({
  userLocation,
  addressInput,
  onAddressInputChange,
  onUseMyLocation,
  onAddressSearch,
  onStartMapPick,
  onClear,
  isLocating,
  isGeocoding,
  isPickingLocation,
  locationError,
  distanceFilter,
  onDistanceFilterChange,
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const containerRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    if (!open) return
    inputRef.current?.focus()
    const handlePointer = (event) => {
      if (containerRef.current && !containerRef.current.contains(event.target)) setOpen(false)
    }
    const handleKey = (event) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      setOpen(false)
    }
    document.addEventListener('mousedown', handlePointer)
    document.addEventListener('keydown', handleKey)
    return () => {
      document.removeEventListener('mousedown', handlePointer)
      document.removeEventListener('keydown', handleKey)
    }
  }, [open])

  // Close once a location has been set.
  useEffect(() => {
    if (userLocation) setOpen(false)
  }, [userLocation])

  const shortAddress = userLocation?.address?.split(',').slice(0, 2).join(',').trim()

  return (
    <div ref={containerRef} className="relative flex min-w-0 items-center gap-2">
      <button
        type="button"
        onClick={() => setOpen(value => !value)}
        aria-expanded={open}
        className={`inline-flex h-9 min-w-0 items-center gap-1.5 rounded-lg border px-3 text-sm ${
          userLocation
            ? 'border-primary-200 bg-primary-50 text-primary-800'
            : 'border-dashed border-neutral-300 text-neutral-700 hover:bg-neutral-50'
        }`}
      >
        <span aria-hidden="true">📍</span>
        <span className="truncate">
          {userLocation ? t('location.nearShort', { address: shortAddress }) : t('location.setShort')}
        </span>
      </button>
      {userLocation && (
        <>
          <button
            type="button"
            onClick={onClear}
            className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg text-neutral-500 hover:bg-neutral-100"
            aria-label={t('location.clear')}
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
          <label className="sr-only" htmlFor="distance-filter">{t('location.distanceFilterTitle')}</label>
          <select
            id="distance-filter"
            value={distanceFilter}
            onChange={(event) => onDistanceFilterChange(event.target.value)}
            className="h-9 flex-shrink-0 rounded-lg border border-neutral-300 bg-white px-2 text-sm"
          >
            {DISTANCE_OPTIONS.map(option => (
              <option key={option} value={option}>
                {option === 'any' ? t('location.distanceAny') : t(`location.distance${option}km`)}
              </option>
            ))}
          </select>
        </>
      )}

      {open && (
        <div className="absolute left-0 top-full z-[1200] mt-2 w-[min(360px,calc(100vw-2rem))] space-y-3 rounded-xl border border-neutral-200 bg-white p-4 shadow-xl">
          <p className="text-sm text-neutral-600">{t('location.whyShort')}</p>
          <button
            type="button"
            onClick={onUseMyLocation}
            disabled={isLocating}
            className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-primary-600 px-3 text-sm font-medium text-white hover:bg-primary-700 disabled:opacity-60"
          >
            {isLocating && <Spinner />}
            {t('location.useMyLocation')}
          </button>
          <form
            className="flex gap-2"
            onSubmit={(event) => {
              event.preventDefault()
              onAddressSearch()
            }}
          >
            <label className="sr-only" htmlFor="location-address">{t('location.enterAddress')}</label>
            <input
              id="location-address"
              ref={inputRef}
              value={addressInput}
              onChange={(event) => onAddressInputChange(event.target.value)}
              placeholder={t('location.addressPlaceholder')}
              className="h-10 min-w-0 flex-1 rounded-lg border border-neutral-300 px-3 text-sm"
            />
            <button
              type="submit"
              disabled={isGeocoding || !addressInput.trim()}
              className="inline-flex h-10 items-center gap-2 rounded-lg bg-neutral-900 px-3 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-60"
            >
              {isGeocoding && <Spinner />}
              {t('common.search')}
            </button>
          </form>
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              onStartMapPick()
            }}
            className="h-10 w-full rounded-lg border border-neutral-300 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
          >
            {isPickingLocation ? t('location.cancelMapPick') : t('location.selectOnMap')}
          </button>
          {locationError && <p className="text-sm text-red-600">{locationError}</p>}
          <p className="text-xs text-neutral-500">
            {t('location.attributionPrefix')}{' '}
            <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer" className="underline">
              {t('location.attributionLink')}
            </a>
          </p>
        </div>
      )}
    </div>
  )
}

export default LocationControl
