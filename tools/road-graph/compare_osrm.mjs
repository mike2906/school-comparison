// Compare browser drive times with OSRM's for the same sample pairs; exit 1 when they drift.
// Usage: node compare_osrm.mjs graph pairs.json osrm.json [report.json]
import { readFileSync, writeFileSync } from 'fs'
import { decodeGraph, snapToRoad, searchFrom, secondsTo } from '../../frontend/src/routing/roadGraph.js'

const LIMITS = { medianRatioMin: 0.95, medianRatioMax: 1.05, within20Min: 0.95, spearmanMedianMin: 0.98 }

const [graphPath, pairsPath, osrmPath, reportPath] = process.argv.slice(2)
const b = readFileSync(graphPath)
const g = decodeGraph(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength))
const pairs = JSON.parse(readFileSync(pairsPath))
const osrm = JSON.parse(readFileSync(osrmPath))
const dests = pairs.dests.map(([x, y]) => snapToRoad(g, x, y))
const q = (a, p) => [...a].sort((x, y) => x - y)[Math.floor((a.length - 1) * p)]
const rank = a => {
  const r = new Array(a.length)
  a.map((v, i) => [v, i]).sort((x, y) => x[0] - y[0]).forEach(([, i], k) => { r[i] = k })
  return r
}
const ratios = []
const rhos = []
const worst = []
pairs.sources.forEach(([x, y], si) => {
  const res = searchFrom(g, snapToRoad(g, x, y))
  const theirs = osrm.tables[si].durations
  const mine = []
  const ref = []
  dests.forEach((d, i) => {
    const m = d ? secondsTo(g, res, d) : Infinity
    // Trips under a minute are dominated by where each side snaps.
    if (!Number.isFinite(m) || theirs[i] == null || theirs[i] < 60) return
    mine.push(m)
    ref.push(theirs[i])
    ratios.push(m / theirs[i])
    worst.push({ from: [x, y], to: pairs.dests[i], mine: Math.round(m), osrm: Math.round(theirs[i]) })
  })
  if (mine.length < 2) return
  const rm = rank(mine)
  const ro = rank(ref)
  const n = mine.length
  rhos.push(1 - (6 * rm.reduce((s, v, i) => s + (v - ro[i]) ** 2, 0)) / (n * (n * n - 1)))
})
worst.sort((a, b) => Math.abs(Math.log(b.mine / b.osrm)) - Math.abs(Math.log(a.mine / a.osrm)))
const report = {
  pairs: ratios.length,
  ratio: { p10: q(ratios, 0.1), median: q(ratios, 0.5), p90: q(ratios, 0.9) },
  within10: ratios.filter(r => Math.abs(r - 1) <= 0.1).length / ratios.length,
  within20: ratios.filter(r => Math.abs(r - 1) <= 0.2).length / ratios.length,
  spearman: { min: Math.min(...rhos), median: q(rhos, 0.5) },
  limits: LIMITS,
  worst: worst.slice(0, 20),
}
console.log(JSON.stringify({ ...report, worst: report.worst.slice(0, 5) }, null, 1))
if (reportPath) writeFileSync(reportPath, JSON.stringify(report, null, 1))
const ok = report.ratio.median >= LIMITS.medianRatioMin && report.ratio.median <= LIMITS.medianRatioMax &&
  report.within20 >= LIMITS.within20Min && report.spearman.median >= LIMITS.spearmanMedianMin
if (!ok) {
  console.error('Drive times drifted from OSRM beyond the limits')
  process.exit(1)
}
