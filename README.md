# ORDERFLOW STUDIO — 20-SESSION MARKET INTELLIGENCE · NSE

A read-only intraday research scanner for **198 NSE symbols in 14 primary sectors** (plus the
cross-sector NIFTY_50 watchlist, which is not counted as a sector). It runs as a local FastAPI server
with a dense dark terminal UI in the browser.

It shows prices, daily direction, time-matched RVOL, sector breadth, fresh momentum (MOM 0–100),
completed-candle order-block context, Top 6 bullish/bearish leaders and sticky Top 3 OB-qualified
picks, plus a full-screen candlestick chart per stock.

**It never places orders.** Its scores are heuristics. They are not win probabilities, trade
instructions, or evidence of institutional orders.

---

## Quick start

```bash
cd premium
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Leave MARKET_MODE=demo for offline UI, or set valid Kite env vars for live data.
python server.py
```

Visit **http://127.0.0.1:8000**.

### Windows (PowerShell)

```powershell
cd premium
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1        # cmd.exe: .venv\Scripts\activate.bat
pip install -r requirements.txt
Copy-Item .env.example .env
python server.py
```

If PowerShell blocks the activation script, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. You can also skip activation and call
`.\.venv\Scripts\python.exe server.py` directly.

Python 3.11 or newer is required (tested on 3.13). Behind a corporate TLS proxy, set
`NODE_EXTRA_CA_CERTS` / `PIP_CERT` / `REQUESTS_CA_BUNDLE` to your CA bundle. Never disable TLS
verification.

### Health checks

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/version
curl -s "http://127.0.0.1:8000/api/symbol/M%26M?timeframe=15m" | head -c 400
```

---

## Modes

| `MARKET_MODE` | Data source | Header pill |
|---|---|---|
| `demo` (default) | Offline seeded random walk on a virtual NSE clock (1 virtual minute per ~4 s). Every value is **fictional** and labelled DEMO. | `DEMO` |
| `kite` | Zerodha Kite Connect: instrument master, one KiteTicker WebSocket (cash QUOTE, futures FULL), throttled historical API. | `LIVE` / `STALE` / `WAITING` / `MARKET CLOSED` / `FEED ERROR` |
| `live` | Authenticated quote batches pushed to `POST /api/ingest`. If Kite credentials are also set, they are used for history only. | same as kite |

The app never silently falls back to demo data. If Kite credentials are missing or rejected, the
pill shows **FEED ERROR** with the reason, and no prices are invented.

**LIVE** requires actual fresh ticks: a trading day, 09:15–15:30 IST, and the last tick no older
than `FEED_FRESH_SECONDS` (30 s). Being inside session hours is not enough. Outside hours the pill
shows MARKET CLOSED, and the last genuine values remain visible but stale. Exchange holidays come
from `NSE_HOLIDAYS`.

### Kite Connect setup and token renewal

1. Create a Kite Connect app at https://developers.kite.trade and note the API key.
2. Each trading day, complete the official Kite login flow for your app to get a `request_token`,
   then exchange it for an `access_token` (`KiteConnect.generate_session`) using your API secret.
   Do this outside this app; it has no login page and never sees your password or API secret.
3. Put `MARKET_MODE=kite`, `KITE_API_KEY` and `KITE_ACCESS_TOKEN` in `.env` and restart.
   Access tokens expire daily. An expired token shows `FEED ERROR · Kite rejected the credentials`.

On startup the feed does the following:

- Downloads the NSE and NFO instrument masters and maps exact NSE EQ symbols. Unresolved symbols
  are listed in `/api/health` and in the flow panel, and the denominator in the header stays truthful.
- Picks the nearest non-expired NFO stock future for OI.
- Takes one REST quote snapshot, then streams ticks.
- Loads 20 completed sessions of daily and 5m history at about 1.8 requests/s. Top 6 leaders are
  loaded first. Retries use backoff, and derived baselines go to a gzip cache keyed by target date.

### External ingest (`MARKET_MODE=live`)

```bash
curl -s -X POST http://127.0.0.1:8000/api/ingest \
  -H "Content-Type: application/json" -H "X-Feed-Token: $MARKET_FEED_TOKEN" \
  -d '{"quotes":[{"symbol":"INFY","ltp":1501.2,"prevClose":1480.0,"open":1485,"high":1503,"low":1483,
       "volume":1834500,"avgPrice":1494.1,"oi":5123400,"timestamp":"2026-11-11T10:31:05+05:30"}]}'
