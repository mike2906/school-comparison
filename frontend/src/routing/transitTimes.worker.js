// Public transport times off the main thread. The walking graph and the timetable are
// fetched once (plain GETs of static files) and the home never leaves the browser.
import { decodeFootGraph, walkFrom } from './footGraph.js'
import { arriveBy, decodeTimetable, schoolWalks, walkToStops, ACCESS_LIMIT, WALK_ONLY_LIMIT } from './transit.js'
import { FOOT_GRAPH_URL, TIMETABLE_URL, timetableFresh } from './transitTimes.js'
import { MAX_START_METRES } from './driveTimes.js'

let dataPromise = null
const schools = new Map() // "lat,lng" -> { snap, stops }

async function fetchBuffer(url) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`)
  let buffer = await response.arrayBuffer()
  const head = new Uint8Array(buffer, 0, 2)
  // Still gzipped unless the server sent it with Content-Encoding and the browser unpacked it.
  if (head[0] === 0x1f && head[1] === 0x8b) {
    const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'))
    buffer = await new Response(stream).arrayBuffer()
  }
  return buffer
}

async function loadData() {
  const [foot, timetable] = await Promise.all([fetchBuffer(FOOT_GRAPH_URL), fetchBuffer(TIMETABLE_URL)])
  return { fg: decodeFootGraph(foot), tt: decodeTimetable(timetable) }
}

self.onmessage = async ({ data }) => {
  const { requestId, origin, targets } = data
  try {
    if (!dataPromise) {
      dataPromise = loadData()
      dataPromise.catch(() => { dataPromise = null })
    }
    const { fg, tt } = await dataPromise
    const meta = { serviceDate: tt.serviceDate }
    if (!timetableFresh(tt.serviceDate, tt.validUntil)) {
      self.postMessage({ requestId, status: 'unavailable', meta })
      return
    }
    const home = walkToStops(tt, fg, origin, ACCESS_LIMIT)
    if (!home.snap || home.snap.metres > MAX_START_METRES) {
      self.postMessage({ requestId, status: 'outside', meta })
      return
    }
    // School buildings first: a building moved since the build is walked here, and each walk
    // replaces the previous one on the graph.
    const places = targets.map(target => {
      const key = `${target.lat},${target.lng}`
      if (!schools.has(key)) schools.set(key, schoolWalks(tt, fg, target))
      return schools.get(key)
    })
    const walk = walkFrom(fg, home.snap, WALK_ONLY_LIMIT * 10)
    const list = targets.map((target, i) => ({
      id: target.id,
      egress: places[i].stops,
      deadline: target.deadline,
      walkSec: places[i].snap ? walk.costTo(places[i].snap) / 10 : Infinity,
    }))
    const deadlines = new Map(list.map(t => [t.id, t.deadline]))
    const trips = {}
    for (const [id, trip] of arriveBy(tt, home.stops, list)) {
      trips[id] = {
        leave: trip.leave,
        arrive: trip.arrive,
        deadline: deadlines.get(id),
        lines: trip.legs.map(leg => ({ name: tt.routeName[leg.route], type: tt.routeType[leg.route] })),
      }
    }
    self.postMessage({ requestId, status: 'ready', trips, meta })
  } catch {
    self.postMessage({ requestId, status: 'unavailable' })
  }
}
