import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import AreaMap from './AreaMap'
import AddressCheck from './AddressCheck'
import {
  addressFactsForSchool,
  districtAt,
  districtByName,
  neighbouringCatchments,
  schoolCatchmentIds,
} from '../../admission/catchments'
import { CATCHMENT_CAMPAIGNS, loadCatchments, loadDistricts } from '../../admission/loadCatchments'
import { usesSofiaKindergartenSystem } from '../../utils/admission'
import { getMappableLocations } from '../SchoolDetailPage/LocationMap'

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

function AddressVerdict({ facts }) {
  const { t } = useTranslation()
  if (!facts || facts.inCatchment == null) return null
  const key = facts.inCatchment ? 'inside' : facts.inNeighbouring ? 'neighbouring' : 'outside'
  return <p className="mt-3 rounded-lg bg-neutral-50 p-3 text-sm text-neutral-900">{t(`admissionArea.verdict.${key}`)}</p>
}

/** First grade at a Sofia municipal school: its catchment, its neighbours, an address check. */
function CatchmentArea({ ids, point }) {
  const { t } = useTranslation()
  const { data: catchments, failed } = useLoaded(loadCatchments, true)
  const [address, setAddress] = useState(null)

  const areas = useMemo(() => {
    if (!catchments) return null
    const own = ids.map(id => catchments.byId.get(id)).filter(Boolean)
    const neighbours = ids
      .flatMap(id => neighbouringCatchments(catchments.byId, id))
      .filter((feature, index, all) => !ids.includes(feature.properties.id) && all.indexOf(feature) === index)
    return { own, neighbours }
  }, [catchments, ids])

  if (failed) return <p className="text-sm text-red-700">{t('admissionArea.loadFailed')}</p>
  if (!areas) return <p className="text-sm text-neutral-500">{t('common.loading')}</p>
  if (areas.own.length === 0) return null

  // A merged school can have several buildings, each with its own catchment.
  const facts = address?.schoolId
    ? ids
      .map(id => addressFactsForSchool(catchments.byId, address.schoolId, id))
      .reduce((best, f) => (f.inCatchment ? f : best.inCatchment || best.inNeighbouring ? best : f))
    : null

  return (
    <>
      <p className="text-sm text-neutral-700">{t('admissionArea.catchmentIntro')}</p>
      <div className="mt-4">
        <AreaMap primary={areas.own} secondary={areas.neighbours} point={point} />
      </div>
      <Legend
        items={[
          { key: 'own', swatch: 'bg-teal-400', text: t('admissionArea.legendOwn') },
          { key: 'neighbours', swatch: 'bg-amber-300', text: t('admissionArea.legendNeighbours') },
          { key: 'outside', swatch: 'border border-neutral-300 bg-white', text: t('admissionArea.legendOutside') },
        ]}
      />
      <h3 className="mt-6 text-lg font-semibold text-neutral-900">{t('admissionArea.checkTitle')}</h3>
      <div className="mt-2">
        <AddressCheck catchments={catchments.byId} ownIds={ids} onResult={setAddress} />
      </div>
      <AddressVerdict facts={facts} />
      <p className="mt-4 text-xs text-neutral-500">
        {t('admissionArea.catchmentSource', { campaign: CATCHMENT_CAMPAIGNS[0] })}{' '}
        <a href={catchmentViewerUrl(ids[0])} target="_blank" rel="noreferrer" className="underline">arcgis.sofia.bg</a>
      </p>
    </>
  )
}

/** Sofia municipal kindergartens: no catchment, a district bonus. */
function DistrictArea({ school, point }) {
  const { t } = useTranslation()
  const { data: districts, failed } = useLoaded(loadDistricts, true)
  if (failed) return <p className="text-sm text-red-700">{t('admissionArea.loadFailed')}</p>
  if (!districts) return <p className="text-sm text-neutral-500">{t('common.loading')}</p>

  const district = districtByName(districts, school.admission_info?.admin_district) ||
    (point ? districtAt(districts, point[0], point[1]) : null)
  if (!district) return null

  return (
    <>
      <p className="text-sm text-neutral-700">
        {t('admissionArea.districtIntro', { district: district.properties.name })}
      </p>
      <div className="mt-4">
        <AreaMap primary={[district]} point={point} />
      </div>
      <p className="mt-4 text-xs text-neutral-500">
        {t('admissionArea.districtSource')}{' '}
        <a href={OFFICIAL_SYSTEM_URL} target="_blank" rel="noreferrer" className="underline">kg.sofia.bg</a>
      </p>
    </>
  )
}

/**
 * "Where do you need to live?" on a school page: the official first-grade catchment for
 * Sofia municipal schools, the district bonus area for Sofia municipal kindergartens.
 */
function SchoolAdmissionArea({ school }) {
  const { t } = useTranslation()
  const ids = schoolCatchmentIds(school)
  const isKindergarten = usesSofiaKindergartenSystem(school)
  const location = getMappableLocations(school.locations).find(p => p.location.is_primary) ||
    getMappableLocations(school.locations)[0]
  const point = location ? [location.lat, location.lng] : null

  if (ids.length === 0 && !isKindergarten) return null

  return (
    <section className="bg-white rounded-2xl shadow-card border border-neutral-200 p-6 md:p-8 mb-6">
      <h2 className="text-2xl font-bold text-neutral-900 mb-4">
        {t(ids.length > 0 ? 'admissionArea.catchmentTitle' : 'admissionArea.districtTitle')}
      </h2>
      {ids.length > 0 ? <CatchmentArea ids={ids} point={point} /> : <DistrictArea school={school} point={point} />}
    </section>
  )
}

export default SchoolAdmissionArea
