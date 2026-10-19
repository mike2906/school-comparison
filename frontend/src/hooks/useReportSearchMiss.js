import { useEffect } from 'react'
import { reportSearchMiss, searchSchools } from '../api/schools'

const SETTLE_MS = 3000
const reported = new Set()

/**
 * Counts a name search that found nothing: once the parent has stopped typing for three
 * seconds, nothing in the list matches (`noMatchHere`) and the API's search finds no school
 * either. Each text once per page load.
 */
export function useReportSearchMiss(query, noMatchHere) {
  const trimmed = query.trim()
  // As the API cleans it up, so spacing and case variants count once.
  const key = trimmed.toLowerCase().split(/\s+/).join(' ')
  useEffect(() => {
    if (!noMatchHere || trimmed.length < 3 || reported.has(key)) return undefined
    let current = true
    const timer = setTimeout(() => {
      searchSchools(trimmed)
        .then(data => {
          if (!current || data.length > 0 || reported.has(key)) return
          reported.add(key)
          reportSearchMiss(trimmed)
        })
        .catch(() => {})
    }, SETTLE_MS)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [trimmed, key, noMatchHere])
}
