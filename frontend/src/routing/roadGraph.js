// Car routing in the browser on the road graph built by tools/road-graph/build_graph.py
// (format in that file, design in docs/DRIVE_TIMES.md). Plain module: runs in the drive-time
// worker and in Node tests.
//
// The search is edge-based: a label is the cost of entering an edge, so turn penalties and
// turn restrictions can look at the edge the car came from. Costs follow OSRM's car profile:
// turn penalty 7.5 s with a 1.075 right-hand bias, u-turns only at dead ends (+20 s), and
// penalties only where more than two roads meet. Units are deciseconds until costTo().

const MAGIC = 0x53524731
const VERSION = 2
const TURN_PENALTY = 75
const U_TURN = 200
const BIAS = 1.075
const CELL = 500 // snapping grid cell, 1e-5 deg (~400 x 550 m)
const SNAP_CELLS = 3 // cells searched each way: at least ~1.2 km, beyond MAX_START_METRES

export function decodeGraph(buf) {
  const h = new Uint32Array(buf, 0, 8)
  if (h[0] !== MAGIC || h[1] !== VERSION) throw new Error('Unknown road graph format')
  const [, , N, E, C, G, R, O] = h
  let off = 32
  const take = (T, n) => {
    const a = new T(buf, off, n)
    off += a.byteLength
    off += (4 - (off % 4)) % 4
    return a
  }
  const dLat = take(Int32Array, N)
  const dLon = take(Int32Array, N)
  const degree = take(Uint32Array, N + 1)
  const dTarget = take(Int32Array, E)
  const weight = take(Uint16Array, E)
  const nRoads = take(Uint8Array, N)
  const bearingIn = take(Uint8Array, E)
  const bearingOut = take(Uint8Array, E)
  const chainFwd = take(Int32Array, C)
  const chainBwd = take(Int32Array, C)
  const nPts = take(Uint16Array, C)
  const geomD = take(Int16Array, 2 * G)
  const bannedFrom = take(Uint32Array, R)
  const bannedTo = take(Uint32Array, R)
  const limited = take(Uint32Array, O)
  const limitedFrom = take(Uint32Array, O)

  const lat = new Int32Array(N)
  const lon = new Int32Array(N)
  for (let i = 0, a = 0, b = 0; i < N; i++) {
    a += dLat[i]
    b += dLon[i]
    lat[i] = a
    lon[i] = b
  }
  const firstOut = new Uint32Array(N + 1)
  for (let i = 1; i <= N; i++) firstOut[i] = firstOut[i - 1] + degree[i]
  const src = new Uint32Array(E)
  const tgt = new Uint32Array(E)
  for (let v = 0; v < N; v++) {
    for (let e = firstOut[v]; e < firstOut[v + 1]; e++) {
      src[e] = v
      tgt[e] = v + dTarget[e]
    }
  }
  // Chain shapes in absolute 1e-5 deg, both end nodes included.
  const geomStart = new Uint32Array(C + 1)
  for (let c = 0; c < C; c++) geomStart[c + 1] = geomStart[c] + nPts[c] + 2
  const gx = new Int32Array(geomStart[C])
  const gy = new Int32Array(geomStart[C])
  for (let c = 0, gi = 0; c < C; c++) {
    const f = chainFwd[c]
    const s = f >= 0 ? src[f] : tgt[chainBwd[c]]
    const t = f >= 0 ? tgt[f] : src[chainBwd[c]]
    let k = geomStart[c]
    let x = Math.round(lon[s] / 10)
    let y = Math.round(lat[s] / 10)
    gx[k] = x
    gy[k++] = y
    for (let j = 0; j < nPts[c]; j++, gi++) {
      x += geomD[2 * gi]
      y += geomD[2 * gi + 1]
      gx[k] = x
      gy[k++] = y
    }
    gx[k] = Math.round(lon[t] / 10)
    gy[k] = Math.round(lat[t] / 10)
  }
  const restricted = new Uint8Array(E)
  const banned = new Set()
  for (let i = 0; i < R; i++) {
    restricted[bannedFrom[i]] = 1
    banned.add(bannedFrom[i] * E + bannedTo[i])
  }
  const onlyFrom = new Int32Array(E).fill(-1)
  for (let i = 0; i < O; i++) onlyFrom[limited[i]] = limitedFrom[i]
  // Turn penalty by bearing difference (256 steps); a positive angle is a right turn.
  const turnTable = new Float64Array(256)
  for (let d = 0; d < 256; d++) {
    const angle = ((((d + 128) & 255) - 128) * 360) / 256
    turnTable[d] = angle >= 0
      ? TURN_PENALTY / (1 + Math.exp(-((13 / BIAS) * angle / 180 - 6.5 * BIAS)))
      : TURN_PENALTY / (1 + Math.exp(-((13 * BIAS) * -angle / 180 - 6.5 / BIAS)))
  }
  return {
    N, E, C, lat, lon, firstOut, src, tgt, weight, nRoads, bearingIn, bearingOut, chainFwd, chainBwd,
    geomStart, gx, gy, restricted, banned, onlyFrom, turnTable, grid: null,
  }
}

