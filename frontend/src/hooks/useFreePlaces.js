import { useEffect, useState } from 'react'
import { fetchFreePlaces } from '../api/schools'
import { usableFreePlaces } from '../utils/freePlaces'

/**
 * Today's free places (one shared, cached request), or null while loading, on an error
 * or when the snapshot is too old: the places are extra, so a failure only hides them.
 * `enabled` false skips the request (a private school, a page with no such groups).
 */
export function useFreePlaces(enabled = true) {
  const [data, setData] = useState(null)
  useEffect(() => {
    if (!enabled) return undefined
    let live = true
    fetchFreePlaces().then(answer => live && setData(usableFreePlaces(answer)), () => live && setData(null))
    return () => {
      live = false
    }
  }, [enabled])
  return enabled ? data : null
}
