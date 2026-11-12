"""Scanner: turns MarketStore state into dashboard snapshots and per-symbol detail.

No network I/O happens here except through the optional history provider used by
symbol_detail (lazy, bounded). Snapshot building is lock-safe: the store lock is held
only while copying the small per-symbol state needed for calculations.
"""
from __future__ import annotations

import math
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from adaptive_ranking import rank_records
from candle_profile import mini_profile, volume_profile
from candles import TIMEFRAMES, resample, session_of, vwap_series
from fresh_momentum import compute_fresh
from history_intelligence import live_history_metrics
from market_time import (IST, epoch, in_session_hours, is_trading_day, session_close_dt, session_open_dt, to_ist)
from momentum_rating import demo_momentum_score, momentum_score
from orderblock_intelligence import DELTA_SOURCE, analyze_order_blocks
from sectors import ALL_SYMBOLS, PRIMARY_SECTORS, SYMBOL_TO_SECTOR
from stable_ob_picks import StableOBPicks
from stock_detail_metrics import (DELTA_PROXY_SOURCE, conviction, delta_proxy, pivots_with_basis, risk_reward)

QUOTE_STALE_SECONDS = 120
MAX_CHART_CANDLES = 90
SESSIONS_FOR_TF = {5: 2, 15: 4, 30: 7, 60: 13}
STATE_CHIP = {"ACTIVE": "ACTIVE", "CONSOLIDATING": "SIDEWAYS", "FADING": "FADING", "WARMING_UP": "WARMUP",
              "STALE": "STALE", "DEMO": "DEMO"}
ERROR_FEED_STATES = {"AUTH_ERROR", "CONFIG_ERROR", "ERROR"}


def oi_caption(day_change: Optional[float], oi: Optional[float], baseline: Optional[float]) -> tuple[str, Optional[float]]:
    if oi is None or baseline is None or day_change is None:
        return "OI UNAVAILABLE", None
    delta = oi - baseline
    pct = round(100.0 * delta / baseline, 3) if baseline > 0 else None
    if delta == 0:
        return "OI UNCHANGED", pct
    if day_change >= 0:
        return ("LONG BUILD-UP" if delta > 0 else "SHORT COVERING"), pct
    return ("SHORT BUILD-UP" if delta > 0 else "LONG UNWINDING"), pct


def _r(x, n=2):
    return round(x, n) if isinstance(x, (int, float)) and math.isfinite(x) else None


