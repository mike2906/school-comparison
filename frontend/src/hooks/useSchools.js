import { useMemo, useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { fetchSchools } from '../api/schools'
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
  const [schools, setSchools] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const { i18n } = useTranslation()

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
          city,
        })
        setSchools(data)
      } catch (err) {
        setError(err.message || 'Failed to load schools')
      } finally {
        setLoading(false)
      }
    }

    loadSchools()
  }, [countryCode, city, ageGroup, schoolType, educationLevel, includeCrossover, languageFocus, specialPrograms, facilities, teachingApproach])

  const localizedSchools = useMemo(
    () => normalizeSchoolList(schools, i18n.language),
    [schools, i18n.language]
  )

  return { schools: localizedSchools, loading, error }
}
