import { useEffect } from 'react'
import { reportSearchMiss, searchSchools } from '../api/schools'

const SETTLE_MS = 2000
const reported = new Set()

/**
 * Counts a name search that found nothing: once the parent has stopped typing for two
 * seconds, nothing in the list matches (`noMatchHere`) and the API's search finds no school
 * either. Each text once per page load.
 */
export function useReportSearchMiss(query, noMatchHere) {
  const trimmed = query.trim()
  useEffect(() => {
    if (!noMatchHere || trimmed.length < 3 || reported.has(trimmed.toLowerCase())) return undefined
    let current = true
    const timer = setTimeout(() => {
      searchSchools(trimmed)
        .then(data => {
          const key = trimmed.toLowerCase()
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
  }, [trimmed, noMatchHere])
}
