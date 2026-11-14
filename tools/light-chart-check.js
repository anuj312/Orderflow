// Screenshots the chart popup in light mode and samples overlay pixels.
// Usage: node tools/light-chart-check.js [baseUrl] [playwrightModulePath]
const path = require('path');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const { chromium } = require(process.argv[3] || 'playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  p.on('pageerror', (e) => errors.push(String(e)));
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await p.addInitScript(() => { try { localStorage.setItem('of-theme', 'light'); } catch (e) { /* ignore */ } });
  await p.goto(base);
  await p.waitForFunction(() => document.querySelectorAll('#bullCards .card:not(.empty)').length === 6, null, { timeout: 120000 });
  await p.click('#bullCards .card:not(.empty)');
  await p.waitForFunction(() => /^\d+ \/ 90 CANDLES/.test(document.getElementById('dStatus').textContent), null, { timeout: 30000 });
  await p.waitForTimeout(800);
  const box = await p.locator('#dCanvas').boundingBox();
  // hover mid-chart so the crosshair price tag renders too
  await p.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4);
  await p.waitForTimeout(300);
  const px = await p.evaluate(() => {
    const c = document.getElementById('dCanvas'), g = c.getContext('2d'), d = window.devicePixelRatio || 1;
    const at = (x, y) => Array.from(g.getImageData(Math.round(x * d), Math.round(y * d), 1, 1).data).slice(0, 3);
    const w = c.getBoundingClientRect().width, h = c.getBoundingClientRect().height;
    return { theme: document.documentElement.dataset.theme, priceAxis: at(w - 8, h * 0.2), ohlcBar: at(12, 14) };
  });
  const dark = (rgb) => rgb.reduce((a, x) => a + x, 0) / 3 < 90;
  await p.screenshot({ path: path.join(__dirname, '..', 'screenshots', 'light-chart-1440.png') });
  console.log(JSON.stringify({ ...px, axisDark: dark(px.priceAxis), ohlcDark: dark(px.ohlcBar), errors }));
  await b.close();
})().catch((e) => { console.error(String(e).split('\n')[0]); process.exit(1); });
