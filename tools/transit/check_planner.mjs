// Check the packed timetable against the official journey planner (sofiatraffic.bg), the way a
// parent would use it: for sample homes and schools, take the time we say to leave home to
// arrive by 08:00, ask the planner for the earliest arrival when leaving then, and count how
// often it is in time. Usage:
//   node check_planner.mjs <foot graph .bin> <timetable .bin> <schools.json> <report.json>
// Exits 1 when too few trips arrive in time, 2 when the planner could not be asked (the
// workflow treats that as a warning: the planner is someone else's site).
import { readFileSync, writeFileSync } from 'node:fs'
import { decodeFootGraph, walkFrom } from '../../frontend/src/routing/footGraph.js'
import { decodeTimetable, walkToStops, schoolWalks, arriveBy, ACCESS_LIMIT, WALK_ONLY_LIMIT } from '../../frontend/src/routing/transit.js'

const PLANNER = 'https://www.sofiatraffic.bg/bg/trip/trip'
const PAIRS = 30
const DEADLINE = 8 * 3600
const LATE_OK = 2 * 60 // a parent's margin
const MIN_IN_TIME = 0.8 // share of trips the planner confirms within LATE_OK
const MIN_FOUND = 0.9 // share of pairs where we find any trip, of those the planner can do

const buf = path => {
  const b = readFileSync(path)
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength)
}
const hm = s => `${String(Math.floor(s / 3600)).padStart(2, '0')}:${String(Math.floor(s / 60) % 60).padStart(2, '0')}`

// Seeded, so a month's run can be repeated: homes up to ~1.5 km from a random school building,
// each paired with another school 1.5-12 km away.
function samplePairs(points) {
  let seed = 20261027
  const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648
  const pairs = []
  while (pairs.length < PAIRS) {
    const a = points[Math.floor(rnd() * points.length)]
    const home = { lat: +(a.lat + (rnd() - 0.5) * 0.026).toFixed(5), lng: +(a.lng + (rnd() - 0.5) * 0.036).toFixed(5) }
    const b = points[Math.floor(rnd() * points.length)]
    const km = Math.hypot((b.lat - home.lat) * 111, (b.lng - home.lng) * 82)
    if (km >= 1.5 && km <= 12) pairs.push({ home, school: b, km: +km.toFixed(1) })
  }
  return pairs
}

async function plannerSession() {
  const res = await fetch(PLANNER, { headers: { 'User-Agent': 'schooldecider.com monthly timetable check' } })
  const html = await res.text()
  const token = html.match(/name="csrf-token" content="([^"]+)"/)?.[1]
  const cookies = res.headers.getSetCookie().map(c => c.split(';')[0])
  const xsrf = cookies.find(c => c.startsWith('XSRF-TOKEN='))?.slice(11)
  if (!token || !xsrf) throw new Error('planner page changed: no CSRF token')
  return { token, xsrf: decodeURIComponent(xsrf), cookie: cookies.join('; ') }
}

async function plannerEarliest(session, date, pair, leave) {
  const body = {
    type: '0', arriveBy: false, locale: 'bg', time: hm(leave),
    date: `${date.slice(0, 4)}-${date.slice(4, 6)}-${date.slice(6, 8)}`,
    from: { latitude: pair.home.lat, longitude: pair.home.lng, name: 'home' },
    to: { latitude: pair.school.lat, longitude: pair.school.lng, name: 'school' },
  }
  const res = await fetch(PLANNER, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json', Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest',
      'X-CSRF-TOKEN': session.token, 'X-XSRF-TOKEN': session.xsrf, Cookie: session.cookie,
      'User-Agent': 'schooldecider.com monthly timetable check',
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`planner HTTP ${res.status}`)
  const its = (await res.json())?.data?.plan?.itineraries
  if (!Array.isArray(its)) throw new Error('planner answer changed: no itineraries')
  if (its.length === 0) return null
  // Seconds after local midnight; the service date is after the clocks change in October or
  // before they change in March only in a few weeks a year, so take the offset per answer.
  const sofia = ms => {
    const parts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Sofia', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
      .formatToParts(new Date(ms))
    const get = t => Number(parts.find(p => p.type === t).value)
    return get('hour') * 3600 + get('minute') * 60 + get('second')
  }
  const best = its.reduce((a, b) => (b.endTime < a.endTime ? b : a))
  return { leave: sofia(best.startTime), arrive: sofia(best.endTime), routes: best.legs.filter(l => l.mode !== 'WALK').map(l => l.route?.shortName) }
}

