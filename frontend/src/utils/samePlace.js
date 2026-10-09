/**
 * One card per place: a kindergarten and its school next door (the API's `same_place`)
 * share the card of whichever comes first in the list. Every card names the others in its
 * places, whether the filters list them or not.
 *
 * An institution in two places (one campus next to each of two kindergartens) is neither
 * folded nor a host: its card names both places' members, and those keep their own cards.
 *
 * Returns `cards` ({ school, samePlace: [{ school, inList }] }) in list order, and `hostById`,
 * the card that shows each folded school.
 */
export function foldSamePlace(schools) {
  const listed = new Map(schools.map(school => [school.id, school]))
  const placeOf = (school) => {
    const places = new Set((school.same_place || []).map(entry => entry.place_id))
    return places.size === 1 ? [...places][0] : null
  }
  const hostByPlace = new Map()
  const hostById = new Map()
  schools.forEach(school => {
    const place = placeOf(school)
    if (place === null) return
    if (hostByPlace.has(place)) hostById.set(school.id, hostByPlace.get(place))
    else hostByPlace.set(place, school.id)
  })

  const cards = []
  schools.forEach(school => {
    if (hostById.has(school.id)) return
    const seen = new Set([school.id])
    const samePlace = []
    ;(school.same_place || []).forEach(entry => {
      if (seen.has(entry.id)) return
      seen.add(entry.id)
      const inList = listed.has(entry.id)
      samePlace.push({ school: inList ? listed.get(entry.id) : entry, inList })
    })
    cards.push({ school, samePlace })
  })
  return { cards, hostById }
}
