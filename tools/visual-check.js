// Visual/interaction check with Playwright (uses an existing Playwright install).
// Usage: node tools/visual-check.js [baseUrl] [playwrightModulePath]
const path = require('path');
const fs = require('fs');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const pwPath = process.argv[3] || 'playwright';
const { chromium } = require(pwPath);
const out = path.join(__dirname, '..', 'screenshots');
fs.mkdirSync(out, { recursive: true });

const sizes = [
  { name: 'desktop-1920', width: 1920, height: 1080 },
  { name: 'laptop-1440', width: 1440, height: 900 },
  { name: 'tablet-1024', width: 1024, height: 768 },
  { name: 'phone-390', width: 390, height: 844 },
];

(async () => {
  const browser = await chromium.launch();
  const report = [];
  const only = process.argv[4] ? process.argv[4].split(',') : null;
  for (const s of sizes.filter((x) => !only || only.includes(x.name))) {
    const ctx = await browser.newContext({ viewport: { width: s.width, height: s.height }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    page._step='start'; try { report.push(await checkSize(page, s)); } catch (e) { await page.screenshot({ path: path.join(out, s.name + '-FAIL.png') }).catch(()=>{}); report.push({ size: s.name, step: page._step, failure: String(e).split('\n')[0] }); }
    await ctx.close();
  }
  await browser.close();
  console.log(JSON.stringify(report, null, 2));
})().catch((e) => { console.error(e); process.exit(1); });

async function checkSize(page, s) {
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(String(e)));
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    page._step=(page._step||'')+'>wait'; await page.waitForFunction(() => document.getElementById('histStatus').textContent === 'READY', null, { timeout: 120000 });
    page._step=(page._step||'')+'>wait'; await page.waitForFunction(() => document.querySelectorAll('#bullCards .card:not(.empty)').length === 6, null, { timeout: 30000 });
    await page.waitForTimeout(1500);
    const metrics = await page.evaluate(() => ({
      scrollW: document.documentElement.scrollWidth, innerW: window.innerWidth,
      scrollH: document.documentElement.scrollHeight, innerH: window.innerHeight,
      sectors: document.querySelectorAll('#sectorList li').length,
      bull: document.querySelectorAll('#bullCards .card').length, bear: document.querySelectorAll('#bearCards .card').length,
      slots: document.querySelectorAll('#bullSlots .slot').length + document.querySelectorAll('#bearSlots .slot').length,
      strands: document.querySelectorAll('#flowSvg .strand').length,
      pill: document.getElementById('marketPill').textContent,
      univ: document.getElementById('univBull').textContent + '/' + document.getElementById('univBear').textContent,
      clipped: Array.from(document.querySelectorAll('.c-sym')).filter((e) => e.scrollWidth > e.clientWidth + 1).map((e) => e.textContent),
    }));
    await page.screenshot({ path: path.join(out, s.name + '-dashboard.png') });
    // sector focus
    page._step=(page._step||'')+'>click'; await page.click('#sectorList li:first-child .sec-row');
    await page.waitForTimeout(600);
    const focusNote = await page.textContent('#bullNote');
    page._step=(page._step||'')+'>click'; await page.click('#showAll');
    // open detail
    page._step=(page._step||'')+'>click'; await page.click('#bullCards .card:not(.empty)');
    page._step=(page._step||'')+'>wait'; await page.waitForFunction(() => !document.getElementById('detail').hidden && /^\d+ \/ 90 CANDLES/.test(document.getElementById('dStatus').textContent), null, { timeout: 30000 });
    await page.waitForTimeout(800);
    const d1 = await page.evaluate(() => ({ status: document.getElementById('dStatus').textContent, sym: document.getElementById('dSymbol').textContent,
      ob: document.getElementById('dOb').textContent, reason: document.getElementById('dReason').textContent,
      overflow: document.documentElement.scrollWidth > window.innerWidth }));
    await page.screenshot({ path: path.join(out, s.name + '-detail-5m.png') });
    // manual pan -> AUTO off, then AUTO on
    const box = await page.locator('#dCanvas').boundingBox();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.mouse.down(); await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5, { steps: 6 }); await page.mouse.up();
    const afterPan = await page.textContent('#dStatus');
    page._step=(page._step||'')+'>click'; await page.click('#dAuto');
    const afterAuto = await page.textContent('#dStatus');
    page._step=(page._step||'')+'>click'; await page.click('#dTf button[data-tf="15m"]');
    page._step=(page._step||'')+'>wait'; await page.waitForFunction(() => document.getElementById('dCrumbTf').textContent.includes('15M'), null, { timeout: 30000 });
    await page.waitForTimeout(600);
    await page.screenshot({ path: path.join(out, s.name + '-detail-15m.png') });
    page._step=(page._step||'')+'>click'; await page.click('#dNext');
    await page.waitForTimeout(1200);
    const nextSym = await page.textContent('#dSymbol');
    await page.keyboard.press('Escape');
    const closed = await page.evaluate(() => document.getElementById('detail').hidden);
    // light theme screenshot
    page._step=(page._step||'')+'>click'; await page.click('#themeToggle'); await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(out, s.name + '-dashboard-light.png') });
    page._step=(page._step||'')+'>click'; await page.click('#themeToggle');
    return { size: s.name, metrics, focusNote, detail: d1, afterPan, afterAuto, nextSym, closed, errors };
}


