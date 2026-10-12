import { useEffect, useRef, useCallback } from 'react'
import { useMap, useMapEvents } from 'react-leaflet'
import L from 'leaflet'
import { pinHasRoom, overlayFitPadding } from '../../utils/mapFit'
import {
  collectPoints,
  fitMapToPoints,
  fitPaddingFor,
  getAutoFitKey,
  MARKER_CLICK_GUARD_MS,
  MAX_BOUNDS_PAD,
  MAX_FIT_ZOOM,
  OVERLAY_FIT_PADDING,
  panPinIntoRoom,
  serializeBounds,
  SINGLE_POINT_ZOOM,
} from './mapView'


export function MapUpdater({ schools, schoolPoints, userLocation, autoFit, lastValidBoundsRef, defaultZoom, countryBounds, keepInitialView, fittedWhileHiddenRef }) {
  const map = useMap()
  // The pins change twice when the age group does: at once (the previous result, filtered
  // by the new group) and again when the new result loads. Only a new result refits, so
  // the pins are read through a ref and `schools` is what the effect depends on.
  const schoolPointsRef = useRef(schoolPoints)
  schoolPointsRef.current = schoolPoints
  const lastAutoFitKeyRef = useRef(null)
  // A restored view (returning from a school page) wins over the first auto-fit.
  const keepInitialViewRef = useRef(keepInitialView)

  useEffect(() => {
    if (!autoFit) return

    const schoolPoints = schoolPointsRef.current
    const autoFitKey = getAutoFitKey(schoolPoints, userLocation, countryBounds)
    if (lastAutoFitKeyRef.current === autoFitKey) return

    const points = collectPoints(schoolPoints, userLocation)

    if (keepInitialViewRef.current) {
      if (schoolPoints.length === 0) return
      keepInitialViewRef.current = false
      lastAutoFitKeyRef.current = autoFitKey
      lastValidBoundsRef.current = L.latLngBounds(points)
      return
    }

    lastAutoFitKeyRef.current = autoFitKey
    const size = map.getSize()
    fittedWhileHiddenRef.current = size.x === 0 || size.y === 0

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
      map.fitBounds(lastValidBoundsRef.current, { padding: fitPaddingFor(map), maxZoom: MAX_FIT_ZOOM, animate: true, duration: 0.4 })
      return
    }

    // Remember the country view too, so a map revealed after an empty result refits to it.
    lastValidBoundsRef.current = countryBounds
    // Not animated: this runs while the schools are loading, and Leaflet drops a fit
    // requested during a zoom animation, which left the map on the country view.
    map.fitBounds(countryBounds, { padding: fitPaddingFor(map), maxZoom: defaultZoom, animate: false })
  }, [schools, userLocation, autoFit, map, lastValidBoundsRef, defaultZoom, countryBounds, fittedWhileHiddenRef])

  return null
}

