/* X-ray style beams: one thin glowing ray per displayed Top 6 card, running from the
 * stock's sector node, through the bullish/bearish breadth gate, onto the card itself.
 * Light pulses travel along each ray; brightness scales with the MOM score.
 * Purely a visual link between breadth and the leader cards: not money flow or order routing. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  function el(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }

  function RayLayer(svg, flow) {
    this.svg = svg; this.flow = flow; this.items = new Map(); this.snap = null;
    this.desktop = window.matchMedia('(min-width: 1280px)');
    this.g = el('g', {}, svg);
    var self = this;
    var schedule = function () { if (self.snap) self.request(); };
    window.addEventListener('resize', schedule);
    window.addEventListener('scroll', schedule, { passive: true, capture: true });
    ['bullCards', 'bearCards'].forEach(function (id) {
      var host = document.getElementById(id);
      host.addEventListener('mouseover', function (e) { var c = e.target.closest('.card'); self.highlight(c ? c.dataset.ray : null); });
      host.addEventListener('mouseleave', function () { self.highlight(null); });
    });
  }

  RayLayer.prototype.request = function () {
    var self = this;
    if (this._raf) return;
    this._raf = requestAnimationFrame(function () { self._raf = 0; self.draw(); });
  };

  RayLayer.prototype.update = function (snap) { this.snap = snap; this.request(); };

  RayLayer.prototype.highlight = function (key) {
    this.items.forEach(function (it, k) {
      it.g.classList.toggle('hi', !!key && k === key);
      it.g.classList.toggle('dim', !!key && k !== key);
    });
  };

  RayLayer.prototype._item = function (key, side) {
    var it = this.items.get(key);
    if (it) return it;
    var g = el('g', { class: 'ray ' + (side === 'bullish' ? 'b' : 'r') }, this.g);
    g.dataset.side = side;
    var glow = el('path', { class: 'ray-glow' }, g);
    var core = el('path', { class: 'ray-core', id: 'ray-' + key }, g);
    var bloom = el('path', { class: 'ray-pulse bloom', pathLength: 1000 }, g);
    var p1 = el('path', { class: 'ray-pulse', pathLength: 1000 }, g);
    var p2 = el('path', { class: 'ray-pulse two', pathLength: 1000 }, g);
    var hit = el('circle', { class: 'ray-hit', r: 3.2 }, g);
    var ring = el('circle', { class: 'ray-ring', r: 3.2 }, g);
    var delay = (-Math.random() * 2.4).toFixed(2) + 's';
    p1.style.animationDelay = delay;
    bloom.style.animationDelay = delay;
    p2.style.animationDelay = (parseFloat(delay) - 1.2).toFixed(2) + 's';
    var p3 = el('path', { class: 'ray-pulse three', pathLength: 1000 }, g);
    p3.style.animationDelay = (parseFloat(delay) - 0.6).toFixed(2) + 's';
    // two sparks riding the beam (SMIL), hidden by CSS when MOTION is off
    for (var s = 0; s < 2; s++) {
      var spark = el('circle', { class: 'ray-spark', r: s ? 1.8 : 2.6 }, g);
      var am = el('animateMotion', { dur: '1.9s', repeatCount: 'indefinite', begin: (parseFloat(delay) - s * 0.95).toFixed(2) + 's' }, spark);
      el('mpath', { href: '#ray-' + key }, am);
    }
    it = { g: g, glow: glow, core: core, bloom: bloom, p1: p1, p2: p2, p3: p3, hit: hit, ring: ring };
    this.items.set(key, it);
    return it;
  };

  RayLayer.prototype.draw = function () {
    var snap = this.snap, self = this;
    if (!snap || !this.desktop.matches || !document.getElementById('detail').hidden) {
      this.svg.style.display = 'none';
      return;
    }
    this.svg.style.display = '';
    var W = window.innerWidth, H = window.innerHeight, vb = '0 0 ' + W + ' ' + H;
    if (this._vb !== vb) { this.svg.setAttribute('viewBox', vb); this._vb = vb; }
    var live = new Set();
    [['bullish', 'bullCards'], ['bearish', 'bearCards']].forEach(function (pair) {
      var side = pair[0], cards = document.getElementById(pair[1]).children;
      var gate = self.flow.gateRect(side);
      for (var i = 0; i < cards.length; i++) {
        var card = cards[i], sym = card.dataset.symbol, key = (side === 'bullish' ? 'up' : 'dn') + i;
        // Every stock gets a beam, but beams never cross into the second column:
        // second-column stocks terminate on the first column's left edge (same row, lower slot).
        var firstCol = i % 2 === 0;
        card.dataset.ray = sym ? key : '';
        if (card.classList.contains('rayed') !== !!sym) card.classList.toggle('rayed', !!sym);
        if (!sym || !gate) continue;
        var row = i >> 1;
        var anchorCard = firstCol ? card : (cards[i - 1] || card);
        var rec = snap.records[sym];
        var node = self.flow.nodeRect(rec && rec.sector !== 'NIFTY_50' ? rec.sector : '__WATCH');
        if (!node) continue;
        var cr = anchorCard.getBoundingClientRect();
        var x0 = node.right, y0 = node.top + node.height / 2;
        var gx = gate.left + gate.width / 2, gy = gate.top + gate.height * (2 * row + (firstCol ? 0.5 : 1.5)) / 6;
        var x1 = cr.left + 2, y1 = cr.top + (firstCol ? Math.min(18, cr.height * 0.22) : cr.height * 0.62);
        var d = 'M' + x0.toFixed(1) + ' ' + y0.toFixed(1) +
                ' C' + (x0 + (gx - x0) * 0.55).toFixed(1) + ' ' + y0.toFixed(1) + ' ' + (gx - (gx - x0) * 0.3).toFixed(1) + ' ' + gy.toFixed(1) + ' ' + gx.toFixed(1) + ' ' + gy.toFixed(1) +
                ' C' + (gx + (x1 - gx) * 0.45).toFixed(1) + ' ' + gy.toFixed(1) + ' ' + (x1 - (x1 - gx) * 0.35).toFixed(1) + ' ' + y1.toFixed(1) + ' ' + x1.toFixed(1) + ' ' + y1.toFixed(1);
        var it = self._item(key, side);
        if (it.d !== d) { [it.glow, it.core, it.bloom, it.p1, it.p2, it.p3].forEach(function (p) { p.setAttribute('d', d); }); it.d = d; }
        it.hit.setAttribute('cx', x1.toFixed(1)); it.hit.setAttribute('cy', y1.toFixed(1));
        it.ring.setAttribute('cx', x1.toFixed(1)); it.ring.setAttribute('cy', y1.toFixed(1));
        var mom = rec && rec.mom != null ? rec.mom : 30;
        it.g.style.setProperty('--k', ((0.4 + 0.6 * mom / 100) * (firstCol ? 1 : 0.8)).toFixed(2));
        it.g.classList.toggle('col2', !firstCol);
        it.g.style.display = '';
        live.add(key);
      }
    });
    this.items.forEach(function (it, k) { if (!live.has(k)) it.g.style.display = 'none'; });
  };

  window.OFRayLayer = RayLayer;
})();
