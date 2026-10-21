import { useEffect, useRef, useState } from 'react'
import { geocodeAddress, reverseGeocode, cancelGeocode } from '../../utils/geocoding.js'
import { mapSearchQuery } from '../../admission/catchments.js'
import { loadAddresses } from '../../admission/loadCatchments.js'
import { useSavedAdmission } from '../../hooks/useSavedAdmission.js'

function readStoredUserLocation(fallbackAddress) {
  try {
    const parsed = JSON.parse(localStorage.getItem('userLocation') || 'null')
    if (typeof parsed?.lat === 'number' && typeof parsed?.lng === 'number') {
      return { lat: parsed.lat, lng: parsed.lng, address: parsed.address || fallbackAddress, savedHome: parsed.savedHome }
    }
  } catch {
    // Corrupt or blocked storage: start without a location.
  }
  return null
}

const persistLocation = (location) => {
  localStorage.setItem('userLocation', JSON.stringify(location))
}

// The parent's location: GPS, an address lookup or a point picked on the map, kept in
// localStorage. View changes around picking on the map stay with the page.
export function useUserLocation({ t, geocodingConfig, arriveNearSavedHome = false, onArrivalHandled = () => {} }) {
  const [userLocation, setUserLocation] = useState(() => readStoredUserLocation(t('location.currentLocation')))
  const [addressInput, setAddressInput] = useState('')
  const [locationError, setLocationError] = useState(null)
  const [isLocating, setIsLocating] = useState(false)
  // false, or which lookup runs: 'address' (typed or from the name search) or 'saved'.
  const [isGeocoding, setIsGeocoding] = useState(false)
  const errorTimeoutRef = useRef(null)

  useEffect(() => {
    return () => {
      if (errorTimeoutRef.current) {
        clearTimeout(errorTimeoutRef.current)
      }
      cancelGeocode()
    }
  }, [])

  const showLocationError = (message, { temporary = false } = {}) => {
    if (errorTimeoutRef.current) {
      clearTimeout(errorTimeoutRef.current)
    }
    setLocationError(message)
    if (temporary) {
      errorTimeoutRef.current = setTimeout(() => {
        setLocationError(null)
      }, 1500)
    }
  }

  // Each way of setting a location takes a token; a slower earlier request (GPS or an
  // address lookup) must not overwrite a location the parent has since set another way.
  const locationRequestRef = useRef(0)
  const startLocationRequest = () => {
    // Also stops a lookup still waiting to send its second request.
    cancelGeocode()
    locationRequestRef.current += 1
    return locationRequestRef.current
  }

  const locateMe = () => {
    const request = startLocationRequest()
    setLocationError(null)
    setIsGeocoding(false)

    if (!navigator.geolocation) {
      showLocationError(t('location.errorUnavailable'))
      return
    }

    setIsLocating(true)
    navigator.geolocation.getCurrentPosition(
      (position) => {
        if (request !== locationRequestRef.current) return
        const newLocation = {
          lat: position.coords.latitude,
          lng: position.coords.longitude,
          address: t('location.currentLocation'),
        }
        setUserLocation(newLocation)
        setAddressInput('')
        persistLocation(newLocation)
        setIsLocating(false)
      },
      (err) => {
        if (request !== locationRequestRef.current) return
        if (err.code === 1) {
          showLocationError(t('location.errorDenied'))
        } else {
          showLocationError(t('location.errorUnavailable'))
        }
        setIsLocating(false)
      },
      {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 300000,
      }
    )
  }

  // One address lookup: `lookup(isCurrent)` resolves to a geocoding result, `extra` is kept
  // with it. Resolves to true when the location was set, false when the lookup failed and
  // null when another request replaced it.
  const runLookup = async (lookup, extra = {}, kind = 'address') => {
    const request = startLocationRequest()
    setIsLocating(false)
    setIsGeocoding(kind)
    setLocationError(null)

    try {
      const result = await lookup(() => request === locationRequestRef.current)
      if (request !== locationRequestRef.current) return null
      const newLocation = {
        lat: result.lat,
        lng: result.lng,
        address: result.address,
        ...extra,
      }
      setUserLocation(newLocation)
      persistLocation(newLocation)
      return true
    } catch (err) {
      if (err?.code === 'CANCELED' || request !== locationRequestRef.current) return null
      if (kind === 'saved' && ['NO_RESULTS', 'OUTSIDE_AREA'].includes(err?.code)) {
        // Not something the parent typed here, so no typing advice.
        showLocationError(t('location.errorSavedHome'))
      } else if (err?.code === 'NO_RESULTS') {
        showLocationError(t('location.errorNotFound'))
      } else if (err?.code === 'OUTSIDE_AREA') {
        const place = String(err.place || '').split(',').slice(0, 3).join(',').trim()
        showLocationError(t('location.errorOutsideCity', { place }))
      } else if (err?.code === 'RATE_LIMIT') {
        showLocationError(t('location.errorRateLimited'))
      } else if (err?.code === 'TOO_FAST') {
        showLocationError(t('location.errorPleaseWait'), { temporary: true })
      } else if (err?.code !== 'EMPTY') {
        showLocationError(t('location.errorUnableToGeocode'))
      }
      return false
    } finally {
      if (request === locationRequestRef.current) setIsGeocoding(false)
    }
  }

  const searchAddress = async (text = addressInput) => {
    const trimmedAddress = text.trim()
    if (!trimmedAddress) return false
    if (text !== addressInput) setAddressInput(text)
    return runLookup(() => geocodeAddress(trimmedAddress, geocodingConfig))
  }

  // The home address saved in the admission calculator (street id and number from the
  // official address table). Finding it on the map sends it to OpenStreetMap's search, so
  // it runs only when the parent taps, like the calculator's own "show on the map".
  const savedHome = useSavedAdmission().home
  const savedHomeKey = savedHome ? `${savedHome.streetId}|${savedHome.number}` : null
  const locateSavedHome = () => {
    if (!savedHome) return Promise.resolve(false)
    setAddressInput('')
    return runLookup(async isCurrent => {
      const { streets } = await loadAddresses()
      // Replaced while the address table loaded: send nothing.
      if (!isCurrent()) {
        const error = new Error('Canceled')
        error.code = 'CANCELED'
        throw error
      }
      const street = streets.find(s => s.id === savedHome.streetId)
      if (!street) {
        const error = new Error('Saved street not in the address table')
        error.code = 'NO_RESULTS'
        throw error
      }
      return geocodeAddress(mapSearchQuery(street, savedHome.number), {
        ...geocodingConfig,
        city_suffix: '',
        remember: false,
        // OpenStreetMap lacks many house numbers; the street is close enough for distances.
        fallback: mapSearchQuery(street, '').replace(/\s+,/, ','),
      })
    }, { savedHome: savedHomeKey }, 'saved')
  }

  const clearLocation = () => {
    startLocationRequest()
    setIsLocating(false)
    setIsGeocoding(false)
    setUserLocation(null)
    setAddressInput('')
    setLocationError(null)
    localStorage.removeItem('userLocation')
  }

  const setLocationFromMap = (lat, lng) => {
    startLocationRequest()
    setIsLocating(false)
    setIsGeocoding(false)
    const newLocation = {
      lat,
      lng,
      address: t('location.selectedOnMap'),
    }
    setUserLocation(newLocation)
    setAddressInput('')
    setLocationError(null)
    persistLocation(newLocation)

    reverseGeocode(lat, lng, geocodingConfig)
      .then((result) => {
        if (!result?.address) return
        setUserLocation(prev => {
          if (!prev) return prev
          if (prev.lat !== lat || prev.lng !== lng) return prev
          const updated = { ...prev, address: result.address }
          persistLocation(updated)
          return updated
        })
      })
      .catch(() => {
        // Silent fallback to "Selected on map" when reverse geocoding fails.
      })
  }

  const changeAddressInput = (value) => {
    setAddressInput(value)
    setLocationError(null)
  }

  // Arriving from the calculator's "schools near your saved address" link: that tap is the
  // parent's go to look the address up, once the map config (the city's bounds) is here.
  // The flag is then dropped from the history entry, so a reload or Back does not repeat it.
  const canUseSaved = Boolean(savedHomeKey) && userLocation?.savedHome !== savedHomeKey
  const arrivalDoneRef = useRef(false)
  const hasBounds = Boolean(geocodingConfig?.bounds)
  useEffect(() => {
    if (arrivalDoneRef.current || !arriveNearSavedHome || !hasBounds) return
    arrivalDoneRef.current = true
    onArrivalHandled()
    if (canUseSaved) locateSavedHome()
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs once, on arrival
  }, [arriveNearSavedHome, hasBounds])

  return {
    userLocation,
    addressInput,
    locationError,
    isLocating,
    isGeocoding,
    changeAddressInput,
    clearLocationError: () => setLocationError(null),
    locateMe,
    searchAddress,
    // Offered only while the location is not already the saved home.
    canUseSavedHome: canUseSaved,
    locateSavedHome,
    clearLocation,
    setLocationFromMap,
  }
}
