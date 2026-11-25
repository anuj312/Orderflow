// Measures popup header vs chart height and screenshots the popup top.
// Usage: node tools/header-size.js [baseUrl] [playwrightModulePath] [tag]
const path = require('path');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const { chromium } = require(process.argv[3] || 'playwright');
const tag = process.argv[4] || 'now';
(async () => {
  const b = await chromium.launch();
  const out = [];
  for (const [theme, w, h] of [['light', 1440, 900], ['dark', 1920, 1080], ['light', 1280, 720], ['light', 390, 844]]) {
    const p = await b.newPage({ viewport: { width: w, height: h } });
    const errors = [];
    p.on('pageerror', (e) => errors.push(String(e)));
    p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    await p.addInitScript((t) => { try { localStorage.setItem('of-theme', t); } catch (e) { /* ignore */ } }, theme);
    await p.goto(base);
    await p.waitForFunction(() => document.querySelectorAll('#bullCards .card:not(.empty)').length === 6, null, { timeout: 120000 });
    const sym = process.argv[5];
    if (sym) await p.evaluate((s) => window.OFApp.openDetail(s), sym);
    else await p.click('#bullCards .card:not(.empty)');
    await p.waitForFunction(() => /^\d+ \/ 90 CANDLES/.test(document.getElementById('dStatus').textContent), null, { timeout: 30000 });
    await p.waitForTimeout(700);
    const m = await p.evaluate(() => {
      const H = (id) => Math.round(document.getElementById(id).getBoundingClientRect().height);
      const clipped = ['dSymbol', 'dLtp', 'dMom', 'dOb'].filter((id) => { const e = document.getElementById(id); return e.scrollWidth > e.clientWidth + 1; });
      const tiles = [...document.querySelectorAll('#dStats > div b')].filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent);
      return { header: H('dSummary'), reason: H('dReason'), chart: H('dChartWrap'), popup: H('detail'),
               clipped, clippedTiles: tiles, overflowX: document.documentElement.scrollWidth > window.innerWidth };
    });
    const box = await p.locator('#detail').boundingBox();
    await p.screenshot({ path: path.join(__dirname, '..', 'screenshots', `hdr-${tag}-${theme}-${w}.png`), clip: { x: box.x, y: box.y, width: box.width, height: Math.min(w < 600 ? 480 : 220, box.height) } });
    out.push({ size: `${w}x${h}`, theme, ...m, errors });
    await p.close();
  }
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(String(e).split('\n')[0]); process.exit(1); });