// Bucket every chain segment into a coarse grid, built on first use.
function buildGrid(g) {
  const { C, geomStart, gx, gy } = g
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
  for (let k = 0; k < gx.length; k++) {
    if (gx[k] < minX) minX = gx[k]
    if (gx[k] > maxX) maxX = gx[k]
    if (gy[k] < minY) minY = gy[k]
    if (gy[k] > maxY) maxY = gy[k]
  }
  const cols = Math.floor((maxX - minX) / CELL) + 1
  const rows = Math.floor((maxY - minY) / CELL) + 1
  const cellOf = (x, y) => Math.floor((y - minY) / CELL) * cols + Math.floor((x - minX) / CELL)
  const forEachCell = (k, fn) => {
    const x0 = Math.min(gx[k], gx[k + 1]), x1 = Math.max(gx[k], gx[k + 1])
    const y0 = Math.min(gy[k], gy[k + 1]), y1 = Math.max(gy[k], gy[k + 1])
    for (let cy = Math.floor((y0 - minY) / CELL); cy <= Math.floor((y1 - minY) / CELL); cy++) {
      for (let cx = Math.floor((x0 - minX) / CELL); cx <= Math.floor((x1 - minX) / CELL); cx++) fn(cy * cols + cx)
    }
  }
  const start = new Uint32Array(cols * rows + 1)
  for (let c = 0; c < C; c++) {
    for (let k = geomStart[c]; k < geomStart[c + 1] - 1; k++) forEachCell(k, cell => { start[cell + 1]++ })
  }
  for (let i = 1; i < start.length; i++) start[i] += start[i - 1]
  const fill = start.slice(0, -1)
  const seg = new Uint32Array(start[start.length - 1])
  const segChain = new Uint32Array(seg.length)
  for (let c = 0; c < C; c++) {
    for (let k = geomStart[c]; k < geomStart[c + 1] - 1; k++) {
      forEachCell(k, cell => {
        seg[fill[cell]] = k
        segChain[fill[cell]++] = c
      })
    }
  }
  return { minX, minY, cols, rows, start, seg, segChain, cellOf }
}

/**
 * Nearest point on a road the user can start from or arrive at (not motorways).
 * Returns { chain, frac (by length along the chain), metres } or null when no road is
 * within ~1.2 km.
 */
export function snapToRoad(g, lng, lat) {
  if (!g.grid) g.grid = buildGrid(g)
  const { minX, minY, cols, rows, start, seg, segChain } = g.grid
  const { geomStart, gx, gy } = g
  const kx = Math.cos((lat * Math.PI) / 180)
  const px = lng * 1e5
  const py = lat * 1e5
  const cx = Math.floor((px - minX) / CELL)
  const cy = Math.floor((py - minY) / CELL)
  let best = Infinity, bc = -1, bk = 0, bt = 0
  for (let y = cy - SNAP_CELLS; y <= cy + SNAP_CELLS; y++) {
    if (y < 0 || y >= rows) continue
    for (let x = cx - SNAP_CELLS; x <= cx + SNAP_CELLS; x++) {
      if (x < 0 || x >= cols) continue
      const cell = y * cols + x
      for (let i = start[cell]; i < start[cell + 1]; i++) {
        const k = seg[i]
        const ax = gx[k] * kx, ay = gy[k], bx = gx[k + 1] * kx, by = gy[k + 1]
        const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy
        let t = len2 > 0 ? ((px * kx - ax) * dx + (py - ay) * dy) / len2 : 0
        t = t < 0 ? 0 : t > 1 ? 1 : t
        const ex = ax + t * dx - px * kx, ey = ay + t * dy - py
        const d = ex * ex + ey * ey
        if (d < best) {
          best = d
          bc = segChain[i]
          bk = k
          bt = t
        }
      }
    }
  }
  if (bc < 0) return null
  let total = 0, before = 0
  for (let k = geomStart[bc]; k < geomStart[bc + 1] - 1; k++) {
    const len = Math.hypot((gx[k + 1] - gx[k]) * kx, gy[k + 1] - gy[k])
    if (k < bk) before += len
    else if (k === bk) before += len * bt
    total += len
  }
  return { chain: bc, frac: total > 0 ? before / total : 0, metres: Math.sqrt(best) * 1.11 }
}

