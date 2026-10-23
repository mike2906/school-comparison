// Public transport times in the browser (docs/TRANSIT_TIMES.md): the timetable built by
// tools/transit/build_timetable.mjs (format there) and a range RAPTOR search that answers, for
// every school at once, "when must we leave home to arrive by the start of the day, and how
// long is the trip". Plain module: runs in the routing worker and in Node tests.
//
// Times are whole seconds after midnight of the timetable's service day.

import { snapToFoot, walkFrom } from './footGraph.js'

const MAGIC = 0x54544231
const VERSION = 1
const NO_TIME = 0x7fffffff

/** Walking limits, seconds. */
export const ACCESS_LIMIT = 20 * 60 // home to the first stop
export const EGRESS_LIMIT = 15 * 60 // last stop to the school
export const WALK_ONLY_LIMIT = 40 * 60 // a school close enough to just walk
export const MAX_TRIP = 100 * 60 // earliest departure considered before a deadline
export const MAX_RIDES = 4 // up to three changes
export const CHANGE_SLACK = 2 * 60 // from one vehicle to the next
export const BOARD_SLACK = 2 * 60 // at the first stop before the vehicle leaves
export const CHANGE_PENALTY = 5 * 60 // how much later a change must let you leave

export function decodeTimetable(buf) {
  const h = new Uint32Array(buf, 0, 12)
  if (h[0] !== MAGIC || h[1] !== VERSION) throw new Error('Unknown timetable format')
  const [, , S, P, R, X, Z, T, serviceDate, validUntil, nameBytes, Q] = h
  let off = 48
  const take = (Ty, n) => {
    const a = new Ty(buf, off, n)
    off += a.byteLength
    off += (4 - (off % 4)) % 4
    return a
  }
  const dLat = take(Int32Array, S)
  const dLon = take(Int32Array, S)
  const stopChain = take(Int32Array, S)
  const stopFrac = take(Uint16Array, S)
  const stopOff = take(Uint16Array, S)
  const nTransfers = take(Uint8Array, S)
  const tTo = take(Uint16Array, X)
  const tSec = take(Uint16Array, X)
  const routeType = take(Uint8Array, R)
  const names = new TextDecoder().decode(take(Uint8Array, nameBytes)).split('\n')
  const patRoute = take(Uint16Array, P)
  const patNStops = take(Uint16Array, P)
  const patNTrips = take(Uint16Array, P)
  const patStops = take(Uint16Array, Z)
  const timesD = take(Uint16Array, T)
  const qLat = take(Int32Array, Q)
  const qLon = take(Int32Array, Q)
  const qChain = take(Int32Array, Q)
  const qFrac = take(Uint16Array, Q)
  const qOff = take(Uint16Array, Q)
  const qCount = take(Uint8Array, Q)
  let E = 0
  for (let i = 0; i < Q; i++) E += qCount[i]
  const qStop = take(Uint16Array, E)
  const qSec = take(Uint16Array, E)
  // School buildings worked out at build time, by "lat,lng" (6 decimals): where they join
  // the foot graph and the stops within a walk.
  const egress = new Map()
  for (let i = 0, a = 0, b = 0, e = 0; i < Q; i++) {
    a += qLat[i]
    b += qLon[i]
    const stops = []
    for (let j = 0; j < qCount[i]; j++, e++) stops.push([qStop[e], qSec[e]])
    const snap = qChain[i] < 0 ? null : { chain: qChain[i], frac: qFrac[i] / 65535, offDs: qOff[i] }
    egress.set(`${(a / 1e6).toFixed(6)},${(b / 1e6).toFixed(6)}`, { snap, stops })
  }

  const lat = new Float64Array(S)
  const lon = new Float64Array(S)
  for (let i = 0, a = 0, b = 0; i < S; i++) {
    a += dLat[i]
    b += dLon[i]
    lat[i] = a / 1e6
    lon[i] = b / 1e6
  }
  const tStart = new Uint32Array(S + 1)
  for (let i = 0; i < S; i++) tStart[i + 1] = tStart[i] + nTransfers[i]
  // Times: per pattern, trip after trip; the first stop's time is a step from the previous
  // trip's first stop, every later one a step from the trip's previous stop.
  const stopStart = new Uint32Array(P + 1)
  const timeStart = new Uint32Array(P + 1)
  for (let p = 0; p < P; p++) {
    stopStart[p + 1] = stopStart[p] + patNStops[p]
    timeStart[p + 1] = timeStart[p] + patNStops[p] * patNTrips[p]
  }
  const times = new Int32Array(T)
  for (let p = 0, i = 0; p < P; p++) {
    let first = 0
    for (let j = 0; j < patNTrips[p]; j++) {
      first += timesD[i]
      times[i++] = first
      for (let k = 1; k < patNStops[p]; k++, i++) times[i] = times[i - 1] + timesD[i]
    }
  }
  // Patterns through each stop, with the stop's position in the pattern.
  const count = new Uint32Array(S + 1)
  for (let z = 0; z < Z; z++) count[patStops[z] + 1]++
  for (let s = 0; s < S; s++) count[s + 1] += count[s]
  const fill = count.slice(0, S)
  const spPat = new Uint32Array(Z)
  const spPos = new Uint16Array(Z)
  for (let p = 0; p < P; p++) {
    for (let k = 0; k < patNStops[p]; k++) {
      const s = patStops[stopStart[p] + k]
      spPat[fill[s]] = p
      spPos[fill[s]++] = k
    }
  }
  return {
    S, P, lat, lon, stopChain, stopFrac, stopOff, tStart, tTo, tSec, routeType,
    routeName: names, patRoute, patNStops, patNTrips, patStops, stopStart, timeStart, times,
    spStart: count, spPat, spPos, serviceDate, validUntil, egress, stopGrid: null,
  }
}

