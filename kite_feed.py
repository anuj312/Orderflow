"""Zerodha Kite Connect market feed (read-only).

- Resolves exact NSE EQ instruments for ALL_SYMBOLS and the nearest non-expired NFO
  stock future per symbol; unresolved symbols are reported, never substituted.
- One KiteTicker WebSocket: cash in QUOTE mode, futures in FULL mode (for OI).
- Serial, throttled (~1.8 req/s) historical loader with retry/backoff that builds the
  20-session baselines and seeds today's completed 5m candles.
- Credentials stay server-side; every error string is redacted before it is stored.
No order placement or account mutation is ever performed.
"""
from __future__ import annotations

import heapq
import itertools
import logging
import threading
import time
from collections import OrderedDict, deque
from datetime import date, datetime, timedelta
from typing import Callable, Iterable, Optional

from history_intelligence import build_baseline, load_cache, save_cache
from market_time import IST, epoch, now_ist, session_open_dt, to_ist
from sectors import ALL_SYMBOLS

log = logging.getLogger("orderflow.kite")
HISTORY_RATE_PER_SEC = 1.8
QUOTE_BATCH = 200
CHART_CACHE_SIZE = 2


class AuthError(Exception):
    pass


# ------------------------------------------------------------------ pure helpers

def resolve_equities(instruments: Iterable[dict], symbols: Iterable[str]) -> tuple[dict[str, dict], list[str]]:
    wanted = set(symbols)
    found: dict[str, dict] = {}
    for ins in instruments:
        ts = ins.get("tradingsymbol")
        if ts in wanted and ins.get("exchange") == "NSE" and ins.get("segment") == "NSE" \
                and ins.get("instrument_type") == "EQ":
            found[ts] = {"token": int(ins["instrument_token"]), "tradingsymbol": ts}
    return found, sorted(wanted - set(found))


def _as_date(x) -> Optional[date]:
    if x is None or x == "":
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def nearest_futures(instruments: Iterable[dict], symbols: Iterable[str], today: date) -> dict[str, dict]:
    wanted = set(symbols)
    best: dict[str, dict] = {}
    for ins in instruments:
        name = ins.get("name")
        if name not in wanted or ins.get("segment") != "NFO-FUT" or ins.get("instrument_type") != "FUT":
            continue
        exp = _as_date(ins.get("expiry"))
        if exp is None or exp < today:
            continue
        cur = best.get(name)
        if cur is None or exp < cur["expiry"]:
            best[name] = {"token": int(ins["instrument_token"]), "tradingsymbol": ins["tradingsymbol"], "expiry": exp}
    return best


