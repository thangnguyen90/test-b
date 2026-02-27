import asyncio
import json
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState
import websockets

from app.api.market import router as market_router
from app.api.ml import router as ml_router
from app.api.analytics import router as analytics_router
from app.api.orders import router as orders_router
from app.api.paper_trades import paper_trade_api
from app.api.paper_trades import router as paper_trades_router
from app.api.signals import get_scan_snapshot
from app.api.signals import router as signals_router
from app.core.config import settings
from app.deps import ml_predictor, price_stream, ws_manager
from app.models.orders import ApiHealth
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.paper_trading_engine import PaperTradingEngine

app = FastAPI(title=settings.app_name)
paper_trade_repo: MySQLTradeRepository | None = None
paper_trade_engine: PaperTradingEngine | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(orders_router)
app.include_router(signals_router)
app.include_router(ml_router)
app.include_router(market_router)
app.include_router(analytics_router)
app.include_router(paper_trades_router)


def _to_ws_symbol(symbol: str) -> str:
    # BTC/USDT or BTC/USDT:USDT -> btcusdt
    return symbol.replace(":USDT", "").replace("/", "").lower().strip()


def _from_ws_symbol(symbol: str) -> str:
    text = symbol.upper().strip()
    if text.endswith("USDT") and len(text) > 4:
        return f"{text[:-4]}/USDT"
    return text


def _ts_ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except Exception:
        return None


@app.on_event("startup")
async def on_startup() -> None:
    global paper_trade_repo, paper_trade_engine

    if settings.mysql_enabled:
        try:
            paper_trade_repo = MySQLTradeRepository(
                host=settings.mysql_host,
                port=settings.mysql_port,
                user=settings.mysql_user,
                password=settings.mysql_password,
                database=settings.mysql_database,
            )
            paper_trade_api.bind_repo(paper_trade_repo)
            paper_trade_engine = PaperTradingEngine(
                repo=paper_trade_repo,
                predictor=ml_predictor,
                min_win_probability=settings.paper_trade_min_win_probability,
                quantity=settings.paper_trade_quantity,
                leverage=settings.paper_trade_leverage,
                poll_interval_sec=settings.paper_trade_poll_interval_sec,
                min_sl_pct=settings.paper_trade_min_sl_pct,
                min_rr=settings.paper_trade_min_rr,
                max_risk_pct=settings.paper_trade_max_risk_pct,
                max_hold_minutes=settings.paper_trade_max_hold_minutes,
                disable_sl=settings.paper_trade_disable_sl,
                move_sl_to_entry_pnl_pct=settings.paper_trade_move_sl_to_entry_pnl_pct,
            )
            await paper_trade_engine.start()
        except Exception:
            paper_trade_repo = None
            paper_trade_engine = None
            paper_trade_api.bind_repo(None)

    await ws_manager.start()
    await price_stream.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if paper_trade_engine is not None:
        await paper_trade_engine.stop()
    await price_stream.stop()
    await ws_manager.stop()


@app.get("/health", response_model=ApiHealth)
def health() -> ApiHealth:
    return ApiHealth(
        status="ok",
        app_name=settings.app_name,
        environment=settings.app_env,
        timestamp=datetime.now(timezone.utc),
    )


