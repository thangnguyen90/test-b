from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
import time
import traceback
from typing import Any

from app.api.signals import get_cached_symbols_snapshot, get_scan_snapshot
from app.core.config import settings
from app.services.binance_client import BinanceFuturesClient
from app.services.liquidation_ml_predictor import LiquidationMLPredictor
from app.services.ml_predictor import MLPredictor
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.risk_manager import (
    calc_atr_from_ohlcv,
    calc_estimated_margin_ratio_pct,
    calc_min_sl_pct_from_loss,
    calc_margin_usdt,
    calc_quantity_from_order_usdt,
    normalize_tp_sl,
)


class PaperTradingEngine:
    def __init__(
        self,
        repo: MySQLTradeRepository,
        predictor: MLPredictor,
        predictor_test: MLPredictor | None = None,
        liquid_predictor: LiquidationMLPredictor | None = None,
        price_stream: Any | None = None,
        min_win_probability: float = 0.75,
        quantity: float = 0.01,
        order_usdt: float = 10.0,
        margin_usdt: float = 0.0,
        leverage: int = 5,
        major_symbols: list[str] | None = None,
        major_dynamic_enabled: bool = True,
        major_dynamic_refresh_sec: int = 180,
        major_dynamic_limit: int = 8,
        major_dynamic_candidates: int = 30,
        major_dynamic_candle_lookback: int = 24,
        major_symbol_leverage: int = 10,
        major_symbol_max_risk_pct: float = 20.0,
        poll_interval_sec: float = 6.0,
        stream_max_stale_sec: float = 5.0,
        min_sl_pct: float = 0.004,
        min_sl_loss_pct: float = 5.0,
        sl_extra_buffer_pct: float = 0.0,
        sl_atr_multiplier: float = 0.0,
        sl_atr_timeframe: str = "5m",
        sl_atr_limit: int = 120,
        min_rr: float = 1.5,
        maint_margin_rate: float = 0.02,
        max_risk_pct: float = 12.0,
        max_hold_minutes: int = 120,
        disable_sl: bool = False,
        move_sl_to_entry_pnl_pct: float = 15.0,
        move_sl_lock_pnl_pct: float = 10.0,
        move_sl_scale_by_leverage: bool = True,
        move_sl_reference_leverage: float = 5.0,
        liquid_enabled: bool = False,
        liquid_min_win_probability: float = 0.68,
        liquid_top_vol_days: int = 1,
        liquid_max_symbols: int = 30,
        liquid_entry_tolerance_pct: float = 0.003,
        btc_filter_enabled: bool = True,
        btc_filter_timeframe: str = "15m",
        btc_filter_cache_sec: float = 20.0,
        btc_filter_min_confidence: float = 0.55,
        btc_filter_block_countertrend: bool = True,
        btc_filter_countertrend_min_win: float = 0.9,
        btc_shock_pause_enabled: bool = True,
        btc_shock_threshold_pct: float = 1.2,
        btc_shock_cooldown_minutes: int = 30,
        btc_shock_up_long_block_minutes: int = 60,
        btc_shock_down_short_block_minutes: int = 60,
        btc_shock_up_require_pullback: bool = True,
        btc_shock_pullback_ema_period: int = 21,
        btc_shock_pullback_tolerance_pct: float = 0.0015,
        btc_reversal_profit_exit_enabled: bool = True,
        btc_reversal_threshold_pct: float = 0.8,
        btc_reversal_min_confidence: float = 0.55,
        btc_profit_lock_enabled: bool = True,
        btc_profit_lock_min_confidence: float = 0.6,
        btc_follow_min_corr: float = 0.45,
        btc_follow_min_beta: float = 0.2,
        btc_follow_lookback: int = 120,
        btc_follow_cache_sec: float = 300.0,
        test_ml_enabled: bool = False,
        test_ml_min_win_probability: float = 0.75,
        test_ml_max_symbols: int = 80,
        test_ml_max_orders_per_cycle: int = 2,
    ) -> None:
        self.repo = repo
        self.predictor = predictor
        self.predictor_test = predictor_test
        self.liquid_predictor = liquid_predictor
        self.price_stream = price_stream
        self.market_client = BinanceFuturesClient()
        self.min_win_probability = min_win_probability
        self.quantity = quantity
        self.order_usdt = max(0.0, order_usdt)
        self.margin_usdt = max(0.0, margin_usdt)
        self.leverage = leverage
        self.major_dynamic_enabled = bool(major_dynamic_enabled)
        self.major_dynamic_refresh_sec = max(30, int(major_dynamic_refresh_sec))
        self.major_dynamic_limit = max(1, min(30, int(major_dynamic_limit)))
        self.major_dynamic_candidates = max(self.major_dynamic_limit, min(120, int(major_dynamic_candidates)))
        self.major_dynamic_candle_lookback = max(12, min(120, int(major_dynamic_candle_lookback)))
        self.major_symbol_leverage = max(1, int(major_symbol_leverage))
        self.major_symbol_max_risk_pct = max(0.0, float(major_symbol_max_risk_pct))
        self.major_symbols_static = {
            self._normalize_symbol_key(symbol)
            for symbol in (major_symbols or [])
            if symbol
        }
        self.major_symbols_runtime: set[str] = set()
        self._major_symbols_runtime_updated_ts: float = 0.0
        self.poll_interval_sec = max(1.0, poll_interval_sec)
        self.stream_max_stale_sec = max(1.0, float(stream_max_stale_sec))
        self.min_sl_pct = min_sl_pct
        self.min_sl_loss_pct = max(0.0, min_sl_loss_pct)
        self.sl_extra_buffer_pct = max(0.0, sl_extra_buffer_pct)
        self.sl_atr_multiplier = max(0.0, sl_atr_multiplier)
        self.sl_atr_timeframe = sl_atr_timeframe or "5m"
        self.sl_atr_limit = max(30, min(500, int(sl_atr_limit)))
        self.min_rr = min_rr
        self.maint_margin_rate = max(0.0, maint_margin_rate)
        self.max_risk_pct = max(0.0, max_risk_pct)
        self.max_hold_minutes = max(1, max_hold_minutes)
        self.disable_sl = disable_sl
        self.move_sl_to_entry_pnl_pct = max(0.0, move_sl_to_entry_pnl_pct)
        self.move_sl_lock_pnl_pct = max(0.0, float(move_sl_lock_pnl_pct))
        self.move_sl_scale_by_leverage = bool(move_sl_scale_by_leverage)
        self.move_sl_reference_leverage = max(0.1, float(move_sl_reference_leverage))
        self.liquid_enabled = liquid_enabled
        self.liquid_min_win_probability = max(0.0, liquid_min_win_probability)
        self.liquid_top_vol_days = max(1, min(7, int(liquid_top_vol_days)))
        self.liquid_max_symbols = max(5, min(80, int(liquid_max_symbols)))
        self.liquid_entry_tolerance_pct = max(0.0008, float(liquid_entry_tolerance_pct))
        self.btc_filter_enabled = btc_filter_enabled
        self.btc_filter_timeframe = (btc_filter_timeframe or "15m").strip()
        self.btc_filter_cache_sec = max(5.0, float(btc_filter_cache_sec))
        self.btc_filter_min_confidence = max(0.5, min(float(btc_filter_min_confidence), 0.99))
        self.btc_filter_block_countertrend = btc_filter_block_countertrend
        self.btc_filter_countertrend_min_win = max(0.5, min(float(btc_filter_countertrend_min_win), 0.99))
        self.btc_shock_pause_enabled = btc_shock_pause_enabled
        self.btc_shock_threshold_pct = max(0.2, float(btc_shock_threshold_pct))
        self.btc_shock_cooldown_minutes = max(1, int(btc_shock_cooldown_minutes))
        self.btc_shock_up_long_block_minutes = max(0, int(btc_shock_up_long_block_minutes))
        self.btc_shock_down_short_block_minutes = max(0, int(btc_shock_down_short_block_minutes))
        self.btc_shock_up_require_pullback = bool(btc_shock_up_require_pullback)
        self.btc_shock_pullback_ema_period = int(btc_shock_pullback_ema_period)
        self.btc_shock_pullback_tolerance_pct = max(0.0, float(btc_shock_pullback_tolerance_pct))
        self.btc_reversal_profit_exit_enabled = bool(btc_reversal_profit_exit_enabled)
        self.btc_reversal_threshold_pct = max(0.0, float(btc_reversal_threshold_pct))
        self.btc_reversal_min_confidence = max(0.0, min(float(btc_reversal_min_confidence), 0.99))
        self.btc_profit_lock_enabled = bool(btc_profit_lock_enabled)
        self.btc_profit_lock_min_confidence = max(0.5, min(float(btc_profit_lock_min_confidence), 0.99))
        self.btc_follow_min_corr = max(0.0, min(float(btc_follow_min_corr), 0.99))
        self.btc_follow_min_beta = max(0.0, float(btc_follow_min_beta))
        self.btc_follow_lookback = max(60, min(500, int(btc_follow_lookback)))
        self.btc_follow_cache_sec = max(30.0, float(btc_follow_cache_sec))
        self.test_ml_enabled = bool(test_ml_enabled)
        self.test_ml_min_win_probability = max(0.0, min(float(test_ml_min_win_probability), 1.0))
        self.test_ml_max_symbols = max(10, min(200, int(test_ml_max_symbols)))
        self.test_ml_max_orders_per_cycle = max(1, min(20, int(test_ml_max_orders_per_cycle)))
        self._task: asyncio.Task | None = None
        self._running = False
        self._vn_tz = timezone(timedelta(hours=7))
        self._atr_cache: dict[str, tuple[float, float]] = {}
        self._top_vol_cache: tuple[float, list[str]] | None = None
        self._btc_trend_cache: tuple[float, dict[str, Any]] | None = None
        self._btc_follow_cache: dict[str, tuple[float, bool, float, float]] = {}
        self._open_pause_until_ts: float = 0.0
        self._open_pause_reason: str | None = None
        self._btc_up_shock_long_block_until_ts: float = 0.0
        self._btc_down_shock_short_block_until_ts: float = 0.0

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._run_once()
            except Exception as exc:
                # Keep the worker alive even if one iteration fails, but do not fail silently.
                print(f"[paper-engine] loop error: {type(exc).__name__}: {exc}")
                traceback.print_exc()
            await asyncio.sleep(self.poll_interval_sec)

    async def _run_once(self) -> None:
        signals: list[dict[str, Any]] = []
        try:
            snapshot = await asyncio.to_thread(get_scan_snapshot, min_win=0.7, max_symbols=100)
            signals = snapshot.get("signals", [])
            if self.major_dynamic_enabled:
                await asyncio.to_thread(self._refresh_major_symbols_runtime, signals)
        except Exception as exc:
            # Never block TP/SL management because scan failed.
            print(f"[paper-engine] scan failed, continue with open-trade management only: {type(exc).__name__}: {exc}")
        open_trades = self.repo.list_open_trades()
        open_trades_by_symbol = self._index_open_trades_by_symbol(open_trades)
        closed_trade_ids: set[int] = set()
        test_symbols: list[str] = []
        if self.test_ml_enabled and self.predictor_test is not None:
            test_symbols = await asyncio.to_thread(get_cached_symbols_snapshot, self.test_ml_max_symbols)
            if not test_symbols:
                # Fallback to symbols present in current scan response if cache is empty.
                test_symbols = [str(item.get("symbol")) for item in signals if item.get("symbol")]
            if len(test_symbols) > self.test_ml_max_symbols:
                test_symbols = test_symbols[: self.test_ml_max_symbols]
        top_vol_symbols: list[str] = []
        if self.liquid_enabled and self.liquid_predictor is not None:
            top_vol_symbols = await asyncio.to_thread(self._load_top_volatility_symbols)

        price_symbols = {str(item.get("symbol")) for item in signals if item.get("symbol")}
        for symbol in test_symbols:
            price_symbols.add(symbol)
        for symbol in top_vol_symbols:
            price_symbols.add(symbol)
        for trade in open_trades:
            symbol = str(trade.get("symbol") or "")
            if symbol:
                price_symbols.add(symbol)
        market_prices = await asyncio.to_thread(self._resolve_market_prices, list(price_symbols))
        stream_prices = await self._resolve_stream_prices(list(price_symbols))
        if stream_prices:
            # Prefer websocket stream cache for realtime TP/SL checks.
            market_prices.update(stream_prices)
        btc_guard = await asyncio.to_thread(self._resolve_btc_trend_guard)
        self._apply_btc_shock_pause(btc_guard)
        open_paused = self._is_open_paused()

        # 1) Open simulated orders when price reaches predicted entry for >=75% setups.
        if not open_paused:
            for item in signals:
                raw_prob = float(item.get("win_probability") or 0.0)
                if raw_prob < self.min_win_probability:
                    continue

                symbol = str(item.get("symbol"))
                side = str(item.get("side"))
                if self.repo.has_open_trade(symbol=symbol, side=side, entry_type="LIMIT"):
                    continue

                entry = float(item.get("predicted_entry_price") or 0.0)
                tp = float(item.get("take_profit") or 0.0)
                sl = float(item.get("stop_loss") or 0.0)
                if entry <= 0 or tp <= 0 or sl <= 0:
                    continue

                market_price = market_prices.get(symbol)
                if market_price is None:
                    market_price = await asyncio.to_thread(self._resolve_market_price, symbol)
                if market_price is None:
                    continue

                # Blend model signal with realized historical accuracy for this symbol.
                hist_acc = self.repo.symbol_accuracy(symbol=symbol, lookback=300)
                effective_prob = (raw_prob * 0.8 + hist_acc * 0.2) if hist_acc is not None else raw_prob
                if effective_prob < self.min_win_probability:
                    continue
                if not self._pass_btc_filter(symbol=symbol, side=side, effective_prob=effective_prob, btc_guard=btc_guard):
                    continue

                # Trigger condition: market touches/gets through entry.
                touched = self._entry_touched(side=side, market_price=market_price, entry=entry)
                if not touched:
                    continue
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=float(market_price),
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                normalized_tp, normalized_sl = normalize_tp_sl(
                    side=side,
                    entry_price=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    min_sl_pct=max(
                        self.min_sl_pct,
                        calc_min_sl_pct_from_loss(min_sl_loss_pct=self.min_sl_loss_pct),
                    ),
                    sl_extra_buffer_pct=self.sl_extra_buffer_pct,
                    atr_value=await self._resolve_symbol_atr(symbol),
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self.min_rr,
                    max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
                )
                leverage = self._resolve_symbol_leverage(symbol)
                risk_pct = calc_estimated_margin_ratio_pct(
                    leverage=leverage,
                    maint_margin_rate=self.maint_margin_rate,
                )
                if risk_pct > self._resolve_symbol_max_risk_pct(symbol):
                    continue

                quantity = calc_quantity_from_order_usdt(
                    entry_price=entry,
                    order_usdt=self.order_usdt,
                    fallback_quantity=self.quantity,
                )
                margin_usdt = self.margin_usdt
                if margin_usdt <= 0:
                    margin_usdt = calc_margin_usdt(entry_price=entry, quantity=quantity, leverage=leverage)
                feature_snapshot = await asyncio.to_thread(self._capture_feature_snapshot, symbol, side)
                btc_following = self._resolve_btc_following_flag(symbol)

                trade_id = self.repo.create_open_trade(
                    {
                        "symbol": symbol,
                        "side": side,
                        "btc_following": btc_following,
                        "entry_type": "LIMIT",
                        "signal_win_probability": raw_prob,
                        "effective_win_probability": effective_prob,
                        "entry_price": entry,
                        "take_profit": normalized_tp,
                        "stop_loss": normalized_sl,
                        "liq_zone_price": float(item["liq_zone_price"]) if item.get("liq_zone_price") is not None else None,
                        "liq_zone_score": None,
                        "quantity": quantity,
                        "margin_usdt": margin_usdt,
                        "leverage": leverage,
                        "mae_pct": 0.0,
                        "mfe_pct": 0.0,
                        "feature_snapshot": feature_snapshot,
                    }
                )
                self._cache_open_trade_row(
                    open_trades_by_symbol=open_trades_by_symbol,
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry_price=entry,
                    quantity=quantity,
                )

        # 1a) Optional test model: open separated ML_TEST orders for side-by-side comparison.
        if (not open_paused) and self.test_ml_enabled and self.predictor_test is not None:
            opened_test_orders = 0
            for symbol in test_symbols:
                if opened_test_orders >= self.test_ml_max_orders_per_cycle:
                    break
                market_price = market_prices.get(symbol)
                if market_price is None:
                    market_price = await asyncio.to_thread(self._resolve_market_price, symbol)
                if market_price is None:
                    continue

                try:
                    test_signal = await asyncio.to_thread(
                        self.predictor_test.predict,
                        symbol,
                        float(market_price),
                    )
                except Exception:
                    continue

                raw_prob = float(test_signal.win_probability)
                if raw_prob < self.test_ml_min_win_probability:
                    continue
                side = str(test_signal.side)
                if self.repo.has_open_trade(symbol=symbol, side=side, entry_type="ML_TEST"):
                    continue

                entry = float(test_signal.predicted_entry_price)
                tp = float(test_signal.take_profit)
                sl = float(test_signal.stop_loss)
                if entry <= 0 or tp <= 0 or sl <= 0:
                    continue

                touched = self._entry_touched(side=side, market_price=market_price, entry=entry)
                if not touched:
                    continue
                if not self._pass_btc_filter(symbol=symbol, side=side, effective_prob=raw_prob, btc_guard=btc_guard):
                    continue
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=float(market_price),
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                normalized_tp, normalized_sl = normalize_tp_sl(
                    side=side,
                    entry_price=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    min_sl_pct=max(
                        self.min_sl_pct,
                        calc_min_sl_pct_from_loss(min_sl_loss_pct=self.min_sl_loss_pct),
                    ),
                    sl_extra_buffer_pct=self.sl_extra_buffer_pct,
                    atr_value=await self._resolve_symbol_atr(symbol),
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self.min_rr,
                    max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
                )
                leverage = self._resolve_symbol_leverage(symbol)
                risk_pct = calc_estimated_margin_ratio_pct(
                    leverage=leverage,
                    maint_margin_rate=self.maint_margin_rate,
                )
                if risk_pct > self._resolve_symbol_max_risk_pct(symbol):
                    continue

                quantity = calc_quantity_from_order_usdt(
                    entry_price=entry,
                    order_usdt=self.order_usdt,
                    fallback_quantity=self.quantity,
                )
                margin_usdt = self.margin_usdt
                if margin_usdt <= 0:
                    margin_usdt = calc_margin_usdt(entry_price=entry, quantity=quantity, leverage=leverage)
                feature_snapshot = await asyncio.to_thread(self._capture_feature_snapshot, symbol, side)
                btc_following = self._resolve_btc_following_flag(symbol)

                trade_id = self.repo.create_open_trade(
                    {
                        "symbol": symbol,
                        "side": side,
                        "btc_following": btc_following,
                        "entry_type": "ML_TEST",
                        "signal_win_probability": raw_prob,
                        "effective_win_probability": raw_prob,
                        "entry_price": entry,
                        "take_profit": normalized_tp,
                        "stop_loss": normalized_sl,
                        "quantity": quantity,
                        "margin_usdt": margin_usdt,
                        "leverage": leverage,
                        "mae_pct": 0.0,
                        "mfe_pct": 0.0,
                        "feature_snapshot": feature_snapshot,
                    }
                )
                self._cache_open_trade_row(
                    open_trades_by_symbol=open_trades_by_symbol,
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry_price=entry,
                    quantity=quantity,
                )
                opened_test_orders += 1

        # 1b) Separate liquidation+EMA99 model on top volatility symbols.
        if (not open_paused) and self.liquid_enabled and self.liquid_predictor is not None:
            for symbol in top_vol_symbols:
                market_price = market_prices.get(symbol)
                if market_price is None:
                    market_price = await asyncio.to_thread(self._resolve_market_price, symbol)
                if market_price is None:
                    continue

                try:
                    liq_signal = await asyncio.to_thread(
                        self.liquid_predictor.predict,
                        symbol,
                        float(market_price),
                    )
                except Exception:
                    continue

                raw_prob = float(liq_signal.win_probability)
                if raw_prob < self.liquid_min_win_probability:
                    continue
                side = str(liq_signal.side)
                if self.repo.has_open_trade(symbol=symbol, side=side, entry_type="LIQ_EMA99"):
                    continue

                entry = float(liq_signal.predicted_entry_price)
                tp = float(liq_signal.take_profit)
                sl = float(liq_signal.stop_loss)
                if entry <= 0 or tp <= 0 or sl <= 0:
                    continue

                near_entry = abs(float(market_price) - entry) / entry <= self.liquid_entry_tolerance_pct
                if side == "SHORT":
                    short_zone_confirmed = bool(liq_signal.near_liq_zone) and (
                        float(liq_signal.liq_zone_score) >= float(self.liquid_predictor.short_zone_min_score)
                    )
                    if not (near_entry or short_zone_confirmed):
                        continue
                else:
                    if not (near_entry or bool(liq_signal.near_ema)):
                        continue

                hist_acc = self.repo.symbol_accuracy(symbol=symbol, lookback=300)
                effective_prob = (raw_prob * 0.8 + hist_acc * 0.2) if hist_acc is not None else raw_prob
                if effective_prob < self.min_win_probability:
                    continue
                if not self._pass_btc_filter(symbol=symbol, side=side, effective_prob=effective_prob, btc_guard=btc_guard):
                    continue
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=float(market_price),
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                normalized_tp, normalized_sl = normalize_tp_sl(
                    side=side,
                    entry_price=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    min_sl_pct=max(
                        self.min_sl_pct,
                        calc_min_sl_pct_from_loss(min_sl_loss_pct=self.min_sl_loss_pct),
                    ),
                    sl_extra_buffer_pct=self.sl_extra_buffer_pct,
                    atr_value=await self._resolve_symbol_atr(symbol),
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self.min_rr,
                    max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
                )
                leverage = self._resolve_symbol_leverage(symbol)
                risk_pct = calc_estimated_margin_ratio_pct(
                    leverage=leverage,
                    maint_margin_rate=self.maint_margin_rate,
                )
                if risk_pct > self._resolve_symbol_max_risk_pct(symbol):
                    continue

                quantity = calc_quantity_from_order_usdt(
                    entry_price=entry,
                    order_usdt=self.order_usdt,
                    fallback_quantity=self.quantity,
                )
                margin_usdt = self.margin_usdt
                if margin_usdt <= 0:
                    margin_usdt = calc_margin_usdt(entry_price=entry, quantity=quantity, leverage=leverage)
                feature_snapshot = await asyncio.to_thread(self._capture_feature_snapshot, symbol, side)
                btc_following = self._resolve_btc_following_flag(symbol)

                trade_id = self.repo.create_open_trade(
                    {
                        "symbol": symbol,
                        "side": side,
                        "btc_following": btc_following,
                        "entry_type": "LIQ_EMA99",
                        "signal_win_probability": raw_prob,
                        "effective_win_probability": effective_prob,
                        "entry_price": entry,
                        "take_profit": normalized_tp,
                        "stop_loss": normalized_sl,
                        "quantity": quantity,
                        "margin_usdt": margin_usdt,
                        "leverage": leverage,
                        "mae_pct": 0.0,
                        "mfe_pct": 0.0,
                        "feature_snapshot": feature_snapshot,
                    }
                )
                self._cache_open_trade_row(
                    open_trades_by_symbol=open_trades_by_symbol,
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry_price=entry,
                    quantity=quantity,
                )

        # 2) Manage open trades: close on TP, otherwise apply timeout policy.
        for trade in open_trades:
            try:
                trade_id = int(trade.get("id") or 0)
                if trade_id in closed_trade_ids:
                    continue
                symbol = str(trade["symbol"])
                side = str(trade["side"])
                price = market_prices.get(symbol)
                if price is None:
                    stream_price = await self._resolve_stream_price(symbol)
                    price = stream_price if stream_price is not None else await asyncio.to_thread(self._resolve_market_price, symbol)
                if price is None:
                    continue

                entry = float(trade["entry_price"])
                tp = float(trade["take_profit"])
                sl = float(trade["stop_loss"])
                qty = float(trade["quantity"])
                pnl = self._calc_pnl(side=side, entry=entry, close_price=price, quantity=qty)

                # Once ROI reaches trigger, move SL to a locked-profit level.
                pnl_pct = self._calc_pnl_pct(side=side, entry=entry, mark_price=price, leverage=int(trade["leverage"]))
                prev_mae = float(trade.get("mae_pct") or 0.0)
                prev_mfe = float(trade.get("mfe_pct") or 0.0)
                next_mae = min(prev_mae, pnl_pct)
                next_mfe = max(prev_mfe, pnl_pct)
                if (abs(next_mae - prev_mae) > 1e-9) or (abs(next_mfe - prev_mfe) > 1e-9):
                    self.repo.update_trade_excursions(
                        trade_id=int(trade["id"]),
                        mae_pct=next_mae,
                        mfe_pct=next_mfe,
                    )
                move_sl_trigger_pct = self._resolve_move_sl_trigger_pnl_pct(leverage=int(trade["leverage"]))
                if not self.disable_sl and pnl_pct >= move_sl_trigger_pct:
                    lock_pnl_pct = min(self.move_sl_lock_pnl_pct, move_sl_trigger_pct)
                    locked_sl = self._calc_locked_profit_sl(
                        side=side,
                        entry=entry,
                        mark_price=price,
                        leverage=int(trade["leverage"]),
                        lock_pnl_pct=lock_pnl_pct,
                    )
                    if locked_sl is not None:
                        if (side == "LONG" and locked_sl > sl) or (side == "SHORT" and locked_sl < sl):
                            self.repo.update_stop_loss(trade_id=int(trade["id"]), stop_loss=locked_sl)
                            sl = locked_sl

                # If BTC reverses down sharply, close profitable LONGs on BTC-following symbols.
                if self._should_force_close_profit_on_btc_reversal(
                    symbol=symbol,
                    side=side,
                    pnl=pnl,
                    btc_guard=btc_guard,
                ):
                    self.repo.close_trade(
                        trade_id=int(trade["id"]),
                        close_price=price,
                        pnl=pnl,
                        result=1,
                        close_reason="BTC_REVERSAL_PROFIT_EXIT",
                    )
                    continue

                # Lock profit when BTC trend flips against this position for BTC-following symbols only.
                if self._should_close_profit_on_btc_trend(
                    symbol=symbol,
                    side=side,
                    pnl=pnl,
                    btc_guard=btc_guard,
                ):
                    self.repo.close_trade(
                        trade_id=int(trade["id"]),
                        close_price=price,
                        pnl=pnl,
                        result=1,
                        close_reason="BTC_TREND_PROFIT_LOCK",
                    )
                    continue

                # Close any counter-trend open trade when BTC filter has high confidence.
                if self._should_force_close_countertrend_on_btc_filter(
                    symbol=symbol,
                    side=side,
                    pnl=pnl,
                    btc_guard=btc_guard,
                ):
                    self.repo.close_trade(
                        trade_id=int(trade["id"]),
                        close_price=price,
                        pnl=pnl,
                        result=1,
                        close_reason="BTC_TREND_COUNTER_EXIT",
                    )
                    continue

                # SL has higher priority than TP per user requirement.
                sl_hit = False
                if not self.disable_sl:
                    sl_hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
                if sl_hit:
                    close_reason = 1 if pnl >= 0 else 0
                    self.repo.close_trade(
                        trade_id=int(trade["id"]),
                        close_price=price,
                        pnl=pnl,
                        result=close_reason,
                        close_reason="SL",
                    )
                    continue

                # Immediate TP exit.
                tp_hit = (side == "LONG" and price >= tp) or (side == "SHORT" and price <= tp)
                if tp_hit:
                    close_reason = 1 if pnl >= 0 else 0
                    self.repo.close_trade(
                        trade_id=int(trade["id"]),
                        close_price=price,
                        pnl=pnl,
                        result=close_reason,
                        close_reason="TP",
                    )
                    continue

                # Timeout policy.
                if not self._is_expired(trade.get("opened_at")):
                    continue

                if pnl > 0:
                    close_reason = 1
                    self.repo.close_trade(
                        trade_id=int(trade["id"]),
                        close_price=price,
                        pnl=pnl,
                        result=close_reason,
                        close_reason="TIMEOUT_PROFIT",
                    )
                    continue

                # If expired but still in loss: move TP to entry and wait for breakeven exit.
                if abs(tp - entry) > max(1e-9, abs(entry) * 1e-8):
                    self.repo.update_take_profit(trade_id=int(trade["id"]), take_profit=entry)

                recovered_to_entry = (side == "LONG" and price >= entry) or (side == "SHORT" and price <= entry)
                if not recovered_to_entry:
                    continue

                close_reason = 1 if pnl >= 0 else 0

                self.repo.close_trade(
                    trade_id=int(trade["id"]),
                    close_price=price,
                    pnl=pnl,
                    result=close_reason,
                    close_reason="TIMEOUT_BREAKEVEN",
                )
            except Exception as exc:
                trade_id = trade.get("id")
                symbol = trade.get("symbol")
                print(
                    f"[paper-engine] manage trade failed id={trade_id} symbol={symbol}: "
                    f"{type(exc).__name__}: {exc}"
                )

    def _index_open_trades_by_symbol(self, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            key = self._normalize_symbol_key(symbol)
            out.setdefault(key, []).append(row)
        return out

    def _cache_open_trade_row(
        self,
        *,
        open_trades_by_symbol: dict[str, list[dict[str, Any]]],
        trade_id: int,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
    ) -> None:
        key = self._normalize_symbol_key(symbol)
        open_trades_by_symbol.setdefault(key, []).append(
            {
                "id": int(trade_id),
                "symbol": symbol,
                "side": side,
                "status": "OPEN",
                "entry_price": float(entry_price),
                "quantity": float(quantity),
            }
        )

    def _handle_opposite_signal_on_touch(
        self,
        *,
        symbol: str,
        target_side: str,
        market_price: float,
        open_trades_by_symbol: dict[str, list[dict[str, Any]]],
        closed_trade_ids: set[int],
    ) -> bool:
        key = self._normalize_symbol_key(symbol)
        bucket = open_trades_by_symbol.get(key, [])
        if not bucket:
            return True

        target_side_key = str(target_side or "").upper()
        opposite_open_rows: list[dict[str, Any]] = []
        for row in bucket:
            trade_id = int(row.get("id") or 0)
            if trade_id in closed_trade_ids:
                continue
            status = str(row.get("status") or "OPEN").upper()
            side = str(row.get("side") or "").upper()
            if status != "OPEN":
                continue
            if side and side != target_side_key:
                opposite_open_rows.append(row)

        if not opposite_open_rows:
            return True

        close_items: list[tuple[int, float]] = []
        for row in opposite_open_rows:
            try:
                trade_id = int(row.get("id") or 0)
                side = str(row.get("side") or "").upper()
                entry = float(row.get("entry_price") or 0.0)
                qty = float(row.get("quantity") or 0.0)
            except Exception:
                return False
            if trade_id <= 0 or side not in {"LONG", "SHORT"} or entry <= 0 or qty <= 0:
                return False

            pnl = self._calc_pnl(side=side, entry=entry, close_price=float(market_price), quantity=qty)
            if pnl <= 0:
                return False
            close_items.append((trade_id, float(pnl)))

        for trade_id, pnl in close_items:
            try:
                self.repo.close_trade(
                    trade_id=trade_id,
                    close_price=float(market_price),
                    pnl=float(pnl),
                    result=1,
                    close_reason="OPPOSITE_SIGNAL_FLIP",
                )
                closed_trade_ids.add(trade_id)
            except Exception:
                return False

        kept_rows: list[dict[str, Any]] = []
        for row in bucket:
            trade_id = int(row.get("id") or 0)
            if trade_id in closed_trade_ids:
                continue
            kept_rows.append(row)
        open_trades_by_symbol[key] = kept_rows
        return True

    def _resolve_market_price(self, symbol: str) -> float | None:
        try:
            ticker = self.market_client.fetch_ticker(symbol=symbol)
            price = self._extract_price_from_ticker(ticker)
            if price is not None:
                return float(price)
        except Exception:
            pass

        try:
            rows = self.market_client.fetch_ohlcv(symbol=symbol, timeframe="1m", limit=2)
            if rows:
                return float(rows[-1][4])
        except Exception:
            return None

        return None

    def _resolve_market_prices(self, symbols: list[str]) -> dict[str, float]:
        unique = [s for s in sorted(set(symbols)) if s]
        if not unique:
            return {}
        try:
            payload = self.market_client.fetch_tickers(unique)
        except Exception:
            return {}

        out: dict[str, float] = {}
        if not isinstance(payload, dict):
            return out
        for symbol in unique:
            ticker = payload.get(symbol)
            if not isinstance(ticker, dict):
                continue
            price = self._extract_price_from_ticker(ticker)
            if price is not None:
                out[symbol] = float(price)
        return out

    def _load_top_volatility_symbols(self) -> list[str]:
        now = time.time()
        if self._top_vol_cache is not None and (now - self._top_vol_cache[0]) <= 300:
            return list(self._top_vol_cache[1])
        try:
            markets = self.market_client.load_markets()
            all_symbols: list[str] = []
            for market in markets.values():
                if not market.get("active", True):
                    continue
                if market.get("swap") is not True:
                    continue
                if market.get("settle") != "USDT":
                    continue
                sym = market.get("symbol")
                if sym:
                    all_symbols.append(str(sym))
            all_symbols = sorted(set(all_symbols))
            tickers = self.market_client.fetch_tickers(all_symbols[:220])
            ranked: list[tuple[str, float]] = []
            for symbol in all_symbols:
                ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
                if not isinstance(ticker, dict):
                    continue
                pct = ticker.get("percentage")
                if pct is None:
                    pct = ticker.get("change")
                try:
                    ranked.append((symbol, abs(float(pct))))
                except Exception:
                    continue
            ranked.sort(key=lambda x: x[1], reverse=True)
            symbols = [sym for sym, _ in ranked[: self.liquid_max_symbols]]
            self._top_vol_cache = (now, symbols)
            return symbols
        except Exception:
            if self._top_vol_cache is not None:
                return list(self._top_vol_cache[1])
            return []

    async def _resolve_stream_prices(self, symbols: list[str]) -> dict[str, float]:
        if self.price_stream is None or not symbols:
            return {}
        try:
            prices, _, timestamps = await self.price_stream.get_prices(symbols)
            out: dict[str, float] = {}
            for key, value in prices.items():
                if value is None:
                    continue
                ts = timestamps.get(str(key))
                if not self._is_stream_timestamp_fresh(ts):
                    continue
                out[str(key)] = float(value)
            return out
        except Exception:
            return {}

    async def _resolve_stream_price(self, symbol: str) -> float | None:
        if self.price_stream is None:
            return None
        try:
            price, ts = await self.price_stream.get_price(symbol=symbol)
            if not self._is_stream_timestamp_fresh(ts):
                return None
            return float(price) if price is not None else None
        except Exception:
            return None

    def _is_stream_timestamp_fresh(self, timestamp: str | None) -> bool:
        if not timestamp:
            return False
        try:
            text = str(timestamp).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()
            return age <= self.stream_max_stale_sec
        except Exception:
            return False

    async def _resolve_symbol_atr(self, symbol: str) -> float | None:
        if self.sl_atr_multiplier <= 0:
            return None
        now = time.time()
        cached = self._atr_cache.get(symbol)
        if cached is not None and (now - cached[0]) <= 60:
            return cached[1]
        try:
            rows = await asyncio.to_thread(
                self.market_client.fetch_ohlcv,
                symbol,
                self.sl_atr_timeframe,
                self.sl_atr_limit,
            )
            atr = calc_atr_from_ohlcv(rows=rows, period=14)
            if atr is None:
                return None
            value = float(atr)
            self._atr_cache[symbol] = (now, value)
            return value
        except Exception:
            return None

    def _resolve_btc_trend_guard(self) -> dict[str, Any]:
        neutral = {
            "side": "NEUTRAL",
            "confidence": 0.0,
            "score": 0.0,
            "timeframe": self.btc_filter_timeframe,
            "mark_price": 0.0,
            "ema_fast": 0.0,
            "ema_slow": 0.0,
            "rsi_15m": 50.0,
            "rsi_1h": 50.0,
            "overheat_long_block": False,
            "close_to_close_pct": 0.0,
            "shock": False,
            "shock_direction": "FLAT",
            "shock_move_pct": 0.0,
            "shock_range_pct": 0.0,
            "shock_metric_pct": 0.0,
        }
        if not self.btc_filter_enabled and not self.btc_shock_pause_enabled:
            return neutral

        now = time.time()
        cached = self._btc_trend_cache
        if cached is not None and (now - cached[0]) <= self.btc_filter_cache_sec:
            return cached[1]

        try:
            rows = self.market_client.fetch_ohlcv(
                symbol="BTC/USDT",
                timeframe=self.btc_filter_timeframe,
                limit=180,
            )
            closes = [float(row[4]) for row in rows if len(row) >= 5]
            if len(closes) < 60:
                payload = neutral
            else:
                ema_fast = self._ema_last(closes[-120:], period=21)
                ema_slow = self._ema_last(closes[-160:], period=55)
                rsi_15m = self._rsi_last(closes, period=14)
                rsi_1h = rsi_15m
                try:
                    rows_1h = self.market_client.fetch_ohlcv(
                        symbol="BTC/USDT",
                        timeframe="1h",
                        limit=220,
                    )
                    closes_1h = [float(row[4]) for row in rows_1h if len(row) >= 5]
                    if len(closes_1h) >= 20:
                        rsi_1h = self._rsi_last(closes_1h, period=14)
                except Exception:
                    pass
                base = max(1e-12, abs(closes[-6]))
                slope_pct = ((closes[-1] - closes[-6]) / base) * 100.0

                cross_component = self._clamp(((ema_fast - ema_slow) / max(1e-12, abs(ema_slow))) * 26.0, -1.0, 1.0)
                slope_component = self._clamp(slope_pct / 0.7, -1.0, 1.0)
                score = (cross_component * 0.65) + (slope_component * 0.35)
                confidence = self._clamp(0.50 + (abs(score) * 0.50), 0.50, 0.99)
                if score > 0.08:
                    trend_side = "LONG"
                elif score < -0.08:
                    trend_side = "SHORT"
                else:
                    trend_side = "NEUTRAL"

                last_row = rows[-1] if rows and len(rows[-1]) >= 5 else None
                prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
                close_to_close_pct = 0.0
                shock_move_pct = 0.0
                shock_range_pct = 0.0
                if last_row is not None:
                    open_px = float(last_row[1])
                    high_px = float(last_row[2])
                    low_px = float(last_row[3])
                    close_px = float(last_row[4])
                    if abs(prev_close) > 1e-12:
                        close_to_close_pct = ((close_px - prev_close) / prev_close) * 100.0
                        shock_move_pct = abs(close_to_close_pct)
                    if abs(open_px) > 1e-12:
                        shock_range_pct = abs((high_px - low_px) / open_px) * 100.0
                shock_metric_pct = max(shock_move_pct, shock_range_pct)
                shock = self.btc_shock_pause_enabled and (shock_metric_pct >= self.btc_shock_threshold_pct)
                if close_to_close_pct > 0:
                    shock_direction = "UP"
                elif close_to_close_pct < 0:
                    shock_direction = "DOWN"
                else:
                    shock_direction = "FLAT"

                payload = {
                    "side": trend_side,
                    "confidence": float(confidence),
                    "score": float(score),
                    "timeframe": self.btc_filter_timeframe,
                    "mark_price": float(closes[-1]),
                    "ema_fast": float(ema_fast),
                    "ema_slow": float(ema_slow),
                    "rsi_15m": float(rsi_15m),
                    "rsi_1h": float(rsi_1h),
                    "overheat_long_block": False,
                    "close_to_close_pct": float(close_to_close_pct),
                    "shock": bool(shock),
                    "shock_direction": shock_direction,
                    "shock_move_pct": float(shock_move_pct),
                    "shock_range_pct": float(shock_range_pct),
                    "shock_metric_pct": float(shock_metric_pct),
                }
        except Exception:
            payload = cached[1] if cached is not None else neutral

        self._btc_trend_cache = (now, payload)
        return payload

    def _pass_btc_filter(self, symbol: str, side: str, effective_prob: float, btc_guard: dict[str, Any]) -> bool:
        if not self._pass_btc_shock_directional_guard(symbol=symbol, side=side, btc_guard=btc_guard):
            return False
        if not self.btc_filter_enabled:
            return True
        # BTC trend filter only applies to symbols that statistically move with BTC.
        if not self._is_symbol_following_btc(symbol):
            return True
        trend_side = str(btc_guard.get("side") or "NEUTRAL").upper()
        try:
            confidence = float(btc_guard.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if trend_side not in {"LONG", "SHORT"}:
            return True
        if confidence < self.btc_filter_min_confidence:
            return True
        if side.upper() == trend_side:
            return True
        if self.btc_filter_block_countertrend:
            return False
        return effective_prob >= self.btc_filter_countertrend_min_win

    def _should_close_profit_on_btc_trend(
        self,
        *,
        symbol: str,
        side: str,
        pnl: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if not self.btc_profit_lock_enabled:
            return False
        if pnl <= 0:
            return False

        trend_side = str(btc_guard.get("side") or "NEUTRAL").upper()
        if trend_side not in {"LONG", "SHORT"}:
            return False
        if side.upper() == trend_side:
            return False
        try:
            confidence = float(btc_guard.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if confidence < self.btc_profit_lock_min_confidence:
            return False
        return self._is_symbol_following_btc(symbol)

    def _should_force_close_countertrend_on_btc_filter(
        self,
        *,
        symbol: str,
        side: str,
        pnl: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if pnl <= 0:
            return False
        if not self.btc_filter_enabled:
            return False
        if not self.btc_filter_block_countertrend:
            return False
        if not self._is_symbol_following_btc(symbol):
            return False

        trend_side = str(btc_guard.get("side") or "NEUTRAL").upper()
        if trend_side not in {"LONG", "SHORT"}:
            return False
        if side.upper() == trend_side:
            return False
        try:
            confidence = float(btc_guard.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        return confidence >= self.btc_filter_min_confidence

    def _should_force_close_profit_on_btc_reversal(
        self,
        *,
        symbol: str,
        side: str,
        pnl: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if not self.btc_reversal_profit_exit_enabled:
            return False
        if pnl <= 0:
            return False
        if side.upper() != "LONG":
            return False
        if not self._is_symbol_following_btc(symbol):
            return False

        shock_direction = str(btc_guard.get("shock_direction") or "FLAT").upper()
        if shock_direction != "DOWN":
            return False
        try:
            shock_metric_pct = float(btc_guard.get("shock_metric_pct") or 0.0)
        except Exception:
            shock_metric_pct = 0.0
        if shock_metric_pct < self.btc_reversal_threshold_pct:
            return False

        trend_side = str(btc_guard.get("side") or "NEUTRAL").upper()
        try:
            confidence = float(btc_guard.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if trend_side == "SHORT" and confidence >= self.btc_reversal_min_confidence:
            return True
        return shock_metric_pct >= (self.btc_reversal_threshold_pct * 1.2)

    def _is_symbol_following_btc(self, symbol: str) -> bool:
        normalized = str(symbol).strip()
        if not normalized:
            return False
        if normalized.upper().startswith("BTC/USDT"):
            return True

        now = time.time()
        cached = self._btc_follow_cache.get(normalized)
        if cached is not None and (now - cached[0]) <= self.btc_follow_cache_sec:
            return bool(cached[1])

        follows = False
        corr = 0.0
        beta = 0.0
        try:
            sym_rows = self.market_client.fetch_ohlcv(
                symbol=normalized,
                timeframe=self.btc_filter_timeframe,
                limit=self.btc_follow_lookback,
            )
            btc_rows = self.market_client.fetch_ohlcv(
                symbol="BTC/USDT",
                timeframe=self.btc_filter_timeframe,
                limit=self.btc_follow_lookback,
            )
            sym_closes = [float(row[4]) for row in sym_rows if len(row) >= 5]
            btc_closes = [float(row[4]) for row in btc_rows if len(row) >= 5]
            sym_ret = self._pct_returns(sym_closes)
            btc_ret = self._pct_returns(btc_closes)
            n = min(len(sym_ret), len(btc_ret))
            if n >= 30:
                corr, beta = self._corr_beta(sym_ret[-n:], btc_ret[-n:])
                follows = (corr >= self.btc_follow_min_corr) and (beta >= self.btc_follow_min_beta)
        except Exception:
            follows = False

        self._btc_follow_cache[normalized] = (now, follows, corr, beta)
        return follows

    def _apply_btc_shock_pause(self, btc_guard: dict[str, Any]) -> None:
        if not self.btc_shock_pause_enabled:
            return
        if not bool(btc_guard.get("shock")):
            return
        now = time.time()
        shock_direction = str(btc_guard.get("shock_direction") or "FLAT").upper()
        if shock_direction == "UP" and self.btc_shock_up_long_block_minutes > 0:
            block_minutes = max(self.btc_shock_cooldown_minutes, self.btc_shock_up_long_block_minutes)
            long_block_until = now + (block_minutes * 60)
            self._btc_up_shock_long_block_until_ts = max(self._btc_up_shock_long_block_until_ts, long_block_until)
        if shock_direction == "DOWN" and self.btc_shock_down_short_block_minutes > 0:
            block_minutes = max(self.btc_shock_cooldown_minutes, self.btc_shock_down_short_block_minutes)
            short_block_until = now + (block_minutes * 60)
            self._btc_down_shock_short_block_until_ts = max(self._btc_down_shock_short_block_until_ts, short_block_until)

    def _pass_btc_shock_directional_guard(self, symbol: str, side: str, btc_guard: dict[str, Any]) -> bool:
        if not self.btc_shock_pause_enabled:
            return True
        if not self._is_symbol_following_btc(symbol):
            return True

        side_key = str(side or "").upper()
        now = time.time()
        if side_key == "LONG":
            if self._btc_up_shock_long_block_until_ts <= 0:
                return True
            if now >= self._btc_up_shock_long_block_until_ts:
                self._btc_up_shock_long_block_until_ts = 0.0
                return True
            if self.btc_shock_up_require_pullback and self._btc_pullback_to_ema_met(side="LONG", btc_guard=btc_guard):
                self._btc_up_shock_long_block_until_ts = 0.0
                return True
            return False

        if side_key == "SHORT":
            if self._btc_down_shock_short_block_until_ts <= 0:
                return True
            if now >= self._btc_down_shock_short_block_until_ts:
                self._btc_down_shock_short_block_until_ts = 0.0
                return True
            if self.btc_shock_up_require_pullback and self._btc_pullback_to_ema_met(side="SHORT", btc_guard=btc_guard):
                self._btc_down_shock_short_block_until_ts = 0.0
                return True
            return False

        return True

    def _pass_btc_up_shock_long_guard(self, side: str, btc_guard: dict[str, Any]) -> bool:
        # Backward-compatible helper used by external precheck code.
        if str(side or "").upper() != "LONG":
            return True
        if self._btc_up_shock_long_block_until_ts <= 0:
            return True
        now = time.time()
        if now >= self._btc_up_shock_long_block_until_ts:
            self._btc_up_shock_long_block_until_ts = 0.0
            return True
        if self.btc_shock_up_require_pullback and self._btc_pullback_to_ema_met(side="LONG", btc_guard=btc_guard):
            self._btc_up_shock_long_block_until_ts = 0.0
            return True
        return False

    def _btc_pullback_to_ema_met(self, side: str, btc_guard: dict[str, Any]) -> bool:
        try:
            mark_price = float(btc_guard.get("mark_price") or 0.0)
            ema_fast = float(btc_guard.get("ema_fast") or 0.0)
            ema_slow = float(btc_guard.get("ema_slow") or 0.0)
        except Exception:
            return False
        if mark_price <= 0:
            return False
        ema_ref = ema_fast if self.btc_shock_pullback_ema_period <= 21 else ema_slow
        if ema_ref <= 0:
            return False
        tolerance = self.btc_shock_pullback_tolerance_pct
        side_key = str(side or "").upper()
        if side_key == "LONG":
            # After strong UP shock, wait BTC to cool down near EMA before allowing new LONG.
            return mark_price <= (ema_ref * (1.0 + tolerance))
        if side_key == "SHORT":
            # After strong DOWN shock, wait BTC to pull back up near EMA before allowing new SHORT.
            return mark_price >= (ema_ref * (1.0 - tolerance))
        return False

    def _is_open_paused(self) -> bool:
        # BTC shock logic is directional and symbol-specific; no global open pause.
        return False

    @staticmethod
    def _ema_last(values: list[float], period: int) -> float:
        if not values:
            return 0.0
        alpha = 2.0 / (period + 1.0)
        ema = float(values[0])
        for value in values[1:]:
            ema = (float(value) * alpha) + (ema * (1.0 - alpha))
        return float(ema)

    @staticmethod
    def _rsi_last(values: list[float], period: int = 14) -> float:
        if len(values) < period + 1:
            return 50.0
        gains: list[float] = []
        losses: list[float] = []
        for idx in range(1, len(values)):
            delta = float(values[idx]) - float(values[idx - 1])
            gains.append(max(delta, 0.0))
            losses.append(max(-delta, 0.0))
        window_gains = gains[-period:]
        window_losses = losses[-period:]
        avg_gain = sum(window_gains) / max(1, len(window_gains))
        avg_loss = sum(window_losses) / max(1, len(window_losses))
        if avg_loss <= 1e-12:
            return 100.0 if avg_gain > 1e-12 else 50.0
        rs = avg_gain / avg_loss
        return float(100.0 - (100.0 / (1.0 + rs)))

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _extract_price_from_ticker(ticker: dict[str, Any]) -> float | None:
        price = ticker.get("last") or ticker.get("close")
        if price is not None:
            return float(price)
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is not None and ask is not None:
            return float((bid + ask) / 2)
        return None

    @staticmethod
    def _entry_touched(side: str, market_price: float, entry: float) -> bool:
        # Allow directional touch with a small slippage buffer so fast moves do not miss fills.
        # LONG: fill when mark <= entry (or up to +0.15% above entry)
        # SHORT: fill when mark >= entry (or up to -0.15% below entry)
        side_key = str(side or "").upper()
        if entry <= 0:
            return False
        buffer_pct = 0.0015
        if side_key == "LONG":
            return market_price <= (entry * (1.0 + buffer_pct))
        if side_key == "SHORT":
            return market_price >= (entry * (1.0 - buffer_pct))
        return False

    @staticmethod
    def _pct_returns(closes: list[float]) -> list[float]:
        out: list[float] = []
        for i in range(1, len(closes)):
            prev = float(closes[i - 1])
            cur = float(closes[i])
            if abs(prev) <= 1e-12:
                continue
            out.append((cur - prev) / prev)
        return out

    @staticmethod
    def _corr_beta(series_y: list[float], series_x: list[float]) -> tuple[float, float]:
        n = min(len(series_y), len(series_x))
        if n < 2:
            return 0.0, 0.0
        ys = [float(v) for v in series_y[-n:]]
        xs = [float(v) for v in series_x[-n:]]

        mean_y = sum(ys) / n
        mean_x = sum(xs) / n
        var_x = sum((x - mean_x) ** 2 for x in xs) / n
        var_y = sum((y - mean_y) ** 2 for y in ys) / n
        if var_x <= 1e-16 or var_y <= 1e-16:
            return 0.0, 0.0

        cov = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n)) / n
        corr = cov / math.sqrt(var_x * var_y)
        beta = cov / var_x
        return float(corr), float(beta)

    def _capture_feature_snapshot(self, symbol: str, side: str) -> dict[str, float] | None:
        try:
            row = self.predictor.pipeline.build_latest_feature_row(symbol=symbol, limit=400)
            if row is None:
                return None
            payload = {k: float(v) for k, v in row.to_dict().items()}
            if side == "LONG":
                payload["setup_side"] = 1.0
            elif side == "SHORT":
                payload["setup_side"] = 0.0
            # Ensure serializable finite values only.
            json.dumps(payload, allow_nan=False)
            return payload
        except Exception:
            return None

    @staticmethod
    def _calc_pnl(side: str, entry: float, close_price: float, quantity: float) -> float:
        if side == "LONG":
            return (close_price - entry) * quantity
        return (entry - close_price) * quantity

    @staticmethod
    def _calc_pnl_pct(side: str, entry: float, mark_price: float, leverage: int) -> float:
        if entry <= 0 or leverage <= 0:
            return 0.0
        move = (mark_price - entry) / entry if side == "LONG" else (entry - mark_price) / entry
        return move * leverage * 100

    def _calc_locked_profit_sl(
        self,
        side: str,
        entry: float,
        mark_price: float,
        leverage: int,
        lock_pnl_pct: float | None = None,
    ) -> float | None:
        if entry <= 0 or leverage <= 0:
            return None
        effective_lock_pnl_pct = self.move_sl_lock_pnl_pct if lock_pnl_pct is None else max(0.0, float(lock_pnl_pct))
        if effective_lock_pnl_pct <= 0:
            return entry

        lock_move_pct = (effective_lock_pnl_pct / 100.0) / float(leverage)
        if side == "LONG":
            target = entry * (1.0 + lock_move_pct)
            # Keep SL slightly below mark to avoid accidental instant close on update tick.
            return min(target, mark_price * 0.99999)

        target = entry * (1.0 - lock_move_pct)
        # Keep SL slightly above mark to avoid accidental instant close on update tick.
        return max(target, mark_price * 1.00001)

    def _resolve_move_sl_trigger_pnl_pct(self, leverage: int) -> float:
        trigger = self.move_sl_to_entry_pnl_pct
        if self.move_sl_scale_by_leverage and leverage > 0:
            trigger = trigger * (float(leverage) / self.move_sl_reference_leverage)
        return max(0.0, trigger)

    @staticmethod
    def _normalize_symbol_key(symbol: str) -> str:
        base = str(symbol or "").upper().strip()
        return base.replace(":USDT", "")

    def is_major_symbol(self, symbol: str) -> bool:
        return self._is_major_symbol(symbol)

    def is_symbol_following_btc(self, symbol: str) -> bool:
        return self._is_symbol_following_btc(symbol)

    def _resolve_btc_following_flag(self, symbol: str) -> bool | None:
        try:
            return bool(self._is_symbol_following_btc(symbol))
        except Exception:
            return None

    def _is_major_symbol(self, symbol: str) -> bool:
        key = self._normalize_symbol_key(symbol)
        if key in self.major_symbols_runtime:
            return True
        return key in self.major_symbols_static

    def _resolve_symbol_leverage(self, symbol: str) -> int:
        if self._is_major_symbol(symbol):
            return self.major_symbol_leverage
        return self.leverage

    def _resolve_symbol_max_risk_pct(self, symbol: str) -> float:
        if self._is_major_symbol(symbol):
            return max(self.max_risk_pct, self.major_symbol_max_risk_pct)
        return self.max_risk_pct

    def _refresh_major_symbols_runtime(self, signals: list[dict[str, Any]]) -> None:
        now = time.time()
        if (now - self._major_symbols_runtime_updated_ts) < self.major_dynamic_refresh_sec:
            return
        if not signals:
            return

        candidates: list[tuple[str, float]] = []
        for item in signals:
            symbol = str(item.get("symbol") or "")
            if not symbol:
                continue
            try:
                win_prob = float(item.get("win_probability") or 0.0)
            except Exception:
                win_prob = 0.0
            candidates.append((symbol, win_prob))

        if not candidates:
            return

        candidates.sort(key=lambda x: x[1], reverse=True)
        selected = candidates[: self.major_dynamic_candidates]
        scores: list[tuple[str, float]] = []

        turnovers: dict[str, float] = {}
        momentum_abs: dict[str, float] = {}
        hist_acc_map: dict[str, float] = {}
        win_prob_map: dict[str, float] = {}

        for symbol, win_prob in selected:
            key = self._normalize_symbol_key(symbol)
            win_prob_map[key] = max(0.0, min(1.0, float(win_prob)))
            hist_acc = self.repo.symbol_accuracy(symbol=symbol, lookback=300)
            hist_acc_map[key] = float(hist_acc) if hist_acc is not None else 0.5
            turnover = 0.0
            momentum = 0.0
            try:
                rows = self.market_client.fetch_ohlcv(
                    symbol=symbol,
                    timeframe="5m",
                    limit=self.major_dynamic_candle_lookback,
                )
                closes = [float(r[4]) for r in rows if len(r) >= 6]
                vols = [float(r[5]) for r in rows if len(r) >= 6]
                n = min(len(closes), len(vols))
                if n > 0:
                    quote_turns = [max(0.0, closes[i] * vols[i]) for i in range(n)]
                    turnover = float(sum(quote_turns) / n)
                    base = closes[0]
                    if abs(base) > 1e-12:
                        momentum = abs((closes[-1] - base) / base)
            except Exception:
                turnover = 0.0
                momentum = 0.0
            turnovers[key] = turnover
            momentum_abs[key] = momentum

        turnover_values = [math.log10(max(v, 1.0)) for v in turnovers.values()]
        turnover_min = min(turnover_values) if turnover_values else 0.0
        turnover_max = max(turnover_values) if turnover_values else 1.0
        momentum_values = list(momentum_abs.values())
        momentum_min = min(momentum_values) if momentum_values else 0.0
        momentum_max = max(momentum_values) if momentum_values else 1.0

        for key in win_prob_map.keys():
            turnover_raw = math.log10(max(turnovers.get(key, 0.0), 1.0))
            turnover_norm = (
                0.5 if turnover_max <= turnover_min else (turnover_raw - turnover_min) / (turnover_max - turnover_min)
            )
            momentum_raw = momentum_abs.get(key, 0.0)
            momentum_norm = (
                0.0 if momentum_max <= momentum_min else (momentum_raw - momentum_min) / (momentum_max - momentum_min)
            )
            score = (
                (0.45 * turnover_norm)
                + (0.30 * win_prob_map.get(key, 0.0))
                + (0.20 * hist_acc_map.get(key, 0.5))
                + (0.05 * momentum_norm)
            )
            scores.append((key, score))

        scores.sort(key=lambda x: x[1], reverse=True)
        runtime = {symbol_key for symbol_key, _ in scores[: self.major_dynamic_limit]}
        if runtime:
            self.major_symbols_runtime = runtime
            self._major_symbols_runtime_updated_ts = now

    def _is_expired(self, opened_at: Any) -> bool:
        if opened_at is None:
            return False
        if isinstance(opened_at, datetime):
            dt = opened_at
        else:
            try:
                dt = datetime.fromisoformat(str(opened_at))
            except Exception:
                return False

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._vn_tz)

        held_seconds = (datetime.now(self._vn_tz) - dt).total_seconds()
        return held_seconds >= (self.max_hold_minutes * 60)
