/**
 * One card per place: a kindergarten and its school next door (the API's `same_place`)
 * share the card of whichever comes first in the list. The rest of the place is named on
 * that card, whether the filters list it (folded in) or not (hidden by the filters).
 *
 * Returns `cards` ({ school, samePlace: [{ school, inList }] }) in list order, and `hostById`,
 * the card that shows each folded school.
 */
export function foldSamePlace(schools) {
  const listed = new Map(schools.map(school => [school.id, school]))
  const hostById = new Map()
  const cards = []
  schools.forEach(school => {
    if (hostById.has(school.id)) return
    const seen = new Set([school.id])
    const samePlace = []
    ;(school.same_place || []).forEach(entry => {
      if (seen.has(entry.id)) return
      seen.add(entry.id)
      const inList = listed.has(entry.id) && !hostById.has(entry.id)
      if (inList) hostById.set(entry.id, school.id)
      samePlace.push({ school: inList ? listed.get(entry.id) : entry, inList })
    })
    cards.push({ school, samePlace })
  })
  return { cards, hostById }
}