def kite_rows_to_candles(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        d = r.get("date")
        if not isinstance(d, datetime):
            continue
        v = r.get("volume")
        out.append({"t": epoch(to_ist(d)), "o": float(r["open"]), "h": float(r["high"]), "l": float(r["low"]),
                    "c": float(r["close"]), "v": int(v) if v is not None else None, "complete": True, "src": "HIST"})
    return out


def kite_rows_to_daily(rows: Iterable[dict]) -> list[dict]:
    out = []
    for r in rows:
        d = r.get("date")
        if not isinstance(d, (datetime, date)):
            continue
        out.append({"date": to_ist(d).date() if isinstance(d, datetime) else d, "o": float(r["open"]),
                    "h": float(r["high"]), "l": float(r["low"]), "c": float(r["close"]), "v": r.get("volume")})
    return out


def tick_to_updates(ticks: Iterable[dict], token_map: dict[int, tuple[str, str]], received: datetime
                    ) -> tuple[list[dict], list[dict]]:
    """Convert KiteTicker ticks. token_map: token -> (symbol, 'EQ'|'FUT')."""
    eq, oi = [], []
    for t in ticks:
        info = token_map.get(t.get("instrument_token"))
        if not info:
            continue
        sym, kind = info
        ts = t.get("exchange_timestamp") or t.get("last_trade_time")
        src = "EXCHANGE"
        if isinstance(ts, datetime):
            ts = to_ist(ts)
            if ts > received + timedelta(seconds=5) or ts < received - timedelta(days=7):
                ts, src = received, "RECEIVED_TS_INVALID"
        else:
            ts, src = received, "RECEIVED"
        if kind == "EQ":
            ohlc = t.get("ohlc") or {}
            eq.append({"symbol": sym, "ltp": t.get("last_price"), "prevClose": ohlc.get("close"),
                       "open": ohlc.get("open"), "high": ohlc.get("high"), "low": ohlc.get("low"),
                       "volume": t.get("volume_traded"), "avgPrice": t.get("average_traded_price"),
                       "ts": ts, "tsSource": src})
        elif t.get("oi") is not None:
            oi.append({"symbol": sym, "oi": t.get("oi"), "ts": ts})
    return eq, oi


class RateLimiter:
    def __init__(self, rate_per_sec: float = HISTORY_RATE_PER_SEC, sleep: Callable[[float], None] = time.sleep):
        self.interval = 1.0 / rate_per_sec
        self._lock = threading.Lock()
        self._next = 0.0
        self._sleep = sleep

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            at = max(now, self._next)
            self._next = at + self.interval
        if at > now:
            self._sleep(at - now)


def call_with_retry(fn: Callable, *, retries: int = 4, base_delay: float = 1.0,
                    sleep: Callable[[float], None] = time.sleep):
    from kiteconnect import exceptions as kex
    for attempt in range(retries + 1):
        try:
            return fn()
        except (kex.TokenException, kex.PermissionException) as e:
            raise AuthError(type(e).__name__) from None
        except kex.InputException:
            raise
        except (kex.NetworkException, kex.DataException, kex.GeneralException, ConnectionError, TimeoutError):
            if attempt >= retries:
                raise
            sleep(base_delay * (2 ** attempt))


# ------------------------------------------------------------------ feed driver

class KiteFeed:
    def __init__(self, store, api_key: str, access_token: str, *, cache_dir: str = "",
                 history_only: bool = False, kite_factory=None, ticker_factory=None):
        self.store = store
        self._api_key = api_key
        self._access_token = access_token
        self.cache_dir = cache_dir
        self.history_only = history_only
        self._kite_factory = kite_factory
        self._ticker_factory = ticker_factory
        self.kite = None
        self.ticker = None
        self.eq: dict[str, dict] = {}
        self.fut: dict[str, dict] = {}
        self.token_map: dict[int, tuple[str, str]] = {}
        self.limiter = RateLimiter()
        self.errors: deque = deque(maxlen=20)
        self._stop = threading.Event()
        self._subscribed = False
        self._hq: list = []
        self._hq_lock = threading.Lock()
        self._hq_seq = itertools.count()
        self._hq_event = threading.Event()
        self._done: set[str] = set()
        self._history_target: Optional[date] = None
        self._cache: dict[str, dict] = {}
        self._chart_cache: "OrderedDict[tuple, list]" = OrderedDict()
        self._chart_lock = threading.Lock()
        store.rollover_listeners.append(self._on_rollover)

    # ---------------------------------------------------------- safety
    def redact(self, msg: str) -> str:
        msg = str(msg)
        for secret in (self._api_key, self._access_token):
            if secret:
                msg = msg.replace(secret, "***")
        return msg[:300]

    def _error(self, where: str, exc: BaseException | str) -> None:
        text = self.redact(f"{where}: {exc if isinstance(exc, str) else type(exc).__name__ + ' ' + str(exc)}")
        self.errors.append({"at": now_ist().isoformat(timespec="seconds"), "error": text})
        log.warning("kite %s", text)

    # ---------------------------------------------------------- bootstrap
    def start(self) -> None:
        threading.Thread(target=self._bootstrap, name="kite-bootstrap", daemon=True).start()

    def _bootstrap(self) -> None:
        try:
            if self._kite_factory:
                self.kite = self._kite_factory()
            else:
                from kiteconnect import KiteConnect
                self.kite = KiteConnect(api_key=self._api_key)
                self.kite.set_access_token(self._access_token)
            self.store.set_feed(state="LOADING_INSTRUMENTS", error=None, detail="Downloading instrument master")
            nse = call_with_retry(lambda: self.kite.instruments("NSE"))
            nfo = call_with_retry(lambda: self.kite.instruments("NFO"))
            self.eq, unresolved = resolve_equities(nse, ALL_SYMBOLS)
            self.fut = nearest_futures(nfo, self.eq.keys(), now_ist().date())
            self.token_map = {v["token"]: (s, "EQ") for s, v in self.eq.items()}
            self.token_map.update({v["token"]: (s, "FUT") for s, v in self.fut.items()})
            if unresolved:
                self._error("instruments", f"unresolved NSE EQ symbols: {', '.join(unresolved)}")
            if not self.history_only:
                self.store.set_mapping(self.eq.keys(), unresolved,
                                       {s: {"tradingsymbol": f["tradingsymbol"], "expiry": f["expiry"].isoformat()}
                                        for s, f in self.fut.items()})
                self.store.ensure_session()
                self._initial_quotes()
                self._start_ticker()
            else:
                with self.store.lock:
                    self.store.unresolved = unresolved
                    self.store.history["target"] = len(self.eq)
                self.store.ensure_session()
            threading.Thread(target=self._history_loop, name="kite-history", daemon=True).start()
        except AuthError:
            self.store.set_feed(state="AUTH_ERROR", connected=False,
                                error="Kite rejected the credentials (access token expired or invalid). "
                                      "Generate a new access token via the Kite login flow and restart.")
        except Exception as e:
            self._error("bootstrap", e)
            self.store.set_feed(state="ERROR", connected=False, error=self.redact(f"Kite bootstrap failed: {type(e).__name__}"))

    def _initial_quotes(self) -> None:
        """One-off REST snapshot so prev-close/OHLC/volume are known before ticks arrive."""
        syms = sorted(self.eq)
        received = now_ist()
        for i in range(0, len(syms), QUOTE_BATCH):
            chunk = syms[i:i + QUOTE_BATCH]
            self.limiter.wait()
            data = call_with_retry(lambda: self.kite.quote([f"NSE:{s}" for s in chunk]))
            ups = []
            for s in chunk:
                row = data.get(f"NSE:{s}")
                if not row:
                    continue
                ts = row.get("timestamp") or row.get("last_trade_time")
                ohlc = row.get("ohlc") or {}
                ups.append({"symbol": s, "ltp": row.get("last_price"), "prevClose": ohlc.get("close"),
                            "open": ohlc.get("open"), "high": ohlc.get("high"), "low": ohlc.get("low"),
                            "volume": row.get("volume"), "avgPrice": row.get("average_price"),
                            "ts": to_ist(ts) if isinstance(ts, datetime) else received,
                            "tsSource": "EXCHANGE_SNAPSHOT" if isinstance(ts, datetime) else "RECEIVED"})
            self.store.apply_batch(ups, received=received)
        futs = sorted(self.fut)
        for i in range(0, len(futs), QUOTE_BATCH):
            chunk = futs[i:i + QUOTE_BATCH]
            self.limiter.wait()
            data = call_with_retry(lambda: self.kite.quote([f"NFO:{self.fut[s]['tradingsymbol']}" for s in chunk]))
            ois = [{"symbol": s, "oi": (data.get(f"NFO:{self.fut[s]['tradingsymbol']}") or {}).get("oi"), "ts": received}
                   for s in chunk]
            self.store.apply_oi([o for o in ois if o["oi"] is not None])

    # ---------------------------------------------------------- websocket
    def _start_ticker(self) -> None:
        if self._ticker_factory:
            t = self._ticker_factory()
        else:
            from kiteconnect import KiteTicker
            t = KiteTicker(self._api_key, self._access_token, reconnect=True, reconnect_max_tries=300,
                           reconnect_max_delay=60)
        t.on_ticks = self._on_ticks
        t.on_connect = self._on_connect
        t.on_close = self._on_close
        t.on_error = self._on_error
        t.on_reconnect = self._on_reconnect
        t.on_noreconnect = self._on_noreconnect
        self.ticker = t
        self.store.set_feed(state="CONNECTING", detail="Opening KiteTicker WebSocket")
        t.connect(threaded=True)

    def _on_connect(self, ws, response=None) -> None:
        eq_tokens = [v["token"] for v in self.eq.values()]
        fut_tokens = [v["token"] for v in self.fut.values()]
        if not self._subscribed:  # KiteTicker re-subscribes by itself after reconnects
            ws.subscribe(eq_tokens + fut_tokens)
            ws.set_mode(ws.MODE_QUOTE, eq_tokens)
            if fut_tokens:
                ws.set_mode(ws.MODE_FULL, fut_tokens)
            self._subscribed = True
        self.store.set_feed(state="CONNECTED", connected=True, error=None,
                            detail=f"{len(eq_tokens)} NSE EQ (QUOTE) + {len(fut_tokens)} NFO FUT (FULL)")

    def _on_ticks(self, ws, ticks) -> None:
        try:
            eq, oi = tick_to_updates(ticks, self.token_map, now_ist())
            if eq or oi:
                self.store.apply_market_update(eq, oi)
        except Exception as e:
            self._error("ticks", e)

    def _on_close(self, ws, code, reason) -> None:
        self.store.set_feed(state="DISCONNECTED", connected=False, detail=self.redact(f"WebSocket closed ({code})"))
        if code == 1006 and "403" in str(reason):
            self._auth_failed(ws)

    def _on_error(self, ws, code, reason) -> None:
        self._error("websocket", f"code {code} {reason}")
        if "403" in str(reason) or "TokenException" in str(reason):
            self._auth_failed(ws)

    def _auth_failed(self, ws) -> None:
        self.store.set_feed(state="AUTH_ERROR", connected=False,
                            error="KiteTicker authentication failed (token expired?). Renew the access token and restart.")
        try:
            ws.stop_retry()
        except Exception:
            pass

    def _on_reconnect(self, ws, attempts) -> None:
        self.store.set_feed(state="RECONNECTING", connected=False, detail=f"Reconnect attempt {attempts}")

    def _on_noreconnect(self, ws) -> None:
        self.store.set_feed(state="DISCONNECTED", connected=False, error="KiteTicker gave up reconnecting.")

    # ---------------------------------------------------------- history
    def _on_rollover(self, d: date) -> None:
        self._hq_event.set()

    def prioritize(self, symbols: Iterable[str]) -> None:
        with self._hq_lock:
            for s in symbols:
                if s in self.eq and s not in self._done:
                    heapq.heappush(self._hq, (0, next(self._hq_seq), s))
        self._hq_event.set()

    def _schedule(self, target: date) -> None:
        with self._hq_lock:
            self._history_target = target
            self._done.clear()
            self._hq = [(1, next(self._hq_seq), s) for s in sorted(self.eq)]
            heapq.heapify(self._hq)
        self._cache = load_cache(self.cache_dir, target, "kite")
        self.store.clear_baselines()
        self.store.history["source"] = "KITE_HISTORICAL"

    def _history_loop(self) -> None:
        loaded_since_save = 0
        while not self._stop.is_set():
            target = self.store.session_date
            if target is None:
                self._stop.wait(1)
                continue
            if target != self._history_target:
                self._schedule(target)
            with self._hq_lock:
                item = heapq.heappop(self._hq) if self._hq else None
            if item is None:
                if loaded_since_save:
                    self._save_cache(target)
                    loaded_since_save = 0
                self._hq_event.wait(5)
                self._hq_event.clear()
                continue
            sym = item[2]
            if sym in self._done:
                continue
            try:
                self._load_symbol(sym, target)
                self._done.add(sym)
                loaded_since_save += 1
                if loaded_since_save >= 25:
                    self._save_cache(target)
                    loaded_since_save = 0
            except AuthError:
                self.store.set_feed(state="AUTH_ERROR", connected=False,
                                    error="Kite historical API rejected the token; history loading stopped.")
                return
            except Exception as e:
                self.store.history["errors"] = self.store.history.get("errors", 0) + 1
                self._error(f"history {sym}", e)
                self._done.add(sym)  # do not hammer the API; reported in health

    def _fetch(self, token: int, frm: datetime, to: datetime, interval: str) -> list[dict]:
        self.limiter.wait()
        return call_with_retry(lambda: self.kite.historical_data(token, frm, to, interval))

    def _load_symbol(self, sym: str, target: date) -> None:
        token = self.eq[sym]["token"]
        now = now_ist()
        start_today = session_open_dt(target)
        cached = self._cache.get(sym)
        if cached:
            baseline = cached
            today = kite_rows_to_candles(self._fetch(token, start_today.replace(tzinfo=None),
                                                     now.replace(tzinfo=None), "5minute"))
        else:
            day_to = datetime.combine(target - timedelta(days=1), datetime.max.time()).replace(microsecond=0)
            daily = kite_rows_to_daily(self._fetch(token, day_to - timedelta(days=45), day_to, "day"))
            intra = kite_rows_to_candles(self._fetch(token, (start_today - timedelta(days=35)).replace(tzinfo=None),
                                                     now.replace(tzinfo=None), "5minute"))
            baseline = build_baseline(sym, daily, intra, target)
            today = [c for c in intra if datetime.fromtimestamp(c["t"], IST).date() == target]
        self._cache[sym] = baseline
        self.store.set_baseline(sym, baseline)
        if today:
            self.store.merge_session_history(sym, today)

    def _save_cache(self, target: date) -> None:
        try:
            save_cache(self.cache_dir, target, "kite", dict(self._cache))
        except OSError as e:
            self._error("cache", e)

    # ---------------------------------------------------------- detail chart (lazy)
    def chart_history(self, sym: str, sessions: int) -> list[dict]:
        """Previous complete sessions of 5m candles for one symbol; bounded 2-entry LRU."""
        if sym not in self.eq or self.kite is None or sessions <= 0:
            return []
        target = self.store.session_date
        key = (sym, target, sessions)
        with self._chart_lock:
            if key in self._chart_cache:
                self._chart_cache.move_to_end(key)
                return self._chart_cache[key]
        to = datetime.combine(target - timedelta(days=1), datetime.max.time()).replace(microsecond=0)
        frm = to - timedelta(days=int(sessions * 1.6) + 6)
        rows = kite_rows_to_candles(self._fetch(self.eq[sym]["token"], frm, to, "5minute"))
        dates = sorted({datetime.fromtimestamp(c["t"], IST).date() for c in rows})[-sessions:]
        rows = [c for c in rows if datetime.fromtimestamp(c["t"], IST).date() in dates]
        with self._chart_lock:
            self._chart_cache[key] = rows
            while len(self._chart_cache) > CHART_CACHE_SIZE:
                self._chart_cache.popitem(last=False)
        return rows

    def stop(self) -> None:
        self._stop.set()
        self._hq_event.set()
        if self.ticker is not None:
            try:
                self.ticker.close()
            except Exception:
                pass
