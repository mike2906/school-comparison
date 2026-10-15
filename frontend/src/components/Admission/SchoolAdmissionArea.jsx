import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import AreaMap from './AreaMap'
import AddressCheck from './AddressCheck'
import {
  addressFactsForSchool,
  areaAtPoint,
  mapSearchQuery,
  streetForLocation,
  districtAt,
  districtByName,
  districtLabel,
  neighbouringCatchments,
  schoolCatchmentIds,
} from '../../admission/catchments'
import { CATCHMENT_CAMPAIGNS, loadAddresses, loadCatchments, loadDistricts } from '../../admission/loadCatchments'
import { saveHome } from '../../admission/saved'
import { useSavedAdmission } from '../../hooks/useSavedAdmission'
import { parseSavedLocation } from '../../utils/keyFacts'
import { usesSofiaKindergartenSystem } from '../../utils/admission'
import { getMappableLocations } from '../SchoolDetailPage/LocationMap'
import { geocodeAddress, reverseGeocodeStreet } from '../../utils/geocoding'

const OFFICIAL_SYSTEM_URL = 'https://kg.sofia.bg/'
// The municipality's own catchment viewer, opened on one school.
const catchmentViewerUrl = id => `https://arcgis.sofia.bg/EsriBg.Sofia.Mun.Schools/?schoolId=${encodeURIComponent(id)}`

function useLoaded(loader, enabled) {
  const [state, setState] = useState({ data: null, failed: false })
  useEffect(() => {
    if (!enabled) return undefined
    let live = true
    loader().then(
      data => live && setState({ data, failed: false }),
      () => live && setState({ data: null, failed: true })
    )
    return () => {
      live = false
    }
  }, [enabled])
  return state
}

function Legend({ items }) {
  return (
    <ul className="mt-3 space-y-1 text-sm text-neutral-700">
      {items.map(item => (
        <li key={item.key} className="flex items-start gap-2">
          <span className={`mt-1 inline-block h-3 w-3 shrink-0 rounded-sm ${item.swatch}`} aria-hidden="true" />
          <span>{item.text}</span>
        </li>
      ))}
    </ul>
  )
}

function Panel({ title, children }) {
  return (
    <section className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <h2 className="text-2xl font-bold text-neutral-900 mb-4">{title}</h2>
      {children}
    </section>
  )
}

/** One sentence: in this school's area or not, and whose area the address is in. */
function AddressVerdict({ facts, schoolName }) {
  const { t } = useTranslation()
  if (!facts || facts.inCatchment == null) return null
  const key = facts.inCatchment ? 'inside' : facts.inNeighbouring ? 'neighbouring' : 'outside'
  const text = !facts.inCatchment && schoolName
    ? t(`admissionArea.verdict.${key}Of`, { school: schoolName })
    : t(`admissionArea.verdict.${key}`)
  return <p className="mt-3 rounded-lg bg-neutral-50 p-3 text-sm text-neutral-900">{text}</p>
}

/**
 * Puts the checked address on the map, only when the parent asks: finding the point
 * sends the address to OpenStreetMap's search (the check itself stays in the browser).
 */
function ShowOnMap({ address, onPin }) {
  const { t } = useTranslation()
  const [state, setState] = useState('idle')
  // A lookup started for an earlier address must not pin it after the address changed.
  const request = useRef(0)
  useEffect(() => {
    request.current += 1
    setState('idle')
    onPin(null)
  }, [address])
  if (!address) return null
  const show = () => {
    const mine = ++request.current
    setState('loading')
    // Not cached locally, unlike the search page's address box.
    geocodeAddress(mapSearchQuery(address.street, address.number), { city_suffix: '', remember: false }).then(
      result => {
        if (mine !== request.current) return
        setState('shown')
        onPin([result.lat, result.lng])
      },
      error => {
        if (mine !== request.current) return
        setState(error?.code === 'CANCELED' ? 'idle' : 'failed')
      }
    )
  }
  return (
    <div className="mt-3 text-sm">
      {state !== 'shown' && (
        <button type="button" onClick={show} disabled={state === 'loading'} className="font-medium text-primary-700 underline disabled:opacity-60">
          {state === 'loading' ? t('common.loading') : t('admissionArea.showOnMap')}
        </button>
      )}
      {state === 'failed' && <p className="mt-1 text-amber-800">{t('admissionArea.showOnMapFailed')}</p>}
      <p className="mt-1 text-xs text-neutral-500">{t(state === 'shown' ? 'admissionArea.showOnMapShown' : 'admissionArea.showOnMapNote')}</p>
    </div>
  )
}

