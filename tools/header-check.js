// Measures the real SSE bytes over HTTP and screenshots the detail popup header.
// Usage: node tools/header-check.js [baseUrl] [playwrightModulePath]
const path = require('path');
const http = require('http');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const { chromium } = require(process.argv[3] || 'playwright');
const out = path.join(__dirname, '..', 'screenshots');

function sseBytes(encoding, ms) {
  return new Promise((resolve, reject) => {
    const req = http.get(base + '/api/events', { headers: { 'Accept-Encoding': encoding } }, (res) => {
      let bytes = 0;
      res.on('data', (c) => { bytes += c.length; });
      setTimeout(() => { req.destroy(); resolve({ encoding: res.headers['content-encoding'] || 'none', bytes }); }, ms);
    });
    req.on('error', (e) => { if (e.code !== 'ECONNRESET') reject(e); });
  });
}

(async () => {
  const [plain, gz] = await Promise.all([sseBytes('identity', 10500), sseBytes('gzip', 10500)]);
  const b = await chromium.launch();
  const report = { sse10s: { plain, gzip: gz, saving: Math.round((1 - gz.bytes / plain.bytes) * 100) + '%' }, shots: [] };
  for (const [theme, w, h] of [['light', 1440, 900], ['dark', 1440, 900], ['light', 390, 844]]) {
    const p = await b.newPage({ viewport: { width: w, height: h } });
    const errors = [];
    p.on('pageerror', (e) => errors.push(String(e)));
    p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    await p.addInitScript((t) => { try { localStorage.setItem('of-theme', t); } catch (e) { /* ignore */ } }, theme);
    await p.goto(base);
    await p.waitForFunction(() => document.querySelectorAll('#bullCards .card:not(.empty)').length === 6, null, { timeout: 120000 });
    await p.waitForTimeout(1500); // let the SSE stream deliver a snapshot (proves gzip decoding in the browser)
    await p.click('#bearCards .card:not(.empty)');
    await p.waitForFunction(() => /^\d+ \/ 90 CANDLES/.test(document.getElementById('dStatus').textContent), null, { timeout: 30000 });
    await p.waitForTimeout(700);
    const info = await p.evaluate(() => {
      const r = document.getElementById('dSummary').getBoundingClientRect();
      const clipped = ['dSymbol', 'dLtp', 'dMom', 'dOb'].filter((id) => { const e = document.getElementById(id); return e.scrollWidth > e.clientWidth + 1; });
      return { side: document.getElementById('dSummary').dataset.side, mom: document.getElementById('dMomCard').dataset.s,
               ob: document.getElementById('dObCard').dataset.s, meter: getComputedStyle(document.querySelector('#dObCard .d-meter i')).width,
               headerH: Math.round(r.height), clipped, overflowX: document.documentElement.scrollWidth > window.innerWidth,
               sseLive: !!window.OFApp && !!window.OFApp.state.snap };
    });
    const top = await p.locator('#detail').boundingBox();
    await p.screenshot({ path: path.join(out, `header-${theme}-${w}.png`), clip: { x: top.x, y: top.y, width: top.width, height: Math.min(w < 600 ? 520 : 260, top.height) } });
    report.shots.push({ theme, size: `${w}x${h}`, ...info, errors });
    await p.close();
  }
  console.log(JSON.stringify(report));
  await b.close();
})().catch((e) => { console.error(String(e).split('\n')[0]); process.exit(1); });