```

Each quote is validated:

- Exact symbol from the universe.
- Positive finite prices; extra fields are rejected.
- At most 500 quotes and 512 KB per batch.
- Timestamps more than 5 s in the future are rejected.

The token is compared in constant time. The route returns 403 in demo and kite modes.

---

## What is real, synthetic or estimated

| Item | Kite / live mode | Demo mode |
|---|---|---|
| LTP, prev close, OHLC, cumulative volume, avg traded price | Provider data | Synthetic |
| 5m candles | Kite history plus tick-built candles. Candles that started after connect are flagged *partial* because missing startup ticks are never reconstructed. | Synthetic |
| ADR20, ATR14, 20D high/low, RVOL20 | Computed from completed sessions only | Computed from synthetic history |
| Futures OI build-up captions | Real OI against the **first value observed since connect** (not previous-close OI) | Synthetic |
| MOM score | Four rolling same-session windows plus context (heuristic) | Separate DEMO formula |
| OB / BOS / FVG / sweep / retest | Derived from completed candles (heuristic) | Same engine on synthetic candles |
| Volume profile | **Estimate**: candle volume spread over each candle's range (`5M CANDLE EST.`) | same |
| Δ proxy | Candle close-location proxy. Aggressor delta is **unavailable** from Kite quotes. | same |
| Directional flow strands | Stock-count breadth per sector. Not rupee money flow, order routing or depth. | same |
| Viewer badge | Display label only. No authentication. | same |

---

## Using the terminal

- **Sector rail.** The 14 sectors are ranked by equal-weight average daily % change, and each row's
  bar shows its bullish/bearish split. Clicking a sector highlights its flow strands and filters
  both leader lanes to that sector's stocks. Those cards keep their **global** ranks and are
  labelled `SECTOR FILTER · GLOBAL RANK`, because this is not a separate sector ranking.
  *Show all sectors* resets the filter.
- **Directional flow.** Each available stock is one strand from its sector to the bullish or
  bearish gate. The big count is the bullish/bearish split of the whole available universe.
  BHARTIARTL (NIFTY_50-only) appears as a separate muted `NIFTY 50 ONLY` origin, so the counts
  reconcile.
- **Top 6 cards** show the ticker, day %, MOM, OB score/state, OI caption, momentum chip
  (ACTIVE / SIDEWAYS / FADING / WARMUP / STALE / DEMO), RVOL, an estimated mini volume profile and
  a `TV ↗` link (TradingView, 5m, opens in a new tab with `noopener,noreferrer`). Clicking a card
  opens the chart.
- **Top 3 picks.** Empty slots read *WAITING FOR ELIGIBLE OB*. The footer shows any pending
  challenger and its revision count.
- **Chart overlay** has these controls:
  - `5m/15m/30m/1h` re-aggregates candles on the backend.
  - `AUTO` keeps the newest bar in view. Any pan or zoom turns it off until you press AUTO again.
  - `+` and `–` zoom (animated); `FIT` resets the view.
  - Mouse: wheel zooms, drag pans, double-click fits. Touch: pinch zooms.
  - Full screen, bookmark (stored client-side as symbol names only), previous/next symbol,
    and a legend whose items toggle each layer.
  - `Esc` closes the chart; `Alt+←/→` moves to the previous/next symbol.
- **Theme.** The sun/moon button toggles light and dark. Only the preference is stored.

---

## Calculation reference (summary)

**Fresh momentum** (`fresh_momentum.py`)
- The tape holds one sample per minute, at most 68, from the current session only.
- The lookback reference is the sample at the exact target minute or one minute earlier.
- D_n = 100·(LTP/LTP_{n min ago} − 1). It is direction-adjusted by the sign of the day change.
- Thresholds: T5 = max(0.10, 0.055·ADR), T15 = max(0.22, 0.12·ADR), T30 = max(0.32, 0.18·ADR),
  T60 = max(0.48, 0.28·ADR).
- States: STALE → WARMING_UP → FADING → ACTIVE / CONSOLIDATING.
- `pickEligible` = ACTIVE and RVOL20 ≥ 1.

**MOM** (`momentum_rating.py`)
- Weights: 25/20/15/10 for the 5/15/30/60m windows, 15 for RVOL, 5 for volume acceleration,
  7 for VWAP and 3 for a 20D breakout.
- Caps: FADING ≤ 25, CONSOLIDATING ≤ 42, ACTIVE but not eligible ≤ 55, 5m or 15m missing ≤ 35,
  STALE = N/A.
- Scores round half-up.

**Top 6** (`adaptive_ranking.py`)
- adaptive = MOM + min(8, 1.4·|session move|) + state modifier (+12/−8/−24/−5/−45/−12)
  + 3 if ACTIVE with confirmed volume.
- The tie-break is a historical-strength score, with a labelled fallback while history warms up.

**Order blocks** (`orderblock_intelligence.py`)
- Uses completed current-session candles only, with at least 12.
- Anchor: a red candle (bullish case) or green candle (bearish case).
- Break of structure: an impulse within 1–3 candles that closes beyond the previous 9-candle
  structure, with displacement ≥ 1.15.
- If several candidates exist, the most recent break wins.
- Also detects FVG, sweep, retests and invalidation (a close through the zone ± ε) and expires
  zones after 36 bars.
- Quality (max 100) = BOS 25 + freshness 15 + 15m trend 15 + volume 15 + FVG 10 + recent retest 10
  + sweep 6 + VWAP 4.
- READY needs a recent rejection, an aligned 15m trend and a score ≥ 60. A feed that is not LIVE
  downgrades READY to WAIT.

**Top 3** (`stable_ob_picks.py`)
- Candidates are drawn from the Top 6 only.
- READY candidates are admitted. WAIT candidates are admitted only as an unconfirmed WATCH, and
  only in real LIVE mode with every safeguard met.
- A challenger must beat the weakest incumbent on **three distinct market-data revisions**.
  Revisions come only from real data changes; one feed message counts as one revision. HTTP polls
  and idle publishes never count.
- Locks reset at session rollover.
- `STRICT_TOP3_CONFIRMATION=true` additionally requires `pickEligible` for READY admission
  (the default is off, which matches the baseline behaviour).

**History** (`history_intelligence.py`)
- Only bars strictly before the target date are used.
- An intraday session counts as valid with ≥ 60 of its 75 5m candles.
- RVOL is current cumulative volume divided by the mean cumulative volume of the same 09:15-anchored
  bucket, interpolated within the bucket. It is N/A until 10 valid sessions exist.

**Chart** (`candles.py`, `scanner.py`)
- 5m candles are resampled into 15m/30m/1h buckets anchored at 09:15, never across sessions.
- Session VWAP uses typical price.
- Floor pivots come from the previous complete session, with a labelled fallback.
- The chart shows about 90 bars and flags the provisional last candle.

---

## API

```
GET  /                         terminal UI
GET  /api/version              build info and feature flags (no secrets)
GET  /api/state                latest snapshot: market, sectors, rankings, leaders, records, picks, history
GET  /api/events               SSE snapshots every ~2 s, keepalive 15 s, retry 3 s
GET  /api/universe             ALL_SYMBOLS, SECTOR_DEFINITIONS, primary sectors
GET  /api/symbol/{symbol}?timeframe=5m|15m|30m|1h
                               summary, ≤90 candles with VWAP, OB analysis + zones, pivots, profile, details, timing
