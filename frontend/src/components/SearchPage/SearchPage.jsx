import { useState, useEffect, useLayoutEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams, useNavigate, useLocation } from 'react-router-dom'
import debounce from 'lodash.debounce'
import { useStableCallback } from '../../hooks/useStableCallback'
import Layout from '../Layout/Layout'
import LanguageToggle from '../LanguageToggle/LanguageToggle'
import SchoolMap from '../Map/SchoolMap'
import SchoolCard from '../SchoolCard/SchoolCard'
import SchoolCardSkeleton from '../SchoolCard/SchoolCardSkeleton'
import SchoolDetailPanel from './SchoolDetailPanel'
import AgePicker from './AgePicker'
import SchoolNameSearch from './SchoolNameSearch'
import LocationControl from './LocationControl'
import { useSchools } from '../../hooks/useSchools'
import { calculateDistance } from '../../utils/distance'
import { geocodeAddress, reverseGeocode, cancelGeocode } from '../../utils/geocoding'
import { compareSchoolNames, getSchoolName, schoolMatchesQuery } from '../../utils/i18n'
import { useCompare } from '../../context/CompareContext'
import { useCountry } from '../../context/CountryContext'
import { fetchAvailableFilters, fetchExamAverages } from '../../api/schools'
import { getAgeGroupKeys, getExclusiveAgeGroups } from '../../utils/countryConfig'
import { AGE_GROUP_KEYS } from '../../utils/education'
import { getLanguageFocusPairs } from '../../utils/schoolAttributes'
import { matchesAdvancedFilters, matchesSchoolType } from '../../utils/advancedFilters'
import { getNvoDetail } from '../../utils/nvo'
import { LIST_PAGE_SIZE, windowForIndex } from '../../utils/listWindow'
import { canonicalLanguagePair, languageKey, languageLabel } from '../../utils/languages'

const uniqueCanonical = (values, canonical) => (
  [...new Set((values || []).map(canonical).filter(Boolean))]
)
import { monthlyEquivalent, toEur } from '../../utils/pricing'
import {
  readViewParams,
  writeViewParams,
  readSavedViewState,
  saveViewState,
  rememberLastSearchUrl,
  isDesktopViewport,
} from '../../utils/searchViewState'

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

const TYPE_SORT_ORDER = {
  state: 0,
  private: 1,
  international: 2,
}

const EMPTY_LIST = []
// Bulgaria, until the country config has loaded.
const FALLBACK_KINDERGARTEN_ONLY_GROUPS = ['nursery', 'first', 'second', 'third']

const DEFAULT_ADVANCED_OPTIONS = {
  language_focus_levels: ['immersion', 'bilingual', 'enrichment'],
  special_programs: ['music_program', 'sports_program', 'arts_program', 'extended_day', 'meals_provided'],
  facilities: ['sports_facilities', 'cafeteria', 'library', 'computer_lab', 'transportation'],
  teaching_approach: ['montessori', 'waldorf', 'ib_program', 'project_based'],
}

