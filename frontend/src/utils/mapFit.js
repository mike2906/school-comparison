/**
 * Points to fit the initial map view to. A few schools in outlying villages of the
 * municipality would otherwise zoom the whole city out to half the province, so with
 * enough points the farthest few percent (from the median point) are left out of the
 * fit. They stay on the map; only the starting zoom ignores them.
 *
 * `distance(a, b)` must be Leaflet's (`L.latLng(a).distanceTo(b)`, see utils/distance.js);
 * it is passed in so this stays testable without a DOM. Without it nothing is trimmed.
 */
export function pointsForFit(points, { distance, keepRatio = 0.95, minPoints = 20 } = {}) {
  if (!Array.isArray(points)) return []
  if (!distance || points.length < minPoints) return points

  const median = (values) => {
    const sorted = [...values].sort((a, b) => a - b)
    return sorted[Math.floor(sorted.length / 2)]
  }
  const center = [median(points.map(([lat]) => lat)), median(points.map(([, lng]) => lng))]

  const ranked = points
    .map(point => ({ point, d: distance(center, point) }))
    .sort((a, b) => a.d - b.d)

  const keep = Math.max(minPoints, Math.ceil(points.length * keepRatio))
  return ranked.slice(0, keep).map(entry => entry.point)
}