function stopSnap(tt, s) {
  if (tt.stopChain[s] < 0) return null
  return { chain: tt.stopChain[s], frac: tt.stopFrac[s] / 65535, offDs: tt.stopOff[s] }
}

// Stops bucketed in ~1.1 km cells, for the walks near a point.
function stopsNear(tt, lng, lat, metres) {
  const CELL = 0.01
  if (!tt.stopGrid) {
    const cells = new Map()
    for (let s = 0; s < tt.S; s++) {
      const key = `${Math.floor(tt.lon[s] / CELL)},${Math.floor(tt.lat[s] / CELL)}`
      if (!cells.has(key)) cells.set(key, [])
      cells.get(key).push(s)
    }
    tt.stopGrid = cells
  }
  const out = []
  const kx = Math.cos((lat * Math.PI) / 180)
  const r = Math.ceil(metres / 111000 / CELL / kx)
  const cx = Math.floor(lng / CELL), cy = Math.floor(lat / CELL)
  for (let y = cy - r; y <= cy + r; y++) {
    for (let x = cx - r; x <= cx + r; x++) {
      for (const s of tt.stopGrid.get(`${x},${y}`) || []) {
        const d = Math.hypot((tt.lon[s] - lng) * kx, tt.lat[s] - lat) * 111000
        if (d <= metres) out.push(s)
      }
    }
  }
  return out
}

/**
 * Stops within a walk of a point: [[stop, seconds], ...]. Walking is on the foot graph; the
 * straight-line radius only picks the stops worth checking.
 */
export function walkToStops(tt, fg, point, limitSec = ACCESS_LIMIT) {
  const snap = point.snap ?? snapToFoot(fg, point.lng, point.lat)
  if (!snap) return { snap: null, stops: [] }
  const walk = walkFrom(fg, snap, limitSec * 10)
  const stops = []
  for (const s of stopsNear(tt, point.lng, point.lat, (limitSec * fg.speed) + 50)) {
    const to = stopSnap(tt, s)
    const ds = to ? walk.costTo(to) : Infinity
    if (ds < Infinity) stops.push([s, Math.round(ds / 10)])
  }
  return { snap, stops, walk }
}

/**
 * A school building's place on the foot graph and the stops within a walk of it: worked out at
 * build time, or now for a building moved since. { snap, stops: [[stop, sec]] }
 */
export function schoolWalks(tt, fg, point) {
  const known = tt.egress.get(`${Number(point.lat).toFixed(6)},${Number(point.lng).toFixed(6)}`)
  if (known) return known
  const { snap, stops } = walkToStops(tt, fg, point, EGRESS_LIMIT)
  return { snap, stops }
}

