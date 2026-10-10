import L from 'leaflet'

export function calculateDistance(lat1, lng1, lat2, lng2) {
  if ([lat1, lng1, lat2, lng2].some(value => typeof value !== 'number')) {
    return null
  }

  const from = L.latLng(lat1, lng1)
  const to = L.latLng(lat2, lng2)
  return from.distanceTo(to) / 1000
}
