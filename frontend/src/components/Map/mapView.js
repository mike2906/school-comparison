import L from 'leaflet'
import '../../utils/leafletGuard'
import {
  pointsForFit,
  fitKeepRatio,
  fitPadding,
  pinTarget,
} from '../../utils/mapFit'


// CARTO requires an API key; without one every tile is watermarked
export const CARTO_API_KEY = import.meta.env.VITE_CARTO_API_KEY
export const TILE_URL = `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png${
  CARTO_API_KEY ? `?key=${encodeURIComponent(CARTO_API_KEY)}` : ''
}`

// Fallback values (used when country config hasn't loaded yet)
export const FALLBACK_CENTER = [42.6977, 23.3219]
export const FALLBACK_ZOOM = 12
export const FALLBACK_BOUNDS = L.latLngBounds([41.235, 22.357], [44.216, 28.887])

export const SINGLE_POINT_ZOOM = 13
// Pins are shown one by one from this zoom; at 14 they still overlap heavily in the centre.
export const UNCLUSTERED_ZOOM = 15
// A fit may zoom in until pins show one by one: stopping a level short left a school's
// two nearby locations as a "2" cluster after searching for it by name.
export const MAX_FIT_ZOOM = UNCLUSTERED_ZOOM
export const MARKER_CLICK_GUARD_MS = 350
// Room a clicked pin needs around it (px) to stay where it is: the popup opens above it
// on desktop, the info sheet covers the bottom of the map on mobile.
export const PIN_ROOM_DESKTOP = { top: 340, side: 170, bottom: 20 }
// Extra room above the pin when its popup also lists schools at the same address.
export const STACK_LIST_ROOM = { base: 36, perSchool: 24 }
export const PIN_ROOM_MOBILE = { top: 60, side: 20, bottom: 20 }
// Padding (px) when fitting a school's locations: their markers are 58px tall.
export const OVERLAY_FIT_PADDING = { top: 70, side: 40, bottom: 24 }
// Without the sheet, the legend and reset buttons sit over the bottom of the map (px), and
// the fixed compare bar lifts them further (see .map-has-compare).
export const MAP_CONTROLS_COVER = 70
export const COMPARE_BAR_COVER = 72
// How far past the country bounds the map can be panned, as a share of their size.
export const MAX_BOUNDS_PAD = 1
// Fixed when the map is created: the cluster layer builds its grid per zoom level then,
// and breaks if the minimum changes later (so it cannot wait for the country config).
export const MIN_ZOOM = 9

export const pointInBounds = (lat, lng, bounds) => {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return false
  return !bounds || bounds.contains([lat, lng])
}

export const leafletDistance = (a, b) => L.latLng(a).distanceTo(b)

// `schoolPoints` are the positions of the pins on the map (see `markers` in SchoolMap), so
// the fit never makes room for a location that the age filter left without a pin.
export const collectPoints = (schoolPoints, userLocation) => {
  // A copy: below its minimum pointsForFit returns the array it was given.
  const points = [...pointsForFit(schoolPoints, { distance: leafletDistance, keepRatio: fitKeepRatio(schoolPoints.length) })]

  if (userLocation?.lat && userLocation?.lng) {
    points.push([userLocation.lat, userLocation.lng])
  }

  return points
}

export const fitPaddingFor = (map) => {
  const pad = fitPadding(map.getSize())
  return [pad, pad]
}

export const fitMapToPoints = (map, points) => {
  if (!map || points.length === 0) return null

  if (points.length === 1) {
    map.setView(points[0], SINGLE_POINT_ZOOM, { animate: true, duration: 0.4 })
    return L.latLngBounds(points)
  }

  const bounds = L.latLngBounds(points)
  map.fitBounds(bounds, { padding: fitPaddingFor(map), maxZoom: MAX_FIT_ZOOM, animate: true, duration: 0.4 })
  return bounds
}

// Pan so the pin lands in the middle of the free part of the map (see pinTarget).
export const panPinIntoRoom = (map, latLng, room) => {
  const size = map.getSize()
  const target = pinTarget(size, room)
  const center = map.unproject(
    map.project(latLng).add([size.x / 2 - target.x, size.y / 2 - target.y])
  )
  map.panTo(center, { animate: true, duration: 0.5 })
}

export const getAutoFitKey = (schoolPoints, userLocation, countryBounds) => {
  const points = collectPoints(schoolPoints, userLocation)
    .map(([lat, lng]) => `${lat},${lng}`)
    .sort()

  return points.length > 0 ? points.join('|') : `bounds:${countryBounds.toBBoxString()}`
}

export const serializeBounds = (bounds) => ({
  southWest: {
    lat: bounds.getSouthWest().lat,
    lng: bounds.getSouthWest().lng,
  },
  northEast: {
    lat: bounds.getNorthEast().lat,
    lng: bounds.getNorthEast().lng,
  },
})
