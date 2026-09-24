import { useEffect, useMemo, useRef, memo, useCallback, useState } from 'react'
import { MapContainer, TileLayer, Marker, Popup, Pane, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import MarkerClusterGroup from 'react-leaflet-cluster'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { calculateDistance, formatDistance } from '../../utils/distance'
import { getSchoolName, getAddress } from '../../utils/i18n'
import { useCompare } from '../../context/CompareContext'
import { useCountry } from '../../context/CountryContext'
import { getAgeGroupKeys } from '../../utils/countryConfig'
import { getFocusEmojis, getFocusLabels } from '../../utils/locationFocus'
import { AGE_GROUP_KEYS } from '../../utils/education'
import { useStableCallback } from '../../hooks/useStableCallback'
import { isDesktopViewport } from '../../utils/searchViewState'
import { pointsForFit } from '../../utils/mapFit'

// CARTO requires an API key; without one every tile is watermarked
const CARTO_API_KEY = import.meta.env.VITE_CARTO_API_KEY
const TILE_URL = `https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png${
  CARTO_API_KEY ? `?key=${encodeURIComponent(CARTO_API_KEY)}` : ''
}`

// Fallback values (used when country config hasn't loaded yet)
const FALLBACK_CENTER = [42.6977, 23.3219]
const FALLBACK_ZOOM = 12
const FALLBACK_BOUNDS = L.latLngBounds([41.235, 22.357], [44.216, 28.887])

const SINGLE_POINT_ZOOM = 13
const MAX_FIT_ZOOM = 14
const FIT_PADDING = [60, 60]
const MARKER_CLICK_GUARD_MS = 350

const HIGHLIGHT_COLOR = '#f97316'
const TYPE_COLORS = {
  state: '#14b8a6',
  private: '#8b5cf6',
}

const getTypeColor = (type) => {
  const normalizedType = type === 'international' ? 'private' : type
  return TYPE_COLORS[normalizedType] || TYPE_COLORS.state
}

const parseCoordinate = (value) => {
  if (value === null || value === undefined) return null
  if (typeof value === 'string') {
    const normalized = value.replace(',', '.').trim()
    const parsed = Number.parseFloat(normalized)
    return Number.isFinite(parsed) ? parsed : null
  }
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

// Custom marker icons
const createMarkerIcon = (type, { isSelected, isHovered, isDimmed }) => {
  const color = isSelected ? HIGHLIGHT_COLOR : getTypeColor(type)

  const svgIcon = isSelected
    ? `<svg width="42" height="52" viewBox="0 0 42 52" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M21 0C9.402 0 0 9.402 0 21c0 14.7 21 31 21 31s21-16.3 21-31C42 9.402 32.598 0 21 0z" fill="${color}"/>
        <circle cx="21" cy="19" r="8.5" fill="white"/>
      </svg>`
    : `<svg width="32" height="40" viewBox="-1 -1 34 42" fill="none" overflow="visible" xmlns="http://www.w3.org/2000/svg">
        <path d="M16 0C7.163 0 0 7.163 0 16c0 11.2 16 24 16 24s16-12.8 16-24C32 7.163 24.837 0 16 0z" fill="white" stroke="${color}" stroke-width="2"/>
        <circle cx="16" cy="14" r="5" fill="${color}"/>
      </svg>`

  return L.divIcon({
    html: svgIcon,
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

const createLocationMarkerIcon = (type, { label, isSelected }) => {
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
    popupAnchor: [0, -30],
  })
}

const getAgeGroupShortLabel = (t, ageGroup) => {
  if (!ageGroup) return '?'
  const shortKey = `ageGroupsShort.${ageGroup}`
  const shortLabel = t(shortKey)
  if (shortLabel && shortLabel !== shortKey) return shortLabel
  const fullKey = `ageGroups.${ageGroup}`
  const fullLabel = t(fullKey)
  if (fullLabel && fullLabel !== fullKey) return fullLabel
  return ageGroup
}

const getAgeGroupFullLabel = (t, ageGroup) => {
  if (!ageGroup) return ''
  const fullKey = `ageGroups.${ageGroup}`
  const fullLabel = t(fullKey)
  if (fullLabel && fullLabel !== fullKey) return fullLabel
  return ageGroup
}

const normalizeAgeGroups = (location) => {
  if (!location) return []
  if (Array.isArray(location.age_groups)) return location.age_groups.filter(Boolean)
  if (location.age_group) return [location.age_group]
  return []
}

const getAgeGroupShifts = (location) => {
  if (!location?.age_group_shifts) return []
  return location.age_group_shifts.filter(item => item && item.age_group)
}

const getLocationTags = (location) => {
  if (!location?.location_tags) return []
  if (!Array.isArray(location.location_tags)) return []
  return location.location_tags.filter(Boolean)
}

const getLocationFocusLabels = (t, location) => {
  const tags = getLocationTags(location)
  if (!tags.length) return []
  return getFocusLabels(t, tags)
}

const getLocationFocusMarkerLabel = (t, location) => {
  const tags = getLocationTags(location)
  if (!tags.length) return ''
  const emojis = getFocusEmojis(tags)
  if (emojis.length) return emojis.join('')
  const labels = getLocationFocusLabels(t, location)
  return labels[0] || ''
}

const getShiftForAgeGroup = (location, ageGroup) => {
  const shifts = getAgeGroupShifts(location)
  if (!shifts.length) return null
  if (ageGroup) {
    const match = shifts.find(item => item.age_group === ageGroup)
    if (match) return match
  }
  return shifts[0] || null
}

const orderAgeGroups = (groups, ageGroupOrder) => {
  if (!groups.length) return []
  const orderMap = new globalThis.Map(ageGroupOrder.map((key, idx) => [key, idx]))
  return [...groups].sort((a, b) => {
    const aIndex = orderMap.has(a) ? orderMap.get(a) : 999
    const bIndex = orderMap.has(b) ? orderMap.get(b) : 999
    return aIndex - bIndex
  })
}

const getLocationShortLabel = (t, location, ageGroupOrder, activeAgeGroup) => {
  const groups = orderAgeGroups(normalizeAgeGroups(location), ageGroupOrder)
  if (!groups.length) return '?'
  if (activeAgeGroup && groups.includes(activeAgeGroup)) {
    return getAgeGroupShortLabel(t, activeAgeGroup)
  }
  const first = getAgeGroupShortLabel(t, groups[0])
  return groups.length > 1 ? `${first}+` : first
}

const getLocationFullLabel = (t, location, ageGroupOrder) => {
  const groups = orderAgeGroups(normalizeAgeGroups(location), ageGroupOrder)
  if (!groups.length) return ''
  return groups.map(group => getAgeGroupFullLabel(t, group)).join(', ')
}

const createUserMarkerIcon = () => {
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

const createClusterIcon = (cluster, { dimmed = false } = {}) => {
  const count = cluster.getChildCount()
  const sizeClass = count <= 10 ? 'cluster-small' : count <= 25 ? 'cluster-medium' : 'cluster-large'

  return L.divIcon({
    html: `<div class="cluster-icon ${sizeClass}"><span>${count}</span></div>`,
    className: dimmed ? 'cluster-wrapper is-dimmed' : 'cluster-wrapper',
    iconSize: L.point(44, 44, true),
  })
}

const pointInBounds = (lat, lng, bounds) => {
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return false
  return !bounds || bounds.contains([lat, lng])
}

const collectSchoolPoints = (schools, countryBounds) => {
  const points = []

  schools.forEach(school => {
    school.locations?.forEach(location => {
      const lat = parseCoordinate(location.lat)
      const lng = parseCoordinate(location.lng)
      if (pointInBounds(lat, lng, countryBounds)) {
        points.push([lat, lng])
      }
    })
  })

  return points
}

const leafletDistance = (a, b) => L.latLng(a).distanceTo(b)

const collectPoints = (schools, userLocation, countryBounds) => {
  const points = pointsForFit(collectSchoolPoints(schools, countryBounds), { distance: leafletDistance })

  if (userLocation?.lat && userLocation?.lng) {
    points.push([userLocation.lat, userLocation.lng])
  }

  return points
}

const fitMapToPoints = (map, points) => {
  if (!map || points.length === 0) return null

  if (points.length === 1) {
    map.setView(points[0], SINGLE_POINT_ZOOM, { animate: true, duration: 0.4 })
    return L.latLngBounds(points)
  }

  const bounds = L.latLngBounds(points)
  map.fitBounds(bounds, { padding: FIT_PADDING, maxZoom: MAX_FIT_ZOOM, animate: true, duration: 0.4 })
  return bounds
}

const getAutoFitKey = (schools, userLocation, countryBounds) => {
  const points = collectPoints(schools, userLocation, countryBounds)
    .map(([lat, lng]) => `${lat},${lng}`)
    .sort()

  return points.length > 0 ? points.join('|') : `bounds:${countryBounds.toBBoxString()}`
}

const serializeBounds = (bounds) => ({
  southWest: {
    lat: bounds.getSouthWest().lat,
    lng: bounds.getSouthWest().lng,
  },
  northEast: {
    lat: bounds.getNorthEast().lat,
    lng: bounds.getNorthEast().lng,
  },
})

function MapUpdater({ schools, userLocation, autoFit, lastValidBoundsRef, defaultZoom, countryBounds, keepInitialView }) {
  const map = useMap()
  const lastAutoFitKeyRef = useRef(null)
  // A restored view (returning from a school page) wins over the first auto-fit.
  const keepInitialViewRef = useRef(keepInitialView)

  useEffect(() => {
    if (!autoFit) return

    const autoFitKey = getAutoFitKey(schools, userLocation, countryBounds)
    if (lastAutoFitKeyRef.current === autoFitKey) return

    const schoolPoints = collectSchoolPoints(schools, countryBounds)
    const points = collectPoints(schools, userLocation, countryBounds)

    if (keepInitialViewRef.current) {
      if (schoolPoints.length === 0) return
      keepInitialViewRef.current = false
      lastAutoFitKeyRef.current = autoFitKey
      lastValidBoundsRef.current = L.latLngBounds(points)
      return
    }

    lastAutoFitKeyRef.current = autoFitKey

    if (schoolPoints.length > 0) {
      const bounds = fitMapToPoints(map, points)
      if (bounds) {
        lastValidBoundsRef.current = bounds
      }
      return
    }

    if (userLocation?.lat && userLocation?.lng) {
      map.setView([userLocation.lat, userLocation.lng], defaultZoom, { animate: true, duration: 0.4 })
      lastValidBoundsRef.current = L.latLngBounds([[userLocation.lat, userLocation.lng]])
      return
    }

    if (lastValidBoundsRef.current) {
      map.fitBounds(lastValidBoundsRef.current, { padding: FIT_PADDING, maxZoom: MAX_FIT_ZOOM, animate: true, duration: 0.4 })
      return
    }

    map.fitBounds(countryBounds, { padding: FIT_PADDING, maxZoom: defaultZoom, animate: true, duration: 0.4 })
  }, [schools, userLocation, autoFit, map, lastValidBoundsRef, defaultZoom, countryBounds])

  return null
}

function MapBoundsWatcher({ onBoundsChange, onViewChange }) {
  const map = useMapEvents({
    moveend: () => {
      if (onBoundsChange) {
        onBoundsChange(serializeBounds(map.getBounds()))
      }
      if (onViewChange) {
        const center = map.getCenter()
        onViewChange({ center: [center.lat, center.lng], zoom: map.getZoom() })
      }
    },
    zoomend: () => {
      if (onBoundsChange) {
        onBoundsChange(serializeBounds(map.getBounds()))
      }
    },
  })

  useEffect(() => {
    if (onBoundsChange) {
      onBoundsChange(serializeBounds(map.getBounds()))
    }
  }, [map, onBoundsChange])

  return null
}

function MapResizer({ resizeKey }) {
  const map = useMap()

  useEffect(() => {
    const timer = setTimeout(() => {
      map.invalidateSize({ animate: false })
    }, 0)
    return () => clearTimeout(timer)
  }, [map, resizeKey])

  return null
}

function MapClickHandler({ onClearSelection, markerInteractionRef }) {
  useMapEvents({
    click: (event) => {
      if (Date.now() - (markerInteractionRef.current || 0) < MARKER_CLICK_GUARD_MS) return
      const target = event.originalEvent?.target
      if (target?.closest?.('.leaflet-marker-icon')) return
      if (target?.closest?.('.leaflet-popup')) return
      if (target?.closest?.('.map-bottom-sheet')) return
      onClearSelection?.()
    },
  })

  return null
}

function MapLocationPicker({ enabled, onPickLocation, markerInteractionRef }) {
  useMapEvents({
    click: (event) => {
      if (!enabled) return
      if (Date.now() - (markerInteractionRef.current || 0) < MARKER_CLICK_GUARD_MS) return
      const target = event.originalEvent?.target
      if (target?.closest?.('.leaflet-marker-icon')) return
      if (target?.closest?.('.leaflet-popup')) return
      if (target?.closest?.('.map-bottom-sheet')) return
      onPickLocation?.({ lat: event.latlng.lat, lng: event.latlng.lng })
    },
  })

  return null
}

function MapOverlayNavigator({ overlaySchoolId, overlayLocations, focusLocation }) {
  const map = useMap()
  const lastOverlayRef = useRef(null)

  useEffect(() => {
    if (!overlaySchoolId || overlayLocations.length === 0) {
      lastOverlayRef.current = null
      return
    }
    const overlayKey = overlayLocations
      .map(location => `${location.id || ''}:${location.__lat ?? location.lat},${location.__lng ?? location.lng}`)
      .join('|')
    if (lastOverlayRef.current === overlayKey) return
    const points = overlayLocations.map(location => [location.__lat ?? location.lat, location.__lng ?? location.lng])
    const fitOverlay = () => fitMapToPoints(map, points)
    fitOverlay()
    const timer = setTimeout(fitOverlay, 120)
    lastOverlayRef.current = overlayKey
    return () => clearTimeout(timer)
  }, [overlaySchoolId, overlayLocations, map])

  useEffect(() => {
    if (!focusLocation?.lat || !focusLocation?.lng) return
    map.panTo([focusLocation.__lat ?? focusLocation.lat, focusLocation.__lng ?? focusLocation.lng], { animate: true, duration: 0.5 })
  }, [focusLocation, map])

  return null
}

function MapSelectionPan({ marker }) {
  const map = useMap()

  useEffect(() => {
    if (!marker) return
    map.panTo(marker.position, { animate: true, duration: 0.5 })
  }, [marker, map])

  return null
}

function PopupContent({
  marker,
  t,
  language,
  userLocation,
  onCompareToggle,
  onViewDetails,
  onClose,
  isMobile,
  inCompare,
  canAddMore,
  activeAgeGroup,
  onShowLocations,
  overlayActive,
}) {
  if (!marker) return null

  const distanceKm = userLocation?.lat && userLocation?.lng
    ? calculateDistance(userLocation.lat, userLocation.lng, marker.location.lat, marker.location.lng)
    : null

  const schoolName = getSchoolName(marker.school, language)
  const displayType = marker.school.school_type === 'international' ? 'private' : marker.school.school_type
  const address = getAddress(marker.location, language)
  const locationLabel = getLocationFullLabel(t, marker.location, AGE_GROUP_KEYS)
  const shiftInfo = getShiftForAgeGroup(marker.location, activeAgeGroup)
  const shiftLabel = shiftInfo?.shift ? t(`shifts.${shiftInfo.shift}`) : null
  const hasOrganisedGroups = Boolean(shiftInfo?.has_organised_groups)
  const locationTags = getLocationTags(marker.location)
  const additionalLocations = (marker.school.locations?.length ?? 0) - 1
  const showLocationsAction = additionalLocations > 0 && onShowLocations && !overlayActive

  const compareDisabled = !inCompare && !canAddMore

  return (
    <div
      className={`p-4 ${isMobile ? '' : 'min-w-[280px]'} space-y-3`}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="text-base font-semibold text-neutral-900 leading-snug">{schoolName}</h3>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            onClose?.()
          }}
          className="ml-2 w-7 h-7 rounded-full flex items-center justify-center text-neutral-500 hover:text-neutral-800 hover:bg-neutral-100 transition-colors"
          aria-label={t('common.close')}
        >
          ×
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium border bg-primary-50 text-primary-700 border-primary-200">
          {t(`schoolTypes.${displayType}`)}
        </span>
        <span className="text-xs text-neutral-500">{locationLabel}</span>
      </div>

      <div className="space-y-2 text-sm text-neutral-600">
        <div className="flex items-start gap-2">
          <span className="text-neutral-400">📍</span>
          <span>{address}</span>
        </div>

        {locationTags.length > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            {locationTags.map(tag => (
              <span
                key={tag}
                className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600"
              >
                {t(`locationTags.${tag}`, { defaultValue: tag })}
              </span>
            ))}
          </div>
        )}

        {typeof distanceKm === 'number' && (
          <div className="flex items-start gap-2">
            <span className="text-neutral-400">📏</span>
            <span>{formatDistance(distanceKm)} {t('location.fromYou')}</span>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <span className="text-neutral-500">{locationLabel}</span>
          {shiftLabel && <span className="text-neutral-400">•</span>}
          {shiftLabel && <span className="text-neutral-500">{shiftLabel}</span>}
          {hasOrganisedGroups && (
            <>
              <span className="text-neutral-400">•</span>
              <span className="text-neutral-500">{t('schools.organisedGroups')}</span>
            </>
          )}
        </div>

        {additionalLocations > 0 && (
          <div className="flex items-center gap-2 text-xs text-neutral-400">
            <span>
              + {additionalLocations} {t('schools.moreLocations', { count: additionalLocations })}
            </span>
            {showLocationsAction && (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation()
                  onShowLocations?.(marker.school)
                }}
                className="text-primary-600 hover:text-primary-700 font-medium"
              >
                {t('schools.showLocationsOnMap')}
              </button>
            )}
          </div>
        )}
      </div>

      <div className="flex items-center gap-2 pt-2">
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            onCompareToggle?.(marker.school)
          }}
          disabled={compareDisabled}
          className={`
            flex-1 px-3 py-2 text-sm font-medium rounded-lg border transition-colors
            ${inCompare
              ? 'bg-primary-50 text-primary-700 border-primary-400'
              : compareDisabled
              ? 'bg-neutral-100 text-neutral-400 border-neutral-200 cursor-not-allowed'
              : 'bg-white text-primary-700 border-primary-300 hover:bg-primary-50'}
          `}
        >
          {inCompare ? t('schools.comparing') : t('schools.compare')}
        </button>
        <button
          type="button"
          onClick={(event) => {
            event.stopPropagation()
            onViewDetails?.(marker.school)
          }}
          className="flex-1 px-3 py-2 text-sm font-medium text-white bg-primary-600 hover:bg-primary-700 rounded-lg transition-colors"
        >
          {t('schools.viewDetails')}
        </button>
      </div>
    </div>
  )
}

