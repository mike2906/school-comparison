// Sample "home" and "school" points for the OSRM check: random road nodes in the urban core,
// shifted 15-60 m off the road. Deterministic, so a rebuild compares like with like.
import { readFileSync, writeFileSync } from 'fs'

const [graphPath, out] = process.argv.slice(2)
const buf = readFileSync(graphPath)
const h = new Uint32Array(buf.buffer, buf.byteOffset, 8)
const N = h[2]
const dLat = new Int32Array(buf.buffer.slice(buf.byteOffset + 32, buf.byteOffset + 32 + 4 * N))
const dLon = new Int32Array(buf.buffer.slice(buf.byteOffset + 32 + 4 * N, buf.byteOffset + 32 + 8 * N))
const lat = new Float64Array(N)
const lon = new Float64Array(N)
for (let i = 0, a = 0, b = 0; i < N; i++) {
  a += dLat[i]
  b += dLon[i]
  lat[i] = a / 1e6
  lon[i] = b / 1e6
}
let seed = 42
const rnd = () => (seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648
const pick = () => {
  for (;;) {
    const i = Math.floor(rnd() * N)
    if (lon[i] < 23.22 || lon[i] > 23.45 || lat[i] < 42.62 || lat[i] > 42.76) continue
    const r = 15 + rnd() * 45
    const t = rnd() * 2 * Math.PI
    return [+(lon[i] + (r * Math.cos(t)) / 82000).toFixed(6), +(lat[i] + (r * Math.sin(t)) / 111000).toFixed(6)]
  }
}
const sources = Array.from({ length: 40 }, pick)
const dests = Array.from({ length: 300 }, pick)
writeFileSync(out, JSON.stringify({ sources, dests }))
