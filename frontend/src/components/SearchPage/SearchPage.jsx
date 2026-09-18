import { useState, useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams, useNavigate } from 'react-router-dom'
import debounce from 'lodash.debounce'
import Layout from '../Layout/Layout'
import SchoolMap from '../Map/SchoolMap'
import SchoolCard from '../SchoolCard/SchoolCard'
import SchoolCardSkeleton from '../SchoolCard/SchoolCardSkeleton'
import { useSchools } from '../../hooks/useSchools'
import { calculateDistance } from '../../utils/distance'
import { geocodeAddress, reverseGeocode, cancelGeocode } from '../../utils/geocoding'
import { getSchoolName } from '../../utils/i18n'
import { useCompare } from '../../context/CompareContext'
import { useCountry } from '../../context/CountryContext'
import { fetchAvailableFilters, fetchExamAverages } from '../../api/schools'
import { getAgeGroupKeys } from '../../utils/countryConfig'
import { AGE_GROUP_KEYS } from '../../utils/education'
import { getLanguageFocusPairs } from '../../utils/schoolAttributes'
import { monthlyEquivalent } from '../../utils/pricing'

const TYPE_SORT_ORDER = {
  state: 0,
  private: 1,
  international: 2,
}

const EMPTY_LIST = []

const DEFAULT_ADVANCED_OPTIONS = {
  language_focus_levels: ['immersion', 'bilingual', 'enrichment'],
  special_programs: ['music_program', 'sports_program', 'arts_program', 'extended_day', 'meals_provided'],
  facilities: ['sports_facilities', 'cafeteria', 'library', 'computer_lab', 'transportation'],
  teaching_approach: ['montessori', 'waldorf', 'ib_program', 'project_based'],
}