const SchoolMarker = memo(function SchoolMarker({
  marker,
  isSelected,
  isHovered,
  activeAgeGroup,
  isDimmed,
  onSelect,
  onDeselect,
  showPopup,
  t,
  language,
  userLocation,
  onCompareToggle,
  onViewDetails,
  onClosePopup,
  inCompare,
  canAddMore,
  onShowLocations,
  overlayActive,
  onMarkerInteraction,
}) {
  const markerRef = useRef(null)
  const schoolType = marker.school.school_type
  // A new icon object makes react-leaflet rebuild the marker DOM, so only create one when its look changes
  const icon = useMemo(
    () => createMarkerIcon(schoolType, { isSelected, isHovered, isDimmed }),
    [schoolType, isSelected, isHovered, isDimmed]
  )

  useEffect(() => {
    if (!markerRef.current || !showPopup) return
    if (isSelected) {
      markerRef.current.openPopup()
    } else {
      markerRef.current.closePopup()
    }
  }, [isSelected, showPopup])

  return (
    <Marker
      ref={markerRef}
      position={marker.position}
      icon={icon}
      zIndexOffset={isSelected ? 1200 : 0}
      eventHandlers={{
        mousedown: (event) => {
          onMarkerInteraction?.()
          if (event.originalEvent) {
            L.DomEvent.stop(event.originalEvent)
          }
        },
        click: (event) => {
          onMarkerInteraction?.()
          if (event.originalEvent) {
            L.DomEvent.stop(event.originalEvent)
          }
          onSelect(marker.school)
        },
      }}
    >
      {showPopup && (
        <Popup autoPan={false} closeButton={false}>
          <PopupContent
            marker={marker}
            t={t}
            language={language}
            userLocation={userLocation}
            onCompareToggle={onCompareToggle}
            onViewDetails={onViewDetails}
            onClose={() => {
              onClosePopup?.()
              onDeselect?.()
            }}
            isMobile={false}
            inCompare={inCompare}
            canAddMore={canAddMore}
            activeAgeGroup={activeAgeGroup}
            onShowLocations={onShowLocations}
            overlayActive={overlayActive}
          />
        </Popup>
      )}
    </Marker>
  )
})