/**
 * Arrive-by search from home to many targets in one pass (range RAPTOR, latest departure
 * first). targets: [{ id, egress: [[stop, sec]], walkSec, deadline }]. Returns
 * Map id -> { leave, arrive, rides } (rides 0 means walking all the way), or no entry when
 * nothing arrives in time within MAX_TRIP. Among the ways to arrive in time, a change counts
 * as CHANGE_PENALTY of extra time, so a parent is not sent on three changes to leave 2 min later.
 */
export function arriveBy(tt, accessWalks, targets) {
  const access = accessWalks.map(([s, w]) => [s, w + BOARD_SLACK])
  const { S, patNStops, patNTrips, stopStart, timeStart, times, patStops, spStart, spPat, spPos, tStart, tTo, tSec } = tt
  const K = MAX_RIDES
  const result = new Map()
  if (targets.length === 0) return result
  let lo = Infinity, hi = -Infinity
  for (const t of targets) {
    lo = Math.min(lo, t.deadline - MAX_TRIP)
    hi = Math.max(hi, t.deadline)
  }
  // Every home departure that catches some vehicle at an access stop, latest first, to the
  // minute (leaving at the start of the minute is never too late).
  const deps = new Set()
  for (const [s, w] of access) {
    for (let i = spStart[s]; i < spStart[s + 1]; i++) {
      const p = spPat[i], k = spPos[i], n = patNStops[p]
      for (let j = 0; j < patNTrips[p]; j++) {
        const dep = times[timeStart[p] + j * n + k]
        if (dep - w >= lo && dep <= hi) deps.add(Math.floor((dep - w) / 60) * 60)
      }
    }
  }
  const order = [...deps].sort((a, b) => b - a)

  // Targets by stop, so a better arrival at a stop updates the targets it walks to.
  const open = targets.filter(t => t.egress.length > 0)
  const nT = open.length
  const stCount = new Uint32Array(S + 1)
  for (const t of open) for (const [s] of t.egress) stCount[s + 1]++
  for (let s = 0; s < S; s++) stCount[s + 1] += stCount[s]
  const stFill = stCount.slice(0, S)
  const stTarget = new Uint32Array(stCount[S])
  const stWalk = new Int32Array(stCount[S])
  open.forEach((t, ti) => {
    for (const [s, w] of t.egress) {
      stTarget[stFill[s]] = ti
      stWalk[stFill[s]++] = w
    }
  })

  // arr[k * S + s]: earliest arrival at s with at most k rides, over departures so far;
  // tArr the same at each target.
  const arr = new Int32Array((K + 1) * S).fill(NO_TIME)
  const tArr = new Int32Array((K + 1) * nT).fill(NO_TIME)
  const tStop = new Int32Array((K + 1) * nT) // the stop walked from to reach the target
  // How each label was reached: a ride (pattern, trip, boarding position), a walk from another
  // stop in the same round (pattern -1, the stop), or the label with one ride fewer (-2).
  const viaPat = new Int32Array((K + 1) * S)
  const viaA = new Int32Array((K + 1) * S)
  const viaB = new Int32Array((K + 1) * S)
  const improve = (k, s, t, pat, a, b) => {
    const i = k * S + s
    arr[i] = t
    viaPat[i] = pat
    viaA[i] = a
    viaB[i] = b
    const row = k * nT
    for (let x = stCount[s]; x < stCount[s + 1]; x++) {
      const v = t + stWalk[x], j = row + stTarget[x]
      if (v < tArr[j]) {
        tArr[j] = v
        tStop[j] = s
      }
    }
  }
  // The rides of the journey that reached stop s with at most k rides, first ride first.
  const legsTo = (k, s) => {
    const legs = []
    while (k > 0) {
      const i = k * S + s, pat = viaPat[i]
      if (pat === -2) {
        k--
      } else if (pat === -1) {
        s = viaA[i]
      } else {
        const n = patNStops[pat], row = timeStart[pat] + viaA[i] * n, from = patStops[stopStart[pat] + viaB[i]]
        const pos = patStops.subarray(stopStart[pat], stopStart[pat] + n).indexOf(s, viaB[i])
        legs.push({ route: tt.patRoute[pat], from, to: s, dep: times[row + viaB[i]], arr: times[row + pos] })
        s = from
        k--
      }
    }
    return legs.reverse()
  }
  let marked = new Uint8Array(S)
  let next = new Uint8Array(S)
  const patFrom = new Int32Array(tt.P).fill(-1)
  const patList = []
  // Per target and ride count: [leave, arrive] of the latest departure that is in time.
  const settled = open.map(() => new Array(K + 1).fill(null))
  let unsettled = nT * K

  for (const tau of order) {
    marked.fill(0)
    for (const [s, w] of access) {
      const t = tau + w
      if (t < arr[s]) {
        arr[s] = t
        marked[s] = 1
      }
    }
    for (let k = 1; k <= K; k++) {
      const prev = (k - 1) * S, cur = k * S
      patList.length = 0
      for (let s = 0; s < S; s++) {
        if (!marked[s]) continue
        for (let i = spStart[s]; i < spStart[s + 1]; i++) {
          const p = spPat[i]
          if (patFrom[p] < 0) patList.push(p)
          if (patFrom[p] < 0 || spPos[i] < patFrom[p]) patFrom[p] = spPos[i]
        }
      }
      next.fill(0)
      let any = false
      for (const p of patList) {
        const n = patNStops[p], m = patNTrips[p], base = timeStart[p], st = stopStart[p]
        let trip = -1, boardAt = -1
        for (let i = patFrom[p]; i < n; i++) {
          const s = patStops[st + i]
          if (trip >= 0) {
            const a = times[base + trip * n + i]
            if (a < arr[cur + s] && a < arr[prev + s]) {
              improve(k, s, a, p, trip, boardAt)
              next[s] = 1
              any = true
            }
          }
          // Board an earlier trip here if the stop was reached in time for it; after a ride,
          // allow CHANGE_SLACK to get from one vehicle to the next.
          const reached = arr[prev + s]
          if (reached === NO_TIME) continue
          const ready = k > 1 && arr[s] > reached ? reached + CHANGE_SLACK : reached
          if (trip < 0 || ready < times[base + trip * n + i]) {
            // Earliest trip leaving at or after `ready` (trips never overtake in a pattern).
            let a = 0, b = trip < 0 ? m : trip
            while (a < b) {
              const mid = (a + b) >> 1
              if (times[base + mid * n + i] >= ready) b = mid
              else a = mid + 1
            }
            if (a < m && a !== trip) {
              trip = a
              boardAt = i
            }
          }
        }
        patFrom[p] = -1
      }
      // Walk to nearby stops from every stop reached by this round's rides.
      for (let s = 0; s < S; s++) {
        if (!next[s]) continue
        const a = arr[cur + s]
        for (let x = tStart[s]; x < tStart[s + 1]; x++) {
          const u = tTo[x], t = a + tSec[x]
          if (t < arr[cur + u] && t < arr[prev + u]) {
            improve(k, u, t, -1, s, 0)
            next[u] = 1
          }
        }
      }
      // A stop not improved this round keeps its value with fewer rides.
      for (let s = 0; s < S; s++) {
        if (marked[s] && !next[s] && arr[prev + s] < arr[cur + s]) {
          arr[cur + s] = arr[prev + s]
          viaPat[cur + s] = -2
        }
      }
      const tmp = marked
      marked = next
      next = tmp
      if (!any) break
    }
    // Settle each ride count at the latest departure that still arrives in time.
    for (let ti = 0; ti < nT; ti++) {
      const st = settled[ti], deadline = open[ti].deadline
      let a = NO_TIME, from = -1
      for (let k = 1; k <= K; k++) {
        if (tArr[k * nT + ti] < a) {
          a = tArr[k * nT + ti]
          from = k
        }
        if (!st[k] && a <= deadline) {
          st[k] = [tau, a, legsTo(from, tStop[from * nT + ti])]
          unsettled--
        }
      }
    }
    if (unsettled === 0) break
  }
  const index = new Map(open.map((t, ti) => [t, ti]))
  targets.forEach(t => {
    let pick = t.walkSec <= WALK_ONLY_LIMIT ? { leave: t.deadline - Math.round(t.walkSec), arrive: t.deadline, rides: 0, legs: [] } : null
    let score = pick ? t.walkSec : Infinity
    const ti = index.get(t)
    if (ti !== undefined) {
      settled[ti].forEach((v, k) => {
        if (!v) return
        const sc = t.deadline - v[0] + CHANGE_PENALTY * (k - 1)
        if (sc < score) {
          score = sc
          pick = { leave: v[0], arrive: v[1], rides: v[2].length, legs: v[2] }
        }
      })
    }
    if (pick) result.set(t.id, pick)
  })
  return result
}
