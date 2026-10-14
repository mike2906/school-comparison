// Drive times off the main thread. The road graph is fetched once (a plain GET of a static
// file) and the user's location never leaves the browser.
import { decodeGraph, snapToRoad, searchFrom, secondsTo } from './roadGraph.js'
import { MAX_START_METRES, ROAD_GRAPH_URL } from './driveTimes.js'

let graphPromise = null
const snaps = new Map()
let lastSearch = null // { key, result }: a new school list for the same start needs no new search

async function loadGraph() {
  const response = await fetch(ROAD_GRAPH_URL)
  if (!response.ok) throw new Error(`Road graph: HTTP ${response.status}`)
  let buffer = await response.arrayBuffer()
  const head = new Uint8Array(buffer, 0, 2)
  // Still gzipped unless the server sent it with Content-Encoding and the browser unpacked it.
  if (head[0] === 0x1f && head[1] === 0x8b) {
    const stream = new Blob([buffer]).stream().pipeThrough(new DecompressionStream('gzip'))
    buffer = await new Response(stream).arrayBuffer()
  }
  return decodeGraph(buffer)
}

function snapTarget(graph, { lat, lng }) {
  const key = `${lat},${lng}`
  if (!snaps.has(key)) snaps.set(key, snapToRoad(graph, lng, lat))
  return snaps.get(key)
}

self.onmessage = async ({ data }) => {
  const { requestId, origin, targets } = data
  try {
    if (!graphPromise) {
      graphPromise = loadGraph()
      graphPromise.catch(() => { graphPromise = null })
    }
    const graph = await graphPromise
    const start = snapToRoad(graph, origin.lng, origin.lat)
    if (!start || start.metres > MAX_START_METRES) {
      self.postMessage({ requestId, status: 'outside' })
      return
    }
    const key = `${origin.lat},${origin.lng}`
    if (lastSearch?.key !== key) lastSearch = { key, result: searchFrom(graph, start) }
    const { result } = lastSearch
    const seconds = {}
    for (const target of targets) {
      const dest = snapTarget(graph, target)
      const s = dest && dest.metres <= MAX_START_METRES ? secondsTo(graph, result, dest) : Infinity
      if (Number.isFinite(s)) seconds[target.id] = s
    }
    self.postMessage({ requestId, status: 'ready', seconds })
  } catch {
    self.postMessage({ requestId, status: 'unavailable' })
  }
}
