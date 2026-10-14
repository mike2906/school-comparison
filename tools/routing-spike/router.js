// Routing spike: decode the compact Sofia graph and run one edge-based Dijkstra from a point.
// Plain ES module so it runs in a Web Worker and in Node for testing.
// Labels are "cost when entering edge e" (deciseconds), so turn penalties and restrictions see
// the incoming edge. OSRM car profile: turn_penalty 7.5 s, turn_bias 1.075, u-turn 20 s and
// only at dead ends, penalties only where more than two roads meet.

const TURN_PENALTY = 75, U_TURN = 200, BIAS = 1.075;

export function decode(buf) {
  const h = new Uint32Array(buf, 0, 8);
  if (h[0] !== 0x53524731) throw new Error('bad graph');
  const [, , N, E, C, G, R, flags] = h;
  let off = 32;
  const take = (T, n) => {
    const a = new T(buf, off, n);
    off += a.byteLength; off += (4 - (off % 4)) % 4;
    return a;
  };
  const dlat = take(Int32Array, N), dlon = take(Int32Array, N), deg = take(Uint32Array, N + 1);
  const tgtD = take(Int32Array, E), w = take(Uint16Array, E);
  const peakRaw = flags & 1 ? take(Uint16Array, E) : null;
  const nRoads = take(Uint8Array, N), bIn = take(Uint8Array, E), bOut = take(Uint8Array, E);
  const chainFwd = take(Int32Array, C), chainBwd = take(Int32Array, C), nPts = take(Uint16Array, C);
  const geomD = take(Int16Array, 2 * G), rFrom = take(Uint32Array, R), rTo = take(Uint32Array, R);

  const lat = new Int32Array(N), lon = new Int32Array(N);
  let a = 0, b = 0;
  for (let i = 0; i < N; i++) { a += dlat[i]; b += dlon[i]; lat[i] = a; lon[i] = b; }
  const firstOut = new Uint32Array(N + 1);
  for (let i = 1; i <= N; i++) firstOut[i] = firstOut[i - 1] + deg[i];
  const src = new Uint32Array(E), tgt = new Uint32Array(E);
  for (let v = 0; v < N; v++) for (let e = firstOut[v]; e < firstOut[v + 1]; e++) { src[e] = v; tgt[e] = v + tgtD[e]; }
  // Absolute chain geometry (1e-5 deg) including both end nodes, for snapping.
  const geomStart = new Uint32Array(C + 1);
  for (let c = 0; c < C; c++) geomStart[c + 1] = geomStart[c] + nPts[c] + 2;
  const gx = new Int32Array(geomStart[C]), gy = new Int32Array(geomStart[C]);
  let gi = 0;
  for (let c = 0; c < C; c++) {
    const f = chainFwd[c], s = f >= 0 ? src[f] : tgt[chainBwd[c]], t = f >= 0 ? tgt[f] : src[chainBwd[c]];
    let x = Math.round(lon[s] / 10), y = Math.round(lat[s] / 10), k = geomStart[c];
    gx[k] = x; gy[k] = y; k++;
    for (let j = 0; j < nPts[c]; j++, gi++) { x += geomD[2 * gi]; y += geomD[2 * gi + 1]; gx[k] = x; gy[k] = y; k++; }
    gx[k] = Math.round(lon[t] / 10); gy[k] = Math.round(lat[t] / 10);
  }
  const restricted = new Uint8Array(E), banned = new Set();
  for (let i = 0; i < R; i++) { restricted[rFrom[i]] = 1; banned.add(rFrom[i] * E + rTo[i]); }
  // Turn penalty by bearing difference byte (OSRM sigmoid; positive angle = right turn).
  const turnTab = new Float64Array(256);
  for (let d = 0; d < 256; d++) {
    const ang = (((d + 128) & 255) - 128) * 360 / 256;
    turnTab[d] = ang >= 0
      ? TURN_PENALTY / (1 + Math.exp(-((13 / BIAS) * ang / 180 - 6.5 * BIAS)))
      : TURN_PENALTY / (1 + Math.exp(-((13 * BIAS) * -ang / 180 - 6.5 / BIAS)));
  }
  const peak = peakRaw;
  const g = { N, E, C, lat, lon, firstOut, src, tgt, w, peak, nRoads, bIn, bOut, chainFwd, chainBwd,
    geomStart, gx, gy, restricted, banned, turnTab };
  g.bytes = [lat, lon, firstOut, src, tgt, w, nRoads, bIn, bOut, chainFwd, chainBwd, geomStart, gx, gy,
    restricted].reduce((s, x) => s + x.byteLength, 0) + (peak ? peak.byteLength : 0) + buf.byteLength;
  return g;
}

