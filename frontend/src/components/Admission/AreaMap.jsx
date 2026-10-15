import { useEffect, useMemo } from 'react'
import { CircleMarker, GeoJSON, MapContainer, TileLayer, useMap } from 'react-leaflet'
import { useTranslation } from 'react-i18next'
import 'leaflet/dist/leaflet.css'

// Same CARTO tiles and key handling as the other maps (see Map/SchoolMap.jsx).
const CARTO_API_KEY = import.meta.env.VITE_CARTO_API_KEY
const TILE_URL = `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png${
  CARTO_API_KEY ? `?key=${encodeURIComponent(CARTO_API_KEY)}` : ''
}`

export const AREA_STYLES = {
  primary: { color: '#0f766e', weight: 2, fillColor: '#14b8a6', fillOpacity: 0.35 },
  secondary: { color: '#d97706', weight: 1, fillColor: '#fbbf24', fillOpacity: 0.18, dashArray: '4 3' },
}

function boundsOf(features) {
  let south = Infinity, west = Infinity, north = -Infinity, east = -Infinity
  const visit = coordinates => {
    if (typeof coordinates[0] === 'number') {
      const [lng, lat] = coordinates
      south = Math.min(south, lat); north = Math.max(north, lat)
      west = Math.min(west, lng); east = Math.max(east, lng)
      return
    }
    coordinates.forEach(visit)
  }
  features.forEach(feature => visit(feature.geometry.coordinates))
  return Number.isFinite(south) ? [[south, west], [north, east]] : null
}

/** Widens the view to show `pin` together with the area, when the parent placed one. */
function FitPin({ bounds, pin }) {
  const map = useMap()
  useEffect(() => {
    if (!pin) return
    map.fitBounds([...bounds, pin], { padding: [24, 24], maxZoom: 16 })
  }, [map, bounds, pin])
  return null
}

/**
 * A school's admission area: its own area in a strong colour, related areas (the
 * neighbouring catchments) in a light one, and the school's location. The view fits the
 * school's own area; `pin` (an address the parent chose to show) widens it to include
 * that point.
 */
function AreaMap({ primary = [], secondary = [], point = null, pin = null, label }) {
  const { t } = useTranslation()
  const bounds = useMemo(() => boundsOf(primary.length ? primary : secondary), [primary, secondary])
  if (!bounds) return null

  return (
    <div
      className="relative z-0 h-64 md:h-80 w-full overflow-hidden rounded-xl border border-neutral-200"
      role="region"
      aria-label={label || t('admissionArea.mapLabel')}
    >
      <MapContainer
        bounds={bounds}
        boundsOptions={{ padding: [16, 16] }}
        className="h-full w-full"
        scrollWheelZoom={false}
        dragging={!('ontouchstart' in window)}
        doubleClickZoom={false}
        attributionControl
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> | &copy; <a href="https://carto.com/">CARTO</a> | Столична община'
          url={TILE_URL}
          subdomains="abcd"
          maxZoom={20}
        />
        {secondary.map(feature => (
          <GeoJSON key={`s-${feature.properties.id ?? feature.properties.code}`} data={feature} style={AREA_STYLES.secondary} />
        ))}
        {primary.map(feature => (
          <GeoJSON key={`p-${feature.properties.id ?? feature.properties.code}`} data={feature} style={AREA_STYLES.primary} />
        ))}
        {point && (
          <CircleMarker
            center={point}
            radius={8}
            pathOptions={{ color: '#ffffff', weight: 3, fillColor: '#0f172a', fillOpacity: 1 }}
          />
        )}
        {pin && (
          <CircleMarker
            center={pin}
            radius={9}
            pathOptions={{ color: '#ffffff', weight: 3, fillColor: '#dc2626', fillOpacity: 1 }}
          />
        )}
        <FitPin bounds={bounds} pin={pin} />
      </MapContainer>
    </div>
  )
}

export default AreaMap
