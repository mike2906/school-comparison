import { useEffect, useState } from 'react'
import { searchSchools } from '../api/schools'

const NONE = new Set()

/**
 * Ids of the schools the API's name search finds for `query`, fetched only while `wanted`.
 * The API also knows names the list can't match in the browser (a brand written only in
 * the web address, busy-school.com). Same request and delay as the search box, so the
 * two share one cached call.
 */
export function useSearchHits(query, wanted) {
  const [hits, setHits] = useState({ query: '', ids: NONE })
  const trimmed = query.trim()
  const active = wanted && trimmed.length >= 2
  useEffect(() => {
    if (!active) return undefined
    let current = true
    const timer = setTimeout(() => {
      searchSchools(trimmed)
        .then(data => { if (current) setHits({ query: trimmed, ids: new Set(data.map(school => school.id)) }) })
        .catch(() => { if (current) setHits({ query: trimmed, ids: NONE }) })
    }, 250)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [active, trimmed])
  return active && hits.query === trimmed ? hits.ids : NONE
}