@app.websocket("/ws/market")
async def market_socket(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


@app.websocket("/ws/signals")
async def signals_socket(
    websocket: WebSocket,
    min_win: float = 0.7,
    max_symbols: int = 80,
    interval_sec: float = 12.0,
) -> None:
    await websocket.accept()
    poll_interval = min(max(interval_sec, 6.0), 60.0)

    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                payload = get_scan_snapshot(min_win=min_win, max_symbols=max_symbols)
                await websocket.send_json({"type": "signals_scan", "data": payload})
            except Exception as exc:
                await websocket.send_json(
                    {
                        "type": "signals_error",
                        "error": str(exc),
                    }
                )
            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return


@app.websocket("/ws/price")
async def price_socket(
    websocket: WebSocket,
    symbol: str = "BTC/USDT",
    interval_sec: float = 0.5,
) -> None:
    await websocket.accept()
    poll_interval = min(max(interval_sec, 0.6), 5.0)
    ws_symbol = _to_ws_symbol(symbol)
    ws_url = f"wss://fstream.binance.com/ws/{ws_symbol}@markPrice@1s"

    async def _fallback_loop() -> None:
        while websocket.client_state == WebSocketState.CONNECTED:
            price, timestamp = await price_stream.get_price(symbol=symbol)
            if price is not None:
                await websocket.send_json(
                    {
                        "type": "price",
                        "symbol": symbol,
                        "price": price,
                        "source": "miniTicker_cache",
                        "timestamp": timestamp,
                    }
                )
            await asyncio.sleep(poll_interval)

    try:
        async with websockets.connect(ws_url, ping_interval=15, ping_timeout=15, close_timeout=5) as upstream:
            while websocket.client_state == WebSocketState.CONNECTED:
                raw = await upstream.recv()
                payload = json.loads(raw)
                if payload.get("e") != "markPriceUpdate":
                    continue
                px = payload.get("p")
                if px is None:
                    continue
                await websocket.send_json(
                    {
                        "type": "price",
                        "symbol": _from_ws_symbol(str(payload.get("s") or ws_symbol)),
                        "price": float(px),
                        "source": "markPrice",
                        "timestamp": _ts_ms_to_iso(payload.get("E")),
                        "funding_rate": float(payload["r"]) if payload.get("r") is not None else None,
                    }
                )
    except WebSocketDisconnect:
        return
    except Exception as exc:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(
                {
                    "type": "price_error",
                    "symbol": symbol,
                    "error": f"markPrice stream unavailable, fallback to cache: {exc}",
                }
            )
            await _fallback_loop()


@app.websocket("/ws/prices")
async def prices_socket(
    websocket: WebSocket,
    symbols: str = "BTC/USDT",
    interval_sec: float = 0.5,
) -> None:
    await websocket.accept()
    poll_interval = min(max(interval_sec, 0.6), 5.0)
    target_symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    if not target_symbols:
        target_symbols = ["BTC/USDT"]
    requested_by_ws_symbol: dict[str, list[str]] = {}
    for sym in target_symbols:
        ws_sym = _to_ws_symbol(sym)
        requested_by_ws_symbol.setdefault(ws_sym, []).append(sym)

    ws_streams = [f"{ws_sym}@markPrice@1s" for ws_sym in sorted(set(requested_by_ws_symbol.keys()))]
    ws_url = f"wss://fstream.binance.com/stream?streams={'/'.join(ws_streams)}"
    latest: dict[str, float] = {}

    async def _fallback_loop() -> None:
        while websocket.client_state == WebSocketState.CONNECTED:
            prices, stamp = await price_stream.get_prices(target_symbols)
            await websocket.send_json(
                {
                    "type": "prices",
                    "symbols": target_symbols,
                    "prices": prices,
                    "timestamp": stamp,
                    "source": "miniTicker_cache",
                }
            )
            await asyncio.sleep(poll_interval)

    try:
        async with websockets.connect(ws_url, ping_interval=15, ping_timeout=15, close_timeout=5) as upstream:
            while websocket.client_state == WebSocketState.CONNECTED:
                raw = await upstream.recv()
                msg = json.loads(raw)
                stream_data = msg.get("data") if isinstance(msg, dict) else None
                if not isinstance(stream_data, dict):
                    continue
                if stream_data.get("e") != "markPriceUpdate":
                    continue
                ws_symbol_from_stream = str(stream_data.get("s") or "").lower().strip()
                price_raw = stream_data.get("p")
                if ws_symbol_from_stream and price_raw is not None:
                    px = float(price_raw)
                    for requested_symbol in requested_by_ws_symbol.get(ws_symbol_from_stream, []):
                        latest[requested_symbol] = px
                await websocket.send_json(
                    {
                        "type": "prices",
                        "symbols": target_symbols,
                        "prices": latest,
                        "timestamp": _ts_ms_to_iso(stream_data.get("E")),
                        "source": "markPrice",
                    }
                )
    except WebSocketDisconnect:
        return
    except Exception as exc:
        if websocket.client_state == WebSocketState.CONNECTED:
            await websocket.send_json(
                {
                    "type": "prices_error",
                    "symbols": target_symbols,
                    "error": f"markPrice stream unavailable, fallback to cache: {exc}",
                }
            )
            await _fallback_loop()
