// Compare browser-router times with OSRM car times for the same pairs.
import { readFileSync } from 'fs';
import { decode, snap, search, costTo } from './router.js';
const [graphPath, pairsPath, osrmPath] = process.argv.slice(2);
const b = readFileSync(graphPath);
const g = decode(b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength));
const pairs = JSON.parse(readFileSync(pairsPath)), osrm = JSON.parse(readFileSync(osrmPath));
const dests = pairs.dests.map(([x, y]) => snap(g, x, y));
const ratios = [], absErr = [], rhos = [], top5 = [], big = [];
const rank = a => { const idx = a.map((v, i) => [v, i]).sort((x, y) => x[0] - y[0]); const r = new Array(a.length); idx.forEach(([, i], k) => r[i] = k); return r; };
pairs.sources.forEach((s, si) => {
  const r = search(g, snap(g, s[0], s[1]));
  const mine = dests.map(d => costTo(g, r, d)), theirs = osrm.tables[si].durations;
  const ok = mine.map((m, i) => isFinite(m) && theirs[i] != null && theirs[i] > 60);
  const m = mine.filter((_, i) => ok[i]), o = theirs.filter((_, i) => ok[i]);
  m.forEach((v, i) => {
    ratios.push(v / o[i]); absErr.push(Math.abs(v - o[i]));
    if (Math.abs(v / o[i] - 1) > 0.3) big.push({ si, from: s, to: pairs.dests[mine.indexOf(v)], mine: Math.round(v), osrm: Math.round(o[i]) });
  });
  const rm = rank(m), ro = rank(o), n = m.length;
  rhos.push(1 - 6 * rm.reduce((acc, x, i) => acc + (x - ro[i]) ** 2, 0) / (n * (n * n - 1)));
  const t5m = new Set(rm.map((x, i) => [x, i]).filter(([x]) => x < 5).map(([, i]) => i));
  top5.push(ro.filter((x, i) => x < 5 && t5m.has(i)).length);
});
const q = (a, p) => { const s = [...a].sort((x, y) => x - y); return s[Math.floor((s.length - 1) * p)]; };
console.log('pairs', ratios.length);
console.log('ratio mine/osrm  p10 %s  median %s  p90 %s', ...[0.1, 0.5, 0.9].map(p => q(ratios, p).toFixed(3)));
console.log('abs error s      median %s  p90 %s', q(absErr, 0.5).toFixed(0), q(absErr, 0.9).toFixed(0));
console.log('within 10%%: %s%%  within 20%%: %s%%', (100 * ratios.filter(r => Math.abs(r - 1) <= 0.1).length / ratios.length).toFixed(1), (100 * ratios.filter(r => Math.abs(r - 1) <= 0.2).length / ratios.length).toFixed(1));
console.log('Spearman per source  min %s  median %s', Math.min(...rhos).toFixed(3), q(rhos, 0.5).toFixed(3));
console.log('top-5 overlap per source  median %s/5  min %s/5', q(top5, 0.5), Math.min(...top5));
console.log('off by >30%%:', big.length); console.log(JSON.stringify(big.slice(0, 8)));