function readSearchLocation() {
  try {
    return parseSavedLocation(window.localStorage.getItem('userLocation'))
  } catch {
    return null
  }
}

/**
 * "Use my current location": the device's position, pinned at once, then the street and
 * number there (OpenStreetMap, on request only) matched to the official address table.
 * The position is kept as the search page's location, like its own "use my location".
 */
function useCurrentLocation({ catchments, onPoint, onAddress }) {
  const [state, setState] = useState('idle')
  const locate = () => {
    if (!navigator.geolocation) {
      setState('unavailable')
      return
    }
    setState('locating')
    navigator.geolocation.getCurrentPosition(
      async position => {
        const { latitude: lat, longitude: lng } = position.coords
        onPoint([lat, lng])
        try {
          window.localStorage.setItem('userLocation', JSON.stringify({ lat, lng, address: null }))
        } catch {
          // Blocked storage: the location is used on this page only.
        }
        try {
          const [{ streets }, found] = await Promise.all([loadAddresses(), reverseGeocodeStreet(lat, lng)])
          const match = streetForLocation(streets, catchments, found, lat, lng)
          if (match) {
            onAddress({ streetId: match.street.id, number: match.number })
            setState('found')
          } else {
            setState('noAddress')
          }
        } catch {
          setState('noAddress')
        }
      },
      error => setState(error?.code === 1 ? 'denied' : 'unavailable'),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 300000 }
    )
  }
  return { state, locate }
}

/**
 * First grade at a Sofia municipal school: its catchment, its neighbours, an address
 * check. The check starts from the address saved on this device; without one, a location
 * saved on the search page is pinned with a hint, and "use my current location" comes
 * before typing an address.
 */
function CatchmentArea({ ids, point }) {
  const { t } = useTranslation()
  const { data: catchments, failed } = useLoaded(loadCatchments, true)
  const savedHome = useSavedAdmission().home
  const [address, setAddress] = useState(null)
  const [found, setFound] = useState(null)
  const [pin, setPin] = useState(null)
  // A pin from a location (not a checked address): only an approximate answer.
  const [locationPin, setLocationPin] = useState(() => {
    const saved = readSearchLocation()
    return saved ? [saved.lat, saved.lng] : null
  })
  const current = useCurrentLocation({
    catchments: catchments?.byId,
    onPoint: point => setLocationPin(point),
    onAddress: setFound,
  })

  const areas = useMemo(() => {
    if (!catchments) return null
    const own = ids.map(id => catchments.byId.get(id)).filter(Boolean)
    const neighbours = ids
      .flatMap(id => neighbouringCatchments(catchments.byId, id))
      .filter((feature, index, all) => !ids.includes(feature.properties.id) && all.indexOf(feature) === index)
    return { own, neighbours }
  }, [catchments, ids])

  const title = t('admissionArea.catchmentTitle')
  if (failed) return <Panel title={title}><p className="text-sm text-red-700">{t('admissionArea.loadFailed')}</p></Panel>
  if (!areas) return <Panel title={title}><p className="text-sm text-neutral-500">{t('common.loading')}</p></Panel>
  // An id the snapshot does not know: no section rather than an empty one.
  if (areas.own.length === 0) return null

  // A merged school can have several buildings, each with its own catchment.
  const facts = address?.schoolId
    ? ids
      .map(id => addressFactsForSchool(catchments.byId, address.schoolId, id))
      .reduce((best, f) => (f.inCatchment ? f : best.inCatchment || best.inNeighbouring ? best : f))
    : null
  const startValue = found || savedHome
  const usingSaved = Boolean(savedHome && !found && address && address.streetId === savedHome.streetId && address.number === savedHome.number)
  const canRemember = Boolean(address?.schoolId && !(savedHome && address.streetId === savedHome.streetId && address.number === savedHome.number))
  const hint = !address && locationPin ? areaAtPoint(areas.own, areas.neighbours, locationPin[0], locationPin[1]) : null

  return (
    <Panel title={title}>
      <p className="text-sm text-neutral-700">{t('admissionArea.catchmentIntro')}</p>
      <div className="mt-4">
        <AreaMap primary={areas.own} secondary={areas.neighbours} point={point} pin={pin || (address ? null : locationPin)} />
      </div>
      <Legend
        items={[
          { key: 'own', swatch: 'bg-teal-400', text: t('admissionArea.legendOwn') },
          { key: 'neighbours', swatch: 'bg-amber-300', text: t('admissionArea.legendNeighbours') },
          { key: 'outside', swatch: 'border border-neutral-300 bg-white', text: t('admissionArea.legendOutside') },
        ]}
      />
      <h3 className="mt-6 text-lg font-semibold text-neutral-900">{t('admissionArea.checkTitle')}</h3>
      {!savedHome && (
        <div className="mt-2">
          <button
            type="button"
            onClick={current.locate}
            disabled={current.state === 'locating'}
            className="inline-flex h-11 w-full items-center justify-center rounded-lg bg-primary-700 px-4 text-sm font-medium text-white hover:bg-primary-800 disabled:opacity-60 sm:w-auto"
          >
            {current.state === 'locating' ? t('common.loading') : t('location.useMyLocation')}
          </button>
          <p className="mt-1 text-xs text-neutral-500">{t('admissionArea.locationNote')}</p>
          {(current.state === 'denied' || current.state === 'unavailable') && (
            <p className="mt-1 text-sm text-amber-800">{t(current.state === 'denied' ? 'location.errorDenied' : 'location.errorUnavailable')}</p>
          )}
          <p className="mt-3 text-sm text-neutral-600">{t('admissionArea.orTypeAddress')}</p>
        </div>
      )}
      <div className="mt-2">
        <AddressCheck catchments={catchments.byId} ownIds={ids} onResult={setAddress} nameSchool={false} value={startValue} />
      </div>
      {hint && (
        <p className="mt-3 rounded-lg bg-neutral-50 p-3 text-sm text-neutral-800">
          {t(`admissionArea.locationHint.${hint}`)}{' '}
          {t(current.state === 'noAddress' ? 'admissionArea.locationNoAddress' : 'admissionArea.locationConfirm')}
        </p>
      )}
      <AddressVerdict facts={facts} schoolName={address?.schoolId ? catchments.byId.get(address.schoolId)?.properties.name : null} />
      {usingSaved && (
        <p className="mt-2 text-xs text-neutral-600">
          {t('admissionArea.usingSaved')}{' '}
          <button type="button" onClick={() => saveHome(null)} className="underline">{t('admissionArea.forgetAddress')}</button>
        </p>
      )}
      {canRemember && (
        <button
          type="button"
          onClick={() => saveHome({ streetId: address.streetId, number: address.number })}
          className="mt-2 text-sm font-medium text-primary-700 underline"
        >
          {t('admissionArea.remember')}
        </button>
      )}
      <ShowOnMap address={address} onPin={setPin} />
      <p className="mt-4 text-xs text-neutral-500">
        {t('admissionArea.catchmentSource', { campaign: CATCHMENT_CAMPAIGNS[0] })}{' '}
        <a href={catchmentViewerUrl(ids[0])} target="_blank" rel="noreferrer" className="underline">arcgis.sofia.bg</a>
      </p>
    </Panel>
  )
}

