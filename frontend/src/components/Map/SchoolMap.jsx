import {
  useEffect,
  useMemo,
  useRef,
  memo,
  useCallback,
  useState,
} from 'react'
import {
  MapContainer,
  TileLayer,
  Marker,
  Popup,
  Pane,
} from 'react-leaflet'
import MarkerClusterGroup from 'react-leaflet-cluster'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { parseCoordinate } from '../../utils/coordinates'
import { getSchoolName } from '../../utils/i18n'
import { useCompare } from '../../context/CompareContext'
import { useCountry } from '../../context/CountryContext'
import { getAgeGroupKeys } from '../../utils/countryConfig'
import { AGE_GROUP_KEYS } from '../../utils/education'
import { useStableCallback } from '../../hooks/useStableCallback'
import { isDesktopViewport } from '../../utils/searchViewState'
import {
  pickSelectedMarker,
  stackedMarkersByKey,
  joinSamePlaceMarkers,
  normalizeAgeGroups,
  pinnedLocations,
} from '../../utils/mapFit'
import { getLocationTags } from '../../utils/schoolLocation'
import {
  MapBoundsWatcher,
  MapClickHandler,
  MapLimits,
  MapLocationPicker,
  MapOverlayNavigator,
  MapResizer,
  MapSelectionPan,
  MapUpdater,
  ResetViewControl,
} from './MapControls'
import { OverlayLocationMarker, PopupContent, SchoolMarker } from './MapMarkers'
import { createClusterIcon, createUserMarkerIcon } from './mapIcons'
import { orderAgeGroups } from './mapLabels'
import {
  COMPARE_BAR_COVER,
  FALLBACK_BOUNDS,
  FALLBACK_CENTER,
  FALLBACK_ZOOM,
  MAP_CONTROLS_COVER,
  MIN_ZOOM,
  PIN_ROOM_DESKTOP,
  PIN_ROOM_MOBILE,
  pointInBounds,
  STACK_LIST_ROOM,
  TILE_URL,
  UNCLUSTERED_ZOOM,
} from './mapView'