function SearchPage() {
  const { t, i18n } = useTranslation()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  // View state lives in the URL so it survives opening a school and coming back.
  const [initialView] = useState(() => readViewParams(searchParams))
  const [savedViewState] = useState(() => readSavedViewState(location.key, location.search))
  // The list renders the cards [visibleStart, visibleCount) of the sorted schools.
  const [visibleStart, setVisibleStart] = useState(() => savedViewState?.listStart || 0)
  // Enough cards to reach a restored scroll position (cards are at least ~100 px tall).
  const [visibleCount, setVisibleCount] = useState(() => (
    (savedViewState?.listStart || 0) + Math.ceil((savedViewState?.scrollTop || 0) / 100) + LIST_PAGE_SIZE
  ))
  const visibleStartRef = useRef(visibleStart)
  visibleStartRef.current = visibleStart
  const listSentinelRef = useRef(null)
  const listTopSentinelRef = useRef(null)
  // The school shown in the side panel (`?detail=<id>`); opening it pushes a history entry
  // so browser Back closes the panel instead of leaving the results.
  const detailSchoolId = (() => {
    const value = Number.parseInt(searchParams.get('detail') || '', 10)
    return Number.isFinite(value) && value > 0 ? value : null
  })()
  const { compareList } = useCompare()
  const { config, countryCode } = useCountry()

  const geocodingConfig = config?.map_config?.geocoding || {}

  const [selectedSchoolId, setSelectedSchoolId] = useState(initialView.school)
  const [hoveredSchoolId, setHoveredSchoolId] = useState(null)
  const [viewMode, setViewMode] = useState(initialView.view) // 'list-map', 'map-only', 'list-only'
  const [mobileTab, setMobileTab] = useState(initialView.tab) // 'list' or 'map'
  const [isFiltersOpen, setIsFiltersOpen] = useState(false) // For tablet/mobile drawer
  const filtersDrawerRef = useRef(null)
  const [userLocation, setUserLocation] = useState(() => readStoredUserLocation(t('location.currentLocation')))
  const [addressInput, setAddressInput] = useState('')
  const [locationError, setLocationError] = useState(null)
  const [isLocating, setIsLocating] = useState(false)
  const [isGeocoding, setIsGeocoding] = useState(false)
  const [isPickingLocation, setIsPickingLocation] = useState(false)
  const [distanceFilter, setDistanceFilter] = useState(initialView.within)
  // With a saved location, nearest-first is the default, as it was before sort lived in the URL.
  const [nameQuery, setNameQuery] = useState(initialView.q)
  // URLs this page has written but that have not landed yet, oldest first.
  const pendingWritesRef = useRef([])
  const adoptingUrlRef = useRef(false)
  const [agePickerOpen, setAgePickerOpen] = useState(false)
  const [welcomeDismissed, setWelcomeDismissed] = useState(() => {
    try {
      return localStorage.getItem('welcomeDismissed') === 'true'
    } catch {
      return false
    }
  })
  const [sortBy, setSortBy] = useState(() => (
    searchParams.has('sort') || !userLocation ? initialView.sort : 'distance'
  ))
  const [searchInBounds, setSearchInBounds] = useState(false)
  const [mapBounds, setMapBounds] = useState(null)
  const [openLocationsId, setOpenLocationsId] = useState(null)
  const [locationOverlay, setLocationOverlay] = useState({
    schoolId: null,
    focusLocationId: null,
    hideOthers: false,
  })
  const [locationToast, setLocationToast] = useState(null)
  const previousLocationRef = useRef(userLocation)
  const listScrollRef = useRef(null)
  // Tracked on scroll: the list node is already detached when the unmount cleanup runs.
  const listScrollTopRef = useRef(0)
  const mapViewRef = useRef(savedViewState?.map || null)
  const locationRef = useRef(location)
  locationRef.current = location
  const previousViewModeRef = useRef(null)
  const previousMobileTabRef = useRef(null)
  const errorTimeoutRef = useRef(null)
  const toastTimeoutRef = useRef(null)
  const latestMapBoundsRef = useRef(null)
  const scrollOnSelectRef = useRef(false)

  // One-shot "focus this school" entry param; consumed once the list has loaded and
  // dropped from the URL by the view-state sync below.
  const entrySchoolIdRef = useRef(searchParams.get('selected_school_id'))
  const rawSchoolType = searchParams.get('school_type')

  // Filters are read from the URL, and re-read whenever it changes: the page has its own
  // history entries (the detail panel), so Back can bring back an older filter set.
  const readFiltersFromUrl = (params) => {
    const getParamList = (key) => {
      const values = params.getAll(key)
      if (values.length > 0) return values
      const csv = params.get(key)
      return csv ? csv.split(',').map(value => value.trim()).filter(Boolean) : []
    }
    const type = params.get('school_type')
    const targetYearParam = params.get('target_year')
    return {
      ageGroup: params.get('age_group'),
      targetYear: targetYearParam ? parseInt(targetYearParam) : new Date().getFullYear() + 1,
      birthYear: Number.parseInt(params.get('birth_year') || '', 10) || null,
      schoolType: type === 'international' ? 'private' : type,
      educationLevel: params.get('education_level'),
      includeCrossover: params.get('include_crossover') === 'true',
      // Canonical, so links saved with older spellings ('English:intensive') still match.
      languageFocus: uniqueCanonical(getParamList('language_focus'), canonicalLanguagePair),
      specialPrograms: getParamList('special_programs'),
      facilities: getParamList('facilities'),
      teachingApproach: getParamList('teaching_approach'),
    }
  }

  const [filters, setFilters] = useState(() => readFiltersFromUrl(searchParams))

  useEffect(() => {
    const fromUrl = readFiltersFromUrl(new URLSearchParams(location.search))
    setFilters(prev => (JSON.stringify(prev) === JSON.stringify(fromUrl) ? prev : fromUrl))
  }, [location.search])



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

  // One fetch per age group; type and advanced filters apply on the client (same rules as
  // the API, except that Private also covers international schools).
  const { schools: ageSchools, loading, error } = useSchools(
    filters.ageGroup,
    null,
    effectiveEducationLevel,
    filters.includeCrossover,
    EMPTY_LIST,
    EMPTY_LIST,
    EMPTY_LIST,
    EMPTY_LIST,
    countryCode
  )

  // Skeletons only before anything is loaded; later loads keep the old results visible.
  const isFirstLoad = loading && ageSchools.length === 0

  // School type is a client-side filter too, so the State / Private chips are instant.
  const baseSchools = useMemo(
    () => ageSchools.filter(school => matchesSchoolType(school, filters.schoolType)),
    [ageSchools, filters.schoolType]
  )

  const schools = useMemo(
    () => baseSchools.filter(school => matchesAdvancedFilters(school, filters)),
    [baseSchools, filters]
  )

  useEffect(() => {
    let isMounted = true
    fetchAvailableFilters({ countryCode })
      .then((data) => {
        if (!isMounted) return
        setAvailableFilters({
          // Canonical keys, so "English" / "английски" / "Английски език" are one option.
          language_focus_pairs: uniqueCanonical(data.language_focus_pairs, canonicalLanguagePair),
          language_focus_languages: uniqueCanonical(data.language_focus_languages, languageKey),
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

  // A URL change we did not write (logo link, Back/Forward) carries its own view state.
  // Runs before the state-to-URL sync below, which then skips this commit: it would
  // otherwise write the old state over the URL we are adopting.
  useEffect(() => {
    const pendingIndex = pendingWritesRef.current.indexOf(location.search)
    if (pendingIndex >= 0) {
      // Our own write landing (earlier ones may have been skipped): nothing to adopt.
      pendingWritesRef.current = pendingWritesRef.current.slice(pendingIndex + 1)
      return
    }
    adoptingUrlRef.current = true
    const params = new URLSearchParams(location.search)
    const view = readViewParams(params)
    setSortBy(params.has('sort') || !userLocation ? view.sort : 'distance')
    setDistanceFilter(view.within)
    setViewMode(view.view)
    setMobileTab(view.tab)
    setSelectedSchoolId(view.school)
    setNameQuery(view.q)
  }, [location.search])

  // Keep sort, distance, view and selection in the URL (replace, so Back is not polluted).
  useEffect(() => {
    if (adoptingUrlRef.current) {
      adoptingUrlRef.current = false
      return
    }
    const next = writeViewParams(searchParams, {
      sort: sortBy,
      within: distanceFilter,
      view: viewMode,
      tab: mobileTab,
      school: selectedSchoolId,
      q: nameQuery,
    })
    if (!next) return
    // Remember the write until it lands, so the URL-to-state sync above does not echo it
    // back (an earlier write landing mid-typing would otherwise undo keystrokes).
    pendingWritesRef.current = [...pendingWritesRef.current, `?${next.toString()}`]
    // Keep the history state (it records whether the detail panel pushed this entry).
    setSearchParams(next, { replace: true, state: location.state })
  }, [sortBy, distanceFilter, viewMode, mobileTab, selectedSchoolId, nameQuery, searchParams, setSearchParams, location.state])


  useEffect(() => {
    rememberLastSearchUrl(`/search${location.search}`)
  }, [location.search])

  // Remember list scroll and map view for this history entry when leaving the page.
  useEffect(() => {
    return () => {
      saveViewState(locationRef.current.key, {
        search: locationRef.current.search,
        scrollTop: listScrollTopRef.current,
        listStart: visibleStartRef.current,
        map: mapViewRef.current,
      })
    }
  }, [])

  const handleMapViewChange = useStableCallback((view) => {
    mapViewRef.current = view
  })

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

  const clearLocationOverlay = useStableCallback(() => {
    setLocationOverlay({ schoolId: null, focusLocationId: null, hideOthers: false })
    setOpenLocationsId(null)
  })

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
    return () => {
      if (errorTimeoutRef.current) {
        clearTimeout(errorTimeoutRef.current)
      }
      cancelGeocode()
    }
  }, [])

  const handleSchoolSelect = (school, { source = 'list' } = {}) => {
    if (!school) return
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

  const handleOpenDetails = useStableCallback((school) => {
    if (!school) return
    const params = new URLSearchParams(searchParams)
    params.set('detail', String(school.id))
    if (detailSchoolId) {
      navigate(`/search?${params.toString()}`, { replace: true, state: location.state })
    } else {
      navigate(`/search?${params.toString()}`, { state: { detailPushed: true } })
    }
  })

  const handleCloseDetails = useStableCallback(() => {
    // Opened from these results: step back to them. Opened from a link: just drop the param.
    if (location.state?.detailPushed) {
      navigate(-1)
      return
    }
    const params = new URLSearchParams(searchParams)
    params.delete('detail')
    navigate(`/search?${params.toString()}`, { replace: true })
  })

  const handleAgeChange = useStableCallback((selection) => {
    handleFilterChange(selection)
  })

  // A school picked by name opens directly (panel on desktop, full page on mobile).
  const handleOpenSchoolByName = useStableCallback((school) => {
    if (isDesktopViewport()) {
      handleOpenDetails(school)
    } else {
      navigate(`/schools/${school.id}`)
    }
  })

  // Everything except the age selection, which is the parent's main choice.
  // One navigation; the URL-to-state syncs pick up the cleared values.
  const handleClearFilters = () => {
    setSearchInBounds(false)
    const params = new URLSearchParams(searchParams)
    ;['school_type', 'language_focus', 'special_programs', 'facilities', 'teaching_approach', 'q', 'within']
      .forEach(key => params.delete(key))
    navigate(`/search?${params.toString()}`, { replace: true, state: location.state })
  }

  const dismissWelcome = () => {
    setWelcomeDismissed(true)
    try {
      localStorage.setItem('welcomeDismissed', 'true')
    } catch {
      // A blocked storage just means the tip shows again next visit.
    }
  }

  const handleListSchoolSelect = useStableCallback((school) => handleSchoolSelect(school, { source: 'list' }))
  const handleMapSchoolSelect = useStableCallback((school) => {
    // With the panel open, a marker click shows that school in the panel.
    if (detailSchoolId && school) {
      handleOpenDetails(school)
      return
    }
    handleSchoolSelect(school, { source: 'map' })
  })

  const handleSchoolHover = useStableCallback((school) => {
    setHoveredSchoolId(school?.id ?? null)
  })

  const handleSchoolHoverEnd = useStableCallback(() => {
    setHoveredSchoolId(null)
  })

  const handleClearSelection = useStableCallback(() => {
    setSelectedSchoolId(null)
  })

  const handleToggleLocationsPanel = useStableCallback((school) => {
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
  })

  const handleShowLocationsForSchool = useStableCallback((school) => {
    if (!school) return
    setOpenLocationsId(school.id)
    setOverlayForSchool(school.id)
    setSelectedSchoolId(school.id)
  })

  const handleShowAllLocations = useStableCallback((schoolId) => {
    if (!schoolId) return
    setOpenLocationsId(schoolId)
    setOverlayForSchool(schoolId)
    setSelectedSchoolId(schoolId)
  })

  const handleFocusLocation = useStableCallback((schoolId, locationId) => {
    if (!schoolId || !locationId) return
    setOverlayForSchool(schoolId, { focusLocationId: locationId })
    setSelectedSchoolId(schoolId)
  })

  const handleToggleHideOthers = useStableCallback((schoolId) => {
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
  })

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

  // Each way of setting a location takes a token; a slower earlier request (GPS or an
  // address lookup) must not overwrite a location the parent has since set another way.
  const locationRequestRef = useRef(0)
  const startLocationRequest = () => {
    locationRequestRef.current += 1
    return locationRequestRef.current
  }

  const handleUseMyLocation = () => {
    const request = startLocationRequest()
    setLocationError(null)
    setIsPickingLocation(false)
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

  const handleAddressSearch = async () => {
    const trimmedAddress = addressInput.trim()
    if (!trimmedAddress) return

    const request = startLocationRequest()
    setIsLocating(false)
    setIsGeocoding(true)
    setLocationError(null)
    setIsPickingLocation(false)

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

  const handleClearLocation = () => {
    startLocationRequest()
    setIsLocating(false)
    setIsGeocoding(false)
    setIsPickingLocation(false)
    setUserLocation(null)
    setAddressInput('')
    setLocationError(null)
    setDistanceFilter('any')
    localStorage.removeItem('userLocation')
  }

  const handleStartMapPick = () => {
    setLocationError(null)
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

  const handleMapPickLocation = useStableCallback(({ lat, lng }) => {
    if (!Number.isFinite(lat) || !Number.isFinite(lng)) return
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
  })

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
    if (newFilters.birthYear !== undefined) {
      if (newFilters.birthYear) {
        params.set('birth_year', String(newFilters.birthYear))
      } else {
        params.delete('birth_year')
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
    if (school.school_type === 'state' || !school.pricing || school.pricing.length === 0) {
      return null
    }

    const tuitionPrices = school.pricing
      .filter(price => price.category === 'tuition')
      .map(price => toEur(monthlyEquivalent(price), price.currency))
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
      return { ...school, distance, distanceApproximate: Boolean(distanceLocation?.coordinates_approximate) }
    })
  }, [schools, userLocation, filters.ageGroup])

  const distanceLimit = userLocation && distanceFilter !== 'any' ? parseFloat(distanceFilter) : null

  const filteredSchools = useMemo(() => {
    return schoolsWithDistance.filter(school => (
      (!distanceLimit || (typeof school.distance === 'number' && school.distance <= distanceLimit)) &&
      schoolMatchesQuery(school, nameQuery)
    ))
  }, [schoolsWithDistance, distanceLimit, nameQuery])

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
    if (!entrySchoolIdRef.current) return
    const schoolId = Number.parseInt(entrySchoolIdRef.current, 10)
    if (!Number.isFinite(schoolId)) {
      entrySchoolIdRef.current = null
      return
    }

    const school = schools.find(item => item.id === schoolId)
    if (!school) return
    entrySchoolIdRef.current = null

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
  }, [schools, searchInBounds])

  useEffect(() => {
    if (!selectedSchoolId || loading) return
    const visibleIds = new Set(filteredSchools.map(school => school.id))
    if (!visibleIds.has(selectedSchoolId)) {
      setSelectedSchoolId(null)
    }
  }, [filteredSchools, selectedSchoolId, loading])

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
      case 'nvo': {
        // Highest latest combined NVO first; schools without results last.
        const scores = new Map(list.map(school => [
          school.id,
          getNvoDetail(school, null, { ageGroup: filters.ageGroup })?.latestCombined ?? Number.NEGATIVE_INFINITY,
        ]))
        list.sort((a, b) => scores.get(b.id) - scores.get(a.id))
        break
      }
      case 'name':
      default:
        list.sort((a, b) => {
          const nameA = getSchoolName(a, i18n.language)
          const nameB = getSchoolName(b, i18n.language)
          return compareSchoolNames(nameA, nameB, i18n.language)
        })
        break
    }

    return list
  }, [boundedSchools, sortBy, i18n.language, filters.ageGroup])

  // Restore the list position once the restored list has rendered.
  const pendingScrollRestoreRef = useRef(savedViewState?.scrollTop || 0)
  useLayoutEffect(() => {
    if (!pendingScrollRestoreRef.current || loading || sortedSchools.length === 0) return
    if (listScrollRef.current) {
      listScrollRef.current.scrollTop = pendingScrollRestoreRef.current
      listScrollTopRef.current = listScrollRef.current.scrollTop
    }
    pendingScrollRestoreRef.current = 0
  }, [loading, sortedSchools.length])

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

  // Moving the map only re-renders the page when the list is filtered by map area
  const handleBoundsChange = useStableCallback((bounds) => {
    latestMapBoundsRef.current = bounds
    if (searchInBounds) {
      debouncedBoundsHandler(bounds)
    }
  })

  useEffect(() => {
    if (searchInBounds) {
      setMapBounds(latestMapBoundsRef.current)
    }
  }, [searchInBounds])

  // Layout effect: after a window change the card is in place before the next paint, so
  // the top sentinel is not seen at the old scroll position.
  const listWindowMovedRef = useRef(false)
  useLayoutEffect(() => {
    if (!selectedSchoolId || !scrollOnSelectRef.current) return
    // A school picked on the map may be outside the rendered cards: render around it first.
    const index = sortedSchools.findIndex(school => school.id === selectedSchoolId)
    const nextWindow = windowForIndex(index, { start: visibleStart, end: visibleCount })
    if (nextWindow) {
      listWindowMovedRef.current = nextWindow.start !== visibleStart
      setVisibleStart(nextWindow.start)
      setVisibleCount(nextWindow.end)
      return
    }
    const node = document.querySelector(`[data-school-id="${selectedSchoolId}"]`)
    if (node) {
      // Different cards are now under the old scroll position: nothing to scroll through.
      node.scrollIntoView({ behavior: listWindowMovedRef.current ? 'auto' : 'smooth', block: 'center' })
    }
    listWindowMovedRef.current = false
    scrollOnSelectRef.current = false
  }, [selectedSchoolId, sortedSchools, visibleStart, visibleCount])

  // New results start at the top with the first page (not on map panning).
  const listKey = JSON.stringify([filters, sortBy, nameQuery, distanceFilter])
  const lastListKeyRef = useRef(listKey)
  useEffect(() => {
    if (lastListKeyRef.current === listKey) return
    lastListKeyRef.current = listKey
    setVisibleStart(0)
    setVisibleCount(LIST_PAGE_SIZE)
    if (listScrollRef.current) listScrollRef.current.scrollTop = 0
  }, [listKey])

  // A list that shrank without a new list key (map-area filtering) may end before a moved
  // window starts: go back to the first page rather than show nothing.
  useEffect(() => {
    if (loading || sortedSchools.length === 0 || visibleStart < sortedSchools.length) return
    setVisibleStart(0)
    setVisibleCount(LIST_PAGE_SIZE)
    if (listScrollRef.current) listScrollRef.current.scrollTop = 0
  }, [loading, sortedSchools.length, visibleStart])

  // Render the next page as the end of the list comes near.
  useEffect(() => {
    const sentinel = listSentinelRef.current
    if (!sentinel || typeof IntersectionObserver === 'undefined') return undefined
    const observer = new IntersectionObserver((entries) => {
      if (entries.some(entry => entry.isIntersecting)) {
        setVisibleCount(count => count + LIST_PAGE_SIZE)
      }
    }, { root: listScrollRef.current, rootMargin: '800px 0px' })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [visibleCount, sortedSchools.length])

  // Likewise render the previous page as the start of a moved window comes near. The
  // cards go in above the ones on screen, so keep those where they are.
  const heightBeforePrependRef = useRef(null)
  useEffect(() => {
    const sentinel = listTopSentinelRef.current
    if (!sentinel || typeof IntersectionObserver === 'undefined') return undefined
    const observer = new IntersectionObserver((entries) => {
      if (entries.some(entry => entry.isIntersecting)) {
        heightBeforePrependRef.current = listScrollRef.current?.scrollHeight ?? null
        setVisibleStart(start => Math.max(0, start - LIST_PAGE_SIZE))
      }
    }, { root: listScrollRef.current, rootMargin: '800px 0px' })
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [visibleStart])

  useLayoutEffect(() => {
    const list = listScrollRef.current
    if (list && heightBeforePrependRef.current !== null) {
      list.scrollTop += list.scrollHeight - heightBeforePrependRef.current
    }
    heightBeforePrependRef.current = null
  }, [visibleStart])

  // Reachable by Tab: rendered, and not inside a collapsed <details> (only its own
  // <summary> is). Chrome still reports boxes for collapsed content, so check directly.
  const isReachable = (element) => {
    if (element.getClientRects().length === 0) return false
    let child = element
    let parent = element.parentElement
    while (parent) {
      if (parent.tagName === 'DETAILS' && !parent.open) {
        const isOwnSummary = child === element && element.tagName === 'SUMMARY' && element.parentElement === parent
        if (!isOwnSummary) return false
      }
      child = parent
      parent = parent.parentElement
    }
    return true
  }

  // Modal drawer: focus moves in, Tab stays inside, Escape closes, focus returns after.
  useEffect(() => {
    if (!isFiltersOpen) return undefined
    const drawer = filtersDrawerRef.current
    const opener = document.activeElement
    drawer?.focus()
    const handleKeyDown = (event) => {
      if (event.key === 'Escape' && !event.defaultPrevented) {
        setIsFiltersOpen(false)
        return
      }
      if (event.key !== 'Tab' || !drawer) return
      const focusable = [...drawer.querySelectorAll(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
      )].filter(isReachable)
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && (document.activeElement === first || document.activeElement === drawer)) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      } else if (!drawer.contains(document.activeElement)) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      if (opener instanceof HTMLElement) opener.focus()
    }
  }, [isFiltersOpen])

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
  const panelBesideMap = Boolean(detailSchoolId) && viewMode === 'map-only'
  const showMap = viewMode === 'list-map' || viewMode === 'map-only'
  const hasCompare = compareList.length > 0

  const kindergartenOnlyGroups = config
    ? getExclusiveAgeGroups(config, 'kindergarten')
    : FALLBACK_KINDERGARTEN_ONLY_GROUPS

  // Kindergarten-only lists are counted as kindergartens, all-ages / preschool lists
  // neutrally, school grades as schools.
  const resultNounKey = !filters.ageGroup || (filters.ageGroup === 'preschool' && filters.includeCrossover)
    ? 'schools.resultsMixed'
    : kindergartenOnlyGroups.includes(filters.ageGroup) ||
      (filters.ageGroup === 'preschool' && filters.educationLevel === 'kindergarten')
      ? 'schools.resultsKindergarten'
      : 'schools.results'

  // Kindergartens have no NVO results, so that sort only applies when schools are listed.
  const kindergartensOnly = kindergartenOnlyGroups.includes(filters.ageGroup) ||
    (filters.ageGroup === 'preschool' && filters.educationLevel === 'kindergarten')
  const allSortOptions = userLocation
    ? [
        { value: 'distance', label: t('sorting.distance') },
        { value: 'name', label: t('sorting.name') },
        { value: 'nvo', label: t('sorting.nvo') },
        { value: 'type', label: t('sorting.type') },
        { value: 'price', label: t('sorting.price') },
      ]
    : [
        { value: 'name', label: t('sorting.name') },
        { value: 'nvo', label: t('sorting.nvo') },
        { value: 'type', label: t('sorting.type') },
        { value: 'price', label: t('sorting.pricePrivate') },
      ]
  const sortOptions = kindergartensOnly
    ? allSortOptions.filter(option => option.value !== 'nvo')
    : allSortOptions

  useEffect(() => {
    if (kindergartensOnly && sortBy === 'nvo') setSortBy('name')
  }, [kindergartensOnly, sortBy])

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

  // Quick State / Private chips above the list: the most used filter, one tap away.
  const renderSchoolTypeChips = () => (
    <div className="flex items-center gap-1.5" role="group" aria-label={t('filters.schoolType')}>
      {[null, 'state', 'private'].map(type => {
        const isActive = (filters.schoolType || null) === type
        return (
          <button
            key={type || 'all'}
            type="button"
            aria-pressed={isActive}
            onClick={() => handleFilterChange({ schoolType: type })}
            className={`h-11 md:h-8 rounded-full border px-3 text-sm font-medium transition-colors ${
              isActive
                ? 'border-primary-600 bg-primary-700 text-white'
                : 'border-neutral-300 bg-white text-neutral-700 hover:border-neutral-400'
            }`}
          >
            {type ? t(`schoolTypes.${type}`) : t('search.allTypes')}
          </button>
        )
      })}
    </div>
  )

  const formatAdvancedLabel = (key) => t(`advancedFilters.options.${key}`, { defaultValue: key.replace(/_/g, ' ') })
  const formatLanguageLabel = (key) => languageLabel(key, t)
  const formatLanguageLevelLabel = (key) => t(`advancedFilters.levels.${key}`, { defaultValue: key.replace(/_/g, ' ') })

  const advancedCounts = useMemo(() => {
    const IGNORE_KEY = {
      language_focus: 'languageFocus',
      special_programs: 'specialPrograms',
      facilities: 'facilities',
      teaching_approach: 'teachingApproach',
    }
    const matchesSelected = (school, groupToIgnore) => (
      matchesAdvancedFilters(school, filters, IGNORE_KEY[groupToIgnore])
    )

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
  }, [baseSchools, availableFilters, filters])

  // Scans every school, so compute once per data change rather than on every render
  const languageFilterOptions = useMemo(() => {
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
  }, [baseSchools, availableFilters])

  const renderAdvancedFilters = () => {
    const buildOptions = (key) => {
      const serverOptions = availableFilters[key] || []
      const defaults = DEFAULT_ADVANCED_OPTIONS[key] || []
      return Array.from(new Set([...defaults, ...serverOptions]))
    }

    const renderLanguageFocusGroup = () => {
      const { languages, levels, byLanguage } = languageFilterOptions
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
              // Languages no listed school offers are noise; keep one if it is selected.
              if (languageCount === 0 && selectedForLanguage.length === 0) return null

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
      <details open className="rounded-xl border border-neutral-200 bg-white p-4">
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

  // Chips for the selected advanced filters (age and school type have their own controls).
  const renderActiveFilters = () => {
    const advancedFilters = []
    const advancedByLanguage = new Map()

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

    if (advancedFilters.length === 0) return null
    return (
      <div className="flex items-center gap-3">
        <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
          {t('search.activeFilters')}
        </span>
        <div className="flex-1 overflow-x-auto">
          <div className="flex items-center gap-2 min-w-max py-1">
            {[...advancedByLanguage.entries()].map(([language, levels]) => (
              <span
                key={`lang-${language}`}
                className="inline-flex items-center gap-2 bg-primary-700 text-white text-xs px-2.5 py-1 rounded-full"
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
                className="inline-flex items-center gap-2 bg-primary-700 text-white text-xs px-2.5 py-1 rounded-full"
              >
                {filter.label}
                <button
                  type="button"
                  onClick={filter.onRemove}
                  className="w-6 h-6 -my-1 -mr-1 rounded-full flex items-center justify-center text-white hover:bg-primary-800 transition-colors"
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
    <Layout hideNavOnMobile hideFooter>
      <div className="h-[100dvh] lg:h-[calc(100dvh-64px)] flex flex-col">
        <h1 className="sr-only">{t('welcome.title')}</h1>
        {/* Mobile/Tablet Header */}
        <div className="lg:hidden border-b border-neutral-200 bg-white">
          <div className="flex items-center gap-2 px-3 py-2">
            <Link
              to="/search"
              className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-primary-500 to-primary-600 text-white"
              aria-label={t('nav.home')}
            >
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253" />
              </svg>
            </Link>

            <div className="flex-1 flex items-center justify-center">
              <div className="inline-flex items-center bg-neutral-100 rounded-lg p-0.5" role="tablist">
                {['list', 'map'].map(tab => (
                  <button
                    key={tab}
                    type="button"
                    role="tab"
                    aria-selected={mobileTab === tab}
                    onClick={() => setMobileTab(tab)}
                    className={`h-10 px-4 text-sm font-medium rounded-md transition-colors ${
                      mobileTab === tab
                        ? 'bg-white text-neutral-900 shadow-sm'
                        : 'text-neutral-600 hover:text-neutral-800'
                    }`}
                  >
                    {tab === 'list' ? t('search.listView') : t('search.mapView')}
                  </button>
                ))}
              </div>
            </div>

            <button
              type="button"
              onClick={() => setIsFiltersOpen(true)}
              className="flex h-11 min-w-[44px] items-center justify-center gap-1.5 px-3 rounded-lg bg-primary-50 text-primary-700 text-sm font-medium"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
              </svg>
              <span className="sr-only sm:not-sr-only">{t('search.filters')}</span>
            </button>
            <LanguageToggle compact />
          </div>
          <div className="grid grid-cols-2 gap-2 px-3 pb-2">
            <AgePicker
              ageGroup={filters.ageGroup}
              educationLevel={filters.educationLevel}
              includeCrossover={filters.includeCrossover}
              targetYear={filters.targetYear}
              birthYear={filters.birthYear}
              compact
              open={agePickerOpen && !isDesktopViewport()}
              onOpenChange={setAgePickerOpen}
              onChange={handleAgeChange}
            />
            <SchoolNameSearch compact value={nameQuery} onChange={setNameQuery} onOpenSchool={handleOpenSchoolByName} />
          </div>
        </div>

        {/* Desktop toolbar */}
        <div className="hidden lg:flex items-center gap-4 px-6 py-3 border-b border-neutral-200 bg-white">
          <AgePicker
            className="w-72 flex-shrink-0"
            ageGroup={filters.ageGroup}
            educationLevel={filters.educationLevel}
            includeCrossover={filters.includeCrossover}
            targetYear={filters.targetYear}
            birthYear={filters.birthYear}
            open={agePickerOpen && isDesktopViewport()}
            onOpenChange={setAgePickerOpen}
            onChange={handleAgeChange}
          />
          <SchoolNameSearch
            className="w-80 flex-shrink-0"
            value={nameQuery}
            onChange={setNameQuery}
            onOpenSchool={handleOpenSchoolByName}
          />

          <div className="flex-1">
            {renderActiveFilters()}
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
        <div className="relative flex-1 flex overflow-hidden">
          {detailSchoolId && (
            <SchoolDetailPanel
              schoolId={detailSchoolId}
              onClose={handleCloseDetails}
              beside={panelBesideMap}
            />
          )}
          {/* Desktop Filters Sidebar (20%) */}
          <aside className={`hidden ${panelBesideMap ? '' : 'lg:block'} w-80 flex-shrink-0 border-r border-neutral-200 bg-white overflow-y-auto`}>
            <div className="p-5 space-y-6">
              {renderMapBoundsFilter()}
              {renderAdvancedFilters()}
            </div>
          </aside>

          {/* School List (Desktop: 50%, Tablet: 60%, Mobile: full on List tab) */}
          <div className={`
            flex flex-col bg-neutral-50
            ${mobileTab === 'list' ? '' : 'hidden lg:flex'}
            ${viewMode === 'list-only' ? 'lg:flex-1' : 'lg:w-2/5'}
            ${showList ? 'lg:flex' : 'lg:hidden'}
            w-full
          `}>
              <div className="px-4 py-2 bg-white border-b border-neutral-200 space-y-2">
                {/* Row 1: where (location) and order (sort). Row 2: which (type) and how many. */}
                <div className="flex items-center justify-between gap-2">
                <LocationControl
                  userLocation={userLocation}
                  addressInput={addressInput}
                  onAddressInputChange={(value) => {
                    setAddressInput(value)
                    setLocationError(null)
                  }}
                  onUseMyLocation={handleUseMyLocation}
                  onAddressSearch={handleAddressSearch}
                  onStartMapPick={handleStartMapPick}
                  onClear={handleClearLocation}
                  isLocating={isLocating}
                  isGeocoding={isGeocoding}
                  isPickingLocation={isPickingLocation}
                  locationError={locationError}
                  distanceFilter={distanceFilter}
                  onDistanceFilterChange={setDistanceFilter}
                />
                  <div className="flex flex-shrink-0 items-center gap-2">
                    <label htmlFor="results-sort" className="text-sm font-medium text-neutral-700 max-sm:sr-only">
                      {t('sorting.sortBy')}
                    </label>
                    <select
                      id="results-sort"
                      value={sortBy}
                      onChange={(e) => setSortBy(e.target.value)}
                      className="h-11 md:h-auto max-w-[44vw] md:max-w-none border border-neutral-300 rounded-lg px-2 md:px-3 md:py-1.5 text-sm bg-white focus:ring-2 focus:ring-primary-500 focus:border-primary-500 transition-shadow"
                    >
                      {sortOptions.map(option => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div className="lg:hidden">
                  {renderActiveFilters()}
                </div>
                <div className="flex items-center justify-between gap-2">
                  {renderSchoolTypeChips()}
                  {isFirstLoad ? (
                    <div className="h-4 w-32 bg-neutral-200 animate-pulse rounded" />
                  ) : (
                    <p className="whitespace-nowrap text-sm text-neutral-600">
                      <span className="font-semibold text-neutral-900">{sortedSchools.length}</span>
                      {/* Phones show just the number beside the type chips; the noun stays for screen readers. */}
                      <span className="max-sm:sr-only">{' '}{t(resultNounKey, { count: sortedSchools.length })}</span>
                    </p>
                  )}
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
              <div
                ref={listScrollRef}
                onScroll={(event) => { listScrollTopRef.current = event.currentTarget.scrollTop }}
                className={`flex-1 overflow-y-auto [overflow-anchor:none] ${hasCompare ? 'pb-24' : ''}`}
              >
                {!filters.ageGroup && !nameQuery && !welcomeDismissed && (
                  <div className="mx-3 mt-3 rounded-xl border border-primary-200 bg-primary-50 p-3 sm:p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h2 className="text-sm sm:text-base font-semibold text-primary-900">{t('welcome.title')}</h2>
                        {/* The explanation is for wider screens; phones keep just the title and action. */}
                        <p className="mt-1 hidden text-sm text-primary-900/80 sm:block">{t('welcome.body')}</p>
                      </div>
                      <button
                        type="button"
                        onClick={dismissWelcome}
                        className="-mr-1 -mt-1 flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg text-primary-800 hover:bg-primary-100"
                        aria-label={t('welcome.dismiss')}
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    </div>
                    <button
                      type="button"
                      onMouseDown={(event) => event.stopPropagation()}
                      onClick={() => setAgePickerOpen(true)}
                      className="mt-2 sm:mt-3 h-10 rounded-lg bg-primary-700 px-4 text-sm font-medium text-white hover:bg-primary-800"
                    >
                      {t('welcome.cta')}
                    </button>
                  </div>
                )}

                {isFirstLoad && (
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
                    <button
                      type="button"
                      onClick={handleClearFilters}
                      className="mt-4 h-10 rounded-lg border border-neutral-300 bg-white px-4 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
                    >
                      {t('search.clearFilters')}
                    </button>
                  </div>
                )}

                {!isFirstLoad && !error && (
                <div className={loading ? 'opacity-60 transition-opacity' : 'transition-opacity'} aria-busy={loading}>
                {visibleStart > 0 && (
                  <div ref={listTopSentinelRef} className="h-16" aria-hidden="true" />
                )}
                {sortedSchools.slice(visibleStart, visibleCount).map(school => (
                  <SchoolCard
                    key={school.id}
                    school={school}
                    location={getLocationForAgeGroup(school, filters.ageGroup)}
                    isSelected={(detailSchoolId || selectedSchoolId) === school.id}
                    onClick={handleListSchoolSelect}
                    onOpenDetails={handleOpenDetails}
                    onHover={handleSchoolHover}
                    onHoverEnd={handleSchoolHoverEnd}
                    ageGroupOrder={ageGroupOrder}
                    activeAgeGroup={filters.ageGroup}
                    isLocationsOpen={openLocationsId === school.id}
                    locationOverlay={locationOverlay}
                    onToggleLocations={handleToggleLocationsPanel}
                    onShowAllLocations={handleShowAllLocations}
                    onFocusLocation={handleFocusLocation}
                    onClearLocations={clearLocationOverlay}
                    examAverages={examAverages}
                  />
                ))}
                {visibleCount < sortedSchools.length && (
                  <div ref={listSentinelRef} className="h-16" aria-hidden="true" />
                )}
                </div>
                )}
              </div>
            </div>

          {/* Map (Desktop: 30%, Tablet: 40%, Mobile: full on Map tab) */}
          <div className={`
            flex-1 relative
            ${mobileTab === 'map' ? '' : 'hidden lg:block'}
            ${showMap ? 'lg:block' : 'lg:hidden'}
          `}>
                {isPickingLocation && (
                  <div className="absolute inset-x-3 top-16 z-[1000] flex items-center justify-between gap-3 rounded-lg bg-neutral-900/90 px-4 py-2 text-sm text-white shadow-lg" role="status">
                    <span>{t('location.mapPickHint')}</span>
                    <button
                      type="button"
                      onClick={handleStartMapPick}
                      className="h-9 flex-shrink-0 rounded-md bg-white/15 px-3 font-medium hover:bg-white/25"
                    >
                      {t('location.cancelMapPick')}
                    </button>
                  </div>
                )}
                <SchoolMap
                  schools={filteredSchools}
                  activeAgeGroup={filters.ageGroup}
                  selectedSchoolId={detailSchoolId || selectedSchoolId}
                  hoveredSchoolId={hoveredSchoolId}
                  onOpenDetails={handleOpenDetails}
                  onSchoolSelect={handleMapSchoolSelect}
                  onClearSelection={handleClearSelection}
                  loading={loading}
                  userLocation={userLocation}
                  isPickingLocation={isPickingLocation}
                  onPickLocation={handleMapPickLocation}
                  onBoundsChange={handleBoundsChange}
                  onViewChange={handleMapViewChange}
                  initialView={savedViewState?.map || null}
                  autoFit={!searchInBounds && !selectedSchoolId && !locationOverlay.schoolId}
                  hasCompare={hasCompare}
                  resizeKey={`${viewMode}-${mobileTab}-${showMap}-${panelBesideMap}`}
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
            <div
              role="dialog"
              aria-modal="true"
              aria-labelledby="filters-drawer-title"
              tabIndex={-1}
              ref={filtersDrawerRef}
              className={`
              fixed z-[2100] bg-white lg:hidden
              md:top-0 md:right-0 md:bottom-0 md:w-96 md:shadow-2xl
              max-md:bottom-0 max-md:left-0 max-md:right-0 max-md:rounded-t-2xl max-md:shadow-up max-md:max-h-[85vh]
              overflow-y-auto
            `}>
              <div className="sticky top-0 bg-white border-b border-neutral-200 px-5 py-4 flex items-center justify-between">
                <h2 id="filters-drawer-title" className="font-semibold text-neutral-900">{t('search.filters')}</h2>
                <button
                  type="button"
                  onClick={() => setIsFiltersOpen(false)}
                  aria-label={t('common.close')}
                  className="flex h-10 w-10 items-center justify-center hover:bg-neutral-100 rounded-lg transition-colors"
                >
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
              <div className="p-5">
                <div className="space-y-6">
                  {/* Map-area filter where the map is visible, or while it is on so it can be turned off. */}
                  {(mobileTab === 'map' || searchInBounds) && renderMapBoundsFilter()}
                  {renderAdvancedFilters()}
                  <Link to="/about" className="inline-block text-sm text-primary-700 underline">
                    {t('nav.about')}
                  </Link>
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