const OverlayLocationMarker = memo(function OverlayLocationMarker({
  marker,
  isSchoolSelected,
  isSelected,
  ageGroupOrder,
  activeAgeGroup,
  labelMode = 'age',
  showPopup,
  t,
  language,
  userLocation,
  onSelect,
  onFocusLocation,
  onCompareToggle,
  onViewDetails,
  onClosePopup,
  inCompare,
  canAddMore,
  onShowLocations,
  overlayActive,
  onMarkerInteraction,
}) {
  const markerRef = useRef(null)
  const focusLabels = getLocationFocusLabels(t, marker.location)
  const label = labelMode === 'focus' && focusLabels.length
    ? getLocationFocusMarkerLabel(t, marker.location)
    : labelMode === 'number'
    ? marker.label
    : getLocationShortLabel(t, marker.location, ageGroupOrder, activeAgeGroup)
  const schoolType = marker.school.school_type
  const icon = useMemo(
    () => createLocationMarkerIcon(schoolType, { label, isSelected }),
    [schoolType, label, isSelected]
  )
  const fullLabel = labelMode === 'focus' && focusLabels.length
    ? focusLabels.join(', ')
    : labelMode === 'number'
    ? getAddress(marker.location, language)
    : getLocationFullLabel(t, marker.location, ageGroupOrder)

  useEffect(() => {
    if (!markerRef.current || !showPopup) return
    if (isSelected) {
      markerRef.current.openPopup()
    } else {
      markerRef.current.closePopup()
    }
  }, [isSelected, showPopup])

  return (
    <Marker
      ref={markerRef}
      position={marker.position}
      icon={icon}
      zIndexOffset={isSelected ? 1600 : 900}
      eventHandlers={{
        mousedown: (event) => {
          onMarkerInteraction?.()
          if (event.originalEvent) {
            L.DomEvent.stop(event.originalEvent)
          }
        },
        click: (event) => {
          onMarkerInteraction?.()
          if (event.originalEvent) {
            L.DomEvent.stop(event.originalEvent)
          }
          onFocusLocation?.(marker.school.id, marker.location.id)
          if (!isSchoolSelected) {
            onSelect?.(marker.school)
          }
        },
      }}
    >
      {fullLabel && (
        <Tooltip direction="top" offset={[0, -14]} opacity={0.9} sticky>
          {fullLabel}
        </Tooltip>
      )}
      {showPopup && (
        <Popup autoPan={false} closeButton={false}>
          <PopupContent
            marker={marker}
            t={t}
            language={language}
            userLocation={userLocation}
            onCompareToggle={onCompareToggle}
            onViewDetails={onViewDetails}
            onClose={() => {
              onClosePopup?.()
            }}
            isMobile={false}
            inCompare={inCompare}
            canAddMore={canAddMore}
            activeAgeGroup={activeAgeGroup}
            onShowLocations={onShowLocations}
            overlayActive={overlayActive}
          />
        </Popup>
      )}
    </Marker>
  )
})

