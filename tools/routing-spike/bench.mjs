// Run bench.html in Chromium at several CPU throttling rates; print medians.
import { chromium } from 'playwright';
import { readFileSync } from 'fs';
const [url, pairsPath, ...rates] = process.argv.slice(2);
const pairs = JSON.parse(readFileSync(pairsPath));
const med = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
const p90 = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length * 0.9)]; };
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium', args: ['--enable-blink-features=ForceEagerMeasureMemory'] });
const out = {};
for (const rate of rates.map(Number)) {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send('Emulation.setCPUThrottlingRate', { rate });
  await page.goto(url);
  const r = await page.evaluate(p => window.bench(p), pairs);
  const runs = r.runs.slice(1); // first search warms the JIT
  out[rate] = {
    ...Object.fromEntries(Object.entries(r.load).map(([k, v]) => [k, Math.round(v)])),
    firstSearchMs: Math.round(r.runs[0].searchMs),
    searchMedMs: Math.round(med(runs.map(x => x.searchMs))), searchP90Ms: Math.round(p90(runs.map(x => x.searchMs))),
    snapMedMs: Math.round(med(runs.map(x => x.snapMs))),
    searchBytes: r.runs[0].searchBytes,
    memoryMB: r.mem && r.mem.bytes ? +(r.mem.bytes / 1e6).toFixed(1) : r.mem,
  };
  console.log(rate + 'x', JSON.stringify(out[rate]));
  await ctx.close();
}
await browser.close();
