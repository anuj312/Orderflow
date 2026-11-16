/* OrderFlow Studio terminal controller. Read-only: no trading actions anywhere. */
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  var INR = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var INT = new Intl.NumberFormat('en-IN');
  var SEGS = 18;
  var STATE = { snap: null, focus: null, detail: null, bookmarks: loadBookmarks(), es: null, lastMsgAt: 0 };

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function pct(v, d) { return v == null ? '—' : (v >= 0 ? '+' : '') + v.toFixed(d == null ? 2 : d) + '%'; }
  function cls(v) { return v == null ? 'flat' : v >= 0 ? 'bull' : 'bear'; }
  function dash(v) { return v == null ? '—' : v; }
  function tvUrl(sym) { return 'https://www.tradingview.com/chart/?symbol=' + encodeURIComponent('NSE:' + sym.replace(/-/g, '_')) + '&interval=5'; }
  function loadBookmarks() { try { return new Set(JSON.parse(localStorage.getItem('of-bookmarks') || '[]')); } catch (e) { return new Set(); } }
  function saveBookmarks() { try { localStorage.setItem('of-bookmarks', JSON.stringify(Array.from(STATE.bookmarks))); } catch (e) { /* ignore */ } }

  // ------------------------------------------------------------------ theme
  $('themeToggle').addEventListener('click', function () {
    var root = document.documentElement, next = root.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    root.setAttribute('data-theme', next);
    try { localStorage.setItem('of-theme', next); } catch (e) { /* ignore */ }
    if (STATE.detail && STATE.detail.chart) STATE.detail.chart.requestRender();
  });

  var motionBtn = $('motionToggle');
  function syncMotion() { motionBtn.setAttribute('aria-pressed', String(document.documentElement.getAttribute('data-motion') !== 'off')); }
  syncMotion();
  motionBtn.addEventListener('click', function () {
    var root = document.documentElement, next = root.getAttribute('data-motion') === 'off' ? 'on' : 'off';
    root.setAttribute('data-motion', next);
    try { localStorage.setItem('of-motion', next); } catch (e) { /* ignore */ }
    syncMotion();
  });

  // ------------------------------------------------------------------ data stream
  function connect() {
    if (!window.EventSource) { poll(); return; }
    var es = new EventSource('/api/events');
    STATE.es = es;
    es.addEventListener('snapshot', function (ev) {
      STATE.lastMsgAt = Date.now();
      banner(null);
      try { render(JSON.parse(ev.data)); } catch (e) { console.error(e); }
    });
    es.onerror = function () { banner('Live stream interrupted — reconnecting automatically…'); };
  }
  function poll() {
    fetch('/api/state', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(render)
      .catch(function () { banner('Server unreachable — retrying…'); })
      .finally(function () { setTimeout(poll, 2000); });
  }
  setInterval(function () {
    if (STATE.lastMsgAt && Date.now() - STATE.lastMsgAt > 20000) banner('No update for ' + Math.round((Date.now() - STATE.lastMsgAt) / 1000) + ' s — values may be stale.');
  }, 5000);
  function banner(msg, kind) {
    var b = $('connBanner');
    if (!msg) { b.hidden = true; return; }
    b.textContent = msg; b.dataset.kind = kind || 'warn'; b.hidden = false;
  }

  // ------------------------------------------------------------------ render
  var flow = new window.OFFlowMap($('flowSvg'), $('flowTip'), function (sec) { setFocus(STATE.focus === sec ? null : sec); });
  var rays = new window.OFRayLayer($('rayLayer'), flow);

  function render(snap) {
    STATE.snap = snap;
    renderHeader(snap);
    renderSectors(snap);
    flow.update(snap, STATE.focus);
    renderUniverse(snap);
    renderLeaders(snap);
    renderPicks(snap);
    rays.update(snap);
    if (STATE.detail) patchDetailLive(snap);
  }

  function renderHeader(s) {
    var h = s.history;
    $('histReady').textContent = h.loaded + ' / ' + h.target;
    $('histStatus').textContent = h.status || '—';
    $('histStatus').dataset.s = h.status || '';
    $('tickCount').textContent = INT.format(s.ticks);
    var pill = $('marketPill');
    pill.textContent = s.market.label; pill.dataset.status = s.market.status; pill.title = s.market.reason || '';
    $('viewerLabel').textContent = s.viewer || 'VIEWER';
    var m = s.market, dot = $('feedDot'), txt;
    if (s.mode === 'demo') { dot.dataset.s = 'demo'; txt = 'DEMO FEED · SYNTHETIC PRICES · NOT MARKET DATA'; }
    else if (m.status === 'ERROR') { dot.dataset.s = 'err'; txt = (s.mode === 'kite' ? 'KITE · ' : 'INGEST · ') + (m.feedError || m.reason); }
    else {
      dot.dataset.s = m.status === 'LIVE' ? 'ok' : 'warn';
      txt = (s.mode === 'kite' ? 'KITE CONNECT · ' + (m.feedState || '') : 'EXTERNAL INGEST · ' + (m.feedState || '')) + ' · ' + m.reason;
    }
    $('feedText').textContent = txt;
    if (m.status === 'ERROR') banner(m.reason, 'error');
  }

  function renderSectors(s) {
    var list = $('sectorList');
    list.classList.toggle('focused', !!STATE.focus);
    var existing = new Map(Array.prototype.map.call(list.children, function (li) { return [li.dataset.sector, li]; }));
    s.sectors.forEach(function (sec, i) {
      var li = existing.get(sec.sector);
      if (!li) {
        li = document.createElement('li'); li.dataset.sector = sec.sector;
        li.innerHTML = '<button type="button" class="sec-row" aria-pressed="false"><span class="sec-name"></span><span class="sec-chg"></span><span class="sec-bars" style="--segs:' + SEGS + '"></span></button>';
        li.firstChild.addEventListener('click', function () { setFocus(STATE.focus === sec.sector ? null : sec.sector); });
        var bars = li.querySelector('.sec-bars');
        for (var k = 0; k < SEGS; k++) bars.appendChild(document.createElement('i'));
      }
      if (list.children[i] !== li) list.insertBefore(li, list.children[i] || null);
      var btn = li.firstChild;
      btn.setAttribute('aria-pressed', String(STATE.focus === sec.sector));
      btn.setAttribute('aria-label', sec.sector + ' ' + pct(sec.changePct) + ', ' + sec.bullish + ' up, ' + sec.bearish + ' down');
      li.querySelector('.sec-name').textContent = sec.sector;
      var chg = li.querySelector('.sec-chg'); chg.textContent = pct(sec.changePct); chg.className = 'sec-chg ' + cls(sec.changePct);
      var nb = sec.total ? Math.round(sec.bullish / sec.total * SEGS) : 0, nr = sec.total ? SEGS - nb : 0;
      Array.prototype.forEach.call(li.querySelectorAll('.sec-bars i'), function (seg, k) { seg.className = k < nb ? 'b' : (k < nb + nr ? 'r' : ''); });
    });
    $('sectorCount').textContent = s.sectors.length + ' SECTORS TRACKED';
    $('showAll').disabled = !STATE.focus;
  }

  function renderUniverse(s) {
    var u = s.universe;
    $('univBull').textContent = u.bullish; $('univBear').textContent = u.bearish;
    var note = u.available + ' OF ' + u.symbols + ' WITH QUOTES';
    if (u.unresolved && u.unresolved.length) note += ' · ' + u.unresolved.length + ' UNRESOLVED';
    $('univNote').textContent = note;
  }

  function setFocus(sec) {
    STATE.focus = sec;
    if (STATE.snap) render(STATE.snap);
  }
  $('showAll').addEventListener('click', function () { setFocus(null); });

  // ------------------------------------------------------------------ leaders
  function leadersFor(s, side) {
    if (!STATE.focus) return s.leaders[side];
    return s.rankings[side].filter(function (sym) { return s.records[sym] && s.records[sym].sector === STATE.focus; }).slice(0, 6);
  }

  function oiKind(caption) {
    if (caption === 'LONG BUILD-UP' || caption === 'SHORT COVERING') return 'up';
    if (caption === 'SHORT BUILD-UP' || caption === 'LONG UNWINDING') return 'dn';
    return '';
  }

  function momPill(v, basis) {
    return '<span class="score mom' + (v == null ? ' na' : '') + '" style="--v:' + (v == null ? 0 : v) + '" title="MOM momentum score 0-100' +
      (basis ? ' (' + esc(basis) + ')' : '') + '. Heuristic, not a probability."><em>MOM</em><b>' + dash(v) + '</b></span>';
  }
  function obPill(v, state) {
    return '<span class="score ob' + (v == null ? ' na' : '') + '" data-s="' + esc(state || '') + '" style="--v:' + (v == null ? 0 : v) +
      '" title="Order-block quality from completed 5m candles"><em>OB</em><b>' + dash(v) + '</b>' + (state ? '<i>' + esc(state) + '</i>' : '') + '</span>';
  }

  function cardHtml(r) {
    var vp = '';
    if (r.vp && r.vp.length) {
      var mx = Math.max.apply(null, r.vp);
      vp = '<div class="c-vp" title="Estimated volume-at-price from today\'s 5m candles (not market depth)">' + r.vp.map(function (v) { return '<i class="' + (v === mx ? 'poc' : '') + '" style="width:' + Math.max(8, v) + '%;align-self:flex-end"></i>'; }).join('') + '</div>';
    } else vp = '<div class="c-vp na" aria-hidden="true">VP N/A</div>';
    return '<div class="c-main">' +
      '<div class="c-top"><span class="c-sym" title="' + esc(r.symbol) + '">' + esc(r.symbol) + '</span>' +
      '<span class="c-chg ' + cls(r.dayChangePct) + '">' + pct(r.dayChangePct) + '</span></div>' +
      '<div class="c-scores">' + momPill(r.mom, r.momBasis) + obPill(r.obQualityScore, r.obState) + '</div>' +
      '<div class="c-meta"><span>₹' + (r.ltp == null ? 'N/A' : INR.format(r.ltp)) + '</span><span>#' + dash(r.rank) + '/' + dash(r.rankOf) +
      (r.pickEligible ? ' · ELIGIBLE' : '') + '</span></div>' +
      '<div class="c-oi" data-k="' + oiKind(r.oiCaption) + '">' + esc(r.oiCaption || 'OI UNAVAILABLE') + '</div>' +
      '<div class="c-foot"><span class="chip" data-s="' + esc(r.stateChip) + '">' + esc(r.stateChip) + '</span>' +
      '<span class="c-rvol">RVOL ' + (r.rvol == null ? 'N/A' : r.rvol.toFixed(2) + '×') + '</span>' +
      '<a class="c-tv" href="' + tvUrl(r.symbol) + '" target="_blank" rel="noopener noreferrer" title="Open ' + esc(r.symbol) + ' on TradingView (5m)">TV ↗</a></div>' +
      '</div>' + vp +
      (STATE.bookmarks.has(r.symbol) ? '<span class="c-star" aria-label="bookmarked">★</span>' : '');
  }

  function renderLane(s, side, gridId, noteId) {
    var grid = $(gridId), syms = leadersFor(s, side);
    while (grid.children.length < 6) {
      var c = document.createElement('div'); c.className = 'card'; c.tabIndex = 0; c.setAttribute('role', 'button');
      c.addEventListener('click', onCardClick); c.addEventListener('keydown', onCardKey);
      grid.appendChild(c);
    }
    for (var i = 0; i < 6; i++) {
      var card = grid.children[i], sym = syms[i], r = sym && s.records[sym];
      var html = r ? cardHtml(r) : '<span>' + (STATE.focus ? 'NO MORE ' + side.toUpperCase() + ' IN SECTOR' : 'NO ' + side.toUpperCase() + ' RECORD') + '</span>';
      if (card._html !== html) { card.innerHTML = html; card._html = html; }
      card.dataset.symbol = r ? sym : '';
      card.classList.toggle('empty', !r);
      card.tabIndex = r ? 0 : -1;
      card.setAttribute('aria-label', r ? sym + ' ' + pct(r.dayChangePct) + ', MOM ' + dash(r.mom) + ', OB ' + dash(r.obQualityScore) + ', open chart' : 'empty');
    }
    $(noteId).textContent = STATE.focus ? 'SECTOR FILTER · GLOBAL RANK OF ' + (s.rankings[side].length) : 'GLOBAL RANK · ' + s.rankings[side].length + ' ' + side.toUpperCase();
  }
  function onCardClick(e) {
    if (e.target.closest('.c-tv')) return; // TradingView link handles itself
    var sym = e.currentTarget.dataset.symbol; if (sym) openDetail(sym);
  }
  function onCardKey(e) { if ((e.key === 'Enter' || e.key === ' ') && !e.target.closest('.c-tv')) { e.preventDefault(); onCardClick(e); } }

  function renderLeaders(s) { renderLane(s, 'bullish', 'bullCards', 'bullNote'); renderLane(s, 'bearish', 'bearCards', 'bearNote'); }

  // ------------------------------------------------------------------ picks
  function slotHtml(p) {
    if (p.empty) {
      return '<div class="s-row"><span class="s-slot">SLOT ' + p.slot + '</span>' + obPill(null, null) + '</div>' +
        '<div class="s-wait">WAITING FOR ELIGIBLE OB</div><div class="s-why">' + esc(p.note) + ' No OB WAIT stock is added without eligibility.</div>';
    }
    var why = p.present ? (p.pickPinned ? 'Pinned: ' + p.qualifyingReason + ' ' : '') + (p.obReason || '') : 'DATA WAIT · identity retained, no score shown.';
    return '<div class="s-row"><span class="s-slot">SLOT ' + p.slot + '</span><span class="s-sym">' + esc(p.symbol) + '</span>' +
      '<span class="s-px ' + cls(p.dayChangePct) + '">' + (p.ltp == null ? 'N/A' : '₹' + INR.format(p.ltp)) + ' ' + pct(p.dayChangePct) + '</span></div>' +
      '<div class="s-row c-scores">' + momPill(p.mom) + obPill(p.obQualityScore, p.obState) +
      '<span class="chip" data-s="' + esc(p.pickStatus) + '">' + esc(p.pickStatus) + '</span>' +
      (p.admissionKind === 'WATCH' ? '<span class="chip" data-s="WATCH">UNCONFIRMED</span>' : '') + '</div>' +
      '<div class="s-why" title="' + esc(why) + '">' + esc(why) + '</div>';
  }
  function renderSlots(side, data, slotsId, pendId) {
    var host = $(slotsId);
    while (host.children.length < 3) {
      var d = document.createElement('div'); d.className = 'slot';
      d.addEventListener('click', function (e) { var sym = e.currentTarget.dataset.symbol; if (sym) openDetail(sym); });
      d.addEventListener('keydown', function (e) { if ((e.key === 'Enter' || e.key === ' ') && e.currentTarget.dataset.symbol) { e.preventDefault(); openDetail(e.currentTarget.dataset.symbol); } });
      host.appendChild(d);
    }
    data.slots.forEach(function (p, i) {
      var el = host.children[i], html = slotHtml(p);
      if (el._html !== html) { el.innerHTML = html; el._html = html; }
      el.className = 'slot ' + (p.empty ? 'empty' : 'filled');
      el.dataset.symbol = p.empty ? '' : p.symbol;
      el.tabIndex = p.empty ? -1 : 0;
      el.setAttribute('role', p.empty ? 'note' : 'button');
    });
    var pend = data.pending;
    $(pendId).textContent = pend ? 'CHALLENGER ' + pend.challenger + ' (' + dash(pend.challengerScore) + ') vs ' + pend.incumbent +
      ' (' + dash(pend.incumbentScore) + ') · ' + pend.count + '/' + pend.required + ' revisions' : 'No pending challenger · replacement needs 3 new data revisions';
  }
  function renderPicks(s) { renderSlots('bullish', s.picks.bullish, 'bullSlots', 'bullPending'); renderSlots('bearish', s.picks.bearish, 'bearSlots', 'bearPending'); }

  // ------------------------------------------------------------------ detail overlay
  var chart = null;
  function navList() {
    var s = STATE.snap; if (!s) return [];
    var out = [];
    ['bullish', 'bearish'].forEach(function (side) { leadersFor(s, side).forEach(function (x) { if (out.indexOf(x) < 0) out.push(x); }); });
    ['bullish', 'bearish'].forEach(function (side) { s.picks[side].slots.forEach(function (p) { if (!p.empty && out.indexOf(p.symbol) < 0) out.push(p.symbol); }); });
    return out;
  }

  function openDetail(sym) {
    var d = STATE.detail;
    if (!d) {
      d = STATE.detail = { tf: STATE.lastTf || '5m', returnFocus: document.activeElement };
      $('detail').classList.remove('expanded'); $('dFull').setAttribute('aria-pressed', 'false');
      $('detailBackdrop').hidden = false;
      $('detail').hidden = false;
      document.body.style.overflow = 'hidden';
      if (!chart) {
        chart = new window.OFChart($('dCanvas'));
        chart.onViewport = updateStatus;
      }
      d.chart = chart;
      rays.request();
      $('dClose').focus();
    }
    d.symbol = sym; d.list = navList(); d.data = null;
    setTfButtons(d.tf);
    $('dSymbol').textContent = sym; $('dCrumbSym').textContent = sym;
    // instant header from the live snapshot while the chart request is in flight
    var r = STATE.snap && STATE.snap.records[sym];
    if (r) {
      $('dSector').textContent = r.sector;
      $('dLtp').textContent = r.ltp == null ? 'N/A' : '₹' + INR.format(r.ltp);
      var chg0 = $('dChg'); chg0.textContent = pct(r.dayChangePct); chg0.className = 'd-chg ' + cls(r.dayChangePct);
      $('dMom').textContent = dash(r.mom);
      var ms0 = $('dMomState'); ms0.textContent = r.stateChip || '—'; ms0.dataset.s = r.stateChip || '';
      $('dOb').textContent = dash(r.obQualityScore);
      var os0 = $('dObState'); os0.textContent = r.obState || '—'; os0.dataset.s = r.obState || '';
      $('dReason').textContent = 'Loading ' + d.tf + ' chart and order-block analysis…';
      $('dStats').innerHTML = '';
    }
    $('dTv').href = tvUrl(sym); $('dTv2').href = tvUrl(sym);
    var bm = STATE.bookmarks.has(sym); $('dMark').setAttribute('aria-pressed', String(bm)); $('dMark').textContent = bm ? '★' : '☆';
    loadDetail(false);
    clearInterval(d.timer);
    d.timer = setInterval(function () { loadDetail(true); }, 15000);
  }

  function closeDetail() {
    var d = STATE.detail; if (!d) return;
    clearInterval(d.timer);
    if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
    $('detail').hidden = true; $('detailBackdrop').hidden = true; document.body.style.overflow = '';
    STATE.lastTf = d.tf;
    STATE.detail = null;
    rays.request();
    if (d.returnFocus && d.returnFocus.focus) d.returnFocus.focus();
  }

  function loadDetail(keepView) {
    var d = STATE.detail; if (!d) return;
    var sym = d.symbol, tf = d.tf, seq = (d.seq = (d.seq || 0) + 1);
    if (!keepView) { $('dWait').hidden = false; $('dWait').textContent = 'Loading ' + sym + ' · ' + tf + '…'; }
    fetch('/api/symbol/' + encodeURIComponent(sym) + '?timeframe=' + encodeURIComponent(tf), { cache: 'no-store' })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || r.status); return j; }); })
      .then(function (j) {
        if (!STATE.detail || d.seq !== seq || d.symbol !== sym) return;
        d.data = j; renderDetail(j, keepView);
      })
      .catch(function (e) {
        if (!STATE.detail || d.seq !== seq) return;
        $('dWait').hidden = false; $('dWait').textContent = 'Could not load chart: ' + e.message + ' — retrying in 15 s.';
      });
  }

  function stat(label, value, klass) { return '<div><span class="micro">' + label + '</span><b class="' + (klass || '') + '">' + value + '</b></div>'; }

  function renderDetail(j, keepView) {
    var s = j.summary || {}, ob = j.ob || {}, det = j.details || {};
    $('dSector').textContent = j.sector; $('dCrumbTf').textContent = '· ' + j.timeframe.toUpperCase();
    $('dLtp').textContent = s.ltp == null ? 'N/A' : '₹' + INR.format(s.ltp);
    var chg = $('dChg'); chg.textContent = pct(s.dayChangePct); chg.className = 'd-chg ' + cls(s.dayChangePct);
    $('dMom').textContent = dash(s.mom);
    var ms = $('dMomState'); ms.textContent = s.stateChip || '—'; ms.dataset.s = s.stateChip || '';
    $('dOb').textContent = dash(ob.obQualityScore);
    var os = $('dObState'); os.textContent = ob.obState || '—'; os.dataset.s = ob.obState || '';
    var rr = det.riskReward;
    $('dStats').innerHTML =
      stat('RVOL', s.rvol == null ? 'N/A' : s.rvol.toFixed(2) + '×') +
      stat('ADR20', det.adr20 == null ? 'N/A' : det.adr20.toFixed(2) + '%') +
      stat('ATR14', det.atr14 == null ? 'N/A' : INR.format(det.atr14)) +
      stat('VWAP', det.vwap == null ? 'N/A' : INR.format(det.vwap)) +
      stat('CONVICTION', det.conviction == null ? '—' : det.conviction) +
      stat('R:R EST.', rr && rr.rr != null ? rr.rr.toFixed(2) : '—') +
      stat('Δ PROXY', det.deltaProxy == null ? 'N/A' : new Intl.NumberFormat('en-IN', { notation: 'compact' }).format(det.deltaProxy), cls(det.deltaProxy)) +
      stat('OI', esc(s.oiCaption || 'OI UNAVAILABLE'));
    var reason = '<b>OB ' + esc(ob.obState || 'WAIT') + (ob.obDirection ? ' · ' + esc(ob.obDirection) : '') + ':</b> ' + esc(ob.obReason || '');
    if (j.timeframe !== '5m' && j.dashboardOb) reason += ' <span class="dim">· Dashboard 5m OB: ' + dash(j.dashboardOb.obQualityScore) + ' ' + esc(j.dashboardOb.obState || '') + '</span>';
    if (j.demo) reason += ' <span class="dim">· DEMO synthetic data</span>';
    if (s.momentumState && s.freshReason) reason += ' <span class="dim">· ' + esc(s.freshReason) + '</span>';
    $('dReason').innerHTML = reason;
    var wait = $('dWait');
    if (!j.candles.length) { wait.hidden = false; wait.textContent = j.timing.waiting || 'Waiting for candles.'; }
    else wait.hidden = true;
    chart.setData(j, keepView);
    updateStatus(chart.vp);
  }

  function updateStatus(vp) {
    var d = STATE.detail; if (!d || !d.data) return;
    var j = d.data, n = j.candles.length;
    $('dAuto').classList.toggle('on', !!vp.auto); $('dAuto').setAttribute('aria-pressed', String(!!vp.auto));
    $('dStatus').innerHTML = n + ' / ' + j.timing.maxCandles + ' CANDLES · ' + (vp.auto ? '<span class="auto">AUTO-FOLLOW</span>' : 'MANUAL VIEW · PRESS AUTO') +
      (j.timing.provisionalCandle ? ' · LAST CANDLE PROVISIONAL' : '') + (j.timing.historyNote ? ' · ' + esc(j.timing.historyNote) : '') + (j.demo ? ' · DEMO' : '');
  }

  function patchDetailLive(s) {
    var d = STATE.detail; if (!d || !d.data) return;
    var r = s.records[d.symbol]; if (!r || r.ltp == null) return;
    $('dLtp').textContent = '₹' + INR.format(r.ltp);
    var chg = $('dChg'); chg.textContent = pct(r.dayChangePct); chg.className = 'd-chg ' + cls(r.dayChangePct);
    $('dMom').textContent = dash(r.mom);
    chart.patchLive(r.ltp);
  }

  function setTfButtons(tf) {
    Array.prototype.forEach.call($('dTf').children, function (b) { b.classList.toggle('on', b.dataset.tf === tf); b.setAttribute('aria-pressed', String(b.dataset.tf === tf)); });
  }
  $('dTf').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b || !STATE.detail) return;
    STATE.detail.tf = b.dataset.tf; setTfButtons(b.dataset.tf); loadDetail(false);
  });
  $('dAuto').addEventListener('click', function () { if (chart) { chart.setAuto(); } });
  $('dZoomIn').addEventListener('click', function () { if (chart) chart.zoomBy(0.75); });
  $('dZoomOut').addEventListener('click', function () { if (chart) chart.zoomBy(1 / 0.75); });
  $('dFit').addEventListener('click', function () { if (chart) chart.fit(); });
  function toggleFull() {
    var el = $('detail');
    if (document.fullscreenElement) document.exitFullscreen().catch(function () {});
    else if (el.requestFullscreen) el.requestFullscreen().catch(function () {});
  }
  $('dFull').addEventListener('click', function () {
    var el = $('detail'), on = !el.classList.contains('expanded');
    el.classList.toggle('expanded', on); $('dFull').setAttribute('aria-pressed', String(on));
    $('dFull').title = on ? 'Restore popup size' : 'Expand popup';
    if (chart) chart.requestRender();
  });
  $('dFull2').addEventListener('click', toggleFull);
  $('detailBackdrop').addEventListener('click', closeDetail);
  $('dClose').addEventListener('click', closeDetail);
  $('dMark').addEventListener('click', function () {
    var d = STATE.detail; if (!d) return;
    if (STATE.bookmarks.has(d.symbol)) STATE.bookmarks.delete(d.symbol); else STATE.bookmarks.add(d.symbol);
    saveBookmarks();
    var bm = STATE.bookmarks.has(d.symbol); $('dMark').setAttribute('aria-pressed', String(bm)); $('dMark').textContent = bm ? '★' : '☆';
    if (STATE.snap) renderLeaders(STATE.snap);
  });
  function step(dir) {
    var d = STATE.detail; if (!d) return;
    var list = d.list && d.list.length ? d.list : navList(); if (!list.length) return;
    var i = list.indexOf(d.symbol), next = list[(i + dir + list.length) % list.length];
    d.list = list; openDetail(next); d.list = list;
  }
  $('dPrev').addEventListener('click', function () { step(-1); });
  $('dNext').addEventListener('click', function () { step(1); });
  $('dLegend').addEventListener('click', function (e) {
    var b = e.target.closest('button'); if (!b || !chart) return;
    var on = !b.classList.contains('on'); b.classList.toggle('on', on); b.setAttribute('aria-pressed', String(on)); chart.setLayer(b.dataset.layer, on);
  });
  document.addEventListener('keydown', function (e) {
    if (!STATE.detail) return;
    if (e.key === 'Escape' && !document.fullscreenElement) closeDetail();
    else if (e.key === 'ArrowLeft' && e.altKey) step(-1);
    else if (e.key === 'ArrowRight' && e.altKey) step(1);
  });

  // ------------------------------------------------------------------ boot
  fetch('/api/state', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(render).catch(function () {}).finally(connect);
  window.OFApp = { state: STATE, openDetail: openDetail, closeDetail: closeDetail, setFocus: setFocus };
})();
