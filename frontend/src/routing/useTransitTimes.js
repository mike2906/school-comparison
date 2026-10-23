import { useEffect, useMemo, useRef, useState } from 'react'
import { parseCoordinate } from '../utils/coordinates'

const EMPTY = new Map()

/**
 * Public transport trips from home to each school's pinned building, arriving by its
 * `deadline` (seconds after midnight), worked out in a Web Worker (docs/TRANSIT_TIMES.md).
 * Nothing loads until `enabled` and a home are set. `schools` carry `distanceLocation`.
 * Returns { status: 'idle' | 'loading' | 'ready' | 'outside' | 'unavailable',
 * trips: Map id -> { leave, arrive, deadline, lines }, serviceDate }.
 */
export function useTransitTimes(home, schools, enabled) {
  const workerRef = useRef(null)
  const requestRef = useRef(0)
  const lastRequestRef = useRef(null)
  const [state, setState] = useState({ status: 'idle', trips: EMPTY, serviceDate: null })

  const targets = useMemo(() => {
    if (!home || !enabled) return []
    return schools.flatMap(school => {
      const lat = parseCoordinate(school.distanceLocation?.lat)
      const lng = parseCoordinate(school.distanceLocation?.lng)
      return lat != null && lng != null ? [{ id: school.id, lat, lng, deadline: school.arriveBy }] : []
    })
  }, [schools, home, enabled])

  useEffect(() => () => {
    workerRef.current?.terminate()
    workerRef.current = null
  }, [])

  useEffect(() => {
    if (!home || !enabled) {
      requestRef.current += 1 // an answer still on its way is no longer wanted
      lastRequestRef.current = null
      setState({ status: 'idle', trips: EMPTY, serviceDate: null })
      return
    }
    if (typeof Worker === 'undefined') {
      setState({ status: 'unavailable', trips: EMPTY, serviceDate: null })
      return
    }
    if (!workerRef.current) {
      workerRef.current = new Worker(new URL('./transitTimes.worker.js', import.meta.url), { type: 'module' })
    }
    const worker = workerRef.current
    const originKey = `${home.lat},${home.lng}`
    const targetsKey = targets.map(t => `${t.id}:${t.lat},${t.lng}@${t.deadline}`).join(';')
    const last = lastRequestRef.current
    if (last && last.originKey === originKey && last.targetsKey === targetsKey) return
    lastRequestRef.current = { originKey, targetsKey }
    const requestId = ++requestRef.current
    // A new home makes every trip stale; a new school list for the same home keeps them.
    setState(prev => ({ ...prev, status: 'loading', trips: last?.originKey === originKey ? prev.trips : EMPTY }))
    worker.onmessage = ({ data }) => {
      if (data.requestId !== requestRef.current) return
      setState({
        status: data.status,
        trips: data.status === 'ready' ? new Map(Object.entries(data.trips).map(([id, trip]) => [Number(id), trip])) : EMPTY,
        serviceDate: data.meta?.serviceDate ?? null,
      })
    }
    worker.postMessage({ requestId, origin: { lat: home.lat, lng: home.lng }, targets })
  }, [home, enabled, targets])

  return state
}
