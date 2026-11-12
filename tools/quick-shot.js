// Quick dashboard screenshots: node tools/quick-shot.js [baseUrl] [playwrightModulePath] [width] [height]
const path = require('path');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const { chromium } = require(process.argv[3] || 'playwright');
const width = +(process.argv[4] || 1920), height = +(process.argv[5] || 1080);
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width, height } });
  const errors = [];
  p.on('pageerror', (e) => errors.push(String(e)));
  p.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  await p.addInitScript(() => {
    window.__long = [];
    new PerformanceObserver((l) => l.getEntries().forEach((e) => window.__long.push(Math.round(e.duration)))).observe({ entryTypes: ['longtask'] });
  });
  await p.goto(base);
  await p.waitForFunction(() => document.querySelectorAll('#bullCards .card:not(.empty)').length === 6, null, { timeout: 120000 });
  await p.waitForTimeout(2500);
  const out = path.join(__dirname, '..', 'screenshots');
  await p.screenshot({ path: path.join(out, `quick-${width}-dark.png`) });
  const info = await p.evaluate(() => {
    const vis = Array.from(document.querySelectorAll('#rayLayer .ray')).filter((g) => g.style.display !== 'none');
    return { rays: vis.length, bullRays: vis.filter((g) => g.classList.contains('b')).length,
             bearRays: vis.filter((g) => g.classList.contains('r')).length,
             longTasks: window.__long.slice(-10), longTotal: window.__long.reduce((a, x) => a + x, 0) };
  });
  await p.click('#themeToggle', { timeout: 10000 });
  await p.waitForTimeout(600);
  await p.screenshot({ path: path.join(out, `quick-${width}-light.png`) });
  await p.click('#themeToggle', { timeout: 10000 });
  console.log(JSON.stringify({ ...info, errors }));
  await b.close();
})().catch((e) => { console.error(String(e).split('\n')[0]); process.exit(1); });
