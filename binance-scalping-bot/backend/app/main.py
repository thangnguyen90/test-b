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
from app.deps import (
    auto_trainer,
    bind_paper_trade_runtime,
    liquid_ml_predictor,
    ml_predictor,
    ml_test_predictor,
    price_stream,
    ws_manager,
)
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


@app.on_event("startup")
async def on_startup() -> None:
    global paper_trade_repo, paper_trade_engine

    paper_trade_api.bind_price_stream(price_stream)
    paper_trade_api.bind_major_symbol_resolver(None)
    bind_paper_trade_runtime(None, None)
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
                predictor_test=ml_test_predictor,
                liquid_predictor=liquid_ml_predictor,
                price_stream=price_stream,
                min_win_probability=settings.paper_trade_min_win_probability,
                quantity=settings.paper_trade_quantity,
                order_usdt=settings.paper_trade_order_usdt,
                margin_usdt=settings.paper_trade_margin_usdt,
                leverage=settings.paper_trade_leverage,
                major_symbols=settings.paper_trade_major_symbols,
                major_dynamic_enabled=settings.paper_trade_major_dynamic_enabled,
                major_dynamic_refresh_sec=settings.paper_trade_major_dynamic_refresh_sec,
                major_dynamic_limit=settings.paper_trade_major_dynamic_limit,
                major_dynamic_candidates=settings.paper_trade_major_dynamic_candidates,
                major_dynamic_candle_lookback=settings.paper_trade_major_dynamic_candle_lookback,
                major_symbol_leverage=settings.paper_trade_major_leverage,
                major_symbol_max_risk_pct=settings.paper_trade_major_max_risk_pct,
                poll_interval_sec=settings.paper_trade_poll_interval_sec,
                stream_max_stale_sec=settings.paper_trade_stream_max_stale_sec,
                min_sl_pct=settings.paper_trade_min_sl_pct,
                min_sl_loss_pct=settings.paper_trade_min_sl_loss_pct,
                sl_extra_buffer_pct=settings.paper_trade_sl_extra_buffer_pct,
                sl_atr_multiplier=settings.paper_trade_sl_atr_multiplier,
                sl_atr_timeframe=settings.paper_trade_sl_atr_timeframe,
                sl_atr_limit=settings.paper_trade_sl_atr_limit,
                min_rr=settings.paper_trade_min_rr,
                maint_margin_rate=settings.paper_trade_maint_margin_rate,
                max_risk_pct=settings.paper_trade_max_risk_pct,
                max_hold_minutes=settings.paper_trade_max_hold_minutes,
                disable_sl=settings.paper_trade_disable_sl,
                move_sl_to_entry_pnl_pct=settings.paper_trade_move_sl_to_entry_pnl_pct,
                move_sl_lock_pnl_pct=settings.paper_trade_move_sl_lock_pnl_pct,
                move_sl_scale_by_leverage=settings.paper_trade_move_sl_scale_by_leverage,
                move_sl_reference_leverage=settings.paper_trade_move_sl_reference_leverage,
                liquid_enabled=settings.liquid_ml_enabled,
                liquid_min_win_probability=settings.liquid_ml_min_win,
                liquid_top_vol_days=settings.liquid_ml_top_vol_days,
                liquid_max_symbols=settings.liquid_ml_max_symbols,
                liquid_entry_tolerance_pct=settings.liquid_ml_touch_tolerance_pct,
                btc_filter_enabled=settings.paper_trade_btc_filter_enabled,
                btc_filter_timeframe=settings.paper_trade_btc_filter_timeframe,
                btc_filter_cache_sec=settings.paper_trade_btc_filter_cache_sec,
                btc_filter_min_confidence=settings.paper_trade_btc_filter_min_confidence,
                btc_filter_block_countertrend=settings.paper_trade_btc_filter_block_countertrend,
                btc_filter_countertrend_min_win=settings.paper_trade_btc_filter_countertrend_min_win,
                btc_shock_pause_enabled=settings.paper_trade_btc_shock_pause_enabled,
                btc_shock_threshold_pct=settings.paper_trade_btc_shock_threshold_pct,
                btc_shock_cooldown_minutes=settings.paper_trade_btc_shock_cooldown_minutes,
                btc_shock_up_long_block_minutes=settings.paper_trade_btc_shock_up_long_block_minutes,
                btc_shock_down_short_block_minutes=settings.paper_trade_btc_shock_down_short_block_minutes,
                btc_shock_up_require_pullback=settings.paper_trade_btc_shock_up_require_pullback,
                btc_shock_pullback_ema_period=settings.paper_trade_btc_shock_pullback_ema_period,
                btc_shock_pullback_tolerance_pct=settings.paper_trade_btc_shock_pullback_tolerance_pct,
                btc_reversal_profit_exit_enabled=settings.paper_trade_btc_reversal_profit_exit_enabled,
                btc_reversal_threshold_pct=settings.paper_trade_btc_reversal_threshold_pct,
                btc_reversal_min_confidence=settings.paper_trade_btc_reversal_min_confidence,
                btc_profit_lock_enabled=settings.paper_trade_btc_profit_lock_enabled,
                btc_profit_lock_min_confidence=settings.paper_trade_btc_profit_lock_min_confidence,
                btc_follow_min_corr=settings.paper_trade_btc_follow_min_corr,
                btc_follow_min_beta=settings.paper_trade_btc_follow_min_beta,
                btc_follow_lookback=settings.paper_trade_btc_follow_lookback,
                btc_follow_cache_sec=settings.paper_trade_btc_follow_cache_sec,
                test_ml_enabled=settings.paper_trade_test_ml_enabled,
                test_ml_min_win_probability=settings.paper_trade_test_ml_min_win,
                test_ml_max_symbols=settings.paper_trade_test_ml_max_symbols,
                test_ml_max_orders_per_cycle=settings.paper_trade_test_ml_max_orders_per_cycle,
            )
            paper_trade_api.bind_major_symbol_resolver(paper_trade_engine.is_major_symbol)
            bind_paper_trade_runtime(paper_trade_repo, paper_trade_engine)
            await paper_trade_engine.start()
        except Exception:
            paper_trade_repo = None
            paper_trade_engine = None
            paper_trade_api.bind_repo(None)
            paper_trade_api.bind_major_symbol_resolver(None)
            bind_paper_trade_runtime(None, None)

    await ws_manager.start()
    await price_stream.start()
    await auto_trainer.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if paper_trade_engine is not None:
        await paper_trade_engine.stop()
    await auto_trainer.stop()
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
                payload = await asyncio.to_thread(
                    get_scan_snapshot,
                    min_win=min_win,
                    max_symbols=max_symbols,
                )
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

    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            price, timestamp = await price_stream.get_price(symbol=symbol)
            if price is not None:
                await websocket.send_json(
                    {
                        "type": "price",
                        "symbol": symbol,
                        "price": float(price),
                        "source": "stream_cache",
                        "timestamp": timestamp,
                    }
                )
            else:
                await websocket.send_json(
                    {
                        "type": "price_error",
                        "symbol": symbol,
                        "error": "No price in stream cache yet",
                    }
                )
            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return
    except Exception:
        return


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

    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            prices, stamp, timestamps = await price_stream.get_prices(target_symbols)
            await websocket.send_json(
                {
                    "type": "prices",
                    "symbols": target_symbols,
                    "prices": prices,
                    "timestamp": stamp,
                    "timestamps": timestamps,
                    "source": "stream_cache",
                }
            )
            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return
    except Exception:
        return
