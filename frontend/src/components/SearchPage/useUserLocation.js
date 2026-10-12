import { useEffect, useRef, useState } from 'react'
import { geocodeAddress, reverseGeocode, cancelGeocode } from '../../utils/geocoding.js'

function readStoredUserLocation(fallbackAddress) {
  try {
    const parsed = JSON.parse(localStorage.getItem('userLocation') || 'null')
    if (typeof parsed?.lat === 'number' && typeof parsed?.lng === 'number') {
      return { lat: parsed.lat, lng: parsed.lng, address: parsed.address || fallbackAddress }
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
export function useUserLocation({ t, geocodingConfig }) {
  const [userLocation, setUserLocation] = useState(() => readStoredUserLocation(t('location.currentLocation')))
  const [addressInput, setAddressInput] = useState('')
  const [locationError, setLocationError] = useState(null)
  const [isLocating, setIsLocating] = useState(false)
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

  const searchAddress = async () => {
    const trimmedAddress = addressInput.trim()
    if (!trimmedAddress) return

    const request = startLocationRequest()
    setIsLocating(false)
    setIsGeocoding(true)
    setLocationError(null)

    try {
      const result = await geocodeAddress(trimmedAddress, geocodingConfig)
      if (request !== locationRequestRef.current) return
      const newLocation = {
        lat: result.lat,
        lng: result.lng,
        address: result.address,
      }
      setUserLocation(newLocation)
      persistLocation(newLocation)
    } catch (err) {
      if (err?.code === 'CANCELED' || request !== locationRequestRef.current) return
      if (err?.code === 'NO_RESULTS') {
        showLocationError(t('location.errorNotFound'))
      } else if (err?.code === 'RATE_LIMIT') {
        showLocationError(t('location.errorRateLimited'))
      } else if (err?.code === 'TOO_FAST') {
        showLocationError(t('location.errorPleaseWait'), { temporary: true })
      } else if (err?.code !== 'EMPTY') {
        showLocationError(t('location.errorUnableToGeocode'))
      }
    } finally {
      if (request === locationRequestRef.current) setIsGeocoding(false)
    }
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
    clearLocation,
    setLocationFromMap,
  }
}