// Nearest point on any snappable chain. Returns chain, fraction along it (by length) and metres.
export function snap(g, lonDeg, latDeg) {
  const kx = Math.cos(latDeg * Math.PI / 180), px = lonDeg * 1e5 * kx, py = latDeg * 1e5;
  let best = Infinity, bc = -1, bseg = 0, bt = 0;
  const { C, geomStart, gx, gy } = g;
  for (let c = 0; c < C; c++) {
    for (let k = geomStart[c]; k < geomStart[c + 1] - 1; k++) {
      const ax = gx[k] * kx, ay = gy[k], bx = gx[k + 1] * kx, by = gy[k + 1];
      const dx = bx - ax, dy = by - ay, L = dx * dx + dy * dy;
      let t = L > 0 ? ((px - ax) * dx + (py - ay) * dy) / L : 0;
      t = t < 0 ? 0 : t > 1 ? 1 : t;
      const ex = ax + t * dx - px, ey = ay + t * dy - py, d = ex * ex + ey * ey;
      if (d < best) { best = d; bc = c; bseg = k; bt = t; }
    }
  }
  // Fraction along the chain by length.
  let total = 0, before = 0;
  for (let k = geomStart[bc]; k < geomStart[bc + 1] - 1; k++) {
    const l = Math.hypot((gx[k + 1] - gx[k]) * kx, gy[k + 1] - gy[k]);
    if (k < bseg) before += l; else if (k === bseg) before += l * bt;
    total += l;
  }
  return { chain: bc, frac: total > 0 ? before / total : 0, metres: Math.sqrt(best) * 1.11 };
}

function turnCost(g, f, e) {
  const v = g.tgt[f];
  const uturn = g.tgt[e] === g.src[f];
  if (uturn && g.firstOut[v + 1] - g.firstOut[v] > 1) return -1; // u-turn only at dead ends
  if (g.restricted[f] && g.banned.has(f * g.E + e)) return -1;
  let c = 0;
  if (g.nRoads[v] > 2 || uturn) c = g.turnTab[(g.bIn[e] - g.bOut[f]) & 255];
  if (uturn) c += U_TURN;
  return c;
}

// One search from a snapped point. Returns per-edge entry cost (deciseconds) in Float64Array.
export function search(g, origin, weights = g.w) {
  const { E, firstOut, tgt } = g;
  const dist = new Float64Array(E).fill(Infinity);
  let cap = 1 << 16, hk = new Float64Array(cap), hv = new Uint32Array(cap), n = 0;
  const push = (k, v) => {
    if (n === cap) {
      cap *= 2;
      const nk = new Float64Array(cap); nk.set(hk); hk = nk;
      const nv = new Uint32Array(cap); nv.set(hv); hv = nv;
    }
    let i = n++;
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (hk[p] <= k) break;
      hk[i] = hk[p]; hv[i] = hv[p]; i = p;
    }
    hk[i] = k; hv[i] = v;
  };
  const pop = () => {
    const v = hv[0], lk = hk[--n], lv = hv[n];
    let i = 0;
    for (;;) {
      let c = 2 * i + 1;
      if (c >= n) break;
      if (c + 1 < n && hk[c + 1] < hk[c]) c++;
      if (hk[c] >= lk) break;
      hk[i] = hk[c]; hv[i] = hv[c]; i = c;
    }
    hk[i] = lk; hv[i] = lv;
    return v;
  };
  const relaxFrom = (f, base) => {
    const v = tgt[f];
    for (let e = firstOut[v]; e < firstOut[v + 1]; e++) {
      const tc = turnCost(g, f, e);
      if (tc < 0) continue;
      const nd = base + tc;
      if (nd < dist[e]) { dist[e] = nd; push(nd, e); }
    }
  };
  // Virtual origin: drive the rest of each directed edge of the snapped chain.
  const fwd = g.chainFwd[origin.chain], bwd = g.chainBwd[origin.chain];
  if (fwd >= 0) relaxFrom(fwd, (1 - origin.frac) * weights[fwd]);
  if (bwd >= 0) relaxFrom(bwd, origin.frac * weights[bwd]);
  let settled = 0;
  while (n > 0) {
    const d = hk[0], f = pop();
    if (d > dist[f]) continue;
    settled++;
    relaxFrom(f, d + weights[f]);
  }
  return { dist, settled, origin, weights };
}

// Seconds to reach a snapped destination, or Infinity.
export function costTo(g, res, dest) {
  const { dist, origin, weights } = res;
  let best = Infinity;
  const f = g.chainFwd[dest.chain], b = g.chainBwd[dest.chain];
  if (f >= 0) best = Math.min(best, dist[f] + dest.frac * weights[f]);
  if (b >= 0) best = Math.min(best, dist[b] + (1 - dest.frac) * weights[b]);
  if (dest.chain === origin.chain) { // same piece of road, ahead of us
    if (f >= 0 && dest.frac >= origin.frac) best = Math.min(best, (dest.frac - origin.frac) * weights[f]);
    if (b >= 0 && dest.frac <= origin.frac) best = Math.min(best, (origin.frac - dest.frac) * weights[b]);
  }
  return best / 10;
}