GET  /api/health               mode, feed state, mapping counts, history progress, redacted errors
POST /api/ingest               X-Feed-Token protected; MARKET_MODE=live only
```

Unknown symbol → 404 (exact match: `infy` is not `INFY`). Unknown timeframe → 400. All JSON is
strict: `null` for missing values, never NaN or Infinity.

---

## Tests

```bash
python -m pytest            # 128 tests: universe, history, momentum, MOM, ranking, OB, Top 3, chart, server, Kite fakes
```

Optional UI check with Playwright. The server must be running, and you need an existing Playwright
install:

```bash
node tools/visual-check.js http://127.0.0.1:8000 <path-to>/node_modules/playwright
```

It screenshots the dashboard and charts at 1920, 1440, 1024 and 390 px into `screenshots/`, and
checks:

- no horizontal overflow
- 14 sectors, 6 + 6 cards and 3 + 3 slots
- 198 strands
- sector filtering
- detail open, pan → MANUAL, AUTO
- timeframe switch and next symbol
- Esc closes the chart
- no console errors

`python tools/smoke.py 80` runs the demo pipeline in-process and prints timings.

---

## Project layout

```
server.py                  FastAPI app, config, SSE, ingest, security headers
scanner.py                 snapshot builder, market status, symbol detail
market_store.py            thread-safe quotes/tapes/candles/baselines; marketDataRevision
market_time.py             IST / NSE session helpers (fixed +05:30, no tz database needed)
kite_feed.py               Kite instruments, KiteTicker, throttled history, chart LRU (2)
demo_feed.py               offline synthetic feed on a virtual session clock
sectors.py                 the exact 198-symbol taxonomy (unchanged)
history_intelligence.py    ADR20 / ATR14 / 20D range / time-matched RVOL + gzip cache
fresh_momentum.py          minute tape + 5/15/30/60 rolling windows + state machine
momentum_rating.py         MOM 0..100 (+ separate demo formula)
adaptive_ranking.py        Top 6 ordering
orderblock_intelligence.py OB / BOS / FVG / sweep / retest engine
stable_ob_picks.py         sticky Top 3
candles.py                 tick→5m candles, 09:15-anchored resampling, VWAP
candle_profile.py          estimated volume-at-price
stock_detail_metrics.py    pivots, conviction, R:R estimate, delta proxy
static/                    index.html, app.js, flow.js, chart.js, viewport.js, CSS, favicon
tests/                     pytest suite
tools/                     smoke.py, visual-check.js
```

The pure calculation modules have no feed or network dependencies. A future, separate backtester
can import `fresh_momentum`, `momentum_rating`, `orderblock_intelligence`, `history_intelligence` and
`candles` directly. This scanner deliberately ships no backtest or replay module.

---

## Security notes

- Credentials are read server-side from `.env` only. They never appear in API responses, SSE, logs,
  static files or browser storage, and error strings are redacted (`***`). Tests verify this.
- The server binds to `127.0.0.1` by default. CSP (`script-src 'self'`), `X-Frame-Options: DENY`,
  `no-referrer` and `nosniff` are set, and there is no CORS.
- The UI is read-only and has no trading endpoints.
- **If you expose this beyond localhost, add real authentication.** Use a reverse proxy with SSO or
  basic auth, or a private network. The viewer label is not authentication.
- If a `.env` with real keys was ever shared or zipped, rotate the Kite API secret and regenerate
  tokens.

## Deploying to Render (optional)

`render.yaml` defines a Python web service.

- Set `KITE_API_KEY`, `KITE_ACCESS_TOKEN` and `MARKET_FEED_TOKEN` as secret env vars in the
  dashboard. `HOST=0.0.0.0`; Render injects `PORT`.
- The filesystem is **ephemeral**, so the baseline cache under `/tmp` is lost on every deploy or
  restart and history reloads (about 4 min for 198 symbols in Kite mode). Attach a paid persistent
  disk and point `HISTORY_CACHE_DIR` at it to keep the cache.
- The free plan (about 0.1 CPU / 512 MB) runs demo mode, but snapshots take longer, and the instance
  sleeps when idle, which drops the WebSocket. Use a paid instance for Kite mode. Memory stays
  bounded: one session of 5m candles per symbol, a 68-point tape, 2 cached detail histories, and
  SSE queues capped at 2 snapshots with at most 32 clients.
- Render does not authenticate visitors. Put it behind access control (for example a Cloudflare
  Access or oauth2-proxy front end) before exposing market data or the ingest route.

## Known limitations

- Kite QUOTE-mode ticks carry no exchange timestamp, so cash-quote freshness uses the server
  receive time (labelled `RECEIVED`). Futures FULL ticks use exchange timestamps.
- Candles that started before the WebSocket connected are taken from Kite history once loaded.
  Until then, the first tick-built candle is marked partial.
- Detail-chart OB analysis on 30m/1h needs 12 completed current-session candles. On those
  timeframes it usually shows WAIT until late in the session, and older zones are shown faded.
- Holidays must be supplied via `NSE_HOLIDAYS`. Special sessions (Muhurat trading) are not modelled.
- The Kite path was verified with fake KiteConnect/KiteTicker objects in tests, not against a live
  account in this build environment.
