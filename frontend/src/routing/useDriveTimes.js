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
  const lastRequestRef = useRef(null) // { originKey, targetsKey } of the request in flight or answered
  const [state, setState] = useState({ status: 'idle', seconds: EMPTY })

  const targets = useMemo(() => {
    if (!userLocation) return []
    return schools.flatMap(school => {
      const lat = parseCoordinate(school.distanceLocation?.lat)
      const lng = parseCoordinate(school.distanceLocation?.lng)
      return lat != null && lng != null ? [{ id: school.id, lat, lng }] : []
    })
  }, [schools, userLocation])

  useEffect(() => () => {
    workerRef.current?.terminate()
    workerRef.current = null
  }, [])

  useEffect(() => {
    if (!userLocation) {
      requestRef.current += 1 // an answer still on its way is for the old location
      lastRequestRef.current = null
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
    const originKey = `${userLocation.lat},${userLocation.lng}`
    const targetsKey = targets.map(t => `${t.id}:${t.lat},${t.lng}`).join(';')
    const last = lastRequestRef.current
    if (last && last.originKey === originKey && last.targetsKey === targetsKey) return
    lastRequestRef.current = { originKey, targetsKey }
    const requestId = ++requestRef.current
    // A new start makes every time stale; a new school list for the same start keeps them.
    setState(prev => ({ status: 'loading', seconds: last?.originKey === originKey ? prev.seconds : EMPTY }))
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
