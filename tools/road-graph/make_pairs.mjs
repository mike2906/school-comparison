// Sample "home" and "school" points for the OSRM check: random road nodes in the urban core,
// shifted 15-60 m off the road. Deterministic, so a rebuild compares like with like.
import { readFileSync, writeFileSync } from 'fs'
import { decodeGraph } from '../../frontend/src/routing/roadGraph.js'

const [graphPath, out] = process.argv.slice(2)
const b = readFileSync(graphPath)
const g = decodeGraph(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength))
let seed = 42
const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648
const pick = () => {
  for (;;) {
    const i = Math.floor(rnd() * g.N)
    const lon = g.lon[i] / 1e6
    const lat = g.lat[i] / 1e6
    if (lon < 23.22 || lon > 23.45 || lat < 42.62 || lat > 42.76) continue
    const r = 15 + rnd() * 45
    const t = rnd() * 2 * Math.PI
    return [+(lon + (r * Math.cos(t)) / 82000).toFixed(6), +(lat + (r * Math.sin(t)) / 111000).toFixed(6)]
  }
}
const sources = Array.from({ length: 40 }, pick)
const dests = Array.from({ length: 300 }, pick)
writeFileSync(out, JSON.stringify({ sources, dests }))
