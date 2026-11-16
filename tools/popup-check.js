// Checks the stock popup: medium size, expand/restore, backdrop close, beams ending at column 1.
// Usage: node tools/popup-check.js [baseUrl] [playwrightModulePath]
const path = require('path');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const { chromium } = require(process.argv[3] || 'playwright');
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  p.on('pageerror', (e) => errors.push(String(e)));
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await p.goto(base);
  await p.waitForFunction(() => document.querySelectorAll('#bullCards .card:not(.empty)').length === 6, null, { timeout: 120000 });
  await p.waitForTimeout(2500);
  const rays = await p.evaluate(() => {
    const col1 = document.querySelector('#bullCards .card').getBoundingClientRect().left;
    const vis = Array.from(document.querySelectorAll('#rayLayer .ray')).filter((g) => g.style.display !== 'none');
    const xs = vis.map((g) => +g.querySelector('.ray-hit').getAttribute('cx'));
    return { count: vis.length, col2: vis.filter((g) => g.classList.contains('col2')).length,
             allEndAtCol1: xs.every((x) => Math.abs(x - (col1 + 2)) < 1.5) };
  });
  await p.screenshot({ path: path.join(__dirname, '..', 'screenshots', 'rays-12-1920.png') });
  await p.click('#bullCards .card:not(.empty)');
  await p.waitForFunction(() => /^\d+ \/ 90 CANDLES/.test(document.getElementById('dStatus').textContent), null, { timeout: 30000 });
  await p.waitForTimeout(700);
  const box = () => p.evaluate(() => { const r = document.getElementById('detail').getBoundingClientRect(); return [Math.round(r.width), Math.round(r.height)]; });
  const medium = await box();
  await p.screenshot({ path: path.join(__dirname, '..', 'screenshots', 'popup-medium-1920.png') });
  await p.click('#dFull'); await p.waitForTimeout(300);
  const expanded = await box();
  await p.click('#dFull'); await p.waitForTimeout(300);
  const restored = await box();
  await p.mouse.click(30, 1050); // backdrop
  await p.waitForTimeout(300);
  const closedByBackdrop = await p.evaluate(() => document.getElementById('detail').hidden && document.getElementById('detailBackdrop').hidden);
  console.log(JSON.stringify({ rays, medium, expanded, restored, closedByBackdrop, errors }));
  await b.close();
})().catch((e) => { console.error(String(e).split('\n')[0]); process.exit(1); });