function SearchPage() {
  const { t, i18n } = useTranslation()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { compareList } = useCompare()
  const { config, countryCode } = useCountry()

  const geocodingConfig = config?.map_config?.geocoding || {}

  const [selectedSchoolId, setSelectedSchoolId] = useState(null)
  const [hoveredSchoolId, setHoveredSchoolId] = useState(null)
  const [viewMode, setViewMode] = useState('list-map') // 'list-map', 'map-only', 'list-only'
  const [mobileTab, setMobileTab] = useState('list') // 'list' or 'map'
  const [isFiltersOpen, setIsFiltersOpen] = useState(false) // For tablet/mobile drawer
  const [userLocation, setUserLocation] = useState(null)
  const [addressInput, setAddressInput] = useState('')
  const [isManualInput, setIsManualInput] = useState(false)
  const [locationError, setLocationError] = useState(null)
  const [isLocating, setIsLocating] = useState(false)
  const [isGeocoding, setIsGeocoding] = useState(false)
  const [isPickingLocation, setIsPickingLocation] = useState(false)
  const [distanceFilter, setDistanceFilter] = useState('any')
  const [sortBy, setSortBy] = useState('name')
  const [searchInBounds, setSearchInBounds] = useState(false)
  const [mapBounds, setMapBounds] = useState(null)
  const [openLocationsId, setOpenLocationsId] = useState(null)
  const [locationOverlay, setLocationOverlay] = useState({
    schoolId: null,
    focusLocationId: null,
    hideOthers: false,
  })
  const [highlightLocation, setHighlightLocation] = useState(false)
  const [locationToast, setLocationToast] = useState(null)
  const previousLocationRef = useRef(null)
  const previousViewModeRef = useRef(null)
  const previousMobileTabRef = useRef(null)
  const locationSectionRef = useRef(null)
  const errorTimeoutRef = useRef(null)
  const toastTimeoutRef = useRef(null)
  const latestGeocodeRef = useRef(0)
  const cardRefs = useRef(new Map())
  const scrollOnSelectRef = useRef(false)

  // Read filters from URL
  const ageGroup = searchParams.get('age_group')
  const rawSchoolType = searchParams.get('school_type')
  const schoolType = rawSchoolType === 'international' ? 'private' : rawSchoolType
  const educationLevel = searchParams.get('education_level')
  const includeCrossover = searchParams.get('include_crossover') === 'true'
  const targetYearParam = searchParams.get('target_year')
  const selectedSchoolIdParam = searchParams.get('selected_school_id')
  const promptLocation = searchParams.get('prompt_location') === '1'
  const getParamList = (key) => {
    const values = searchParams.getAll(key)
    if (values.length > 0) return values
    const csv = searchParams.get(key)
    return csv ? csv.split(',').map(value => value.trim()).filter(Boolean) : []
  }

  const [filters, setFilters] = useState({
    ageGroup: ageGroup,
    targetYear: targetYearParam ? parseInt(targetYearParam) : new Date().getFullYear() + 1,
    birthYear: null,
    schoolType: schoolType,
    educationLevel: educationLevel,
    includeCrossover: includeCrossover,
    languageFocus: getParamList('language_focus'),
    specialPrograms: getParamList('special_programs'),
    facilities: getParamList('facilities'),
    teachingApproach: getParamList('teaching_approach'),
  })

  const clearSelectedSchoolParam = () => {
    if (!searchParams.has('selected_school_id')) return
    const params = new URLSearchParams(searchParams)
    params.delete('selected_school_id')
    navigate(`/search?${params.toString()}`, { replace: true })
  }


  const [availableFilters, setAvailableFilters] = useState({
    language_focus_pairs: [],
    language_focus_languages: [],
    language_focus_levels: [],
    special_programs: [],
    facilities: [],
    teaching_approach: [],
  })
  const [examAverages, setExamAverages] = useState(null)

  const effectiveEducationLevel =
    filters.ageGroup === 'preschool' && !filters.includeCrossover
      ? filters.educationLevel
      : null

  const { schools, loading, error } = useSchools(
    filters.ageGroup,
    filters.schoolType,
    effectiveEducationLevel,
    filters.includeCrossover,
    filters.languageFocus,
    filters.specialPrograms,
    filters.facilities,
    filters.teachingApproach,
    countryCode
  )

  const { schools: baseSchools } = useSchools(
    filters.ageGroup,
    filters.schoolType,
    effectiveEducationLevel,
    filters.includeCrossover,
    EMPTY_LIST,
    EMPTY_LIST,
    EMPTY_LIST,
    EMPTY_LIST,
    countryCode
  )

  useEffect(() => {
    let isMounted = true
    fetchAvailableFilters({ countryCode })
      .then((data) => {
        if (!isMounted) return
        setAvailableFilters({
          language_focus_pairs: data.language_focus_pairs || [],
          language_focus_languages: data.language_focus_languages || [],
          language_focus_levels: data.language_focus_levels || [],
          special_programs: data.special_programs || [],
          facilities: data.facilities || [],
          teaching_approach: data.teaching_approach || [],
        })
      })
      .catch(() => {
        if (!isMounted) return
        setAvailableFilters({
          language_focus_pairs: [],
          language_focus_languages: [],
          language_focus_levels: [],
          special_programs: [],
          facilities: [],
          teaching_approach: [],
        })
      })
    return () => {
      isMounted = false
    }
  }, [countryCode])

  useEffect(() => {
    let isMounted = true

    fetchExamAverages({ countryCode })
      .then((data) => {
        if (!isMounted) return
        setExamAverages(data)
      })
      .catch(() => {
        if (!isMounted) return
        setExamAverages(null)
      })

    return () => {
      isMounted = false
    }
  }, [countryCode])

  useEffect(() => {
    const storedLocation = localStorage.getItem('userLocation')
    if (storedLocation) {
      try {
        const parsed = JSON.parse(storedLocation)
        if (typeof parsed?.lat === 'number' && typeof parsed?.lng === 'number') {
          setUserLocation({
            lat: parsed.lat,
            lng: parsed.lng,
            address: parsed.address || t('location.currentLocation'),
          })
        }
      } catch {
        localStorage.removeItem('userLocation')
      }
    }
  }, [t])

  useEffect(() => {
    const storedPreference = localStorage.getItem('searchInMapBounds')
    if (storedPreference === 'true') {
      setSearchInBounds(true)
    } else if (storedPreference === 'false') {
      setSearchInBounds(false)
    }
  }, [])

  useEffect(() => {
    localStorage.setItem('searchInMapBounds', searchInBounds ? 'true' : 'false')
  }, [searchInBounds])

  useEffect(() => {
    if (!promptLocation) return
    if (userLocation) return
    if (window.innerWidth >= 768) return

    setIsFiltersOpen(true)
    setHighlightLocation(true)

    const timer = setTimeout(() => {
      locationSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }, 200)

    const highlightTimer = setTimeout(() => {
      setHighlightLocation(false)
    }, 2500)

    return () => {
      clearTimeout(timer)
      clearTimeout(highlightTimer)
    }
  }, [promptLocation, userLocation])

  const ageGroupOrder = useMemo(() => {
    const keys = getAgeGroupKeys(config)
    return keys.length > 0 ? keys : AGE_GROUP_KEYS
  }, [config])

  const setOverlayForSchool = (schoolId, { focusLocationId } = {}) => {
    setLocationOverlay(prev => {
      const sameSchool = prev.schoolId === schoolId
      return {
        schoolId,
        focusLocationId: focusLocationId !== undefined
          ? focusLocationId
          : (sameSchool ? prev.focusLocationId : null),
        hideOthers: sameSchool ? prev.hideOthers : false,
      }
    })
  }

  const clearLocationOverlay = () => {
    setLocationOverlay({ schoolId: null, focusLocationId: null, hideOthers: false })
    setOpenLocationsId(null)
  }

  useEffect(() => {
    if (userLocation && !previousLocationRef.current) {
      setSortBy('distance')
    }
    if (!userLocation && sortBy === 'distance') {
      setSortBy('name')
    }
    previousLocationRef.current = userLocation
  }, [userLocation, sortBy])

  useEffect(() => {
    if (userLocation || !isManualInput) {
      cancelGeocode()
      return
    }

    const trimmedAddress = addressInput.trim()
    if (!trimmedAddress) {
      cancelGeocode()
      setIsGeocoding(false)
      return
    }

    const requestId = Date.now()
    latestGeocodeRef.current = requestId
    setIsGeocoding(true)

    geocodeAddress(trimmedAddress, geocodingConfig)
      .then((result) => {
        if (latestGeocodeRef.current !== requestId) return
        const newLocation = {
          lat: result.lat,
          lng: result.lng,
          address: result.address,
        }
        setUserLocation(newLocation)
        setIsManualInput(false)
        persistLocation(newLocation)
      })
      .catch((err) => {
        if (latestGeocodeRef.current !== requestId) return
        if (err?.code === 'CANCELED') return
        if (err?.code === 'NO_RESULTS') {
          showLocationError(t('location.errorNotFound'))
        } else if (err?.code === 'RATE_LIMIT') {
          showLocationError(t('location.errorRateLimited'))
        } else if (err?.code === 'TOO_FAST') {
          showLocationError(t('location.errorPleaseWait'), { temporary: true })
        } else if (err?.code !== 'EMPTY') {
          showLocationError(t('location.errorUnableToGeocode'))
        }
      })
      .finally(() => {
        if (latestGeocodeRef.current !== requestId) return
        setIsGeocoding(false)
      })
  }, [addressInput, userLocation, isManualInput, t, geocodingConfig])

  useEffect(() => {
    return () => {
      if (errorTimeoutRef.current) {
        clearTimeout(errorTimeoutRef.current)
      }
      cancelGeocode()
    }
  }, [])

  const handleSchoolSelect = (school, { source = 'list' } = {}) => {
    if (!school) return
    clearSelectedSchoolParam()
    if (locationOverlay.schoolId && locationOverlay.schoolId !== school.id) {
      clearLocationOverlay()
    }
    setSelectedSchoolId((prev) => {
      const nextId = source === 'map'
        ? school.id
        : (prev === school.id ? null : school.id)
      scrollOnSelectRef.current = source === 'map' && nextId !== null
      return nextId
    })
  }

  const handleSchoolHover = (school) => {
    setHoveredSchoolId(school?.id ?? null)
  }

  const handleSchoolHoverEnd = () => {
    setHoveredSchoolId(null)
  }

  const handleClearSelection = () => {
    clearSelectedSchoolParam()
    setSelectedSchoolId(null)
  }

  const handleToggleLocationsPanel = (school) => {
    if (!school) return
    const hasMultipleLocations = (school.locations?.length || 0) > 1
    setOpenLocationsId(prev => {
      const nextId = prev === school.id ? null : school.id
      if (nextId) {
        if (hasMultipleLocations) {
          setOverlayForSchool(school.id)
        } else if (locationOverlay.schoolId === school.id) {
          clearLocationOverlay()
        }
      } else if (locationOverlay.schoolId === school.id) {
        clearLocationOverlay()
      }
      return nextId
    })
    setSelectedSchoolId(school.id)
  }

  const handleShowLocationsForSchool = (school) => {
    if (!school) return
    setOpenLocationsId(school.id)
    setOverlayForSchool(school.id)
    setSelectedSchoolId(school.id)
  }

  const handleShowAllLocations = (schoolId) => {
    if (!schoolId) return
    setOpenLocationsId(schoolId)
    setOverlayForSchool(schoolId)
    setSelectedSchoolId(schoolId)
  }

  const handleFocusLocation = (schoolId, locationId) => {
    if (!schoolId || !locationId) return
    setOverlayForSchool(schoolId, { focusLocationId: locationId })
    setSelectedSchoolId(schoolId)
  }

  const handleToggleHideOthers = (schoolId) => {
    if (!schoolId) return
    setLocationOverlay(prev => {
      const sameSchool = prev.schoolId === schoolId
      return {
        schoolId,
        focusLocationId: sameSchool ? prev.focusLocationId : null,
        hideOthers: sameSchool ? !prev.hideOthers : true,
      }
    })
    setSelectedSchoolId(schoolId)
  }

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

  const showLocationToast = (message) => {
    if (toastTimeoutRef.current) {
      clearTimeout(toastTimeoutRef.current)
    }
    setLocationToast(message)
    toastTimeoutRef.current = setTimeout(() => {
      setLocationToast(null)
    }, 1800)
  }

  const persistLocation = (location) => {
    localStorage.setItem('userLocation', JSON.stringify(location))
  }

  const handleUseMyLocation = () => {
    setLocationError(null)
    setIsPickingLocation(false)

    if (!navigator.geolocation) {
      showLocationError(t('location.errorUnavailable'))
      return
    }

    setIsLocating(true)
    navigator.geolocation.getCurrentPosition(
      (position) => {
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

  const handleAddressSearch = async () => {
    const trimmedAddress = addressInput.trim()
    if (!trimmedAddress) return

    setIsGeocoding(true)
    setLocationError(null)
    setIsPickingLocation(false)

    try {
      const result = await geocodeAddress(trimmedAddress, geocodingConfig)
      const newLocation = {
        lat: result.lat,
        lng: result.lng,
        address: result.address,
      }
      setUserLocation(newLocation)
      setIsManualInput(false)
      persistLocation(newLocation)
    } catch (err) {
      if (err?.code === 'CANCELED') return
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
      setIsGeocoding(false)
    }
  }

  const handleChangeLocation = () => {
    if (userLocation?.address) {
      setAddressInput(userLocation.address)
    }
    setIsPickingLocation(false)
    setUserLocation(null)
    setLocationError(null)
    setIsManualInput(false)
    localStorage.removeItem('userLocation')
  }

  const handleClearLocation = () => {
    setIsPickingLocation(false)
    setUserLocation(null)
    setAddressInput('')
    setLocationError(null)
    setDistanceFilter('any')
    setIsManualInput(false)
    localStorage.removeItem('userLocation')
  }

  const handleStartMapPick = () => {
    setLocationError(null)
    setIsManualInput(false)
    setIsPickingLocation(prev => {
      const next = !prev
      if (next) {
        setIsFiltersOpen(false)
        previousViewModeRef.current = viewMode
        previousMobileTabRef.current = mobileTab
        if (window.innerWidth < 1024) {
          setViewMode('map-only')
        }
        if (window.innerWidth < 768) {
          setMobileTab('map')
        }
      } else {
        if (previousViewModeRef.current) {
          setViewMode(previousViewModeRef.current)
        }
        if (previousMobileTabRef.current) {
          setMobileTab(previousMobileTabRef.current)
        }
      }
      return next
    })
  }

  const handleMapPickLocation = ({ lat, lng }) => {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return
    const newLocation = {
      lat,
      lng,
      address: t('location.selectedOnMap'),
    }
    setUserLocation(newLocation)
    setAddressInput('')
    setIsManualInput(false)
    setLocationError(null)
    persistLocation(newLocation)
    setIsPickingLocation(false)
    if (window.innerWidth < 1024) {
      setViewMode('list-map')
    } else if (previousViewModeRef.current) {
      setViewMode(previousViewModeRef.current)
    }
    if (window.innerWidth < 768) {
      setMobileTab('list')
      showLocationToast(t('location.setOnMapToast'))
    } else if (previousMobileTabRef.current) {
      setMobileTab(previousMobileTabRef.current)
    }
    previousViewModeRef.current = null
    previousMobileTabRef.current = null

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

  const handleFilterChange = (newFilters) => {
    const nextFilters = { ...filters, ...newFilters }
    if (newFilters.ageGroup !== undefined) {
      if (nextFilters.ageGroup !== 'preschool') {
        nextFilters.educationLevel = null
        nextFilters.includeCrossover = false
      } else if (nextFilters.includeCrossover) {
        nextFilters.educationLevel = null
      }
    }
    if (newFilters.includeCrossover !== undefined && nextFilters.includeCrossover) {
      nextFilters.educationLevel = null
    }

    setFilters(nextFilters)

    // Update URL when filters change
    const params = new URLSearchParams(searchParams)
    if (newFilters.ageGroup !== undefined) {
      if (newFilters.ageGroup) {
        params.set('age_group', newFilters.ageGroup)
      } else {
        params.delete('age_group')
      }
    }
    if (newFilters.schoolType !== undefined) {
      if (newFilters.schoolType) {
        params.set('school_type', newFilters.schoolType)
      } else {
        params.delete('school_type')
      }
    }
    if (newFilters.targetYear !== undefined) {
      if (newFilters.targetYear) {
        params.set('target_year', String(newFilters.targetYear))
      } else {
        params.delete('target_year')
      }
    }
    if (newFilters.includeCrossover !== undefined) {
      if (newFilters.includeCrossover) {
        params.set('include_crossover', 'true')
      } else {
        params.delete('include_crossover')
      }
    }
    if (nextFilters.ageGroup === 'preschool' && !nextFilters.includeCrossover && nextFilters.educationLevel) {
      params.set('education_level', nextFilters.educationLevel)
    } else {
      params.delete('education_level')
    }
    if (newFilters.languageFocus !== undefined) {
      params.delete('language_focus')
      newFilters.languageFocus.forEach(value => params.append('language_focus', value))
    }
    if (newFilters.specialPrograms !== undefined) {
      params.delete('special_programs')
      newFilters.specialPrograms.forEach(value => params.append('special_programs', value))
    }
    if (newFilters.facilities !== undefined) {
      params.delete('facilities')
      newFilters.facilities.forEach(value => params.append('facilities', value))
    }
    if (newFilters.teachingApproach !== undefined) {
      params.delete('teaching_approach')
      newFilters.teachingApproach.forEach(value => params.append('teaching_approach', value))
    }
    navigate(`/search?${params.toString()}`, { replace: true })
  }

  useEffect(() => {
    if (rawSchoolType !== 'international') return
    handleFilterChange({ schoolType: 'private' })
  }, [rawSchoolType])

  const getPrimaryLocation = (school) => {
    return school.locations?.find(location => location.is_primary) || school.locations?.[0]
  }

  const getLocationForAgeGroup = (school, ageGroupValue) => {
    if (!ageGroupValue) return getPrimaryLocation(school)
    const matching = school.locations?.filter(location => {
      if (Array.isArray(location.age_groups)) return location.age_groups.includes(ageGroupValue)
      if (location.age_group) return location.age_group === ageGroupValue
      return false
    }) || []
    return matching.find(location => location.is_primary) || matching[0] || getPrimaryLocation(school)
  }

  const getStartingPrice = (school) => {
    if (school.school_type !== 'private' || !school.pricing || school.pricing.length === 0) {
      return null
    }

    const tuitionPrices = school.pricing
      .filter(price => price.category === 'tuition')
      .map(monthlyEquivalent)
      .filter(value => value != null)

    if (tuitionPrices.length === 0) return null

    return Math.min(...tuitionPrices)
  }

  const schoolsWithDistance = useMemo(() => {
    if (!userLocation) return schools

    return schools.map(school => {
      const distanceLocation = getLocationForAgeGroup(school, filters.ageGroup)
      const distance = distanceLocation?.lat && distanceLocation?.lng
        ? calculateDistance(userLocation.lat, userLocation.lng, distanceLocation.lat, distanceLocation.lng)
        : null
      return { ...school, distance }
    })
  }, [schools, userLocation, filters.ageGroup])

  const distanceLimit = userLocation && distanceFilter !== 'any' ? parseFloat(distanceFilter) : null

  const filteredSchools = useMemo(() => {
    if (!distanceLimit) return schoolsWithDistance
    return schoolsWithDistance.filter(school => typeof school.distance === 'number' && school.distance <= distanceLimit)
  }, [schoolsWithDistance, distanceLimit])

  const isSchoolInBounds = (school, bounds) => {
    if (!bounds?.southWest || !bounds?.northEast) return true
    return school.locations?.some(location => {
      if (!location?.lat || !location?.lng) return false
      return (
        location.lat >= bounds.southWest.lat &&
        location.lat <= bounds.northEast.lat &&
        location.lng >= bounds.southWest.lng &&
        location.lng <= bounds.northEast.lng
      )
    })
  }

  const boundedSchools = useMemo(() => {
    if (!searchInBounds || !mapBounds) return filteredSchools
    return filteredSchools.filter(school => isSchoolInBounds(school, mapBounds))
  }, [filteredSchools, searchInBounds, mapBounds])

  useEffect(() => {
    if (!selectedSchoolIdParam) return
    const schoolId = Number.parseInt(selectedSchoolIdParam, 10)
    if (!Number.isFinite(schoolId)) return

    const school = schools.find(item => item.id === schoolId)
    if (!school) return

    if (searchInBounds) {
      setSearchInBounds(false)
    }
    setSelectedSchoolId(schoolId)
    scrollOnSelectRef.current = true

    if ((school.locations?.length || 0) > 1) {
      setOpenLocationsId(schoolId)
      setLocationOverlay({ schoolId, focusLocationId: null, hideOthers: true })
    }

    if (window.innerWidth < 768) {
      setMobileTab('map')
    }
  }, [schools, searchInBounds, selectedSchoolIdParam])

  useEffect(() => {
    if (!selectedSchoolId) return
    const visibleIds = new Set(filteredSchools.map(school => school.id))
    if (!visibleIds.has(selectedSchoolId)) {
      setSelectedSchoolId(null)
    }
  }, [filteredSchools, selectedSchoolId])

  useEffect(() => {
    if (!locationOverlay.schoolId) return
    const visibleIds = new Set(filteredSchools.map(school => school.id))
    if (!visibleIds.has(locationOverlay.schoolId)) {
      clearLocationOverlay()
    }
  }, [filteredSchools, locationOverlay.schoolId])

  const sortedSchools = useMemo(() => {
    const list = [...boundedSchools]
    if (list.length === 0) return list

    switch (sortBy) {
      case 'distance':
        list.sort((a, b) => (a.distance ?? Number.POSITIVE_INFINITY) - (b.distance ?? Number.POSITIVE_INFINITY))
        break
      case 'type':
        list.sort((a, b) => (TYPE_SORT_ORDER[a.school_type] ?? 99) - (TYPE_SORT_ORDER[b.school_type] ?? 99))
        break
      case 'price':
        list.sort((a, b) => {
          const priceA = getStartingPrice(a)
          const priceB = getStartingPrice(b)
          const valueA = a.school_type === 'state' ? 0 : (priceA ?? Number.POSITIVE_INFINITY)
          const valueB = b.school_type === 'state' ? 0 : (priceB ?? Number.POSITIVE_INFINITY)
          return valueA - valueB
        })
        break
      case 'name':
      default:
        list.sort((a, b) => {
          const nameA = getSchoolName(a, i18n.language)
          const nameB = getSchoolName(b, i18n.language)
          return nameA.localeCompare(nameB, i18n.language, { sensitivity: 'base' })
        })
        break
    }

    return list
  }, [boundedSchools, sortBy, i18n.language])

  const totalFilteredCount = filteredSchools.length
  const visibleFilteredCount = boundedSchools.length

  const debouncedBoundsHandler = useMemo(
    () => debounce((bounds) => setMapBounds(bounds), 500),
    []
  )

  useEffect(() => {
    return () => {
      debouncedBoundsHandler.cancel()
    }
  }, [debouncedBoundsHandler])

  const handleBoundsChange = (bounds) => {
    debouncedBoundsHandler(bounds)
  }

  useEffect(() => {
    if (!selectedSchoolId || !scrollOnSelectRef.current) return
    const node = cardRefs.current.get(selectedSchoolId)
    if (node) {
      node.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
    scrollOnSelectRef.current = false
  }, [selectedSchoolId])

  const handleBackToLanding = () => {
    // Preserve current filters when going back to landing page
    const params = new URLSearchParams(searchParams)
    navigate(`/?${params.toString()}`)
  }

  // Close filters drawer on desktop
  useEffect(() => {
    const handleResize = () => {
      if (window.innerWidth >= 1024) {
        setIsFiltersOpen(false)
      }
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  const showList = viewMode === 'list-map' || viewMode === 'list-only'
  const showMap = viewMode === 'list-map' || viewMode === 'map-only'
  const hasCompare = compareList.length > 0

  const sortOptions = userLocation
    ? [
        { value: 'distance', label: t('sorting.distance') },
        { value: 'name', label: t('sorting.name') },
        { value: 'type', label: t('sorting.type') },
        { value: 'price', label: t('sorting.price') },
      ]
    : [
        { value: 'name', label: t('sorting.name') },
        { value: 'type', label: t('sorting.type') },
        { value: 'price', label: t('sorting.pricePrivate') },
      ]

  const renderLocationSection = () => {
    const addressParts = userLocation?.address ? userLocation.address.split(',') : []
    const addressLine1 = addressParts[0]?.trim()
    const addressLine2 = addressParts.slice(1, 3).join(', ').trim()

    return (
      <div
        ref={locationSectionRef}
        className={`rounded-xl border border-neutral-200 bg-neutral-50 p-4 space-y-4 ${
          highlightLocation ? 'location-highlight' : ''
        }`}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-lg">📍</span>
            <h3 className="font-semibold text-neutral-900">{t('location.title')}</h3>
          </div>
          {!userLocation && (
            <span className="text-xs text-neutral-500 font-medium">{t('location.optional')}</span>
          )}
        </div>

        {!userLocation ? (
          <div className="space-y-4">
            <div className="space-y-2">
              <p className="text-sm text-neutral-600">{t('location.setYourLocation')}</p>
              <ul className="text-sm text-neutral-600 list-disc ml-5 space-y-1">
                <li>{t('location.benefitDistances')}</li>
                <li>{t('location.benefitSort')}</li>
                <li>{t('location.benefitFilter')}</li>
              </ul>
            </div>

            <button
              onClick={handleUseMyLocation}
              disabled={isLocating}
              className="w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-primary-600 text-white text-sm font-medium hover:bg-primary-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
            >
              {isLocating && (
                <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
              )}
              {t('location.useMyLocation')}
            </button>

            <button
              onClick={handleStartMapPick}
              className={`w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium border transition-colors ${
                isPickingLocation
                  ? 'bg-primary-50 border-primary-300 text-primary-800'
                  : 'bg-white border-neutral-300 text-neutral-700 hover:bg-neutral-50'
              }`}
            >
              {isPickingLocation ? t('location.cancelMapPick') : t('location.selectOnMap')}
            </button>

            {isPickingLocation && (
              <p className="text-xs text-primary-700 bg-primary-50 border border-primary-200 rounded-lg px-3 py-2">
                {t('location.mapPickHint')}
              </p>
            )}

            <div className="space-y-2">
              <label className="block text-sm font-medium text-neutral-700">
                {t('location.enterAddress')}
              </label>
              <div className="flex gap-2">
                <input
                  value={addressInput}
                  onChange={(e) => {
                    setAddressInput(e.target.value)
                    setIsManualInput(true)
                    setLocationError(null)
                  }}
                  placeholder={t('location.addressPlaceholder')}
                  className="flex-1 border border-neutral-300 rounded-lg px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-shadow"
                />
                <button
                  onClick={handleAddressSearch}
                  disabled={isGeocoding || !addressInput.trim()}
                  className="inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-neutral-900 text-white text-sm font-medium hover:bg-neutral-800 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
                >
                  {isGeocoding && (
                    <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                    </svg>
                  )}
                  {t('common.search')}
                </button>
              </div>
              <p className="text-xs text-neutral-500">
                {t('location.attributionPrefix')}{' '}
                <a
                  href="https://www.openstreetmap.org/copyright"
                  target="_blank"
                  rel="noreferrer"
                  className="underline hover:text-neutral-700"
                >
                  {t('location.attributionLink')}
                </a>
              </p>
            </div>

            {locationError && (
              <p className="text-sm text-red-600">{locationError}</p>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <div>
              <p className="text-sm font-medium text-neutral-700">{t('location.near')}</p>
              <p className="text-sm text-neutral-900">{addressLine1}</p>
              {addressLine2 && (
                <p className="text-sm text-neutral-500">{addressLine2}</p>
              )}
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleChangeLocation}
                className="flex-1 px-3 py-2 text-sm font-medium rounded-lg bg-white border border-neutral-300 text-neutral-700 hover:bg-neutral-50 transition-colors"
              >
                {t('location.change')}
              </button>
              <button
                onClick={handleClearLocation}
                className="flex-1 px-3 py-2 text-sm font-medium rounded-lg bg-neutral-100 text-neutral-700 hover:bg-neutral-200 transition-colors"
              >
                {t('location.clear')}
              </button>
            </div>
            <button
              onClick={handleStartMapPick}
              className={`w-full inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg text-sm font-medium border transition-colors ${
                isPickingLocation
                  ? 'bg-primary-50 border-primary-300 text-primary-800'
                  : 'bg-white border-neutral-300 text-neutral-700 hover:bg-neutral-50'
              }`}
            >
              {isPickingLocation ? t('location.cancelMapPick') : t('location.selectOnMap')}
            </button>
            {isPickingLocation && (
              <p className="text-xs text-primary-700 bg-primary-50 border border-primary-200 rounded-lg px-3 py-2">
                {t('location.mapPickHint')}
              </p>
            )}
          </div>
        )}

        {userLocation && (
          <div className="pt-3 border-t border-neutral-200">
            {/* TODO: State school catchment areas
            // State schools have district restrictions. When location is set:
            // - Add badge "⚠️ Outside your district" for state schools >5km away
            // - Add filter: "☐ Show only schools in my area" (hides distant state schools)
            // - Research exact district boundaries (район) for Sofia
            // See: https://kg.sofia.bg for official district rules
            */}
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium text-neutral-700">
                {t('location.distanceFilterTitle')}
              </legend>
              <label className="flex items-center gap-2 text-sm text-neutral-600">
                <input
                  type="radio"
                  name="distance-filter"
                  value="any"
                  checked={distanceFilter === 'any'}
                  onChange={() => setDistanceFilter('any')}
                  className="text-primary-600 focus:ring-primary-500"
                />
                {t('location.distanceAny')}
              </label>
              <label className="flex items-center gap-2 text-sm text-neutral-600">
                <input
                  type="radio"
                  name="distance-filter"
                  value="2"
                  checked={distanceFilter === '2'}
                  onChange={() => setDistanceFilter('2')}
                  className="text-primary-600 focus:ring-primary-500"
                />
                {t('location.distance2km')}
              </label>
              <label className="flex items-center gap-2 text-sm text-neutral-600">
                <input
                  type="radio"
                  name="distance-filter"
                  value="5"
                  checked={distanceFilter === '5'}
                  onChange={() => setDistanceFilter('5')}
                  className="text-primary-600 focus:ring-primary-500"
                />
                {t('location.distance5km')}
              </label>
            </fieldset>
          </div>
        )}
      </div>
    )
  }

  const renderMapBoundsFilter = () => (
    <div className="rounded-xl border border-neutral-200 bg-white p-4 space-y-2">
      <label className="flex items-center justify-between gap-3 text-sm font-medium text-neutral-700">
        <span className="flex items-center gap-2">
          <input
            type="checkbox"
            checked={searchInBounds}
            onChange={(e) => setSearchInBounds(e.target.checked)}
            className="text-primary-600 focus:ring-primary-500"
          />
          {t('map.searchInBounds')}
        </span>
        {searchInBounds && (
          <span className="text-[11px] font-semibold uppercase tracking-wide text-primary-700 bg-primary-50 px-2 py-0.5 rounded-full">
            {t('common.active')}
          </span>
        )}
      </label>
      <p className="text-xs text-neutral-500">
        {t('map.searchInBoundsHint')}
      </p>
    </div>
  )

  const renderSchoolTypeToggle = () => (
    <div className="rounded-xl border border-neutral-200 bg-white p-4 space-y-3">
      <p className="text-sm font-semibold text-neutral-800">{t('filters.schoolType')}</p>
      <div className="flex items-center gap-2">
        {['state', 'private'].map(type => {
          const isActive = filters.schoolType === type
          return (
            <button
              key={type}
              type="button"
              onClick={() => handleFilterChange({ schoolType: isActive ? null : type })}
              className={`
                flex-1 px-3 py-2 text-sm font-medium rounded-lg border transition-colors
                ${isActive
                  ? 'bg-primary-600 text-white border-primary-600'
                  : 'bg-white text-neutral-700 border-neutral-200 hover:border-neutral-300'}
              `}
            >
              {t(`schoolTypes.${type}`)}
            </button>
          )
        })}
      </div>
      <p className="text-xs text-neutral-500">{t('filters.schoolTypeOptional')}</p>
    </div>
  )

  const formatAdvancedLabel = (key) => t(`advancedFilters.options.${key}`, { defaultValue: key.replace(/_/g, ' ') })
  const formatLanguageLabel = (key) => t(`advancedFilters.languages.${key}`, { defaultValue: key })
  const formatLanguageLevelLabel = (key) => t(`advancedFilters.levels.${key}`, { defaultValue: key.replace(/_/g, ' ') })

  const advancedCounts = useMemo(() => {
    const groups = {
      special_programs: filters.specialPrograms,
      facilities: filters.facilities,
      teaching_approach: filters.teachingApproach,
    }

    const matchesSelected = (school, groupToIgnore) => {
      const attributes = school.attributes || {}
      const focusPairs = getLanguageFocusPairs(school)

      if (groupToIgnore !== 'language_focus' && filters.languageFocus.length > 0) {
        if (!filters.languageFocus.some(value => focusPairs.has(value))) {
          return false
        }
      }

      return Object.entries(groups).every(([key, selected]) => {
        if (key === groupToIgnore) return true
        if (!selected || selected.length === 0) return true
        // Canonical advanced-filter tags (P1.9), not the free-text display lists.
        const values = attributes.filter_tags?.[key] || []
        return selected.some(value => values.includes(value))
      })
    }

    const counts = {
      language_focus_pairs: {},
      special_programs: {},
      facilities: {},
      teaching_approach: {},
    }

    const languageOptions = Array.from(new Set([
      ...(availableFilters.language_focus_pairs || []),
    ]))

    languageOptions.forEach(option => {
      counts.language_focus_pairs[option] = baseSchools.filter(school => {
        if (!matchesSelected(school, 'language_focus')) return false
        return getLanguageFocusPairs(school).has(option)
      }).length
    })

    Object.keys(counts)
      .filter(key => key !== 'language_focus_pairs')
      .forEach(groupKey => {
        const options = Array.from(new Set([
          ...(DEFAULT_ADVANCED_OPTIONS[groupKey] || []),
          ...(availableFilters[groupKey] || []),
        ]))

        options.forEach(option => {
          counts[groupKey][option] = baseSchools.filter(school => {
            if (!matchesSelected(school, groupKey)) return false
            const values = school.attributes?.filter_tags?.[groupKey] || []
            return values.includes(option)
          }).length
        })
      })

    return counts
  }, [baseSchools, availableFilters, filters.languageFocus, filters.specialPrograms, filters.facilities, filters.teachingApproach])

  const renderAdvancedFilters = () => {
    const buildOptions = (key) => {
      const serverOptions = availableFilters[key] || []
      const defaults = DEFAULT_ADVANCED_OPTIONS[key] || []
      return Array.from(new Set([...defaults, ...serverOptions]))
    }

    const buildLanguageOptions = () => {
      const derivedLanguages = new Set()
      const derivedLevels = new Set()
      const derivedPairs = new Set()

      baseSchools.forEach(school => {
        getLanguageFocusPairs(school).forEach(pair => {
          derivedPairs.add(pair)
          if (pair.includes(':')) {
            const [language, level] = pair.split(':')
            if (language) derivedLanguages.add(language)
            if (level) derivedLevels.add(level)
          }
        })
      })

      const languages = Array.from(new Set([
        ...(availableFilters.language_focus_languages || []),
        ...derivedLanguages,
      ]))

      const levels = Array.from(new Set([
        ...(DEFAULT_ADVANCED_OPTIONS.language_focus_levels || []),
        ...(availableFilters.language_focus_levels || []),
        ...derivedLevels,
      ]))

      const pairs = [
        ...(availableFilters.language_focus_pairs || []),
        ...derivedPairs,
      ]
      const byLanguage = new Map()

      pairs.forEach(pair => {
        const [language, level] = pair.split(':')
        if (!language || !level) return
        if (!byLanguage.has(language)) {
          byLanguage.set(language, new Set())
        }
        byLanguage.get(language).add(level)
      })

      languages.forEach(language => {
        const existingLevels = byLanguage.get(language) || new Set()
        const combined = new Set([...levels, ...existingLevels])
        byLanguage.set(language, combined)
      })

      return { languages, levels, byLanguage }
    }

    const renderLanguageFocusGroup = () => {
      const { languages, levels, byLanguage } = buildLanguageOptions()
      if (languages.length === 0) return null

      return (
        <details className="rounded-lg border border-neutral-200 bg-white px-3 py-2">
          <summary className="cursor-pointer text-sm font-medium text-neutral-700 flex items-center justify-between">
            <span className="flex items-center gap-2">
              <svg className="w-4 h-4 text-neutral-500" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 10.94l3.71-3.7a.75.75 0 111.06 1.06l-4.24 4.24a.75.75 0 01-1.06 0L5.21 8.29a.75.75 0 01.02-1.08z" clipRule="evenodd" />
              </svg>
              {t('advancedFilters.languageFocus')}
            </span>
            {filters.languageFocus.length > 0 && (
              <span className="text-xs text-primary-700 bg-primary-50 px-2 py-0.5 rounded-full">
                {filters.languageFocus.length}
              </span>
            )}
          </summary>
          <div className="space-y-3 mt-3">
            {languages.map(language => {
              const availableLevels = Array.from(byLanguage.get(language) || new Set(levels))
              const selectedForLanguage = filters.languageFocus.filter(value => value.startsWith(`${language}:`))
              const allLevelKeys = availableLevels.map(level => `${language}:${level}`)
              const isLanguageChecked = selectedForLanguage.length === allLevelKeys.length && allLevelKeys.length > 0
              const languageCount = allLevelKeys.reduce((sum, key) => {
                return sum + (advancedCounts.language_focus_pairs?.[key] ?? 0)
              }, 0)

              return (
                <details key={language} className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2">
                  <summary className="cursor-pointer text-sm font-medium text-neutral-700 flex items-center justify-between">
                    <span className="flex items-center gap-2">
                      <input
                        type="checkbox"
                        checked={isLanguageChecked}
                        onChange={(event) => {
                          const next = isLanguageChecked
                            ? filters.languageFocus.filter(value => !value.startsWith(`${language}:`))
                            : Array.from(new Set([...filters.languageFocus, ...allLevelKeys]))
                          handleFilterChange({ languageFocus: next })
                          const details = event.currentTarget.closest('details')
                          if (details) details.open = true
                        }}
                        className="text-primary-600 focus:ring-primary-500"
                      />
                      {formatLanguageLabel(language)}
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="text-xs text-neutral-400">({languageCount})</span>
                      {selectedForLanguage.length > 0 && (
                        <span className="text-xs text-primary-700 bg-white px-2 py-0.5 rounded-full border border-primary-200">
                          {selectedForLanguage.length}
                        </span>
                      )}
                    </span>
                  </summary>
                  <div className="space-y-2 mt-3">
                    {availableLevels.map(level => {
                      const option = `${language}:${level}`
                      const isChecked = filters.languageFocus.includes(option)
                      const count = advancedCounts.language_focus_pairs?.[option] ?? 0
                      const isDisabled = count === 0 && !isChecked
                      return (
                        <label
                          key={option}
                          className={`flex items-center gap-2 text-sm ${
                            isDisabled ? 'text-neutral-400' : 'text-neutral-600'
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={isChecked}
                            disabled={isDisabled}
                            onChange={() => {
                              if (isDisabled) return
                              const next = isChecked
                                ? filters.languageFocus.filter(value => value !== option)
                                : [...filters.languageFocus, option]
                              handleFilterChange({ languageFocus: next })
                            }}
                            className="text-primary-600 focus:ring-primary-500 disabled:cursor-not-allowed"
                          />
                          <span className="flex-1">{formatLanguageLevelLabel(level)}</span>
                          <span className="text-xs text-neutral-400">({count})</span>
                        </label>
                      )
                    })}
                  </div>
                </details>
              )
            })}
          </div>
        </details>
      )
    }

    const renderGroup = (groupKey, titleKey, selectedValues) => {
      const options = buildOptions(groupKey)
      if (options.length === 0) return null

      const selectedCount = selectedValues.length

      return (
        <details className="rounded-lg border border-neutral-200 bg-white px-3 py-2">
          <summary className="cursor-pointer text-sm font-medium text-neutral-700 flex items-center justify-between">
            <span className="flex items-center gap-2">
              <svg className="w-4 h-4 text-neutral-500" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 10.94l3.71-3.7a.75.75 0 111.06 1.06l-4.24 4.24a.75.75 0 01-1.06 0L5.21 8.29a.75.75 0 01.02-1.08z" clipRule="evenodd" />
              </svg>
              {t(titleKey)}
            </span>
            {selectedCount > 0 && (
              <span className="text-xs text-primary-700 bg-primary-50 px-2 py-0.5 rounded-full">
                {selectedCount}
              </span>
            )}
          </summary>
          <div className="space-y-2 mt-3">
            {options.map(option => {
              const isChecked = selectedValues.includes(option)
              const count = advancedCounts[groupKey]?.[option] ?? 0
              const isDisabled = count === 0 && !isChecked
              return (
                <label
                  key={option}
                  className={`flex items-center gap-2 text-sm ${
                    isDisabled ? 'text-neutral-400' : 'text-neutral-600'
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={isChecked}
                    disabled={isDisabled}
                    onChange={() => {
                      if (isDisabled) return
                      const next = isChecked
                        ? selectedValues.filter(value => value !== option)
                        : [...selectedValues, option]
                      const update = {
                        [groupKey === 'language_focus' ? 'languageFocus'
                          : groupKey === 'special_programs' ? 'specialPrograms'
                          : groupKey === 'facilities' ? 'facilities'
                          : 'teachingApproach']: next,
                      }
                      handleFilterChange(update)
                    }}
                    className="text-primary-600 focus:ring-primary-500 disabled:cursor-not-allowed"
                  />
                  <span className="flex-1">{formatAdvancedLabel(option)}</span>
                  <span className="text-xs text-neutral-400">({count})</span>
                </label>
              )
            })}
          </div>
        </details>
      )
    }

    return (
      <details className="rounded-xl border border-neutral-200 bg-white p-4">
        <summary className="cursor-pointer text-sm font-semibold text-neutral-800">
          {t('advancedFilters.title')}
        </summary>
        <div className="mt-4 space-y-4">
          {renderLanguageFocusGroup()}
          {renderGroup('special_programs', 'advancedFilters.specialPrograms', filters.specialPrograms)}
          {renderGroup('facilities', 'advancedFilters.facilities', filters.facilities)}
          {renderGroup('teaching_approach', 'advancedFilters.teachingApproach', filters.teachingApproach)}
          <div>
            <button
              type="button"
              onClick={() => handleFilterChange({
                languageFocus: [],
                specialPrograms: [],
                facilities: [],
                teachingApproach: [],
              })}
              className="text-sm text-neutral-600 hover:text-neutral-800 hover:underline"
            >
              {t('advancedFilters.clear')}
            </button>
          </div>
        </div>
      </details>
    )
  }

  const renderActiveFilters = ({ compact = false } = {}) => {
    const activeFilters = []
    const advancedFilters = []
    const advancedByLanguage = new Map()

    if (filters.ageGroup) {
      activeFilters.push({
        key: 'ageGroup',
        label: t(`ageGroups.${filters.ageGroup}`),
        locked: true,
      })
    }

    if (filters.schoolType) {
      activeFilters.push({
        key: 'schoolType',
        label: t(`schoolTypes.${filters.schoolType}`),
        onRemove: () => handleFilterChange({ schoolType: null }),
      })
    }

    const addAdvancedChips = (values, groupKey) => {
      values.forEach(value => {
        if (groupKey === 'languageFocus') {
          const [language, level] = value.split(':')
          if (language && level) {
            if (!advancedByLanguage.has(language)) {
              advancedByLanguage.set(language, [])
            }
            advancedByLanguage.get(language).push(level)
          } else if (language) {
            if (!advancedByLanguage.has(language)) {
              advancedByLanguage.set(language, [])
            }
          }
        }
        const label = groupKey === 'languageFocus'
          ? (() => {
              const [language, level] = value.split(':')
              if (language && level) {
                return `${formatLanguageLabel(language)} · ${formatLanguageLevelLabel(level)}`
              }
              return formatLanguageLabel(language || value)
            })()
          : formatAdvancedLabel(value)
        advancedFilters.push({
          key: `${groupKey}-${value}`,
          label,
          onRemove: () => {
            const next = values.filter(item => item !== value)
            if (groupKey === 'languageFocus') {
              handleFilterChange({ languageFocus: next })
            } else if (groupKey === 'specialPrograms') {
              handleFilterChange({ specialPrograms: next })
            } else if (groupKey === 'facilities') {
              handleFilterChange({ facilities: next })
            } else {
              handleFilterChange({ teachingApproach: next })
            }
          },
        })
      })
    }

    addAdvancedChips(filters.languageFocus, 'languageFocus')
    addAdvancedChips(filters.specialPrograms, 'specialPrograms')
    addAdvancedChips(filters.facilities, 'facilities')
    addAdvancedChips(filters.teachingApproach, 'teachingApproach')

    if (compact) {
      return (
        <div className="flex items-center gap-3">
          <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            {t('search.activeFilters')}
          </span>
          <div className="flex-1 overflow-x-auto">
            <div className="flex items-center gap-2 min-w-max py-1">
              {activeFilters.map(filter => (
                <span
                  key={filter.key}
                  className={`inline-flex items-center gap-2 text-xs px-2.5 py-1 rounded-full ${
                    filter.locked
                      ? 'bg-primary-50 text-primary-700 border border-primary-200'
                      : 'bg-primary-600 text-white'
                  }`}
                >
                  {filter.label}
                  {!filter.locked && (
                    <button
                      type="button"
                      onClick={filter.onRemove}
                      className="w-6 h-6 -my-1 -mr-1 rounded-full flex items-center justify-center text-white hover:bg-primary-700 transition-colors"
                      aria-label={t('common.close')}
                    >
                      ×
                    </button>
                  )}
                </span>
              ))}
              {[...advancedByLanguage.entries()].map(([language, levels]) => (
                <span
                  key={`lang-${language}`}
                  className="inline-flex items-center gap-2 bg-primary-600 text-white text-xs px-2.5 py-1 rounded-full"
                >
                  <span>{formatLanguageLabel(language)}:</span>
                  <span className="flex items-center gap-1">
                    {levels.map(level => (
                      <span key={`${language}-${level}`} className="inline-flex items-center gap-1 bg-white/15 px-2 py-0.5 rounded-full">
                        {formatLanguageLevelLabel(level)}
                        <button
                          type="button"
                          onClick={() => {
                            const option = `${language}:${level}`
                            handleFilterChange({
                              languageFocus: filters.languageFocus.filter(value => value !== option),
                            })
                          }}
                          className="w-4 h-4 rounded-full flex items-center justify-center text-white hover:bg-white/20"
                          aria-label={t('common.close')}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </span>
                </span>
              ))}
              {advancedFilters.filter(filter => !filter.key.startsWith('languageFocus-')).map(filter => (
                <span
                  key={filter.key}
                  className="inline-flex items-center gap-2 bg-primary-600 text-white text-xs px-2.5 py-1 rounded-full"
                >
                  {filter.label}
                  <button
                    type="button"
                    onClick={filter.onRemove}
                    className="w-6 h-6 -my-1 -mr-1 rounded-full flex items-center justify-center text-white hover:bg-primary-700 transition-colors"
                    aria-label={t('common.close')}
                  >
                    ×
                  </button>
                </span>
              ))}
              {advancedFilters.length > 0 && (
                <button
                  type="button"
                  onClick={() => handleFilterChange({
                    languageFocus: [],
                    specialPrograms: [],
                    facilities: [],
                    teachingApproach: [],
                  })}
                  className="text-xs text-neutral-500 hover:text-neutral-700 hover:underline whitespace-nowrap"
                >
                  {t('advancedFilters.clear')}
                </button>
              )}
            </div>
          </div>
        </div>
      )
    }

    return (
      <div className="rounded-xl border border-neutral-200 bg-white p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-neutral-800">{t('search.activeFilters')}</h3>
          {advancedFilters.length > 0 && (
            <button
              type="button"
              onClick={() => handleFilterChange({
                languageFocus: [],
                specialPrograms: [],
                facilities: [],
                teachingApproach: [],
              })}
              className="text-xs text-neutral-500 hover:text-neutral-700 hover:underline"
            >
              {t('advancedFilters.clear')}
            </button>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            {t('search.mainFilters')}
          </p>
          {activeFilters.length === 0 ? (
            <p className="text-xs text-neutral-500">{t('search.noActiveFilters')}</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {activeFilters.map(filter => (
                <span
                  key={filter.key}
                  className={`inline-flex items-center gap-2 text-sm px-3 py-1.5 rounded-full ${
                    filter.locked
                      ? 'bg-primary-50 text-primary-700 border border-primary-200'
                      : 'bg-primary-600 text-white'
                  }`}
                >
                  {filter.label}
                  {!filter.locked && (
                    <button
                      type="button"
                      onClick={filter.onRemove}
                      className="w-[44px] h-[44px] -my-2 -mr-1 rounded-full flex items-center justify-center text-white hover:bg-primary-700 transition-colors"
                      aria-label={t('common.close')}
                    >
                      ×
                    </button>
                  )}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            {t('search.advancedFilters')}
          </p>
          {advancedFilters.length === 0 ? (
            <p className="text-xs text-neutral-500">{t('search.noAdvancedFilters')}</p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {advancedFilters.map(filter => (
                <span
                  key={filter.key}
                  className="inline-flex items-center gap-2 bg-primary-600 text-white text-sm px-3 py-1.5 rounded-full"
                >
                  {filter.label}
                  <button
                    type="button"
                    onClick={filter.onRemove}
                    className="w-[44px] h-[44px] -my-2 -mr-1 rounded-full flex items-center justify-center text-white hover:bg-primary-700 transition-colors"
                    aria-label={t('common.close')}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        
      </div>
    )
  }

  return (
    <Layout hideNavOnMobile>
      <div className="h-screen md:h-[calc(100vh-64px)] flex flex-col">
        {/* Mobile/Tablet Header with Filters Button and Tabs */}
        <div className="lg:hidden border-b border-neutral-200 bg-white">
          <div className="flex items-center gap-2 px-3 py-2">
            <button
              onClick={handleBackToLanding}
              className="p-2 -ml-2 text-neutral-600 hover:text-neutral-900"
              aria-label={t('search.backToFilters')}
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
              </svg>
            </button>

            <div className="flex-1 flex items-center justify-center">
              <div className="inline-flex items-center bg-neutral-100 rounded-lg p-0.5">
                <button
                  onClick={() => setMobileTab('list')}
                  className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                    mobileTab === 'list'
                      ? 'bg-white text-neutral-900 shadow-sm'
                      : 'text-neutral-600 hover:text-neutral-800'
                  }`}
                >
                  {t('search.listView')}
                </button>
                <button
                  onClick={() => setMobileTab('map')}
                  className={`px-3 py-1 text-xs font-medium rounded-md transition-colors ${
                    mobileTab === 'map'
                      ? 'bg-white text-neutral-900 shadow-sm'
                      : 'text-neutral-600 hover:text-neutral-800'
                  }`}
                >
                  {t('search.mapView')}
                </button>
              </div>
            </div>

            <button
              onClick={() => setIsFiltersOpen(true)}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-primary-50 text-primary-700 text-xs font-medium"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
              </svg>
              {t('search.filters')}
            </button>
          </div>
        </div>

        {/* Desktop View Toggle */}
        <div className="hidden lg:flex items-center gap-6 px-6 py-3 border-b border-neutral-200 bg-white">
          <button
            onClick={handleBackToLanding}
            className="flex items-center gap-2 text-sm text-neutral-600 hover:text-neutral-900"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            {t('search.backToFilters')}
          </button>

          <div className="flex-1">
            {renderActiveFilters({ compact: true })}
          </div>

          <div className="flex items-center gap-2 bg-neutral-100 rounded-lg p-1">
            <button
              onClick={() => setViewMode('list-map')}
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                viewMode === 'list-map'
                  ? 'bg-white text-neutral-900 shadow-sm'
                  : 'text-neutral-600 hover:text-neutral-900'
              }`}
            >
              {t('search.listAndMap')}
            </button>
            <button
              onClick={() => setViewMode('map-only')}
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                viewMode === 'map-only'
                  ? 'bg-white text-neutral-900 shadow-sm'
                  : 'text-neutral-600 hover:text-neutral-900'
              }`}
            >
              {t('search.mapOnly')}
            </button>
            <button
              onClick={() => setViewMode('list-only')}
              className={`px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                viewMode === 'list-only'
                  ? 'bg-white text-neutral-900 shadow-sm'
                  : 'text-neutral-600 hover:text-neutral-900'
              }`}
            >
              {t('search.listOnly')}
            </button>
          </div>
        </div>

        {/* Main Content Area */}
        <div className="flex-1 flex overflow-hidden">
          {/* Desktop Filters Sidebar (20%) */}
          <aside className="hidden lg:block w-1/5 border-r border-neutral-200 bg-white overflow-y-auto">
            <div className="p-5 space-y-6">
              {renderLocationSection()}
              {renderSchoolTypeToggle()}
              {renderMapBoundsFilter()}
              {renderAdvancedFilters()}
            </div>
          </aside>

          {/* School List (Desktop: 50%, Tablet: 60%, Mobile: full on List tab) */}
          <div className={`
            flex flex-col bg-neutral-50
            ${mobileTab === 'list' ? '' : 'hidden md:flex'}
            ${viewMode === 'list-only' ? 'lg:w-4/5' : 'lg:w-1/2'}
            ${showList ? 'lg:flex' : 'lg:hidden'}
            md:w-3/5
            w-full
          `}>
              <div className="px-4 py-2 bg-white border-b border-neutral-200 space-y-2 lg:hidden">
                {renderActiveFilters({ compact: true })}
                <div className="flex items-center justify-between gap-3">
                  {loading ? (
                    <div className="h-4 w-32 bg-neutral-200 animate-pulse rounded" />
                  ) : (
                    <p className="text-sm text-neutral-600">
                      <span className="font-semibold text-neutral-900">{sortedSchools.length}</span>
                      {' '}{t('schools.results', { count: sortedSchools.length })}
                    </p>
                  )}
                  <div className="flex items-center gap-2">
                    <label className="text-sm font-medium text-neutral-700">
                      {t('sorting.sortBy')}
                    </label>
                    <select
                      value={sortBy}
                      onChange={(e) => setSortBy(e.target.value)}
                      className="border border-neutral-300 rounded-lg px-3 py-1.5 text-sm bg-white focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-shadow"
                    >
                      {sortOptions.map(option => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                {searchInBounds && !loading && (
                  <p className="text-xs text-neutral-500">
                    {t('map.showingInArea', {
                      shown: visibleFilteredCount,
                      total: totalFilteredCount,
                    })}
                  </p>
                )}
              </div>

              {/* School list */}
              <div className={`flex-1 overflow-y-auto ${hasCompare ? 'pb-24' : ''}`}>
                {loading && (
                  <div className="p-4 space-y-4">
                    {[...Array(5)].map((_, i) => (
                      <SchoolCardSkeleton key={i} />
                    ))}
                  </div>
                )}

                {error && (
                  <div className="p-8 text-center">
                    <div className="w-12 h-12 mx-auto mb-4 rounded-full bg-red-100 flex items-center justify-center">
                      <svg className="w-6 h-6 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                      </svg>
                    </div>
                    <p className="text-neutral-600">{t('common.error')}</p>
                  </div>
                )}

                {!loading && !error && sortedSchools.length === 0 && (
                  <div className="p-8 text-center">
                    <div className="w-12 h-12 mx-auto mb-4 rounded-full bg-neutral-100 flex items-center justify-center">
                      <svg className="w-6 h-6 text-neutral-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                      </svg>
                    </div>
                    <p className="text-neutral-600">{t('schools.noResults')}</p>
                    <p className="text-sm text-neutral-400 mt-1">{t('schools.tryDifferentFilters')}</p>
                  </div>
                )}

                {!loading && !error && sortedSchools.map(school => (
                  <SchoolCard
                    key={school.id}
                    ref={(node) => {
                      if (node) {
                        cardRefs.current.set(school.id, node)
                      } else {
                        cardRefs.current.delete(school.id)
                      }
                    }}
                    school={school}
                    location={getLocationForAgeGroup(school, filters.ageGroup)}
                    isSelected={selectedSchoolId === school.id}
                    onClick={() => handleSchoolSelect(school, { source: 'list' })}
                    onHover={() => handleSchoolHover(school)}
                    onHoverEnd={handleSchoolHoverEnd}
                    ageGroupOrder={ageGroupOrder}
                    activeAgeGroup={filters.ageGroup}
                    isLocationsOpen={openLocationsId === school.id}
                    locationOverlay={locationOverlay}
                    onToggleLocations={() => handleToggleLocationsPanel(school)}
                    onShowAllLocations={() => handleShowAllLocations(school.id)}
                    onFocusLocation={(locationId) => handleFocusLocation(school.id, locationId)}
                    onClearLocations={clearLocationOverlay}
                    examAverages={examAverages}
                  />
                ))}
              </div>
            </div>

          {/* Map (Desktop: 30%, Tablet: 40%, Mobile: full on Map tab) */}
          <div className={`
            flex-1 relative
            ${mobileTab === 'map' ? '' : 'hidden md:block'}
            ${showMap ? 'lg:block' : 'lg:hidden'}
            ${viewMode === 'map-only' ? 'lg:w-full' : 'lg:w-3/10'}
            md:w-2/5
          `}>
                <SchoolMap
                  schools={filteredSchools}
                  activeAgeGroup={filters.ageGroup}
                  selectedSchoolId={selectedSchoolId}
                  hoveredSchoolId={hoveredSchoolId}
                  onSchoolSelect={(school) => handleSchoolSelect(school, { source: 'map' })}
                  onClearSelection={handleClearSelection}
                  loading={loading}
                  userLocation={userLocation}
                  isPickingLocation={isPickingLocation}
                  onPickLocation={handleMapPickLocation}
                  onBoundsChange={handleBoundsChange}
                  autoFit={!searchInBounds && !selectedSchoolId && !locationOverlay.schoolId}
                  hasCompare={hasCompare}
                  resizeKey={`${viewMode}-${mobileTab}-${showMap}`}
                  locationOverlay={locationOverlay}
                  onShowLocations={handleShowLocationsForSchool}
                  onClearLocationOverlay={clearLocationOverlay}
                  onFocusLocation={handleFocusLocation}
                  onToggleHideOthers={handleToggleHideOthers}
                />
            </div>
        </div>

        {/* Filters Bottom Sheet / Drawer (Mobile/Tablet) */}
        {isFiltersOpen && (
          <>
            {/* Backdrop */}
            <div
              className="fixed inset-0 bg-black/50 z-[2000] lg:hidden"
              onClick={() => setIsFiltersOpen(false)}
            />

            {/* Bottom Sheet (Mobile) / Side Drawer (Tablet) */}
            <div className={`
              fixed z-[2100] bg-white lg:hidden
              md:top-0 md:right-0 md:bottom-0 md:w-96 md:shadow-2xl
              max-md:bottom-0 max-md:left-0 max-md:right-0 max-md:rounded-t-2xl max-md:shadow-up max-md:max-h-[85vh]
              overflow-y-auto
            `}>
              <div className="sticky top-0 bg-white border-b border-neutral-200 px-5 py-4 flex items-center justify-between">
                <h2 className="font-semibold text-neutral-900">{t('search.filters')}</h2>
                <button
                  onClick={() => setIsFiltersOpen(false)}
                  className="p-1 hover:bg-neutral-100 rounded-lg transition-colors"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <div className="p-5">
                <div className="space-y-6">
                  {renderLocationSection()}
                  {renderSchoolTypeToggle()}
                  {renderMapBoundsFilter()}
                  {renderAdvancedFilters()}
                </div>
              </div>
            </div>
          </>
        )}

        {locationToast && (
          <div className="fixed inset-x-0 bottom-6 z-[2200] flex justify-center px-4 md:hidden">
            <div className="bg-neutral-900 text-white text-sm font-medium px-4 py-2 rounded-full shadow-lg">
              {locationToast}
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}

export default SearchPage
