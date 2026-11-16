// Applies persisted UI preferences before first paint (theme + motion only; no market data stored).
(function () {
  var root = document.documentElement;
  try {
    var t = localStorage.getItem('of-theme');
    if (t === 'light' || t === 'dark') root.setAttribute('data-theme', t);
    var m = localStorage.getItem('of-motion');
    root.setAttribute('data-motion', m === 'off' ? 'off' : 'on');
  } catch (e) { root.setAttribute('data-motion', 'on'); }
})();