function ResetViewControl({ schools, userLocation, lastValidBoundsRef, label, defaultZoom, countryBounds }) {
  const map = useMap()

  const handleResetView = useCallback(() => {
    const schoolPoints = collectSchoolPoints(schools, countryBounds)
    const points = collectPoints(schools, userLocation, countryBounds)

    if (schoolPoints.length > 0) {
      const bounds = fitMapToPoints(map, points)
      if (bounds) {
        lastValidBoundsRef.current = bounds
      }
      return
    }

    if (userLocation?.lat && userLocation?.lng) {
      map.setView([userLocation.lat, userLocation.lng], defaultZoom, { animate: true, duration: 0.4 })
      return
    }

    map.fitBounds(countryBounds, { padding: FIT_PADDING, maxZoom: defaultZoom, animate: true, duration: 0.4 })
  }, [map, schools, userLocation, lastValidBoundsRef, defaultZoom, countryBounds])

  return (
    <div className="leaflet-top leaflet-right">
      <div className="leaflet-control">
        <button
          type="button"
          onClick={handleResetView}
          className="mt-3 mr-3 inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg bg-primary-600 text-white shadow-panel hover:bg-primary-700 transition-colors"
        >
          {label}
        </button>
      </div>
    </div>
  )
}

function SchoolMap({
  schools,
  activeAgeGroup,
  selectedSchoolId,
  hoveredSchoolId,
  onSchoolSelect,
  onClearSelection,
  loading,
  userLocation,
  isPickingLocation,
  onPickLocation,
  onBoundsChange,
  onViewChange,
  onOpenDetails,
  initialView = null,
  autoFit = true,
  hasCompare = false,
  resizeKey,
  locationOverlay,
  onShowLocations,
  onClearLocationOverlay,
  onFocusLocation,
  onToggleHideOthers,
}) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { addToCompare, removeFromCompare, isInCompare, canAddMore } = useCompare()
  const { config } = useCountry()
  const lastValidBoundsRef = useRef(null)
  const markerInteractionRef = useRef(0)
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768)
  const [sheetOffset, setSheetOffset] = useState(120)
  const sheetStartRef = useRef(null)

  const noteMarkerInteraction = useCallback(() => {
    markerInteractionRef.current = Date.now()
  }, [])

  const mapCenter = config?.map_config?.center || FALLBACK_CENTER
  const defaultZoom = config?.map_config?.default_zoom || FALLBACK_ZOOM
  const countryBounds = useMemo(() => {
    const bounds = config?.map_config?.bounds
    if (bounds && bounds.length === 2) {
      return L.latLngBounds(bounds[0], bounds[1])
    }
    return FALLBACK_BOUNDS
  }, [config])

  const markers = useMemo(() => {
    const result = []
    schools.forEach(school => {
      const eligibleLocations = activeAgeGroup
        ? school.locations?.filter(location => normalizeAgeGroups(location).includes(activeAgeGroup))
        : school.locations
      eligibleLocations?.forEach((location, idx) => {
        const lat = parseCoordinate(location.lat)
        const lng = parseCoordinate(location.lng)
        if (pointInBounds(lat, lng, countryBounds)) {
          result.push({
            school,
            location,
            position: [lat, lng],
            key: `${school.id}-${location.id || idx}`,
          })
        }
      })
    })
    return result
  }, [schools, activeAgeGroup, countryBounds])

  const overlaySchoolId = locationOverlay?.schoolId ?? null
  const hideOthers = Boolean(locationOverlay?.hideOthers)
  const overlaySchool = useMemo(() => {
    if (!overlaySchoolId) return null
    return schools.find(school => school.id === overlaySchoolId) || null
  }, [schools, overlaySchoolId])

  const overlayLocations = useMemo(() => {
    if (!overlaySchool?.locations) return []
    return overlaySchool.locations
      .map(location => ({
        ...location,
        __lat: parseCoordinate(location.lat),
        __lng: parseCoordinate(location.lng),
      }))
      .filter(location => pointInBounds(location.__lat, location.__lng, countryBounds))
  }, [overlaySchool, countryBounds])

  const overlayFocusLocation = useMemo(() => {
    if (!locationOverlay?.focusLocationId) return null
    return overlayLocations.find(location => location.id === locationOverlay.focusLocationId) || null
  }, [overlayLocations, locationOverlay?.focusLocationId])

  const ageGroupOrder = useMemo(() => {
    const keys = getAgeGroupKeys(config)
    return keys.length > 0 ? keys : AGE_GROUP_KEYS
  }, [config])

  const overlayFocusLabels = useMemo(() => {
    if (overlayLocations.length < 2) return false
    const orderedGroups = overlayLocations.map(location =>
      orderAgeGroups(normalizeAgeGroups(location), ageGroupOrder).join('|')
    )
    if (!orderedGroups.length) return false
    const first = orderedGroups[0]
    const consistentGroups = orderedGroups.every(groups => groups === first)
    const hasTags = overlayLocations.every(location => getLocationTags(location).length > 0)
    return consistentGroups && hasTags
  }, [overlayLocations, ageGroupOrder])

  const overlayMarkers = useMemo(() => {
    if (!overlaySchool) return []
    return overlayLocations.map((location, idx) => ({
      school: overlaySchool,
      location,
      position: [location.__lat, location.__lng],
      key: `overlay-${overlaySchool.id}-${location.id || idx}`,
      label: String(idx + 1),
    }))
  }, [overlaySchool, overlayLocations])

  const overlayActiveOnMap = overlaySchoolId && overlayLocations.length > 0

  const overlayFocusActive = overlayActiveOnMap && Boolean(locationOverlay?.focusLocationId)
  const overlaySelectedLocationId = useMemo(() => {
    if (!overlaySchoolId || selectedSchoolId !== overlaySchoolId) return null
    return locationOverlay?.focusLocationId || null
  }, [overlaySchoolId, selectedSchoolId, locationOverlay?.focusLocationId])

  const selectedMarker = useMemo(() => {
    if (!selectedSchoolId) return null
    if (overlayFocusActive && overlaySelectedLocationId) {
      const focused = overlayLocations.find(location => location.id === overlaySelectedLocationId)
      if (focused) {
        return {
          school: overlaySchool,
          location: focused,
          position: [focused.__lat ?? focused.lat, focused.__lng ?? focused.lng],
          key: `overlay-focus-${overlaySchool?.id}-${focused.id || 'focus'}`,
        }
      }
    }
    const candidates = markers.filter(marker => marker.school.id === selectedSchoolId)
    if (candidates.length > 0) {
      return candidates.find(marker => marker.location.is_primary) || candidates[0]
    }
    if (overlayLocations.length > 0) {
      const fallback = overlayLocations.find(location => location.is_primary) || overlayLocations[0]
      return fallback
        ? {
          school: overlaySchool,
          location: fallback,
          position: [fallback.__lat ?? fallback.lat, fallback.__lng ?? fallback.lng],
          key: `overlay-focus-${overlaySchool?.id}-${fallback.id || 'fallback'}`,
        }
        : null
    }
    return null
  }, [markers, selectedSchoolId, overlayLocations, overlaySelectedLocationId, overlaySchool, overlayFocusActive])

  const baseMarkers = useMemo(() => {
    if (!overlayActiveOnMap) return markers
    if (hideOthers) return []
    return markers.filter(marker => marker.school.id !== overlaySchoolId)
  }, [markers, overlaySchoolId, hideOthers, overlayActiveOnMap])

  const selectedMarkers = useMemo(() => {
    if (!selectedSchoolId) return []
    if (overlayActiveOnMap) return []
    const candidates = baseMarkers.filter(marker => selectedSchoolId === marker.school.id)
    if (candidates.length === 0) return []
    const primary = candidates.find(marker => marker.location?.is_primary) || candidates[0]
    return primary ? [primary] : []
  }, [baseMarkers, selectedSchoolId, overlayActiveOnMap])

  const regularMarkers = useMemo(
    () => baseMarkers.filter(marker => selectedSchoolId !== marker.school.id),
    [baseMarkers, selectedSchoolId]
  )

  useEffect(() => {
    const handleResize = () => {
      setIsMobile(window.innerWidth < 768)
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => {
    if (!selectedSchoolId) return
    setSheetOffset(120)
    sheetStartRef.current = null
    const frame = requestAnimationFrame(() => {
      setSheetOffset(0)
    })
    return () => cancelAnimationFrame(frame)
  }, [selectedSchoolId])

  const handleCompareToggle = useStableCallback((school) => {
    if (!school) return
    if (isInCompare(school.id)) {
      removeFromCompare(school.id)
    } else {
      addToCompare(school)
    }
  })

  const handleViewDetails = useCallback((school) => {
    if (onOpenDetails && isDesktopViewport()) {
      onOpenDetails(school)
      return
    }
    navigate(`/schools/${school.id}`)
  }, [navigate, onOpenDetails])

  return (
    <div
      className={`relative h-full w-full ${hasCompare ? 'map-has-compare' : ''} ${
        overlayActiveOnMap && !hideOthers ? 'map-dim-others' : ''
      } ${isPickingLocation ? 'map-picking-location' : ''}`}
    >
      <MapContainer
        center={initialView?.center || mapCenter}
        zoom={initialView?.zoom || defaultZoom}
        className="h-full w-full"
        zoomControl={false}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> | &copy; <a href="https://carto.com/">CARTO</a>'
          url={TILE_URL}
          subdomains="abcd"
          maxZoom={20}
        />

        <Pane name="overlayFlow" style={{ zIndex: 620 }} />
        <Pane name="overlayLocations" style={{ zIndex: 650 }}>
          {overlayActiveOnMap && (
            <>
              {overlayMarkers.map(marker => (
                <OverlayLocationMarker
                  key={`${marker.key}-${overlayActiveOnMap ? 'overlay' : 'base'}`}
                  marker={marker}
                  isSchoolSelected={selectedSchoolId === overlaySchoolId}
                  isSelected={marker.location.id === overlaySelectedLocationId}
                  ageGroupOrder={ageGroupOrder}
                  activeAgeGroup={activeAgeGroup}
                  labelMode={overlayFocusLabels ? 'focus' : 'number'}
                  showPopup={!isMobile}
                  t={t}
                  language={i18n.language}
                  userLocation={userLocation}
                  onSelect={onSchoolSelect}
                  onFocusLocation={onFocusLocation}
                  onCompareToggle={handleCompareToggle}
                  onViewDetails={handleViewDetails}
                  onClosePopup={onClearSelection}
                  inCompare={isInCompare(marker.school.id)}
                  canAddMore={canAddMore}
                  onShowLocations={onShowLocations}
                  overlayActive={locationOverlay?.schoolId === marker.school.id}
                  onMarkerInteraction={noteMarkerInteraction}
                />
              ))}

            </>
          )}
        </Pane>

        <MapUpdater
          schools={schools}
          userLocation={userLocation}
          autoFit={autoFit}
          lastValidBoundsRef={lastValidBoundsRef}
          defaultZoom={defaultZoom}
          countryBounds={countryBounds}
          keepInitialView={Boolean(initialView)}
        />

        <MapBoundsWatcher onBoundsChange={onBoundsChange} onViewChange={onViewChange} />
        <MapResizer resizeKey={resizeKey} />
        <MapClickHandler
          onClearSelection={onClearSelection}
          markerInteractionRef={markerInteractionRef}
        />
        <MapLocationPicker
          enabled={isPickingLocation}
          onPickLocation={onPickLocation}
          markerInteractionRef={markerInteractionRef}
        />
        <MapSelectionPan marker={selectedMarker} />
        <MapOverlayNavigator
          overlaySchoolId={overlaySchoolId}
          overlayLocations={overlayLocations}
          focusLocation={overlayFocusLocation}
        />

        <ResetViewControl
          schools={schools}
          userLocation={userLocation}
          lastValidBoundsRef={lastValidBoundsRef}
          label={t('map.resetView')}
          defaultZoom={defaultZoom}
          countryBounds={countryBounds}
        />

        {userLocation?.lat && userLocation?.lng && (
          <Marker
            position={[userLocation.lat, userLocation.lng]}
            icon={createUserMarkerIcon()}
            zIndexOffset={2000}
          >
            <Popup autoPan={false}>
              <div className="p-2 text-sm text-neutral-700">
                {userLocation.address || t('location.currentLocation')}
              </div>
            </Popup>
          </Marker>
        )}

        <MarkerClusterGroup
          key={`clusters-${overlayActiveOnMap ? 'overlay' : 'base'}-${hideOthers ? 'hide' : 'show'}`}
          chunkedLoading
          disableClusteringAtZoom={14}
          maxClusterRadius={(zoom) => (zoom >= 13 ? 60 : 80)}
          iconCreateFunction={(cluster) => createClusterIcon(cluster, { dimmed: overlayActiveOnMap && !hideOthers })}
          showCoverageOnHover={false}
          animate
          spiderfyOnMaxZoom={false}
        >
          {regularMarkers.map(marker => (
            <SchoolMarker
              key={`${marker.key}-${overlayActiveOnMap ? 'overlay' : 'base'}-${hideOthers ? 'hide' : 'show'}`}
              marker={marker}
              isSelected={false}
              isHovered={hoveredSchoolId === marker.school.id}
              activeAgeGroup={activeAgeGroup}
              isDimmed={overlayActiveOnMap && !hideOthers}
              onSelect={onSchoolSelect}
              onDeselect={onClearSelection}
              showPopup={!isMobile}
              t={t}
              language={i18n.language}
              userLocation={userLocation}
              onCompareToggle={handleCompareToggle}
              onViewDetails={handleViewDetails}
              onClosePopup={onClearSelection}
              inCompare={isInCompare(marker.school.id)}
              canAddMore={canAddMore}
              onShowLocations={onShowLocations}
              overlayActive={locationOverlay?.schoolId === marker.school.id}
              onMarkerInteraction={noteMarkerInteraction}
            />
          ))}
        </MarkerClusterGroup>

        {selectedMarkers.map(marker => (
            <SchoolMarker
              key={marker.key}
              marker={marker}
              isSelected
              isHovered={false}
              activeAgeGroup={activeAgeGroup}
              isDimmed={false}
              onSelect={onSchoolSelect}
              onDeselect={onClearSelection}
              showPopup={!isMobile && !overlayFocusActive}
              t={t}
              language={i18n.language}
              userLocation={userLocation}
              onCompareToggle={handleCompareToggle}
              onViewDetails={handleViewDetails}
              onClosePopup={onClearSelection}
            inCompare={isInCompare(marker.school.id)}
            canAddMore={canAddMore}
            onShowLocations={onShowLocations}
            overlayActive={locationOverlay?.schoolId === marker.school.id}
            onMarkerInteraction={noteMarkerInteraction}
          />
        ))}

        {/* overlay markers and flow are rendered inside the overlay pane */}
      </MapContainer>

      {isMobile && selectedMarker && (
        <div
          className="map-bottom-sheet absolute inset-x-0 bottom-0 z-[1200] transition-transform duration-300"
          style={{ transform: `translateY(${sheetOffset}px)` }}
        >
          <div
            className="mx-3 mb-3 rounded-2xl bg-white shadow-2xl border border-neutral-200"
          >
            <div
              className="flex items-center justify-center py-2 cursor-grab active:cursor-grabbing"
              onTouchStart={(event) => {
                sheetStartRef.current = event.touches[0].clientY
              }}
              onTouchMove={(event) => {
                if (sheetStartRef.current === null) return
                const delta = Math.max(0, event.touches[0].clientY - sheetStartRef.current)
                setSheetOffset(delta)
              }}
              onTouchEnd={() => {
                if (sheetOffset > 80) {
                  onClearSelection?.()
                }
                setSheetOffset(0)
                sheetStartRef.current = null
              }}
            >
              <span className="h-1.5 w-12 rounded-full bg-neutral-200" />
            </div>
            <PopupContent
              marker={selectedMarker}
              t={t}
              language={i18n.language}
              userLocation={userLocation}
              onCompareToggle={handleCompareToggle}
              onViewDetails={handleViewDetails}
              onClose={onClearSelection}
              isMobile
              inCompare={isInCompare(selectedMarker.school.id)}
              canAddMore={canAddMore}
              activeAgeGroup={activeAgeGroup}
              onShowLocations={onShowLocations}
              overlayActive={locationOverlay?.schoolId === selectedMarker.school.id}
            />
          </div>
        </div>
      )}

      {/* Loading: a full overlay only for the first load; afterwards the old markers stay
          usable and a small pill says the results are updating. */}
      {loading && schools.length > 0 && (
        <div className="pointer-events-none absolute left-1/2 top-4 z-[1000] -translate-x-1/2" role="status">
          <div className="flex items-center gap-2 rounded-full bg-white px-3 py-1.5 text-sm font-medium text-neutral-700 shadow-lg">
            <svg className="h-4 w-4 animate-spin text-primary-500" fill="none" viewBox="0 0 24 24" aria-hidden="true">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            {t('common.updating')}
          </div>
        </div>
      )}
      {loading && schools.length === 0 && (
        <div className="absolute inset-0 bg-white/60 backdrop-blur-sm flex items-center justify-center z-[1000]">
          <div className="flex items-center gap-3 bg-white px-5 py-3 rounded-full shadow-lg">
            <svg className="animate-spin h-5 w-5 text-primary-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
            </svg>
            <span className="text-sm font-medium text-neutral-700">{t('common.loading')}</span>
          </div>
        </div>
      )}

      {/* Map legend */}
      <div className={`absolute left-4 bg-white rounded-lg shadow-panel p-3 z-[1000] ${hasCompare ? 'bottom-24' : 'bottom-6'}`}>
        <p className="text-xs font-medium text-neutral-700 mb-2">{t('map.legend')}</p>
        <div className="space-y-1.5">
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-primary-500" />
            <span className="text-xs text-neutral-600">{t('schoolTypes.state')}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-3 h-3 rounded-full bg-violet-500" />
            <span className="text-xs text-neutral-600">{t('schoolTypes.private')}</span>
          </div>
        </div>
      </div>

      {overlaySchool && (
        <div className="absolute top-4 left-4 bg-white/95 backdrop-blur rounded-lg shadow-panel p-3 z-[1000]">
          <p className="text-xs font-semibold text-neutral-800">
            {t('map.locationsFor', { name: getSchoolName(overlaySchool, i18n.language) })}
          </p>
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={() => onToggleHideOthers?.(overlaySchool.id)}
              disabled={!overlayActiveOnMap}
              className={`px-2.5 py-1 text-xs font-semibold rounded-full border transition-colors ${
                !overlayActiveOnMap
                  ? 'border-neutral-200 text-neutral-400 cursor-not-allowed'
                  : hideOthers
                  ? 'border-primary-300 bg-primary-50 text-primary-700'
                  : 'border-neutral-200 text-neutral-600 hover:border-primary-200 hover:text-primary-700'
              }`}
            >
              {hideOthers ? t('schools.showOtherSchools') : t('schools.hideOtherSchools')}
            </button>
            <button
              type="button"
              onClick={() => onClearLocationOverlay?.()}
              className="px-2.5 py-1 text-xs font-semibold rounded-full border border-neutral-200 text-neutral-600 hover:text-neutral-800 hover:border-neutral-300 transition-colors"
            >
              {t('schools.clearLocations')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// Memoised: the search page re-renders on every keystroke, sort and hover, and
// re-rendering ~440 markers each time made those interactions sluggish.
export default memo(SchoolMap)
