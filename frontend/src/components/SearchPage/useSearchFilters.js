import { useEffect, useState } from 'react'
import { fetchAvailableFilters, fetchExamAverages } from '../../api/schools.js'
import {
  EMPTY_AVAILABLE_FILTERS,
  filterSearchParams,
  nextFilterState,
  normalizeAvailableFilters,
  readFiltersFromUrl,
} from '../../utils/searchFilters.js'

// The search filters, kept in the URL. Re-read whenever the URL changes, so Back can bring
// back an older filter set; a change replaces the current history entry.
export function useUrlFilters({ searchParams, locationSearch, navigate }) {
  const [filters, setFilters] = useState(() => readFiltersFromUrl(searchParams))

  useEffect(() => {
    const fromUrl = readFiltersFromUrl(new URLSearchParams(locationSearch))
    setFilters(prev => (JSON.stringify(prev) === JSON.stringify(fromUrl) ? prev : fromUrl))
  }, [locationSearch])

  const handleFilterChange = (newFilters) => {
    const nextFilters = nextFilterState(filters, newFilters)
    setFilters(nextFilters)
    const params = filterSearchParams(searchParams, newFilters, nextFilters)
    navigate(`/search?${params.toString()}`, { replace: true })
  }

  return { filters, handleFilterChange }
}

// The filter options the API offers and the city's exam averages, per country.
export function useFilterData(countryCode) {
  const [availableFilters, setAvailableFilters] = useState(EMPTY_AVAILABLE_FILTERS)
  const [examAverages, setExamAverages] = useState(null)

  useEffect(() => {
    let isMounted = true
    fetchAvailableFilters({ countryCode })
      .then((data) => {
        if (!isMounted) return
        setAvailableFilters(normalizeAvailableFilters(data))
      })
      .catch(() => {
        if (!isMounted) return
        setAvailableFilters(EMPTY_AVAILABLE_FILTERS)
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

  return { availableFilters, examAverages }
}
