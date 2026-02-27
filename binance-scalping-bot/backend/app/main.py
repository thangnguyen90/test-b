import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.websockets import WebSocketState

from app.api.market import router as market_router
from app.api.ml import router as ml_router
from app.api.analytics import router as analytics_router
from app.api.orders import router as orders_router
from app.api.paper_trades import paper_trade_api
from app.api.paper_trades import router as paper_trades_router
from app.api.signals import get_scan_snapshot
from app.api.signals import router as signals_router
from app.core.config import settings
from app.deps import ml_predictor, ws_manager
from app.models.orders import ApiHealth
from app.services.binance_client import BinanceFuturesClient
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.paper_trading_engine import PaperTradingEngine

app = FastAPI(title=settings.app_name)
price_client = BinanceFuturesClient()
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
            )
            await paper_trade_engine.start()
        except Exception:
            paper_trade_repo = None
            paper_trade_engine = None
            paper_trade_api.bind_repo(None)

    await ws_manager.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if paper_trade_engine is not None:
        await paper_trade_engine.stop()
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
            payload = get_scan_snapshot(min_win=min_win, max_symbols=max_symbols)
            await websocket.send_json({"type": "signals_scan", "data": payload})
            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return


@app.websocket("/ws/price")
async def price_socket(
    websocket: WebSocket,
    symbol: str = "BTC/USDT",
    interval_sec: float = 2.0,
) -> None:
    await websocket.accept()
    poll_interval = min(max(interval_sec, 1.0), 10.0)

    def _resolve_price() -> tuple[float, str, str | None]:
        ticker = price_client.fetch_ticker(symbol=symbol)
        last = ticker.get("last")
        if last is None:
            last = ticker.get("close")
        if last is None:
            bid = ticker.get("bid")
            ask = ticker.get("ask")
            if bid is not None and ask is not None:
                last = (bid + ask) / 2
        if last is not None:
            return float(last), "ticker", ticker.get("datetime")

        rows = price_client.fetch_ohlcv(symbol=symbol, timeframe="1m", limit=2)
        if rows:
            return float(rows[-1][4]), "ohlcv", None
        raise RuntimeError("No price data")

    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                price, source, timestamp = _resolve_price()
                await websocket.send_json(
                    {
                        "type": "price",
                        "symbol": symbol,
                        "price": price,
                        "source": source,
                        "timestamp": timestamp,
                    }
                )
            except Exception as exc:
                await websocket.send_json(
                    {
                        "type": "price_error",
                        "symbol": symbol,
                        "error": str(exc),
                    }
                )
            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return