async function main() {
  const [footPath, ttPath, schoolsPath, reportPath] = process.argv.slice(2)
  const fg = decodeFootGraph(buf(footPath))
  const tt = decodeTimetable(buf(ttPath))
  const date = String(tt.serviceDate)
  const points = JSON.parse(readFileSync(schoolsPath, 'utf8')).flatMap(s => (s.locations || [])
    .filter(l => l.lat && l.lng).map(l => ({ id: l.id, lat: Number(l.lat), lng: Number(l.lng) })))
  const pairs = samplePairs(points)

  const rows = pairs.map(pair => {
    const { snap, stops } = walkToStops(tt, fg, pair.home, ACCESS_LIMIT)
    if (!snap) return { pair, ours: null }
    const school = schoolWalks(tt, fg, pair.school)
    const walk = walkFrom(fg, snap, WALK_ONLY_LIMIT * 10)
    const target = { id: 0, egress: school.stops, deadline: DEADLINE, walkSec: school.snap ? walk.costTo(school.snap) / 10 : Infinity }
    return { pair, ours: arriveBy(tt, stops, [target]).get(0) ?? null }
  })

  let session
  try {
    session = await plannerSession()
    for (const row of rows) {
      // Leaving at our time, or 07:00 when we found nothing (does the planner find a trip?).
      row.planner = await plannerEarliest(session, date, row.pair, row.ours ? row.ours.leave : 7 * 3600)
      await new Promise(r => setTimeout(r, 1500))
    }
  } catch (e) {
    console.error(`Could not ask the planner: ${e.message}`)
    writeFileSync(reportPath, JSON.stringify({ date, error: e.message }, null, 1))
    process.exit(2)
  }

  const ours = rows.filter(r => r.ours && r.ours.rides > 0)
  const confirmed = ours.filter(r => r.planner && r.planner.arrive <= DEADLINE + LATE_OK)
  const plannerCan = rows.filter(r => r.planner && r.planner.arrive <= DEADLINE)
  const missed = plannerCan.filter(r => !r.ours)
  const inTime = ours.length ? confirmed.length / ours.length : 0
  const found = plannerCan.length ? 1 - missed.length / plannerCan.length : 1
  for (const r of rows) {
    const o = r.ours, p = r.planner
    console.log(`${String(r.pair.km).padStart(5)} km  ours ${o ? `${hm(o.leave)}->${hm(o.arrive)} ${o.rides} rides` : 'none'}` +
      `  planner leaving then ${p ? `${hm(p.leave)}->${hm(p.arrive)} ${p.routes.join('+')}` : 'none'}`)
  }
  const summary = `Timetable of ${date}: ${confirmed.length} of ${ours.length} public transport trips confirmed by the official planner ` +
    `(arrive by ${hm(DEADLINE + LATE_OK)} when leaving at our time); we find a trip for ${plannerCan.length - missed.length} of ${plannerCan.length} pairs the planner can do.`
  console.log(summary)
  writeFileSync(reportPath, JSON.stringify({ date, inTime, found, summary, rows }, null, 1))
  if (inTime < MIN_IN_TIME || found < MIN_FOUND) {
    console.error(`Below the bar: in time ${inTime.toFixed(2)} (need ${MIN_IN_TIME}), found ${found.toFixed(2)} (need ${MIN_FOUND})`)
    process.exit(1)
  }
}

main()
