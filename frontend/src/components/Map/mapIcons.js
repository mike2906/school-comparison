import L from 'leaflet'


export const HIGHLIGHT_COLOR = '#f97316'
export const TYPE_COLORS = {
  state: '#14b8a6',
  private: '#8b5cf6',
}

export const getTypeColor = (type) => {
  const normalizedType = type === 'international' ? 'private' : type
  return TYPE_COLORS[normalizedType] || TYPE_COLORS.state
}

// Custom marker icons
// Colour = state / private; inner mark = school (dot) or kindergarten (square), so the
// two can be told apart in "All ages".
export const createMarkerIcon = (type, { isSelected, isHovered, isDimmed, isKindergarten = false, stackCount = 0 }) => {
  const color = isSelected ? HIGHLIGHT_COLOR : getTypeColor(type)

  const svgIcon = isSelected
    ? `<svg width="42" height="52" viewBox="0 0 42 52" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M21 0C9.402 0 0 9.402 0 21c0 14.7 21 31 21 31s21-16.3 21-31C42 9.402 32.598 0 21 0z" fill="${color}"/>
        ${isKindergarten
          ? '<rect x="13" y="11" width="16" height="16" rx="3" fill="white"/>'
          : '<circle cx="21" cy="19" r="8.5" fill="white"/>'}
      </svg>`
    : `<svg width="32" height="40" viewBox="-1 -1 34 42" fill="none" overflow="visible" xmlns="http://www.w3.org/2000/svg">
        <path d="M16 0C7.163 0 0 7.163 0 16c0 11.2 16 24 16 24s16-12.8 16-24C32 7.163 24.837 0 16 0z" fill="white" stroke="${color}" stroke-width="2"/>
        ${isKindergarten
          ? `<rect x="11" y="9" width="10" height="10" rx="2" fill="${color}"/>`
          : `<circle cx="16" cy="14" r="5" fill="${color}"/>`}
      </svg>`

  // Schools sharing the spot: the pins are exactly on top of each other.
  const badge = stackCount > 1 ? `<span class="pin-stack-badge">${stackCount}</span>` : ''

  return L.divIcon({
    html: svgIcon + badge,
    className: [
      'custom-div-icon',
      isSelected ? 'is-selected' : '',
      isSelected ? 'is-click-through' : '',
      isHovered ? 'is-hovered' : '',
      isDimmed ? 'is-dimmed' : '',
    ].filter(Boolean).join(' '),
    iconSize: isSelected ? [42, 52] : [32, 40],
    iconAnchor: isSelected ? [21, 52] : [16, 40],
    popupAnchor: [0, -40],
  })
}

export const createLocationMarkerIcon = (type, { label, isSelected }) => {
  const color = isSelected ? HIGHLIGHT_COLOR : getTypeColor(type)
  const labelClass = label && label.length > 2 ? 'location-marker-label wide' : 'location-marker-label'

  return L.divIcon({
    html: `
      <div class="location-marker ${isSelected ? 'is-selected' : ''}" style="--marker-color: ${color}">
        <span class="${labelClass}">${label || ''}</span>
      </div>
    `,
    className: 'leaflet-div-icon location-marker-wrapper',
    iconSize: [48, 58],
    iconAnchor: [24, 56],
    // Above the whole marker: the popup is drawn over the markers.
    popupAnchor: [0, -60],
  })
}

export const createUserMarkerIcon = () => {
  const svgIcon = `<svg width="28" height="28" viewBox="0 0 28 28" fill="none" xmlns="http://www.w3.org/2000/svg">
      <circle cx="14" cy="14" r="10" fill="#2563eb" stroke="white" stroke-width="3"/>
      <circle cx="14" cy="14" r="4" fill="white"/>
    </svg>`

  return L.divIcon({
    html: svgIcon,
    className: 'custom-div-icon user-marker',
    iconSize: [28, 28],
    iconAnchor: [14, 14],
    popupAnchor: [0, -14],
  })
}

export const createClusterIcon = (cluster, { dimmed = false } = {}) => {
  const count = cluster.getChildCount()
  const sizeClass = count <= 10 ? 'cluster-small' : count <= 25 ? 'cluster-medium' : 'cluster-large'

  return L.divIcon({
    html: `<div class="cluster-icon ${sizeClass}"><span>${count}</span></div>`,
    className: dimmed ? 'cluster-wrapper is-dimmed' : 'cluster-wrapper',
    iconSize: L.point(44, 44, true),
  })
}
