import { useState, useEffect } from 'react'
import { fetchSchools } from '../api/schools'

export function useSchools(
  ageGroup = null,
  schoolType = null,
  educationLevel = null,
  includeCrossover = false,
  languageFocus = [],
  specialPrograms = [],
  facilities = [],
  teachingApproach = [],
  countryCode = 'bg'
) {
  const [schools, setSchools] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const loadSchools = async () => {
      setLoading(true)
      setError(null)

      try {
        const data = await fetchSchools({
          countryCode,
          ageGroup,
          schoolType,
          educationLevel,
          includeCrossover,
          languageFocus,
          specialPrograms,
          facilities,
          teachingApproach,
        })
        setSchools(data)
      } catch (err) {
        setError(err.message || 'Failed to load schools')
      } finally {
        setLoading(false)
      }
    }

    loadSchools()
  }, [countryCode, ageGroup, schoolType, educationLevel, includeCrossover, languageFocus, specialPrograms, facilities, teachingApproach])

  return { schools, loading, error }
}
