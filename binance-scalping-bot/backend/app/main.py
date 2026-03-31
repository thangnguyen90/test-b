import asyncio
from datetime import datetime, timezone
import logging

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
    candle_pattern_refresher,
    bind_paper_trade_runtime,
    liquid_ml_predictor,
    ml_candles_predictor,
    ml_predictor,
    ml_test_predictor,
    price_stream,
    ws_manager,
)
from app.models.orders import ApiHealth
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.paper_trading_engine import PaperTradingEngine

logger = logging.getLogger(__name__)

def _warm_signal_predictors() -> None:
    warm_symbol = 'BTC/USDT'
    warm_price = 100000.0
    try:
        ml_predictor.predict(symbol=warm_symbol, mark_price=warm_price)
    except Exception as exc:
        logger.warning('ML predictor warm-up failed: %s', exc)
    try:
        ml_candles_predictor.predict(symbol=warm_symbol, mark_price=warm_price)
    except Exception as exc:
        logger.warning('ML candles predictor warm-up failed: %s', exc)

app = FastAPI(title=settings.app_name)
paper_trade_repo: MySQLTradeRepository | None = None
paper_trade_candle_repo: MySQLTradeRepository | None = None
paper_trade_engine: PaperTradingEngine | None = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
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
    global paper_trade_repo, paper_trade_candle_repo, paper_trade_engine

    paper_trade_api.bind_price_stream(price_stream)
    paper_trade_api.bind_major_symbol_resolver(None)
    paper_trade_api.bind_btc_follow_resolver(None)
    bind_paper_trade_runtime(None, None)
    paper_trade_api.bind_candle_repo(None)
    if settings.mysql_enabled:
        try:
            primary_database = settings.mysql_candle_database or settings.mysql_database
            paper_trade_repo = MySQLTradeRepository(
                host=settings.mysql_host,
                port=settings.mysql_port,
                user=settings.mysql_user,
                password=settings.mysql_password,
                database=primary_database,
            )
            # All models now share a single trading DB (trading_bot_candle).
            paper_trade_candle_repo = paper_trade_repo
            try:
                paper_trade_repo.refresh_hourly_profiles(lookback_days=settings.paper_trade_hourly_profile_lookback_days)
            except Exception:
                pass
            paper_trade_api.bind_repo(paper_trade_repo)
            paper_trade_api.bind_candle_repo(paper_trade_candle_repo)
            paper_trade_engine = PaperTradingEngine(
                repo=paper_trade_repo,
                predictor=ml_predictor,
                predictor_test=ml_test_predictor,
                predictor_candles=ml_candles_predictor,
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
                entry_require_fresh_stream_price=settings.paper_trade_entry_require_fresh_stream_price,
                min_sl_pct=settings.paper_trade_min_sl_pct,
                min_sl_loss_pct=settings.paper_trade_min_sl_loss_pct,
                sl_extra_buffer_pct=settings.paper_trade_sl_extra_buffer_pct,
                sl_atr_multiplier=settings.paper_trade_sl_atr_multiplier,
                sl_atr_timeframe=settings.paper_trade_sl_atr_timeframe,
                sl_atr_limit=settings.paper_trade_sl_atr_limit,
                min_rr=settings.paper_trade_min_rr,
                maint_margin_rate=settings.paper_trade_maint_margin_rate,
                max_risk_pct=settings.paper_trade_max_risk_pct,
                max_margin_loss_pct=settings.paper_trade_max_margin_loss_pct,
                max_margin_loss_high_atr_pct=settings.paper_trade_max_margin_loss_high_atr_pct,
                max_margin_loss_high_atr_threshold_pct=settings.paper_trade_max_margin_loss_high_atr_threshold_pct,
                max_margin_loss_aligned_regime_bonus_pct=settings.paper_trade_max_margin_loss_aligned_regime_bonus_pct,
                max_margin_loss_countertrend_penalty_pct=settings.paper_trade_max_margin_loss_countertrend_penalty_pct,
                max_hold_minutes=settings.paper_trade_max_hold_minutes,
                negative_recovery_exit_enabled=settings.paper_trade_negative_recovery_exit_enabled,
                negative_recovery_exit_arm_after_minutes=settings.paper_trade_negative_recovery_exit_arm_after_minutes,
                negative_recovery_exit_negative_pnl_pct=settings.paper_trade_negative_recovery_exit_negative_pnl_pct,
                negative_recovery_exit_recover_pnl_pct=settings.paper_trade_negative_recovery_exit_recover_pnl_pct,
                hourly_transition_guard_enabled=settings.paper_trade_hourly_transition_guard_enabled,
                hourly_transition_start_minute=settings.paper_trade_hourly_transition_start_minute,
                hourly_transition_force_close_end_minute=settings.paper_trade_hourly_transition_force_close_end_minute,
                hourly_transition_entry_block_before_minutes=settings.paper_trade_hourly_transition_entry_block_before_minutes,
                hourly_transition_entry_block_after_minutes=settings.paper_trade_hourly_transition_entry_block_after_minutes,
                hourly_transition_min_hold_minutes=settings.paper_trade_hourly_transition_min_hold_minutes,
                hourly_transition_safe_pnl_pct=settings.paper_trade_hourly_transition_safe_pnl_pct,
                funding_guard_enabled=settings.paper_trade_funding_guard_enabled,
                funding_guard_force_close_before_minutes=settings.paper_trade_funding_guard_force_close_before_minutes,
                funding_guard_force_close_after_minutes=settings.paper_trade_funding_guard_force_close_after_minutes,
                funding_guard_entry_block_before_minutes=settings.paper_trade_funding_guard_entry_block_before_minutes,
                funding_guard_entry_block_after_minutes=settings.paper_trade_funding_guard_entry_block_after_minutes,
                funding_guard_min_hold_minutes=settings.paper_trade_funding_guard_min_hold_minutes,
                funding_guard_safe_pnl_pct=settings.paper_trade_funding_guard_safe_pnl_pct,
                session_open_guard_enabled=settings.paper_trade_session_open_guard_enabled,
                session_open_guard_sessions=settings.paper_trade_session_open_guard_sessions,
                session_open_guard_force_close_before_minutes=settings.paper_trade_session_open_guard_force_close_before_minutes,
                session_open_guard_force_close_after_minutes=settings.paper_trade_session_open_guard_force_close_after_minutes,
                session_open_guard_entry_block_before_minutes=settings.paper_trade_session_open_guard_entry_block_before_minutes,
                session_open_guard_entry_block_after_minutes=settings.paper_trade_session_open_guard_entry_block_after_minutes,
                session_open_guard_min_hold_minutes=settings.paper_trade_session_open_guard_min_hold_minutes,
                session_open_guard_safe_pnl_pct=settings.paper_trade_session_open_guard_safe_pnl_pct,
                macro_event_guard_enabled=settings.paper_trade_macro_event_guard_enabled,
                macro_event_guard_keywords=settings.paper_trade_macro_event_guard_keywords,
                macro_event_guard_entry_block_before_minutes=settings.paper_trade_macro_event_guard_entry_block_before_minutes,
                macro_event_guard_entry_block_after_minutes=settings.paper_trade_macro_event_guard_entry_block_after_minutes,
                macro_event_guard_force_close_before_minutes=settings.paper_trade_macro_event_guard_force_close_before_minutes,
                macro_event_guard_force_close_after_minutes=settings.paper_trade_macro_event_guard_force_close_after_minutes,
                macro_event_guard_min_hold_minutes=settings.paper_trade_macro_event_guard_min_hold_minutes,
                macro_event_guard_safe_pnl_pct=settings.paper_trade_macro_event_guard_safe_pnl_pct,
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
                btc_filter_block_nonfollow_countertrend=settings.paper_trade_btc_filter_block_nonfollow_countertrend,
                btc_filter_nonfollow_countertrend_min_confidence=settings.paper_trade_btc_filter_nonfollow_countertrend_min_confidence,
                btc_filter_nonfollow_countertrend_min_win=settings.paper_trade_btc_filter_nonfollow_countertrend_min_win,
                btc_trend_hour_lock_enabled=settings.paper_trade_btc_trend_hour_lock_enabled,
                btc_trend_hour_lock_min_confidence=settings.paper_trade_btc_trend_hour_lock_min_confidence,
                btc_trend_hour_lock_countertrend_hours=settings.paper_trade_btc_trend_hour_lock_countertrend_hours,
                btc_trend_hour_lock_apply_non_btc_follow=settings.paper_trade_btc_trend_hour_lock_apply_non_btc_follow,
                btc_shock_pause_enabled=settings.paper_trade_btc_shock_pause_enabled,
                btc_shock_threshold_pct=settings.paper_trade_btc_shock_threshold_pct,
                btc_shock_cooldown_minutes=settings.paper_trade_btc_shock_cooldown_minutes,
                btc_shock_up_long_block_minutes=settings.paper_trade_btc_shock_up_long_block_minutes,
                btc_shock_down_short_block_minutes=settings.paper_trade_btc_shock_down_short_block_minutes,
                btc_shock_up_require_pullback=settings.paper_trade_btc_shock_up_require_pullback,
                btc_shock_pullback_ema_period=settings.paper_trade_btc_shock_pullback_ema_period,
                btc_shock_pullback_tolerance_pct=settings.paper_trade_btc_shock_pullback_tolerance_pct,
                btc_kill_short_guard_enabled=settings.paper_trade_btc_kill_short_guard_enabled,
                btc_kill_short_pump_min_body_pct=settings.paper_trade_btc_kill_short_pump_min_body_pct,
                btc_kill_short_ema_tolerance_pct=settings.paper_trade_btc_kill_short_ema_tolerance_pct,
                btc_reversal_profit_exit_enabled=settings.paper_trade_btc_reversal_profit_exit_enabled,
                btc_reversal_threshold_pct=settings.paper_trade_btc_reversal_threshold_pct,
                btc_reversal_min_confidence=settings.paper_trade_btc_reversal_min_confidence,
                btc_reversal_min_profit_pct=settings.paper_trade_btc_reversal_min_profit_pct,
                btc_reversal_loss_exit_enabled=settings.paper_trade_btc_reversal_loss_exit_enabled,
                btc_reversal_loss_exit_days_vn=settings.paper_trade_btc_reversal_loss_exit_days_vn,
                btc_reversal_loss_exit_min_loss_pct=settings.paper_trade_btc_reversal_loss_exit_min_loss_pct,
                btc_reversal_entry_block_enabled=settings.paper_trade_btc_reversal_entry_block_enabled,
                btc_reversal_entry_cooldown_minutes=settings.paper_trade_btc_reversal_entry_cooldown_minutes,
                btc_profit_lock_enabled=settings.paper_trade_btc_profit_lock_enabled,
                btc_profit_lock_min_confidence=settings.paper_trade_btc_profit_lock_min_confidence,
                btc_follow_min_corr=settings.paper_trade_btc_follow_min_corr,
                btc_follow_min_beta=settings.paper_trade_btc_follow_min_beta,
                btc_follow_lookback=settings.paper_trade_btc_follow_lookback,
                btc_follow_cache_sec=settings.paper_trade_btc_follow_cache_sec,
                base_ml_max_symbols=settings.paper_trade_base_ml_max_symbols,
                basic_ml_pattern_gate_enabled=settings.paper_trade_basic_ml_pattern_gate_enabled,
                basic_ml_pattern_min_win_rate_pct=settings.paper_trade_basic_ml_pattern_min_win_rate_pct,
                test_ml_enabled=settings.paper_trade_test_ml_enabled,
                test_ml_min_win_probability=settings.paper_trade_test_ml_min_win,
                test_ml_max_symbols=settings.paper_trade_test_ml_max_symbols,
                test_ml_max_orders_per_cycle=settings.paper_trade_test_ml_max_orders_per_cycle,
                candles_bg_enabled=settings.paper_trade_candles_bg_enabled,
                candles_bg_min_win_probability=settings.paper_trade_candles_bg_min_win,
                candles_bg_max_symbols=settings.paper_trade_candles_bg_max_symbols,
                candles_bg_max_orders_per_cycle=settings.paper_trade_candles_bg_max_orders_per_cycle,
                candles_bg_entry_type="ML_CANDLES_BG",
                candles_bg_block_hours_vn=settings.paper_trade_candles_bg_block_hours_vn,
                candles_bg_long_block_hours_vn=settings.paper_trade_candles_bg_long_block_hours_vn,
                candles_bg_short_block_hours_vn=settings.paper_trade_candles_bg_short_block_hours_vn,
                candles_bg_long_strict_hours_vn=settings.paper_trade_candles_bg_long_strict_hours_vn,
                candles_bg_short_strict_hours_vn=settings.paper_trade_candles_bg_short_strict_hours_vn,
                candles_bg_strict_min_win_bonus=settings.paper_trade_candles_bg_strict_min_win_bonus,
                candles_bg_discord_webhook_enabled=settings.paper_trade_ml_candles_bg_discord_webhook_enabled,
                candles_bg_discord_webhook_url=settings.paper_trade_ml_candles_bg_discord_webhook_url,
                candles_bg_discord_webhook_username=settings.paper_trade_ml_candles_bg_discord_webhook_username,
                single_position_per_symbol_side=settings.paper_trade_single_position_per_symbol_side,
                reentry_cooldown_minutes=settings.paper_trade_reentry_cooldown_minutes,
                reentry_after_sl_cooldown_minutes=settings.paper_trade_reentry_after_sl_cooldown_minutes,
                instant_sl_guard_enabled=settings.paper_trade_instant_sl_guard_enabled,
                instant_sl_guard_max_hold_minutes=settings.paper_trade_instant_sl_guard_max_hold_minutes,
                instant_sl_guard_min_abs_pnl_pct=settings.paper_trade_instant_sl_guard_min_abs_pnl_pct,
                instant_sl_guard_min_abs_mae_pct=settings.paper_trade_instant_sl_guard_min_abs_mae_pct,
                instant_sl_guard_cooldown_minutes=settings.paper_trade_instant_sl_guard_cooldown_minutes,
                instant_sl_guard_short_top_test_bypass_enabled=settings.paper_trade_instant_sl_guard_short_top_test_bypass_enabled,
                instant_sl_guard_short_top_test_lookback_candles=settings.paper_trade_instant_sl_guard_short_top_test_lookback_candles,
                instant_sl_guard_short_top_test_tolerance_pct=settings.paper_trade_instant_sl_guard_short_top_test_tolerance_pct,
                instant_sl_guard_short_rejection_min_upper_wick_ratio=settings.paper_trade_instant_sl_guard_short_rejection_min_upper_wick_ratio,
                instant_sl_guard_short_rejection_min_wick_body_ratio=settings.paper_trade_instant_sl_guard_short_rejection_min_wick_body_ratio,
                instant_sl_guard_short_top_test_cache_sec=settings.paper_trade_instant_sl_guard_short_top_test_cache_sec,
                instant_sl_global_guard_enabled=settings.paper_trade_instant_sl_global_guard_enabled,
                instant_sl_global_threshold=settings.paper_trade_instant_sl_global_threshold,
                instant_sl_global_window_minutes=settings.paper_trade_instant_sl_global_window_minutes,
                instant_sl_global_cooldown_minutes=settings.paper_trade_instant_sl_global_cooldown_minutes,
                entry_hard_block_hours_vn=settings.paper_trade_entry_hard_block_hours_vn,
                limit_long_block_hours_vn=settings.paper_trade_limit_long_block_hours_vn,
                hourly_profile_enabled=settings.paper_trade_hourly_profile_enabled,
                hourly_profile_min_samples=settings.paper_trade_hourly_profile_min_samples,
                hourly_profile_prob_alpha=settings.paper_trade_hourly_profile_prob_alpha,
                hourly_profile_refresh_sec=settings.paper_trade_hourly_profile_refresh_sec,
                hourly_profile_lookback_days=settings.paper_trade_hourly_profile_lookback_days,
                hourly_profile_use_weekday=settings.paper_trade_hourly_profile_use_weekday,
                hourly_profile_use_btc_trend=settings.paper_trade_hourly_profile_use_btc_trend,
                hourly_profile_btc_trend_min_confidence=settings.paper_trade_hourly_profile_btc_trend_min_confidence,
                hourly_bad_window_enabled=settings.paper_trade_hourly_bad_window_enabled,
                hourly_bad_window_min_samples=settings.paper_trade_hourly_bad_window_min_samples,
                hourly_bad_window_block_win_rate_pct=settings.paper_trade_hourly_bad_window_block_win_rate_pct,
                hourly_bad_window_strict_win_rate_pct=settings.paper_trade_hourly_bad_window_strict_win_rate_pct,
                hourly_bad_window_strict_min_win_bonus=settings.paper_trade_hourly_bad_window_strict_min_win_bonus,
                hourly_bad_window_countertrend_hard_block=settings.paper_trade_hourly_bad_window_countertrend_hard_block,
                bullish_short_nonfollow_max_open_ratio=settings.paper_trade_bullish_short_nonfollow_max_open_ratio,
                bullish_short_nonfollow_min_win_bonus=settings.paper_trade_bullish_short_nonfollow_min_win_bonus,
                short_sl_streak_guard_enabled=settings.paper_trade_short_sl_streak_guard_enabled,
                short_sl_streak_threshold=settings.paper_trade_short_sl_streak_threshold,
                short_sl_streak_cooldown_minutes=settings.paper_trade_short_sl_streak_cooldown_minutes,
                short_sl_streak_refresh_sec=settings.paper_trade_short_sl_streak_refresh_sec,
            )
            paper_trade_api.bind_major_symbol_resolver(paper_trade_engine.is_major_symbol)
            paper_trade_api.bind_btc_follow_resolver(paper_trade_engine.is_symbol_following_btc)
            bind_paper_trade_runtime(paper_trade_repo, paper_trade_engine)
            await paper_trade_engine.start()
        except Exception:
            paper_trade_repo = None
            paper_trade_engine = None
            paper_trade_api.bind_repo(None)
            paper_trade_api.bind_major_symbol_resolver(None)
            paper_trade_api.bind_btc_follow_resolver(None)
            bind_paper_trade_runtime(None, None)

    await asyncio.to_thread(_warm_signal_predictors)
    await ws_manager.start()
    await price_stream.start()
    await auto_trainer.start()
    await candle_pattern_refresher.start()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if paper_trade_engine is not None:
        await paper_trade_engine.stop()
    await candle_pattern_refresher.stop()
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


