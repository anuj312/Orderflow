"""OrderFlow Studio — 20-session market intelligence · NSE (read-only live scanner).

Run:  python server.py      (reads .env; MARKET_MODE=demo|kite|live)
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from candles import TIMEFRAMES
from market_store import MarketStore
from market_time import IST, now_ist, parse_holidays, to_ist
from scanner import Scanner
from sectors import ALL_SYMBOLS, PRIMARY_SECTORS, SECTOR_DEFINITIONS

APP_NAME = "OrderFlow Studio"
APP_VERSION = "1.0.0"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
MAX_SSE_CLIENTS = 32
MAX_INGEST_BYTES = 512 * 1024
SYMBOL_SET = frozenset(ALL_SYMBOLS)

log = logging.getLogger("orderflow")


def _bool(v: Optional[str], default=False) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    mode: str = "demo"
    host: str = "127.0.0.1"
    port: int = 8000
    kite_api_key: str = field(default="", repr=False)
    kite_access_token: str = field(default="", repr=False)
    feed_token: str = field(default="", repr=False)
    cache_dir: str = ""
    strict_top3: bool = False
    publish_seconds: float = 2.0
    demo_interval: float = 4.0
    demo_start_minute: int = 135
    feed_fresh_seconds: float = 30.0
    viewer_label: str = "VIEWER"
    holidays: frozenset = frozenset()
    start_feeds: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(BASE_DIR / ".env", override=False)
        mode = (os.getenv("MARKET_MODE") or "demo").strip().lower()
        cache = os.getenv("HISTORY_CACHE_DIR") or str(BASE_DIR / ".history_cache")
        return cls(mode=mode, host=os.getenv("HOST") or "127.0.0.1", port=int(os.getenv("PORT") or 8000),
                   kite_api_key=(os.getenv("KITE_API_KEY") or "").strip(),
                   kite_access_token=(os.getenv("KITE_ACCESS_TOKEN") or "").strip(),
                   feed_token=(os.getenv("MARKET_FEED_TOKEN") or "").strip(), cache_dir=cache,
                   strict_top3=_bool(os.getenv("STRICT_TOP3_CONFIRMATION")),
                   publish_seconds=float(os.getenv("PUBLISH_SECONDS") or 2.0),
                   demo_interval=float(os.getenv("DEMO_INTERVAL_SECONDS") or 4.0),
                   demo_start_minute=int(os.getenv("DEMO_START_MINUTE") or 135),
                   feed_fresh_seconds=float(os.getenv("FEED_FRESH_SECONDS") or 30.0),
                   viewer_label=(os.getenv("VIEWER_LABEL") or "VIEWER")[:24],
                   holidays=frozenset(parse_holidays()))


# ------------------------------------------------------------------ JSON safety

def sanitize(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [sanitize(v) for v in obj]
    if isinstance(obj, datetime):
        return to_ist(obj).isoformat(timespec="seconds")
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def dumps(obj) -> str:
    return json.dumps(sanitize(obj), allow_nan=False, separators=(",", ":"))


def json_response(obj, status: int = 200, headers: Optional[dict] = None) -> Response:
    return Response(dumps(obj), status_code=status, media_type="application/json", headers=headers)


# ------------------------------------------------------------------ ingest models

PositivePrice = Field(gt=0, lt=10_000_000, allow_inf_nan=False)


class IngestQuote(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=20)
    ltp: float = PositivePrice
    prevClose: float = PositivePrice
    open: Optional[float] = Field(default=None, gt=0, lt=10_000_000, allow_inf_nan=False)
    high: Optional[float] = Field(default=None, gt=0, lt=10_000_000, allow_inf_nan=False)
    low: Optional[float] = Field(default=None, gt=0, lt=10_000_000, allow_inf_nan=False)
    volume: Optional[int] = Field(default=None, ge=0, le=10 ** 13)
    avgPrice: Optional[float] = Field(default=None, gt=0, lt=10_000_000, allow_inf_nan=False)
    oi: Optional[int] = Field(default=None, ge=0, le=10 ** 13)
    timestamp: datetime

    @field_validator("symbol")
    @classmethod
    def exact_symbol(cls, v: str) -> str:
        if v not in SYMBOL_SET:
            raise ValueError("unknown symbol")
        return v

    @model_validator(mode="after")
    def coherent(self):
        if self.high is not None and self.low is not None and self.high < self.low:
            raise ValueError("high < low")
        return self


class IngestBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quotes: list[IngestQuote] = Field(min_length=1, max_length=500)


# ------------------------------------------------------------------ runtime

class Runtime:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.store = MarketStore(cfg.mode, holidays=cfg.holidays)
        self.demo = None
        self.kite = None
        provider = None
        if cfg.mode == "demo":
            from demo_feed import DemoFeed
            self.demo = DemoFeed(self.store, interval=cfg.demo_interval, start_minute=cfg.demo_start_minute,
                                 holidays=cfg.holidays)
            provider = self.demo.chart_history
        elif cfg.mode == "kite":
            if not (cfg.kite_api_key and cfg.kite_access_token):
                self.store.set_feed(state="CONFIG_ERROR", connected=False,
                                    error="MARKET_MODE=kite needs KITE_API_KEY and KITE_ACCESS_TOKEN in .env.")
            else:
                from kite_feed import KiteFeed
                self.kite = KiteFeed(self.store, cfg.kite_api_key, cfg.kite_access_token, cache_dir=cfg.cache_dir)
                provider = self.kite.chart_history
        elif cfg.mode == "live":
            self.store.set_feed(state="WAITING_INGEST", connected=False,
                                detail="Awaiting authenticated POST /api/ingest batches")
            if not cfg.feed_token:
                self.store.set_feed(state="CONFIG_ERROR", error="MARKET_MODE=live needs MARKET_FEED_TOKEN in .env.")
            if cfg.kite_api_key and cfg.kite_access_token:
                from kite_feed import KiteFeed
                self.kite = KiteFeed(self.store, cfg.kite_api_key, cfg.kite_access_token, cache_dir=cfg.cache_dir,
                                     history_only=True)
                provider = self.kite.chart_history
        else:
            self.store.set_feed(state="CONFIG_ERROR", error="MARKET_MODE must be demo, kite or live.")
        self.scanner = Scanner(self.store, mode=cfg.mode, strict_top3=cfg.strict_top3,
                               feed_fresh_seconds=cfg.feed_fresh_seconds, history_provider=provider,
                               viewer_label=cfg.viewer_label,
                               wall_clock=None if cfg.mode == "demo" else now_ist)
        self.subscribers: set[asyncio.Queue] = set()
        self.last_payload: Optional[str] = None
        self._task: Optional[asyncio.Task] = None
        self.started_at = now_ist()

    def start_feeds(self) -> None:
        self.store.ensure_session()
        if self.demo:
            self.demo.start()
        if self.kite:
            self.kite.start()

    def stop(self) -> None:
        if self.demo:
            self.demo.stop()
        if self.kite:
            self.kite.stop()

    def publish_once(self) -> str:
        snap = self.scanner.build_snapshot(advance_picks=True)
        if self.kite:
            self.kite.prioritize(snap["leaders"]["bullish"] + snap["leaders"]["bearish"])
        payload = dumps(snap)
        self.last_payload = payload
        return payload

    def broadcast(self, payload: str) -> None:
        for q in list(self.subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    async def publish_loop(self) -> None:
        while True:
            try:
                payload = await asyncio.to_thread(self.publish_once)
                self.broadcast(payload)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("publish failed")
            await asyncio.sleep(self.cfg.publish_seconds)

    def health(self) -> dict:
        st = self.store
        errors = list(self.kite.errors) if self.kite else []
        return {
            "app": APP_NAME, "version": APP_VERSION, "mode": self.cfg.mode,
            "status": (self.scanner.last_snapshot or {}).get("market", {}).get("status"),
            "feed": {k: st.feed.get(k) for k in ("state", "connected", "detail", "error")},
            "mapping": {"requested": len(ALL_SYMBOLS), "mappedEquities": len(st.mapped),
                        "futuresMapped": len(st.futures), "unresolved": list(st.unresolved)},
            "history": dict(st.history),
            "ticks": st.tick_count, "marketDataRevision": st.market_revision,
            "lastTickAt": st.last_tick_wall, "sessionDate": st.session_date,
            "startedAt": self.started_at, "now": now_ist(),
            "sseClients": len(self.subscribers), "errors": errors,
        }


# ------------------------------------------------------------------ app

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                                "base-uri 'none'; form-action 'none'"),
}


def create_app(cfg: Optional[Config] = None) -> FastAPI:
    cfg = cfg or Config.from_env()
    rt = Runtime(cfg)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if cfg.start_feeds:
            await asyncio.to_thread(rt.start_feeds)
            rt._task = asyncio.create_task(rt.publish_loop())
        yield
        if rt._task:
            rt._task.cancel()
        rt.stop()

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.runtime = rt

    @app.middleware("http")
    async def headers_mw(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error on %s", request.url.path)
            response = json_response({"error": "internal_error", "detail": "Unexpected server error."}, 500)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            return json_response({"error": "http_error", "detail": exc.detail}, exc.status_code)
        if exc.status_code == 404:
            return FileResponse(STATIC_DIR / "404.html", status_code=404)
        return json_response({"error": "http_error", "detail": exc.detail}, exc.status_code)

    @app.exception_handler(404)
    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return json_response({"error": "not_found", "detail": "Not found"}, 404)
        return FileResponse(STATIC_DIR / "404.html", status_code=404)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    @app.get("/api/version")
    def version():
        return json_response({"app": APP_NAME, "version": APP_VERSION, "mode": cfg.mode,
                              "features": {"strictTop3Confirmation": cfg.strict_top3, "timeframes": list(TIMEFRAMES),
                                           "ingestEnabled": cfg.mode == "live", "orderPlacement": False,
                                           "backtest": False, "sse": True},
                              "publishSeconds": cfg.publish_seconds,
                              "demoIntervalSeconds": cfg.demo_interval if cfg.mode == "demo" else None})

    @app.get("/api/state")
    def state():
        snap = rt.scanner.last_snapshot
        if snap is None:
            snap = rt.scanner.build_snapshot(advance_picks=False)
        return json_response(snap)

    @app.get("/api/universe")
    def universe():
        return json_response({"allSymbols": ALL_SYMBOLS, "count": len(ALL_SYMBOLS),
                              "sectorDefinitions": SECTOR_DEFINITIONS, "primarySectors": list(PRIMARY_SECTORS),
                              "watchlists": ["NIFTY_50"]})

    @app.get("/api/health")
    def health():
        return json_response(rt.health())

    @app.get("/api/symbol/{symbol}")
    def symbol_detail(symbol: str, timeframe: str = Query("5m", max_length=4)):
        if symbol not in SYMBOL_SET:
            return json_response({"error": "unknown_symbol", "detail": "Symbol is not in the universe."}, 404)
        if timeframe not in TIMEFRAMES:
            return json_response({"error": "bad_timeframe", "detail": "timeframe must be one of 5m, 15m, 30m, 1h."}, 400)
        if symbol not in rt.store.mapped:
            return json_response({"error": "unmapped_symbol",
                                  "detail": "Symbol not resolved to an NSE instrument in this session."}, 404)
        return json_response(rt.scanner.symbol_detail(symbol, timeframe))

    @app.get("/api/events")
    async def events(request: Request):
        if len(rt.subscribers) >= MAX_SSE_CLIENTS:
            return json_response({"error": "too_many_clients"}, 503)
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        rt.subscribers.add(q)

        async def gen():
            try:
                yield "retry: 3000\n\n"
                if rt.last_payload:
                    yield f"event: snapshot\ndata: {rt.last_payload}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        data = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"event: snapshot\ndata: {data}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                rt.subscribers.discard(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/ingest")
    async def ingest(request: Request, x_feed_token: Optional[str] = Header(default=None)):
        if cfg.mode != "live":
            return json_response({"error": "disabled", "detail": "Ingest is disabled unless MARKET_MODE=live."}, 403)
        if not cfg.feed_token:
            return json_response({"error": "misconfigured", "detail": "MARKET_FEED_TOKEN is not configured."}, 503)
        if not x_feed_token or not hmac.compare_digest(x_feed_token.encode(), cfg.feed_token.encode()):
            return json_response({"error": "unauthorized"}, 401)
        if "application/json" not in (request.headers.get("content-type") or ""):
            return json_response({"error": "unsupported_media_type"}, 415)
        body = await request.body()
        if len(body) > MAX_INGEST_BYTES:
            return json_response({"error": "payload_too_large"}, 413)
        try:
            batch = IngestBatch.model_validate_json(body)
        except ValidationError as e:
            return json_response({"error": "invalid_batch",
                                  "detail": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()[:20]]}, 422)
        received = to_ist(rt.store.clock())
        ups, ois, rejected = [], [], []
        for qt in batch.quotes:
            ts = to_ist(qt.timestamp)
            if (ts - received).total_seconds() > 5:
                rejected.append({"symbol": qt.symbol, "reason": "timestamp in the future"})
                continue
            ups.append({"symbol": qt.symbol, "ltp": qt.ltp, "prevClose": qt.prevClose, "open": qt.open,
                        "high": qt.high, "low": qt.low, "volume": qt.volume, "avgPrice": qt.avgPrice,
                        "ts": ts, "tsSource": "INGEST"})
            if qt.oi is not None:
                ois.append({"symbol": qt.symbol, "oi": qt.oi, "ts": ts})
        changed = rt.store.apply_market_update(ups, ois, received=received) if (ups or ois) else 0
        rt.store.set_feed(state="INGESTING", connected=True, error=None)
        return json_response({"accepted": len(ups), "changed": changed, "rejected": rejected,
                              "marketDataRevision": rt.store.market_revision})

    return app


_app_instance: Optional[FastAPI] = None


def __getattr__(name: str):
    """Lets `uvicorn server:app` work (e.g. on Render) without building the app at import
    time, so tests and tools that import this module don't start a second runtime."""
    global _app_instance
    if name == "app":
        if _app_instance is None:
            _app_instance = create_app()
        return _app_instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    if cfg.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("HOST=%s exposes the scanner beyond localhost. Put real authentication in front of it.", cfg.host)
    log.info("Starting %s %s in MARKET_MODE=%s on http://%s:%s", APP_NAME, APP_VERSION, cfg.mode, cfg.host, cfg.port)
    import uvicorn
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="info", access_log=False)


if __name__ == "__main__":
    main()
