"""Offline DEMO feed. Every number it produces is SYNTHETIC and labelled DEMO.

A virtual NSE session clock advances one virtual minute per demo step (~4 s). Prices
are a deterministic seeded random walk with regime drift and occasional impulses, so
the real calculation pipeline (tape, candles, baselines, OB engine) runs on fictional
data. When the virtual clock reaches 15:30 the demo rolls into the next trading day,
which exercises the session-rollover path.
"""
from __future__ import annotations

import logging
import math
import random
import threading
import zlib
from datetime import date, datetime, timedelta
from typing import Optional

from history_intelligence import build_baseline
from market_time import (SESSION_MINUTES, epoch, is_trading_day, next_trading_day, now_ist,
                         previous_trading_day, session_open_dt)
from sectors import ALL_SYMBOLS, SYMBOL_TO_SECTOR

log = logging.getLogger("orderflow.demo")
HISTORY_SESSIONS = 20


def _seed(*parts) -> int:
    return zlib.crc32("|".join(str(p) for p in parts).encode())


def _ushape(slot: float) -> float:
    return 0.7 + 1.1 * math.exp(-slot / 8.0) + 0.6 * math.exp(-(74 - slot) / 6.0)


_USHAPE = [_ushape(k) for k in range(75)]


class DemoFeed:
    def __init__(self, store, *, interval: float = 4.0, start_minute: int = 135, holidays=()):
        self.store = store
        self.interval = interval
        self.holidays = set(holidays)
        today = now_ist().date()
        self.vdate = today if is_trading_day(today, self.holidays) else previous_trading_day(today, self.holidays)
        self.vminute = max(0, min(SESSION_MINUTES - 1, int(start_minute)))
        self.start_minute = self.vminute
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.params = {s: self._params(s) for s in ALL_SYMBOLS}
        self._close: dict[tuple[str, date], float] = {}
        self._overrides: dict[date, dict[str, list[dict]]] = {}
        self._today: dict[str, list[tuple]] = {}
        self._oi: dict[str, list[float]] = {}
        self._acc: dict[str, list[tuple]] = {}
        self._fed_minute = -1
        self.history_dates: list[date] = []
        store.clock = self.clock

    # ------------------------------------------------------------ clock
    def clock(self) -> datetime:
        # Lock-free on purpose: the store calls this while holding its own lock, and the
        # demo thread takes the store lock while holding self._lock (avoid lock inversion).
        d, m = self.vdate, min(self.vminute, SESSION_MINUTES - 1)
        return session_open_dt(d) + timedelta(minutes=m, seconds=50)

    # ------------------------------------------------------------ synthetic parameters
    def _params(self, sym: str) -> dict:
        r = random.Random(_seed(sym, "params"))
        price = math.exp(r.uniform(math.log(60), math.log(9000)))
        return {"base": round(price, 2), "adr": r.uniform(1.3, 3.8), "vol": int(10 ** r.uniform(5.0, 7.2)),
                "hasFut": _seed(sym, "fut") % 100 < 88, "oi": int(10 ** r.uniform(5.5, 7.5))}

    def _day_bias(self, sym: str, d: date) -> float:
        sec = random.Random(_seed(SYMBOL_TO_SECTOR[sym], d, "sector")).gauss(0, 1)
        idio = random.Random(_seed(sym, d, "idio")).gauss(0, 1)
        return 0.55 * sec + 0.75 * idio

    # ------------------------------------------------------------ history sessions (5m)
    def _gen_session_5m(self, sym: str, d: date, prev_close: float) -> list[dict]:
        p = self.params[sym]
        r = random.Random(_seed(sym, d, "5m"))
        gauss, rnd = r.gauss, r.random
        sig = p["adr"] / 100.0 / 36.0 * math.sqrt(5)
        bias = self._day_bias(sym, d) * sig * 0.22
        price = prev_close * (1 + gauss(0, p["adr"] / 100.0 * 0.15))
        out, drift, left = [], 0.0, 0
        t0 = epoch(session_open_dt(d))
        vbase = p["vol"] / 75
        wick = sig * 0.45
        for k in range(75):
            if left <= 0:
                drift, left = gauss(0, 0.55) * sig + bias, 3 + int(rnd() * 7)
            left -= 1
            ret = drift + gauss(0, sig)
            o = price
            c = max(0.05, o * (1 + ret))
            h = (o if o > c else c) * (1 + rnd() * wick * 1.6)
            l = (c if o > c else o) * (1 - rnd() * wick * 1.6)
            v = int(vbase * _USHAPE[k] * math.exp((rnd() - 0.5) * 1.2) * (1 + 1.5 * abs(ret) / sig))
            out.append({"t": t0 + 300 * k, "o": round(o, 2), "h": round(h, 2), "l": round(l, 2), "c": round(c, 2),
                        "v": v, "complete": True, "src": "DEMO"})
            price = c
        return out

    def _session(self, sym: str, d: date) -> list[dict]:
        over = self._overrides.get(d, {}).get(sym)
        if over is not None:
            return over
        prev_d = previous_trading_day(d, self.holidays)
        prev_close = self._close.get((sym, prev_d), self.params[sym]["base"])
        candles = self._gen_session_5m(sym, d, prev_close)
        self._close[(sym, d)] = candles[-1]["c"]
        return candles

    def _history_dates_for(self, d: date, n: int = HISTORY_SESSIONS) -> list[date]:
        out, cur = [], d
        for _ in range(n):
            cur = previous_trading_day(cur, self.holidays)
            out.append(cur)
        return list(reversed(out))

    def chart_history(self, sym: str, sessions: int) -> list[dict]:
        """Previous complete synthetic sessions (oldest first), consistent with baselines."""
        with self._lock:
            dates = self.history_dates[-sessions:] if sessions > 0 else []
            out = []
            for d in self.history_dates:
                c = self._session(sym, d)  # chronological walk keeps the close chain consistent
                if d in dates:
                    out.extend(dict(x) for x in c)
            return out

    def _build_history(self) -> None:
        with self._lock:
            target = self.vdate
            dates = self.history_dates
        self.store.set_feed(detail="Building synthetic 20-session baselines")
        for i, sym in enumerate(ALL_SYMBOLS):
            if self._stop.is_set():
                return
            with self._lock:
                if self.vdate != target:
                    return
                intraday = []
                daily = []
                for d in dates:
                    c = self._session(sym, d)
                    intraday.extend(c)
                    daily.append({"date": d, "o": c[0]["o"], "h": max(x["h"] for x in c),
                                  "l": min(x["l"] for x in c), "c": c[-1]["c"], "v": sum(x["v"] for x in c)})
            self.store.set_baseline(sym, build_baseline(sym, daily, intraday, target))
        self.store.history["source"] = "DEMO_SYNTHETIC"
        self.store.set_feed(detail="Synthetic baselines ready")

    # ------------------------------------------------------------ today (1m)
    def _gen_today(self) -> None:
        d = self.vdate
        last_hist = self.history_dates[-1]
        self._today.clear()
        self._oi.clear()
        self._acc.clear()
        for sym in ALL_SYMBOLS:
            p = self.params[sym]
            r = random.Random(_seed(sym, d, "1m"))
            sig = p["adr"] / 100.0 / 36.0
            bias = self._day_bias(sym, d) * sig * 0.22
            prev_close = self._close.get((sym, last_hist), p["base"])
            price = prev_close * (1 + r.gauss(0, p["adr"] / 100.0 * 0.15))
            path, drift, left = [], 0.0, 0
            oi = float(p["oi"])
            corr = 1.0 if r.random() < 0.6 else -1.0
            ois = []
            gauss, rnd = r.gauss, r.random
            trend = 0.0
            pullback_left = 0
            vbase = p["vol"] / SESSION_MINUTES
            for m in range(SESSION_MINUTES):
                impulse = 1.0
                if left <= 0:
                    trend = gauss(0, 0.6) * sig + bias
                    drift, left = trend, 6 + int(rnd() * 22)
                left -= 1
                if pullback_left > 0:
                    # synthetic pullback after a displacement leg, so zones get retested
                    pullback_left -= 1
                    if pullback_left == 0:
                        drift = trend
                ret = drift + gauss(0, sig)
                if rnd() < 0.03 and pullback_left == 0:  # occasional displacement minute
                    impulse = 2.5 + rnd() * 2.0
                    direction = 1.0 if (trend if trend else ret) >= 0 else -1.0
                    ret = direction * (abs(ret) * impulse + sig * impulse)
                    pullback_left = 6 + int(rnd() * 10)
                    drift = -direction * sig * (0.5 + rnd() * 0.5)
                    left = max(left, pullback_left + 8)
                o = price
                c = max(0.05, o * (1 + ret))
                h = (o if o > c else c) * (1 + rnd() * sig * 0.55)
                l = (c if o > c else o) * (1 - rnd() * sig * 0.55)
                v = int(vbase * _USHAPE[min(74, m // 5)] * math.exp((rnd() - 0.5) * 1.3)
                        * (1 + 1.5 * abs(ret) / sig) * (2.5 if impulse > 1 else 1.0))
                path.append((round(o, 2), round(h, 2), round(l, 2), round(c, 2), max(1, v)))
                oi = max(1.0, oi * (1 + (rnd() - 0.5) * 0.002 + 0.3 * corr * ret))
                ois.append(round(oi))
                price = c
            self._today[sym] = path
            self._oi[sym] = ois
            acc, hi, lo, cum, pv = [], -math.inf, math.inf, 0, 0.0
            for (o, h, l, c, v) in path:
                hi, lo = max(hi, h), min(lo, l)
                pv += (h + l + c) / 3 * v
                acc.append((hi, lo, cum, round(pv / (cum + v), 2)))
                cum += v
            self._acc[sym] = acc

    def _minute_updates(self, m: int) -> tuple[list[dict], list[dict]]:
        base = session_open_dt(self.vdate) + timedelta(minutes=m)
        updates, oi_updates = [], []
        for sym in ALL_SYMBOLS:
            path = self._today[sym]
            o, h, l, c, v = path[m]
            seq = (o, l, h, c) if c >= o else (o, h, l, c)
            day_o = path[0][0]
            prev = self._close.get((sym, self.history_dates[-1]), self.params[sym]["base"])
            hi, lo, cum_before, atp = self._acc[sym][m]
            run_hi, run_lo = (self._acc[sym][m - 1][:2] if m > 0 else (o, o))
            for i, px in enumerate(seq):
                run_hi, run_lo = max(run_hi, px), min(run_lo, px)
                updates.append({"symbol": sym, "ltp": px, "prevClose": prev, "open": day_o, "high": run_hi,
                                "low": run_lo, "volume": cum_before + int(v * (i + 1) / 4), "avgPrice": atp,
                                "ts": base + timedelta(seconds=15 * i), "tsSource": "DEMO"})
            if self.params[sym]["hasFut"]:
                oi_updates.append({"symbol": sym, "oi": self._oi[sym][m], "ts": base})
        updates.sort(key=lambda u: u["ts"])
        return updates, oi_updates

    def _feed_minute(self, m: int) -> None:
        ups, ois = self._minute_updates(m)
        self.store.apply_market_update(ups, ois, received=ups[-1]["ts"] + timedelta(seconds=5))
        self._fed_minute = m

    def _prepare_day(self) -> None:
        """Roll the store to the virtual session, rebuild synthetic baselines (this also
        warms the close chain), then generate today's 1m path."""
        with self._lock:
            self.history_dates = self._history_dates_for(self.vdate)
        self.store.ensure_session(self.clock())
        self._build_history()
        with self._lock:
            self._gen_today()

    # ------------------------------------------------------------ lifecycle
    def bootstrap(self) -> None:
        """Synchronous setup: prepare the virtual day and replay the session up to the start minute."""
        self.store.set_feed(state="DEMO", connected=True, error=None, detail="Synthetic demo feed")
        self._prepare_day()
        for m in range(0, self.vminute + 1):
            self._feed_minute(m)

    def step(self) -> None:
        with self._lock:
            if self.vminute + 1 >= SESSION_MINUTES:
                self._rollover()
                return
            self.vminute += 1
            m = self.vminute
        self._feed_minute(m)

    def _rollover(self) -> None:
        """Persist the finished synthetic session, then move to the next trading day."""
        finished = self.vdate
        now = self.clock() + timedelta(minutes=10)
        sessions = {}
        for sym in ALL_SYMBOLS:
            sc = self.store.sessions.get(sym)
            cs = [dict(c, complete=True) for c in (sc.candles(now) if sc else [])]
            if cs:
                sessions[sym] = cs
                self._close[(sym, finished)] = cs[-1]["c"]
        self._overrides[finished] = sessions
        for old in sorted(self._overrides)[:-HISTORY_SESSIONS]:
            del self._overrides[old]
        self.vdate = next_trading_day(finished, self.holidays)
        self.vminute = 0
        self._prepare_day()
        self._feed_minute(0)

    def _run(self) -> None:
        try:
            self.bootstrap()
        except Exception:
            log.exception("demo bootstrap failed")
            self.store.set_feed(state="ERROR", error="Demo bootstrap failed; see server log.")
            return
        while not self._stop.wait(self.interval):
            try:
                self.step()
            except Exception:  # keep demo alive; log without data
                log.exception("demo step failed")

    def start(self) -> None:
        """Non-blocking: the HTTP server is up immediately and shows LOADING states."""
        self.store.set_feed(state="DEMO", connected=False, error=None, detail="Preparing synthetic session")
        self._thread = threading.Thread(target=self._run, name="demo-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
