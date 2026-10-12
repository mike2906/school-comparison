import { useTranslation } from 'react-i18next'
import { useCountry } from '../../context/CountryContext'
import { getAddress } from '../../utils/i18n'
import { getFocusEmoji } from '../../utils/locationFocus'
import { orderAgeGroups } from '../../utils/ageGroupOrder'
import { parseCoordinate } from '../../utils/coordinates'
import { getLocationAgeGroups } from './cardHelpers'
import { getAgeGroupShifts, getLocationTags } from '../../utils/schoolLocation'

/** The card's open "locations / age groups and shifts" panel. */
function LocationsPanel({
  school,
  orderedLocations,
  flowGroups,
  canShowOnMap,
  focusedLocationId,
  overlayActive,
  handleShowAllLocations,
  handleFocusLocation,
  handleClearLocations,
}) {
  const { t, i18n } = useTranslation()
  const { config: countryConfig } = useCountry()

  return (
    <div className="mt-3 rounded-xl border border-neutral-200 bg-white px-4 py-3 space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <p className="text-sm font-semibold text-neutral-900">
            {school.school_type === 'state' ? t('schools.ageGroupsShifts') : t('schools.locations')}
          </p>
          {flowGroups.length > 1 && (
            <div className="flex flex-wrap items-center gap-1 text-xs text-neutral-500">
              {flowGroups.map((group, idx) => (
                <span key={group} className="inline-flex items-center gap-1">
                  <span className="px-2 py-0.5 rounded-md bg-neutral-100 text-neutral-600 font-medium">
                    {t(`ageGroups.${group}`)}
                  </span>
                  {idx < flowGroups.length - 1 && <span className="text-neutral-300">→</span>}
                </span>
              ))}
            </div>
          )}
        </div>
        {orderedLocations.length > 1 && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={`px-2.5 py-1 text-xs font-semibold rounded-full border transition-colors ${
                canShowOnMap
                  ? 'border-primary-200 text-primary-700 hover:bg-primary-50'
                  : 'border-neutral-200 text-neutral-400 cursor-not-allowed'
              }`}
              onClick={handleShowAllLocations}
              disabled={!canShowOnMap}
              title={canShowOnMap ? '' : t('schools.mapLocationMissing')}
            >
              {t('schools.showAllOnMap')}
            </button>
          </div>
        )}
      </div>

      <div className="space-y-2">
        {orderedLocations.map((locationItem, idx) => {
          const locationKey = locationItem.id ?? `${school.id}-${idx}`
          const isFocused = focusedLocationId === locationItem.id
          const addressLabel = getAddress(locationItem, i18n.language)
          const locationGroups = orderAgeGroups(getLocationAgeGroups(locationItem), countryConfig)
          const shiftEntries = getAgeGroupShifts(locationItem)
          const shiftByGroup = new Map(shiftEntries.map(entry => [entry.age_group, entry]))
          const locationTags = getLocationTags(locationItem)
          const hasMultipleLocations = orderedLocations.length > 1
          return (
            <button
              key={locationKey}
              type="button"
              onClick={(event) => handleFocusLocation(event, locationItem.id)}
              disabled={!hasMultipleLocations}
              className={`w-full text-left rounded-lg px-3 py-2 transition-colors ${
                hasMultipleLocations
                  ? isFocused
                    ? 'border border-primary-300 bg-primary-50'
                    : 'border border-neutral-200 bg-white hover:bg-neutral-50'
                  : 'bg-transparent'
              }`}
            >
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="space-y-1">
                  {addressLabel && (
                    <div className="text-sm font-semibold text-neutral-800">{addressLabel}</div>
                  )}
                  {locationTags.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {locationTags.map(tag => (
                        <span
                          key={tag}
                          className="inline-flex items-center rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] font-semibold text-neutral-600"
                        >
                          {getFocusEmoji(tag) ? `${getFocusEmoji(tag)} ` : ''}
                          {t(`locationTags.${tag}`, { defaultValue: tag })}
                        </span>
                      ))}
                    </div>
                  )}
                  <div className="space-y-1">
                    {locationGroups.map(group => {
                      const shiftInfo = shiftByGroup.get(group)
                      return (
                        <div
                          key={group}
                          className="text-xs text-neutral-600"
                        >
                          <span className="font-semibold text-neutral-700">
                            {t(`ageGroups.${group}`)}
                          </span>
                          {shiftInfo?.shift && (
                            <span className="text-neutral-500"> • {t(`shifts.${shiftInfo.shift}`)}</span>
                          )}
                          {shiftInfo?.has_organised_groups && (
                            <span className="text-primary-600"> • {t('schools.organisedGroups')}</span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
                {!Number.isFinite(parseCoordinate(locationItem.lat)) || !Number.isFinite(parseCoordinate(locationItem.lng)) ? (
                  <span className="text-[11px] uppercase tracking-wide text-neutral-400">
                    {t('schools.mapLocationMissing')}
                  </span>
                ) : (
                  locationItem.is_primary && (
                    <span className="text-[10px] uppercase tracking-wide text-neutral-400">
                      {t('schools.primaryLocation')}
                    </span>
                  )
                )}
              </div>
            </button>
          )
        })}
      </div>

      {overlayActive && (
        <div className="flex items-center justify-between text-xs text-neutral-400">
          <span>{t('schools.locationsShownOnMap')}</span>
          <button
            type="button"
            className="text-xs font-semibold text-neutral-500 hover:text-neutral-700"
            onClick={handleClearLocations}
          >
            {t('schools.clearLocations')}
          </button>
        </div>
      )}
    </div>
  )
}

export default LocationsPanel
