import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { fetchSchools } from '../api/schools'
import { schoolCatchmentIds } from '../admission/catchments'
import { getSchoolName } from '../utils/i18n'

/**
 * Our own name for a catchment's school, in the reader's language: the catchment file
 * only has the municipality's Bulgarian name (82. ОУ "Васил Априлов"), which is fine in
 * Bulgarian. In other languages it loads the school list (cached, the same request as
 * the search page) once a name is wanted, and falls back to the catchment's name until
 * then or when no school matches.
 */
export function useCatchmentSchoolName(catchmentSchoolId, catchmentName) {
  const { i18n } = useTranslation()
  const [schools, setSchools] = useState(null)
  const wanted = Boolean(catchmentSchoolId) && !String(i18n.language).startsWith('bg')
  useEffect(() => {
    if (!wanted || schools) return undefined
    let live = true
    fetchSchools().then(list => live && setSchools(list), () => {})
    return () => {
      live = false
    }
  }, [wanted, schools])
  if (!catchmentSchoolId) return null
  if (!wanted) return catchmentName || null
  const own = schools?.find(school => schoolCatchmentIds(school).includes(catchmentSchoolId))
  return own ? getSchoolName(own, i18n.language) : catchmentName || null
}
