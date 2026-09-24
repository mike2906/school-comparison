/**
 * Points to fit the initial map view to. A few schools in outlying villages of the
 * municipality would otherwise zoom the whole city out to half the province, so with
 * enough points the farthest few percent (from the median point) are left out of the
 * fit. They stay on the map; only the starting zoom ignores them.
 *
 * Distances here only rank points around one city, so a planar approximation (longitude
 * scaled by cos(latitude)) is enough; real distances use Leaflet (utils/distance.js).
 */
export function pointsForFit(points, { keepRatio = 0.95, minPoints = 20 } = {}) {
  if (!Array.isArray(points) || points.length < minPoints) return points || []

  const median = (values) => {
    const sorted = [...values].sort((a, b) => a - b)
    return sorted[Math.floor(sorted.length / 2)]
  }
  const centerLat = median(points.map(([lat]) => lat))
  const centerLng = median(points.map(([, lng]) => lng))
  const lngScale = Math.cos((centerLat * Math.PI) / 180)

  const ranked = points
    .map(point => {
      const dLat = point[0] - centerLat
      const dLng = (point[1] - centerLng) * lngScale
      return { point, d: dLat * dLat + dLng * dLng }
    })
    .sort((a, b) => a.d - b.d)

  const keep = Math.max(minPoints, Math.ceil(points.length * keepRatio))
  return ranked.slice(0, keep).map(entry => entry.point)
}