// Cost of turning from edge f into edge e, or -1 when the turn is not allowed.
function turnCost(g, f, e) {
  const v = g.tgt[f]
  // Back the way it came: same two nodes and a heading within ~22 degrees of reversed, so a
  // different road between the same two junctions is an ordinary turn.
  const uTurn = g.tgt[e] === g.src[f] && Math.abs(((g.bearingIn[e] - g.bearingOut[f]) & 255) - 128) <= 16
  if (uTurn && g.firstOut[v + 1] - g.firstOut[v] > 1) return -1
  if (g.onlyFrom[e] >= 0 && g.onlyFrom[e] !== f) return -1
  if (g.restricted[f] && g.banned.has(f * g.E + e)) return -1
  let cost = 0
  if (g.nRoads[v] > 2 || uTurn) cost = g.turnTable[(g.bearingIn[e] - g.bearingOut[f]) & 255]
  return uTurn ? cost + U_TURN : cost
}

/** One search from a snapped origin over the whole graph; withParents keeps the route tree. */
export function searchFrom(g, origin, { withParents = false } = {}) {
  const { E, firstOut, tgt, weight } = g
  const dist = new Float64Array(E).fill(Infinity)
  const parent = withParents ? new Int32Array(E).fill(-1) : null
  let cap = 1 << 16
  let keys = new Float64Array(cap)
  let vals = new Uint32Array(cap)
  let n = 0
  const push = (k, v) => {
    if (n === cap) {
      cap *= 2
      const nk = new Float64Array(cap)
      nk.set(keys)
      keys = nk
      const nv = new Uint32Array(cap)
      nv.set(vals)
      vals = nv
    }
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
  const relaxFrom = (f, base) => {
    const v = tgt[f]
    for (let e = firstOut[v]; e < firstOut[v + 1]; e++) {
      const tc = turnCost(g, f, e)
      if (tc < 0) continue
      const nd = base + tc
      if (nd < dist[e]) {
        dist[e] = nd
        if (parent) parent[e] = f
        push(nd, e)
      }
    }
  }
  // Start part-way along the snapped road, in each direction the road allows.
  const fwd = g.chainFwd[origin.chain]
  const bwd = g.chainBwd[origin.chain]
  if (fwd >= 0) relaxFrom(fwd, (1 - origin.frac) * weight[fwd])
  if (bwd >= 0) relaxFrom(bwd, origin.frac * weight[bwd])
  while (n > 0) {
    const d = keys[0]
    const f = pop()
    if (d > dist[f]) continue
    relaxFrom(f, d + weight[f])
  }
  return { dist, origin, parent }
}

/** Free-flow seconds from the search origin to a snapped destination (Infinity if unreachable). */
export function secondsTo(g, result, dest) {
  const { dist, origin } = result
  const { weight } = g
  const f = g.chainFwd[dest.chain]
  const b = g.chainBwd[dest.chain]
  let best = Infinity
  if (f >= 0) best = Math.min(best, dist[f] + dest.frac * weight[f])
  if (b >= 0) best = Math.min(best, dist[b] + (1 - dest.frac) * weight[b])
  if (dest.chain === origin.chain) {
    if (f >= 0 && dest.frac >= origin.frac) best = Math.min(best, (dest.frac - origin.frac) * weight[f])
    if (b >= 0 && dest.frac <= origin.frac) best = Math.min(best, (origin.frac - dest.frac) * weight[b])
  }
  return best / 10
}
