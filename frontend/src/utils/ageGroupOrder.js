import { getAgeGroupKeys } from './countryConfig.js'

/**
 * Age-group keys in the country's own order (youngest first), for display. Locations store
 * them in no particular order, so a list read "First group, Nursery, Pre-school". Keys the
 * country config doesn't know keep their place after the known ones.
 */
export function orderAgeGroups(keys = [], config) {
  const order = getAgeGroupKeys(config)
  const rank = key => {
    const index = order.indexOf(key)
    return index === -1 ? order.length : index
  }
  return keys
    .map((key, index) => ({ key, index }))
    .sort((a, b) => rank(a.key) - rank(b.key) || a.index - b.index)
    .map(({ key }) => key)
}
