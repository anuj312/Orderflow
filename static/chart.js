/* Custom canvas candlestick chart for the detail overlay.
 * Draws candles, volume, session VWAP, floor pivots, OB zones, BOS/FVG/sweep marks, an
 * estimated volume profile, a live-price line and a crosshair with OHLC readout. */
(function () {
  'use strict';
  var VP = window.OFViewport;
  var INR = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var VOLF = new Intl.NumberFormat('en-IN', { notation: 'compact', maximumFractionDigits: 1 });
  var TIMEF = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false });
  var DAYF = new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short' });

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function OFChart(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.data = null;
    this.candles = [];
    this.vp = VP.fit(0);
    this.layers = { ob: true, vwap: true, sr: true, marks: true, vp: true };
    this.hover = null;
    this.onViewport = null;
    this.livePrice = null;
    this._raf = 0;
    this._pointers = new Map();
    this._anim = 0;
    this._bind();
    var self = this;
    this._ro = new ResizeObserver(function () { self.requestRender(); });
    this._ro.observe(canvas);
  }

  OFChart.prototype.setData = function (detail, keepView) {
    var old = this.candles;
    var next = detail.candles || [];
    var shift = 0;
    if (keepView && old.length && next.length) {
      var firstT = old[0].t, idx = -1;
      for (var i = 0; i < next.length; i++) { if (next[i].t === firstT) { idx = i; break; } }
      if (idx >= 0) shift = -idx; else {
        for (var j = 0; j < old.length; j++) { if (old[j].t === next[0].t) { shift = j; break; } }
      }
    }
    this.data = detail;
    this.candles = next;
    this.tIndex = new Map(next.map(function (c, i) { return [c.t, i]; }));
    this.livePrice = detail.summary ? detail.summary.ltp : null;
    this.vp = keepView ? VP.onData(this.vp, next.length, shift) : VP.fit(next.length);
    this._emit();
    this.requestRender();
  };

  OFChart.prototype.patchLive = function (ltp) {
    if (ltp == null || !this.candles.length) return;
    this.livePrice = ltp;
    var last = this.candles[this.candles.length - 1];
    if (!last.complete) { last.c = ltp; if (ltp > last.h) last.h = ltp; if (ltp < last.l) last.l = ltp; }
    this.requestRender();
  };

  OFChart.prototype.setLayer = function (k, on) { this.layers[k] = on; this.requestRender(); };
  OFChart.prototype.setAuto = function () { this.vp = VP.setAuto(this.vp, this.candles.length); this._emit(); this.requestRender(); };
  OFChart.prototype.fit = function () { this._animateTo(VP.fit(this.candles.length)); };
  OFChart.prototype.zoomBy = function (f) { this._animateTo(VP.zoom(this.vp, this.candles.length, f, 1)); };
  OFChart.prototype._emit = function () { if (this.onViewport) this.onViewport(this.vp); };

  OFChart.prototype._animateTo = function (target) {
    var self = this, from = this.vp, t0 = performance.now(), dur = 160;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    cancelAnimationFrame(this._anim);
    if (reduce) { this.vp = target; this._emit(); this.requestRender(); return; }
    function step(now) {
      var k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      self.vp = { start: from.start + (target.start - from.start) * e, end: from.end + (target.end - from.end) * e, auto: target.auto };
      self.render();
      if (k < 1) self._anim = requestAnimationFrame(step); else self._emit();
    }
    this._anim = requestAnimationFrame(step);
  };

  OFChart.prototype.requestRender = function () {
    var self = this;
    if (this._raf) return;
    this._raf = requestAnimationFrame(function () { self._raf = 0; self.render(); });
  };

  OFChart.prototype._layout = function () {
    var r = this.canvas.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    var w = Math.max(10, Math.round(r.width)), h = Math.max(10, Math.round(r.height));
    if (this.canvas.width !== w * dpr || this.canvas.height !== h * dpr) {
      this.canvas.width = w * dpr; this.canvas.height = h * dpr;
    }
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var axisW = 70, axisH = 22, top = 46;
    var plotW = w - axisW, plotH = h - axisH - top;
    var volH = Math.round(plotH * 0.16);
    return { w: w, h: h, top: top, plotW: plotW, priceH: plotH - volH - 6, volTop: top + plotH - volH, volH: volH, axisW: axisW };
  };

  OFChart.prototype._bind = function () {
    var self = this, c = this.canvas;
    c.addEventListener('wheel', function (e) {
      if (!self.candles.length) return;
      e.preventDefault();
      var L = self._layout(), rect = c.getBoundingClientRect();
      var frac = Math.min(1, Math.max(0, (e.clientX - rect.left) / L.plotW));
      self.vp = VP.zoom(self.vp, self.candles.length, Math.exp(e.deltaY * 0.0015), frac);
      self._emit(); self.requestRender();
    }, { passive: false });
    c.addEventListener('pointerdown', function (e) {
      c.setPointerCapture(e.pointerId);
      self._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (self._pointers.size === 1) { self._drag = { x: e.clientX, moved: false }; c.classList.add('grabbing'); }
      if (self._pointers.size === 2) { self._pinch = self._pinchState(); self._drag = null; }
    });
    c.addEventListener('pointermove', function (e) {
      var rect = c.getBoundingClientRect();
      if (self._pointers.has(e.pointerId)) self._pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (self._pinch && self._pointers.size === 2) {
        var p = self._pinchState();
        if (p.dist > 0 && self._pinch.dist > 0) {
          var L0 = self._layout();
          self.vp = VP.zoom(self.vp, self.candles.length, self._pinch.dist / p.dist, Math.min(1, Math.max(0, (p.mid - rect.left) / L0.plotW)));
          self._pinch = p; self._emit(); self.requestRender();
        }
        return;
      }
      if (self._drag) {
        var L = self._layout(), barW = L.plotW / (self.vp.end - self.vp.start);
        var dx = e.clientX - self._drag.x;
        if (Math.abs(dx) > 2) self._drag.moved = true;
        if (self._drag.moved) {
          self.vp = VP.pan(self.vp, self.candles.length, -dx / barW);
          self._drag.x = e.clientX; self._emit();
        }
      }
      self.hover = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      self.requestRender();
    });
    function up(e) {
      self._pointers.delete(e.pointerId);
      if (self._pointers.size < 2) self._pinch = null;
      if (self._pointers.size === 0) { self._drag = null; c.classList.remove('grabbing'); }
    }
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', up);
    c.addEventListener('pointerleave', function () { if (!self._drag) { self.hover = null; self.requestRender(); } });
    c.addEventListener('dblclick', function () { self.fit(); });
  };

  OFChart.prototype._pinchState = function () {
    var pts = Array.from(this._pointers.values());
    if (pts.length < 2) return { dist: 0, mid: 0 };
    return { dist: Math.abs(pts[0].x - pts[1].x) + Math.abs(pts[0].y - pts[1].y) * 0.25, mid: (pts[0].x + pts[1].x) / 2 };
  };

  OFChart.prototype._idxForT = function (t) {
    if (this.tIndex.has(t)) return this.tIndex.get(t);
    var cs = this.candles, lo = 0, hi = cs.length - 1;
    if (!cs.length) return 0;
    if (t <= cs[0].t) return 0;
    if (t >= cs[hi].t) return hi;
    while (lo < hi - 1) { var m = (lo + hi) >> 1; if (cs[m].t <= t) lo = m; else hi = m; }
    return lo;
  };

  OFChart.prototype.render = function () {
    var ctx = this.ctx, L = this._layout(), cs = this.candles, vp = this.vp;
    var C = { bull: css('--bull'), bear: css('--bear'), sage: css('--sage'), dim: css('--sage-dim'), text: css('--text'),
              border: css('--border-soft'), blue: css('--blue'), warn: css('--warn') };
    // Overlay backgrounds follow the theme: dark ink on the dark theme, white on light.
    var light = document.documentElement.getAttribute('data-theme') === 'light';
    var INK = light ? '255,255,255' : '10,17,24';
    var ink = function (a) { return 'rgba(' + INK + ',' + a + ')'; };
    C.pivot = light ? '#8a6420' : '#c9a86a';
    C.pivotRgb = light ? '138,100,32' : '201,168,106';
    C.cross = light ? '#d5e3df' : '#29404a';
    C.crossText = light ? '#0d2420' : C.text;
    ctx.clearRect(0, 0, L.w, L.h);
    if (!cs.length) return;
    var span = vp.end - vp.start, barW = L.plotW / span;
    var range = VP.visibleRange(vp, cs.length), i0 = range[0], i1 = range[1];
    var X = function (i) { return (i - vp.start + 0.5) * barW; };
    var lo = Infinity, hi = -Infinity, vmax = 0, k;
    for (k = i0; k <= i1; k++) {
      var c = cs[k]; if (c.l < lo) lo = c.l; if (c.h > hi) hi = c.h; if (c.v > vmax) vmax = c.v;
      if (this.layers.vwap && c.vwap != null) { lo = Math.min(lo, c.vwap); hi = Math.max(hi, c.vwap); }
    }
    if (!isFinite(lo)) return;
    var pad = (hi - lo) * 0.07 || hi * 0.002; lo -= pad; hi += pad;
    var Y = function (p) { return L.top + (hi - p) / (hi - lo) * L.priceH; };
    var occupied = [];
    function place(x, y, wdt, hgt) {
      for (var a = 0; a < 6; a++) {
        var yy = y + (a % 2 ? 1 : -1) * Math.ceil(a / 2) * (hgt + 2);
        var rct = { x: x, y: yy, w: wdt, h: hgt }, hit = false;
        for (var b = 0; b < occupied.length; b++) {
          var o = occupied[b];
          if (rct.x < o.x + o.w && rct.x + rct.w > o.x && rct.y < o.y + o.h && rct.y + rct.h > o.y) { hit = true; break; }
        }
        if (!hit && yy > L.top - 2 && yy + hgt < L.volTop) { occupied.push(rct); return yy; }
      }
      return null;
    }
    function label(text, x, y, color, bg) {
      ctx.font = '700 10px Inter, Segoe UI, sans-serif';
      var tw = ctx.measureText(text).width + 8;
      var yy = place(x, y - 8, tw, 15);
      if (yy == null) return;
      ctx.fillStyle = bg || ink(.86); ctx.fillRect(x, yy, tw, 15);
      ctx.fillStyle = color; ctx.fillText(text, x + 4, yy + 11);
    }

    // grid
    ctx.strokeStyle = C.border; ctx.lineWidth = 1; ctx.globalAlpha = 0.55;
    var steps = 6, pstep = (hi - lo) / steps;
    for (k = 1; k < steps; k++) { var gy = Math.round(Y(lo + pstep * k)) + 0.5; ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(L.plotW, gy); ctx.stroke(); }
    ctx.globalAlpha = 1;
    // session separators
    ctx.strokeStyle = C.border; ctx.setLineDash([2, 4]);
    for (k = Math.max(1, i0); k <= i1; k++) {
      if (DAYF.format(cs[k].t * 1000) !== DAYF.format(cs[k - 1].t * 1000)) {
        var sx = Math.round(X(k) - barW / 2) + 0.5; ctx.beginPath(); ctx.moveTo(sx, L.top); ctx.lineTo(sx, L.volTop + L.volH); ctx.stroke();
      }
    }
    ctx.setLineDash([]);

    var d = this.data || {};
    // estimated volume profile (right side of plot)
    if (this.layers.vp && d.profile && d.profile.bins) {
      var bins = d.profile.bins, bmax = 0;
      bins.forEach(function (b) { if (b.volume > bmax) bmax = b.volume; });
      var pw = L.plotW * 0.13;
      bins.forEach(function (b) {
        var y1 = Y(b.high), y2 = Y(b.low), bw = bmax ? b.volume / bmax * pw : 0;
        ctx.fillStyle = (b.low + b.high) / 2 >= (d.summary && d.summary.ltp || 0) ? 'rgba(255,128,152,.16)' : 'rgba(70,222,176,.16)';
        ctx.fillRect(L.plotW - bw, y1 + 0.5, bw, Math.max(1, y2 - y1 - 1));
      });
      if (d.profile.poc != null) { ctx.fillStyle = C.dim; ctx.fillRect(L.plotW - pw, Y(d.profile.poc), pw, 1); }
    }

    // OB zones
    var self = this;
    if (this.layers.ob && d.zones) {
      d.zones.forEach(function (z) {
        var a = self._idxForT(z.anchorT), e = z.invalid || z.historical ? self._idxForT(z.endT) : cs.length - 1;
        var x1 = X(a) - barW / 2, x2 = z.selected && !z.invalid ? L.plotW : X(e) + barW / 2;
        if (x2 < 0 || x1 > L.plotW) return;
        var bull = z.direction === 'BULLISH', col = bull ? '70,222,176' : '255,128,152';
        var faded = z.historical || z.invalid || z.expired || !z.selected;
        ctx.fillStyle = 'rgba(' + col + ',' + (faded ? 0.07 : 0.17) + ')';
        ctx.strokeStyle = 'rgba(' + col + ',' + (faded ? 0.25 : 0.7) + ')';
        ctx.setLineDash(z.invalid ? [3, 3] : []);
        var y1 = Y(z.high), y2 = Y(z.low);
        ctx.fillRect(x1, y1, x2 - x1, y2 - y1); ctx.strokeRect(Math.round(x1) + 0.5, Math.round(y1) + 0.5, Math.round(x2 - x1), Math.round(y2 - y1));
        ctx.setLineDash([]);
        if (z.selected && d.ob && d.ob.obQualityScore != null) {
          label('QUALITY OB ' + d.ob.obQualityScore + ' · ' + d.ob.obState, Math.max(4, x1 + 4), y1 - 2, bull ? C.bull : C.bear);
          label(INR.format(z.high) + ' – ' + INR.format(z.low), Math.max(4, x1 + 4), y2 + 14, C.sage);
        }
      });
    }
    // FVG
    if (this.layers.marks && d.ob && d.ob.obFvg) {
      var f = d.ob.obFvg, fa = this._idxForT(f.t1), fb = this._idxForT(f.t3);
      ctx.fillStyle = 'rgba(90,169,255,.12)'; ctx.strokeStyle = 'rgba(90,169,255,.5)';
      var fx1 = X(fa) - barW / 2, fx2 = X(fb) + barW * 3;
      ctx.fillRect(fx1, Y(f.high), fx2 - fx1, Y(f.low) - Y(f.high)); ctx.strokeRect(fx1, Y(f.high), fx2 - fx1, Y(f.low) - Y(f.high));
      label('FVG', fx2 + 3, Y(f.high) + 4, C.blue);
    }
    // pivots
    if (this.layers.sr && d.pivots && d.pivots.levels) {
      var lv = d.pivots.levels;
      Object.keys(lv).forEach(function (key) {
        var p = lv[key]; if (p < lo || p > hi) return;
        var py = Math.round(Y(p)) + 0.5;
        ctx.strokeStyle = 'rgba(' + C.pivotRgb + ',' + (key === 'P' ? .8 : .5) + ')';
        ctx.setLineDash(key === 'P' ? [] : [6, 4]); ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(L.plotW, py); ctx.stroke(); ctx.setLineDash([]);
        label(key + ' ' + INR.format(p), 6, py, C.pivot, ink(.75));
      });
    }
    // volume bars
    for (k = i0; k <= i1; k++) {
      var cv = cs[k]; if (cv.v == null || !vmax) continue;
      var vh = cv.v / vmax * L.volH;
      ctx.fillStyle = cv.c >= cv.o ? 'rgba(70,222,176,.38)' : 'rgba(255,128,152,.38)';
      ctx.fillRect(X(k) - barW * 0.36, L.volTop + L.volH - vh, Math.max(1, barW * 0.72), vh);
    }
    // VWAP
    if (this.layers.vwap) {
      ctx.strokeStyle = C.blue; ctx.lineWidth = 1.4; ctx.setLineDash([5, 4]); ctx.beginPath();
      var started = false, prevDay = null;
      for (k = i0; k <= i1; k++) {
        var vw = cs[k].vwap, day = DAYF.format(cs[k].t * 1000);
        if (vw == null || day !== prevDay) { started = false; }
        prevDay = day;
        if (vw == null) continue;
        if (!started) { ctx.moveTo(X(k), Y(vw)); started = true; } else ctx.lineTo(X(k), Y(vw));
      }
      ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth = 1;
    }
    // candles
    var bw = Math.max(1, barW * 0.64);
    for (k = i0; k <= i1; k++) {
      var cc = cs[k], up = cc.c >= cc.o, col = up ? C.bull : C.bear, x = X(k);
      ctx.strokeStyle = col; ctx.fillStyle = col; ctx.globalAlpha = cc.complete ? 1 : 0.6;
      ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, Y(cc.h)); ctx.lineTo(Math.round(x) + 0.5, Y(cc.l)); ctx.stroke();
      var yo = Y(cc.o), yc = Y(cc.c), bh = Math.max(1, Math.abs(yc - yo));
      if (cc.complete) ctx.fillRect(x - bw / 2, Math.min(yo, yc), bw, bh);
      else ctx.strokeRect(x - bw / 2 + 0.5, Math.min(yo, yc) + 0.5, bw - 1, bh);
      ctx.globalAlpha = 1;
    }
    // BOS + sweep
    if (this.layers.marks && d.ob && d.ob.obBos) {
      var bos = d.ob.obBos, bi = this._idxForT(bos.t), by = Math.round(Y(bos.level)) + 0.5;
      var bcol = d.ob.obDirection === 'BULLISH' ? C.bull : C.bear;
      ctx.strokeStyle = bcol; ctx.setLineDash([2, 3]); ctx.beginPath(); ctx.moveTo(X(Math.max(0, bi - 9)), by); ctx.lineTo(X(bi) + barW, by); ctx.stroke(); ctx.setLineDash([]);
      label('BOS ×' + bos.displacement.toFixed(2), X(bi) + barW, by, bcol);
    }
    if (this.layers.marks && d.ob && d.ob.obSweep) {
      var sw = d.ob.obSweep, si = this._idxForT(sw.t), sy = Y(sw.extreme);
      ctx.strokeStyle = C.warn; ctx.beginPath(); ctx.arc(X(si), sy, 5, 0, Math.PI * 2); ctx.stroke();
      label('SWEEP', X(si) + 7, sy + (d.ob.obDirection === 'BULLISH' ? 12 : -2), C.warn);
    }

    // right axis + tags
    ctx.fillStyle = ink(light ? .7 : .55); ctx.fillRect(L.plotW, 0, L.axisW, L.h);
    ctx.font = '600 10px JetBrains Mono, Consolas, monospace'; ctx.fillStyle = C.sage; ctx.textAlign = 'right';
    for (k = 0; k <= steps; k++) { var pv = lo + pstep * k; ctx.fillText(INR.format(pv), L.w - 6, Y(pv) + 3); }
    var tags = [];
    var live = this.livePrice != null ? this.livePrice : cs[cs.length - 1].c;
    var lastC = cs[cs.length - 1];
    tags.push({ p: live, text: INR.format(live), col: live >= (d.summary && d.summary.prevClose || lastC.o) ? C.bull : C.bear, fg: '#06140f' });
    if (this.layers.vwap && lastC.vwap != null) tags.push({ p: lastC.vwap, text: INR.format(lastC.vwap), col: C.blue, fg: '#06101a' });
    tags = tags.filter(function (t) { return t.p >= lo && t.p <= hi; }).map(function (t) { t.y = Y(t.p); return t; }).sort(function (a, b) { return a.y - b.y; });
    for (k = 1; k < tags.length; k++) if (tags[k].y - tags[k - 1].y < 15) tags[k].y = tags[k - 1].y + 15;
    if (live >= lo && live <= hi) {
      var ly = Math.round(Y(live)) + 0.5; ctx.strokeStyle = tags[0] ? tags.find(function (t) { return t.p === live; }).col : C.text;
      ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(0, ly); ctx.lineTo(L.plotW, ly); ctx.stroke(); ctx.setLineDash([]);
    }
    tags.forEach(function (t) {
      ctx.fillStyle = t.col; ctx.fillRect(L.plotW + 1, t.y - 8, L.axisW - 2, 16);
      ctx.fillStyle = t.fg; ctx.font = '700 10px JetBrains Mono, Consolas, monospace'; ctx.fillText(t.text, L.w - 5, t.y + 4);
    });
    // time axis
    ctx.textAlign = 'center'; ctx.fillStyle = C.sage; ctx.font = '600 10px JetBrains Mono, Consolas, monospace';
    var every = Math.max(1, Math.ceil(80 / barW)), lastX = -1e9;
    for (k = i0; k <= i1; k++) {
      if (k % every) continue;
      var tx = X(k); if (tx - lastX < 70) continue; lastX = tx;
      var newDay = k > 0 && DAYF.format(cs[k].t * 1000) !== DAYF.format(cs[k - 1].t * 1000);
      ctx.fillText(newDay ? DAYF.format(cs[k].t * 1000) : TIMEF.format(cs[k].t * 1000), tx, L.h - 7);
    }
    ctx.textAlign = 'left';

    // crosshair + OHLC readout
    var hv = this.hover;
    var hi_ = hv ? Math.floor(vp.start + hv.x / barW) : cs.length - 1;
    if (hi_ < 0 || hi_ >= cs.length || (hv && hv.x > L.plotW)) hi_ = cs.length - 1;
    if (hv && hv.x <= L.plotW) {
      ctx.strokeStyle = 'rgba(142,173,166,.45)'; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(Math.round(X(hi_)) + 0.5, L.top); ctx.lineTo(Math.round(X(hi_)) + 0.5, L.volTop + L.volH); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, Math.round(hv.y) + 0.5); ctx.lineTo(L.plotW, Math.round(hv.y) + 0.5); ctx.stroke(); ctx.setLineDash([]);
      if (hv.y >= L.top && hv.y <= L.top + L.priceH) {
        var hp = hi - (hv.y - L.top) / L.priceH * (hi - lo);
        ctx.fillStyle = C.cross; ctx.fillRect(L.plotW + 1, hv.y - 8, L.axisW - 2, 16);
        ctx.fillStyle = C.text; ctx.textAlign = 'right'; ctx.fillText(INR.format(hp), L.w - 5, hv.y + 4); ctx.textAlign = 'left';
      }
    }
    var hc = cs[hi_];
    var txt = DAYF.format(hc.t * 1000) + ' ' + TIMEF.format(hc.t * 1000) + ' IST   O ' + INR.format(hc.o) + '  H ' + INR.format(hc.h) +
              '  L ' + INR.format(hc.l) + '  C ' + INR.format(hc.c) + '  V ' + (hc.v != null ? VOLF.format(hc.v) : 'N/A') +
              (hc.complete ? '' : '  · PROVISIONAL') + (hc.partial ? '  · PARTIAL VOL' : '');
    ctx.font = '600 10.5px JetBrains Mono, Consolas, monospace';
    ctx.fillStyle = ink(.86); ctx.fillRect(8, 10, Math.min(L.plotW - 16, ctx.measureText(txt).width + 12), 18);
    ctx.fillStyle = hc.c >= hc.o ? C.bull : C.bear; ctx.fillText(txt, 14, 23);
  };

  window.OFChart = OFChart;
})();
