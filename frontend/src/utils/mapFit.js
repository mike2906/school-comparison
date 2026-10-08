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

// Up to SHORTLIST points nearly all are fitted; from CITY points on, the central half.
const SHORTLIST = { points: 50, keep: 0.95 }
const CITY = { points: 500, keep: 0.5 }

/**
 * Share of the points the first view fits. A shortlist shows nearly all of its schools.
 * A whole city fitted that way opens as three clusters holding everything, so it opens
 * on its central half, where the pins already spread out, and the rest is a pan away.
 * In between the share falls evenly, so one more result never changes the view much.
 */
export function fitKeepRatio(count) {
  if (count <= SHORTLIST.points) return SHORTLIST.keep
  if (count >= CITY.points) return CITY.keep
  const share = (count - SHORTLIST.points) / (CITY.points - SHORTLIST.points)
  return SHORTLIST.keep - share * (SHORTLIST.keep - CITY.keep)
}

/**
 * Padding (px) around fitted points: room for a pin (40px tall) on a large map, less on
 * a phone, where 60px a side cost a whole zoom level.
 */
export function fitPadding(size) {
  const pad = Math.round(Math.min(size.x, size.y) * 0.08)
  return Math.min(60, Math.max(24, pad))
}

/**
 * The pin to highlight for a selected school: the location clicked on the map when it is
 * one of the school's pins, otherwise the primary location (a school picked from the list
 * has no clicked pin).
 */
export function pickSelectedMarker(candidates, pickedKey) {
  if (!candidates?.length) return null
  return candidates.find(marker => marker.key === pickedKey)
    || candidates.find(marker => marker.location?.is_primary)
    || candidates[0]
}

/**
 * Whether a pin at container `point` can stay where it is: `room` is the space (px) it
 * needs to each edge of the map for what opens around it (popup above, sheet below).
 */
export function pinHasRoom(point, size, room) {
  return point.x >= room.side && point.x <= size.x - room.side
    && point.y >= room.top && point.y <= size.y - room.bottom
}

/**
 * Where to put a pin that has to move: the middle of the part of the map left free by
 * `room`, so it is centred in what the parent can actually see, not under the sheet.
 * Falls back to the map centre when the map is too small to have a free part.
 */
export function pinTarget(size, room) {
  const freeHeight = size.y - room.top - room.bottom
  return {
    x: size.x / 2,
    y: freeHeight > 0 ? room.top + freeHeight / 2 : size.y / 2,
  }
}

// Less free map than this (px) is not worth fitting into.
const MIN_FREE_FOR_FIT = 40

/**
 * fitBounds padding that keeps a school's locations clear of what covers the map
 * (`covered.top`: the locations panel, `covered.bottom`: the mobile sheet), or null when
 * too little of the map would be left: padding larger than the map gives Leaflet a NaN zoom.
 */
export function overlayFitPadding(size, covered, pad) {
  const top = covered.top + pad.top
  const bottom = covered.bottom + pad.bottom
  if (size.y - top - bottom < MIN_FREE_FOR_FIT || size.x - 2 * pad.side < MIN_FREE_FOR_FIT) return null
  return { paddingTopLeft: [pad.side, top], paddingBottomRight: [pad.side, bottom] }
}

/**
 * Pins of other schools at the same spot, per marker key. Several schools can share a
 * building, and their pins then sit exactly on top of each other: only the top one can be
 * clicked, so the popup lists the rest. Positions are compared to ~1 m (5 decimals).
 * Markers without company are left out of the result.
 */
export function stackedMarkersByKey(markers) {
  const byPosition = new Map()
  markers.forEach(marker => {
    const positionKey = marker.position.map(value => value.toFixed(5)).join(',')
    if (!byPosition.has(positionKey)) byPosition.set(positionKey, [])
    byPosition.get(positionKey).push(marker)
  })

  const result = new Map()
  byPosition.forEach(group => {
    if (group.length < 2) return
    group.forEach(marker => {
      const others = group.filter(other => other.school.id !== marker.school.id)
      if (others.length > 0) result.set(marker.key, others)
    })
  })
  return result
}
