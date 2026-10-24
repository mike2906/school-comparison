// Pack one school day of Sofia's GTFS timetable for public transport times in the browser
// (docs/TRANSIT_TIMES.md). Usage:
//   node build_timetable.mjs <unzipped gtfs dir> <foot graph .bin> <out .bin>
//     [--date YYYYMMDD] [--schools schools.json]
//
// --schools takes the site's /schools response: the walks from each school building to its
// nearby stops are worked out here, so a phone only walks from home. A building moved since
// the build is walked in the browser.
//
// The service day is pinned: the first Tuesday to Thursday from tomorrow whose number of trips
// is a normal weekday's (so not a holiday timetable), unless --date says otherwise. Only
// trips that run in the morning or midday windows are kept, whole.
//
// Output (little-endian; each section padded to 4 bytes so typed arrays can view it):
//   header      Uint32[12] magic, version, S stops, P patterns, R routes, X walking transfers,
//                          Z pattern stops, T stop times, service date, valid until (YYYYMMDD),
//                          route name bytes, Q school points
//   dLat, dLon  Int32[S]   1e-6 deg, delta-coded
//   stopChain   Int32[S]   foot graph chain the stop snaps to, or -1
//   stopFrac    Uint16[S]  position along that chain, 0..65535
//   stopOff     Uint16[S]  deciseconds walked from the chain to the stop
//   nTransfers  Uint8[S]   walking transfers from each stop (on the foot graph, up to 8 min)
//   tTo, tSec   Uint16[X]  their target stop and seconds
//   routeType   Uint8[R]   GTFS route_type
//   names       Uint8[]    route short names, UTF-8, one per line
//   patRoute, patNStops, patNTrips  Uint16[P]   a pattern is one stop sequence of one route
//   patStops    Uint16[Z]
//   times       Uint16[T]  per pattern, trip by trip (sorted, never overtaking): first stop as a
//                          step from the previous trip's first stop, then steps along the trip
//   qLat, qLon  Int32[Q]   school buildings, 1e-6 deg, delta-coded
//   qChain      Int32[Q]   foot graph chain each building snaps to, or -1
//   qFrac, qOff Uint16[Q]  position along it, deciseconds from it (as for stops)
//   qCount      Uint8[Q]   stops within EGRESS_LIMIT on foot of each
//   qStop, qSec Uint16[]   those stops and the seconds to walk from them to the building
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { decodeFootGraph, snapToFoot, walkFrom } from '../../frontend/src/routing/footGraph.js'
import { decodeTimetable, walkToStops, EGRESS_LIMIT } from '../../frontend/src/routing/transit.js'

const MAGIC = 0x54544231
const VERSION = 1
const WINDOWS = [[6 * 3600, 9 * 3600], [11 * 3600 + 30 * 60, 14 * 3600]]
const TRANSFER_SEC = 8 * 60
const MAX_SNAP_M = 300

function parseCsv(path) {
  const text = readFileSync(path, 'utf8').replace(/^﻿/, '')
  const rows = []
  let row = [], field = '', q = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (q) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++ } else q = false
      } else field += c
    } else if (c === '"') q = true
    else if (c === ',') { row.push(field); field = '' }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++
      row.push(field); field = ''
      if (row.length > 1 || row[0] !== '') rows.push(row)
      row = []
    } else field += c
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row) }
  const [head, ...body] = rows
  return body.map(r => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ''])))
}

const toSec = t => {
  const [h, m, s] = t.split(':').map(Number)
  return h * 3600 + m * 60 + s
}
const ymd = d => d.toISOString().slice(0, 10).replace(/-/g, '')

