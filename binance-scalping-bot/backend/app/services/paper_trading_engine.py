from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import time
import traceback
from typing import Any
from urllib.request import Request, urlopen

from app.api.signals import get_cached_symbols_snapshot, get_scan_snapshot
from app.core.config import settings
from app.services.binance_client import BinanceFuturesClient
from app.services.candle_pattern_analyzer import CandlePatternAnalyzer
from app.services.liquidation_ml_predictor import LiquidationMLPredictor
from app.services.ml_predictor import MLPredictor
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.pattern_performance import PatternPerformanceResolver
from app.services.pump_scanner_service import PumpScannerService
from app.services.risk_manager import (
    calc_atr_from_ohlcv,
    calc_estimated_margin_ratio_pct,
    calc_min_sl_pct_from_loss,
    calc_margin_usdt,
    calc_quantity_from_order_usdt,
    normalize_tp_sl,
)

logger = logging.getLogger(__name__)


class PaperTradingEngine:
    def __init__(
        self,
        repo: MySQLTradeRepository,
        predictor: MLPredictor,
        candle_repo: MySQLTradeRepository | None = None,
        predictor_test: MLPredictor | None = None,
        predictor_candles: MLPredictor | None = None,
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
        major_symbol_leverage: int = 5,
        major_symbol_max_risk_pct: float = 20.0,
        poll_interval_sec: float = 6.0,
        stream_max_stale_sec: float = 5.0,
        entry_require_fresh_stream_price: bool = True,
        min_sl_pct: float = 0.004,
        min_sl_loss_pct: float = 5.0,
        sl_extra_buffer_pct: float = 0.0,
        sl_atr_multiplier: float = 0.0,
        sl_atr_timeframe: str = "5m",
        sl_atr_limit: int = 120,
        min_rr: float = 1.5,
        maint_margin_rate: float = 0.02,
        max_risk_pct: float = 12.0,
        max_margin_loss_pct: float = 10.5,
        max_margin_loss_high_atr_pct: float = 12.5,
        max_margin_loss_high_atr_threshold_pct: float = 1.5,
        max_margin_loss_aligned_regime_bonus_pct: float = 1.0,
        max_margin_loss_countertrend_penalty_pct: float = 1.0,
        max_hold_minutes: int = 120,
        disable_sl: bool = False,
        move_sl_to_entry_pnl_pct: float = 15.0,
        move_sl_lock_pnl_pct: float = 10.0,
        move_sl_scale_by_leverage: bool = True,
        move_sl_reference_leverage: float = 5.0,
        pump_hunter_move_sl_to_entry_pnl_pct: float = 3.0,
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
        btc_filter_block_nonfollow_countertrend: bool = True,
        btc_filter_nonfollow_countertrend_min_confidence: float = 0.70,
        btc_filter_nonfollow_countertrend_min_win: float = 0.88,
        btc_trend_hour_lock_enabled: bool = True,
        btc_trend_hour_lock_min_confidence: float = 0.60,
        btc_trend_hour_lock_countertrend_hours: float = 2.0,
        btc_trend_hour_lock_apply_non_btc_follow: bool = True,
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
        btc_reversal_min_profit_pct: float = 0.1,
        btc_reversal_loss_exit_enabled: bool = False,
        btc_reversal_loss_exit_days_vn: str = "MON,TUE,WED,THU,FRI,SAT,SUN",
        btc_reversal_loss_exit_min_loss_pct: float = 0.1,
        btc_short_rebound_profit_exit_enabled: bool = True,
        btc_short_stall_profit_exit_enabled: bool = True,
        btc_short_stall_profit_exit_lookback_candles: int = 3,
        btc_short_stall_profit_exit_min_pullback_pct: float = 0.9,
        btc_short_stall_profit_exit_max_cluster_range_pct: float = 0.45,
        btc_short_stall_profit_exit_max_avg_body_pct: float = 0.18,
        btc_short_stall_profit_exit_near_low_pct: float = 0.35,
        btc_short_slow_grind_block_enabled: bool = True,
        btc_short_slow_grind_min_rebound_pct: float = 0.35,
        btc_short_slow_grind_max_avg_body_pct: float = 0.22,
        btc_short_slow_grind_min_upper_wick_ratio: float = 0.28,
        btc_short_slow_grind_max_box_position_pct: float = 0.72,
        btc_long_weak_base_block_enabled: bool = True,
        btc_long_weak_base_min_pullback_pct: float = 0.45,
        btc_long_weak_base_max_avg_body_pct: float = 0.22,
        btc_long_weak_base_min_upper_wick_ratio: float = 0.28,
        btc_long_weak_base_max_box_position_pct: float = 0.48,
        btc_short_rebound_ema99_block_enabled: bool = True,
        btc_long_pullback_ema99_block_enabled: bool = True,
        btc_long_top_fade_block_enabled: bool = True,
        btc_short_rebound_ema99_tolerance_pct: float = 0.004,
        btc_short_rebound_ema99_rebound_pct: float = 0.9,
        btc_long_top_fade_pullback_pct: float = 0.45,
        btc_short_rebound_ema99_green_candle_pct: float = 0.35,
        btc_short_rebound_ema99_lookback_candles: int = 12,
        btc_short_rebound_1h_block_enabled: bool = True,
        btc_short_rebound_1h_lookback_candles: int = 12,
        btc_short_rebound_1h_rebound_pct: float = 1.0,
        btc_short_rebound_1h_green_candle_pct: float = 0.25,
        btc_short_rebound_1h_15m_confirm_pct: float = 0.6,
        btc_short_rebound_1h_ema8_tolerance_pct: float = 0.003,
        btc_profit_lock_enabled: bool = True,
        btc_profit_lock_min_confidence: float = 0.6,
        btc_follow_min_corr: float = 0.45,
        btc_follow_min_beta: float = 0.2,
        btc_follow_lookback: int = 120,
        btc_follow_cache_sec: float = 300.0,
        discord_loss_alert_enabled: bool = True,
        discord_loss_alert_threshold_pct: float = -8.0,
        discord_loss_alert_rearm_pct: float = -6.0,
        discord_loss_webhook_url: str = "",
        discord_open_alert_enabled: bool = True,
        discord_open_webhook_url: str = "",
        base_ml_max_symbols: int = 200,
        limit_max_orders_per_cycle: int = 4,
        test_ml_enabled: bool = False,
        test_ml_min_win_probability: float = 0.75,
        test_ml_max_symbols: int = 80,
        test_ml_max_orders_per_cycle: int = 2,
        candles_bg_enabled: bool = False,
        candles_bg_min_win_probability: float = 0.75,
        candles_bg_max_symbols: int = 80,
        candles_bg_max_orders_per_cycle: int = 2,
        liquid_max_orders_per_cycle: int = 2,
        max_open_trades: int = 24,
        pump_max_open_trades: int = 48,
        ema99_bounce_max_open_trades: int = 24,
        max_open_shorts: int = 18,
        candles_bg_entry_type: str = "ML_CANDLES_BG",
        single_position_per_symbol_side: bool = True,
        reentry_cooldown_minutes: int = 0,
        reentry_after_sl_cooldown_minutes: int = 30,
        symbol_sl_block_minutes: int = 180,
        instant_sl_guard_enabled: bool = True,
        instant_sl_guard_max_hold_minutes: int = 25,
        instant_sl_guard_min_abs_pnl_pct: float = 10.0,
        instant_sl_guard_min_abs_mae_pct: float = 8.0,
        instant_sl_guard_cooldown_minutes: int = 90,
        instant_sl_guard_short_top_test_bypass_enabled: bool = True,
        instant_sl_guard_short_top_test_lookback_candles: int = 20,
        instant_sl_guard_short_top_test_tolerance_pct: float = 0.001,
        instant_sl_guard_short_rejection_min_upper_wick_ratio: float = 0.35,
        instant_sl_guard_short_rejection_min_wick_body_ratio: float = 1.2,
        entry_long_pump_red_block_enabled: bool = True,
        entry_long_pump_red_lookback_candles: int = 12,
        entry_long_pump_red_top_tolerance_pct: float = 0.0015,
        entry_long_pump_red_pump_min_body_pct: float = 0.6,
        entry_long_pump_red_confirm_min_body_pct: float = 0.2,
        entry_symbol_shock_pause_enabled: bool = True,
        entry_symbol_shock_pause_lookback_candles: int = 12,
        entry_symbol_shock_pause_cooldown_candles: int = 3,
        entry_symbol_shock_pause_min_range_pct: float = 0.9,
        entry_symbol_shock_pause_min_body_pct: float = 0.3,
        entry_symbol_shock_pause_min_wick_ratio: float = 0.45,
        entry_symbol_shock_pause_min_volume_ratio: float = 2.2,
        entry_symbol_shock_pause_min_range_vs_avg: float = 1.8,
        entry_symbol_shock_pause_use_btc_for_alts: bool = True,
        entry_symbol_shock_pause_btc_symbol: str = "BTC/USDT",
        entry_symbol_shock_pause_action: str = "OFFSET",
        entry_symbol_shock_pause_offset_factor: float = 0.35,
        entry_symbol_shock_pause_offset_max_pct: float = 0.8,
        entry_symbol_shock_directional_offset_only: bool = True,
        entry_symbol_shock_long_offset_factor: float = 0.6,
        entry_symbol_shock_short_offset_factor: float = 0.6,
        entry_symbol_shock_min_offset_pct: float = 0.2,
        entry_short_btc_short_base_offset_pct: float = 0.1,
        entry_bull_body_short_btc_short_offset_pct: float = 0.15,
        entry_strong_bull_short_offset_pct: float = 0.25,
        entry_bull_engulfing_short_offset_pct: float = 0.15,
        entry_hammer_short_offset_pct: float = 0.18,
        entry_doji_short_offset_pct: float = 0.12,
        entry_inside_bar_long_offset_pct: float = 0.15,
        entry_doji_neutral_offset_pct: float = 0.1,
        entry_symbol_shock_strong_block_enabled: bool = True,
        entry_symbol_shock_strong_min_range_pct: float = 1.2,
        entry_symbol_shock_strong_min_body_pct: float = 0.5,
        entry_symbol_shock_strong_min_volume_ratio: float = 3.0,
        entry_symbol_shock_strong_min_range_vs_avg: float = 2.4,
        entry_short_inside_bar_breakdown_confirm_enabled: bool = True,
        entry_long_inside_bar_breakout_confirm_enabled: bool = True,
        entry_strong_bull_long_confirm_enabled: bool = True,
        entry_strong_bull_short_confirm_enabled: bool = True,
        instant_sl_guard_short_top_test_cache_sec: float = 8.0,
        instant_sl_global_guard_enabled: bool = True,
        instant_sl_global_threshold: int = 3,
        instant_sl_global_window_minutes: int = 20,
        instant_sl_global_cooldown_minutes: int = 60,
        entry_hard_block_hours_vn: str = "6,7",
        hourly_profile_enabled: bool = True,
        hourly_profile_min_samples: int = 60,
        hourly_profile_prob_alpha: float = 0.25,
        hourly_profile_refresh_sec: int = 300,
        hourly_profile_lookback_days: int = 60,
        hourly_profile_use_weekday: bool = True,
        hourly_profile_use_btc_trend: bool = True,
        hourly_profile_btc_trend_min_confidence: float = 0.55,
        hourly_bad_window_enabled: bool = True,
        hourly_bad_window_min_samples: int = 60,
        hourly_bad_window_block_win_rate_pct: float = 48.0,
        hourly_bad_window_strict_win_rate_pct: float = 53.0,
        hourly_bad_window_strict_min_win_bonus: float = 0.04,
        hourly_bad_window_countertrend_hard_block: bool = True,
        bullish_short_nonfollow_max_open_ratio: float = 0.25,
        bullish_short_nonfollow_min_win_bonus: float = 0.05,
        short_sl_streak_guard_enabled: bool = True,
        short_sl_streak_threshold: int = 3,
        short_sl_streak_cooldown_minutes: int = 45,
        short_sl_streak_refresh_sec: int = 15,
        open_pressure_close_enabled: bool = True,
        open_pressure_close_window_minutes: int = 12,
        open_pressure_close_min_opens: int = 3,
        open_pressure_close_min_lead: int = 1,
        open_pressure_close_min_net_pnl_usdt: float = 0.0,
        fee_taker_pct: float = 0.0005,
        fee_maker_pct: float = 0.0002,
    ) -> None:
        self.repo = repo
        self.candle_repo = candle_repo
        self.predictor = predictor
        self.predictor_test = predictor_test
        self.predictor_candles = predictor_candles
        self.liquid_predictor = liquid_predictor
        self.price_stream = price_stream
        self.market_client = BinanceFuturesClient()
        self.pump_scanner = PumpScannerService(client=self.market_client)
        self.pattern_analyzer = CandlePatternAnalyzer(client=self.market_client)
        self.pattern_performance = PatternPerformanceResolver(
            analyzer=self.pattern_analyzer,
            repos=[self.repo, self.candle_repo],
            lookback=settings.paper_trade_perfect_pattern_lookback,
        )
        self.min_win_probability = min_win_probability
        self.quantity = quantity
        self.order_usdt = max(0.0, order_usdt)
        self.margin_usdt = max(0.0, margin_usdt)
        self.leverage = leverage
        self.perfect_pattern_leverage_enabled = bool(settings.paper_trade_perfect_pattern_leverage_enabled)
        self.perfect_pattern_leverage = max(1, int(settings.paper_trade_perfect_pattern_leverage))
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
        self.entry_require_fresh_stream_price = bool(entry_require_fresh_stream_price)
        self.min_sl_pct = min_sl_pct
        self.min_sl_loss_pct = max(0.0, min_sl_loss_pct)
        self.sl_extra_buffer_pct = max(0.0, sl_extra_buffer_pct)
        self.sl_atr_multiplier = max(0.0, sl_atr_multiplier)
        self.sl_atr_timeframe = sl_atr_timeframe or "5m"
        self.sl_atr_limit = max(30, min(500, int(sl_atr_limit)))
        self.min_rr = min_rr
        self.maint_margin_rate = max(0.0, maint_margin_rate)
        self.max_risk_pct = max(0.0, max_risk_pct)
        self.max_margin_loss_pct = max(0.5, float(max_margin_loss_pct))
        self.max_margin_loss_high_atr_pct = max(self.max_margin_loss_pct, float(max_margin_loss_high_atr_pct))
        self.max_margin_loss_high_atr_threshold_pct = max(0.0, float(max_margin_loss_high_atr_threshold_pct))
        self.max_margin_loss_aligned_regime_bonus_pct = max(0.0, float(max_margin_loss_aligned_regime_bonus_pct))
        self.max_margin_loss_countertrend_penalty_pct = max(0.0, float(max_margin_loss_countertrend_penalty_pct))
        self.max_hold_minutes = max(1, max_hold_minutes)
        self.disable_sl = disable_sl
        self.move_sl_to_entry_pnl_pct = max(0.0, move_sl_to_entry_pnl_pct)
        self.move_sl_lock_pnl_pct = max(0.0, float(move_sl_lock_pnl_pct))
        self.move_sl_scale_by_leverage = bool(move_sl_scale_by_leverage)
        self.move_sl_reference_leverage = max(0.1, float(move_sl_reference_leverage))
        self.pump_hunter_move_sl_to_entry_pnl_pct = max(0.0, float(pump_hunter_move_sl_to_entry_pnl_pct))
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
        self.btc_filter_block_nonfollow_countertrend = bool(btc_filter_block_nonfollow_countertrend)
        self.btc_filter_nonfollow_countertrend_min_confidence = max(
            self.btc_filter_min_confidence,
            min(float(btc_filter_nonfollow_countertrend_min_confidence), 0.99),
        )
        self.btc_filter_nonfollow_countertrend_min_win = max(
            0.5,
            min(float(btc_filter_nonfollow_countertrend_min_win), 0.99),
        )
        self.btc_trend_hour_lock_enabled = bool(btc_trend_hour_lock_enabled)
        self.btc_trend_hour_lock_min_confidence = max(0.5, min(float(btc_trend_hour_lock_min_confidence), 0.99))
        self.btc_trend_hour_lock_countertrend_hours = max(0.0, float(btc_trend_hour_lock_countertrend_hours))
        self.btc_trend_hour_lock_apply_non_btc_follow = bool(btc_trend_hour_lock_apply_non_btc_follow)
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
        self.btc_reversal_min_profit_pct = max(0.0, float(btc_reversal_min_profit_pct))
        self.btc_reversal_loss_exit_enabled = bool(btc_reversal_loss_exit_enabled)
        self.btc_reversal_loss_exit_days_vn = str(btc_reversal_loss_exit_days_vn or "").strip()
        self._btc_reversal_loss_exit_days_set = self._parse_weekday_set(self.btc_reversal_loss_exit_days_vn)
        self.btc_reversal_loss_exit_min_loss_pct = max(0.0, float(btc_reversal_loss_exit_min_loss_pct))
        self.btc_short_rebound_profit_exit_enabled = bool(btc_short_rebound_profit_exit_enabled)
        self.btc_short_stall_profit_exit_enabled = bool(btc_short_stall_profit_exit_enabled)
        self.btc_short_stall_profit_exit_lookback_candles = max(3, min(int(btc_short_stall_profit_exit_lookback_candles), 8))
        self.btc_short_stall_profit_exit_min_pullback_pct = max(0.1, min(float(btc_short_stall_profit_exit_min_pullback_pct), 10.0))
        self.btc_short_stall_profit_exit_max_cluster_range_pct = max(0.05, min(float(btc_short_stall_profit_exit_max_cluster_range_pct), 5.0))
        self.btc_short_stall_profit_exit_max_avg_body_pct = max(0.02, min(float(btc_short_stall_profit_exit_max_avg_body_pct), 5.0))
        self.btc_short_stall_profit_exit_near_low_pct = max(0.05, min(float(btc_short_stall_profit_exit_near_low_pct), 5.0))
        self.btc_short_slow_grind_block_enabled = bool(btc_short_slow_grind_block_enabled)
        self.btc_short_slow_grind_min_rebound_pct = max(0.05, min(float(btc_short_slow_grind_min_rebound_pct), 5.0))
        self.btc_short_slow_grind_max_avg_body_pct = max(0.02, min(float(btc_short_slow_grind_max_avg_body_pct), 5.0))
        self.btc_short_slow_grind_min_upper_wick_ratio = max(0.05, min(float(btc_short_slow_grind_min_upper_wick_ratio), 0.95))
        self.btc_short_slow_grind_max_box_position_pct = max(0.05, min(float(btc_short_slow_grind_max_box_position_pct), 0.98))
        self.btc_long_weak_base_block_enabled = bool(btc_long_weak_base_block_enabled)
        self.btc_long_weak_base_min_pullback_pct = max(0.05, min(float(btc_long_weak_base_min_pullback_pct), 5.0))
        self.btc_long_weak_base_max_avg_body_pct = max(0.02, min(float(btc_long_weak_base_max_avg_body_pct), 5.0))
        self.btc_long_weak_base_min_upper_wick_ratio = max(0.05, min(float(btc_long_weak_base_min_upper_wick_ratio), 0.95))
        self.btc_long_weak_base_max_box_position_pct = max(0.05, min(float(btc_long_weak_base_max_box_position_pct), 0.98))
        self.btc_short_rebound_ema99_block_enabled = bool(btc_short_rebound_ema99_block_enabled)
        self.btc_long_pullback_ema99_block_enabled = bool(btc_long_pullback_ema99_block_enabled)
        self.btc_long_top_fade_block_enabled = bool(btc_long_top_fade_block_enabled)
        self.btc_short_rebound_ema99_tolerance_pct = max(0.0, min(float(btc_short_rebound_ema99_tolerance_pct), 0.03))
        self.btc_short_rebound_ema99_rebound_pct = max(0.1, min(float(btc_short_rebound_ema99_rebound_pct), 10.0))
        self.btc_long_top_fade_pullback_pct = max(0.05, min(float(btc_long_top_fade_pullback_pct), 5.0))
        self.btc_short_rebound_ema99_green_candle_pct = max(0.0, min(float(btc_short_rebound_ema99_green_candle_pct), 5.0))
        self.btc_short_rebound_ema99_lookback_candles = max(4, min(int(btc_short_rebound_ema99_lookback_candles), 48))
        self.btc_short_rebound_1h_block_enabled = bool(btc_short_rebound_1h_block_enabled)
        self.btc_short_rebound_1h_lookback_candles = max(4, min(int(btc_short_rebound_1h_lookback_candles), 72))
        self.btc_short_rebound_1h_rebound_pct = max(0.1, min(float(btc_short_rebound_1h_rebound_pct), 12.0))
        self.btc_short_rebound_1h_green_candle_pct = max(0.0, min(float(btc_short_rebound_1h_green_candle_pct), 6.0))
        self.btc_short_rebound_1h_15m_confirm_pct = max(0.0, min(float(btc_short_rebound_1h_15m_confirm_pct), 8.0))
        self.btc_short_rebound_1h_ema8_tolerance_pct = max(0.0, min(float(btc_short_rebound_1h_ema8_tolerance_pct), 0.03))
        self.btc_profit_lock_enabled = bool(btc_profit_lock_enabled)
        self.btc_profit_lock_min_confidence = max(0.5, min(float(btc_profit_lock_min_confidence), 0.99))
        self.btc_follow_min_corr = max(0.0, min(float(btc_follow_min_corr), 0.99))
        self.btc_follow_min_beta = max(0.0, float(btc_follow_min_beta))
        self.btc_follow_lookback = max(60, min(500, int(btc_follow_lookback)))
        self.btc_follow_cache_sec = max(30.0, float(btc_follow_cache_sec))
        self.discord_loss_alert_enabled = bool(discord_loss_alert_enabled)
        self.discord_loss_alert_threshold_pct = float(discord_loss_alert_threshold_pct)
        self.discord_loss_alert_rearm_pct = float(discord_loss_alert_rearm_pct)
        self.discord_loss_webhook_url = str(discord_loss_webhook_url or "").strip()
        self._discord_loss_alerted_trade_ids: set[int] = set()
        self.discord_open_alert_enabled = bool(discord_open_alert_enabled)
        self.discord_open_webhook_url = str(discord_open_webhook_url or "").strip()
        self.base_ml_max_symbols = max(10, min(600, int(base_ml_max_symbols)))
        self.limit_max_orders_per_cycle = max(0, min(50, int(limit_max_orders_per_cycle)))
        self.test_ml_enabled = bool(test_ml_enabled)
        self.test_ml_min_win_probability = max(0.0, min(float(test_ml_min_win_probability), 1.0))
        self.test_ml_max_symbols = max(10, min(600, int(test_ml_max_symbols)))
        self.test_ml_max_orders_per_cycle = max(1, min(20, int(test_ml_max_orders_per_cycle)))
        self.candles_bg_enabled = bool(candles_bg_enabled)
        self.candles_bg_min_win_probability = max(0.0, min(float(candles_bg_min_win_probability), 1.0))
        self.candles_bg_max_symbols = max(10, min(600, int(candles_bg_max_symbols)))
        self.candles_bg_max_orders_per_cycle = max(1, min(20, int(candles_bg_max_orders_per_cycle)))
        self.liquid_max_orders_per_cycle = max(0, min(20, int(liquid_max_orders_per_cycle)))
        self.max_open_trades = max(0, min(500, int(max_open_trades)))
        self.pump_max_open_trades = max(0, min(500, int(pump_max_open_trades)))
        self.ema99_bounce_max_open_trades = max(0, min(500, int(ema99_bounce_max_open_trades)))
        self.max_open_shorts = max(0, min(500, int(max_open_shorts)))
        self.ema99_bounce_max_hold_minutes = max(15, int(settings.ema99_bounce_paper_max_hold_minutes))
        self.candles_bg_entry_type = str(candles_bg_entry_type or "ML_CANDLES_BG").strip().upper() or "ML_CANDLES_BG"
        self.single_position_per_symbol_side = bool(single_position_per_symbol_side)
        self.reentry_cooldown_minutes = max(0, int(reentry_cooldown_minutes))
        self.reentry_after_sl_cooldown_minutes = max(0, int(reentry_after_sl_cooldown_minutes))
        self.symbol_sl_block_minutes = max(0, int(symbol_sl_block_minutes))
        self.instant_sl_guard_enabled = bool(instant_sl_guard_enabled)
        self.instant_sl_guard_max_hold_minutes = max(1, int(instant_sl_guard_max_hold_minutes))
        self.instant_sl_guard_min_abs_pnl_pct = max(0.0, float(instant_sl_guard_min_abs_pnl_pct))
        self.instant_sl_guard_min_abs_mae_pct = max(0.0, float(instant_sl_guard_min_abs_mae_pct))
        self.instant_sl_guard_cooldown_minutes = max(1, int(instant_sl_guard_cooldown_minutes))
        self.instant_sl_guard_short_top_test_bypass_enabled = bool(instant_sl_guard_short_top_test_bypass_enabled)
        self.instant_sl_guard_short_top_test_lookback_candles = max(
            5,
            min(120, int(instant_sl_guard_short_top_test_lookback_candles)),
        )
        self.instant_sl_guard_short_top_test_tolerance_pct = max(
            0.0,
            min(float(instant_sl_guard_short_top_test_tolerance_pct), 0.02),
        )
        self.instant_sl_guard_short_rejection_min_upper_wick_ratio = max(
            0.05,
            min(float(instant_sl_guard_short_rejection_min_upper_wick_ratio), 0.95),
        )
        self.instant_sl_guard_short_rejection_min_wick_body_ratio = max(
            0.5,
            min(float(instant_sl_guard_short_rejection_min_wick_body_ratio), 8.0),
        )
        self.entry_long_pump_red_block_enabled = bool(entry_long_pump_red_block_enabled)
        self.entry_long_pump_red_lookback_candles = max(5, min(120, int(entry_long_pump_red_lookback_candles)))
        self.entry_long_pump_red_top_tolerance_pct = max(0.0, min(float(entry_long_pump_red_top_tolerance_pct), 0.02))
        self.entry_long_pump_red_pump_min_body_pct = max(0.05, min(float(entry_long_pump_red_pump_min_body_pct), 10.0))
        self.entry_long_pump_red_confirm_min_body_pct = max(0.05, min(float(entry_long_pump_red_confirm_min_body_pct), 10.0))
        self.entry_symbol_shock_pause_enabled = bool(entry_symbol_shock_pause_enabled)
        self.entry_symbol_shock_pause_lookback_candles = max(5, min(120, int(entry_symbol_shock_pause_lookback_candles)))
        self.entry_symbol_shock_pause_cooldown_candles = max(1, min(12, int(entry_symbol_shock_pause_cooldown_candles)))
        self.entry_symbol_shock_pause_min_range_pct = max(0.1, min(float(entry_symbol_shock_pause_min_range_pct), 10.0))
        self.entry_symbol_shock_pause_min_body_pct = max(0.05, min(float(entry_symbol_shock_pause_min_body_pct), 10.0))
        self.entry_symbol_shock_pause_min_wick_ratio = max(0.05, min(float(entry_symbol_shock_pause_min_wick_ratio), 0.95))
        self.entry_symbol_shock_pause_min_volume_ratio = max(1.0, min(float(entry_symbol_shock_pause_min_volume_ratio), 20.0))
        self.entry_symbol_shock_pause_min_range_vs_avg = max(1.0, min(float(entry_symbol_shock_pause_min_range_vs_avg), 10.0))
        self.entry_symbol_shock_pause_use_btc_for_alts = bool(entry_symbol_shock_pause_use_btc_for_alts)
        self.entry_symbol_shock_pause_btc_symbol = str(entry_symbol_shock_pause_btc_symbol or "BTC/USDT").strip() or "BTC/USDT"
        shock_action = str(entry_symbol_shock_pause_action or "OFFSET").strip().upper()
        self.entry_symbol_shock_pause_action = shock_action if shock_action in {"BLOCK", "OFFSET"} else "OFFSET"
        self.entry_symbol_shock_pause_offset_factor = max(0.0, min(float(entry_symbol_shock_pause_offset_factor), 2.0))
        self.entry_symbol_shock_pause_offset_max_pct = max(0.0, min(float(entry_symbol_shock_pause_offset_max_pct), 5.0))
        self.entry_symbol_shock_directional_offset_only = bool(entry_symbol_shock_directional_offset_only)
        self.entry_symbol_shock_long_offset_factor = max(0.0, min(float(entry_symbol_shock_long_offset_factor), 3.0))
        self.entry_symbol_shock_short_offset_factor = max(0.0, min(float(entry_symbol_shock_short_offset_factor), 3.0))
        self.entry_symbol_shock_min_offset_pct = max(0.0, min(float(entry_symbol_shock_min_offset_pct), 2.0))
        # Central place to tune pattern-specific entry offsets.
        self.pattern_entry_offset_rules: list[dict[str, str | float]] = [
            {
                "side": "SHORT",
                "btc_trend": "SHORT",
                "offset_pct": max(0.0, min(float(entry_short_btc_short_base_offset_pct), 2.0)),
            },
            {
                "pattern": "BULL_ENGULFING",
                "btc_trend": "SHORT",
                "offset_pct": max(0.0, min(float(entry_bull_engulfing_short_offset_pct), 2.0)),
            },
            {
                "pattern": "BULL_BODY",
                "btc_trend": "SHORT",
                "offset_pct": max(0.0, min(float(entry_bull_body_short_btc_short_offset_pct), 2.0)),
            },
            {
                "pattern": "HAMMER",
                "btc_trend": "SHORT",
                "offset_pct": max(0.0, min(float(entry_hammer_short_offset_pct), 2.0)),
            },
            {
                "pattern": "DOJI",
                "btc_trend": "SHORT",
                "offset_pct": max(0.0, min(float(entry_doji_short_offset_pct), 2.0)),
            },
            {
                "pattern": "INSIDE_BAR",
                "btc_trend": "LONG",
                "offset_pct": max(0.0, min(float(entry_inside_bar_long_offset_pct), 2.0)),
            },
            {
                "pattern": "DOJI",
                "btc_trend": "NEUTRAL",
                "offset_pct": max(0.0, min(float(entry_doji_neutral_offset_pct), 2.0)),
            },
        ]
        self.entry_symbol_shock_strong_block_enabled = bool(entry_symbol_shock_strong_block_enabled)
        self.entry_symbol_shock_strong_min_range_pct = max(0.2, min(float(entry_symbol_shock_strong_min_range_pct), 10.0))
        self.entry_symbol_shock_strong_min_body_pct = max(0.1, min(float(entry_symbol_shock_strong_min_body_pct), 10.0))
        self.entry_symbol_shock_strong_min_volume_ratio = max(1.0, min(float(entry_symbol_shock_strong_min_volume_ratio), 20.0))
        self.entry_symbol_shock_strong_min_range_vs_avg = max(1.0, min(float(entry_symbol_shock_strong_min_range_vs_avg), 10.0))
        self.entry_short_inside_bar_breakdown_confirm_enabled = bool(entry_short_inside_bar_breakdown_confirm_enabled)
        self.entry_long_inside_bar_breakout_confirm_enabled = bool(entry_long_inside_bar_breakout_confirm_enabled)
        self.entry_strong_bull_long_confirm_enabled = bool(entry_strong_bull_long_confirm_enabled)
        self.entry_strong_bull_short_confirm_enabled = bool(entry_strong_bull_short_confirm_enabled)
        self.instant_sl_guard_short_top_test_cache_sec = max(
            1.0,
            min(float(instant_sl_guard_short_top_test_cache_sec), 60.0),
        )
        self.instant_sl_global_guard_enabled = bool(instant_sl_global_guard_enabled)
        self.instant_sl_global_threshold = max(1, int(instant_sl_global_threshold))
        self.instant_sl_global_window_minutes = max(1, int(instant_sl_global_window_minutes))
        self.instant_sl_global_cooldown_minutes = max(1, int(instant_sl_global_cooldown_minutes))
        self.entry_hard_block_hours_vn = str(entry_hard_block_hours_vn or "").strip()
        self._entry_hard_block_hours_set = self._parse_entry_hard_block_hours(self.entry_hard_block_hours_vn)
        self.hourly_profile_enabled = bool(hourly_profile_enabled)
        self.hourly_profile_min_samples = max(10, int(hourly_profile_min_samples))
        self.hourly_profile_prob_alpha = max(0.0, float(hourly_profile_prob_alpha))
        self.hourly_profile_refresh_sec = max(30, int(hourly_profile_refresh_sec))
        self.hourly_profile_lookback_days = max(1, min(3650, int(hourly_profile_lookback_days)))
        self.hourly_profile_use_weekday = bool(hourly_profile_use_weekday)
        self.hourly_profile_use_btc_trend = bool(hourly_profile_use_btc_trend)
        self.hourly_profile_btc_trend_min_confidence = max(0.0, min(float(hourly_profile_btc_trend_min_confidence), 0.99))
        self.hourly_bad_window_enabled = bool(hourly_bad_window_enabled)
        self.hourly_bad_window_min_samples = max(10, int(hourly_bad_window_min_samples))
        self.hourly_bad_window_block_win_rate_pct = max(0.0, min(float(hourly_bad_window_block_win_rate_pct), 100.0))
        self.hourly_bad_window_strict_win_rate_pct = max(0.0, min(float(hourly_bad_window_strict_win_rate_pct), 100.0))
        if self.hourly_bad_window_strict_win_rate_pct < self.hourly_bad_window_block_win_rate_pct:
            self.hourly_bad_window_strict_win_rate_pct = self.hourly_bad_window_block_win_rate_pct
        self.hourly_bad_window_strict_min_win_bonus = max(0.0, min(float(hourly_bad_window_strict_min_win_bonus), 0.25))
        self.hourly_bad_window_countertrend_hard_block = bool(hourly_bad_window_countertrend_hard_block)
        self.bullish_short_nonfollow_max_open_ratio = max(0.0, min(float(bullish_short_nonfollow_max_open_ratio), 0.9))
        self.bullish_short_nonfollow_min_win_bonus = max(0.0, min(float(bullish_short_nonfollow_min_win_bonus), 0.25))
        self.short_sl_streak_guard_enabled = bool(short_sl_streak_guard_enabled)
        self.short_sl_streak_threshold = max(1, int(short_sl_streak_threshold))
        self.short_sl_streak_cooldown_minutes = max(1, int(short_sl_streak_cooldown_minutes))
        self.short_sl_streak_refresh_sec = max(5, int(short_sl_streak_refresh_sec))
        self.open_pressure_close_enabled = bool(open_pressure_close_enabled)
        self.open_pressure_close_window_minutes = max(1, int(open_pressure_close_window_minutes))
        self.open_pressure_close_min_opens = max(1, int(open_pressure_close_min_opens))
        self.open_pressure_close_min_lead = max(1, int(open_pressure_close_min_lead))
        self.open_pressure_close_min_net_pnl_usdt = float(open_pressure_close_min_net_pnl_usdt)
        # Binance Futures fee rates (per-side). Default: taker=0.05%, maker=0.02%.
        self.fee_taker_pct = max(0.0, float(fee_taker_pct))
        self.fee_maker_pct = max(0.0, float(fee_maker_pct))
        self._task: asyncio.Task | None = None
        self._running = False
        self._vn_tz = timezone(timedelta(hours=7))
        self._atr_cache: dict[str, tuple[float, float]] = {}
        self._top_vol_cache: tuple[float, list[str]] | None = None
        self._short_top_test_rejection_cache: dict[str, tuple[float, bool]] = {}
        self._long_pump_red_confirm_cache: dict[str, tuple[float, bool]] = {}
        self._symbol_shock_pause_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._short_inside_bar_breakdown_cache: dict[str, tuple[float, bool]] = {}
        self._long_inside_bar_breakout_cache: dict[str, tuple[float, bool]] = {}
        self._strong_bull_long_confirm_cache: dict[str, tuple[float, bool]] = {}
        self._strong_bull_short_confirm_cache: dict[str, tuple[float, bool]] = {}
        self._btc_long_confirmation_cache: dict[str, tuple[float, bool]] = {}
        self._btc_trend_cache: tuple[float, dict[str, Any]] | None = None
        self._btc_follow_cache: dict[str, tuple[float, bool, float, float]] = {}
        self._open_pause_until_ts: float = 0.0
        self._open_pause_reason: str | None = None
        self._btc_long_regime_short_lock_until_ts: float = 0.0
        self._btc_short_regime_long_lock_until_ts: float = 0.0
        self._btc_trend_hour_lock_trend_side: str = "NEUTRAL"
        self._btc_up_shock_long_block_until_ts: float = 0.0
        self._btc_down_shock_short_block_until_ts: float = 0.0
        self._hourly_profiles_cache: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
        self._hourly_profiles_refreshed_ts: float = 0.0
        self._short_sl_pause_until_ts: float = 0.0
        self._short_sl_streak_refreshed_ts: float = 0.0
        self._short_sl_streak_count: int = 0
        self._short_sl_last_processed_close_id: int = 0
        self._instant_sl_symbol_side_lock_until_ts: dict[str, float] = {}
        self._instant_sl_global_events_ts: list[float] = []
        self._recent_open_pressure_events: list[tuple[float, str]] = []
        self.high_volatility_threshold_pct = 2.0
        self.high_volatility_leverage = 3

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
        await asyncio.to_thread(self._refresh_hourly_profiles_if_needed)
        signals: list[dict[str, Any]] = []
        try:
            snapshot = await asyncio.to_thread(
                get_scan_snapshot,
                min_win=0.7,
                max_symbols=self.base_ml_max_symbols,
            )
            signals = snapshot.get("signals", [])
            if self.major_dynamic_enabled:
                await asyncio.to_thread(self._refresh_major_symbols_runtime, signals)
        except Exception as exc:
            # Never block TP/SL management because scan failed.
            print(f"[paper-engine] scan failed, continue with open-trade management only: {type(exc).__name__}: {exc}")
        open_trades = self.repo.list_open_trades()
        open_trades_by_symbol = self._index_open_trades_by_symbol(open_trades)
        candles_entry_type = self.candles_bg_entry_type
        candles_open_trades = [
            row for row in open_trades
            if str(row.get("entry_type") or "").strip().upper() == candles_entry_type
        ]
        candles_open_trades_by_symbol = self._index_open_trades_by_symbol(candles_open_trades)
        closed_trade_ids: set[int] = set()
        test_symbols: list[str] = []
        if self.test_ml_enabled and self.predictor_test is not None:
            test_symbols = await asyncio.to_thread(get_cached_symbols_snapshot, self.test_ml_max_symbols)
            if not test_symbols:
                # Fallback to symbols present in current scan response if cache is empty.
                test_symbols = [str(item.get("symbol")) for item in signals if item.get("symbol")]
            if len(test_symbols) > self.test_ml_max_symbols:
                test_symbols = test_symbols[: self.test_ml_max_symbols]
        candles_symbols: list[str] = []
        if self.candles_bg_enabled and self.predictor_candles is not None:
            candles_symbols = await asyncio.to_thread(get_cached_symbols_snapshot, self.candles_bg_max_symbols)
            if not candles_symbols:
                candles_symbols = [str(item.get("symbol")) for item in signals if item.get("symbol")]
            if len(candles_symbols) > self.candles_bg_max_symbols:
                candles_symbols = candles_symbols[: self.candles_bg_max_symbols]
        top_vol_symbols: list[str] = []
        if self.liquid_enabled and self.liquid_predictor is not None:
            top_vol_symbols = await asyncio.to_thread(self._load_top_volatility_symbols)

        price_symbols = {str(item.get("symbol")) for item in signals if item.get("symbol")}
        for symbol in test_symbols:
            price_symbols.add(symbol)
        for symbol in candles_symbols:
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
        self._apply_btc_trend_hour_lock(btc_guard)
        self._apply_btc_shock_pause(btc_guard)
        await asyncio.to_thread(self._refresh_short_sl_streak_guard_if_needed)
        open_paused = self._is_open_paused()
        entry_hard_blocked = self._is_entry_hard_blocked_now()

        # 1) Open simulated orders when price reaches predicted entry for >=75% setups.
        if (not open_paused) and (not entry_hard_blocked):
            opened_limit_orders = 0
            for item in signals:
                if self.limit_max_orders_per_cycle > 0 and opened_limit_orders >= self.limit_max_orders_per_cycle:
                    break
                raw_prob = float(item.get("win_probability") or 0.0)
                if raw_prob < self.min_win_probability:
                    continue

                symbol = str(item.get("symbol"))
                side = str(item.get("side"))
                if self._has_conflicting_open_trade(symbol=symbol, side=side, entry_type="LIMIT"):
                    continue
                if self._is_reentry_cooldown_active(symbol=symbol, side=side, entry_type="LIMIT"):
                    continue
                if not self._pass_instant_sl_guard(symbol=symbol, side=side):
                    continue
                if self._portfolio_guard_reason(side=side, open_trades_by_symbol=open_trades_by_symbol):
                    continue

                entry = float(item.get("predicted_entry_price") or 0.0)
                entry = self._apply_symbol_shock_entry_offset(symbol=symbol, side=side, entry=entry)
                entry = self._apply_pattern_entry_offset(symbol=symbol, side=side, entry=entry)
                tp = float(item.get("take_profit") or 0.0)
                sl = float(item.get("stop_loss") or 0.0)
                if entry <= 0 or tp <= 0 or sl <= 0:
                    continue

                market_price = stream_prices.get(symbol)
                if market_price is None and self.entry_require_fresh_stream_price:
                    continue
                if market_price is None:
                    market_price = market_prices.get(symbol)
                if market_price is None:
                    market_price = await asyncio.to_thread(self._resolve_market_price, symbol)
                if market_price is None:
                    continue

                if self.predictor_candles is None:
                    continue
                try:
                    candles_compare_signal = await asyncio.to_thread(
                        self.predictor_candles.predict,
                        symbol,
                        float(market_price),
                    )
                except Exception:
                    continue
                if str(candles_compare_signal.side or "").upper() != str(side or "").upper():
                    continue

                # Blend model signal with realized historical accuracy for this symbol.
                hist_acc = self.repo.symbol_accuracy(symbol=symbol, lookback=300)
                effective_prob = (raw_prob * 0.8 + hist_acc * 0.2) if hist_acc is not None else raw_prob
                effective_prob = self._apply_hourly_profile_to_probability(
                    effective_prob=effective_prob,
                    side=side,
                    entry_type="LIMIT",
                    btc_guard=btc_guard,
                )
                can_open_now, required_min_win = self._evaluate_hourly_bad_window_guard(
                    side=side,
                    entry_type="LIMIT",
                    base_min_win=self.min_win_probability,
                    btc_guard=btc_guard,
                )
                if not can_open_now:
                    continue
                required_min_win = self._apply_bullish_short_nonfollow_min_win_bonus(
                    required_min_win=required_min_win,
                    side=side,
                    symbol=symbol,
                    btc_guard=btc_guard,
                )
                if effective_prob < required_min_win:
                    continue
                if not self._pass_short_sl_streak_guard(side=side):
                    continue
                if not self._pass_bullish_short_nonfollow_ratio_guard(
                    side=side,
                    symbol=symbol,
                    btc_guard=btc_guard,
                    open_trades_by_symbol=open_trades_by_symbol,
                ):
                    continue
                if not self._pass_btc_filter(
                    symbol=symbol,
                    side=side,
                    effective_prob=effective_prob,
                    btc_guard=btc_guard,
                    entry_type="LIMIT",
                ):
                    continue

                # Trigger condition: market touches/gets through entry.
                touched = self._entry_touched(side=side, market_price=market_price, entry=entry)
                if not touched:
                    continue
                fill_price = float(market_price)
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=fill_price,
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                atr_value = await self._resolve_symbol_atr(symbol)
                atr_for_pct = float(atr_value) if atr_value is not None else 0.0
                atr_pct = (atr_for_pct / fill_price) * 100 if fill_price > 0 else 0.0
                leverage = self._resolve_symbol_leverage(symbol, atr_pct, side)
                normalized_tp, normalized_sl = normalize_tp_sl(
                    side=side,
                    entry_price=fill_price,
                    take_profit=tp,
                    stop_loss=sl,
                    min_sl_pct=max(
                        self.min_sl_pct,
                        calc_min_sl_pct_from_loss(min_sl_loss_pct=self.min_sl_loss_pct),
                    ),
                    sl_extra_buffer_pct=self.sl_extra_buffer_pct,
                    atr_value=atr_value,
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self.min_rr,
                    max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
                    leverage=leverage,
                    max_margin_loss_pct=self._resolve_max_margin_loss_pct(symbol=symbol, side=side, atr_pct=atr_pct, btc_guard=btc_guard),
                )
                
                risk_pct = calc_estimated_margin_ratio_pct(
                    leverage=leverage,
                    maint_margin_rate=self.maint_margin_rate,
                )
                if risk_pct > self._resolve_symbol_max_risk_pct(symbol, leverage):
                    continue

                quantity = calc_quantity_from_order_usdt(
                    entry_price=fill_price,
                    order_usdt=self.order_usdt,
                    fallback_quantity=self.quantity,
                )
                margin_usdt = self.margin_usdt
                if margin_usdt <= 0:
                    margin_usdt = calc_margin_usdt(entry_price=fill_price, quantity=quantity, leverage=leverage)
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
                        "entry_price": fill_price,
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
                    entry_price=fill_price,
                    quantity=quantity,
                )
                self._register_open_pressure_event(side=side)
                opened_limit_orders += 1

        # 1a) Optional test model: open separated ML_TEST orders for side-by-side comparison.
        if (not open_paused) and (not entry_hard_blocked) and self.test_ml_enabled and self.predictor_test is not None:
            opened_test_orders = 0
            for symbol in test_symbols:
                if opened_test_orders >= self.test_ml_max_orders_per_cycle:
                    break
                market_price = stream_prices.get(symbol)
                if market_price is None and self.entry_require_fresh_stream_price:
                    continue
                if market_price is None:
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
                effective_prob = self._apply_hourly_profile_to_probability(
                    effective_prob=raw_prob,
                    side=side,
                    entry_type="ML_TEST",
                    btc_guard=btc_guard,
                )
                can_open_now, required_min_win = self._evaluate_hourly_bad_window_guard(
                    side=side,
                    entry_type="ML_TEST",
                    base_min_win=self.test_ml_min_win_probability,
                    btc_guard=btc_guard,
                )
                if not can_open_now:
                    continue
                required_min_win = self._apply_bullish_short_nonfollow_min_win_bonus(
                    required_min_win=required_min_win,
                    side=side,
                    symbol=symbol,
                    btc_guard=btc_guard,
                )
                if effective_prob < required_min_win:
                    continue
                if not self._pass_short_sl_streak_guard(side=side):
                    continue
                if not self._pass_bullish_short_nonfollow_ratio_guard(
                    side=side,
                    symbol=symbol,
                    btc_guard=btc_guard,
                    open_trades_by_symbol=open_trades_by_symbol,
                ):
                    continue
                if self._has_conflicting_open_trade(symbol=symbol, side=side, entry_type="ML_TEST"):
                    continue
                if self._is_reentry_cooldown_active(symbol=symbol, side=side, entry_type="ML_TEST"):
                    continue
                if not self._pass_instant_sl_guard(symbol=symbol, side=side):
                    continue
                if self._portfolio_guard_reason(side=side, open_trades_by_symbol=open_trades_by_symbol):
                    continue

                entry = self._apply_symbol_shock_entry_offset(
                    symbol=symbol,
                    side=side,
                    entry=float(test_signal.predicted_entry_price),
                )
                entry = self._apply_pattern_entry_offset(symbol=symbol, side=side, entry=entry)
                tp = float(test_signal.take_profit)
                sl = float(test_signal.stop_loss)
                if entry <= 0 or tp <= 0 or sl <= 0:
                    continue

                touched = self._entry_touched(side=side, market_price=market_price, entry=entry)
                if not touched:
                    continue
                fill_price = float(market_price)
                if not self._pass_btc_filter(
                    symbol=symbol,
                    side=side,
                    effective_prob=effective_prob,
                    btc_guard=btc_guard,
                    entry_type="ML_TEST",
                ):
                    continue
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=fill_price,
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                atr_value = await self._resolve_symbol_atr(symbol)
                atr_for_pct = float(atr_value) if atr_value is not None else 0.0
                atr_pct = (atr_for_pct / fill_price) * 100 if fill_price > 0 else 0.0
                leverage = self._resolve_symbol_leverage(symbol, atr_pct, side)
                normalized_tp, normalized_sl = normalize_tp_sl(
                    side=side,
                    entry_price=fill_price,
                    take_profit=tp,
                    stop_loss=sl,
                    min_sl_pct=max(
                        self.min_sl_pct,
                        calc_min_sl_pct_from_loss(min_sl_loss_pct=self.min_sl_loss_pct),
                    ),
                    sl_extra_buffer_pct=self.sl_extra_buffer_pct,
                    atr_value=atr_value,
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self.min_rr,
                    max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
                    leverage=leverage,
                    max_margin_loss_pct=self._resolve_max_margin_loss_pct(symbol=symbol, side=side, atr_pct=atr_pct, btc_guard=btc_guard),
                )
                
                risk_pct = calc_estimated_margin_ratio_pct(
                    leverage=leverage,
                    maint_margin_rate=self.maint_margin_rate,
                )
                if risk_pct > self._resolve_symbol_max_risk_pct(symbol, leverage):
                    continue

                quantity = calc_quantity_from_order_usdt(
                    entry_price=fill_price,
                    order_usdt=self.order_usdt,
                    fallback_quantity=self.quantity,
                )
                margin_usdt = self.margin_usdt
                if margin_usdt <= 0:
                    margin_usdt = calc_margin_usdt(entry_price=fill_price, quantity=quantity, leverage=leverage)
                feature_snapshot = await asyncio.to_thread(self._capture_feature_snapshot, symbol, side)
                btc_following = self._resolve_btc_following_flag(symbol)

                trade_id = self.repo.create_open_trade(
                    {
                        "symbol": symbol,
                        "side": side,
                        "btc_following": btc_following,
                        "entry_type": "ML_TEST",
                        "signal_win_probability": raw_prob,
                        "effective_win_probability": effective_prob,
                        "entry_price": fill_price,
                        "take_profit": normalized_tp,
                        "stop_loss": normalized_sl,
                        "reference_win_symbol": getattr(candles_signal, "reference_win_symbol", None),
                        "reference_win_at": getattr(candles_signal, "reference_win_at", None),
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
                    entry_price=fill_price,
                    quantity=quantity,
                )
                self._register_open_pressure_event(side=side)
                opened_test_orders += 1

        # 1aa) Background ML Candles model: independent order stream for comparison.
        if (not open_paused) and (not entry_hard_blocked) and self.candles_bg_enabled and self.predictor_candles is not None:
            opened_candles_orders = 0
            for symbol in candles_symbols:
                if opened_candles_orders >= self.candles_bg_max_orders_per_cycle:
                    break
                market_price = stream_prices.get(symbol)
                if market_price is None and self.entry_require_fresh_stream_price:
                    continue
                if market_price is None:
                    market_price = market_prices.get(symbol)
                if market_price is None:
                    market_price = await asyncio.to_thread(self._resolve_market_price, symbol)
                if market_price is None:
                    continue

                try:
                    candles_signal = await asyncio.to_thread(
                        self.predictor_candles.predict,
                        symbol,
                        float(market_price),
                    )
                except Exception:
                    continue

                raw_prob = float(candles_signal.win_probability)
                if raw_prob < self.candles_bg_min_win_probability:
                    continue
                side = str(candles_signal.side)
                skip_btc_guards = self._skip_btc_guards_for_entry_type(candles_entry_type)
                effective_prob = self._apply_hourly_profile_to_probability(
                    effective_prob=raw_prob,
                    side=side,
                    entry_type=candles_entry_type,
                    btc_guard=btc_guard,
                )
                effective_prob, _ = self._apply_recent_symbol_behavior_penalty(
                    symbol=symbol,
                    side=side,
                    entry_type=candles_entry_type,
                    effective_prob=effective_prob,
                    force_entry_type_scope=True,
                )
                can_open_now, required_min_win = self._evaluate_hourly_bad_window_guard(
                    side=side,
                    entry_type=candles_entry_type,
                    base_min_win=self.candles_bg_min_win_probability,
                    btc_guard=btc_guard,
                )
                if not can_open_now:
                    continue
                if not skip_btc_guards:
                    required_min_win = self._apply_bullish_short_nonfollow_min_win_bonus(
                        required_min_win=required_min_win,
                        side=side,
                        symbol=symbol,
                        btc_guard=btc_guard,
                    )
                if effective_prob < required_min_win:
                    continue
                if not self._pass_short_sl_streak_guard(side=side):
                    continue
                if not skip_btc_guards:
                    if not self._pass_bullish_short_nonfollow_ratio_guard(
                        side=side,
                        symbol=symbol,
                        btc_guard=btc_guard,
                        open_trades_by_symbol=candles_open_trades_by_symbol,
                    ):
                        continue
                if self._has_conflicting_open_trade(
                    symbol=symbol,
                    side=side,
                    entry_type=candles_entry_type,
                    force_entry_type_scope=True,
                ):
                    continue
                if self._is_reentry_cooldown_active(
                    symbol=symbol,
                    side=side,
                    entry_type=candles_entry_type,
                    force_entry_type_scope=True,
                ):
                    continue
                if not self._pass_instant_sl_guard(symbol=symbol, side=side):
                    continue
                if self._portfolio_guard_reason(side=side, open_trades_by_symbol=open_trades_by_symbol):
                    continue

                entry = self._apply_symbol_shock_entry_offset(
                    symbol=symbol,
                    side=side,
                    entry=float(candles_signal.predicted_entry_price),
                )
                entry = self._apply_pattern_entry_offset(symbol=symbol, side=side, entry=entry)
                tp = float(candles_signal.take_profit)
                sl = float(candles_signal.stop_loss)
                if entry <= 0 or tp <= 0 or sl <= 0:
                    continue

                touched = self._entry_touched(side=side, market_price=market_price, entry=entry)
                if not touched:
                    continue
                fill_price = float(market_price)
                if not skip_btc_guards:
                    if not self._pass_btc_filter(
                        symbol=symbol,
                        side=side,
                        effective_prob=effective_prob,
                        btc_guard=btc_guard,
                        entry_type=candles_entry_type,
                    ):
                        continue
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=fill_price,
                    open_trades_by_symbol=candles_open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                atr_value = await self._resolve_symbol_atr(symbol)
                atr_for_pct = float(atr_value) if atr_value is not None else 0.0
                atr_pct = (atr_for_pct / fill_price) * 100 if fill_price > 0 else 0.0
                leverage = self._resolve_symbol_leverage(symbol, atr_pct, side)
                normalized_tp, normalized_sl = normalize_tp_sl(
                    side=side,
                    entry_price=fill_price,
                    take_profit=tp,
                    stop_loss=sl,
                    min_sl_pct=max(
                        self.min_sl_pct,
                        calc_min_sl_pct_from_loss(min_sl_loss_pct=self.min_sl_loss_pct),
                    ),
                    sl_extra_buffer_pct=self.sl_extra_buffer_pct,
                    atr_value=atr_value,
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self.min_rr,
                    max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
                    leverage=leverage,
                    max_margin_loss_pct=self._resolve_max_margin_loss_pct(symbol=symbol, side=side, atr_pct=atr_pct, btc_guard=btc_guard),
                )

                risk_pct = calc_estimated_margin_ratio_pct(
                    leverage=leverage,
                    maint_margin_rate=self.maint_margin_rate,
                )
                if risk_pct > self._resolve_symbol_max_risk_pct(symbol, leverage):
                    continue

                quantity = calc_quantity_from_order_usdt(
                    entry_price=fill_price,
                    order_usdt=self.order_usdt,
                    fallback_quantity=self.quantity,
                )
                margin_usdt = self.margin_usdt
                if margin_usdt <= 0:
                    margin_usdt = calc_margin_usdt(entry_price=fill_price, quantity=quantity, leverage=leverage)
                feature_snapshot = await asyncio.to_thread(self._capture_feature_snapshot, symbol, side)
                btc_following = self._resolve_btc_following_flag(symbol)

                trade_id = self.repo.create_open_trade(
                    {
                        "symbol": symbol,
                        "side": side,
                        "btc_following": btc_following,
                        "entry_type": candles_entry_type,
                        "signal_win_probability": raw_prob,
                        "effective_win_probability": effective_prob,
                        "entry_price": fill_price,
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
                    entry_price=fill_price,
                    quantity=quantity,
                )
                self._cache_open_trade_row(
                    open_trades_by_symbol=candles_open_trades_by_symbol,
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry_price=fill_price,
                    quantity=quantity,
                )
                self._register_open_pressure_event(side=side)
                opened_candles_orders += 1
                self._maybe_schedule_open_trade_discord_alert(
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry_type=candles_entry_type,
                    btc_following=btc_following,
                    entry_price=fill_price,
                    take_profit=normalized_tp,
                    stop_loss=normalized_sl,
                    leverage=leverage,
                    margin_usdt=margin_usdt,
                    raw_prob=raw_prob,
                    effective_prob=effective_prob,
                    opened_at=datetime.now(timezone(timedelta(hours=7))),
                )

        # 1b) Separate liquidation+EMA99 model on top volatility symbols.
        if (not open_paused) and (not entry_hard_blocked) and self.liquid_enabled and self.liquid_predictor is not None:
            opened_liquid_orders = 0
            for symbol in top_vol_symbols:
                if self.liquid_max_orders_per_cycle > 0 and opened_liquid_orders >= self.liquid_max_orders_per_cycle:
                    break
                market_price = stream_prices.get(symbol)
                if market_price is None and self.entry_require_fresh_stream_price:
                    continue
                if market_price is None:
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
                if self._has_conflicting_open_trade(symbol=symbol, side=side, entry_type="LIQ_EMA99"):
                    continue
                if self._is_reentry_cooldown_active(symbol=symbol, side=side, entry_type="LIQ_EMA99"):
                    continue
                if not self._pass_instant_sl_guard(symbol=symbol, side=side):
                    continue
                if self._portfolio_guard_reason(side=side, open_trades_by_symbol=open_trades_by_symbol):
                    continue

                entry = self._apply_symbol_shock_entry_offset(
                    symbol=symbol,
                    side=side,
                    entry=float(liq_signal.predicted_entry_price),
                )
                entry = self._apply_pattern_entry_offset(symbol=symbol, side=side, entry=entry)
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
                effective_prob = self._apply_hourly_profile_to_probability(
                    effective_prob=effective_prob,
                    side=side,
                    entry_type="LIQ_EMA99",
                    btc_guard=btc_guard,
                )
                can_open_now, required_min_win = self._evaluate_hourly_bad_window_guard(
                    side=side,
                    entry_type="LIQ_EMA99",
                    base_min_win=self.min_win_probability,
                    btc_guard=btc_guard,
                )
                if not can_open_now:
                    continue
                required_min_win = self._apply_bullish_short_nonfollow_min_win_bonus(
                    required_min_win=required_min_win,
                    side=side,
                    symbol=symbol,
                    btc_guard=btc_guard,
                )
                if effective_prob < required_min_win:
                    continue
                if not self._pass_short_sl_streak_guard(side=side):
                    continue
                if not self._pass_bullish_short_nonfollow_ratio_guard(
                    side=side,
                    symbol=symbol,
                    btc_guard=btc_guard,
                    open_trades_by_symbol=open_trades_by_symbol,
                ):
                    continue
                if not self._pass_btc_filter(symbol=symbol, side=side, effective_prob=effective_prob, btc_guard=btc_guard):
                    continue
                fill_price = float(market_price)
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=fill_price,
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                atr_value = await self._resolve_symbol_atr(symbol)
                atr_for_pct = float(atr_value) if atr_value is not None else 0.0
                atr_pct = (atr_for_pct / fill_price) * 100 if fill_price > 0 else 0.0
                leverage = self._resolve_symbol_leverage(symbol, atr_pct, side)
                normalized_tp, normalized_sl = normalize_tp_sl(
                    side=side,
                    entry_price=fill_price,
                    take_profit=tp,
                    stop_loss=sl,
                    min_sl_pct=max(
                        self.min_sl_pct,
                        calc_min_sl_pct_from_loss(min_sl_loss_pct=self.min_sl_loss_pct),
                    ),
                    sl_extra_buffer_pct=self.sl_extra_buffer_pct,
                    atr_value=atr_value,
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self.min_rr,
                    max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
                    leverage=leverage,
                    max_margin_loss_pct=self._resolve_max_margin_loss_pct(symbol=symbol, side=side, atr_pct=atr_pct, btc_guard=btc_guard),
                )
                
                risk_pct = calc_estimated_margin_ratio_pct(
                    leverage=leverage,
                    maint_margin_rate=self.maint_margin_rate,
                )
                if risk_pct > self._resolve_symbol_max_risk_pct(symbol, leverage):
                    continue

                quantity = calc_quantity_from_order_usdt(
                    entry_price=fill_price,
                    order_usdt=self.order_usdt,
                    fallback_quantity=self.quantity,
                )
                margin_usdt = self.margin_usdt
                if margin_usdt <= 0:
                    margin_usdt = calc_margin_usdt(entry_price=fill_price, quantity=quantity, leverage=leverage)
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
                        "entry_price": fill_price,
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
                    entry_price=fill_price,
                    quantity=quantity,
                )
                self._register_open_pressure_event(side=side)
                opened_liquid_orders += 1

        await self._apply_open_pressure_profit_exit(
            closed_trade_ids=closed_trade_ids,
            market_prices=market_prices,
        )

        # 2) Manage open trades: close on TP, otherwise apply timeout policy.
        for trade in open_trades:
            try:
                trade_id = int(trade.get("id") or 0)
                if trade_id in closed_trade_ids:
                    continue
                symbol = str(trade["symbol"])
                side = str(trade["side"])
                entry_type = str(trade.get("entry_type") or "LIMIT")
                skip_btc_guards = self._skip_btc_guards_for_entry_type(entry_type)
                allow_btc_reversal_profit_exit = (not skip_btc_guards) or self._is_liquidation_style_entry_type(entry_type)
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
                self._maybe_schedule_discord_loss_alert(
                    trade=trade,
                    market_price=price,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                )
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
                if self._is_ema99_bounce_entry_type(entry_type):
                    if self._handle_ema99_bounce_trade_exit(
                        trade=trade,
                        side=side,
                        price=float(price),
                        entry=entry,
                        tp=tp,
                        sl=sl,
                        qty=qty,
                        pnl=pnl,
                    ):
                        continue
                    continue
                if self._is_liquidation_style_entry_type(entry_type):
                    move_sl_trigger_pct = self._resolve_pump_hunter_move_sl_trigger_pnl_pct()
                    if not self.disable_sl and pnl_pct >= move_sl_trigger_pct:
                        locked_sl = entry
                        if (side == "LONG" and locked_sl > sl) or (side == "SHORT" and locked_sl < sl):
                            self.repo.update_stop_loss(trade_id=int(trade["id"]), stop_loss=locked_sl)
                            sl = locked_sl
                else:
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

                # Close losing counter-trend positions on BTC 1H reversal (day-toggle capable).
                if not skip_btc_guards:
                    if self._should_force_close_loss_on_btc_reversal(
                        entry_type=entry_type,
                        side=side,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        reversal_tf_label = str(self.btc_filter_timeframe or "15m").upper()
                        self._close_trade_with_context(
                            trade,
                            close_price=price,
                            pnl=net_pnl,
                            result=0,
                            close_reason=f"BTC_{reversal_tf_label}_REVERSAL_LOSS_EXIT",
                            commission_usdt=commission,
                        )
                        continue

                # Close profitable counter-trend positions when BTC trend reversal is detected.
                if allow_btc_reversal_profit_exit:
                    if self._should_force_close_profit_on_btc_reversal(
                        symbol=symbol,
                        side=side,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        reversal_tf_label = str(self.btc_filter_timeframe or "15m").upper()
                        reversal_reason = (
                            f"BTC_{reversal_tf_label}_REVERSAL_PROFIT_EXIT"
                            if self._is_countertrend_on_btc_1h_reversal(side=side, btc_guard=btc_guard)
                            else "BTC_REVERSAL_PROFIT_EXIT"
                        )
                        self._close_trade_with_context(
                            trade,
                            close_price=price,
                            pnl=net_pnl,
                            result=1,
                            close_reason=reversal_reason,
                            commission_usdt=commission,
                        )
                        continue

                # Take profit on SHORT when BTC rebounds sharply from local low.
                if not skip_btc_guards:
                    if self._should_close_profit_on_btc_short_rebound(
                        side=side,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        self._close_trade_with_context(
                            trade,
                            close_price=price,
                            pnl=net_pnl,
                            result=1,
                            close_reason="BTC_SHORT_REBOUND_PROFIT_EXIT",
                            commission_usdt=commission,
                        )
                        continue

                # Take profit on SHORT when BTC stalls/compresses near local low after a dump.
                if not skip_btc_guards:
                    if self._should_close_profit_on_btc_short_stall(
                        side=side,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        self._close_trade_with_context(
                            trade,
                            close_price=price,
                            pnl=net_pnl,
                            result=1,
                            close_reason="BTC_SHORT_STALL_PROFIT_EXIT",
                            commission_usdt=commission,
                        )
                        continue

                # Take profit on LONG when BTC pulls back sharply from local high.
                if not skip_btc_guards:
                    if self._should_close_profit_on_btc_long_pullback(
                        side=side,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        self._close_trade_with_context(
                            trade,
                            close_price=price,
                            pnl=net_pnl,
                            result=1,
                            close_reason="BTC_LONG_PULLBACK_PROFIT_EXIT",
                            commission_usdt=commission,
                        )
                        continue

                # Take profit on LONG when BTC fades near local top after a strong rebound.
                if not skip_btc_guards:
                    if self._should_close_profit_on_btc_long_top_fade(
                        side=side,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        self._close_trade_with_context(
                            trade,
                            close_price=price,
                            pnl=net_pnl,
                            result=1,
                            close_reason="BTC_LONG_TOP_FADE_PROFIT_EXIT",
                            commission_usdt=commission,
                        )
                        continue

                # Lock profit when BTC trend flips against this position for BTC-following symbols only.
                if not skip_btc_guards:
                    if self._should_close_profit_on_btc_trend(
                        symbol=symbol,
                        side=side,
                        pnl=pnl,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        self._close_trade_with_context(
                            trade,
                            close_price=price,
                            pnl=net_pnl,
                            result=1,
                            close_reason="BTC_TREND_PROFIT_LOCK",
                            commission_usdt=commission,
                        )
                        continue

                # Close any counter-trend open trade when BTC filter has high confidence.
                if not skip_btc_guards:
                    if self._should_force_close_countertrend_on_btc_filter(
                        symbol=symbol,
                        side=side,
                        pnl=pnl,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        self._close_trade_with_context(
                            trade,
                            close_price=price,
                            pnl=net_pnl,
                            result=1,
                            close_reason="BTC_TREND_COUNTER_EXIT",
                            commission_usdt=commission,
                        )
                        continue

                # SL has higher priority than TP per user requirement.
                sl_hit = False
                if not self.disable_sl:
                    sl_hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
                if sl_hit:
                    close_reason = 1 if pnl >= 0 else 0
                    commission = self._calc_fee(entry=entry, quantity=qty, entry_type=str(trade.get("entry_type") or "LIMIT"), fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                    net_pnl = pnl - commission
                    self._register_instant_sl_event(
                        symbol=symbol,
                        side=side,
                        opened_at=trade.get("opened_at"),
                        pnl_pct=pnl_pct,
                        mae_pct=next_mae,
                    )
                    self._close_trade_with_context(
                        trade,
                        close_price=price,
                        pnl=net_pnl,
                        result=close_reason,
                        close_reason="SL",
                        commission_usdt=commission,
                    )
                    continue

                # Immediate TP exit.
                tp_hit = (side == "LONG" and price >= tp) or (side == "SHORT" and price <= tp)
                if tp_hit:
                    close_reason = 1 if pnl >= 0 else 0
                    commission = self._calc_fee(entry=entry, quantity=qty, entry_type=str(trade.get("entry_type") or "LIMIT"), fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                    net_pnl = pnl - commission
                    self._close_trade_with_context(
                        trade,
                        close_price=price,
                        pnl=net_pnl,
                        result=close_reason,
                        close_reason="TP",
                        commission_usdt=commission,
                    )
                    continue

                # Timeout policy.
                if not self._is_expired(trade.get("opened_at")):
                    continue

                if pnl > 0:
                    continue

                # TIMEOUT_BREAKEVEN is temporarily disabled.
                continue
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

    def _register_open_pressure_event(self, *, side: str) -> None:
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return
        now_ts = time.time()
        self._recent_open_pressure_events.append((now_ts, side_key))
        self._prune_open_pressure_events(now_ts=now_ts)

    def _prune_open_pressure_events(self, *, now_ts: float | None = None) -> None:
        if not self._recent_open_pressure_events:
            return
        current_ts = float(now_ts if now_ts is not None else time.time())
        min_ts = current_ts - (self.open_pressure_close_window_minutes * 60.0)
        self._recent_open_pressure_events = [
            (event_ts, side_key)
            for event_ts, side_key in self._recent_open_pressure_events
            if event_ts >= min_ts and side_key in {"LONG", "SHORT"}
        ]

    def _resolve_open_pressure_side(self) -> str | None:
        if not self.open_pressure_close_enabled:
            return None
        self._prune_open_pressure_events()
        if not self._recent_open_pressure_events:
            return None

        counts = {"LONG": 0, "SHORT": 0}
        for _, side_key in self._recent_open_pressure_events:
            counts[side_key] += 1

        long_count = int(counts["LONG"])
        short_count = int(counts["SHORT"])
        lead = abs(long_count - short_count)
        if lead < self.open_pressure_close_min_lead:
            return None
        if long_count >= self.open_pressure_close_min_opens and long_count > short_count:
            return "LONG"
        if short_count >= self.open_pressure_close_min_opens and short_count > long_count:
            return "SHORT"
        return None

    async def _apply_open_pressure_profit_exit(
        self,
        *,
        closed_trade_ids: set[int] | None = None,
        market_prices: dict[str, float] | None = None,
    ) -> int:
        pressure_side = self._resolve_open_pressure_side()
        if pressure_side not in {"LONG", "SHORT"}:
            return 0

        target_close_side = "SHORT" if pressure_side == "LONG" else "LONG"
        closed_ids = closed_trade_ids if closed_trade_ids is not None else set()
        prices = market_prices if market_prices is not None else {}
        open_rows = self.repo.list_open_trades()
        close_candidates: list[tuple[int, float, float, float]] = []

        for row in open_rows:
            try:
                trade_id = int(row.get("id") or 0)
                side = str(row.get("side") or "").upper()
                status = str(row.get("status") or "OPEN").upper()
                symbol = str(row.get("symbol") or "")
                entry = float(row.get("entry_price") or 0.0)
                qty = float(row.get("quantity") or 0.0)
                entry_type = str(row.get("entry_type") or "LIMIT")
            except Exception:
                continue
            if trade_id <= 0 or trade_id in closed_ids:
                continue
            if status != "OPEN" or side != target_close_side or entry <= 0 or qty <= 0 or not symbol:
                continue
            if self._is_liquidation_style_entry_type(entry_type) or self._is_ema99_bounce_entry_type(entry_type):
                continue

            price = prices.get(symbol)
            if price is None:
                stream_price = await self._resolve_stream_price(symbol)
                price = stream_price if stream_price is not None else await asyncio.to_thread(self._resolve_market_price, symbol)
                if price is None:
                    continue
                prices[symbol] = float(price)

            pnl = self._calc_pnl(side=side, entry=entry, close_price=float(price), quantity=qty)
            commission = self._calc_fee(
                entry=entry,
                quantity=qty,
                entry_type=entry_type,
                fee_taker=self.fee_taker_pct,
                fee_maker=self.fee_maker_pct,
            )
            net_pnl = pnl - commission
            if net_pnl <= self.open_pressure_close_min_net_pnl_usdt:
                continue
            close_candidates.append((row, float(price), float(net_pnl), float(commission)))

        for trade_row, close_price, net_pnl, commission in close_candidates:
            self._close_trade_with_context(
                trade_row,
                close_price=close_price,
                pnl=net_pnl,
                result=1,
                close_reason=f"{pressure_side}_OPEN_PRESSURE_EXIT",
                commission_usdt=commission,
            )
            closed_ids.add(int(trade_row.get("id") or 0))
        return len(close_candidates)

    def register_open_pressure_event(self, *, side: str) -> None:
        self._register_open_pressure_event(side=side)

    async def apply_open_pressure_profit_exit(self) -> int:
        return await self._apply_open_pressure_profit_exit()

    async def close_profitable_pump_trades_on_btc_signal(
        self,
        *,
        signal_side: str,
        exclude_trade_id: int | None = None,
        close_reason: str | None = None,
    ) -> int:
        normalized_signal_side = str(signal_side or "").upper()
        if normalized_signal_side not in {"LONG", "SHORT"}:
            return 0

        target_close_side = "SHORT" if normalized_signal_side == "LONG" else "LONG"
        open_rows = self.repo.list_open_trades()
        candidate_rows: list[dict[str, Any]] = []
        symbols: list[str] = []

        for row in open_rows:
            try:
                trade_id = int(row.get("id") or 0)
                side = str(row.get("side") or "").upper()
                status = str(row.get("status") or "OPEN").upper()
                symbol = str(row.get("symbol") or "")
                entry = float(row.get("entry_price") or 0.0)
                qty = float(row.get("quantity") or 0.0)
                entry_type = str(row.get("entry_type") or "LIMIT")
            except Exception:
                continue
            if trade_id <= 0 or (exclude_trade_id is not None and trade_id == exclude_trade_id):
                continue
            if status != "OPEN" or side != target_close_side or entry <= 0 or qty <= 0 or not symbol:
                continue
            if not self._is_liquidation_style_entry_type(entry_type):
                continue
            candidate_rows.append(row)
            symbols.append(symbol)

        prices = await self._resolve_stream_prices(symbols)
        missing_symbols = [symbol for symbol in symbols if symbol and symbol not in prices]
        if missing_symbols:
            prices.update(self._resolve_market_prices(missing_symbols))

        close_candidates: list[tuple[dict[str, Any], float, float, float]] = []
        for row in candidate_rows:
            try:
                side = str(row.get("side") or "").upper()
                symbol = str(row.get("symbol") or "")
                entry = float(row.get("entry_price") or 0.0)
                qty = float(row.get("quantity") or 0.0)
                leverage = max(1, int(row.get("leverage") or 1))
                entry_type = str(row.get("entry_type") or "LIMIT")
            except Exception:
                continue

            price = prices.get(symbol)
            if price is None:
                stream_price = await self._resolve_stream_price(symbol)
                price = stream_price if stream_price is not None else self._resolve_market_price(symbol)
                if price is None:
                    continue
                prices[symbol] = float(price)

            pnl = self._calc_pnl(side=side, entry=entry, close_price=float(price), quantity=qty)
            pnl_pct = self._calc_pnl_pct(side=side, entry=entry, mark_price=float(price), leverage=leverage)
            commission = self._calc_fee(
                entry=entry,
                quantity=qty,
                entry_type=entry_type,
                fee_taker=self.fee_taker_pct,
                fee_maker=self.fee_maker_pct,
            )
            net_pnl = pnl - commission
            if pnl <= 0 or net_pnl <= 0 or pnl_pct <= 0:
                continue
            close_candidates.append((row, float(price), float(net_pnl), float(commission)))

        reason = close_reason or f"BTC_{normalized_signal_side}_SIGNAL_PROFIT_EXIT"
        for trade_row, close_price, net_pnl, commission in close_candidates:
            self._close_trade_with_context(
                trade_row,
                close_price=close_price,
                pnl=net_pnl,
                result=1,
                close_reason=reason,
                commission_usdt=commission,
            )
        return len(close_candidates)

    @staticmethod
    def _parse_dt(value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None

    def _has_conflicting_open_trade(
        self,
        *,
        symbol: str,
        side: str,
        entry_type: str,
        force_entry_type_scope: bool = False,
    ) -> bool:
        if self.single_position_per_symbol_side and not force_entry_type_scope:
            return self.repo.has_open_trade(symbol=symbol, side=side)
        return self.repo.has_open_trade(symbol=symbol, side=side, entry_type=entry_type)

    def _is_reentry_cooldown_active(
        self,
        *,
        symbol: str,
        side: str,
        entry_type: str,
        force_entry_type_scope: bool = False,
    ) -> bool:
        if (
            self.reentry_cooldown_minutes <= 0
            and self.reentry_after_sl_cooldown_minutes <= 0
            and (not self.instant_sl_guard_enabled)
        ):
            return False

        latest = self.repo.latest_trade(
            symbol=symbol,
            side=side,
            entry_type=None if (self.single_position_per_symbol_side and not force_entry_type_scope) else entry_type,
        )
        if not latest:
            return False

        if str(latest.get("status") or "").upper() == "OPEN":
            return True

        last_update = self._parse_dt(latest.get("updated_at"))
        if last_update is None:
            last_update = self._parse_dt(latest.get("closed_at"))
        if last_update is None:
            return False

        close_reason = str(latest.get("close_reason") or "").upper()
        cooldown_minutes = self.reentry_cooldown_minutes
        if close_reason in {"SL", "MANUAL_FORCE_LOSS"}:
            cooldown_minutes = max(cooldown_minutes, self.reentry_after_sl_cooldown_minutes)
            if self._is_severe_instant_sl_row(latest):
                cooldown_minutes = max(cooldown_minutes, self.instant_sl_guard_cooldown_minutes)
        if cooldown_minutes <= 0:
            return False

        now = datetime.now(self._vn_tz).replace(tzinfo=None)
        elapsed = (now - last_update).total_seconds()
        return elapsed < float(cooldown_minutes * 60)

    def _elapsed_seconds_since(self, value: object) -> float | None:
        dt = self._parse_dt(value)
        if dt is None:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(self._vn_tz).replace(tzinfo=None)
        now = datetime.now(self._vn_tz).replace(tzinfo=None)
        return max(0.0, (now - dt).total_seconds())

    def _estimate_margin_base_from_trade_row(self, row: dict[str, Any]) -> float:
        try:
            margin = float(row.get("margin_usdt") or 0.0)
        except Exception:
            margin = 0.0
        if margin > 0:
            return margin
        try:
            entry = float(row.get("entry_price") or 0.0)
            qty = float(row.get("quantity") or 0.0)
            lev = float(row.get("leverage") or 0.0)
            if entry > 0 and qty > 0 and lev > 0:
                return (entry * qty) / lev
        except Exception:
            pass
        return 0.0

    def _estimate_trade_pnl_pct_from_row(self, row: dict[str, Any]) -> float:
        try:
            pnl = float(row.get("pnl") or 0.0)
        except Exception:
            pnl = 0.0
        margin = self._estimate_margin_base_from_trade_row(row)
        if margin <= 0:
            return 0.0
        return (pnl / margin) * 100.0

    def _is_severe_instant_sl_row(self, row: dict[str, Any]) -> bool:
        if not self.instant_sl_guard_enabled:
            return False
        opened_at = row.get("opened_at")
        closed_at = row.get("closed_at")
        if closed_at is None:
            closed_at = row.get("updated_at")
        hold_sec = None
        if opened_at is not None and closed_at is not None:
            opened_dt = self._parse_dt(opened_at)
            closed_dt = self._parse_dt(closed_at)
            if opened_dt is not None and closed_dt is not None:
                if opened_dt.tzinfo is not None:
                    opened_dt = opened_dt.astimezone(self._vn_tz).replace(tzinfo=None)
                if closed_dt.tzinfo is not None:
                    closed_dt = closed_dt.astimezone(self._vn_tz).replace(tzinfo=None)
                hold_sec = max(0.0, (closed_dt - opened_dt).total_seconds())
        if hold_sec is None:
            hold_sec = self._elapsed_seconds_since(opened_at)
        if hold_sec is None:
            return False
        if hold_sec > float(self.instant_sl_guard_max_hold_minutes * 60):
            return False

        pnl_pct = self._estimate_trade_pnl_pct_from_row(row)
        try:
            mae_pct = float(row.get("mae_pct") or 0.0)
        except Exception:
            mae_pct = 0.0
        severe_loss = abs(min(0.0, pnl_pct)) >= self.instant_sl_guard_min_abs_pnl_pct
        severe_mae = abs(min(0.0, mae_pct)) >= self.instant_sl_guard_min_abs_mae_pct
        return severe_loss or severe_mae

    def _instant_sl_guard_key(self, symbol: str, side: str) -> str:
        return f"{self._normalize_symbol_key(symbol)}|{str(side or '').upper()}"

    def _recent_symbol_sl_guard_reason(self, *, symbol: str, side: str) -> str | None:
        if self.symbol_sl_block_minutes <= 0:
            return None
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return None
        try:
            rows = self.repo.list_recent_closed_trades_for_symbol(symbol=symbol, limit=6)
        except Exception:
            return None
        latest_same_side = next(
            (row for row in rows if str(row.get("side") or "").upper() == side_key),
            None,
        )
        if latest_same_side is None:
            return None
        close_reason = str(latest_same_side.get("close_reason") or "").upper()
        if not self._is_sl_close_reason(close_reason):
            return None
        closed_at = latest_same_side.get("closed_at") or latest_same_side.get("updated_at")
        elapsed = self._elapsed_seconds_since(closed_at)
        if elapsed is None:
            return None
        remain_seconds = float(self.symbol_sl_block_minutes * 60) - elapsed
        if remain_seconds <= 0:
            return None
        remain_minutes = max(1, int(math.ceil(remain_seconds / 60.0)))
        return f"Recent SL block {side_key} ({remain_minutes}m left)"

    def _instant_sl_guard_reason(self, *, symbol: str, side: str) -> str | None:
        if not self.instant_sl_guard_enabled:
            return None
        side_key = str(side or "").upper()
        if side_key == "LONG":
            if self._is_short_top_test_rejection(symbol=symbol):
                return "Long blocked after top sweep rejection"
            if self._is_long_pump_red_confirmation(symbol=symbol):
                return "Long blocked after pump + red confirm"
        key = self._instant_sl_guard_key(symbol, side)
        lock_until_ts = float(self._instant_sl_symbol_side_lock_until_ts.get(key) or 0.0)
        if lock_until_ts <= 0:
            try:
                latest = self.repo.latest_trade(
                    symbol=symbol,
                    side=side,
                    entry_type=None,
                )
            except Exception:
                latest = None
            if latest and str(latest.get("status") or "").upper() != "OPEN":
                close_reason = str(latest.get("close_reason") or "").upper()
                if self._is_sl_close_reason(close_reason) and self._is_severe_instant_sl_row(latest):
                    latest_update = self._parse_dt(latest.get("updated_at")) or self._parse_dt(latest.get("closed_at"))
                    if latest_update is not None:
                        if latest_update.tzinfo is not None:
                            latest_update = latest_update.astimezone(self._vn_tz).replace(tzinfo=None)
                        now_naive = datetime.now(self._vn_tz).replace(tzinfo=None)
                        elapsed = max(0.0, (now_naive - latest_update).total_seconds())
                        remain_seconds = float(self.instant_sl_guard_cooldown_minutes * 60) - elapsed
                        if remain_seconds > 0:
                            lock_until_ts = time.time() + remain_seconds
                            self._instant_sl_symbol_side_lock_until_ts[key] = lock_until_ts
        if lock_until_ts <= 0:
            return None
        now_ts = time.time()
        if now_ts >= lock_until_ts:
            self._instant_sl_symbol_side_lock_until_ts.pop(key, None)
            return None
        if side_key == "SHORT" and self._is_short_top_test_rejection(symbol=symbol):
            return None
        remain_minutes = max(1, int(math.ceil((lock_until_ts - now_ts) / 60.0)))
        return f"Instant-SL guard {side_key} ({remain_minutes}m left)"

    def _entry_pattern_guard_reason(self, *, symbol: str, side: str) -> str | None:
        side_key = str(side or "").upper()
        strong_btc_shock_reason = self._symbol_shock_strong_block_reason(symbol=symbol, side=side)
        if strong_btc_shock_reason:
            return strong_btc_shock_reason
        symbol_shock_reason = self._symbol_shock_pause_reason(symbol=symbol)
        if symbol_shock_reason:
            return symbol_shock_reason
        if side_key == "SHORT" and self._is_strong_bull_waiting_short_confirmation(symbol=symbol):
            return "Short waits for confirmation after STRONG_BULL"
        if side_key == "LONG" and self._is_strong_bull_waiting_long_confirmation(symbol=symbol):
            return "Long waits for confirmation after STRONG_BULL"
        if side_key == "SHORT" and self._is_short_inside_bar_waiting_breakdown(symbol=symbol):
            return "Short waits for inside-bar breakdown close"
        if side_key == "LONG" and self._is_long_inside_bar_waiting_breakout(symbol=symbol):
            return "Long waits for inside-bar breakout close"
        return None

    def _entry_guard_reason(self, *, symbol: str, side: str) -> str | None:
        recent_sl_reason = self._recent_symbol_sl_guard_reason(symbol=symbol, side=side)
        if recent_sl_reason:
            return recent_sl_reason
        instant_reason = self._instant_sl_guard_reason(symbol=symbol, side=side)
        if instant_reason:
            return instant_reason
        return self._entry_pattern_guard_reason(symbol=symbol, side=side)

    def _pass_instant_sl_guard(self, *, symbol: str, side: str) -> bool:
        return self._entry_guard_reason(symbol=symbol, side=side) is None

    def _is_short_top_test_rejection(self, *, symbol: str) -> bool:
        if not self.instant_sl_guard_short_top_test_bypass_enabled:
            return False

        key = self._normalize_symbol_key(symbol)
        now_ts = time.time()
        cached = self._short_top_test_rejection_cache.get(key)
        if cached is not None:
            cached_ts, cached_value = cached
            if (now_ts - cached_ts) <= self.instant_sl_guard_short_top_test_cache_sec:
                return bool(cached_value)

        matched = False
        try:
            # Use the most recently closed 5m candle to avoid intrabar noise.
            limit = max(self.instant_sl_guard_short_top_test_lookback_candles + 3, 18)
            rows = self.market_client.fetch_ohlcv(symbol=symbol, timeframe="5m", limit=limit)
            if rows and len(rows) >= 4:
                closed_idx = -2 if len(rows) >= 2 else -1
                candle = rows[closed_idx]
                prev_rows = rows[:closed_idx]
                if len(prev_rows) >= 3:
                    lookback = self.instant_sl_guard_short_top_test_lookback_candles
                    prev_scope = prev_rows[-lookback:] if len(prev_rows) > lookback else prev_rows
                    recent_high = max(float(r[2]) for r in prev_scope if len(r) >= 3)
                    prev_candle = prev_rows[-1]

                    open_price = float(candle[1])
                    high_price = float(candle[2])
                    low_price = float(candle[3])
                    close_price = float(candle[4])
                    if recent_high > 0 and high_price > 0:
                        top_tolerance = self.instant_sl_guard_short_top_test_tolerance_pct
                        top_tested = high_price >= (recent_high * (1.0 - top_tolerance))
                        close_back_below_top = close_price <= recent_high
                        bearish_close = close_price <= open_price

                        candle_range = max(1e-12, high_price - low_price)
                        upper_wick = max(0.0, high_price - max(open_price, close_price))
                        body_size = abs(close_price - open_price)
                        upper_wick_ratio = upper_wick / candle_range
                        close_position_in_range = (close_price - low_price) / candle_range
                        wick_range_ok = upper_wick_ratio >= self.instant_sl_guard_short_rejection_min_upper_wick_ratio
                        wick_body_ok = upper_wick >= (
                            body_size * self.instant_sl_guard_short_rejection_min_wick_body_ratio
                        )
                        weak_close = bearish_close or close_position_in_range <= 0.45
                        shooting_star_like = bool(
                            close_back_below_top
                            and close_position_in_range <= 0.5
                            and wick_range_ok
                            and upper_wick >= max(body_size * 1.8, candle_range * 0.3)
                        )
                        try:
                            candle_pattern = CandlePatternAnalyzer._classify_pattern(prev_candle, candle)
                        except Exception:
                            candle_pattern = ""
                        matched = bool(
                            top_tested
                            and close_back_below_top
                            and (
                                candle_pattern == "SHOOTING_STAR"
                                or shooting_star_like
                                or (weak_close and wick_range_ok and wick_body_ok)
                            )
                        )
        except Exception:
            matched = False

        self._short_top_test_rejection_cache[key] = (now_ts, bool(matched))
        return bool(matched)

    def _is_long_pump_red_confirmation(self, *, symbol: str) -> bool:
        if not self.entry_long_pump_red_block_enabled:
            return False

        key = self._normalize_symbol_key(symbol)
        now_ts = time.time()
        cached = self._long_pump_red_confirm_cache.get(key)
        if cached is not None:
            cached_ts, cached_value = cached
            if (now_ts - cached_ts) <= self.instant_sl_guard_short_top_test_cache_sec:
                return bool(cached_value)

        matched = False
        try:
            limit = max(self.entry_long_pump_red_lookback_candles + 5, 18)
            rows = self.market_client.fetch_ohlcv(symbol=symbol, timeframe="5m", limit=limit)
            if rows and len(rows) >= 5:
                confirm_candle = rows[-2]
                pump_candle = rows[-3]
                prior_rows = rows[:-3]
                if len(prior_rows) >= 3:
                    lookback = self.entry_long_pump_red_lookback_candles
                    prior_scope = prior_rows[-lookback:] if len(prior_rows) > lookback else prior_rows
                    recent_high = max(float(r[2]) for r in prior_scope if len(r) >= 3)

                    pump_open = float(pump_candle[1])
                    pump_high = float(pump_candle[2])
                    pump_close = float(pump_candle[4])
                    confirm_open = float(confirm_candle[1])
                    confirm_close = float(confirm_candle[4])

                    pump_body_pct = ((pump_close - pump_open) / pump_open) * 100.0 if pump_open > 0 else 0.0
                    confirm_body_pct = ((confirm_open - confirm_close) / confirm_open) * 100.0 if confirm_open > 0 else 0.0
                    pump_bullish = pump_close > pump_open
                    confirm_bearish = confirm_close < confirm_open
                    top_tolerance = self.entry_long_pump_red_top_tolerance_pct
                    pump_near_top = (
                        recent_high > 0
                        and (
                            pump_high >= (recent_high * (1.0 - top_tolerance))
                            or pump_close >= (recent_high * (1.0 - top_tolerance))
                        )
                    )
                    matched = bool(
                        pump_bullish
                        and confirm_bearish
                        and pump_near_top
                        and pump_body_pct >= self.entry_long_pump_red_pump_min_body_pct
                        and confirm_body_pct >= self.entry_long_pump_red_confirm_min_body_pct
                        and confirm_close < pump_close
                    )
        except Exception:
            matched = False

        self._long_pump_red_confirm_cache[key] = (now_ts, bool(matched))
        return bool(matched)

    def _symbol_shock_context(self, *, symbol: str) -> dict[str, Any] | None:
        if not self.entry_symbol_shock_pause_enabled:
            return None

        source_symbol = symbol
        source_label = "symbol"
        normalized_key = self._normalize_symbol_key(symbol)
        btc_key = self._normalize_symbol_key(self.entry_symbol_shock_pause_btc_symbol)
        if self.entry_symbol_shock_pause_use_btc_for_alts and normalized_key != btc_key:
            source_symbol = self.entry_symbol_shock_pause_btc_symbol
            source_label = "BTC"

        key = self._normalize_symbol_key(source_symbol)
        now_ts = time.time()
        cached = self._symbol_shock_pause_cache.get(key)
        if cached is not None:
            cached_ts, cached_context = cached
            if (now_ts - cached_ts) <= self.instant_sl_guard_short_top_test_cache_sec:
                return cached_context

        context: dict[str, Any] | None = None
        try:
            lookback = self.entry_symbol_shock_pause_lookback_candles
            cooldown = self.entry_symbol_shock_pause_cooldown_candles
            limit = max(lookback + cooldown + 8, 24)
            rows = self.market_client.fetch_ohlcv(symbol=source_symbol, timeframe="5m", limit=limit)
            closed_rows = [row for row in rows[:-1] if len(row) >= 6] if rows and len(rows) >= 3 else []
            if len(closed_rows) >= (lookback + 2):
                min_idx = max(lookback, len(closed_rows) - cooldown)
                for idx in range(len(closed_rows) - 1, min_idx - 1, -1):
                    candle = closed_rows[idx]
                    history = closed_rows[max(0, idx - lookback) : idx]
                    if len(history) < max(4, lookback // 2):
                        continue

                    open_price = float(candle[1])
                    high_price = float(candle[2])
                    low_price = float(candle[3])
                    close_price = float(candle[4])
                    volume = float(candle[5])
                    if open_price <= 0:
                        continue

                    candle_range = max(0.0, high_price - low_price)
                    body_size = abs(close_price - open_price)
                    upper_wick = max(0.0, high_price - max(open_price, close_price))
                    lower_wick = max(0.0, min(open_price, close_price) - low_price)
                    range_pct = (candle_range / open_price) * 100.0
                    body_pct = (body_size / open_price) * 100.0
                    max_wick_ratio = max(upper_wick, lower_wick) / candle_range if candle_range > 1e-12 else 0.0

                    history_ranges = []
                    history_volumes = []
                    for row in history:
                        hist_open = float(row[1])
                        hist_high = float(row[2])
                        hist_low = float(row[3])
                        hist_volume = float(row[5])
                        if hist_open > 0:
                            history_ranges.append(((hist_high - hist_low) / hist_open) * 100.0)
                        if hist_volume > 0:
                            history_volumes.append(hist_volume)
                    if not history_ranges:
                        continue

                    avg_range_pct = sum(history_ranges) / float(len(history_ranges))
                    avg_volume = sum(history_volumes) / float(len(history_volumes)) if history_volumes else 0.0
                    volume_ratio = (volume / avg_volume) if avg_volume > 1e-12 else 0.0
                    range_vs_avg = (range_pct / avg_range_pct) if avg_range_pct > 1e-12 else 0.0

                    is_shock_candle = bool(
                        range_pct >= self.entry_symbol_shock_pause_min_range_pct
                        and range_vs_avg >= self.entry_symbol_shock_pause_min_range_vs_avg
                        and volume_ratio >= self.entry_symbol_shock_pause_min_volume_ratio
                        and (
                            body_pct >= self.entry_symbol_shock_pause_min_body_pct
                            or max_wick_ratio >= self.entry_symbol_shock_pause_min_wick_ratio
                        )
                    )
                    if not is_shock_candle:
                        continue

                    bars_after = (len(closed_rows) - 1) - idx
                    remain_candles = self.entry_symbol_shock_pause_cooldown_candles - bars_after
                    if remain_candles <= 0:
                        continue

                    if close_price < open_price:
                        shock_label = "bearish dump"
                        shock_direction = "DOWN"
                    elif close_price > open_price:
                        shock_label = "bullish spike"
                        shock_direction = "UP"
                    else:
                        shock_label = "shock candle"
                        shock_direction = "FLAT"
                    context = {
                        "source_symbol": source_symbol,
                        "source_label": source_label,
                        "shock_label": shock_label,
                        "shock_direction": shock_direction,
                        "remain_candles": int(remain_candles),
                        "range_pct": float(range_pct),
                        "body_pct": float(body_pct),
                        "volume_ratio": float(volume_ratio),
                        "range_vs_avg": float(range_vs_avg),
                    }
                    break
        except Exception:
            context = None

        self._symbol_shock_pause_cache[key] = (now_ts, context)
        return context

    def _symbol_shock_pause_reason(self, *, symbol: str) -> str | None:
        if self.entry_symbol_shock_pause_action != "BLOCK":
            return None
        context = self._symbol_shock_context(symbol=symbol)
        if not context:
            return None
        return (
            f"Entry paused after {context['source_label']} {context['shock_label']} "
            f"({int(context['remain_candles'])}x5m left, {float(context['range_pct']):.2f}% range, "
            f"x{float(context['volume_ratio']):.1f} vol)"
        )

    def _symbol_shock_strong_block_reason(self, *, symbol: str, side: str) -> str | None:
        if not self.entry_symbol_shock_strong_block_enabled:
            return None
        context = self._symbol_shock_context(symbol=symbol)
        if not context:
            return None

        side_key = str(side or "").upper()
        shock_direction = str(context.get("shock_direction") or "").upper()
        if side_key == "LONG":
            if shock_direction != "DOWN":
                return None
        elif side_key == "SHORT":
            if shock_direction != "UP":
                return None
        else:
            return None

        range_pct = float(context.get("range_pct") or 0.0)
        body_pct = float(context.get("body_pct") or 0.0)
        volume_ratio = float(context.get("volume_ratio") or 0.0)
        range_vs_avg = float(context.get("range_vs_avg") or 0.0)
        if (
            range_pct < self.entry_symbol_shock_strong_min_range_pct
            or body_pct < self.entry_symbol_shock_strong_min_body_pct
            or volume_ratio < self.entry_symbol_shock_strong_min_volume_ratio
            or range_vs_avg < self.entry_symbol_shock_strong_min_range_vs_avg
        ):
            return None

        return (
            f"Entry blocked after strong {context['source_label']} {context['shock_label']} "
            f"({int(context['remain_candles'])}x5m left, {range_pct:.2f}% range, "
            f"{body_pct:.2f}% body, x{volume_ratio:.1f} vol)"
        )

    def _apply_symbol_shock_entry_offset(self, *, symbol: str, side: str, entry: float) -> float:
        if entry <= 0 or self.entry_symbol_shock_pause_action != "OFFSET":
            return entry
        context = self._symbol_shock_context(symbol=symbol)
        if not context:
            return entry

        side_key = str(side or "").upper()
        shock_direction = str(context.get("shock_direction") or "").upper()
        offset_factor = self.entry_symbol_shock_pause_offset_factor
        if side_key == "LONG":
            if self.entry_symbol_shock_directional_offset_only and shock_direction != "DOWN":
                return entry
            offset_factor = self.entry_symbol_shock_long_offset_factor or offset_factor
        elif side_key == "SHORT":
            if self.entry_symbol_shock_directional_offset_only and shock_direction not in {"UP", "DOWN"}:
                return entry
            offset_factor = self.entry_symbol_shock_short_offset_factor or offset_factor

        raw_offset_pct = float(context.get("range_pct") or 0.0) * float(offset_factor)
        offset_pct = min(
            self.entry_symbol_shock_pause_offset_max_pct,
            max(self.entry_symbol_shock_min_offset_pct, raw_offset_pct),
        )
        if offset_pct <= 0:
            return entry

        offset_ratio = offset_pct / 100.0
        if side_key == "LONG":
            return float(entry) * (1.0 - offset_ratio)
        if side_key == "SHORT":
            return float(entry) * (1.0 + offset_ratio)
        return entry

    def _apply_pattern_entry_offset(self, *, symbol: str, side: str, entry: float) -> float:
        if entry <= 0:
            return entry
        side_key = str(side or "").strip().upper()
        try:
            pattern = str(self.pattern_analyzer.current_symbol_pattern(symbol) or "").strip().upper()
        except Exception:
            pattern = ""
        if side_key not in {"LONG", "SHORT"} or not pattern:
            return entry
        btc_trend: str | None = None
        best_offset_pct = 0.0
        for rule in self.pattern_entry_offset_rules:
            required_side = str(rule.get("side") or "").strip().upper()
            if required_side and required_side != "ANY" and required_side != side_key:
                continue
            required_pattern = str(rule.get("pattern") or "").strip().upper()
            if required_pattern and required_pattern != pattern:
                continue
            required_btc_trend = str(rule.get("btc_trend") or "").strip().upper()
            if required_btc_trend:
                if btc_trend is None:
                    try:
                        btc_trend = str(self.pattern_analyzer.btc_trend_now() or "").strip().upper()
                    except Exception:
                        btc_trend = ""
                if btc_trend != required_btc_trend:
                    continue
            try:
                offset_pct = float(rule.get("offset_pct") or 0.0)
            except Exception:
                offset_pct = 0.0
            if offset_pct <= 0:
                continue
            best_offset_pct = max(best_offset_pct, offset_pct)
        if best_offset_pct <= 0:
            return entry
        offset_ratio = best_offset_pct / 100.0
        if side_key == "LONG":
            return float(entry) * (1.0 - offset_ratio)
        return float(entry) * (1.0 + offset_ratio)
        return entry

    def _is_short_inside_bar_waiting_breakdown(self, *, symbol: str) -> bool:
        if not self.entry_short_inside_bar_breakdown_confirm_enabled:
            return False

        key = self._normalize_symbol_key(symbol)
        now_ts = time.time()
        cached = self._short_inside_bar_breakdown_cache.get(key)
        if cached is not None:
            cached_ts, cached_value = cached
            if (now_ts - cached_ts) <= self.instant_sl_guard_short_top_test_cache_sec:
                return bool(cached_value)

        matched = False
        try:
            rows = self.market_client.fetch_ohlcv(symbol=symbol, timeframe="5m", limit=6)
            if rows and len(rows) >= 4:
                inside_bar = rows[-2]
                mother_candle = rows[-3]
                pattern = CandlePatternAnalyzer._classify_pattern(mother_candle, inside_bar)
                matched = pattern == "INSIDE_BAR"
        except Exception:
            matched = False

        self._short_inside_bar_breakdown_cache[key] = (now_ts, bool(matched))
        return bool(matched)

    def _is_long_inside_bar_waiting_breakout(self, *, symbol: str) -> bool:
        if not self.entry_long_inside_bar_breakout_confirm_enabled:
            return False

        key = self._normalize_symbol_key(symbol)
        now_ts = time.time()
        cached = self._long_inside_bar_breakout_cache.get(key)
        if cached is not None:
            cached_ts, cached_value = cached
            if (now_ts - cached_ts) <= self.instant_sl_guard_short_top_test_cache_sec:
                return bool(cached_value)

        matched = False
        try:
            rows = self.market_client.fetch_ohlcv(symbol=symbol, timeframe="5m", limit=6)
            if rows and len(rows) >= 4:
                inside_bar = rows[-2]
                mother_candle = rows[-3]
                pattern = CandlePatternAnalyzer._classify_pattern(mother_candle, inside_bar)
                matched = pattern == "INSIDE_BAR"
        except Exception:
            matched = False

        self._long_inside_bar_breakout_cache[key] = (now_ts, bool(matched))
        return bool(matched)

    def _is_strong_bull_waiting_long_confirmation(self, *, symbol: str) -> bool:
        if not self.entry_strong_bull_long_confirm_enabled:
            return False

        key = self._normalize_symbol_key(symbol)
        now_ts = time.time()
        cached = self._strong_bull_long_confirm_cache.get(key)
        if cached is not None:
            cached_ts, cached_value = cached
            if (now_ts - cached_ts) <= self.instant_sl_guard_short_top_test_cache_sec:
                return bool(cached_value)

        waiting = False
        try:
            rows = self.market_client.fetch_ohlcv(symbol=symbol, timeframe="5m", limit=7)
            if rows and len(rows) >= 5:
                latest_closed = rows[-2]
                latest_prev = rows[-3]
                latest_pattern = CandlePatternAnalyzer._classify_pattern(latest_prev, latest_closed)
                if latest_pattern == "STRONG_BULL":
                    waiting = True
                else:
                    setup_prev = rows[-4]
                    setup_candle = rows[-3]
                    confirm_candle = rows[-2]
                    setup_pattern = CandlePatternAnalyzer._classify_pattern(setup_prev, setup_candle)
                    if setup_pattern == "STRONG_BULL":
                        setup_high = float(setup_candle[2])
                        setup_close = float(setup_candle[4])
                        confirm_open = float(confirm_candle[1])
                        confirm_close = float(confirm_candle[4])
                        bullish_confirm = (
                            confirm_close > confirm_open
                            and confirm_close >= setup_close
                            and float(confirm_candle[2]) >= setup_high
                        )
                        waiting = not bullish_confirm
        except Exception:
            waiting = False

        self._strong_bull_long_confirm_cache[key] = (now_ts, bool(waiting))
        return bool(waiting)

    def _is_strong_bull_waiting_short_confirmation(self, *, symbol: str) -> bool:
        if not self.entry_strong_bull_short_confirm_enabled:
            return False

        key = self._normalize_symbol_key(symbol)
        now_ts = time.time()
        cached = self._strong_bull_short_confirm_cache.get(key)
        if cached is not None:
            cached_ts, cached_value = cached
            if (now_ts - cached_ts) <= self.instant_sl_guard_short_top_test_cache_sec:
                return bool(cached_value)

        waiting = False
        try:
            rows = self.market_client.fetch_ohlcv(symbol=symbol, timeframe="5m", limit=7)
            if rows and len(rows) >= 5:
                latest_closed = rows[-2]
                latest_prev = rows[-3]
                latest_pattern = CandlePatternAnalyzer._classify_pattern(latest_prev, latest_closed)
                if latest_pattern == "STRONG_BULL":
                    waiting = True
                else:
                    setup_prev = rows[-4]
                    setup_candle = rows[-3]
                    confirm_candle = rows[-2]
                    setup_pattern = CandlePatternAnalyzer._classify_pattern(setup_prev, setup_candle)
                    if setup_pattern == "STRONG_BULL":
                        setup_open = float(setup_candle[1])
                        setup_low = float(setup_candle[3])
                        confirm_open = float(confirm_candle[1])
                        confirm_close = float(confirm_candle[4])
                        bearish_confirm = (
                            confirm_close < confirm_open
                            and confirm_close <= setup_open
                            and float(confirm_candle[3]) <= setup_low
                        )
                        waiting = not bearish_confirm
        except Exception:
            waiting = False

        self._strong_bull_short_confirm_cache[key] = (now_ts, bool(waiting))
        return bool(waiting)

    def _register_instant_sl_event(
        self,
        *,
        symbol: str,
        side: str,
        opened_at: object,
        pnl_pct: float,
        mae_pct: float,
    ) -> None:
        if not self.instant_sl_guard_enabled:
            return
        if pnl_pct >= 0:
            return

        held_seconds = self._elapsed_seconds_since(opened_at)
        if held_seconds is None:
            return
        if held_seconds > float(self.instant_sl_guard_max_hold_minutes * 60):
            return

        abs_loss_pct = abs(float(pnl_pct))
        abs_mae_pct = abs(min(0.0, float(mae_pct)))
        severe_loss = abs_loss_pct >= self.instant_sl_guard_min_abs_pnl_pct
        severe_mae = abs_mae_pct >= self.instant_sl_guard_min_abs_mae_pct
        if not (severe_loss or severe_mae):
            return

        now_ts = time.time()
        lock_until_ts = now_ts + float(self.instant_sl_guard_cooldown_minutes * 60)
        key = self._instant_sl_guard_key(symbol, side)
        self._instant_sl_symbol_side_lock_until_ts[key] = max(
            float(self._instant_sl_symbol_side_lock_until_ts.get(key) or 0.0),
            lock_until_ts,
        )

        if not self.instant_sl_global_guard_enabled:
            return

        self._instant_sl_global_events_ts.append(now_ts)
        window_seconds = float(self.instant_sl_global_window_minutes * 60)
        cutoff_ts = now_ts - window_seconds
        self._instant_sl_global_events_ts = [ts for ts in self._instant_sl_global_events_ts if ts >= cutoff_ts]
        if len(self._instant_sl_global_events_ts) < self.instant_sl_global_threshold:
            return

        pause_until_ts = now_ts + float(self.instant_sl_global_cooldown_minutes * 60)
        self._open_pause_until_ts = max(self._open_pause_until_ts, pause_until_ts)
        self._open_pause_reason = (
            f"Instant-SL global pause {self.instant_sl_global_cooldown_minutes}m "
            f"({len(self._instant_sl_global_events_ts)} severe losses/{self.instant_sl_global_window_minutes}m)"
        )

    @staticmethod
    def _is_sl_close_reason(close_reason: str) -> bool:
        reason = str(close_reason or "").upper()
        return reason in {"SL", "MANUAL_FORCE_LOSS"}

    def _is_btc_bullish_regime(self, btc_guard: dict[str, Any] | None) -> bool:
        guard = btc_guard or {}
        trend_side = str(guard.get("side") or "NEUTRAL").upper()
        try:
            confidence = float(guard.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        return trend_side == "LONG" and confidence >= self.btc_filter_min_confidence

    @staticmethod
    def _count_open_positions_by_side(
        open_trades_by_symbol: dict[str, list[dict[str, Any]]],
    ) -> tuple[int, int]:
        total_open = 0
        short_open = 0
        for bucket in open_trades_by_symbol.values():
            for row in bucket:
                if str(row.get("status") or "OPEN").upper() != "OPEN":
                    continue
                total_open += 1
                if str(row.get("side") or "").upper() == "SHORT":
                    short_open += 1
        return total_open, short_open

    def _pass_bullish_short_nonfollow_ratio_guard(
        self,
        *,
        side: str,
        symbol: str,
        btc_guard: dict[str, Any],
        open_trades_by_symbol: dict[str, list[dict[str, Any]]],
    ) -> bool:
        side_key = str(side or "").upper()
        if side_key != "SHORT":
            return True
        if not self._is_btc_bullish_regime(btc_guard):
            return True
        if self._is_symbol_following_btc(symbol):
            return True

        total_open, short_open = self._count_open_positions_by_side(open_trades_by_symbol)
        projected_short_ratio = (short_open + 1) / max(1, total_open + 1)
        return projected_short_ratio <= self.bullish_short_nonfollow_max_open_ratio

    def _portfolio_guard_reason(
        self,
        *,
        side: str,
        entry_type: str | None = None,
        open_trades_by_symbol: dict[str, list[dict[str, Any]]] | None = None,
        open_rows: list[dict[str, Any]] | None = None,
    ) -> str | None:
        if open_trades_by_symbol is None:
            rows = open_rows
            if rows is None:
                try:
                    rows = self.repo.list_open_trades()
                except Exception:
                    rows = []
            open_trades_by_symbol = self._index_open_trades_by_symbol(rows)
        else:
            rows = open_rows

        normalized_entry_type = str(entry_type or "").strip().upper()
        if self._is_ema99_bounce_entry_type(normalized_entry_type):
            if rows is None:
                try:
                    rows = self.repo.list_open_trades()
                except Exception:
                    rows = []
            if self.ema99_bounce_max_open_trades > 0:
                ema99_open = sum(
                    1
                    for row in (rows or [])
                    if self._is_ema99_bounce_entry_type(str(row.get("entry_type") or ""))
                )
                if ema99_open >= self.ema99_bounce_max_open_trades:
                    return f"Max open EMA99_BOUNCE trades reached ({self.ema99_bounce_max_open_trades})"
            return None
        if self._is_liquidation_style_entry_type(normalized_entry_type):
            if rows is None:
                try:
                    rows = self.repo.list_open_trades()
                except Exception:
                    rows = []
            if self.pump_max_open_trades > 0:
                pump_open = sum(
                    1
                    for row in (rows or [])
                    if self._is_liquidation_style_entry_type(str(row.get("entry_type") or ""))
                )
                if pump_open >= self.pump_max_open_trades:
                    return f"Max open PUMP trades reached ({self.pump_max_open_trades})"
            return None

        total_open, short_open = self._count_open_positions_by_side(open_trades_by_symbol)
        if self.max_open_trades > 0 and total_open >= self.max_open_trades:
            return f"Max open trades reached ({self.max_open_trades})"

        side_key = str(side or "").upper()
        if side_key != "SHORT":
            return None
        if self.max_open_shorts > 0 and short_open >= self.max_open_shorts:
            return f"Max open SHORT reached ({self.max_open_shorts})"
        return None

    @staticmethod
    def _is_liquidation_style_entry_type(entry_type: str | None) -> bool:
        normalized = str(entry_type or "").strip().upper()
        if not normalized:
            return False
        if normalized == "PUMP_ENTRY_TOUCH":
            return True
        return normalized.startswith("PUMP_")

    @staticmethod
    def _is_ema99_bounce_entry_type(entry_type: str | None) -> bool:
        return str(entry_type or "").strip().upper() == "EMA99_BOUNCE"

    @staticmethod
    def _skip_btc_guards_for_entry_type(entry_type: str | None) -> bool:
        return (
            PaperTradingEngine._is_liquidation_style_entry_type(entry_type)
            or PaperTradingEngine._is_ema99_bounce_entry_type(entry_type)
        )

    def _apply_bullish_short_nonfollow_min_win_bonus(
        self,
        *,
        required_min_win: float,
        side: str,
        symbol: str,
        btc_guard: dict[str, Any],
    ) -> float:
        side_key = str(side or "").upper()
        if side_key != "SHORT":
            return required_min_win
        if not self._is_btc_bullish_regime(btc_guard):
            return required_min_win
        if self._is_symbol_following_btc(symbol):
            return required_min_win
        return min(0.99, float(required_min_win) + self.bullish_short_nonfollow_min_win_bonus)

    def _refresh_short_sl_streak_guard_if_needed(self) -> None:
        if not self.short_sl_streak_guard_enabled:
            return
        now_ts = time.time()
        if (now_ts - self._short_sl_streak_refreshed_ts) < float(self.short_sl_streak_refresh_sec):
            return
        self._short_sl_streak_refreshed_ts = now_ts
        try:
            rows = self.repo.list_recent_closed_trades_by_side(side="SHORT", limit=120)
            if not rows:
                return

            if self._short_sl_last_processed_close_id <= 0:
                latest_id = int(rows[0].get("id") or 0)
                streak = 0
                for row in rows:
                    if self._is_sl_close_reason(str(row.get("close_reason") or "")):
                        streak += 1
                    else:
                        break
                self._short_sl_streak_count = int(streak)
                self._short_sl_last_processed_close_id = latest_id
                if self._short_sl_streak_count >= self.short_sl_streak_threshold:
                    self._short_sl_pause_until_ts = max(
                        self._short_sl_pause_until_ts,
                        now_ts + float(self.short_sl_streak_cooldown_minutes * 60),
                    )
                return

            fresh_rows = [
                row
                for row in reversed(rows)
                if int(row.get("id") or 0) > self._short_sl_last_processed_close_id
            ]
            for row in fresh_rows:
                close_id = int(row.get("id") or 0)
                if close_id <= 0:
                    continue
                if self._is_sl_close_reason(str(row.get("close_reason") or "")):
                    self._short_sl_streak_count += 1
                else:
                    self._short_sl_streak_count = 0
                self._short_sl_last_processed_close_id = max(self._short_sl_last_processed_close_id, close_id)
                if self._short_sl_streak_count >= self.short_sl_streak_threshold:
                    self._short_sl_pause_until_ts = max(
                        self._short_sl_pause_until_ts,
                        now_ts + float(self.short_sl_streak_cooldown_minutes * 60),
                    )
        except Exception as exc:
            print(f"[paper-engine] short streak guard refresh failed: {type(exc).__name__}: {exc}")

    def _pass_short_sl_streak_guard(self, *, side: str) -> bool:
        if not self.short_sl_streak_guard_enabled:
            return True
        side_key = str(side or "").upper()
        if side_key != "SHORT":
            return True
        if self._short_sl_pause_until_ts <= 0:
            return True
        now_ts = time.time()
        if now_ts >= self._short_sl_pause_until_ts:
            self._short_sl_pause_until_ts = 0.0
            return True
        return False

    def _refresh_hourly_profiles_if_needed(self) -> None:
        if not (self.hourly_profile_enabled or self.hourly_bad_window_enabled):
            return
        now_ts = time.time()
        if (now_ts - self._hourly_profiles_refreshed_ts) < float(self.hourly_profile_refresh_sec):
            return
        self._hourly_profiles_refreshed_ts = now_ts
        try:
            self.repo.refresh_hourly_profiles(lookback_days=self.hourly_profile_lookback_days)
            self._hourly_profiles_cache = self.repo.hourly_profile_map()
        except Exception as exc:
            print(f"[paper-engine] hourly profile refresh failed: {type(exc).__name__}: {exc}")

    def _apply_hourly_profile_to_probability(
        self,
        *,
        effective_prob: float,
        side: str,
        entry_type: str,
        btc_guard: dict[str, Any] | None = None,
    ) -> float:
        if not self.hourly_profile_enabled:
            return max(0.0, min(1.0, float(effective_prob)))
        if self.hourly_profile_prob_alpha <= 0:
            return max(0.0, min(1.0, float(effective_prob)))
        if not self._hourly_profiles_cache:
            return max(0.0, min(1.0, float(effective_prob)))

        chosen = self._find_hourly_profile_for_now(side=side, entry_type=entry_type, btc_guard=btc_guard)
        if not chosen:
            return max(0.0, min(1.0, float(effective_prob)))

        total_orders = int(chosen.get("total_orders") or 0)
        if total_orders < self.hourly_profile_min_samples:
            return max(0.0, min(1.0, float(effective_prob)))

        win_rate_pct = float(chosen.get("win_rate_pct") or 0.0)
        edge = (win_rate_pct / 100.0) - 0.5
        adjusted = float(effective_prob) + edge * self.hourly_profile_prob_alpha
        return max(0.0, min(1.0, adjusted))

    def _apply_recent_symbol_behavior_penalty(
        self,
        *,
        symbol: str,
        side: str,
        entry_type: str,
        effective_prob: float,
        force_entry_type_scope: bool = False,
    ) -> tuple[float, str | None]:
        normalized_entry_type = str(entry_type or "").strip().upper()
        if not normalized_entry_type.startswith("ML_CANDLES"):
            return max(0.0, min(1.0, float(effective_prob))), None

        try:
            rows = self.repo.list_recent_closed_trades_for_symbol(
                symbol=symbol,
                entry_type=normalized_entry_type if force_entry_type_scope else None,
                limit=6,
            )
        except Exception:
            return max(0.0, min(1.0, float(effective_prob))), None

        if not rows:
            return max(0.0, min(1.0, float(effective_prob))), None

        penalty = 0.0
        sl_count = 0
        deep_loss_count = 0
        flip_count = 0
        previous_side: str | None = None

        for row in rows[:4]:
            row_side = str(row.get("side") or "").upper()
            close_reason = str(row.get("close_reason") or "").upper()
            pnl_pct = self._estimate_trade_pnl_pct_from_row(row)
            try:
                mae_pct = float(row.get("mae_pct") or 0.0)
            except Exception:
                mae_pct = 0.0

            if close_reason in {"SL", "MANUAL_FORCE_LOSS"}:
                sl_count += 1
                penalty += 0.08
            if pnl_pct <= -4.5 or mae_pct <= -6.0:
                deep_loss_count += 1
                penalty += 0.07
            if previous_side and row_side and row_side != previous_side:
                flip_count += 1
            previous_side = row_side or previous_side

        target_side = str(side or "").upper()
        latest_side = str(rows[0].get("side") or "").upper()
        if latest_side and target_side and latest_side != target_side:
            penalty += 0.05

        if sl_count >= 2:
            penalty += 0.12
        if deep_loss_count >= 2:
            penalty += 0.12
        if flip_count >= 2:
            penalty += 0.14 + max(0, flip_count - 2) * 0.04

        adjusted = max(0.0, min(1.0, float(effective_prob) - penalty))
        if penalty < 0.08:
            return adjusted, None

        reasons: list[str] = []
        if deep_loss_count > 0:
            reasons.append("deep-loss")
        if sl_count > 0:
            reasons.append("SL")
        if flip_count > 0:
            reasons.append("flip")
        detail = "/".join(reasons) if reasons else "recent-bad-behavior"
        return adjusted, f"Recent {detail} penalty"

    def _current_vn_hour_weekday(self) -> tuple[int, int]:
        now = datetime.now(self._vn_tz)
        return int(now.hour), int(now.weekday())

    @staticmethod
    def _parse_entry_hard_block_hours(raw: str) -> set[int]:
        blocked: set[int] = set()
        text = str(raw or "").strip()
        if not text:
            return blocked

        for chunk in text.split(","):
            token = str(chunk or "").strip()
            if not token:
                continue
            if "-" in token:
                start_text, end_text = token.split("-", 1)
                try:
                    start_hour = int(start_text.strip())
                    end_hour = int(end_text.strip())
                except Exception:
                    continue
                if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23):
                    continue
                if start_hour <= end_hour:
                    blocked.update(range(start_hour, end_hour + 1))
                else:
                    blocked.update(range(start_hour, 24))
                    blocked.update(range(0, end_hour + 1))
                continue

            try:
                hour = int(token)
            except Exception:
                continue
            if 0 <= hour <= 23:
                blocked.add(hour)
        return blocked

    @staticmethod
    def _parse_weekday_set(raw: str) -> set[int]:
        text = str(raw or "").strip()
        if not text:
            return set()

        all_days = {0, 1, 2, 3, 4, 5, 6}
        mapping = {
            "MON": 0,
            "MONDAY": 0,
            "TUE": 1,
            "TUESDAY": 1,
            "WED": 2,
            "WEDNESDAY": 2,
            "THU": 3,
            "THURSDAY": 3,
            "FRI": 4,
            "FRIDAY": 4,
            "SAT": 5,
            "SATURDAY": 5,
            "SUN": 6,
            "SUNDAY": 6,
        }

        out: set[int] = set()
        for chunk in text.split(","):
            token = str(chunk or "").strip().upper()
            if not token:
                continue
            if token in {"ALL", "*"}:
                return all_days
            if token.isdigit():
                day = int(token)
                if 0 <= day <= 6:
                    out.add(day)
                continue
            day_idx = mapping.get(token)
            if day_idx is None and len(token) >= 3:
                day_idx = mapping.get(token[:3])
            if day_idx is not None:
                out.add(day_idx)
        return out

    def _is_entry_hard_blocked_now(self) -> bool:
        if not self._entry_hard_block_hours_set:
            return False
        hour_vn, _ = self._current_vn_hour_weekday()
        return int(hour_vn) in self._entry_hard_block_hours_set

    def _entry_hard_block_reason(self) -> str | None:
        if not self._is_entry_hard_blocked_now():
            return None
        hour_vn, _ = self._current_vn_hour_weekday()
        return f"Hard block hour VN ({hour_vn:02d}h)"

    def _resolve_profile_trend_key(self, btc_guard: dict[str, Any] | None) -> str:
        if not self.hourly_profile_use_btc_trend:
            return "ALL"
        guard = btc_guard or {}
        trend_side = str(guard.get("side") or "NEUTRAL").upper()
        try:
            confidence = float(guard.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if trend_side in {"LONG", "SHORT"} and confidence >= self.hourly_profile_btc_trend_min_confidence:
            return trend_side
        return "NEUTRAL"

    def _build_hourly_profile_scope_candidates(
        self,
        *,
        entry_type: str,
        weekday_vn: int,
        trend_key: str,
    ) -> list[str]:
        entry_scope = f"ENTRY:{str(entry_type or 'UNKNOWN').upper()}"
        day_enabled = bool(self.hourly_profile_use_weekday)
        trend_enabled = bool(self.hourly_profile_use_btc_trend)
        trend = str(trend_key or "ALL").upper()
        if trend not in {"LONG", "SHORT", "NEUTRAL"}:
            trend = "ALL"

        candidates: list[str] = []
        for base_scope in [entry_scope, "ALL"]:
            if day_enabled and trend_enabled and trend in {"LONG", "SHORT", "NEUTRAL"}:
                candidates.append(f"{base_scope}|DOW:{weekday_vn}|TREND:{trend}")
            if day_enabled:
                candidates.append(f"{base_scope}|DOW:{weekday_vn}")
            if trend_enabled and trend in {"LONG", "SHORT", "NEUTRAL"}:
                candidates.append(f"{base_scope}|TREND:{trend}")
            candidates.append(base_scope)

        out: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            scope = str(item or "").upper()
            if not scope or scope in seen:
                continue
            seen.add(scope)
            out.append(scope)
        return out

    def _find_hourly_profile_for_now(
        self,
        *,
        side: str,
        entry_type: str,
        btc_guard: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self._hourly_profiles_cache:
            return None
        hour_vn, weekday_vn = self._current_vn_hour_weekday()
        side_key = str(side or "ALL").upper()
        trend_key = self._resolve_profile_trend_key(btc_guard)
        scope_candidates = self._build_hourly_profile_scope_candidates(
            entry_type=entry_type,
            weekday_vn=weekday_vn,
            trend_key=trend_key,
        )
        for scope in scope_candidates:
            for key in (side_key, "ALL"):
                chosen = self._hourly_profiles_cache.get(scope, {}).get(key, {}).get(hour_vn)
                if chosen is not None:
                    return chosen
        return None

    def _evaluate_hourly_bad_window_guard(
        self,
        *,
        side: str,
        entry_type: str,
        base_min_win: float,
        btc_guard: dict[str, Any],
    ) -> tuple[bool, float]:
        required_min_win = max(0.0, min(1.0, float(base_min_win)))
        if not self.hourly_bad_window_enabled:
            return True, required_min_win

        profile = self._find_hourly_profile_for_now(side=side, entry_type=entry_type, btc_guard=btc_guard)
        if not profile:
            return True, required_min_win

        total_orders = int(profile.get("total_orders") or 0)
        if total_orders < self.hourly_bad_window_min_samples:
            return True, required_min_win

        wins = int(profile.get("wins") or 0)
        losses = int(profile.get("losses") or 0)
        win_rate_pct = float(profile.get("win_rate_pct") or 0.0)
        net_pnl = float(profile.get("net_pnl") or 0.0)

        block_bad_window = (
            win_rate_pct <= self.hourly_bad_window_block_win_rate_pct
            or (losses > wins and net_pnl < 0.0)
        )
        strict_bad_window = (
            (not block_bad_window)
            and (win_rate_pct <= self.hourly_bad_window_strict_win_rate_pct or net_pnl < 0.0)
        )

        trend_side = str((btc_guard or {}).get("side") or "NEUTRAL").upper()
        trend_confidence = float((btc_guard or {}).get("confidence") or 0.0)
        side_key = str(side or "").upper()
        countertrend = (
            trend_side in {"LONG", "SHORT"}
            and side_key in {"LONG", "SHORT"}
            and side_key != trend_side
            and trend_confidence >= self.btc_filter_min_confidence
        )
        if self.hourly_bad_window_countertrend_hard_block and countertrend and strict_bad_window:
            block_bad_window = True
            strict_bad_window = False

        if strict_bad_window:
            required_min_win = min(0.99, required_min_win + self.hourly_bad_window_strict_min_win_bonus)
        if block_bad_window:
            return False, required_min_win
        return True, required_min_win

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
            entry_type = str(row.get("entry_type") or "LIMIT")
            commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
            close_items.append((row, float(pnl), commission))

        for trade_row, pnl, commission in close_items:
            try:
                net_pnl = pnl - commission
                self._close_trade_with_context(
                    trade_row,
                    close_price=float(market_price),
                    pnl=net_pnl,
                    result=1,
                    close_reason="OPPOSITE_SIGNAL_FLIP",
                    commission_usdt=commission,
                )
                closed_trade_ids.add(int(trade_row.get("id") or 0))
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
            ticker = self.market_client.fetch_binance_ticker(symbol=symbol)
            price = self._extract_price_from_ticker(ticker)
            if price is not None:
                return float(price)
        except Exception:
            pass

        try:
            rows = self.market_client.fetch_binance_ohlcv(symbol=symbol, timeframe="1m", limit=2)
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
            payload = self.market_client.fetch_binance_tickers(unique)
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
            markets = self.market_client.load_binance_markets()
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
            tickers = self.market_client.fetch_binance_tickers(all_symbols[:220])
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
            "trend_1h_side": "NEUTRAL",
            "trend_1h_confidence": 0.0,
            "trend_1h_score": 0.0,
            "trend_1h_prev_side": "NEUTRAL",
            "trend_1h_reversal": False,
            "ema99_15m": 0.0,
            "ema99_1h": 0.0,
            "ema8_1h": 0.0,
            "ema13_1h": 0.0,
            "ema21_1h": 0.0,
            "nearest_ema99": 0.0,
            "near_ema99": False,
            "near_ema99_gap_pct": 0.0,
            "recent_low_15m": 0.0,
            "recent_high_15m": 0.0,
            "recent_low_1h": 0.0,
            "recent_high_1h": 0.0,
            "rebound_from_recent_low_pct": 0.0,
            "rebound_from_recent_low_1h_pct": 0.0,
            "green_candle_pct": 0.0,
            "prev_green_candle_pct": 0.0,
            "green_candle_1h_pct": 0.0,
            "prev_green_candle_1h_pct": 0.0,
            "ema99_reclaim_up": False,
            "ema99_reclaim_down": False,
            "ema8_reclaim_up_1h": False,
            "short_rebound_ema99_risk": False,
            "short_rebound_ema99_release": False,
            "short_rebound_1h_risk": False,
            "pullback_from_recent_high_pct": 0.0,
            "pullback_from_recent_high_1h_pct": 0.0,
            "long_pullback_ema99_risk": False,
            "long_pullback_ema99_release": False,
            "long_top_fade_risk": False,
            "long_top_fade_release": False,
            "short_stall_profit_exit_risk": False,
            "short_stall_cluster_range_pct": 0.0,
            "short_stall_avg_body_pct": 0.0,
            "short_stall_near_low_pct": 0.0,
            "cluster_higher_close_steps_15m": 0,
            "cluster_lower_close_steps_15m": 0,
            "cluster_red_count_15m": 0,
            "cluster_green_count_15m": 0,
            "cluster_net_move_pct_15m": 0.0,
            "box_position_pct_15m": 0.5,
            "latest_upper_wick_ratio_15m": 0.0,
            "cluster_upper_wick_ratio_15m": 0.0,
            "short_slow_grind_risk": False,
            "long_weak_base_risk": False,
        }
        if (
            not self.btc_filter_enabled
            and not self.btc_shock_pause_enabled
            and not self.btc_trend_hour_lock_enabled
            and not self.btc_reversal_profit_exit_enabled
            and not self.btc_reversal_loss_exit_enabled
            and not self.btc_profit_lock_enabled
            and not self.btc_short_rebound_ema99_block_enabled
            and not self.btc_short_rebound_1h_block_enabled
        ):
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
                trend_side, confidence, score, ema_fast, ema_slow = self._resolve_trend_signal(closes)
                trend_prev_side = "NEUTRAL"
                trend_reversal = False
                if len(closes) >= 61:
                    prev_side, _, _, _, _ = self._resolve_trend_signal(closes[:-1])
                    trend_prev_side = prev_side
                    trend_reversal = (
                        trend_side in {"LONG", "SHORT"}
                        and trend_prev_side in {"LONG", "SHORT"}
                        and trend_side != trend_prev_side
                        and confidence >= self.btc_reversal_min_confidence
                    )
                rsi_15m = self._rsi_last(closes, period=14)
                rsi_1h = rsi_15m
                ema99_15m = self._ema_last(closes[-180:], period=99) if len(closes) >= 99 else 0.0
                ema99_1h = 0.0
                ema8_1h = 0.0
                ema13_1h = 0.0
                ema21_1h = 0.0
                trend_1h_side = "NEUTRAL"
                trend_1h_confidence = 0.0
                trend_1h_score = 0.0
                trend_1h_prev_side = "NEUTRAL"
                trend_1h_reversal = False
                green_candle_1h_pct = 0.0
                prev_green_candle_1h_pct = 0.0
                recent_low_1h = 0.0
                recent_high_1h = 0.0
                rebound_from_recent_low_1h_pct = 0.0
                pullback_from_recent_high_1h_pct = 0.0
                ema8_reclaim_up_1h = False
                try:
                    rows_1h = self.market_client.fetch_ohlcv(
                        symbol="BTC/USDT",
                        timeframe="1h",
                        limit=220,
                    )
                    closes_1h = [float(row[4]) for row in rows_1h if len(row) >= 5]
                    if len(closes_1h) >= 8:
                        ema8_1h = self._ema_last(closes_1h[-80:], period=8)
                    if len(closes_1h) >= 13:
                        ema13_1h = self._ema_last(closes_1h[-130:], period=13)
                    if len(closes_1h) >= 21:
                        ema21_1h = self._ema_last(closes_1h[-180:], period=21)
                    if len(closes_1h) >= 20:
                        rsi_1h = self._rsi_last(closes_1h, period=14)
                    if len(closes_1h) >= 60:
                        trend_1h_side, trend_1h_confidence, trend_1h_score, _, _ = self._resolve_trend_signal(closes_1h)
                        prev_1h_side, _, _, _, _ = self._resolve_trend_signal(closes_1h[:-1])
                        trend_1h_prev_side = prev_1h_side
                        trend_1h_reversal = (
                            trend_1h_side in {"LONG", "SHORT"}
                            and trend_1h_prev_side in {"LONG", "SHORT"}
                            and trend_1h_side != trend_1h_prev_side
                            and trend_1h_confidence >= self.btc_reversal_min_confidence
                        )
                    if len(closes_1h) >= 99:
                        ema99_1h = self._ema_last(closes_1h[-180:], period=99)
                    if rows_1h:
                        last_row_1h = rows_1h[-1] if len(rows_1h[-1]) >= 5 else None
                        prev_row_1h = rows_1h[-2] if len(rows_1h) >= 2 and len(rows_1h[-2]) >= 5 else None
                        if last_row_1h is not None:
                            open_px_1h = float(last_row_1h[1])
                            close_px_1h = float(last_row_1h[4])
                            if abs(open_px_1h) > 1e-12:
                                green_candle_1h_pct = ((close_px_1h - open_px_1h) / open_px_1h) * 100.0
                        if prev_row_1h is not None:
                            prev_open_px_1h = float(prev_row_1h[1])
                            prev_close_px_1h = float(prev_row_1h[4])
                            if abs(prev_open_px_1h) > 1e-12:
                                prev_green_candle_1h_pct = ((prev_close_px_1h - prev_open_px_1h) / prev_open_px_1h) * 100.0
                        lookback_rows_1h = rows_1h[-self.btc_short_rebound_1h_lookback_candles :]
                        recent_low_1h = min((float(row[3]) for row in lookback_rows_1h if len(row) >= 4), default=0.0)
                        recent_high_1h = max((float(row[2]) for row in lookback_rows_1h if len(row) >= 3), default=0.0)
                        latest_close_1h = float(closes_1h[-1]) if closes_1h else 0.0
                        prev_close_1h = float(closes_1h[-2]) if len(closes_1h) >= 2 else latest_close_1h
                        rebound_from_recent_low_1h_pct = (
                            ((latest_close_1h - recent_low_1h) / recent_low_1h) * 100.0
                            if recent_low_1h > 0 and latest_close_1h > recent_low_1h
                            else 0.0
                        )
                        pullback_from_recent_high_1h_pct = (
                            ((recent_high_1h - latest_close_1h) / recent_high_1h) * 100.0
                            if recent_high_1h > 0 and latest_close_1h < recent_high_1h
                            else 0.0
                        )
                        ema8_reclaim_up_1h = bool(
                            ema8_1h > 0
                            and len(closes_1h) >= 2
                            and prev_close_1h < ema8_1h
                            and latest_close_1h >= ema8_1h
                        )
                except Exception:
                    pass

                last_row = rows[-1] if rows and len(rows[-1]) >= 5 else None
                prev_row = rows[-2] if len(rows) >= 2 and len(rows[-2]) >= 5 else None
                prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
                close_to_close_pct = 0.0
                shock_move_pct = 0.0
                shock_range_pct = 0.0
                green_candle_pct = 0.0
                prev_green_candle_pct = 0.0
                if last_row is not None:
                    open_px = float(last_row[1])
                    high_px = float(last_row[2])
                    low_px = float(last_row[3])
                    close_px = float(last_row[4])
                    if abs(open_px) > 1e-12:
                        green_candle_pct = ((close_px - open_px) / open_px) * 100.0
                    if abs(prev_close) > 1e-12:
                        close_to_close_pct = ((close_px - prev_close) / prev_close) * 100.0
                        shock_move_pct = abs(close_to_close_pct)
                    if abs(open_px) > 1e-12:
                        shock_range_pct = abs((high_px - low_px) / open_px) * 100.0
                if prev_row is not None:
                    prev_open_px = float(prev_row[1])
                    prev_close_px = float(prev_row[4])
                    if abs(prev_open_px) > 1e-12:
                        prev_green_candle_pct = ((prev_close_px - prev_open_px) / prev_open_px) * 100.0
                shock_metric_pct = max(shock_move_pct, shock_range_pct)
                shock = self.btc_shock_pause_enabled and (shock_metric_pct >= self.btc_shock_threshold_pct)
                if close_to_close_pct > 0:
                    shock_direction = "UP"
                elif close_to_close_pct < 0:
                    shock_direction = "DOWN"
                else:
                    shock_direction = "FLAT"

                lookback_rows = rows[-self.btc_short_rebound_ema99_lookback_candles :]
                recent_low_15m = min((float(row[3]) for row in lookback_rows if len(row) >= 4), default=0.0)
                recent_high_15m = max((float(row[2]) for row in lookback_rows if len(row) >= 3), default=0.0)
                nearest_ema99 = 0.0
                ema99_candidates = [value for value in [ema99_15m, ema99_1h] if value > 0]
                if ema99_candidates:
                    nearest_ema99 = min(ema99_candidates, key=lambda value: abs(float(closes[-1]) - value))
                near_ema99_gap_pct = (
                    abs((float(closes[-1]) - nearest_ema99) / float(closes[-1])) * 100.0
                    if nearest_ema99 > 0 and abs(float(closes[-1])) > 1e-12
                    else 0.0
                )
                near_ema99 = (
                    nearest_ema99 > 0
                    and abs((float(closes[-1]) - nearest_ema99) / float(closes[-1])) <= self.btc_short_rebound_ema99_tolerance_pct
                )
                rebound_from_recent_low_pct = (
                    ((float(closes[-1]) - recent_low_15m) / recent_low_15m) * 100.0
                    if recent_low_15m > 0 and float(closes[-1]) > recent_low_15m
                    else 0.0
                )
                pullback_from_recent_high_pct = (
                    ((recent_high_15m - float(closes[-1])) / recent_high_15m) * 100.0
                    if recent_high_15m > 0 and float(closes[-1]) < recent_high_15m
                    else 0.0
                )
                box_range_pct_15m = (
                    ((recent_high_15m - recent_low_15m) / recent_low_15m) * 100.0
                    if recent_low_15m > 0 and recent_high_15m > recent_low_15m
                    else 0.0
                )
                box_position_pct_15m = (
                    (float(closes[-1]) - recent_low_15m) / max(1e-12, (recent_high_15m - recent_low_15m))
                    if recent_high_15m > recent_low_15m
                    else 0.5
                )
                cluster_rows_15m = rows[-4:]
                cluster_green_count_15m = 0
                cluster_red_count_15m = 0
                cluster_higher_close_steps_15m = 0
                cluster_lower_close_steps_15m = 0
                cluster_avg_body_pct_15m = 0.0
                cluster_net_move_pct_15m = 0.0
                cluster_trend_15m = "FLAT"
                cluster_body_pcts_15m: list[float] = []
                cluster_upper_wick_ratios_15m: list[float] = []
                cluster_first_open_15m: float | None = None
                cluster_last_close_15m: float | None = None
                prev_cluster_close_15m: float | None = None
                latest_upper_wick_ratio_15m = 0.0
                for row in cluster_rows_15m:
                    if len(row) < 5:
                        continue
                    row_open = float(row[1])
                    row_high = float(row[2])
                    row_low = float(row[3])
                    row_close = float(row[4])
                    if cluster_first_open_15m is None:
                        cluster_first_open_15m = row_open
                    cluster_last_close_15m = row_close
                    if row_close > row_open:
                        cluster_green_count_15m += 1
                    elif row_close < row_open:
                        cluster_red_count_15m += 1
                    if abs(row_open) > 1e-12:
                        cluster_body_pcts_15m.append(abs((row_close - row_open) / row_open) * 100.0)
                    candle_range = max(1e-12, row_high - row_low)
                    upper_wick = max(0.0, row_high - max(row_open, row_close))
                    upper_wick_ratio = upper_wick / candle_range
                    cluster_upper_wick_ratios_15m.append(upper_wick_ratio)
                    latest_upper_wick_ratio_15m = upper_wick_ratio
                    if prev_cluster_close_15m is not None:
                        if row_close > prev_cluster_close_15m:
                            cluster_higher_close_steps_15m += 1
                        elif row_close < prev_cluster_close_15m:
                            cluster_lower_close_steps_15m += 1
                    prev_cluster_close_15m = row_close
                if cluster_body_pcts_15m:
                    cluster_avg_body_pct_15m = sum(cluster_body_pcts_15m) / float(len(cluster_body_pcts_15m))
                cluster_upper_wick_ratio_15m = 0.0
                if cluster_upper_wick_ratios_15m:
                    cluster_upper_wick_ratio_15m = sum(cluster_upper_wick_ratios_15m) / float(len(cluster_upper_wick_ratios_15m))
                if (
                    cluster_first_open_15m is not None
                    and cluster_last_close_15m is not None
                    and abs(cluster_first_open_15m) > 1e-12
                ):
                    cluster_net_move_pct_15m = ((cluster_last_close_15m - cluster_first_open_15m) / cluster_first_open_15m) * 100.0
                if (
                    cluster_net_move_pct_15m >= 0.18
                    and cluster_green_count_15m >= 3
                    and cluster_higher_close_steps_15m >= 2
                ):
                    cluster_trend_15m = "LONG"
                elif (
                    cluster_net_move_pct_15m <= -0.18
                    and cluster_red_count_15m >= 3
                    and cluster_lower_close_steps_15m >= 2
                ):
                    cluster_trend_15m = "SHORT"
                ema99_reclaim_up = bool(
                    nearest_ema99 > 0
                    and len(closes) >= 2
                    and float(prev_close) < nearest_ema99
                    and float(closes[-1]) >= nearest_ema99
                )
                ema99_reclaim_down = bool(
                    nearest_ema99 > 0
                    and len(closes) >= 2
                    and float(prev_close) > nearest_ema99
                    and float(closes[-1]) <= nearest_ema99
                )
                current_bearish_candle = green_candle_pct < 0.0
                current_bullish_candle = green_candle_pct > 0.0
                prev_candle_rebound_risk = bool(
                    self.btc_short_rebound_ema99_block_enabled
                    and nearest_ema99 > 0
                    and prev_row is not None
                    and rebound_from_recent_low_pct >= self.btc_short_rebound_ema99_rebound_pct
                    and prev_green_candle_pct >= self.btc_short_rebound_ema99_green_candle_pct
                    and (
                        float(prev_row[4]) >= (nearest_ema99 * (1.0 - self.btc_short_rebound_ema99_tolerance_pct))
                        or float(prev_row[2]) >= (nearest_ema99 * (1.0 - self.btc_short_rebound_ema99_tolerance_pct))
                    )
                )
                short_rebound_ema99_release = bool(
                    prev_candle_rebound_risk
                    and current_bearish_candle
                    and float(closes[-1]) <= float(prev_close)
                )
                prev_candle_pullback_risk = bool(
                    self.btc_long_pullback_ema99_block_enabled
                    and nearest_ema99 > 0
                    and prev_row is not None
                    and pullback_from_recent_high_pct >= self.btc_short_rebound_ema99_rebound_pct
                    and prev_green_candle_pct <= -self.btc_short_rebound_ema99_green_candle_pct
                    and (
                        float(prev_row[4]) <= (nearest_ema99 * (1.0 + self.btc_short_rebound_ema99_tolerance_pct))
                        or float(prev_row[3]) <= (nearest_ema99 * (1.0 + self.btc_short_rebound_ema99_tolerance_pct))
                    )
                )
                long_pullback_ema99_release = bool(
                    prev_candle_pullback_risk
                    and current_bullish_candle
                    and float(closes[-1]) >= float(prev_close)
                )
                long_top_fade_release = bool(
                    self.btc_long_top_fade_block_enabled
                    and current_bullish_candle
                    and float(closes[-1]) >= float(prev_close)
                )
                short_rebound_ema99_risk = bool(
                    self.btc_short_rebound_ema99_block_enabled
                    and nearest_ema99 > 0
                    and trend_1h_side != "LONG"
                    and rebound_from_recent_low_pct >= self.btc_short_rebound_ema99_rebound_pct
                    and not short_rebound_ema99_release
                    and (
                        near_ema99
                        or ema99_reclaim_up
                    )
                    and (
                        green_candle_pct >= self.btc_short_rebound_ema99_green_candle_pct
                    )
                )
                long_pullback_ema99_risk = bool(
                    self.btc_long_pullback_ema99_block_enabled
                    and nearest_ema99 > 0
                    and trend_1h_side != "SHORT"
                    and pullback_from_recent_high_pct >= self.btc_short_rebound_ema99_rebound_pct
                    and not long_pullback_ema99_release
                    and (
                        near_ema99
                        or ema99_reclaim_down
                    )
                    and (
                        green_candle_pct <= -self.btc_short_rebound_ema99_green_candle_pct
                    )
                )
                long_top_fade_risk = bool(
                    self.btc_long_top_fade_block_enabled
                    and trend_1h_side != "SHORT"
                    and rebound_from_recent_low_pct >= self.btc_short_rebound_ema99_rebound_pct
                    and pullback_from_recent_high_pct >= self.btc_long_top_fade_pullback_pct
                    and not long_top_fade_release
                    and green_candle_pct <= -self.btc_short_rebound_ema99_green_candle_pct
                    and float(closes[-1]) <= float(prev_close)
                )
                short_stall_cluster_range_pct = 0.0
                short_stall_avg_body_pct = 0.0
                short_stall_near_low_pct = 0.0
                short_stall_profit_exit_risk = False
                short_slow_grind_risk = bool(
                    self.btc_short_slow_grind_block_enabled
                    and rebound_from_recent_low_pct >= self.btc_short_slow_grind_min_rebound_pct
                    and box_position_pct_15m <= self.btc_short_slow_grind_max_box_position_pct
                    and cluster_green_count_15m >= 2
                    and cluster_higher_close_steps_15m >= 2
                    and cluster_net_move_pct_15m > 0
                    and cluster_avg_body_pct_15m <= self.btc_short_slow_grind_max_avg_body_pct
                    and (
                        latest_upper_wick_ratio_15m >= self.btc_short_slow_grind_min_upper_wick_ratio
                        or cluster_upper_wick_ratio_15m >= self.btc_short_slow_grind_min_upper_wick_ratio
                    )
                    and not short_break_confirmed
                )
                long_weak_base_risk = bool(
                    self.btc_long_weak_base_block_enabled
                    and pullback_from_recent_high_pct >= self.btc_long_weak_base_min_pullback_pct
                    and box_position_pct_15m <= self.btc_long_weak_base_max_box_position_pct
                    and cluster_red_count_15m >= 2
                    and cluster_lower_close_steps_15m >= 2
                    and cluster_net_move_pct_15m < 0
                    and cluster_avg_body_pct_15m <= self.btc_long_weak_base_max_avg_body_pct
                    and (
                        latest_upper_wick_ratio_15m >= self.btc_long_weak_base_min_upper_wick_ratio
                        or cluster_upper_wick_ratio_15m >= self.btc_long_weak_base_min_upper_wick_ratio
                    )
                    and not long_reclaim_confirmed
                )
                stall_rows = rows[-self.btc_short_stall_profit_exit_lookback_candles :]
                if self.btc_short_stall_profit_exit_enabled and len(stall_rows) >= 3 and recent_low_15m > 0:
                    highs = [float(row[2]) for row in stall_rows if len(row) >= 3]
                    lows = [float(row[3]) for row in stall_rows if len(row) >= 4]
                    body_pcts = []
                    green_count = 0
                    for row in stall_rows:
                        if len(row) < 5:
                            continue
                        row_open = float(row[1])
                        row_close = float(row[4])
                        if row_open > 0:
                            body_pcts.append(abs((row_close - row_open) / row_open) * 100.0)
                        if row_close > row_open:
                            green_count += 1
                    if highs and lows and body_pcts:
                        cluster_high = max(highs)
                        cluster_low = min(lows)
                        cluster_base = max(1e-12, abs(float(closes[-1])))
                        short_stall_cluster_range_pct = ((cluster_high - cluster_low) / cluster_base) * 100.0
                        short_stall_avg_body_pct = sum(body_pcts) / float(len(body_pcts))
                        short_stall_near_low_pct = (
                            ((float(closes[-1]) - recent_low_15m) / recent_low_15m) * 100.0
                            if float(closes[-1]) >= recent_low_15m
                            else 0.0
                        )
                        short_stall_profit_exit_risk = bool(
                            pullback_from_recent_high_pct >= self.btc_short_stall_profit_exit_min_pullback_pct
                            and short_stall_near_low_pct <= self.btc_short_stall_profit_exit_near_low_pct
                            and short_stall_cluster_range_pct <= self.btc_short_stall_profit_exit_max_cluster_range_pct
                            and short_stall_avg_body_pct <= self.btc_short_stall_profit_exit_max_avg_body_pct
                    and green_count >= 1
                )
                short_rebound_1h_risk = bool(
                    self.btc_short_rebound_1h_block_enabled
                    and trend_side == "SHORT"
                    and confidence >= self.btc_filter_min_confidence
                    and rebound_from_recent_low_pct >= self.btc_short_rebound_1h_15m_confirm_pct
                    and rebound_from_recent_low_1h_pct >= self.btc_short_rebound_1h_rebound_pct
                    and (
                        green_candle_1h_pct >= self.btc_short_rebound_1h_green_candle_pct
                        or prev_green_candle_1h_pct >= self.btc_short_rebound_1h_green_candle_pct
                    )
                    and (
                        ema8_reclaim_up_1h
                        or (
                            ema8_1h > 0
                            and abs(float(closes[-1])) > 1e-12
                            and float(closes[-1]) >= (ema8_1h * (1.0 - self.btc_short_rebound_1h_ema8_tolerance_pct))
                        )
                    )
                )

                payload = {
                    "side": trend_side,
                    "confidence": float(confidence),
                    "score": float(score),
                    "timeframe": self.btc_filter_timeframe,
                    "trend_prev_side": trend_prev_side,
                    "trend_reversal": bool(trend_reversal),
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
                    "trend_1h_side": trend_1h_side,
                    "trend_1h_confidence": float(trend_1h_confidence),
                    "trend_1h_score": float(trend_1h_score),
                    "trend_1h_prev_side": trend_1h_prev_side,
                    "trend_1h_reversal": bool(trend_1h_reversal),
                    "ema99_15m": float(ema99_15m),
                    "ema99_1h": float(ema99_1h),
                    "ema8_1h": float(ema8_1h),
                    "ema13_1h": float(ema13_1h),
                    "ema21_1h": float(ema21_1h),
                    "nearest_ema99": float(nearest_ema99),
                    "near_ema99": bool(near_ema99),
                    "near_ema99_gap_pct": float(near_ema99_gap_pct),
                    "recent_low_15m": float(recent_low_15m),
                    "recent_high_15m": float(recent_high_15m),
                    "recent_low_1h": float(recent_low_1h),
                    "recent_high_1h": float(recent_high_1h),
                    "box_range_pct_15m": float(box_range_pct_15m),
                    "box_position_pct_15m": float(box_position_pct_15m),
                    "cluster_trend_15m": cluster_trend_15m,
                    "cluster_net_move_pct_15m": float(cluster_net_move_pct_15m),
                    "cluster_avg_body_pct_15m": float(cluster_avg_body_pct_15m),
                    "cluster_higher_close_steps_15m": int(cluster_higher_close_steps_15m),
                    "cluster_lower_close_steps_15m": int(cluster_lower_close_steps_15m),
                    "cluster_green_count_15m": int(cluster_green_count_15m),
                    "cluster_red_count_15m": int(cluster_red_count_15m),
                    "latest_upper_wick_ratio_15m": float(latest_upper_wick_ratio_15m),
                    "cluster_upper_wick_ratio_15m": float(cluster_upper_wick_ratio_15m),
                    "rebound_from_recent_low_pct": float(rebound_from_recent_low_pct),
                    "rebound_from_recent_low_1h_pct": float(rebound_from_recent_low_1h_pct),
                    "pullback_from_recent_high_pct": float(pullback_from_recent_high_pct),
                    "pullback_from_recent_high_1h_pct": float(pullback_from_recent_high_1h_pct),
                    "green_candle_pct": float(green_candle_pct),
                    "prev_green_candle_pct": float(prev_green_candle_pct),
                    "green_candle_1h_pct": float(green_candle_1h_pct),
                    "prev_green_candle_1h_pct": float(prev_green_candle_1h_pct),
                    "ema99_reclaim_up": bool(ema99_reclaim_up),
                    "ema99_reclaim_down": bool(ema99_reclaim_down),
                    "ema8_reclaim_up_1h": bool(ema8_reclaim_up_1h),
                    "short_rebound_ema99_risk": bool(short_rebound_ema99_risk),
                    "short_rebound_ema99_release": bool(short_rebound_ema99_release),
                    "short_rebound_1h_risk": bool(short_rebound_1h_risk),
                    "long_pullback_ema99_risk": bool(long_pullback_ema99_risk),
                    "long_pullback_ema99_release": bool(long_pullback_ema99_release),
                    "long_top_fade_risk": bool(long_top_fade_risk),
                    "long_top_fade_release": bool(long_top_fade_release),
                    "short_stall_profit_exit_risk": bool(short_stall_profit_exit_risk),
                    "short_stall_cluster_range_pct": float(short_stall_cluster_range_pct),
                    "short_stall_avg_body_pct": float(short_stall_avg_body_pct),
                    "short_stall_near_low_pct": float(short_stall_near_low_pct),
                    "short_slow_grind_risk": bool(short_slow_grind_risk),
                    "long_weak_base_risk": bool(long_weak_base_risk),
                }
        except Exception:
            payload = cached[1] if cached is not None else neutral

        self._btc_trend_cache = (now, payload)
        return payload

    def _resolve_trend_signal(self, closes: list[float]) -> tuple[str, float, float, float, float]:
        if len(closes) < 60:
            return "NEUTRAL", 0.0, 0.0, 0.0, 0.0
        ema_fast = self._ema_last(closes[-120:], period=21)
        ema_slow = self._ema_last(closes[-160:], period=55)
        lookback = min(6, len(closes) - 1)
        base_idx = len(closes) - 1 - lookback
        base = max(1e-12, abs(closes[base_idx]))
        slope_pct = ((closes[-1] - closes[base_idx]) / base) * 100.0

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
        return trend_side, float(confidence), float(score), float(ema_fast), float(ema_slow)

    @staticmethod
    def _is_countertrend_to_btc(*, side: str, trend_side: str, confidence: float, min_confidence: float) -> bool:
        side_key = str(side or "").upper()
        trend_key = str(trend_side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return False
        if trend_key not in {"LONG", "SHORT"}:
            return False
        if side_key == trend_key:
            return False
        return float(confidence) >= float(min_confidence)

    def _btc_short_rebound_ema99_reason(self, *, side: str, btc_guard: dict[str, Any] | None = None) -> str | None:
        if not self.btc_short_rebound_ema99_block_enabled:
            return None
        if str(side or "").upper() != "SHORT":
            return None
        guard = btc_guard or {}
        if bool(guard.get("short_rebound_ema99_release")):
            return None
        if not bool(guard.get("short_rebound_ema99_risk")):
            return None
        try:
            rebound_pct = float(guard.get("rebound_from_recent_low_pct") or 0.0)
            gap_pct = float(guard.get("near_ema99_gap_pct") or 0.0)
        except Exception:
            rebound_pct = 0.0
            gap_pct = 0.0
        if bool(guard.get("ema99_reclaim_up")):
            return f"BTC reclaim EMA99 after selloff (+{rebound_pct:.2f}% from low)"
        return f"BTC rebound near EMA99 ({gap_pct:.2f}% gap, +{rebound_pct:.2f}% from low)"

    def _btc_limit_short_rebound_1h_reason(
        self,
        *,
        side: str,
        entry_type: str | None,
        btc_guard: dict[str, Any] | None = None,
    ) -> str | None:
        if not self.btc_short_rebound_1h_block_enabled:
            return None
        if str(side or "").upper() != "SHORT":
            return None
        if str(entry_type or "").strip().upper() != "LIMIT":
            return None
        guard = btc_guard or {}
        if not bool(guard.get("short_rebound_1h_risk")):
            return None
        try:
            rebound_15m = float(guard.get("rebound_from_recent_low_pct") or 0.0)
            rebound_1h = float(guard.get("rebound_from_recent_low_1h_pct") or 0.0)
            green_1h = float(guard.get("green_candle_1h_pct") or 0.0)
            ema8_reclaim_up_1h = bool(guard.get("ema8_reclaim_up_1h"))
        except Exception:
            rebound_15m = 0.0
            rebound_1h = 0.0
            green_1h = 0.0
            ema8_reclaim_up_1h = False
        if ema8_reclaim_up_1h:
            return (
                "BTC 1h rebound after dump: reclaim EMA8 "
                f"(15m +{rebound_15m:.2f}%, 1h +{rebound_1h:.2f}%, 1h candle +{green_1h:.2f}%)"
            )
        return (
            "BTC 1h rebound after dump "
            f"(15m +{rebound_15m:.2f}%, 1h +{rebound_1h:.2f}%, 1h candle +{green_1h:.2f}%)"
        )

    def _btc_long_pullback_ema99_reason(self, *, side: str, btc_guard: dict[str, Any] | None = None) -> str | None:
        if not self.btc_long_pullback_ema99_block_enabled:
            return None
        if str(side or "").upper() != "LONG":
            return None
        guard = btc_guard or {}
        if bool(guard.get("long_pullback_ema99_release")):
            return None
        if not bool(guard.get("long_pullback_ema99_risk")):
            return None
        try:
            pullback_pct = float(guard.get("pullback_from_recent_high_pct") or 0.0)
            gap_pct = float(guard.get("near_ema99_gap_pct") or 0.0)
        except Exception:
            pullback_pct = 0.0
            gap_pct = 0.0
        if bool(guard.get("ema99_reclaim_down")):
            return f"BTC lost EMA99 after rally (-{pullback_pct:.2f}% from high)"
        return f"BTC pullback near EMA99 ({gap_pct:.2f}% gap, -{pullback_pct:.2f}% from high)"

    def _btc_long_top_fade_reason(self, *, side: str, btc_guard: dict[str, Any] | None = None) -> str | None:
        if not self.btc_long_top_fade_block_enabled:
            return None
        if str(side or "").upper() != "LONG":
            return None
        guard = btc_guard or {}
        if bool(guard.get("long_top_fade_release")):
            return None
        if not bool(guard.get("long_top_fade_risk")):
            return None
        try:
            pullback_pct = float(guard.get("pullback_from_recent_high_pct") or 0.0)
            rebound_pct = float(guard.get("rebound_from_recent_low_pct") or 0.0)
        except Exception:
            pullback_pct = 0.0
            rebound_pct = 0.0
        return f"BTC fading from recent top (-{pullback_pct:.2f}% from high, +{rebound_pct:.2f}% off low)"

    def _btc_limit_box_wait_reason(
        self,
        *,
        side: str,
        entry_type: str | None,
        btc_guard: dict[str, Any] | None = None,
    ) -> str | None:
        if str(entry_type or "").strip().upper() != "LIMIT":
            return None
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return None
        guard = btc_guard or {}
        try:
            mark_price = float(guard.get("mark_price") or 0.0)
            recent_low = float(guard.get("recent_low_15m") or 0.0)
            recent_high = float(guard.get("recent_high_15m") or 0.0)
            box_range_pct = float(guard.get("box_range_pct_15m") or 0.0)
            box_position_pct = float(guard.get("box_position_pct_15m") or 0.5)
            cluster_net_move_pct = float(guard.get("cluster_net_move_pct_15m") or 0.0)
            cluster_trend = str(guard.get("cluster_trend_15m") or "FLAT").upper()
        except Exception:
            return None

        if mark_price <= 0 or recent_low <= 0 or recent_high <= recent_low:
            return None
        if box_range_pct < 0.35:
            return None

        break_confirm_pct = 0.0008
        edge_tolerance_pct = 0.0015
        red_zone_max = 0.36
        green_zone_min = 0.64

        short_break_confirmed = mark_price <= (recent_low * (1.0 - break_confirm_pct))
        long_reclaim_confirmed = mark_price >= (recent_high * (1.0 + break_confirm_pct))
        in_red_zone = (box_position_pct <= red_zone_max) or (mark_price <= (recent_low * (1.0 + edge_tolerance_pct)))
        in_green_zone = (box_position_pct >= green_zone_min) or (mark_price >= (recent_high * (1.0 - edge_tolerance_pct)))

        if side_key == "SHORT":
            if short_break_confirmed:
                return None
            if in_red_zone:
                rebound_note = ""
                if cluster_trend == "LONG" or cluster_net_move_pct > 0.18:
                    rebound_note = " Cum nen 15m dang hoi len."
                return (
                    "Chan SHORT ML basic: BTC dang o red zone ho tro 15m, tranh short vao day box."
                    f"{rebound_note} Cho break duoi {recent_low:.1f} roi moi short."
                )
            return None

        if long_reclaim_confirmed:
            return None
        if in_green_zone:
            reject_note = ""
            if cluster_trend == "SHORT" or cluster_net_move_pct < -0.18:
                reject_note = " Cum nen 15m dang bi tu choi xuong."
            return (
                "Chan LONG ML basic: BTC dang o green zone khang cu 15m, tranh long vao dinh box."
                f"{reject_note} Cho reclaim tren {recent_high:.1f} roi moi long."
            )
        return None

    def _btc_short_slow_grind_reason(
        self,
        *,
        side: str,
        entry_type: str | None,
        btc_guard: dict[str, Any] | None = None,
    ) -> str | None:
        if str(entry_type or "").strip().upper() != "LIMIT":
            return None
        if str(side or "").upper() != "SHORT":
            return None
        guard = btc_guard or {}
        if not bool(guard.get("short_slow_grind_risk")):
            return None
        try:
            rebound_pct = float(guard.get("rebound_from_recent_low_pct") or 0.0)
            avg_body_pct = float(guard.get("cluster_avg_body_pct_15m") or 0.0)
            latest_upper_wick_ratio = float(guard.get("latest_upper_wick_ratio_15m") or 0.0)
            cluster_upper_wick_ratio = float(guard.get("cluster_upper_wick_ratio_15m") or 0.0)
            recent_low = float(guard.get("recent_low_15m") or 0.0)
        except Exception:
            rebound_pct = 0.0
            avg_body_pct = 0.0
            latest_upper_wick_ratio = 0.0
            cluster_upper_wick_ratio = 0.0
            recent_low = 0.0
        wick_ratio = max(latest_upper_wick_ratio, cluster_upper_wick_ratio) * 100.0
        return (
            "Chan SHORT ML basic: BTC dang bo cham roi rut rau tren o cum nen 15m, "
            f"de quet SL short som (rebound +{rebound_pct:.2f}%, body TB {avg_body_pct:.2f}%, wick {wick_ratio:.0f}%). "
            f"Cho break lai day {recent_low:.1f} roi moi short."
        )

    def _btc_long_weak_base_reason(
        self,
        *,
        side: str,
        entry_type: str | None,
        btc_guard: dict[str, Any] | None = None,
    ) -> str | None:
        if str(entry_type or "").strip().upper() != "LIMIT":
            return None
        if str(side or "").upper() != "LONG":
            return None
        guard = btc_guard or {}
        if not bool(guard.get("long_weak_base_risk")):
            return None
        try:
            pullback_pct = float(guard.get("pullback_from_recent_high_pct") or 0.0)
            avg_body_pct = float(guard.get("cluster_avg_body_pct_15m") or 0.0)
            latest_upper_wick_ratio = float(guard.get("latest_upper_wick_ratio_15m") or 0.0)
            cluster_upper_wick_ratio = float(guard.get("cluster_upper_wick_ratio_15m") or 0.0)
            recent_high = float(guard.get("recent_high_15m") or 0.0)
        except Exception:
            pullback_pct = 0.0
            avg_body_pct = 0.0
            latest_upper_wick_ratio = 0.0
            cluster_upper_wick_ratio = 0.0
            recent_high = 0.0
        wick_ratio = max(latest_upper_wick_ratio, cluster_upper_wick_ratio) * 100.0
        return (
            "Chan LONG ML basic: BTC vua giam xong va dang tao weak base 15m, "
            f"di ngang yeu dan kem rut rau tren (pullback -{pullback_pct:.2f}%, body TB {avg_body_pct:.2f}%, wick {wick_ratio:.0f}%). "
            f"Cho reclaim lai tren {recent_high:.1f} hoac it nhat co cum nen manh hon roi moi long."
        )

    def _btc_long_confirmation_ready(self, *, btc_guard: dict[str, Any] | None = None) -> bool:
        key = self._normalize_symbol_key(self.entry_symbol_shock_pause_btc_symbol or "BTC/USDT")
        now_ts = time.time()
        cached = self._btc_long_confirmation_cache.get(key)
        if cached is not None:
            cached_ts, cached_value = cached
            if (now_ts - cached_ts) <= self.instant_sl_guard_short_top_test_cache_sec:
                return bool(cached_value)

        confirmed = False
        try:
            rows = self.market_client.fetch_ohlcv(symbol=self.entry_symbol_shock_pause_btc_symbol, timeframe="15m", limit=7)
            if rows and len(rows) >= 5:
                prev_candle = rows[-3]
                confirm_candle = rows[-2]
                prev_open = float(prev_candle[1])
                prev_high = float(prev_candle[2])
                prev_close = float(prev_candle[4])
                confirm_open = float(confirm_candle[1])
                confirm_high = float(confirm_candle[2])
                confirm_close = float(confirm_candle[4])
                ema_fast = float((btc_guard or {}).get("ema_fast") or 0.0)

                confirmed = bool(
                    confirm_close > confirm_open
                    and confirm_close >= prev_close
                    and (confirm_close >= prev_open or confirm_high >= prev_high)
                    and (ema_fast <= 0.0 or confirm_close >= ema_fast)
                )
                if prev_close >= prev_open:
                    confirmed = bool(
                        confirmed
                        and confirm_high >= prev_high
                        and confirm_close >= prev_close
                    )
        except Exception:
            confirmed = False

        self._btc_long_confirmation_cache[key] = (now_ts, bool(confirmed))
        return bool(confirmed)

    def _btc_limit_long_confirmation_reason(
        self,
        *,
        side: str,
        entry_type: str | None,
        btc_guard: dict[str, Any] | None = None,
    ) -> str | None:
        if str(entry_type or "").strip().upper() != "LIMIT":
            return None
        if str(side or "").upper() != "LONG":
            return None
        guard = btc_guard or {}
        try:
            pullback_pct = float(guard.get("pullback_from_recent_high_pct") or 0.0)
            box_position_pct = float(guard.get("box_position_pct_15m") or 0.5)
            cluster_trend = str(guard.get("cluster_trend_15m") or "FLAT").upper()
            cluster_red_count = int(guard.get("cluster_red_count_15m") or 0)
            cluster_lower_close_steps = int(guard.get("cluster_lower_close_steps_15m") or 0)
            cluster_net_move_pct = float(guard.get("cluster_net_move_pct_15m") or 0.0)
            latest_upper_wick_ratio = float(guard.get("latest_upper_wick_ratio_15m") or 0.0)
            cluster_upper_wick_ratio = float(guard.get("cluster_upper_wick_ratio_15m") or 0.0)
            recent_high = float(guard.get("recent_high_15m") or 0.0)
        except Exception:
            return None

        fade_risk = bool(
            guard.get("long_weak_base_risk")
            or guard.get("long_top_fade_risk")
            or guard.get("long_pullback_ema99_risk")
            or (
                pullback_pct >= 0.25
                and box_position_pct <= 0.68
                and cluster_red_count >= 2
                and cluster_lower_close_steps >= 2
                and cluster_net_move_pct <= -0.12
                and (cluster_trend == "SHORT" or latest_upper_wick_ratio >= 0.18 or cluster_upper_wick_ratio >= 0.18)
            )
        )
        if not fade_risk:
            return None
        if self._btc_long_confirmation_ready(btc_guard=guard):
            return None

        wick_ratio = max(latest_upper_wick_ratio, cluster_upper_wick_ratio) * 100.0
        return (
            "Chan LONG ML basic: BTC dang fade sau nhip tang 15m va chua co nen xac nhan hoi lai, "
            f"(pullback -{pullback_pct:.2f}%, wick {wick_ratio:.0f}%, red_count {cluster_red_count}). "
            f"Cho it nhat 1 nen 15m xanh dong manh va reclaim lai nen truoc, uu tien huong ve {recent_high:.1f} roi moi long."
        )

    def _pass_btc_filter(
        self,
        symbol: str,
        side: str,
        effective_prob: float,
        btc_guard: dict[str, Any],
        entry_type: str | None = None,
    ) -> bool:
        if not self._pass_btc_trend_hour_lock(symbol=symbol, side=side, btc_guard=btc_guard):
            return False
        if not self._pass_btc_shock_directional_guard(symbol=symbol, side=side, btc_guard=btc_guard):
            return False
        if self._btc_limit_short_rebound_1h_reason(side=side, entry_type=entry_type, btc_guard=btc_guard):
            return False
        if self._btc_short_rebound_ema99_reason(side=side, btc_guard=btc_guard):
            return False
        if self._btc_long_pullback_ema99_reason(side=side, btc_guard=btc_guard):
            return False
        if self._btc_long_top_fade_reason(side=side, btc_guard=btc_guard):
            return False
        if self._btc_limit_long_confirmation_reason(side=side, entry_type=entry_type, btc_guard=btc_guard):
            return False
        if self._btc_long_weak_base_reason(side=side, entry_type=entry_type, btc_guard=btc_guard):
            return False
        if self._btc_short_slow_grind_reason(side=side, entry_type=entry_type, btc_guard=btc_guard):
            return False
        if self._btc_limit_box_wait_reason(side=side, entry_type=entry_type, btc_guard=btc_guard):
            return False
        if not self.btc_filter_enabled:
            return True

        trend_side = str((btc_guard or {}).get("side") or "NEUTRAL").upper()
        try:
            confidence = float((btc_guard or {}).get("confidence") or 0.0)
        except Exception:
            confidence = 0.0

        follows_btc = self._is_symbol_following_btc(symbol)
        if not follows_btc:
            # Tightened rule: when BTC trend is strong enough, non-following symbols can no longer bypass
            # counter-trend entries (especially SHORT in bullish regime).
            countertrend_nonfollow = self._is_countertrend_to_btc(
                side=side,
                trend_side=trend_side,
                confidence=confidence,
                min_confidence=self.btc_filter_nonfollow_countertrend_min_confidence,
            )
            if not countertrend_nonfollow:
                return True
            if self.btc_filter_block_nonfollow_countertrend:
                return False
            threshold = max(self.btc_filter_countertrend_min_win, self.btc_filter_nonfollow_countertrend_min_win)
            return float(effective_prob) >= float(threshold)

        if trend_side not in {"LONG", "SHORT"}:
            return True
        if confidence < self.btc_filter_min_confidence:
            return True
        if side.upper() == trend_side:
            return True
        if self.btc_filter_block_countertrend:
            return False
        return effective_prob >= self.btc_filter_countertrend_min_win

    def _apply_btc_trend_hour_lock(self, btc_guard: dict[str, Any]) -> None:
        if not self.btc_trend_hour_lock_enabled:
            self._btc_trend_hour_lock_trend_side = "NEUTRAL"
            return
        if self.btc_trend_hour_lock_countertrend_hours <= 0:
            self._btc_trend_hour_lock_trend_side = "NEUTRAL"
            return

        trend_side = str((btc_guard or {}).get("side") or "NEUTRAL").upper()
        if trend_side not in {"LONG", "SHORT"}:
            self._btc_trend_hour_lock_trend_side = "NEUTRAL"
            return
        try:
            confidence = float((btc_guard or {}).get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if confidence < self.btc_trend_hour_lock_min_confidence:
            self._btc_trend_hour_lock_trend_side = "NEUTRAL"
            return
        self._btc_trend_hour_lock_trend_side = trend_side

    @staticmethod
    def _resolve_btc_running_candle_side(btc_guard: dict[str, Any] | None) -> str:
        guard = btc_guard or {}
        try:
            candle_pct = float(guard.get("green_candle_pct") or 0.0)
        except Exception:
            candle_pct = 0.0
        if candle_pct > 0:
            return "LONG"
        if candle_pct < 0:
            return "SHORT"
        try:
            close_to_close_pct = float(guard.get("close_to_close_pct") or 0.0)
        except Exception:
            close_to_close_pct = 0.0
        if close_to_close_pct > 0:
            return "LONG"
        if close_to_close_pct < 0:
            return "SHORT"
        return "NEUTRAL"

    def _pass_btc_trend_hour_lock(self, *, symbol: str, side: str, btc_guard: dict[str, Any] | None = None) -> bool:
        if not self.btc_trend_hour_lock_enabled:
            return True
        if self.btc_trend_hour_lock_countertrend_hours <= 0:
            return True
        if not self.btc_trend_hour_lock_apply_non_btc_follow and (not self._is_symbol_following_btc(symbol)):
            return True
        guard = btc_guard or {}
        trend_side = str(guard.get("side") or "NEUTRAL").upper()
        if trend_side not in {"LONG", "SHORT"}:
            return True
        try:
            confidence = float(guard.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if confidence < self.btc_trend_hour_lock_min_confidence:
            return True

        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return True
        if side_key == trend_side:
            return True

        running_candle_side = self._resolve_btc_running_candle_side(guard)
        if running_candle_side == side_key:
            return True
        return False

    def _btc_trend_hour_lock_reason(self, *, symbol: str, side: str, btc_guard: dict[str, Any] | None = None) -> str | None:
        if btc_guard is not None:
            self._apply_btc_trend_hour_lock(btc_guard)
        if self._pass_btc_trend_hour_lock(symbol=symbol, side=side, btc_guard=btc_guard):
            return None
        guard = btc_guard or {}
        trend_side = str(guard.get("side") or "NEUTRAL").upper()
        running_candle_side = self._resolve_btc_running_candle_side(guard)
        side_key = str(side or "").upper()
        if side_key == "SHORT":
            return f"BTC bullish lock SHORT (wait running BTC candle, now {running_candle_side}, regime {trend_side})"
        if side_key == "LONG":
            return f"BTC bearish lock LONG (wait running BTC candle, now {running_candle_side}, regime {trend_side})"
        return f"BTC trend candle lock (candle {running_candle_side}, regime {trend_side})"

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

    def _is_btc_reversal_loss_exit_day(self) -> bool:
        if not self._btc_reversal_loss_exit_days_set:
            return True
        _, weekday_vn = self._current_vn_hour_weekday()
        return int(weekday_vn) in self._btc_reversal_loss_exit_days_set

    def _should_force_close_loss_on_btc_reversal(
        self,
        *,
        entry_type: str,
        side: str,
        pnl: float,
        pnl_pct: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if not self.btc_reversal_loss_exit_enabled:
            return False
        normalized_entry_type = str(entry_type or "").strip().upper()
        if normalized_entry_type == "LIMIT":
            if pnl <= 0:
                return False
        elif pnl >= 0:
            return False
        if abs(float(pnl_pct)) < self.btc_reversal_loss_exit_min_loss_pct:
            return False
        if not self._is_btc_reversal_loss_exit_day():
            return False
        return self._is_countertrend_on_btc_1h_reversal(side=side, btc_guard=btc_guard)

    def _should_force_close_profit_on_btc_reversal(
        self,
        *,
        symbol: str,
        side: str,
        pnl: float,
        pnl_pct: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if not self.btc_reversal_profit_exit_enabled:
            return False
        if pnl <= 0:
            return False
        if pnl_pct <= self.btc_reversal_min_profit_pct:
            return False

        if self._is_countertrend_on_btc_1h_reversal(side=side, btc_guard=btc_guard):
            return True

        # Backward-compatible fallback: close profitable BTC-following LONG
        # when there is a strong downward shock and short BTC regime.
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

    def _should_close_profit_on_btc_short_rebound(
        self,
        *,
        side: str,
        pnl: float,
        pnl_pct: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if not self.btc_short_rebound_profit_exit_enabled:
            return False
        if str(side or "").upper() != "SHORT":
            return False
        if pnl <= 0:
            return False
        if pnl_pct < self.btc_reversal_min_profit_pct:
            return False

        if bool((btc_guard or {}).get("short_rebound_ema99_risk")):
            return True

        shock_direction = str((btc_guard or {}).get("shock_direction") or "FLAT").upper()
        if shock_direction != "UP":
            return False
        try:
            green_candle_pct = float((btc_guard or {}).get("green_candle_pct") or 0.0)
            rebound_from_recent_low_pct = float((btc_guard or {}).get("rebound_from_recent_low_pct") or 0.0)
        except Exception:
            green_candle_pct = 0.0
            rebound_from_recent_low_pct = 0.0
        if green_candle_pct < self.btc_short_rebound_ema99_green_candle_pct:
            return False
        if rebound_from_recent_low_pct < self.btc_short_rebound_ema99_rebound_pct:
            return False
        return True

    def _should_close_profit_on_btc_short_stall(
        self,
        *,
        side: str,
        pnl: float,
        pnl_pct: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if not self.btc_short_stall_profit_exit_enabled:
            return False
        if str(side or "").upper() != "SHORT":
            return False
        if pnl <= 0:
            return False
        if pnl_pct < self.btc_reversal_min_profit_pct:
            return False
        return bool((btc_guard or {}).get("short_stall_profit_exit_risk"))

    def _should_close_profit_on_btc_long_pullback(
        self,
        *,
        side: str,
        pnl: float,
        pnl_pct: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if str(side or "").upper() != "LONG":
            return False
        if pnl <= 0:
            return False
        if pnl_pct < self.btc_reversal_min_profit_pct:
            return False
        return bool((btc_guard or {}).get("long_pullback_ema99_risk"))

    def _should_close_profit_on_btc_long_top_fade(
        self,
        *,
        side: str,
        pnl: float,
        pnl_pct: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if str(side or "").upper() != "LONG":
            return False
        if pnl <= 0:
            return False
        if pnl_pct < self.btc_reversal_min_profit_pct:
            return False
        return bool((btc_guard or {}).get("long_top_fade_risk"))

    def _is_countertrend_on_btc_1h_reversal(self, *, side: str, btc_guard: dict[str, Any]) -> bool:
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return False

        active_trend_side = str((btc_guard or {}).get("side") or "NEUTRAL").upper()
        active_prev_side = str((btc_guard or {}).get("trend_prev_side") or "NEUTRAL").upper()
        try:
            active_confidence = float((btc_guard or {}).get("confidence") or 0.0)
        except Exception:
            active_confidence = 0.0
        reversal_active = bool((btc_guard or {}).get("trend_reversal"))
        if not reversal_active:
            reversal_active = (
                active_trend_side in {"LONG", "SHORT"}
                and active_prev_side in {"LONG", "SHORT"}
                and active_trend_side != active_prev_side
                and active_confidence >= self.btc_reversal_min_confidence
            )
        if reversal_active and active_trend_side in {"LONG", "SHORT"} and active_confidence >= self.btc_reversal_min_confidence:
            return side_key != active_trend_side

        trend_1h_side = str((btc_guard or {}).get("trend_1h_side") or "NEUTRAL").upper()
        trend_1h_prev_side = str((btc_guard or {}).get("trend_1h_prev_side") or "NEUTRAL").upper()
        try:
            trend_1h_confidence = float((btc_guard or {}).get("trend_1h_confidence") or 0.0)
        except Exception:
            trend_1h_confidence = 0.0
        reversal_1h = bool((btc_guard or {}).get("trend_1h_reversal"))
        if not reversal_1h:
            reversal_1h = (
                trend_1h_side in {"LONG", "SHORT"}
                and trend_1h_prev_side in {"LONG", "SHORT"}
                and trend_1h_side != trend_1h_prev_side
                and trend_1h_confidence >= self.btc_reversal_min_confidence
            )
        if not reversal_1h:
            return False
        if trend_1h_side not in {"LONG", "SHORT"}:
            return False
        if trend_1h_confidence < self.btc_reversal_min_confidence:
            return False
        return side_key != trend_1h_side

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
        if self._open_pause_until_ts <= 0:
            return False
        now_ts = time.time()
        if now_ts >= self._open_pause_until_ts:
            self._open_pause_until_ts = 0.0
            self._open_pause_reason = None
            return False
        return True

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
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is not None and ask is not None:
            return float((bid + ask) / 2)
        mark_price = ticker.get("markPrice")
        if mark_price is None:
            info = ticker.get("info")
            if isinstance(info, dict):
                mark_price = info.get("markPrice")
        if mark_price is not None:
            return float(mark_price)
        price = ticker.get("last") or ticker.get("close")
        if price is not None:
            return float(price)
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

    def _maybe_schedule_discord_loss_alert(
        self,
        *,
        trade: dict[str, Any],
        market_price: float,
        pnl: float,
        pnl_pct: float,
    ) -> None:
        if not self.discord_loss_alert_enabled or not self.discord_loss_webhook_url:
            return
        trade_id = int(trade.get("id") or 0)
        if trade_id <= 0:
            return
        entry_type = str(trade.get("entry_type") or "LIMIT").strip().upper()
        if entry_type != "PUMP_ENTRY_TOUCH":
            return
        if pnl_pct >= self.discord_loss_alert_rearm_pct:
            self._discord_loss_alerted_trade_ids.discard(trade_id)
            return
        if pnl_pct > self.discord_loss_alert_threshold_pct:
            return
        if trade_id in self._discord_loss_alerted_trade_ids:
            return
        self._discord_loss_alerted_trade_ids.add(trade_id)
        asyncio.create_task(
            self._send_discord_loss_alert(
                trade=trade,
                market_price=float(market_price),
                pnl=float(pnl),
                pnl_pct=float(pnl_pct),
            )
        )

    def _maybe_schedule_open_trade_discord_alert(
        self,
        *,
        trade_id: int,
        symbol: str,
        side: str,
        entry_type: str,
        btc_following: bool | None,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        leverage: int,
        margin_usdt: float,
        raw_prob: float,
        effective_prob: float,
        opened_at: datetime,
    ) -> None:
        if not self.discord_open_alert_enabled or not self.discord_open_webhook_url:
            return
        if str(entry_type or "").strip().upper() != self.candles_bg_entry_type:
            return
        asyncio.create_task(
            self._send_open_trade_discord_alert(
                trade_id=trade_id,
                symbol=str(symbol or ""),
                side=str(side or "").upper(),
                btc_following=btc_following,
                entry_type=str(entry_type or "").strip().upper(),
                entry_price=float(entry_price),
                take_profit=float(take_profit),
                stop_loss=float(stop_loss),
                leverage=int(leverage),
                margin_usdt=float(margin_usdt),
                raw_prob=float(raw_prob),
                effective_prob=float(effective_prob),
                opened_at=opened_at,
            )
        )

    @staticmethod
    def _format_discord_number(value: float, digits: int = 6) -> str:
        text = f"{float(value):.{digits}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    @staticmethod
    def _format_discord_signed_pct(value: float) -> str:
        return f"{float(value):+,.2f}%"

    @staticmethod
    def _format_discord_naive_vn(value: datetime) -> str:
        dt = value
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone(timedelta(hours=7))).replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S ICT")

    def _build_open_trade_pattern_summary(self, *, symbol: str, btc_following: bool | None) -> str:
        pattern = "-"
        btc_trend = "-"
        try:
            resolved_pattern = self.pattern_analyzer.current_symbol_pattern(symbol)
            if resolved_pattern:
                pattern = str(resolved_pattern).strip().upper()
        except Exception:
            pass
        try:
            resolved_btc_trend = self.pattern_analyzer.btc_trend_now()
            if resolved_btc_trend:
                btc_trend = str(resolved_btc_trend).strip().upper()
        except Exception:
            pass

        stat_suffix = ""
        try:
            stat = self.pattern_performance.resolve_live_pattern_stat(symbol)
            if stat is not None:
                win_rate_pct = float(stat.get("win_rate_pct") or 0.0)
                total_trades = int(stat.get("total_trades") or 0)
                if total_trades > 0:
                    stat_suffix = f" | win {win_rate_pct:.1f}% over {total_trades} signals"
        except Exception:
            stat_suffix = ""

        follow_label = "BTC FOLLOWING" if btc_following is True else "BTC NON_FOLLOWING" if btc_following is False else "BTC FOLLOW ?"
        return f"{pattern} | BTC {btc_trend} | {follow_label}{stat_suffix}"

    async def _send_open_trade_discord_alert(
        self,
        *,
        trade_id: int,
        symbol: str,
        side: str,
        btc_following: bool | None,
        entry_type: str,
        entry_price: float,
        take_profit: float,
        stop_loss: float,
        leverage: int,
        margin_usdt: float,
        raw_prob: float,
        effective_prob: float,
        opened_at: datetime,
    ) -> None:
        try:
            tp_pct = self._calc_pnl_pct(side=side, entry=entry_price, mark_price=take_profit, leverage=int(leverage))
            sl_pct = self._calc_pnl_pct(side=side, entry=entry_price, mark_price=stop_loss, leverage=int(leverage))
            pattern_text = await asyncio.to_thread(
                self._build_open_trade_pattern_summary,
                symbol=symbol,
                btc_following=btc_following,
            )
            embed = {
                "title": f"{entry_type} {side} filled: {symbol}",
                "color": 0xED4245 if side == "SHORT" else 0x57F287,
                "fields": [
                    {"name": "Trade ID", "value": str(trade_id), "inline": True},
                    {"name": "Side", "value": side, "inline": True},
                    {"name": "BTC Follow", "value": "YES" if btc_following is True else "NO" if btc_following is False else "-", "inline": True},
                    {"name": "Entry", "value": self._format_discord_number(entry_price, 8), "inline": True},
                    {"name": "TP", "value": f"{self._format_discord_number(take_profit, 8)} ({self._format_discord_signed_pct(tp_pct)})", "inline": True},
                    {"name": "SL", "value": f"{self._format_discord_number(stop_loss, 8)} ({self._format_discord_signed_pct(sl_pct)})", "inline": True},
                    {"name": "Leverage", "value": f"{int(leverage)}x", "inline": True},
                    {"name": "Margin", "value": f"{float(margin_usdt):.2f}", "inline": True},
                    {"name": "Win Prob", "value": f"raw {raw_prob:.4f} | eff {effective_prob:.4f}", "inline": True},
                    {"name": "Pattern", "value": pattern_text, "inline": False},
                    {"name": "Opened At", "value": self._format_discord_naive_vn(opened_at), "inline": False},
                ],
            }
            payload = json.dumps(
                {
                    "username": "ML Candles BG Bot",
                    "allowed_mentions": {"parse": []},
                    "embeds": [embed],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            request = Request(
                self.discord_open_webhook_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "curl/8.7.1",
                    "Accept": "application/json",
                },
                method="POST",
            )
            await asyncio.to_thread(self._post_discord_request, request)
        except Exception:
            logger.exception("Open trade Discord alert failed: trade_id=%s symbol=%s entry_type=%s", trade_id, symbol, entry_type)

    async def _send_discord_loss_alert(
        self,
        *,
        trade: dict[str, Any],
        market_price: float,
        pnl: float,
        pnl_pct: float,
    ) -> None:
        trade_id = int(trade.get("id") or 0)
        symbol = str(trade.get("symbol") or "-")
        side = str(trade.get("side") or "-").upper()
        entry_type = str(trade.get("entry_type") or "LIMIT").upper()
        try:
            suggestion = await asyncio.to_thread(
                self._build_loss_alert_entry_hint,
                symbol,
                market_price,
            )
            leverage = int(trade.get("leverage") or 1)
            margin_usdt = float(trade.get("margin_usdt") or 0.0)
            if margin_usdt <= 0:
                margin_usdt = calc_margin_usdt(
                    entry_price=float(trade.get("entry_price") or 0.0),
                    quantity=float(trade.get("quantity") or 0.0),
                    leverage=leverage,
                )
            tp_value = float(trade.get("take_profit") or 0.0)
            sl_value = float(trade.get("stop_loss") or 0.0)
            embed = {
                "title": f"{entry_type} {side} drawdown: {symbol}",
                "color": 0xED4245,
                "fields": [
                    {"name": "Trade ID", "value": str(trade_id), "inline": True},
                    {"name": "Side", "value": side, "inline": True},
                    {
                        "name": "BTC Follow",
                        "value": "YES" if trade.get("btc_following") is True else "NO" if trade.get("btc_following") is False else "-",
                        "inline": True,
                    },
                    {"name": "Entry", "value": self._format_discord_number(float(trade.get("entry_price") or 0.0), 8), "inline": True},
                    {"name": "TP", "value": f"{self._format_discord_number(tp_value, 8)} ({self._format_discord_signed_pct(self._calc_pnl_pct(side=side, entry=float(trade.get('entry_price') or 0.0), mark_price=tp_value, leverage=leverage))})", "inline": True},
                    {"name": "SL", "value": f"{self._format_discord_number(sl_value, 8)} ({self._format_discord_signed_pct(self._calc_pnl_pct(side=side, entry=float(trade.get('entry_price') or 0.0), mark_price=sl_value, leverage=leverage))})", "inline": True},
                    {"name": "Leverage", "value": f"{leverage}x", "inline": True},
                    {"name": "Margin", "value": f"{margin_usdt:.2f}", "inline": True},
                    {"name": "uPnL", "value": f"{pnl:+.2f} USDT | {pnl_pct:+.2f}%", "inline": True},
                    {"name": "Pattern", "value": suggestion, "inline": False},
                    {"name": "Opened At", "value": self._format_discord_naive_vn(self._parse_dt(trade.get('opened_at')) or datetime.now(timezone(timedelta(hours=7)))), "inline": False},
                ],
            }
            payload = json.dumps(
                {
                    "username": "Paper Trade Bot",
                    "allowed_mentions": {"parse": []},
                    "embeds": [embed],
                },
                ensure_ascii=False,
            ).encode("utf-8")
            request = Request(
                self.discord_loss_webhook_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "curl/8.7.1",
                    "Accept": "application/json",
                },
                method="POST",
            )
            await asyncio.to_thread(self._post_discord_request, request)
        except Exception:
            self._discord_loss_alerted_trade_ids.discard(trade_id)

    @staticmethod
    def _post_discord_request(request: Request) -> None:
        with urlopen(request, timeout=10) as response:
            response.read()

    def _build_loss_alert_entry_hint(self, symbol: str, market_price: float) -> str:
        try:
            analysis = self.pump_scanner.analyze_symbol(
                symbol,
                ticker={"last": market_price, "close": market_price},
            )
        except Exception as exc:
            return f"Entry goi y hien tai: khong lay duoc ({type(exc).__name__})"

        signal_label = str(analysis.get("signal_label") or "WATCH").upper()
        stage = str(analysis.get("stage") or "-").upper()
        effective_score = float(analysis.get("effective_score") or analysis.get("pump_score") or 0.0)
        is_post_sweep = signal_label in {"ENTER", "SWEEPED"} or stage in {"POST_SWEEP", "SWEEPED"}
        side = "SHORT" if is_post_sweep else "LONG"
        entry_price = float(analysis.get("mark_price") or market_price or 0.0)
        take_profit_raw = analysis.get("invalidation_price") if side == "SHORT" else analysis.get("est_liq_target_price")
        stop_loss_raw = analysis.get("est_liq_target_high") if side == "SHORT" else analysis.get("invalidation_price")
        take_profit = float(take_profit_raw or 0.0)
        stop_loss = float(stop_loss_raw or 0.0)
        range_pct_15m = float(analysis.get("range_pct_15m") or 0.0)
        atr_pct_15m = float(analysis.get("atr_pct_15m") or 0.0)
        upper_wick_pct_15m = float(analysis.get("upper_wick_pct_15m") or 0.0)
        volatile_note = ""
        if range_pct_15m >= 4.8 or atr_pct_15m >= 2.8 or upper_wick_pct_15m >= 2.4:
            volatile_note = " | Canh bao: 15m bien dong gat"
        return (
            f"Entry phu hop luc nay: {side} | signal {signal_label}/{stage} | score {effective_score:.1f} | "
            f"entry {entry_price:.6f} | TP {take_profit:.6f} | SL {stop_loss:.6f}{volatile_note}"
        )

    @staticmethod
    def _calc_pnl(side: str, entry: float, close_price: float, quantity: float) -> float:
        if side == "LONG":
            return (close_price - entry) * quantity
        return (entry - close_price) * quantity

    @staticmethod
    def _calc_fee(
        entry: float,
        quantity: float,
        entry_type: str,
        fee_taker: float,
        fee_maker: float,
    ) -> float:
        """Return total commission for both legs (entry + exit) in USDT.
        MARKET order → taker rate, LIMIT order → maker rate.
        """
        notional = entry * quantity
        rate = fee_taker if str(entry_type).upper() == "MARKET" else fee_maker
        return notional * rate * 2  # entry leg + exit leg

    def _resolve_close_context_values(self, symbol: str, close_dt: datetime | None = None) -> tuple[str | None, str | None]:
        safe_symbol = str(symbol or "").strip()
        if not safe_symbol:
            return None, None
        at_dt = close_dt or datetime.now(tz=timezone.utc)
        try:
            pattern = self.pattern_analyzer.symbol_pattern_at(safe_symbol, at_dt)
        except Exception:
            pattern = None
        try:
            btc_trend = self.pattern_analyzer.btc_trend_at(at_dt)
        except Exception:
            btc_trend = None
        return pattern, btc_trend

    def _close_trade_with_context(
        self,
        trade_row: dict[str, Any],
        *,
        close_price: float,
        pnl: float,
        result: int,
        close_reason: str | None = None,
        commission_usdt: float | None = None,
    ) -> None:
        pattern, btc_trend = self._resolve_close_context_values(str(trade_row.get("symbol") or ""))
        self.repo.close_trade(
            trade_id=int(trade_row["id"]),
            close_price=close_price,
            pnl=pnl,
            result=result,
            close_reason=close_reason,
            commission_usdt=commission_usdt,
            close_candle_pattern=pattern,
            btc_trend_at_close=btc_trend,
        )

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

    def _resolve_pump_hunter_move_sl_trigger_pnl_pct(self) -> float:
        return max(0.0, float(self.pump_hunter_move_sl_to_entry_pnl_pct))

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

    def _has_perfect_pattern_win_rate(self, symbol: str) -> bool:
        if not self.perfect_pattern_leverage_enabled:
            return False
        try:
            return bool(
                self.pattern_performance.has_perfect_live_pattern(
                    symbol,
                    min_win_rate_pct=100.0,
                    min_trades=1,
                )
            )
        except Exception:
            return False

    def _use_strong_bear_short_leverage(self, symbol: str, side: str | None = None) -> bool:
        side_key = str(side or "").strip().upper()
        if side_key and side_key != "SHORT":
            return False
        try:
            pattern = str(self.pattern_analyzer.current_symbol_pattern(symbol) or "").strip().upper()
        except Exception:
            pattern = ""
        if pattern != "STRONG_BEAR":
            return False
        try:
            btc_trend = str(self.pattern_analyzer.btc_trend_now() or "").strip().upper()
        except Exception:
            btc_trend = ""
        return btc_trend == "SHORT"

    def _resolve_symbol_leverage(self, symbol: str, atr_pct: float | None = None, side: str | None = None) -> int:
        base_lev = self.major_symbol_leverage if self._is_major_symbol(symbol) else self.leverage
        strong_bear_short = self._use_strong_bear_short_leverage(symbol=symbol, side=side)
        if strong_bear_short:
            base_lev = max(base_lev, 10)
        perfect_pattern = self._has_perfect_pattern_win_rate(symbol)
        if perfect_pattern:
            base_lev = max(base_lev, self.perfect_pattern_leverage)
        if atr_pct is not None and atr_pct >= self.high_volatility_threshold_pct:
            return min(base_lev, self.high_volatility_leverage)
        if perfect_pattern or strong_bear_short:
            return max(1, int(base_lev))
        return min(base_lev, 5)

    def _resolve_max_margin_loss_pct(
        self,
        *,
        symbol: str,
        side: str,
        atr_pct: float | None,
        btc_guard: dict[str, Any] | None,
    ) -> float:
        cap_pct = float(self.max_margin_loss_pct)
        if atr_pct is not None and float(atr_pct) >= self.max_margin_loss_high_atr_threshold_pct:
            cap_pct = max(cap_pct, float(self.max_margin_loss_high_atr_pct))

        guard = btc_guard or {}
        trend_side = str(guard.get("side") or "NEUTRAL").upper()
        try:
            confidence = float(guard.get("confidence") or 0.0)
        except Exception:
            confidence = 0.0

        follows_btc = self._is_symbol_following_btc(symbol)
        min_conf = self.btc_filter_min_confidence if follows_btc else self.btc_filter_nonfollow_countertrend_min_confidence
        if self._is_countertrend_to_btc(
            side=side,
            trend_side=trend_side,
            confidence=confidence,
            min_confidence=min_conf,
        ):
            cap_pct = max(0.5, cap_pct - self.max_margin_loss_countertrend_penalty_pct)
        elif str(side or "").upper() == trend_side and trend_side in {"LONG", "SHORT"} and confidence >= min_conf:
            cap_pct = cap_pct + self.max_margin_loss_aligned_regime_bonus_pct

        return max(0.5, float(cap_pct))

    def _resolve_symbol_max_risk_pct(self, symbol: str, leverage: int | None = None) -> float:
        if self._is_major_symbol(symbol):
            base = max(self.max_risk_pct, self.major_symbol_max_risk_pct)
        else:
            base = self.max_risk_pct
        if leverage is not None and self._has_perfect_pattern_win_rate(symbol):
            base = max(
                float(base),
                calc_estimated_margin_ratio_pct(
                    leverage=max(1, int(leverage)),
                    maint_margin_rate=self.maint_margin_rate,
                ),
            )
        return float(base)

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

    def _is_ema99_bounce_expired(self, opened_at: Any) -> bool:
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
        return held_seconds >= (self.ema99_bounce_max_hold_minutes * 60)

    def _handle_ema99_bounce_trade_exit(
        self,
        *,
        trade: dict[str, Any],
        side: str,
        price: float,
        entry: float,
        tp: float,
        sl: float,
        qty: float,
        pnl: float,
    ) -> bool:
        entry_type = str(trade.get("entry_type") or "LIMIT").strip().upper()
        if not self._is_ema99_bounce_entry_type(entry_type):
            return False

        sl_hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
        if sl_hit:
            result = 1 if pnl >= 0 else 0
            commission = self._calc_fee(
                entry=entry,
                quantity=qty,
                entry_type=entry_type,
                fee_taker=self.fee_taker_pct,
                fee_maker=self.fee_maker_pct,
            )
            net_pnl = pnl - commission
            self._close_trade_with_context(
                trade,
                close_price=price,
                pnl=net_pnl,
                result=result,
                close_reason="EMA99_SL",
                commission_usdt=commission,
            )
            return True

        tp_hit = (side == "LONG" and price >= tp) or (side == "SHORT" and price <= tp)
        if tp_hit:
            result = 1 if pnl >= 0 else 0
            commission = self._calc_fee(
                entry=entry,
                quantity=qty,
                entry_type=entry_type,
                fee_taker=self.fee_taker_pct,
                fee_maker=self.fee_maker_pct,
            )
            net_pnl = pnl - commission
            self._close_trade_with_context(
                trade,
                close_price=price,
                pnl=net_pnl,
                result=result,
                close_reason="EMA99_TP",
                commission_usdt=commission,
            )
            return True

        if not self._is_ema99_bounce_expired(trade.get("opened_at")):
            return False

        result = 1 if pnl >= 0 else 0
        commission = self._calc_fee(
            entry=entry,
            quantity=qty,
            entry_type=entry_type,
            fee_taker=self.fee_taker_pct,
            fee_maker=self.fee_maker_pct,
        )
        net_pnl = pnl - commission
        self._close_trade_with_context(
            trade,
            close_price=price,
            pnl=net_pnl,
            result=result,
            close_reason="EMA99_TIMEOUT",
            commission_usdt=commission,
        )
        return True
