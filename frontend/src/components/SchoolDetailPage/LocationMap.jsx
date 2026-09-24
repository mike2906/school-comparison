import { useMemo } from 'react'
import { MapContainer, TileLayer, CircleMarker, Tooltip } from 'react-leaflet'
import { useTranslation } from 'react-i18next'
import 'leaflet/dist/leaflet.css'
import { getAddress } from '../../utils/i18n'
import { parseCoordinate } from './helpers'

// Same CARTO tiles and key handling as the search map (see Map/SchoolMap.jsx).
const CARTO_API_KEY = import.meta.env.VITE_CARTO_API_KEY
const TILE_URL = `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png${
  CARTO_API_KEY ? `?key=${encodeURIComponent(CARTO_API_KEY)}` : ''
}`
const SINGLE_POINT_ZOOM = 15

/** Locations with usable coordinates, as `{ id, lat, lng, location }`. */
export function getMappableLocations(locations = []) {
  return (locations || [])
    .map((location, idx) => ({
      id: location.id ?? idx,
      lat: parseCoordinate(location.lat),
      lng: parseCoordinate(location.lng),
      location,
    }))
    .filter(point => Number.isFinite(point.lat) && Number.isFinite(point.lng))
}

export function directionsUrl(lat, lng) {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`
}

/**
 * A small, mostly static map of a school's locations. Scroll-zoom and touch dragging are
 * off so the map never traps a page scroll; the zoom buttons still work.
 */
function LocationMap({ locations }) {
  const { t, i18n } = useTranslation()
  const points = useMemo(() => getMappableLocations(locations), [locations])

  if (points.length === 0) return null

  const single = points.length === 1
  const mapProps = single
    ? { center: [points[0].lat, points[0].lng], zoom: SINGLE_POINT_ZOOM }
    : { bounds: points.map(point => [point.lat, point.lng]), boundsOptions: { padding: [30, 30], maxZoom: 15 } }

  return (
    <div
      className="relative z-0 h-48 md:h-60 w-full overflow-hidden rounded-xl border border-neutral-200 mb-6"
      role="region"
      aria-label={t('schoolDetail.mapLabel')}
    >
      <MapContainer
        {...mapProps}
        className="h-full w-full"
        scrollWheelZoom={false}
        dragging={!('ontouchstart' in window)}
        doubleClickZoom={false}
        attributionControl
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> | &copy; <a href="https://carto.com/">CARTO</a>'
          url={TILE_URL}
          subdomains="abcd"
          maxZoom={20}
        />
        {points.map(point => (
          <CircleMarker
            key={point.id}
            center={[point.lat, point.lng]}
            radius={9}
            pathOptions={{ color: '#ffffff', weight: 3, fillColor: '#0d9488', fillOpacity: 1 }}
          >
            {!single && (
              <Tooltip direction="top" offset={[0, -8]}>
                {getAddress(point.location, i18n.language)}
              </Tooltip>
            )}
          </CircleMarker>
        ))}
      </MapContainer>
    </div>
  )
}

export default LocationMap
