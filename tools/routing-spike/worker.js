import { decode, snap, search, costTo } from './router.js';
let g = null, dests = null;
self.onmessage = async ({ data }) => {
  const t = {};
  if (data.type === 'load') {
    let t0 = performance.now();
    const res = await fetch(data.url, { cache: 'no-store' });
    const raw = await res.arrayBuffer();
    t.downloadMs = performance.now() - t0;
    t0 = performance.now();
    const ab = await new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
    t.gunzipMs = performance.now() - t0;
    t0 = performance.now();
    g = decode(ab);
    t.decodeMs = performance.now() - t0;
    t0 = performance.now();
    dests = data.dests.map(([x, y]) => snap(g, x, y));
    t.snapAllDestsMs = performance.now() - t0;
    t.graphBytes = g.bytes;
    self.postMessage(t);
  } else if (data.type === 'route') {
    let t0 = performance.now();
    const o = snap(g, data.from[0], data.from[1]);
    t.snapMs = performance.now() - t0;
    t0 = performance.now();
    const r = search(g, o);
    t.searchMs = performance.now() - t0;
    t0 = performance.now();
    t.secs = dests.map(d => costTo(g, r, d));
    t.costMs = performance.now() - t0;
    t.searchBytes = r.dist.byteLength;
    self.postMessage(t);
  }
};