/** Sofia municipal kindergartens: no catchment, a district bonus. */
function DistrictArea({ school, point }) {
  const { t, i18n } = useTranslation()
  const { data: districts, failed } = useLoaded(loadDistricts, true)
  const title = t('admissionArea.districtTitle')
  if (failed) return <Panel title={title}><p className="text-sm text-red-700">{t('admissionArea.loadFailed')}</p></Panel>
  if (!districts) return <Panel title={title}><p className="text-sm text-neutral-500">{t('common.loading')}</p></Panel>

  const district = districtByName(districts, school.admission_info?.admin_district) ||
    (point ? districtAt(districts, point[0], point[1]) : null)
  if (!district) return null

  return (
    <Panel title={title}>
      <p className="text-sm text-neutral-700">
        {t('admissionArea.districtIntro', { district: districtLabel(district.properties.name, i18n.language) })}
      </p>
      <div className="mt-4">
        <AreaMap primary={[district]} point={point} />
      </div>
      <p className="mt-4 text-xs text-neutral-500">
        {t('admissionArea.districtSource')}{' '}
        <a href={OFFICIAL_SYSTEM_URL} target="_blank" rel="noreferrer" className="underline">kg.sofia.bg</a>
      </p>
    </Panel>
  )
}

/**
 * "Where do you need to live?" on a school page: the official first-grade catchment for
 * Sofia municipal schools, the district bonus area for Sofia municipal kindergartens.
 */
function SchoolAdmissionArea({ school }) {
  const ids = schoolCatchmentIds(school)
  const isKindergarten = usesSofiaKindergartenSystem(school)
  const location = getMappableLocations(school.locations).find(p => p.location.is_primary) ||
    getMappableLocations(school.locations)[0]
  const point = location ? [location.lat, location.lng] : null

  // Kindergartens have no first-grade catchment, whatever ids they carry.
  if (isKindergarten) return <DistrictArea school={school} point={point} />
  if (ids.length > 0) return <CatchmentArea ids={ids} point={point} />
  return null
}

export default SchoolAdmissionArea
