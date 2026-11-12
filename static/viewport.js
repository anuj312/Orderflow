/* Pure chart-viewport math (no DOM). Works in the browser (window.OFViewport) and Node (tests).
 * A viewport is {start, end, auto} in fractional bar-index units; bar i occupies [i, i+1).
 * AUTO keeps the right edge pinned to the newest bar. Any manual pan/zoom turns AUTO off
 * until setAuto() is called again. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.OFViewport = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  var MIN_SPAN = 12;
  var DEFAULT_SPAN = 90;
  var RIGHT_PAD = 3;

  function maxSpan(n) { return Math.max(MIN_SPAN, n + RIGHT_PAD); }

  function clamp(vp, n) {
    var span = Math.min(Math.max(vp.end - vp.start, MIN_SPAN), Math.max(maxSpan(n), MIN_SPAN));
    var start = vp.start, end = start + span;
    var lo = -span * 0.5, hi = n + span * 0.5;
    if (start < lo) { start = lo; end = start + span; }
    if (end > hi) { end = hi; start = end - span; }
    return { start: start, end: end, auto: !!vp.auto };
  }

  function fit(n, span) {
    span = Math.min(span || DEFAULT_SPAN, maxSpan(n));
    var end = n + RIGHT_PAD;
    return clamp({ start: end - span, end: end, auto: true }, n);
  }

  function setAuto(vp, n) {
    var span = vp.end - vp.start;
    var end = n + RIGHT_PAD;
    return clamp({ start: end - span, end: end, auto: true }, n);
  }

  function zoom(vp, n, factor, anchorFrac) {
    if (anchorFrac == null) anchorFrac = 1;
    anchorFrac = Math.min(1, Math.max(0, anchorFrac));
    var span = vp.end - vp.start;
    var next = Math.min(Math.max(span * factor, MIN_SPAN), maxSpan(n));
    var anchor = vp.start + span * anchorFrac;
    var start = anchor - next * anchorFrac;
    return clamp({ start: start, end: start + next, auto: false }, n);
  }

  function pan(vp, n, deltaBars) {
    if (!deltaBars) return vp;
    return clamp({ start: vp.start + deltaBars, end: vp.end + deltaBars, auto: false }, n);
  }

  /* New data arrived. `shift` = how many bars were dropped from the front of the array
   * (the window keeps the latest ~90 bars), so manual viewports stay on the same candles. */
  function onData(vp, n, shift) {
    if (vp.auto) return setAuto(vp, n);
    shift = shift || 0;
    return clamp({ start: vp.start - shift, end: vp.end - shift, auto: false }, n);
  }

  function visibleRange(vp, n) {
    var i0 = Math.max(0, Math.floor(vp.start));
    var i1 = Math.min(n - 1, Math.ceil(vp.end) - 1);
    return [i0, i1];
  }

  return { MIN_SPAN: MIN_SPAN, DEFAULT_SPAN: DEFAULT_SPAN, RIGHT_PAD: RIGHT_PAD, clamp: clamp, fit: fit,
           setAuto: setAuto, zoom: zoom, pan: pan, onData: onData, visibleRange: visibleRange };
});
