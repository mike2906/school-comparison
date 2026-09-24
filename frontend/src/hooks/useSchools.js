import { useMemo, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { fetchSchools, peekSchools } from '../api/schools'
import { normalizeSchoolList } from '../utils/schoolAttributes'

export function useSchools(
  ageGroup = null,
  schoolType = null,
  educationLevel = null,
  includeCrossover = false,
  languageFocus = [],
  specialPrograms = [],
  facilities = [],
  teachingApproach = [],
  countryCode = 'bg',
  city = 'sofia'
) {
  const options = useMemo(() => ({
    countryCode,
    ageGroup,
    schoolType,
    educationLevel,
    includeCrossover,
    languageFocus,
    specialPrograms,
    facilities,
    teachingApproach,
    city,
  }), [countryCode, city, ageGroup, schoolType, educationLevel, includeCrossover, languageFocus, specialPrograms, facilities, teachingApproach])

  // Start from already-loaded data (e.g. returning from a school page) so there is no skeleton flash.
  const [schools, setSchools] = useState(() => peekSchools(options) || [])
  const [loading, setLoading] = useState(() => peekSchools(options) === undefined)
  const [error, setError] = useState(null)
  const { i18n } = useTranslation()

  useEffect(() => {
    let isCurrent = true
    const cached = peekSchools(options)
    if (cached !== undefined) {
      setSchools(cached)
      setLoading(false)
      setError(null)
      return () => { isCurrent = false }
    }

    setLoading(true)
    setError(null)
    fetchSchools(options)
      .then(data => {
        if (isCurrent) setSchools(data)
      })
      .catch(err => {
        if (isCurrent) setError(err.message || 'Failed to load schools')
      })
      .finally(() => {
        if (isCurrent) setLoading(false)
      })

    return () => { isCurrent = false }
  }, [options])

  const localizedSchools = useMemo(
    () => normalizeSchoolList(schools, i18n.language),
    [schools, i18n.language]
  )

  return { schools: localizedSchools, loading, error }
}
