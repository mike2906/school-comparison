import { getFocusEmojis, getFocusLabels } from '../../utils/locationFocus'
import { normalizeAgeGroups } from '../../utils/mapFit'
import { getLocationTags } from '../../utils/schoolLocation'


export const getAgeGroupShortLabel = (t, ageGroup) => {
  if (!ageGroup) return '?'
  const shortKey = `ageGroupsShort.${ageGroup}`
  const shortLabel = t(shortKey)
  if (shortLabel && shortLabel !== shortKey) return shortLabel
  const fullKey = `ageGroups.${ageGroup}`
  const fullLabel = t(fullKey)
  if (fullLabel && fullLabel !== fullKey) return fullLabel
  return ageGroup
}

export const getAgeGroupFullLabel = (t, ageGroup) => {
  if (!ageGroup) return ''
  const fullKey = `ageGroups.${ageGroup}`
  const fullLabel = t(fullKey)
  if (fullLabel && fullLabel !== fullKey) return fullLabel
  return ageGroup
}

export const getLocationFocusLabels = (t, location) => {
  const tags = getLocationTags(location)
  if (!tags.length) return []
  return getFocusLabels(t, tags)
}

export const getLocationFocusMarkerLabel = (t, location) => {
  const tags = getLocationTags(location)
  if (!tags.length) return ''
  const emojis = getFocusEmojis(tags)
  if (emojis.length) return emojis.join('')
  const labels = getLocationFocusLabels(t, location)
  return labels[0] || ''
}

export const orderAgeGroups = (groups, ageGroupOrder) => {
  if (!groups.length) return []
  const orderMap = new globalThis.Map(ageGroupOrder.map((key, idx) => [key, idx]))
  return [...groups].sort((a, b) => {
    const aIndex = orderMap.has(a) ? orderMap.get(a) : 999
    const bIndex = orderMap.has(b) ? orderMap.get(b) : 999
    return aIndex - bIndex
  })
}

export const getLocationShortLabel = (t, location, ageGroupOrder, activeAgeGroup) => {
  const groups = orderAgeGroups(normalizeAgeGroups(location), ageGroupOrder)
  if (!groups.length) return '?'
  if (activeAgeGroup && groups.includes(activeAgeGroup)) {
    return getAgeGroupShortLabel(t, activeAgeGroup)
  }
  const first = getAgeGroupShortLabel(t, groups[0])
  return groups.length > 1 ? `${first}+` : first
}

export const getLocationFullLabel = (t, location, ageGroupOrder) => {
  const groups = orderAgeGroups(normalizeAgeGroups(location), ageGroupOrder)
  if (!groups.length) return ''
  return groups.map(group => getAgeGroupFullLabel(t, group)).join(', ')
}
