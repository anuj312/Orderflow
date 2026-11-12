/* Directional breadth map. One fine strand per available stock, from its sector node to
 * the bullish or bearish destination. Strand counts are participation/breadth, NOT rupee
 * money flow, order routing or exchange depth. DOM nodes are keyed so live updates never
 * restart animations or flicker. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function FlowMap(svg, tip, onSector) {
    this.svg = svg; this.tip = tip; this.onSector = onSector;
    this.gGrid = el('g', {}, svg);
    this.gStrands = el('g', {}, svg);
    this.gRibbons = el('g', {}, svg);
    this.gTracers = el('g', {}, svg);
    this.gNodes = el('g', {}, svg);
    this.strands = new Map(); this.tracers = new Map(); this.nodes = new Map(); this.ribbons = new Map();
    this.reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.focus = null; this.hoverSector = null; this.last = null;
    var self = this;
    this._ro = new ResizeObserver(function () { if (self.last) self.update(self.last, self.focus, true); });
    this._ro.observe(svg);
    this._particles();
  }

  FlowMap.prototype._particles = function () {
    for (var i = 0; i < 26; i++) {
      var c = el('circle', { r: (Math.random() * 1.4 + 0.5).toFixed(2), class: 'particle',
        fill: i % 2 ? 'rgba(70,222,176,.7)' : 'rgba(255,128,152,.6)' }, this.gGrid);
      c.dataset.fx = Math.random(); c.dataset.fy = Math.random();
      c.style.animationDelay = (-Math.random() * 7).toFixed(2) + 's';
    }
  };

  FlowMap.prototype.update = function (snap, focus, force) {
    this.last = snap; this.focus = focus;
    var r = this.svg.getBoundingClientRect();
    var W = Math.max(320, r.width), H = Math.max(240, r.height);
    this.svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    this.svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    Array.prototype.forEach.call(this.gGrid.children, function (c) {
      c.setAttribute('cx', (+c.dataset.fx * W).toFixed(1)); c.setAttribute('cy', (+c.dataset.fy * H).toFixed(1));
    });
    var recs = snap.records, sectors = snap.sectors.slice();
    var watch = snap.watchlistOnly || { symbols: [] };
    var rows = sectors.map(function (s) { return { key: s.sector, label: s.sector, chg: s.changePct, syms: s.symbols, watch: false }; });
    if (watch.symbols.length) rows.push({ key: '__WATCH', label: 'NIFTY 50 ONLY', chg: null, syms: watch.symbols, watch: true });
    var n = rows.length, top = 10, bottom = H - 10, gap = 4;
    var nodeW = Math.min(150, Math.max(84, W * 0.2)), nodeH = Math.max(14, (bottom - top - gap * (n - 1)) / n);
    var showValues = nodeW >= 128; // narrow layouts: the sector rail already shows the %
    // Destinations are slim breadth "gates"; their heights scale with the bullish/bearish share.
    var destW = 14, destX = W * 0.6;
    var totalAvail = 0;
    rows.forEach(function (row) { row.syms.forEach(function (s) { if (recs[s] && recs[s].dayChangePct != null) totalAvail++; }); });
    var usable = H * 0.78, bShare = 0.5;
    var nB = 0; rows.forEach(function (row) { row.syms.forEach(function (s) { if (recs[s] && recs[s].dayChangePct >= 0) nB++; }); });
    if (totalAvail) bShare = Math.min(0.85, Math.max(0.15, nB / totalAvail));
    var bullH = usable * bShare, bearH = usable - bullH;
    var bullBox = { x: destX, y: H * 0.07, h: bullH }, bearBox = { x: destX, y: H * 0.93 - bearH, h: bearH };
    var bullOrder = snap.rankings.bullish, bearOrder = snap.rankings.bearish;
    var bullIdx = new Map(), bearIdx = new Map();
    // destination slot order follows sector order so strands cross less
    var orderB = [], orderR = [];
    rows.forEach(function (row) {
      row.syms.forEach(function (s) { var rc = recs[s]; if (!rc || rc.dayChangePct == null) return; (rc.dayChangePct >= 0 ? orderB : orderR).push(s); });
    });
    orderB.forEach(function (s, i) { bullIdx.set(s, i); });
    orderR.forEach(function (s, i) { bearIdx.set(s, i); });
    var seen = new Set(), seenNodes = new Set(), self = this;
    var dimSector = this.focus || this.hoverSector;
    var tracerBudget = new Map();

    rows.forEach(function (row, ri) {
      var y = top + ri * (nodeH + gap);
      seenNodes.add(row.key);
      var g = self.nodes.get(row.key);
      if (!g) {
        g = el('g', { class: 'node', tabindex: row.watch ? -1 : 0, role: row.watch ? 'img' : 'button' }, self.gNodes);
        el('rect', { rx: 4 }, g); el('text', { class: 'n' }, g); el('text', { class: 'v', 'text-anchor': 'end' }, g);
        if (!row.watch) {
          g.addEventListener('click', function () { self.onSector(row.key); });
          g.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); self.onSector(row.key); } });
          g.addEventListener('mouseenter', function () { self.hoverSector = row.key; self._applyDim(); self._showTip(row.key); });
          g.addEventListener('mouseleave', function () { self.hoverSector = null; self._applyDim(); self.tip.hidden = true; });
        }
        self.nodes.set(row.key, g);
      }
      g.setAttribute('class', 'node' + (row.watch ? ' watch' : '') + (self.focus === row.key ? ' sel' : ''));
      g.setAttribute('aria-label', row.label + ' ' + (row.chg == null ? '' : row.chg.toFixed(2) + '%'));
      var rect = g.children[0], t1 = g.children[1], t2 = g.children[2];
      rect.setAttribute('x', 4); rect.setAttribute('y', y.toFixed(1)); rect.setAttribute('width', nodeW.toFixed(1)); rect.setAttribute('height', nodeH.toFixed(1));
      var fs = Math.min(11, nodeH * 0.62);
      t1.setAttribute('x', 11); t1.setAttribute('y', (y + nodeH / 2 + fs * 0.36).toFixed(1)); t1.style.fontSize = fs + 'px'; t1.textContent = row.label;
      t2.setAttribute('x', (nodeW - 3).toFixed(1)); t2.setAttribute('y', (y + nodeH / 2 + fs * 0.36).toFixed(1)); t2.style.fontSize = fs + 'px';
      t2.textContent = !showValues ? '' : row.chg == null ? row.syms.length : (row.chg >= 0 ? '+' : '') + row.chg.toFixed(2) + '%';
      if (!showValues && row.label.length > 9) t1.textContent = row.label.slice(0, 8) + '…';
      t2.setAttribute('fill', row.chg == null ? 'var(--sage)' : row.chg >= 0 ? 'var(--bull)' : 'var(--bear)');

      var syms = row.syms.filter(function (s) { return recs[s] && recs[s].dayChangePct != null; });
      syms.forEach(function (s, si) {
        var rc = recs[s], bull = rc.dayChangePct >= 0;
        var box = bull ? bullBox : bearBox, total = bull ? orderB.length : orderR.length;
        var di = bull ? bullIdx.get(s) : bearIdx.get(s);
        var x0 = 4 + nodeW, y0 = y + 2 + (nodeH - 4) * (syms.length > 1 ? si / (syms.length - 1) : 0.5);
        var x1 = box.x, y1 = box.y + 6 + (box.h - 12) * (total > 1 ? di / (total - 1) : 0.5);
        var dx = x1 - x0;
        var dPath = 'M' + x0.toFixed(1) + ' ' + y0.toFixed(1) + ' C' + (x0 + dx * 0.5).toFixed(1) + ' ' + y0.toFixed(1) + ' ' +
                    (x1 - dx * 0.42).toFixed(1) + ' ' + y1.toFixed(1) + ' ' + x1.toFixed(1) + ' ' + y1.toFixed(1);
        var p = self.strands.get(s);
        if (!p) { p = el('path', {}, self.gStrands); self.strands.set(s, p); }
        p.setAttribute('d', dPath);
        p.dataset.sector = row.key;
        p.setAttribute('class', 'strand ' + (bull ? 'b' : 'r'));
        seen.add(s);
      });
    });
    this.strands.forEach(function (p, s) { if (!seen.has(s)) { p.remove(); self.strands.delete(s); } });

    this.nodes.forEach(function (g, k) { if (!seenNodes.has(k)) { g.remove(); self.nodes.delete(k); } });
    // destinations
    [['__BULL', bullBox, 'b', 'BULLISH', orderB.length], ['__BEAR', bearBox, 'r', 'BEARISH', orderR.length]].forEach(function (d) {
      var g = self.nodes.get(d[0]);
      if (!g) { g = el('g', { class: 'dest ' + d[2] }, self.gNodes); el('rect', { rx: 6 }, g); el('text', {}, g); self.nodes.set(d[0], g); }
      seenNodes.add(d[0]);
      var rect = g.children[0], t = g.children[1];
      rect.setAttribute('x', d[1].x.toFixed(1)); rect.setAttribute('y', d[1].y.toFixed(1)); rect.setAttribute('width', destW.toFixed(1)); rect.setAttribute('height', Math.max(6, d[1].h).toFixed(1));
      t.setAttribute('x', (d[1].x + destW + 8).toFixed(1));
      t.setAttribute('y', (d[2] === 'b' ? d[1].y + 12 : d[1].y + d[1].h - 4).toFixed(1));
      t.textContent = d[3] + ' · ' + d[4];
    });
    this._applyDim();
  };

  FlowMap.prototype._applyDim = function () {
    var sec = this.hoverSector || this.focus;
    this.strands.forEach(function (p) {
      var on = !sec || p.dataset.sector === sec;
      p.classList.toggle('dim', !on); p.classList.toggle('hi', !!sec && on);
    });
  };

  /* Viewport-space anchors used by the beam overlay (rays.js). */
  FlowMap.prototype.nodeRect = function (sectorKey) {
    var g = this.nodes.get(sectorKey) || this.nodes.get('__WATCH');
    return g ? g.children[0].getBoundingClientRect() : null;
  };
  FlowMap.prototype.gateRect = function (side) {
    var g = this.nodes.get(side === 'bullish' ? '__BULL' : '__BEAR');
    return g ? g.children[0].getBoundingClientRect() : null;
  };

  FlowMap.prototype._showTip = function (key) {
    var snap = this.last; if (!snap) return;
    var s = snap.sectors.find(function (x) { return x.sector === key; }); if (!s) return;
    var g = this.nodes.get(key), box = g.getBoundingClientRect(), host = this.svg.getBoundingClientRect();
    this.tip.innerHTML = '<b>' + esc(s.sector) + '</b> · ' + (s.changePct == null ? '—' : (s.changePct >= 0 ? '+' : '') + s.changePct.toFixed(2) + '%') +
      '<br><span class="bull">' + s.bullish + ' up</span> · <span class="bear">' + s.bearish + ' down</span> · ' + s.total + ' stocks' +
      '<br><span class="dim">Equal-weight average · breadth, not money flow</span>';
    this.tip.style.left = (box.right - host.left + 8) + 'px';
    this.tip.style.top = (box.top - host.top) + 'px';
    this.tip.hidden = false;
  };

  window.OFFlowMap = FlowMap;
})();
