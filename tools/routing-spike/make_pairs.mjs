// Sample "home" points (random graph node in the urban core, shifted 15-60 m) and "school" points.
import { readFileSync, writeFileSync } from 'fs';
import { decode } from './router.js';
const [graphPath, out] = process.argv.slice(2);
const b = readFileSync(graphPath);
const g = decode(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
let seed = 42;
const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
const pick = () => {
  for (;;) {
    const i = Math.floor(rnd() * g.N), lon = g.lon[i] / 1e6, lat = g.lat[i] / 1e6;
    if (lon < 23.22 || lon > 23.45 || lat < 42.62 || lat > 42.76) continue;
    const r = 15 + rnd() * 45, a = rnd() * 2 * Math.PI;
    return [+(lon + r * Math.cos(a) / 82000).toFixed(6), +(lat + r * Math.sin(a) / 111000).toFixed(6)];
  }
};
const sources = Array.from({ length: 40 }, pick), dests = Array.from({ length: 300 }, pick);
const routes = Array.from({ length: 12 }, (_, i) => [sources[i], dests[i]]);
writeFileSync(out, JSON.stringify({ sources, dests, routes }));
