import {
  useEffect,
  useMemo,
  useRef,
  memo,
} from 'react'
import { Marker, Popup, Tooltip } from 'react-leaflet'
import L from 'leaflet'
import { calculateDistance } from '../../utils/distance'
import { formatDistance } from '../../utils/format'
import { getSchoolName, getAddress } from '../../utils/i18n'
import { useCountry } from '../../context/CountryContext'
import { AGE_GROUP_KEYS } from '../../utils/education'
import { schoolLevelLabel, schoolTypeLabel } from '../../utils/levelLabel'
import { getLocationTags, getShiftForAgeGroup } from '../../utils/schoolLocation'
import { createLocationMarkerIcon, createMarkerIcon } from './mapIcons'
import {
  getLocationFocusLabels,
  getLocationFocusMarkerLabel,
  getLocationFullLabel,
  getLocationShortLabel,
} from './mapLabels'


export function PopupContent({
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
  stackedMarkers,
  onSelectMarker,
}) {
  const { config } = useCountry()
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
        {/* Popup: a very long name is cut to three lines so the popup fits above the pin. */}
        <h3
          className={`text-base font-semibold text-neutral-900 leading-snug ${isMobile ? '' : 'line-clamp-3'}`}
          title={isMobile ? undefined : schoolName}
        >
          {schoolName}
        </h3>
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
          {schoolTypeLabel(marker.school, t, displayType)}
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
            <span>
              {formatDistance(distanceKm, { approximate: marker.location.coordinates_approximate, language, t })}{' '}
              {t('location.fromYou')}
            </span>
          </div>
        )}

        {marker.location.coordinates_approximate && (
          <p className="text-xs text-neutral-500">{t('map.approximatePin')}</p>
        )}

        {/* The groups are already in the badge row; only add shift / care details here. */}
        {(shiftLabel || hasOrganisedGroups) && (
          <div className="flex flex-wrap items-center gap-2 text-neutral-500">
            {[shiftLabel, hasOrganisedGroups ? t('schools.organisedGroups') : null].filter(Boolean).join(' • ')}
          </div>
        )}

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

      {stackedMarkers?.length > 0 && (
        <div className="border-t border-neutral-100 pt-2">
          <p className="text-xs text-neutral-500">
            {t(marker.samePlace && stackedMarkers.every(other => other.samePlace) ? 'map.samePlace' : 'map.alsoHere')}
          </p>
          <ul className="mt-1 space-y-1">
            {stackedMarkers.map(other => (
              <li key={other.key}>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation()
                    onSelectMarker?.(other.school, other)
                  }}
                  className="text-left text-sm font-medium text-primary-700 hover:text-primary-800 hover:underline"
                >
                  {getSchoolName(other.school, language)}
                  {/* Schools sharing an address often share a name too (kindergarten + school). */}
                  <span className="font-normal text-neutral-500">
                    {' · '}{schoolLevelLabel(other.school, t, config)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

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
          className="flex-1 px-3 py-2 text-sm font-medium text-white bg-primary-700 hover:bg-primary-800 rounded-lg transition-colors"
        >
          {t('schools.viewDetails')}
        </button>
      </div>
    </div>
  )
}

export const SchoolMarker = memo(function SchoolMarker({
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
  stackedMarkers,
}) {
  const markerRef = useRef(null)
  const schoolType = marker.school.school_type
  const isKindergarten = marker.school.education_level === 'kindergarten'
  const stackCount = stackedMarkers ? stackedMarkers.length + 1 : 0
  // A new icon object makes react-leaflet rebuild the marker DOM, so only create one when its look changes
  const icon = useMemo(
    () => createMarkerIcon(schoolType, { isSelected, isHovered, isDimmed, isKindergarten, stackCount }),
    [schoolType, isSelected, isHovered, isDimmed, isKindergarten, stackCount]
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
          onSelect(marker.school, marker)
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
            stackedMarkers={stackedMarkers}
            onSelectMarker={onSelect}
          />
        </Popup>
      )}
    </Marker>
  )
})

export const OverlayLocationMarker = memo(function OverlayLocationMarker({
  marker,
  isSchoolSelected,
  isSelected,
  ageGroupOrder,
  activeAgeGroup,
  labelMode = 'age',
  showPopup,
  addressShown,
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
      {/* Popup and tooltip go in Leaflet's own panes: inside the overlay pane the other
          location markers are drawn over them. The popup or the sheet of the selected
          location already has the address. */}
      {fullLabel && !(isSelected && addressShown) && (
        <Tooltip pane="tooltipPane" direction="top" offset={[0, -14]} opacity={0.9} sticky>
          {fullLabel}
        </Tooltip>
      )}
      {showPopup && (
        <Popup pane="popupPane" autoPan={false} closeButton={false}>
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