export function MapBoundsWatcher({ onBoundsChange, onViewChange }) {
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

export function MapResizer({ resizeKey, lastValidBoundsRef, fittedWhileHiddenRef }) {
  const map = useMap()

  useEffect(() => {
    const timer = setTimeout(() => {
      map.invalidateSize({ animate: false })
      const size = map.getSize()
      // An auto-fit that ran while the map was hidden (mobile/tablet List tab) used a zero
      // size and zoomed out to the whole province: redo it once the map is visible. Only
      // then, so a view the parent zoomed to themselves is kept across tab switches.
      if (fittedWhileHiddenRef.current && size.x > 0 && size.y > 0 && lastValidBoundsRef.current) {
        map.fitBounds(lastValidBoundsRef.current, { padding: fitPaddingFor(map), maxZoom: MAX_FIT_ZOOM, animate: false })
        fittedWhileHiddenRef.current = false
      }
    }, 0)
    return () => clearTimeout(timer)
  }, [map, resizeKey, lastValidBoundsRef, fittedWhileHiddenRef])

  return null
}

export function MapClickHandler({ onClearSelection, onEscape, markerInteractionRef }) {
  useMapEvents({
    click: (event) => {
      if (Date.now() - (markerInteractionRef.current || 0) < MARKER_CLICK_GUARD_MS) return
      const target = event.originalEvent?.target
      if (target?.closest?.('.leaflet-marker-icon')) return
      if (target?.closest?.('.leaflet-popup')) return
      if (target?.closest?.('.map-bottom-sheet')) return
      onClearSelection?.()
    },
    // Leaflet closes the popup on Esc; a pin left selected without it cannot be reopened.
    // It also swallows the key once any popup has been opened, so nothing outside the map
    // hears it: the page's own Esc action (closing the detail panel) is run from here.
    keydown: (event) => {
      if (event.originalEvent?.key !== 'Escape') return
      // Marked handled, for the case where Leaflet lets the key through to the page.
      event.originalEvent.preventDefault()
      onEscape?.()
    },
  })

  return null
}

export function MapLocationPicker({ enabled, onPickLocation, markerInteractionRef }) {
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

// Keeps the map near the country: without limits it pans and zooms out to a grey world.
// Checked when a move ends rather than with Leaflet's maxBounds, which pans at once when
// set and cuts short a fit that is still animating (the country config loads late).
export function MapLimits({ countryBounds }) {
  const map = useMap()

  useEffect(() => {
    const maxBounds = countryBounds.pad(MAX_BOUNDS_PAD)
    const keepInside = () => {
      const size = map.getSize()
      // Hidden map (mobile List tab): nothing to keep in view.
      if (size.x === 0 || size.y === 0) return
      map.panInsideBounds(maxBounds, { animate: true, duration: 0.3 })
    }
    map.on('moveend', keepInside)
    return () => map.off('moveend', keepInside)
  }, [map, countryBounds])

  return null
}

export function MapOverlayNavigator({ overlaySchoolId, overlayLocations, focusLocation, getCovered, getPinRoom }) {
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
    // Fit into the part of the map the header panel and the mobile sheet leave free.
    const fitOverlay = () => {
      const padding = overlayFitPadding(map.getSize(), getCovered(), OVERLAY_FIT_PADDING)
      if (!padding) {
        fitMapToPoints(map, points)
        return
      }
      map.fitBounds(L.latLngBounds(points), {
        ...padding,
        maxZoom: points.length === 1 ? SINGLE_POINT_ZOOM : MAX_FIT_ZOOM,
        animate: true,
        duration: 0.4,
      })
    }
    fitOverlay()
    const timer = setTimeout(fitOverlay, 120)
    lastOverlayRef.current = overlayKey
    return () => clearTimeout(timer)
  }, [overlaySchoolId, overlayLocations, map, getCovered])

  useEffect(() => {
    if (!focusLocation?.lat || !focusLocation?.lng) return
    panPinIntoRoom(map, [focusLocation.__lat ?? focusLocation.lat, focusLocation.__lng ?? focusLocation.lng], getPinRoom())
  }, [focusLocation, map, getPinRoom])

  return null
}

export function MapSelectionPan({ marker, mapPickedSchoolIdRef, restoredSelectionRef, getPinRoom }) {
  const map = useMap()
  const markerKey = marker?.key
  const schoolId = marker?.school?.id
  const lat = marker?.position?.[0]
  const lng = marker?.position?.[1]

  // Keyed on the selection, not the marker object: a data refresh must not pan again.
  useEffect(() => {
    const pickedOnMap = mapPickedSchoolIdRef.current
    mapPickedSchoolIdRef.current = null
    if (markerKey == null) return
    // Back from a school page: the map is where the parent left it, selection included.
    // Kept for as long as that pin stays the selected one (effects run twice in StrictMode).
    const restored = restoredSelectionRef.current
    if (restored && restored.schoolId === schoolId && pickedOnMap !== schoolId
      && (restored.markerKey ?? markerKey) === markerKey) {
      restored.markerKey = markerKey
      return
    }
    restoredSelectionRef.current = null
    // The room is measured when the selection changes; resizing the window must not pan.
    const room = getPinRoom()
    // A pin clicked on the map is already in view; moving the map under the cursor reads
    // as the pin jumping. Still pan when its popup or the sheet would not fit.
    if (pickedOnMap === schoolId
      && pinHasRoom(map.latLngToContainerPoint([lat, lng]), map.getSize(), room)) return
    panPinIntoRoom(map, [lat, lng], room)
  }, [markerKey, schoolId, lat, lng, map, mapPickedSchoolIdRef, restoredSelectionRef, getPinRoom])

  return null
}

export function ResetViewControl({ schoolPoints, userLocation, lastValidBoundsRef, label, defaultZoom, countryBounds }) {
  const map = useMap()

  const handleResetView = useCallback(() => {
    const points = collectPoints(schoolPoints, userLocation)

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

    map.fitBounds(countryBounds, { padding: fitPaddingFor(map), maxZoom: defaultZoom, animate: true, duration: 0.4 })
  }, [map, schoolPoints, userLocation, lastValidBoundsRef, defaultZoom, countryBounds])

  return (
    // Bottom-right, so a school's popup (opened near the top) never covers it.
    <div className="leaflet-bottom leaflet-right">
      <div className="leaflet-control">
        <button
          type="button"
          onClick={handleResetView}
          className="mb-8 mr-3 inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-lg border border-neutral-200 bg-white text-neutral-800 shadow-panel hover:bg-neutral-50 transition-colors"
        >
          {label}
        </button>
      </div>
    </div>
  )
}
