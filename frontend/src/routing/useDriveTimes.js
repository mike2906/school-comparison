import { useEffect, useMemo, useRef, useState } from 'react'
import { parseCoordinate } from '../utils/coordinates'

const EMPTY = new Map()

/**
 * Free-flow drive seconds from the user's location to each school's pinned building,
 * worked out in a Web Worker. Nothing loads until a location is set.
 * `schools` carry `distanceLocation` (the pinned building used for distance).
 * Returns { status: 'idle' | 'loading' | 'ready' | 'outside' | 'unavailable', seconds: Map }.
 */
export function useDriveTimes(userLocation, schools) {
  const workerRef = useRef(null)
  const requestRef = useRef(0)
  const [state, setState] = useState({ status: 'idle', seconds: EMPTY })

  const targets = useMemo(() => {
    if (!userLocation) return []
    return schools.flatMap(school => {
      const lat = parseCoordinate(school.distanceLocation?.lat)
      const lng = parseCoordinate(school.distanceLocation?.lng)
      return lat != null && lng != null ? [{ id: school.id, lat, lng }] : []
    })
  }, [schools, userLocation])

  useEffect(() => () => workerRef.current?.terminate(), [])

  useEffect(() => {
    if (!userLocation) {
      requestRef.current += 1 // an answer still on its way is for the old location
      setState({ status: 'idle', seconds: EMPTY })
      return
    }
    if (typeof Worker === 'undefined') {
      setState({ status: 'unavailable', seconds: EMPTY })
      return
    }
    if (!workerRef.current) {
      workerRef.current = new Worker(new URL('./driveTimes.worker.js', import.meta.url), { type: 'module' })
    }
    const worker = workerRef.current
    const requestId = ++requestRef.current
    setState(prev => ({ status: 'loading', seconds: prev.seconds }))
    worker.onmessage = ({ data }) => {
      if (data.requestId !== requestRef.current) return
      setState({
        status: data.status,
        seconds: data.status === 'ready' ? new Map(Object.entries(data.seconds).map(([id, s]) => [Number(id), s])) : EMPTY,
      })
    }
    worker.postMessage({ requestId, origin: { lat: userLocation.lat, lng: userLocation.lng }, targets })
  }, [userLocation, targets])

  return state
}