function pickDate(services, explicit, today) {
  if (explicit) return explicit
  const day = new Date(today)
  day.setUTCDate(day.getUTCDate() + 1)
  const counts = []
  for (let i = 0; i < 42; i++, day.setUTCDate(day.getUTCDate() + 1)) {
    const d = ymd(day)
    counts.push([d, day.getUTCDay(), services.get(d)?.trips ?? 0])
  }
  const normal = Math.max(...counts.filter(([, wd]) => wd >= 1 && wd <= 5).map(c => c[2]))
  const pick = counts.find(([, wd, n]) => wd >= 2 && wd <= 4 && n >= normal * 0.98)
  if (!pick) throw new Error('No ordinary Tuesday to Thursday in the next six weeks')
  return pick[0]
}

/**
 * Pack the timetable. fg: decoded foot graph; schools: the site's /schools list or null.
 * Returns { file: Buffer, summary }.
 */
export function buildTimetable(dir, fg, { date: dateArg = null, schools = null, today = new Date() } = {}) {
  const tripsCsv = parseCsv(join(dir, 'trips.txt'))
  const byService = new Map()
  for (const t of tripsCsv) byService.set(t.service_id, (byService.get(t.service_id) || 0) + 1)
  const services = new Map() // date -> { ids, trips }
  for (const r of parseCsv(join(dir, 'calendar_dates.txt'))) {
    if (r.exception_type !== '1') continue
    if (!services.has(r.date)) services.set(r.date, { ids: new Set(), trips: 0 })
    const s = services.get(r.date)
    s.ids.add(r.service_id)
    s.trips += byService.get(r.service_id) || 0
  }
  const date = pickDate(services, dateArg, today)
  const active = services.get(date)?.ids
  if (!active) throw new Error(`No service on ${date}`)
  const feedInfo = parseCsv(join(dir, 'feed_info.txt'))[0] || {}
  const validUntil = feedInfo.feed_end_date || date

  const tripRoute = new Map()
  for (const t of tripsCsv) if (active.has(t.service_id)) tripRoute.set(t.trip_id, t.route_id)
  const tripStops = new Map()
  for (const r of parseCsv(join(dir, 'stop_times.txt'))) {
    if (!tripRoute.has(r.trip_id)) continue
    if (!tripStops.has(r.trip_id)) tripStops.set(r.trip_id, [])
    tripStops.get(r.trip_id).push([Number(r.stop_sequence), r.stop_id, toSec(r.departure_time || r.arrival_time)])
  }
  const inWindow = t => WINDOWS.some(([a, b]) => t >= a && t <= b)
  const kept = []
  for (const [id, st] of tripStops) {
    st.sort((a, b) => a[0] - b[0])
    if (!st.some(s => inWindow(s[2]))) continue
    if (st[st.length - 1][2] > 65535) throw new Error(`Trip ${id} runs past 18:12`)
    kept.push({ id, route: tripRoute.get(id), stops: st.map(s => s[1]), times: st.map(s => s[2]) })
  }

  // Patterns: one stop sequence of one route; trips that would overtake go to another lane.
  const groups = new Map()
  for (const t of kept) {
    const key = `${t.route}|${t.stops.join(',')}`
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(t)
  }
  const patterns = []
  for (const trips of groups.values()) {
    trips.sort((a, b) => a.times[0] - b.times[0])
    const lanes = []
    for (const t of trips) {
      const lane = lanes.find(l => l[l.length - 1].times.every((x, i) => x <= t.times[i]))
      if (lane) lane.push(t)
      else lanes.push([t])
    }
    for (const lane of lanes) patterns.push(lane)
  }

  const stopsCsv = new Map(parseCsv(join(dir, 'stops.txt')).map(s => [s.stop_id, s]))
  const usedStops = [...new Set(kept.flatMap(t => t.stops))]
  usedStops.sort((a, b) => Number(stopsCsv.get(a).stop_lat) - Number(stopsCsv.get(b).stop_lat))
  const sid = new Map(usedStops.map((s, i) => [s, i]))
  const S = usedStops.length
  if (S > 65535) throw new Error('Too many stops for Uint16')
  const routesCsv = new Map(parseCsv(join(dir, 'routes.txt')).map(r => [r.route_id, r]))
  const usedRoutes = [...new Set(patterns.map(p => p[0].route))]
  const rid = new Map(usedRoutes.map((r, i) => [r, i]))

  // Snap stops to the foot graph, then walk between them.
  const lat = usedStops.map(s => Number(stopsCsv.get(s).stop_lat))
  const lon = usedStops.map(s => Number(stopsCsv.get(s).stop_lon))
  const snaps = usedStops.map((_, i) => {
    const sn = snapToFoot(fg, lon[i], lat[i])
    return sn && sn.metres <= MAX_SNAP_M ? sn : null
  })
  const unsnapped = snaps.filter(s => !s).length
  const transfers = []
  const CELL = 0.01
  const grid = new Map()
  for (let i = 0; i < S; i++) {
    const k = `${Math.floor(lon[i] / CELL)},${Math.floor(lat[i] / CELL)}`
    if (!grid.has(k)) grid.set(k, [])
    grid.get(k).push(i)
  }
  for (let i = 0; i < S; i++) {
    const list = []
    if (snaps[i]) {
      const walk = walkFrom(fg, snaps[i], TRANSFER_SEC * 10)
      const cx = Math.floor(lon[i] / CELL), cy = Math.floor(lat[i] / CELL)
      for (let y = cy - 1; y <= cy + 1; y++) {
        for (let x = cx - 1; x <= cx + 1; x++) {
          for (const j of grid.get(`${x},${y}`) || []) {
            if (j === i || !snaps[j]) continue
            const ds = walk.costTo(snaps[j])
            if (ds < Infinity) list.push([j, Math.round(ds / 10)])
          }
        }
      }
    }
    list.sort((a, b) => a[1] - b[1])
    transfers.push(list.slice(0, 255))
  }
  // The feed's own minimum transfer times win where they are longer (changes inside metro
  // stations, where the foot graph only sees the street above).
  let longer = 0
  for (const r of existsSync(join(dir, 'transfers.txt')) ? parseCsv(join(dir, 'transfers.txt')) : []) {
    const a = sid.get(r.from_stop_id), b = sid.get(r.to_stop_id)
    if (a === undefined || b === undefined || r.transfer_type !== '2' || r.from_trip_id || r.from_route_id) continue
    const x = transfers[a].find(t => t[0] === b)
    if (x && Number(r.min_transfer_time) > x[1]) {
      x[1] = Number(r.min_transfer_time)
      longer++
    }
  }

  const P = patterns.length
  const patStops = patterns.flatMap(p => p[0].stops.map(s => sid.get(s)))
  const times = []
  for (const p of patterns) {
    let prevFirst = 0
    for (const t of p) {
      times.push(t.times[0] - prevFirst)
      prevFirst = t.times[0]
      for (let k = 1; k < t.times.length; k++) times.push(t.times[k] - t.times[k - 1])
    }
  }
  if (times.some(x => x < 0 || x > 65535)) throw new Error('Time step out of range')
  const names = new TextEncoder().encode(usedRoutes.map(r => routesCsv.get(r).route_short_name).join('\n'))
  const delta = a => a.map((v, i) => v - (i ? a[i - 1] : 0))
  const flat = transfers.flat()
  const parts = [
    new Int32Array(delta(lat.map(v => Math.round(v * 1e6)))),
    new Int32Array(delta(lon.map(v => Math.round(v * 1e6)))),
    new Int32Array(snaps.map(s => (s ? s.chain : -1))),
    new Uint16Array(snaps.map(s => (s ? Math.round(s.frac * 65535) : 0))),
    new Uint16Array(snaps.map(s => (s ? Math.min(65535, Math.round(s.offDs)) : 0))),
    new Uint8Array(transfers.map(l => l.length)),
    new Uint16Array(flat.map(x => x[0])),
    new Uint16Array(flat.map(x => x[1])),
    new Uint8Array(usedRoutes.map(r => Number(routesCsv.get(r).route_type) % 256)),
    names,
    new Uint16Array(patterns.map(p => rid.get(p[0].route))),
    new Uint16Array(patterns.map(p => p[0].stops.length)),
    new Uint16Array(patterns.map(p => p.length)),
    new Uint16Array(patStops),
    new Uint16Array(times),
  ]
  const header = new Uint32Array([MAGIC, VERSION, S, P, usedRoutes.length, flat.length, patStops.length,
    times.length, Number(date), Number(validUntil), names.length, 0])
  const pack = list => {
    const chunks = []
    for (const p of list) {
      const b = Buffer.from(p.buffer, p.byteOffset, p.byteLength)
      chunks.push(b, Buffer.alloc((4 - (b.length % 4)) % 4))
    }
    return chunks
  }
  let file = Buffer.concat([Buffer.from(header.buffer), ...pack(parts)])

  // Walks from school buildings, on the timetable just packed (the browser's own code).
  const points = []
  if (schools) {
    const seen = new Set()
    for (const school of schools) {
      for (const l of school.locations || []) {
        const lat = Number(l.lat), lng = Number(l.lng)
        if (!l.lat || !l.lng || !Number.isFinite(lat) || !Number.isFinite(lng)) continue
        const key = `${Math.round(lat * 1e6)},${Math.round(lng * 1e6)}`
        if (seen.has(key)) continue
        seen.add(key)
        points.push({ lat: Math.round(lat * 1e6) / 1e6, lng: Math.round(lng * 1e6) / 1e6 })
      }
    }
    const tt = decodeTimetable(file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength))
    for (const pt of points) {
      const { snap, stops } = walkToStops(tt, fg, pt, EGRESS_LIMIT)
      pt.snap = snap && snap.metres <= MAX_SNAP_M ? snap : null
      pt.stops = stops.slice(0, 255)
    }
    header[11] = points.length
    const eg = points.flatMap(pt => pt.stops)
    file = Buffer.concat([Buffer.from(header.buffer), ...pack(parts), ...pack([
      new Int32Array(delta(points.map(pt => Math.round(pt.lat * 1e6)))),
      new Int32Array(delta(points.map(pt => Math.round(pt.lng * 1e6)))),
      new Int32Array(points.map(pt => (pt.snap ? pt.snap.chain : -1))),
      new Uint16Array(points.map(pt => (pt.snap ? Math.round(pt.snap.frac * 65535) : 0))),
      new Uint16Array(points.map(pt => (pt.snap ? Math.min(65535, Math.round(pt.snap.offDs)) : 0))),
      new Uint8Array(points.map(pt => pt.stops.length)),
      new Uint16Array(eg.map(x => x[0])),
      new Uint16Array(eg.map(x => x[1])),
    ])])
  }
  const summary = `date ${date} (valid until ${validUntil}): trips ${kept.length} patterns ${P} stops ${S} ` +
    `(not near a footpath: ${unsnapped}) routes ${usedRoutes.length} transfers ${flat.length} (${longer} longer in the feed) stop times ${times.length} ` +
    `school buildings ${points.length} (no stop within a walk: ${points.filter(pt => !pt.stops.length).length})`
  return { file, summary }
}

function main() {
  const [dir, footPath, out, ...rest] = process.argv.slice(2)
  const opt = name => (rest.includes(name) ? rest[rest.indexOf(name) + 1] : null)
  const fbuf = readFileSync(footPath)
  const fg = decodeFootGraph(fbuf.buffer.slice(fbuf.byteOffset, fbuf.byteOffset + fbuf.byteLength))
  const schools = opt('--schools') ? JSON.parse(readFileSync(opt('--schools'), 'utf8')) : null
  const { file, summary } = buildTimetable(dir, fg, { date: opt('--date'), schools })
  writeFileSync(out, file)
  console.error(summary)
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) main()
