// Checks the chart popup bars: sizes, no overlap with the canvas, controls still work, screenshots.
// Usage: node tools/chart-bars-check.js [baseUrl] [playwrightModulePath]
const path = require('path');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const { chromium } = require(process.argv[3] || 'playwright');
(async () => {
  const b = await chromium.launch();
  const out = [];
  for (const [theme, w, h] of [['light', 1440, 900], ['dark', 1920, 1080], ['light', 1280, 720], ['dark', 390, 844]]) {
    const p = await b.newPage({ viewport: { width: w, height: h } });
    const errors = [];
    p.on('pageerror', (e) => errors.push(String(e)));
    p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    await p.addInitScript((t) => { try { localStorage.setItem('of-theme', t); } catch (e) { /* ignore */ } }, theme);
    await p.goto(base);
    await p.waitForFunction(() => document.querySelectorAll('#bullCards .card:not(.empty)').length === 6, null, { timeout: 120000 });
    await p.click('#bullCards .card:not(.empty)');
    await p.waitForFunction(() => /^\d+ \/ 90 CANDLES/.test(document.getElementById('dStatus').textContent), null, { timeout: 30000 });
    await p.waitForTimeout(700);
    const geo = await p.evaluate(() => {
      const R = (s) => document.querySelector(s).getBoundingClientRect();
      const c = R('#dCanvas'), t = R('.d-toolbar'), f = R('.d-chart-foot');
      return { canvasH: Math.round(c.height), toolbarH: Math.round(t.height), footH: Math.round(f.height),
               overlap: t.bottom > c.top + 1 || f.top < c.bottom - 1,
               tfBtnH: Math.round(R('#dTf button').height), legendBtnH: Math.round(R('#dLegend button').height),
               overflowX: document.documentElement.scrollWidth > window.innerWidth };
    });
    const box = await p.locator('#detail').boundingBox();
    await p.screenshot({ path: path.join(__dirname, '..', 'screenshots', `bars-${theme}-${w}.png`), clip: box });
    // controls still work: legend toggle, pan -> manual, AUTO, timeframe switch
    await p.click('#dLegend button[data-layer="vp"]');
    const vpOff = await p.evaluate(() => !document.querySelector('#dLegend button[data-layer="vp"]').classList.contains('on'));
    await p.click('#dLegend button[data-layer="vp"]');
    const cb = await p.locator('#dCanvas').boundingBox();
    await p.mouse.move(cb.x + cb.width * 0.5, cb.y + cb.height * 0.5);
    await p.mouse.down(); await p.mouse.move(cb.x + cb.width * 0.3, cb.y + cb.height * 0.5, { steps: 6 }); await p.mouse.up();
    const manual = (await p.textContent('#dStatus')).includes('MANUAL');
    await p.click('#dAuto');
    const auto = (await p.textContent('#dStatus')).includes('AUTO-FOLLOW');
    await p.click('#dTf button[data-tf="15m"]');
    await p.waitForFunction(() => document.getElementById('dCrumbTf').textContent.includes('15M'), null, { timeout: 30000 });
    out.push({ size: `${w}x${h}`, theme, ...geo, vpOff, manual, auto, tf15: true, errors });
    await p.close();
  }
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(String(e).split('\n')[0]); process.exit(1); });