function SchoolMap({
  schools,
  activeAgeGroup,
  selectedSchoolId,
  hoveredSchoolId,
  onSchoolSelect,
  onClearSelection,
  onEscape,
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
  detailOpen = false,
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
  const fittedWhileHiddenRef = useRef(false)
  const markerInteractionRef = useRef(0)
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 768)
  const [sheetOffset, setSheetOffset] = useState(120)
  const [legendOpen, setLegendOpen] = useState(false)
  const sheetStartRef = useRef(null)
  const sheetRef = useRef(null)
  const overlayPanelRef = useRef(null)

  // What covers the map's top (locations panel) and bottom (mobile sheet), in px.
  const getCovered = useStableCallback(() => {
    const panel = overlayPanelRef.current
    return {
      top: panel ? panel.offsetTop + panel.offsetHeight : 0,
      bottom: sheetRef.current?.offsetHeight || MAP_CONTROLS_COVER + (hasCompare ? COMPARE_BAR_COVER : 0),
    }
  })
  // Called after render, so it sees the current selection's sheet, panel and stack.
  const getPinRoom = useStableCallback(() => {
    const covered = getCovered()
    if (isMobile) {
      return { ...PIN_ROOM_MOBILE, top: PIN_ROOM_MOBILE.top + covered.top, bottom: PIN_ROOM_MOBILE.bottom + covered.bottom }
    }
    const stacked = selectedMarker ? stackedByKey.get(selectedMarker.key)?.length || 0 : 0
    const stackList = stacked > 0 ? STACK_LIST_ROOM.base + stacked * STACK_LIST_ROOM.perSchool : 0
    return { ...PIN_ROOM_DESKTOP, top: PIN_ROOM_DESKTOP.top + covered.top + stackList }
  })

  const noteMarkerInteraction = useCallback(() => {
    markerInteractionRef.current = Date.now()
  }, [])

  const mapPickedSchoolIdRef = useRef(null)
  // The school that was selected when a saved view was restored (see MapSelectionPan).
  const restoredSelectionRef = useRef(initialView && selectedSchoolId ? { schoolId: selectedSchoolId, markerKey: null } : null)
  // The pin that was clicked, so a school with several locations highlights that one.
  const [pickedMarker, setPickedMarker] = useState(null)
  const handleMarkerSelect = useCallback((school, marker) => {
    mapPickedSchoolIdRef.current = school?.id ?? null
    setPickedMarker(marker ? { schoolId: school.id, key: marker.key } : null)
    onSchoolSelect?.(school)
  }, [onSchoolSelect])
  const pickedMarkerKey = pickedMarker?.schoolId === selectedSchoolId ? pickedMarker.key : null

  // Selecting another school, or clearing the selection, forgets the clicked pin.
  useEffect(() => {
    setPickedMarker(prev => (prev && prev.schoolId !== selectedSchoolId ? null : prev))
  }, [selectedSchoolId])

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
      pinnedLocations(school, activeAgeGroup)?.forEach((location, idx) => {
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
    return joinSamePlaceMarkers(result)
  }, [schools, activeAgeGroup, countryBounds])

  const stackedByKey = useMemo(() => stackedMarkersByKey(markers), [markers])
  const schoolPoints = useMemo(() => markers.map(marker => marker.position), [markers])

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
      return pickSelectedMarker(candidates, pickedMarkerKey)
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
  }, [markers, selectedSchoolId, overlayLocations, overlaySelectedLocationId, overlaySchool, overlayFocusActive, pickedMarkerKey])

  const baseMarkers = useMemo(() => {
    if (!overlayActiveOnMap) return markers
    if (hideOthers) return []
    return markers.filter(marker => marker.school.id !== overlaySchoolId)
  }, [markers, overlaySchoolId, hideOthers, overlayActiveOnMap])

  const selectedMarkers = useMemo(() => {
    if (!selectedSchoolId) return []
    if (overlayActiveOnMap) return []
    const candidates = baseMarkers.filter(marker => selectedSchoolId === marker.school.id)
    const highlighted = pickSelectedMarker(candidates, pickedMarkerKey)
    return highlighted ? [highlighted] : []
  }, [baseMarkers, selectedSchoolId, overlayActiveOnMap, pickedMarkerKey])

  // The selected school's other locations stay on the map as ordinary pins.
  const regularMarkers = useMemo(() => {
    const highlightedKey = selectedMarkers[0]?.key
    return highlightedKey ? baseMarkers.filter(marker => marker.key !== highlightedKey) : baseMarkers
  }, [baseMarkers, selectedMarkers])

  // Desktop only, and not beside the detail panel: it already shows the school, and a
  // popup closed there (Esc, its ×) left an orange pin that could not be reopened.
  const showPopups = !isMobile && !detailOpen
  // Phones: a school's locations get the whole map until one of them is tapped; the sheet
  // then takes the place of the locations panel (a short phone has no room for both).
  const sheetMarker = isMobile
    && !(overlayActiveOnMap && selectedSchoolId === overlaySchoolId && !overlaySelectedLocationId)
    ? selectedMarker
    : null

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
        minZoom={MIN_ZOOM}
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
                  showPopup={showPopups}
                  addressShown={showPopups || Boolean(sheetMarker)}
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
          schoolPoints={schoolPoints}
          userLocation={userLocation}
          autoFit={autoFit}
          lastValidBoundsRef={lastValidBoundsRef}
          defaultZoom={defaultZoom}
          countryBounds={countryBounds}
          keepInitialView={Boolean(initialView)}
          fittedWhileHiddenRef={fittedWhileHiddenRef}
        />

        <MapBoundsWatcher onBoundsChange={onBoundsChange} onViewChange={onViewChange} />
        <MapResizer resizeKey={resizeKey} lastValidBoundsRef={lastValidBoundsRef} fittedWhileHiddenRef={fittedWhileHiddenRef} />
        <MapClickHandler
          onClearSelection={onClearSelection}
          onEscape={onEscape || onClearSelection}
          markerInteractionRef={markerInteractionRef}
        />
        <MapLocationPicker
          enabled={isPickingLocation}
          onPickLocation={onPickLocation}
          markerInteractionRef={markerInteractionRef}
        />
        <MapLimits countryBounds={countryBounds} />
        <MapSelectionPan
          marker={selectedMarker}
          mapPickedSchoolIdRef={mapPickedSchoolIdRef}
          restoredSelectionRef={restoredSelectionRef}
          getPinRoom={getPinRoom}
        />
        <MapOverlayNavigator
          overlaySchoolId={overlaySchoolId}
          overlayLocations={overlayLocations}
          focusLocation={overlayFocusLocation}
          getCovered={getCovered}
          getPinRoom={getPinRoom}
        />

        <ResetViewControl
          schoolPoints={schoolPoints}
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
          disableClusteringAtZoom={UNCLUSTERED_ZOOM}
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
              onSelect={handleMarkerSelect}
              onDeselect={onClearSelection}
              showPopup={showPopups}
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
              stackedMarkers={stackedByKey.get(marker.key)}
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
              onSelect={handleMarkerSelect}
              onDeselect={onClearSelection}
              showPopup={showPopups && !overlayFocusActive}
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
            stackedMarkers={stackedByKey.get(marker.key)}
          />
        ))}

        {/* overlay markers and flow are rendered inside the overlay pane */}
      </MapContainer>

      {sheetMarker && (
        <div
          ref={sheetRef}
          className="map-bottom-sheet absolute inset-x-0 bottom-0 z-[1200] transition-transform duration-300"
          style={{ transform: `translateY(${sheetOffset}px)` }}
        >
          {/* Clear the fixed compare bar so its buttons do not cover the sheet's actions. */}
          <div
            className={`mx-3 ${hasCompare ? 'mb-[calc(4.5rem+env(safe-area-inset-bottom))]' : 'mb-3'} rounded-2xl bg-white shadow-2xl border border-neutral-200`}
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
              marker={sheetMarker}
              t={t}
              language={i18n.language}
              userLocation={userLocation}
              onCompareToggle={handleCompareToggle}
              onViewDetails={handleViewDetails}
              onClose={onClearSelection}
              isMobile
              inCompare={isInCompare(sheetMarker.school.id)}
              canAddMore={canAddMore}
              activeAgeGroup={activeAgeGroup}
              onShowLocations={onShowLocations}
              overlayActive={locationOverlay?.schoolId === sheetMarker.school.id}
              stackedMarkers={stackedByKey.get(sheetMarker.key)}
              onSelectMarker={handleMarkerSelect}
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

      {/* Map legend: always open on desktop, a button on phones where it covered 40% of the map. */}
      <div className={`absolute left-4 bg-white rounded-lg shadow-panel z-[1000] ${hasCompare ? 'bottom-24' : 'bottom-6'}`}>
        {isMobile && (
          <button
            type="button"
            onClick={() => setLegendOpen(open => !open)}
            aria-expanded={legendOpen}
            className="flex h-9 items-center gap-1.5 px-3 text-xs font-medium text-neutral-700"
          >
            {t('map.legend')}
            <svg className={`h-3 w-3 transition-transform ${legendOpen ? '' : 'rotate-180'}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        )}
        {(!isMobile || legendOpen) && (
          <div className={isMobile ? 'px-3 pb-3' : 'p-3'}>
            {!isMobile && <p className="text-xs font-medium text-neutral-700 mb-2">{t('map.legend')}</p>}
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-primary-500" />
                <span className="text-xs text-neutral-600">{t('schoolTypes.state')}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-full bg-violet-500" />
                <span className="text-xs text-neutral-600">{t('schoolTypes.private')}</span>
              </div>
              <div className="flex items-center gap-2 pt-1">
                <span className="w-3 h-3 rounded-full border-2 border-neutral-500" />
                <span className="text-xs text-neutral-600">{t('map.legendSchool')}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="w-3 h-3 rounded-sm border-2 border-neutral-500" />
                <span className="text-xs text-neutral-600">{t('map.legendKindergarten')}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="flex h-3.5 min-w-[0.875rem] items-center justify-center rounded-full bg-neutral-700 px-1 text-[9px] font-bold leading-none text-white">2</span>
                <span className="text-xs text-neutral-600">{t('map.legendStacked')}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {overlaySchool && !(sheetMarker && overlayActiveOnMap) && (
        <div
          ref={overlayPanelRef}
          className="absolute top-4 left-4 max-w-[calc(100%-2rem)] bg-white/95 backdrop-blur rounded-lg shadow-panel p-3 z-[1000]"
        >
          <p className="mb-2 text-xs font-semibold text-neutral-800 line-clamp-2">
            {t('map.locationsFor', { name: getSchoolName(overlaySchool, i18n.language) })}
          </p>
          <div className="flex flex-wrap items-center gap-2">
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
