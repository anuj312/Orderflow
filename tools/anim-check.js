// Verifies the X-ray beams really animate (also with OS reduced-motion on).
// Usage: node tools/anim-check.js [baseUrl] [playwrightModulePath]
const path = require('path');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const { chromium } = require(process.argv[3] || 'playwright');
(async () => {
  const b = await chromium.launch();
  const results = {};
  for (const reducedMotion of ['no-preference', 'reduce']) {
    const ctx = await b.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion });
    const p = await ctx.newPage();
    await p.goto(base);
    await p.waitForFunction(() => document.querySelectorAll('#rayLayer .ray-pulse').length >= 12, null, { timeout: 120000 });
    const sample = () => p.evaluate(() => {
      const pulse = document.querySelector('#rayLayer .ray:not([style*="none"]) .ray-pulse:not(.bloom)');
      const strand = document.querySelector('#flowSvg .strand');
      const spark = document.querySelector('#rayLayer .ray-spark');
      const sb = spark ? spark.getBoundingClientRect() : null;
      return { pulse: getComputedStyle(pulse).strokeDashoffset, strand: getComputedStyle(strand).strokeDashoffset,
               spark: sb ? Math.round(sb.x) + ',' + Math.round(sb.y) : null };
    });
    const a = await sample(); await p.waitForTimeout(500); const c = await sample();
    results[reducedMotion] = { pulseMoves: a.pulse !== c.pulse, strandMoves: a.strand !== c.strand, sparkMoves: a.spark !== c.spark, a, c };
    if (reducedMotion === 'no-preference') await p.screenshot({ path: path.join(__dirname, '..', 'screenshots', 'anim-1920.png') });
    // motion toggle off -> frozen
    if (reducedMotion === 'reduce') {
      await p.click('#motionToggle');
      const x = await sample(); await p.waitForTimeout(500); const y = await sample();
      results.toggleOff = { pulseMoves: x.pulse !== y.pulse, motionAttr: await p.evaluate(() => document.documentElement.dataset.motion) };
      await p.click('#motionToggle');
    }
    await ctx.close();
  }
  console.log(JSON.stringify(results, null, 1));
  await b.close();
})().catch((e) => { console.error(String(e).split('\n')[0]); process.exit(1); });
