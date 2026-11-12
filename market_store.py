"""Thread-safe in-memory market state shared by feed drivers and the scanner.

marketDataRevision increments only when a batch actually changes quote data
(price, cumulative volume, timestamp or OI). Periodic publishes and HTTP reads
never touch it.
"""
from __future__ import annotations

import math
import threading
from datetime import date, datetime, time
from typing import Callable, Iterable, Optional

from candles import SessionCandles
from fresh_momentum import MinuteTape
from market_time import is_trading_day, now_ist, previous_trading_day, to_ist
from sectors import ALL_SYMBOLS

SESSION_ROLLOVER_TIME = time(9, 0)  # new session state is prepared at 09:00 IST


def _num(x) -> Optional[float]:
    tx = type(x)
    if tx is float:
        return x if math.isfinite(x) else None
    if tx is int:
        return float(x)
    if isinstance(x, bool) or x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class MarketStore:
    def __init__(self, mode: str, clock: Callable[[], datetime] = now_ist, holidays: Iterable[date] = ()):
        self.mode = mode
        self.clock = clock
        self.holidays = set(holidays)
        self.lock = threading.RLock()
        self.quotes: dict[str, dict] = {}
        self.tapes: dict[str, MinuteTape] = {}
        self.sessions: dict[str, SessionCandles] = {}
        self.baselines: dict[str, dict] = {}
        self.mapped: set[str] = set(ALL_SYMBOLS) if mode != "kite" else set()
        self.unresolved: list[str] = []
        self.futures: dict[str, dict] = {}
        self.session_date: Optional[date] = None
        self.market_revision = 0
        self.tick_count = 0
        self.last_tick_wall: Optional[datetime] = None
        self.history = {"status": "LOADING", "loaded": 0, "target": len(self.mapped), "targetDate": None,
                        "errors": 0, "source": None}
        self.feed = {"state": "INIT", "error": None, "connected": False, "detail": None}
        self.rollover_listeners: list[Callable[[date], None]] = []

    # ------------------------------------------------------------ sessions
    def active_session_date(self, now: Optional[datetime] = None) -> date:
        """Today from 09:00 IST on a trading day; otherwise the previous trading session
        (so after-hours/holiday views show the last genuine session as stale)."""
        now = to_ist(now or self.clock())
        d = now.date()
        if is_trading_day(d, self.holidays) and now.time() >= SESSION_ROLLOVER_TIME:
            return d
        return previous_trading_day(d, self.holidays)

    def ensure_session(self, now: Optional[datetime] = None) -> bool:
        """Roll same-day state when the active NSE session changes. Returns True on rollover."""
        with self.lock:
            d = self.active_session_date(now)
            if d == self.session_date:
                return False
            self.session_date = d
            self.tapes = {s: MinuteTape(d) for s in ALL_SYMBOLS}
            self.sessions = {s: SessionCandles(d) for s in ALL_SYMBOLS}
            for q in self.quotes.values():
                q["oiBaseline"] = None  # per-connection/per-session OI reference resets
            # Baselines are target-date specific; they are recomputed for the new session.
            self.baselines.clear()
            self.history.update(status="LOADING", loaded=0, targetDate=d.isoformat())
            listeners = list(self.rollover_listeners)
        for fn in listeners:
            try:
                fn(d)
            except Exception:  # listeners must not break the feed
                pass
        return True

    # ------------------------------------------------------------ writes
    def apply_batch(self, updates: list[dict], *, received: Optional[datetime] = None, bump: bool = True) -> int:
        """updates: {symbol, ltp, prevClose?, open?, high?, low?, volume?, avgPrice?, ts, tsSource}
        Returns the number of symbols whose data changed."""
        with self.lock:
            now = to_ist(received or self.clock())
            self.ensure_session(now)
            changed: set[str] = set()
            for u in updates:
                sym = u.get("symbol")
                if sym not in self.mapped:
                    continue
                ltp = _num(u.get("ltp"))
                if ltp is None or ltp <= 0:
                    continue
                ts = to_ist(u.get("ts") or now)
                q = self.quotes.setdefault(sym, {"symbol": sym})
                if q.get("ts") is not None and ts < q["ts"]:
                    continue  # out-of-order quote: ignore
                vol = _num(u.get("volume"))
                if vol is not None and q.get("volume") is not None and vol < q["volume"] and q.get("sessionDate") == ts.date():
                    vol = q["volume"]  # cumulative volume never decreases within a session
                before = (q.get("ltp"), q.get("volume"), q.get("ts"))
                q["ltp"] = ltp
                for key in ("prevClose", "open", "high", "low", "avgPrice"):
                    val = _num(u.get(key))
                    if val is not None and val > 0:
                        q[key] = val
                if vol is not None:
                    q["volume"] = vol
                elif q.get("sessionDate") != ts.date():
                    q["volume"] = None
                q["ts"] = ts
                q["tsSource"] = u.get("tsSource", "RECEIVED")
                q["sessionDate"] = ts.date()
                if (q["ltp"], q.get("volume"), q["ts"]) != before:
                    changed.add(sym)
                if ts.date() == self.session_date:
                    self.tapes[sym].add(ts, ltp, q.get("volume"))
                    self.sessions[sym].on_tick(ts, ltp, q.get("volume"))
            self.tick_count += len(updates)
            if updates:
                self.last_tick_wall = now
            if changed and bump:
                self.market_revision += 1
            return len(changed)

    def apply_market_update(self, quotes: list[dict], oi_updates: list[dict], *,
                            received: Optional[datetime] = None) -> int:
        """One feed message / ingest batch / demo step = at most ONE market-data revision,
        however many quote and OI fields changed inside it."""
        with self.lock:
            n_q = self.apply_batch(quotes, received=received, bump=False) if quotes else 0
            n_oi = self.apply_oi(oi_updates, bump=False) if oi_updates else 0
            if n_q or n_oi:
                self.market_revision += 1
            return n_q + n_oi

    def apply_oi(self, updates: list[dict], *, bump: bool = True) -> int:
        """updates: {symbol, oi, ts}. The OI baseline is the first value observed since
        connect for the current session, NOT previous-close OI."""
        with self.lock:
            changed = 0
            for u in updates:
                sym = u.get("symbol")
                oi = _num(u.get("oi"))
                if sym not in self.mapped or oi is None or oi < 0:
                    continue
                q = self.quotes.setdefault(sym, {"symbol": sym})
                if q.get("oiBaseline") is None:
                    q["oiBaseline"] = oi
                if q.get("oi") != oi:
                    changed += 1
                q["oi"] = oi
                q["oiTs"] = to_ist(u.get("ts") or self.clock())
            if changed and bump:
                self.market_revision += 1
            return changed

    def merge_session_history(self, symbol: str, candles: list[dict]) -> int:
        with self.lock:
            sc = self.sessions.get(symbol)
            return sc.merge_history(candles) if sc else 0

    def set_baseline(self, symbol: str, baseline: dict) -> None:
        with self.lock:
            if self.session_date and baseline.get("cacheDate") != self.session_date.isoformat():
                return  # baseline computed for a different target session
            self.baselines[symbol] = baseline
            self.history["loaded"] = len(self.baselines)
            self.history["status"] = "READY" if self.history["loaded"] >= self.history["target"] else "PARTIAL"

    def clear_baselines(self) -> None:
        with self.lock:
            self.baselines.clear()
            self.history.update(status="LOADING", loaded=0)

    def set_mapping(self, mapped: Iterable[str], unresolved: Iterable[str], futures: dict[str, dict]) -> None:
        with self.lock:
            self.mapped = set(mapped)
            self.unresolved = sorted(unresolved)
            self.futures = dict(futures)
            self.history["target"] = len(self.mapped)

    def set_feed(self, **kw) -> None:
        with self.lock:
            self.feed.update(kw)
