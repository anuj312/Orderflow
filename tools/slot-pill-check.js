// Checks Top 3 slot score pills are not clipped, including the worst case of two status chips
// (WATCH + UNCONFIRMED), which demo mode never produces on its own.
// Usage: node tools/slot-pill-check.js [baseUrl] [playwrightModulePath]
const path = require('path');
const base = process.argv[2] || 'http://127.0.0.1:8000';
const { chromium } = require(process.argv[3] || 'playwright');
const sizes = [[1920, 1080], [1440, 900], [1280, 800]];
(async () => {
  const b = await chromium.launch();
  const out = [];
  for (const [width, height] of sizes) {
    const p = await b.newPage({ viewport: { width, height } });
    await p.addInitScript(() => { try { localStorage.setItem('of-theme', 'light'); } catch (e) { /* ignore */ } });
    await p.goto(base);
    await p.waitForFunction(() => document.querySelectorAll('#bullSlots .slot').length === 3, null, { timeout: 120000 });
    await p.waitForTimeout(1500);
    const r = await p.evaluate(() => {
      // Build a worst-case slot row inside each real slot host so it gets the real width and CSS.
      const mk = (cls, inner) => { const s = document.createElement('span'); s.className = cls; s.innerHTML = inner; return s; };
      const results = [];
      document.querySelectorAll('#bullSlots, #bearSlots').forEach((host) => {
        const slot = document.createElement('div');
        slot.className = 'slot filled';
        const row = document.createElement('div');
        row.className = 's-row c-scores';
        const mom = mk('score mom', '<em>MOM</em><b>42</b>'); mom.style.setProperty('--v', 42);
        const ob = mk('score ob', '<em>OB</em><b>81</b><i>WAIT</i>'); ob.dataset.s = 'WAIT'; ob.style.setProperty('--v', 81);
        row.append(mom, ob, mk('chip', 'SIDEWAYS'), mk('chip', 'UNCONFIRMED'));
        slot.appendChild(row);
        host.appendChild(slot);
        const clipped = [...row.querySelectorAll('.score')].filter((e) => e.scrollWidth > e.clientWidth + 1).length;
        results.push({ slotW: Math.round(slot.getBoundingClientRect().width), clipped, rows: new Set([...row.children].map((c) => Math.round(c.getBoundingClientRect().top))).size });
        slot.remove();
      });
      const realClipped = [...document.querySelectorAll('.slot .score')].filter((e) => e.scrollWidth > e.clientWidth + 1).length;
      return { worstCase: results, realClipped };
    });
    if (width === 1440) await p.screenshot({ path: path.join(__dirname, '..', 'screenshots', 'slot-pills-1440.png'), clip: { x: width - 260, y: 40, width: 260, height: height - 40 } });
    out.push({ size: `${width}x${height}`, ...r });
    await p.close();
  }
  console.log(JSON.stringify(out));
  await b.close();
})().catch((e) => { console.error(String(e).split('\n')[0]); process.exit(1); });