class Scanner:
    def __init__(self, store, *, mode: str, strict_top3: bool = False, feed_fresh_seconds: float = 30.0,
                 ob_cache_seconds: float = 25.0, history_provider: Optional[Callable[[str, int], list]] = None,
                 viewer_label: str = "VIEWER", wall_clock: Optional[Callable[[], datetime]] = None):
        self.store = store
        self.mode = mode
        self.demo = mode == "demo"
        self.feed_fresh_seconds = feed_fresh_seconds
        self.ob_cache_seconds = ob_cache_seconds
        self.history_provider = history_provider
        self.viewer_label = viewer_label
        self.wall_clock = wall_clock
        self.picks = StableOBPicks(strict=strict_top3)
        self._ob_cache: dict[str, tuple] = {}
        self._build_lock = threading.Lock()
        self.revision = 0
        self.last_snapshot: Optional[dict] = None
        self.last_records: dict[str, dict] = {}

    # ------------------------------------------------------------ status
    def market_status(self, now: datetime) -> dict:
        st = self.store
        feed = dict(st.feed)
        base = {"feedState": feed.get("state"), "feedDetail": feed.get("detail"), "feedError": feed.get("error"),
                "inSessionHours": in_session_hours(now), "sessionDate": st.session_date.isoformat() if st.session_date else None,
                "lastTickAt": st.last_tick_wall.isoformat(timespec="seconds") if st.last_tick_wall else None}
        if self.demo:
            return {**base, "status": "DEMO", "label": "DEMO",
                    "reason": "Synthetic demo feed: fictional prices, not market data."}
        if feed.get("state") in ERROR_FEED_STATES:
            return {**base, "status": "ERROR", "label": "FEED ERROR", "reason": feed.get("error") or "Feed error"}
        wall = to_ist(self.wall_clock() if self.wall_clock else now)
        if not is_trading_day(wall.date(), st.holidays):
            return {**base, "status": "CLOSED", "label": "MARKET CLOSED", "reason": "Weekend or configured NSE holiday."}
        if wall < session_open_dt(wall.date()) or wall > session_close_dt(wall.date()):
            return {**base, "status": "CLOSED", "label": "MARKET CLOSED",
                    "reason": "Outside 09:15-15:30 IST; showing last genuine values as stale."}
        if st.last_tick_wall is None:
            return {**base, "status": "WAITING", "label": "WAITING",
                    "reason": "Session hours, but no exchange ticks received yet."}
        age = (wall - st.last_tick_wall).total_seconds()
        if age <= self.feed_fresh_seconds:
            return {**base, "status": "LIVE", "label": "LIVE", "reason": "Fresh exchange ticks confirmed."}
        return {**base, "status": "STALE", "label": "STALE", "reason": f"No fresh ticks for {int(age)} s."}

    # ------------------------------------------------------------ records
    def _ob(self, sym: str, sc, now: datetime, rec: dict, feed_live: bool) -> dict:
        all_c = sc.candles(now) if sc else []
        completed = [c for c in all_c if c["complete"]]
        key = (self.store.session_date, completed[-1]["t"] if completed else None, len(completed), feed_live,
               len(all_c), all_c[-1]["v"] if all_c else None)
        hit = self._ob_cache.get(sym)
        mono = time.monotonic()
        if hit and hit[0][:4] == key[:4] and mono - hit[1] < self.ob_cache_seconds:
            return hit[2]
        htf = [c["c"] for c in resample(completed, 15, epoch(now)) if c["complete"]]
        res = analyze_order_blocks(completed, htf_closes=htf, rvol20=rec.get("rvol"), vwap=rec.get("vwap"),
                                   ltp=rec.get("ltp"), now_epoch=epoch(now), tf_minutes=5, feed_live=feed_live,
                                   demo=self.demo)
        res["vp"] = mini_profile(all_c, 10)
        res.pop("zones", None)
        self._ob_cache[sym] = (key, mono, res)
        return res

    def _record(self, sym: str, q: Optional[dict], tape, sc, baseline, now: datetime, session_date: date,
                feed_live: bool) -> dict:
        rec = {"symbol": sym, "sector": SYMBOL_TO_SECTOR[sym], "available": False, "stale": True,
               "momentumState": "STALE", "stateChip": "STALE", "mom": None, "obQualityScore": None, "obState": None}
        if not q or q.get("ltp") is None or not q.get("prevClose"):
            rec["unavailableReason"] = "No quote with previous close yet."
            return rec
        ltp, prev = q["ltp"], q["prevClose"]
        day = 100.0 * (ltp / prev - 1.0)
        ts = q.get("ts")
        age = (now - ts).total_seconds() if ts else None
        stale = ts is None or q.get("sessionDate") != session_date or age is None or age > QUOTE_STALE_SECONDS
        hist = live_history_metrics(baseline, ltp, q.get("volume"), q.get("high"), q.get("low"), now)
        fresh = compute_fresh(tape, now=now, ltp=ltp, prev_close=prev, adr20=hist["adr20"], rvol20=hist["rvol"],
                              session_date=session_date) if tape is not None else None
        vwap, vwap_src = None, None
        if q.get("avgPrice") and q.get("sessionDate") == session_date:
            vwap, vwap_src = q["avgPrice"], ("DEMO_ATP" if self.demo else "PROVIDER_AVG_TRADED_PRICE")
        elif sc is not None:
            series = vwap_series(sc.candles(now))
            if series and series[-1] is not None:
                vwap, vwap_src = series[-1], "CANDLE_TYPICAL_PRICE"
        bullish = day >= 0
        vwap_aligned = None if vwap is None else (ltp >= vwap if bullish else ltp <= vwap)
        breakout_match = (hist["breakout20"] == "ABOVE_20D_HIGH" and bullish) or \
                         (hist["breakout20"] == "BELOW_20D_LOW" and not bullish)
        caption, oi_pct = oi_caption(day, q.get("oi"), q.get("oiBaseline"))
        session_move = 100.0 * (ltp / q["open"] - 1.0) if q.get("open") else None
        if self.demo:
            state = "DEMO"
            oi_confirms = caption == ("LONG BUILD-UP" if bullish else "SHORT BUILD-UP")
            mom = demo_momentum_score(day, hist["rvol"], oi_confirms, vwap_aligned)
            pick_eligible = False
            fresh_score, fresh_reason = 0.0, "DEMO: simulated display state, not live momentum confirmation."
            volume_confirmed = hist["rvol"] is not None and hist["rvol"] >= 1.0
            stale = False if age is not None and age <= QUOTE_STALE_SECONDS else stale
        else:
            if stale:
                fresh = dict(fresh or {}, momentumState="STALE", pickEligible=False, freshScore=0.0,
                             freshReason="Quote is stale or from a previous session.")
            state = fresh["momentumState"]
            mom = momentum_score(fresh, rvol20=hist["rvol"], vwap_aligned=vwap_aligned,
                                 breakout_matches=breakout_match, history_status=hist["historyStatus"])
            pick_eligible = bool(fresh["pickEligible"])
            fresh_score, fresh_reason = fresh["freshScore"], fresh["freshReason"]
            volume_confirmed = fresh["volumeConfirmed"]
        rec.update({
            "available": True, "stale": stale, "ltp": ltp, "prevClose": prev, "open": q.get("open"),
            "high": q.get("high"), "low": q.get("low"), "volume": q.get("volume"),
            "dayChangePct": round(day, 3), "sessionMovePct": _r(session_move, 3),
            "quoteTs": ts.isoformat(timespec="seconds") if ts else None, "quoteAgeSec": _r(age, 1),
            "quoteTsSource": q.get("tsSource"),
            "vwap": _r(vwap, 2), "vwapSource": vwap_src, "vwapAligned": vwap_aligned,
            **hist,
            "rvolBasis": ("DEMO_SYNTHETIC_HISTORY" if self.demo else "TIME_MATCHED_20D") if hist["rvol"] is not None else None,
            "momentumState": state, "stateChip": STATE_CHIP.get(state, state),
            "windows": fresh["windows"] if fresh else None, "readyCount": fresh["readyCount"] if fresh else 0,
            "recentVolumeRatio": fresh["recentVolumeRatio"] if fresh else None,
            "volumeConfirmed": volume_confirmed, "pickEligible": pick_eligible,
            "freshScore": fresh_score, "freshReason": fresh_reason,
            "mom": mom["score"], "momBasis": mom["basis"], "momComponents": mom["components"], "momCap": mom["cap"],
            "momMissing": mom["missing"],
            "oiCaption": caption, "oiChangePct": oi_pct,
            "oiBasis": "FIRST_OBSERVED_SINCE_CONNECT" if q.get("oiBaseline") is not None else None,
            "futures": self.store.futures.get(sym, {}).get("tradingsymbol") if self.store.futures else None,
        })
        rec.update(self._ob(sym, sc, now, rec, feed_live))
        return rec

    def build_records(self, now: datetime, feed_live: bool) -> dict[str, dict]:
        st = self.store
        with st.lock:
            session_date = st.session_date
            items = [(s, dict(st.quotes[s]) if s in st.quotes else None, st.tapes.get(s), st.sessions.get(s),
                      st.baselines.get(s)) for s in ALL_SYMBOLS if s in st.mapped]
            # Tapes/candles are read under the lock below as calculations are quick.
            out = {}
            for sym, q, tape, sc, baseline in items:
                out[sym] = self._record(sym, q, tape, sc, baseline, now, session_date, feed_live)
        return out

    # ------------------------------------------------------------ snapshot
    def build_snapshot(self, advance_picks: bool = True) -> dict:
        with self._build_lock:
            st = self.store
            now = to_ist(st.clock())
            st.ensure_session(now)
            market = self.market_status(now)
            feed_live = market["status"] == "LIVE"
            records = self.build_records(now, feed_live)
            avail = [r for r in records.values() if r["available"]]
            sides = rank_records(avail, self.demo)
            leaders = {s: sides[s][:6] for s in sides}
            if advance_picks:
                picks = self.picks.update(leaders=leaders, records=records, market_revision=st.market_revision,
                                          market_live=feed_live, demo=self.demo, session_date=st.session_date)
            else:
                picks = self.picks.peek(records=records, market_live=feed_live, demo=self.demo)
            sectors = []
            for sec, syms in PRIMARY_SECTORS.items():
                rs = [records[s] for s in syms if s in records and records[s]["available"]]
                chg = [r["dayChangePct"] for r in rs]
                sectors.append({"sector": sec, "changePct": round(sum(chg) / len(chg), 3) if chg else None,
                                "bullish": sum(1 for c in chg if c >= 0), "bearish": sum(1 for c in chg if c < 0),
                                "total": len(rs), "symbols": [r["symbol"] for r in rs]})
            sectors.sort(key=lambda x: (x["changePct"] is None, -(x["changePct"] or 0)))
            primary = {s for syms in PRIMARY_SECTORS.values() for s in syms}
            watch_only = [r for r in avail if r["symbol"] not in primary]
            self.revision += 1
            hist = dict(st.history)
            snap = {
                "revision": self.revision,
                "marketDataRevision": st.market_revision,
                "generatedAt": datetime.now(IST).isoformat(timespec="seconds"),
                "marketTime": now.isoformat(timespec="seconds"),
                "mode": self.mode, "demo": self.demo,
                "market": market,
                "ticks": st.tick_count,
                "viewer": self.viewer_label,
                "history": {"loaded": hist.get("loaded", 0), "target": hist.get("target", 0),
                            "status": hist.get("status"), "targetDate": hist.get("targetDate"),
                            "source": hist.get("source"), "errors": hist.get("errors", 0)},
                "universe": {"symbols": len(ALL_SYMBOLS), "mapped": len(st.mapped), "available": len(avail),
                             "bullish": len(sides["bullish"]), "bearish": len(sides["bearish"]),
                             "unresolved": list(st.unresolved)},
                "sectors": sectors,
                "watchlistOnly": {"symbols": [r["symbol"] for r in watch_only],
                                  "bullish": sum(1 for r in watch_only if r["dayChangePct"] >= 0),
                                  "bearish": sum(1 for r in watch_only if r["dayChangePct"] < 0)},
                "rankings": {s: [r["symbol"] for r in sides[s]] for s in sides},
                "leaders": {s: [r["symbol"] for r in leaders[s]] for s in leaders},
                "records": {s: self.card(r) for s, r in records.items()},
                "picks": picks,
            }
            self.last_records = records
            self.last_snapshot = snap
            return snap

    @staticmethod
    def card(r: dict) -> dict:
        # Compact per-symbol card; long reason strings live in /api/symbol and Top 3 slots.
        keys = ("symbol", "sector", "available", "stale", "ltp", "dayChangePct", "mom", "momBasis",
                "obQualityScore", "obState", "obDirection", "oiCaption", "momentumState", "stateChip", "rvol", "vp",
                "rank", "rankOf", "side", "adaptiveRankScore", "pickEligible")
        out = {k: r.get(k) for k in keys if r.get(k) is not None or k in ("mom", "obQualityScore", "rvol")}
        if isinstance(out.get("rvol"), float):
            out["rvol"] = round(out["rvol"], 2)
        if isinstance(out.get("dayChangePct"), float):
            out["dayChangePct"] = round(out["dayChangePct"], 2)
        return out

    # ------------------------------------------------------------ detail
    def symbol_detail(self, sym: str, timeframe: str) -> dict:
        tfm = TIMEFRAMES[timeframe]
        st = self.store
        now = to_ist(st.clock())
        snap = self.last_snapshot or self.build_snapshot(advance_picks=False)
        market = snap["market"]
        feed_live = market["status"] == "LIVE"
        rec = self.last_records.get(sym) or {"symbol": sym, "sector": SYMBOL_TO_SECTOR[sym], "available": False}
        with st.lock:
            sc = st.sessions.get(sym)
            today = sc.candles(now) if sc else []
            baseline = st.baselines.get(sym)
            session_date = st.session_date
        prev_sessions = SESSIONS_FOR_TF[tfm] - 1
        history_note = None
        prev: list[dict] = []
        if self.history_provider and prev_sessions > 0:
            try:
                prev = [c for c in self.history_provider(sym, prev_sessions) if session_of(c["t"]) < session_date]
            except Exception as e:
                history_note = f"Previous-session candles unavailable ({type(e).__name__})."
        elif prev_sessions > 0:
            history_note = "No historical candle provider in this mode; showing current-session candles only."
        all5 = sorted(prev, key=lambda c: c["t"]) + today
        bars = resample(all5, tfm, epoch(now))
        vw5 = vwap_series(all5)
        vw_by_bucket: dict[int, Optional[float]] = {}
        for c, v in zip(all5, vw5):
            dt = datetime.fromtimestamp(c["t"], IST)
            k = int(((dt - session_open_dt(dt.date())).total_seconds() // 60) // tfm)
            vw_by_bucket[epoch(session_open_dt(dt.date()) + timedelta(minutes=k * tfm))] = v
        display = bars[-MAX_CHART_CANDLES:]
        for b in display:
            b["vwap"] = vw_by_bucket.get(b["t"])
            b.pop("members", None)

        cur_tf = [b for b in bars if session_of(b["t"]) == session_date and b["complete"]]
        cur5_done = [c for c in today if c["complete"]]
        htf = [c["c"] for c in resample(cur5_done, 15, epoch(now)) if c["complete"]]
        ob = analyze_order_blocks(cur_tf, htf_closes=htf, rvol20=rec.get("rvol"), vwap=rec.get("vwap"),
                                  ltp=rec.get("ltp"), now_epoch=epoch(now), tf_minutes=tfm, feed_live=feed_live,
                                  demo=self.demo, include_zones=True)
        zones = [dict(z, historical=False) for z in ob.get("zones", [])]
        shown_from = display[0]["t"] if display else None
        for d in sorted({session_of(b["t"]) for b in display if session_of(b["t"]) != session_date}):
            sess = [b for b in bars if session_of(b["t"]) == d and b["complete"]]
            old = analyze_order_blocks(sess, tf_minutes=tfm, demo=self.demo, include_zones=True)
            for z in old.get("zones", [])[:2]:
                if shown_from is None or z["endT"] >= shown_from:
                    zones.append(dict(z, historical=True, selected=False))
        profile_src = [c for c in all5 if shown_from is None or c["t"] >= shown_from]
        profile = volume_profile(profile_src, 24)
        prev_session = (baseline or {}).get("prevSession")
        if not prev_session and prev:
            last_d = session_of(prev[-1]["t"])
            ps = [c for c in prev if session_of(c["t"]) == last_d]
            prev_session = {"date": last_d.isoformat(), "h": max(c["h"] for c in ps), "l": min(c["l"] for c in ps),
                            "c": ps[-1]["c"]}
        pivots = pivots_with_basis(prev_session, today)
        provisional = bool(display) and not display[-1]["complete"]
        conv = conviction(rec.get("mom"), ob.get("obQualityScore"))
        rr = risk_reward(ob.get("obDirection") or ("BULLISH" if (rec.get("dayChangePct") or 0) >= 0 else "BEARISH"),
                         rec.get("ltp"), ob.get("obZone"), pivots)
        return {
            "symbol": sym, "sector": SYMBOL_TO_SECTOR[sym], "timeframe": timeframe, "tfMinutes": tfm,
            "mode": self.mode, "demo": self.demo, "market": market,
            "summary": {k: v for k, v in rec.items() if k not in ("vp",)},
            "dashboardOb": {k: rec.get(k) for k in ("obQualityScore", "obState", "obReason", "obDirection")},
            "candles": display,
            "ob": ob,
            "zones": zones,
            "pivots": pivots,
            "profile": profile,
            "details": {"conviction": conv, "convictionBasis": "0.6*MOM + 0.4*OB quality (heuristic)",
                        "riskReward": rr, "deltaProxy": delta_proxy(cur5_done), "deltaProxySource": DELTA_PROXY_SOURCE,
                        "obDeltaSource": DELTA_SOURCE, "atr14": rec.get("atr14"), "adr20": rec.get("adr20"),
                        "rvol": rec.get("rvol"), "oiCaption": rec.get("oiCaption"), "vwap": rec.get("vwap"),
                        "vwapSource": rec.get("vwapSource"), "high20": rec.get("high20"), "low20": rec.get("low20")},
            "timing": {"generatedAt": datetime.now(IST).isoformat(timespec="seconds"),
                       "marketTime": now.isoformat(timespec="seconds"), "quoteTs": rec.get("quoteTs"),
                       "quoteAgeSec": rec.get("quoteAgeSec"), "provisionalCandle": provisional,
                       "candleCount": len(display), "maxCandles": MAX_CHART_CANDLES, "refreshSeconds": 15,
                       "sessionDate": session_date.isoformat() if session_date else None,
                       "historyNote": history_note,
                       "waiting": None if display else "Waiting for candles: no history or ticks for this symbol yet."},
        }
