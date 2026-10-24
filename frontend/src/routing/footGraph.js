// Walking on the foot graph built by tools/transit/build_foot_graph.py (format in that file,
// design in docs/TRANSIT_TIMES.md). Plain module: runs in the routing worker, in the timetable
// builder and in Node tests. Snapping is shared with the car graph (roadGraph.js).
//
// The graph is undirected and has no turn costs, so the search is a plain node Dijkstra,
// bounded by a walking limit. Units are deciseconds.

import { snapToRoad } from './roadGraph.js'

const MAGIC = 0x46475431
const VERSION = 1

export function decodeFootGraph(buf) {
  const h = new Uint32Array(buf, 0, 6)
  if (h[0] !== MAGIC || h[1] !== VERSION) throw new Error('Unknown foot graph format')
  const [, , N, C, G, speedMm] = h
  let off = 24
  const take = (T, n) => {
    const a = new T(buf, off, n)
    off += a.byteLength
    off += (4 - (off % 4)) % 4
    return a
  }
  const dLat = take(Int32Array, N)
  const dLon = take(Int32Array, N)
  const dA = take(Int32Array, C)
  const dB = take(Int32Array, C)
  const weight = take(Uint16Array, C)
  const nPts = take(Uint16Array, C)
  const geomD = take(Int16Array, 2 * G)

  const lat = new Int32Array(N)
  const lon = new Int32Array(N)
  for (let i = 0, a = 0, b = 0; i < N; i++) {
    a += dLat[i]
    b += dLon[i]
    lat[i] = a
    lon[i] = b
  }
  const ca = new Uint32Array(C)
  const cb = new Uint32Array(C)
  const deg = new Uint32Array(N + 1)
  for (let c = 0, a = 0; c < C; c++) {
    a += dA[c]
    ca[c] = a
    cb[c] = a + dB[c]
    deg[ca[c] + 1]++
    deg[cb[c] + 1]++
  }
  // Both directions of every chain: adjacency lists of (neighbour, chain).
  const firstOut = new Uint32Array(N + 1)
  for (let i = 1; i <= N; i++) firstOut[i] = firstOut[i - 1] + deg[i]
  const fill = firstOut.slice(0, N)
  const adjNode = new Uint32Array(2 * C)
  const adjChain = new Uint32Array(2 * C)
  for (let c = 0; c < C; c++) {
    adjNode[fill[ca[c]]] = cb[c]
    adjChain[fill[ca[c]]++] = c
    adjNode[fill[cb[c]]] = ca[c]
    adjChain[fill[cb[c]]++] = c
  }
  // Chain shapes in absolute 1e-5 deg, both end nodes included (the layout snapToRoad reads).
  const geomStart = new Uint32Array(C + 1)
  for (let c = 0; c < C; c++) geomStart[c + 1] = geomStart[c] + nPts[c] + 2
  const gx = new Int32Array(geomStart[C])
  const gy = new Int32Array(geomStart[C])
  for (let c = 0, gi = 0; c < C; c++) {
    let k = geomStart[c]
    let x = Math.round(lon[ca[c]] / 10)
    let y = Math.round(lat[ca[c]] / 10)
    gx[k] = x
    gy[k++] = y
    for (let j = 0; j < nPts[c]; j++, gi++) {
      x += geomD[2 * gi]
      y += geomD[2 * gi + 1]
      gx[k] = x
      gy[k++] = y
    }
    gx[k] = Math.round(lon[cb[c]] / 10)
    gy[k] = Math.round(lat[cb[c]] / 10)
  }
  return {
    N, C, lat, lon, ca, cb, weight, firstOut, adjNode, adjChain, geomStart, gx, gy,
    speed: speedMm / 1000, grid: null, scratch: null,
  }
}

/**
 * Nearest walkable point: { chain, frac, metres } or null beyond ~1.2 km. The walk onto the
 * graph is counted as straight at walking speed (offDs, deciseconds).
 */
export function snapToFoot(fg, lng, lat) {
  const s = snapToRoad(fg, lng, lat)
  return s && { ...s, offDs: (s.metres / fg.speed) * 10 }
}

function scratchFor(fg) {
  if (!fg.scratch) {
    fg.scratch = {
      dist: new Float64Array(fg.N).fill(Infinity),
      touched: new Uint32Array(fg.N),
      keys: new Float64Array(1 << 14),
      vals: new Uint32Array(1 << 14),
    }
  }
  return fg.scratch
}

/**
 * Walk from a snapped point up to maxDs deciseconds. Returns a reader: costTo(snap) gives the
 * deciseconds to another snapped point (Infinity beyond the limit). Valid until the next walk
 * on the same graph.
 */
export function walkFrom(fg, from, maxDs) {
  const sc = scratchFor(fg)
  const { dist, touched } = sc
  const { firstOut, adjNode, adjChain, weight } = fg
  // Clear the previous walk.
  for (let i = 0; i < sc.nTouched; i++) dist[touched[i]] = Infinity
  sc.nTouched = 0
  let n = 0
  const push = (k, v) => {
    if (n === sc.keys.length) {
      const nk = new Float64Array(n * 2)
      nk.set(sc.keys)
      sc.keys = nk
      const nv = new Uint32Array(n * 2)
      nv.set(sc.vals)
      sc.vals = nv
    }
    const keys = sc.keys, vals = sc.vals
    let i = n++
    while (i > 0) {
      const p = (i - 1) >> 1
      if (keys[p] <= k) break
      keys[i] = keys[p]
      vals[i] = vals[p]
      i = p
    }
    keys[i] = k
    vals[i] = v
  }
  const pop = () => {
    const keys = sc.keys, vals = sc.vals
    const top = vals[0]
    const lk = keys[--n], lv = vals[n]
    let i = 0
    for (;;) {
      let c = 2 * i + 1
      if (c >= n) break
      if (c + 1 < n && keys[c + 1] < keys[c]) c++
      if (keys[c] >= lk) break
      keys[i] = keys[c]
      vals[i] = vals[c]
      i = c
    }
    keys[i] = lk
    vals[i] = lv
    return top
  }
  const relax = (v, d) => {
    if (d > maxDs || d >= dist[v]) return
    if (dist[v] === Infinity) touched[sc.nTouched++] = v
    dist[v] = d
    push(d, v)
  }
  const w0 = weight[from.chain]
  relax(fg.ca[from.chain], from.offDs + from.frac * w0)
  relax(fg.cb[from.chain], from.offDs + (1 - from.frac) * w0)
  while (n > 0) {
    const d = sc.keys[0]
    const v = pop()
    if (d > dist[v]) continue
    for (let e = firstOut[v]; e < firstOut[v + 1]; e++) relax(adjNode[e], d + weight[adjChain[e]])
  }
  return {
    costTo(to) {
      const w = weight[to.chain]
      let best = Math.min(dist[fg.ca[to.chain]] + to.frac * w, dist[fg.cb[to.chain]] + (1 - to.frac) * w)
      if (to.chain === from.chain) best = Math.min(best, from.offDs + Math.abs(to.frac - from.frac) * w)
      best += to.offDs
      return best <= maxDs ? best : Infinity
    },
  }
}