@app.websocket("/ws/ml-candles/compare")
async def ml_candles_compare_socket(
    websocket: WebSocket,
    interval_sec: float = 6.0,
    history_limit: int = 200,
) -> None:
    await websocket.accept()
    poll_interval = min(max(interval_sec, 3.0), 30.0)

    def fetch_data(repo_scope: str, is_open: bool):
        repo = paper_trade_api._resolve_repo(repo_scope=repo_scope)
        if is_open:
            rows = repo.list_open_trades()
            btc_follow_map = paper_trade_api._resolve_btc_follow_map(rows)
        else:
            rows, _ = repo.list_recent_trades_paged(page=1, page_size=history_limit)
            btc_follow_map = {}
        
        return [
            paper_trade_api._map_trade(row, btc_following=btc_follow_map.get(str(row.get("symbol") or ""))).dict()
            for row in rows
        ]

    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                (
                    main_open_items,
                    candles_open_items,
                    main_history_items,
                    candles_history_items,
                ) = await asyncio.wait_for(
                    asyncio.gather(
                        asyncio.to_thread(fetch_data, repo_scope="main", is_open=True),
                        asyncio.to_thread(fetch_data, repo_scope="candles", is_open=True),
                        asyncio.to_thread(fetch_data, repo_scope="main", is_open=False),
                        asyncio.to_thread(fetch_data, repo_scope="candles", is_open=False),
                    ),
                    timeout=max(8.0, poll_interval * 2),
                )

                payload = {
                    "main_open_items": main_open_items,
                    "candles_open_items": candles_open_items,
                    "main_history_items": main_history_items,
                    "candles_history_items": candles_history_items,
                }

                await websocket.send_json({"type": "ml_candles_compare_sync", "data": payload})
            except asyncio.TimeoutError:
                logger.warning("ml_candles_compare_socket sync timed out")
                try:
                    await websocket.send_json(
                        {
                            "type": "ml_candles_compare_error",
                            "error": "Compare sync timed out",
                        }
                    )
                except Exception:
                    pass
            except Exception as exc:
                try:
                    await websocket.send_json({"type": "ml_candles_compare_error", "error": str(exc)})
                except Exception:
                    pass
            await asyncio.sleep(poll_interval)
    except Exception:
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
    max_symbols = 160
    requested_symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    deduped_symbols: list[str] = []
    seen_symbols: set[str] = set()
    for symbol in requested_symbols:
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        deduped_symbols.append(symbol)
    truncated = len(deduped_symbols) > max_symbols
    target_symbols = deduped_symbols[:max_symbols]
    if not target_symbols:
        target_symbols = ["BTC/USDT"]
        truncated = False

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
                    "truncated": truncated,
                    "requested_symbol_count": len(deduped_symbols),
                }
            )
            await asyncio.sleep(poll_interval)
    except WebSocketDisconnect:
        return
    except Exception:
        return
