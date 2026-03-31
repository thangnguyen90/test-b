from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
import json
import math
import time
import traceback
from typing import Any
from zoneinfo import ZoneInfo

from app.api.signals import get_cached_symbols_snapshot, get_scan_snapshot
from app.core.config import settings
from app.services.binance_client import BinanceFuturesClient
from app.services.discord_webhook_notifier import DiscordWebhookNotifier
from app.services.liquidation_ml_predictor import LiquidationMLPredictor
from app.services.ml_predictor import MLPredictor
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.signal_candle_pattern_service import signal_candle_pattern_service
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
        negative_recovery_exit_enabled: bool = True,
        negative_recovery_exit_arm_after_minutes: int = 30,
        negative_recovery_exit_negative_pnl_pct: float = 0.0,
        negative_recovery_exit_recover_pnl_pct: float = 0.1,
        hourly_transition_guard_enabled: bool = True,
        hourly_transition_start_minute: int = 55,
        hourly_transition_force_close_end_minute: int = 5,
        hourly_transition_entry_block_before_minutes: int = 10,
        hourly_transition_entry_block_after_minutes: int = 10,
        hourly_transition_min_hold_minutes: int = 15,
        hourly_transition_safe_pnl_pct: float = 0.3,
        funding_guard_enabled: bool = True,
        funding_guard_force_close_before_minutes: int = 5,
        funding_guard_force_close_after_minutes: int = 5,
        funding_guard_entry_block_before_minutes: int = 10,
        funding_guard_entry_block_after_minutes: int = 10,
        funding_guard_min_hold_minutes: int = 15,
        funding_guard_safe_pnl_pct: float = 0.3,
        session_open_guard_enabled: bool = True,
        session_open_guard_sessions: list[str] | None = None,
        session_open_guard_force_close_before_minutes: int = 5,
        session_open_guard_force_close_after_minutes: int = 10,
        session_open_guard_entry_block_before_minutes: int = 15,
        session_open_guard_entry_block_after_minutes: int = 15,
        session_open_guard_min_hold_minutes: int = 15,
        session_open_guard_safe_pnl_pct: float = 0.3,
        macro_event_guard_enabled: bool = True,
        macro_event_guard_keywords: list[str] | None = None,
        macro_event_guard_entry_block_before_minutes: int = 30,
        macro_event_guard_entry_block_after_minutes: int = 30,
        macro_event_guard_force_close_before_minutes: int = 15,
        macro_event_guard_force_close_after_minutes: int = 15,
        macro_event_guard_min_hold_minutes: int = 15,
        macro_event_guard_safe_pnl_pct: float = 0.3,
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
        btc_kill_short_guard_enabled: bool = True,
        btc_kill_short_pump_min_body_pct: float = 0.7,
        btc_kill_short_ema_tolerance_pct: float = 0.0025,
        btc_reversal_profit_exit_enabled: bool = True,
        btc_reversal_threshold_pct: float = 0.8,
        btc_reversal_min_confidence: float = 0.55,
        btc_reversal_min_profit_pct: float = 0.1,
        btc_reversal_loss_exit_enabled: bool = False,
        btc_reversal_loss_exit_days_vn: str = "MON,TUE,WED,THU,FRI,SAT,SUN",
        btc_reversal_loss_exit_min_loss_pct: float = 0.1,
        btc_reversal_entry_block_enabled: bool = True,
        btc_reversal_entry_cooldown_minutes: int = 60,
        btc_profit_lock_enabled: bool = True,
        btc_profit_lock_min_confidence: float = 0.6,
        btc_follow_min_corr: float = 0.45,
        btc_follow_min_beta: float = 0.2,
        btc_follow_lookback: int = 120,
        btc_follow_cache_sec: float = 300.0,
        base_ml_max_symbols: int = 200,
        basic_ml_pattern_gate_enabled: bool = True,
        basic_ml_pattern_min_win_rate_pct: float = 90.0,
        basic_ml_btc_candle_confirm_enabled: bool = True,
        basic_ml_btc_candle_confirm_min_trend_confidence: float = 0.58,
        basic_ml_btc_candle_confirm_min_1h_confidence: float = 0.60,
        basic_ml_btc_candle_confirm_min_body_pct: float = 0.12,
        basic_ml_btc_candle_confirm_cluster_score: float = 0.18,
        basic_ml_post_dump_short_guard_enabled: bool = True,
        basic_ml_post_dump_short_block_minutes: int = 120,
        basic_ml_post_dump_short_recovery_minutes: int = 180,
        basic_ml_post_dump_short_min_1h_confidence: float = 0.68,
        basic_ml_post_dump_short_nonfollow_min_win: float = 0.90,
        basic_ml_post_dump_short_follow_min_win: float = 0.84,
        basic_ml_reversal_short_guard_enabled: bool = True,
        basic_ml_post_pump_long_guard_enabled: bool = True,
        basic_ml_post_pump_long_block_minutes: int = 120,
        basic_ml_post_pump_long_recovery_minutes: int = 180,
        basic_ml_post_pump_long_min_1h_confidence: float = 0.68,
        basic_ml_post_pump_long_nonfollow_min_win: float = 0.90,
        basic_ml_post_pump_long_follow_min_win: float = 0.84,
        basic_ml_reversal_long_guard_enabled: bool = True,
        test_ml_enabled: bool = False,
        test_ml_min_win_probability: float = 0.75,
        test_ml_max_symbols: int = 80,
        test_ml_max_orders_per_cycle: int = 2,
        candles_bg_enabled: bool = False,
        candles_bg_min_win_probability: float = 0.75,
        candles_bg_max_symbols: int = 80,
        candles_bg_max_orders_per_cycle: int = 2,
        candles_bg_entry_type: str = "ML_CANDLES_BG",
        candles_bg_block_hours_vn: str = "",
        candles_bg_long_block_hours_vn: str = "00,04,05,06,11,12,15,18,21,22",
        candles_bg_short_block_hours_vn: str = "03,12,17",
        candles_bg_long_strict_hours_vn: str = "01,07,13,14,16,23",
        candles_bg_short_strict_hours_vn: str = "",
        candles_bg_strict_min_win_bonus: float = 0.04,
        candles_bg_bullish_short_entry_buffer_pct: float = 0.003,
        candles_bg_bullish_short_nonfollow_extra_buffer_pct: float = 0.001,
        candles_bg_discord_webhook_enabled: bool = False,
        candles_bg_discord_webhook_url: str = "",
        candles_bg_discord_webhook_username: str = "ML Candles BG Bot",
        single_position_per_symbol_side: bool = True,
        reentry_cooldown_minutes: int = 0,
        reentry_after_sl_cooldown_minutes: int = 30,
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
        instant_sl_guard_short_top_test_cache_sec: float = 8.0,
        instant_sl_global_guard_enabled: bool = True,
        instant_sl_global_threshold: int = 3,
        instant_sl_global_window_minutes: int = 20,
        instant_sl_global_cooldown_minutes: int = 60,
        entry_hard_block_hours_vn: str = "20",
        limit_long_block_hours_vn: str = "",
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
        fee_taker_pct: float = 0.0005,
        fee_maker_pct: float = 0.0002,
    ) -> None:
        self.repo = repo
        self.predictor = predictor
        self.predictor_test = predictor_test
        self.predictor_candles = predictor_candles
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
        self.negative_recovery_exit_enabled = bool(negative_recovery_exit_enabled)
        self.negative_recovery_exit_arm_after_minutes = max(1, int(negative_recovery_exit_arm_after_minutes))
        self.negative_recovery_exit_negative_pnl_pct = float(negative_recovery_exit_negative_pnl_pct)
        self.negative_recovery_exit_recover_pnl_pct = float(negative_recovery_exit_recover_pnl_pct)
        self.hourly_transition_guard_enabled = bool(hourly_transition_guard_enabled)
        self.hourly_transition_start_minute = max(0, min(59, int(hourly_transition_start_minute)))
        self.hourly_transition_force_close_end_minute = max(0, min(59, int(hourly_transition_force_close_end_minute)))
        self.hourly_transition_entry_block_before_minutes = max(0, min(30, int(hourly_transition_entry_block_before_minutes)))
        self.hourly_transition_entry_block_after_minutes = max(0, min(30, int(hourly_transition_entry_block_after_minutes)))
        self.hourly_transition_min_hold_minutes = max(1, int(hourly_transition_min_hold_minutes))
        self.hourly_transition_safe_pnl_pct = float(hourly_transition_safe_pnl_pct)
        self.funding_guard_enabled = bool(funding_guard_enabled)
        self.funding_guard_force_close_before_minutes = max(0, min(30, int(funding_guard_force_close_before_minutes)))
        self.funding_guard_force_close_after_minutes = max(0, min(30, int(funding_guard_force_close_after_minutes)))
        self.funding_guard_entry_block_before_minutes = max(0, min(60, int(funding_guard_entry_block_before_minutes)))
        self.funding_guard_entry_block_after_minutes = max(0, min(60, int(funding_guard_entry_block_after_minutes)))
        self.funding_guard_min_hold_minutes = max(1, int(funding_guard_min_hold_minutes))
        self.funding_guard_safe_pnl_pct = float(funding_guard_safe_pnl_pct)
        self.session_open_guard_enabled = bool(session_open_guard_enabled)
        self.session_open_guard_sessions = {
            str(item or "").strip().upper()
            for item in (session_open_guard_sessions or [])
            if str(item or "").strip().upper() in {"TOKYO", "LONDON", "US"}
        }
        self.session_open_guard_force_close_before_minutes = max(0, min(60, int(session_open_guard_force_close_before_minutes)))
        self.session_open_guard_force_close_after_minutes = max(0, min(60, int(session_open_guard_force_close_after_minutes)))
        self.session_open_guard_entry_block_before_minutes = max(0, min(90, int(session_open_guard_entry_block_before_minutes)))
        self.session_open_guard_entry_block_after_minutes = max(0, min(90, int(session_open_guard_entry_block_after_minutes)))
        self.session_open_guard_min_hold_minutes = max(1, int(session_open_guard_min_hold_minutes))
        self.session_open_guard_safe_pnl_pct = float(session_open_guard_safe_pnl_pct)
        self.macro_event_guard_enabled = bool(macro_event_guard_enabled)
        self.macro_event_guard_keywords = tuple(
            str(item or "").strip().upper()
            for item in (macro_event_guard_keywords or [])
            if str(item or "").strip().upper()
        )
        self.macro_event_guard_entry_block_before_minutes = max(0, min(240, int(macro_event_guard_entry_block_before_minutes)))
        self.macro_event_guard_entry_block_after_minutes = max(0, min(240, int(macro_event_guard_entry_block_after_minutes)))
        self.macro_event_guard_force_close_before_minutes = max(0, min(120, int(macro_event_guard_force_close_before_minutes)))
        self.macro_event_guard_force_close_after_minutes = max(0, min(120, int(macro_event_guard_force_close_after_minutes)))
        self.macro_event_guard_min_hold_minutes = max(1, int(macro_event_guard_min_hold_minutes))
        self.macro_event_guard_safe_pnl_pct = float(macro_event_guard_safe_pnl_pct)
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
        self.btc_kill_short_guard_enabled = bool(btc_kill_short_guard_enabled)
        self.btc_kill_short_pump_min_body_pct = max(0.1, float(btc_kill_short_pump_min_body_pct))
        self.btc_kill_short_ema_tolerance_pct = max(0.0, float(btc_kill_short_ema_tolerance_pct))
        self.btc_reversal_profit_exit_enabled = bool(btc_reversal_profit_exit_enabled)
        self.btc_reversal_threshold_pct = max(0.0, float(btc_reversal_threshold_pct))
        self.btc_reversal_min_confidence = max(0.0, min(float(btc_reversal_min_confidence), 0.99))
        self.btc_reversal_min_profit_pct = max(0.0, float(btc_reversal_min_profit_pct))
        self.btc_reversal_loss_exit_enabled = bool(btc_reversal_loss_exit_enabled)
        self.btc_reversal_loss_exit_days_vn = str(btc_reversal_loss_exit_days_vn or "").strip()
        self._btc_reversal_loss_exit_days_set = self._parse_weekday_set(self.btc_reversal_loss_exit_days_vn)
        self.btc_reversal_loss_exit_min_loss_pct = max(0.0, float(btc_reversal_loss_exit_min_loss_pct))
        self.btc_reversal_entry_block_enabled = bool(btc_reversal_entry_block_enabled)
        self.btc_reversal_entry_cooldown_minutes = max(0, int(btc_reversal_entry_cooldown_minutes))
        self.btc_profit_lock_enabled = bool(btc_profit_lock_enabled)
        self.btc_profit_lock_min_confidence = max(0.5, min(float(btc_profit_lock_min_confidence), 0.99))
        self.btc_follow_min_corr = max(0.0, min(float(btc_follow_min_corr), 0.99))
        self.btc_follow_min_beta = max(0.0, float(btc_follow_min_beta))
        self.btc_follow_lookback = max(60, min(500, int(btc_follow_lookback)))
        self.btc_follow_cache_sec = max(30.0, float(btc_follow_cache_sec))
        self.base_ml_max_symbols = max(10, min(600, int(base_ml_max_symbols)))
        self.basic_ml_pattern_gate_enabled = bool(basic_ml_pattern_gate_enabled)
        self.basic_ml_pattern_min_win_rate_pct = max(0.0, min(100.0, float(basic_ml_pattern_min_win_rate_pct)))
        self.basic_ml_btc_candle_confirm_enabled = bool(basic_ml_btc_candle_confirm_enabled)
        self.basic_ml_btc_candle_confirm_min_trend_confidence = max(
            0.0,
            min(float(basic_ml_btc_candle_confirm_min_trend_confidence), 0.99),
        )
        self.basic_ml_btc_candle_confirm_min_1h_confidence = max(
            0.0,
            min(float(basic_ml_btc_candle_confirm_min_1h_confidence), 0.99),
        )
        self.basic_ml_btc_candle_confirm_min_body_pct = max(
            0.01,
            float(basic_ml_btc_candle_confirm_min_body_pct),
        )
        self.basic_ml_btc_candle_confirm_cluster_score = max(
            0.05,
            min(float(basic_ml_btc_candle_confirm_cluster_score), 0.95),
        )
        self.basic_ml_post_dump_short_guard_enabled = bool(basic_ml_post_dump_short_guard_enabled)
        self.basic_ml_post_dump_short_block_minutes = max(0, int(basic_ml_post_dump_short_block_minutes))
        self.basic_ml_post_dump_short_recovery_minutes = max(
            self.basic_ml_post_dump_short_block_minutes,
            int(basic_ml_post_dump_short_recovery_minutes),
        )
        self.basic_ml_post_dump_short_min_1h_confidence = max(
            0.0,
            min(float(basic_ml_post_dump_short_min_1h_confidence), 0.99),
        )
        self.basic_ml_post_dump_short_nonfollow_min_win = max(
            0.0,
            min(float(basic_ml_post_dump_short_nonfollow_min_win), 1.0),
        )
        self.basic_ml_post_dump_short_follow_min_win = max(
            0.0,
            min(float(basic_ml_post_dump_short_follow_min_win), 1.0),
        )
        self.basic_ml_reversal_short_guard_enabled = bool(basic_ml_reversal_short_guard_enabled)
        self.basic_ml_post_pump_long_guard_enabled = bool(basic_ml_post_pump_long_guard_enabled)
        self.basic_ml_post_pump_long_block_minutes = max(0, int(basic_ml_post_pump_long_block_minutes))
        self.basic_ml_post_pump_long_recovery_minutes = max(
            self.basic_ml_post_pump_long_block_minutes,
            int(basic_ml_post_pump_long_recovery_minutes),
        )
        self.basic_ml_post_pump_long_min_1h_confidence = max(
            0.0,
            min(float(basic_ml_post_pump_long_min_1h_confidence), 0.99),
        )
        self.basic_ml_post_pump_long_nonfollow_min_win = max(
            0.0,
            min(float(basic_ml_post_pump_long_nonfollow_min_win), 1.0),
        )
        self.basic_ml_post_pump_long_follow_min_win = max(
            0.0,
            min(float(basic_ml_post_pump_long_follow_min_win), 1.0),
        )
        self.basic_ml_reversal_long_guard_enabled = bool(basic_ml_reversal_long_guard_enabled)
        self.test_ml_enabled = bool(test_ml_enabled)
        self.test_ml_min_win_probability = max(0.0, min(float(test_ml_min_win_probability), 1.0))
        self.test_ml_max_symbols = max(10, min(600, int(test_ml_max_symbols)))
        self.test_ml_max_orders_per_cycle = max(1, min(20, int(test_ml_max_orders_per_cycle)))
        self.candles_bg_enabled = bool(candles_bg_enabled)
        self.candles_bg_min_win_probability = max(0.0, min(float(candles_bg_min_win_probability), 1.0))
        self.candles_bg_max_symbols = max(10, min(600, int(candles_bg_max_symbols)))
        self.candles_bg_max_orders_per_cycle = max(1, min(20, int(candles_bg_max_orders_per_cycle)))
        self.candles_bg_entry_type = str(candles_bg_entry_type or "ML_CANDLES_BG").strip().upper() or "ML_CANDLES_BG"
        self.candles_bg_block_hours_vn = str(candles_bg_block_hours_vn or "").strip()
        self._candles_bg_block_hours_set = self._parse_entry_hard_block_hours(self.candles_bg_block_hours_vn)
        self.candles_bg_long_block_hours_vn = str(candles_bg_long_block_hours_vn or "").strip()
        self._candles_bg_long_block_hours_set = self._parse_entry_hard_block_hours(self.candles_bg_long_block_hours_vn)
        self.candles_bg_short_block_hours_vn = str(candles_bg_short_block_hours_vn or "").strip()
        self._candles_bg_short_block_hours_set = self._parse_entry_hard_block_hours(self.candles_bg_short_block_hours_vn)
        self.candles_bg_long_strict_hours_vn = str(candles_bg_long_strict_hours_vn or "").strip()
        self._candles_bg_long_strict_hours_set = self._parse_entry_hard_block_hours(self.candles_bg_long_strict_hours_vn)
        self.candles_bg_short_strict_hours_vn = str(candles_bg_short_strict_hours_vn or "").strip()
        self._candles_bg_short_strict_hours_set = self._parse_entry_hard_block_hours(self.candles_bg_short_strict_hours_vn)
        self.candles_bg_strict_min_win_bonus = max(0.0, min(float(candles_bg_strict_min_win_bonus), 0.25))
        self.candles_bg_bullish_short_entry_buffer_pct = max(0.0, min(float(candles_bg_bullish_short_entry_buffer_pct), 0.02))
        self.candles_bg_bullish_short_nonfollow_extra_buffer_pct = max(
            0.0,
            min(float(candles_bg_bullish_short_nonfollow_extra_buffer_pct), 0.02),
        )
        self.candles_bg_discord_notifier = DiscordWebhookNotifier(
            enabled=bool(candles_bg_discord_webhook_enabled),
            webhook_url=candles_bg_discord_webhook_url,
            username=candles_bg_discord_webhook_username,
        )
        self.single_position_per_symbol_side = bool(single_position_per_symbol_side)
        self.reentry_cooldown_minutes = max(0, int(reentry_cooldown_minutes))
        self.reentry_after_sl_cooldown_minutes = max(0, int(reentry_after_sl_cooldown_minutes))
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
        self.limit_long_block_hours_vn = str(limit_long_block_hours_vn or "").strip()
        self._limit_long_block_hours_set = self._parse_entry_hard_block_hours(self.limit_long_block_hours_vn)
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
        # Binance Futures fee rates (per-side). Default: taker=0.05%, maker=0.02%.
        self.fee_taker_pct = max(0.0, float(fee_taker_pct))
        self.fee_maker_pct = max(0.0, float(fee_maker_pct))
        self._task: asyncio.Task | None = None
        self._stream_exit_task: asyncio.Task | None = None
        self._stream_watch_task: asyncio.Task | None = None
        self._running = False
        self._stream_exit_event = asyncio.Event()
        self._stream_exit_prices: dict[str, float] = {}
        self._stream_open_trades_by_symbol: dict[str, list[dict[str, Any]]] = {}
        self._watched_stream_symbol_keys: set[str] = set()
        self._stream_open_trade_refresh_sec = 1.0
        self._stream_open_trades_refreshed_ts: float = 0.0
        self._negative_recovery_exit_armed_trade_ids: set[int] = set()
        self._vn_tz = timezone(timedelta(hours=7))
        self._session_guard_timezones = {
            "TOKYO": ZoneInfo("Asia/Tokyo"),
            "LONDON": ZoneInfo("Europe/London"),
            "US": ZoneInfo("America/New_York"),
        }
        self._session_guard_specs = {
            "TOKYO": {"hour": 9, "minute": 0},
            "LONDON": {"hour": 8, "minute": 0},
            "US": {"hour": 9, "minute": 30},
        }
        self._macro_event_guard_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._atr_cache: dict[str, tuple[float, float]] = {}
        self._top_vol_cache: tuple[float, list[str]] | None = None
        self._short_top_test_rejection_cache: dict[str, tuple[float, bool]] = {}
        self._btc_trend_cache: tuple[float, dict[str, Any]] | None = None
        self._btc_follow_cache: dict[str, tuple[float, bool, float, float]] = {}
        self._open_pause_until_ts: float = 0.0
        self._open_pause_reason: str | None = None
        self._btc_long_regime_short_lock_until_ts: float = 0.0
        self._btc_short_regime_long_lock_until_ts: float = 0.0
        self._btc_trend_hour_lock_trend_side: str = "NEUTRAL"
        self._btc_reversal_block_until_ts: float = 0.0
        self._btc_reversal_block_side: str = "NEUTRAL"
        self._btc_up_shock_long_block_until_ts: float = 0.0
        self._btc_down_shock_short_block_until_ts: float = 0.0
        self._btc_last_up_shock_ts: float = 0.0
        self._btc_last_down_shock_ts: float = 0.0
        self._hourly_profiles_cache: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
        self._hourly_profiles_refreshed_ts: float = 0.0
        self._short_sl_pause_until_ts: float = 0.0
        self._short_sl_streak_refreshed_ts: float = 0.0
        self._short_sl_streak_count: int = 0
        self._short_sl_last_processed_close_id: int = 0
        self._instant_sl_symbol_side_lock_until_ts: dict[str, float] = {}
        self._instant_sl_global_events_ts: list[float] = []
        self.high_volatility_threshold_pct = 2.0
        self.high_volatility_leverage = 3

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        await asyncio.to_thread(self._refresh_stream_open_trade_watch, True)
        if self.price_stream is not None and hasattr(self.price_stream, "add_listener"):
            try:
                self.price_stream.add_listener(self._handle_stream_price_updates)
            except Exception:
                pass
        self._task = asyncio.create_task(self._loop())
        self._stream_watch_task = asyncio.create_task(self._stream_watch_loop())
        self._stream_exit_task = asyncio.create_task(self._stream_exit_loop())

    async def stop(self) -> None:
        self._running = False
        if self.price_stream is not None and hasattr(self.price_stream, "remove_listener"):
            try:
                self.price_stream.remove_listener(self._handle_stream_price_updates)
            except Exception:
                pass
        for task in (self._stream_exit_task, self._stream_watch_task, self._task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
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

    async def _stream_watch_loop(self) -> None:
        while self._running:
            try:
                await asyncio.to_thread(self._refresh_stream_open_trade_watch, True)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[paper-engine] stream watch refresh failed: {type(exc).__name__}: {exc}")
            await asyncio.sleep(self._stream_open_trade_refresh_sec)

    async def _handle_stream_price_updates(self, updates: dict[str, float], _stamps: dict[str, str]) -> None:
        watched = self._watched_stream_symbol_keys
        if not watched:
            return
        matched = False
        for key, price in updates.items():
            if key not in watched:
                continue
            self._stream_exit_prices[key] = float(price)
            matched = True
        if matched:
            self._stream_exit_event.set()

    async def _stream_exit_loop(self) -> None:
        while self._running:
            try:
                await self._stream_exit_event.wait()
                self._stream_exit_event.clear()
                pending = self._stream_exit_prices
                self._stream_exit_prices = {}
                if not pending:
                    continue
                watch_snapshot = dict(self._stream_open_trades_by_symbol)
                for key, price in pending.items():
                    for trade in list(watch_snapshot.get(key, [])):
                        try:
                            self._process_realtime_tp_sl_for_trade(trade, float(price))
                        except Exception as exc:
                            trade_id = int(trade.get("id") or 0)
                            print(
                                f"[paper-engine] realtime manage failed id={trade_id} symbol={trade.get('symbol')}: "
                                f"{type(exc).__name__}: {exc}"
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[paper-engine] stream exit loop error: {type(exc).__name__}: {exc}")
                traceback.print_exc()

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
        self._set_stream_open_trade_watch(open_trades)
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
        self._apply_btc_reversal_entry_cooldown(btc_guard)
        self._apply_btc_shock_pause(btc_guard)
        await asyncio.to_thread(self._refresh_short_sl_streak_guard_if_needed)
        open_paused = self._is_open_paused()
        entry_hard_blocked = self._is_entry_hard_blocked_now()
        scan_signal_sides_by_symbol: dict[str, set[str]] = {}
        for item in signals:
            self._register_active_signal_side(
                scan_signal_sides_by_symbol,
                symbol=str(item.get("symbol") or ""),
                side=str(item.get("side") or ""),
            )

        # 1) Open simulated orders when price reaches predicted entry for >=75% setups.
        if not entry_hard_blocked:
            for item in signals:
                raw_prob = float(item.get("win_probability") or 0.0)
                if raw_prob < self.min_win_probability:
                    continue

                symbol = str(item.get("symbol"))
                side = str(item.get("side"))
                if open_paused and not self._should_bypass_instant_sl_global_pause_for_entry_type("LIMIT"):
                    continue
                if self._entry_block_reason(symbol=symbol, side=side, entry_type="LIMIT", btc_guard=btc_guard):
                    continue
                basic_ml_short_guard_reason = self._basic_ml_post_dump_short_guard_reason(
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                if basic_ml_short_guard_reason:
                    continue
                basic_ml_long_guard_reason = self._basic_ml_post_pump_long_guard_reason(
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                if basic_ml_long_guard_reason:
                    continue
                basic_ml_candle_confirm_reason = self._basic_ml_btc_candle_confirmation_reason(
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                if basic_ml_candle_confirm_reason:
                    continue
                if self._has_conflicting_open_trade(symbol=symbol, side=side, entry_type="LIMIT"):
                    continue
                if self._is_reentry_cooldown_active(symbol=symbol, side=side, entry_type="LIMIT"):
                    continue
                if (
                    not self._should_bypass_instant_sl_symbol_guard_for_entry_type("LIMIT")
                    and not self._pass_instant_sl_guard(symbol=symbol, side=side)
                ):
                    continue

                entry = float(item.get("predicted_entry_price") or 0.0)
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
                required_min_win = self._apply_basic_ml_post_dump_short_min_win(
                    required_min_win=required_min_win,
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                required_min_win = self._apply_basic_ml_post_pump_long_min_win(
                    required_min_win=required_min_win,
                    symbol=symbol,
                    side=side,
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

                entry, tp, sl = self._adjust_entry_for_btc_kill_short(
                    side=side,
                    entry=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    market_price=float(market_price),
                    btc_guard=btc_guard,
                )

                entry, tp, sl = self._adjust_entry_for_btc_kill_short(
                    side=side,
                    entry=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    market_price=float(market_price),
                    btc_guard=btc_guard,
                )

                entry, tp, sl = self._adjust_entry_for_btc_kill_short(
                    side=side,
                    entry=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    market_price=float(market_price),
                    btc_guard=btc_guard,
                )

                # Trigger condition: market touches/gets through entry.
                touched = self._entry_touched(side=side, market_price=market_price, entry=entry)
                if not touched:
                    continue
                if not self._close_profitable_opposite_trades_on_btc_follow_cluster(
                    target_side=side,
                    current_btc_following=self._resolve_btc_following_flag(symbol),
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                    market_prices=market_prices,
                    scan_signal_sides_by_symbol=scan_signal_sides_by_symbol,
                ):
                    continue
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=float(market_price),
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                feature_snapshot = None
                basic_pattern_reason, pattern_sample, feature_snapshot = await asyncio.to_thread(
                    self._evaluate_basic_ml_pattern_gate,
                    symbol=symbol,
                    side=side,
                    candle_pattern_sample=self._coerce_pattern_sample(item.get("candle_pattern_sample")),
                )
                if basic_pattern_reason:
                    continue
                pattern_leverage_override = self._resolve_ml_pattern_leverage_override(pattern_sample)

                atr_value = await self._resolve_symbol_atr(symbol)
                atr_for_pct = float(atr_value) if atr_value is not None else 0.0
                atr_pct = (atr_for_pct / float(entry)) * 100 if entry > 0 else 0.0
                leverage = pattern_leverage_override or self._resolve_symbol_leverage(symbol, atr_pct)
                tp, sl = self._expand_signal_exit_targets(
                    side=side,
                    entry=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    leverage=leverage,
                    effective_prob=effective_prob,
                    base_min_win=required_min_win,
                )
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
                    atr_value=atr_value,
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self._resolve_signal_min_rr(
                        effective_prob=effective_prob,
                        base_min_win=required_min_win,
                    ),
                    max_tp_pct=self._resolve_signal_max_tp_pct(
                        entry=entry,
                        take_profit=tp,
                    ),
                    leverage=leverage,
                    max_margin_loss_pct=self._resolve_signal_max_margin_loss_pct(
                        entry=entry,
                        stop_loss=sl,
                        leverage=leverage,
                        symbol=symbol,
                        side=side,
                        atr_pct=atr_pct,
                        btc_guard=btc_guard,
                    ),
                )

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
                if feature_snapshot is None:
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
                if self._btc_reversal_entry_block_reason(side=side, btc_guard=btc_guard):
                    continue
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
                required_min_win = self._apply_basic_ml_post_dump_short_min_win(
                    required_min_win=required_min_win,
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                required_min_win = self._apply_basic_ml_post_pump_long_min_win(
                    required_min_win=required_min_win,
                    symbol=symbol,
                    side=side,
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

                entry = float(test_signal.predicted_entry_price)
                tp = float(test_signal.take_profit)
                sl = float(test_signal.stop_loss)
                if entry <= 0 or tp <= 0 or sl <= 0:
                    continue

                touched = self._entry_touched(side=side, market_price=market_price, entry=entry)
                if not touched:
                    continue
                if not self._pass_btc_filter(symbol=symbol, side=side, effective_prob=effective_prob, btc_guard=btc_guard):
                    continue
                if not self._close_profitable_opposite_trades_on_btc_follow_cluster(
                    target_side=side,
                    current_btc_following=self._resolve_btc_following_flag(symbol),
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                    market_prices=market_prices,
                    scan_signal_sides_by_symbol=scan_signal_sides_by_symbol,
                ):
                    continue
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=float(market_price),
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                feature_snapshot = getattr(test_signal, "feature_snapshot", None)
                pattern_sample, feature_snapshot = await asyncio.to_thread(
                    self._resolve_ml_pattern_sample,
                    symbol=symbol,
                    side=side,
                    feature_snapshot=feature_snapshot,
                )
                pattern_leverage_override = self._resolve_ml_pattern_leverage_override(pattern_sample)

                atr_value = await self._resolve_symbol_atr(symbol)
                atr_for_pct = float(atr_value) if atr_value is not None else 0.0
                atr_pct = (atr_for_pct / float(entry)) * 100 if entry > 0 else 0.0
                leverage = pattern_leverage_override or self._resolve_symbol_leverage(symbol, atr_pct)
                tp, sl = self._expand_signal_exit_targets(
                    side=side,
                    entry=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    leverage=leverage,
                    effective_prob=effective_prob,
                    base_min_win=required_min_win,
                )
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
                    atr_value=atr_value,
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self._resolve_signal_min_rr(
                        effective_prob=effective_prob,
                        base_min_win=required_min_win,
                    ),
                    max_tp_pct=self._resolve_signal_max_tp_pct(
                        entry=entry,
                        take_profit=tp,
                    ),
                    leverage=leverage,
                    max_margin_loss_pct=self._resolve_signal_max_margin_loss_pct(
                        entry=entry,
                        stop_loss=sl,
                        leverage=leverage,
                        symbol=symbol,
                        side=side,
                        atr_pct=atr_pct,
                        btc_guard=btc_guard,
                    ),
                )
                
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
                if feature_snapshot is None:
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
                        "entry_price": entry,
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
                    entry_price=entry,
                    quantity=quantity,
                )
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
                if self._candles_bg_block_reason(entry_type=candles_entry_type, side=side):
                    continue
                if self._btc_reversal_entry_block_reason(side=side, btc_guard=btc_guard):
                    continue
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
                candles_bg_strict_bonus = self._candles_bg_strict_min_win_bonus_for_now(
                    entry_type=candles_entry_type,
                    side=side,
                )
                if candles_bg_strict_bonus > 0:
                    required_min_win = min(0.99, required_min_win + candles_bg_strict_bonus)
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

                entry = float(candles_signal.predicted_entry_price)
                tp = float(candles_signal.take_profit)
                sl = float(candles_signal.stop_loss)
                if entry <= 0 or tp <= 0 or sl <= 0:
                    continue

                entry, tp, sl = self._adjust_entry_for_btc_kill_short(
                    side=side,
                    entry=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    market_price=float(market_price),
                    btc_guard=btc_guard,
                )
                entry, tp, sl = self._adjust_ml_candles_bg_entry_for_btc_regime(
                    symbol=symbol,
                    side=side,
                    entry=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    market_price=float(market_price),
                    btc_guard=btc_guard,
                )

                entry_timing_reason = self._ml_candles_bg_entry_timing_reason(
                    symbol=symbol,
                    side=side,
                    market_price=float(market_price),
                    entry=entry,
                    btc_guard=btc_guard,
                )
                if entry_timing_reason:
                    continue
                # ML Candles BG should still honor BTC shock / directional entry blocks like normal ML.
                if not self._pass_btc_filter(symbol=symbol, side=side, effective_prob=effective_prob, btc_guard=btc_guard):
                    continue
                if not self._close_profitable_opposite_trades_on_btc_follow_cluster(
                    target_side=side,
                    current_btc_following=self._resolve_btc_following_flag(symbol),
                    open_trades_by_symbol=candles_open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                    market_prices=market_prices,
                    scan_signal_sides_by_symbol=scan_signal_sides_by_symbol,
                ):
                    continue
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=float(market_price),
                    open_trades_by_symbol=candles_open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                feature_snapshot = None
                candles_pattern_reason, pattern_sample, feature_snapshot = await asyncio.to_thread(
                    self._evaluate_ml_candles_bg_pattern_gate,
                    symbol=symbol,
                    side=side,
                    feature_snapshot=getattr(candles_signal, "feature_snapshot", None),
                )
                if candles_pattern_reason:
                    continue
                pattern_leverage_override = self._resolve_ml_pattern_leverage_override(pattern_sample)

                atr_value = await self._resolve_symbol_atr(symbol)
                atr_for_pct = float(atr_value) if atr_value is not None else 0.0
                atr_pct = (atr_for_pct / float(entry)) * 100 if entry > 0 else 0.0
                leverage = pattern_leverage_override or self._resolve_symbol_leverage(symbol, atr_pct)
                tp, sl = self._expand_signal_exit_targets(
                    side=side,
                    entry=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    leverage=leverage,
                    effective_prob=effective_prob,
                    base_min_win=required_min_win,
                )
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
                    atr_value=atr_value,
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self._resolve_signal_min_rr(
                        effective_prob=effective_prob,
                        base_min_win=required_min_win,
                    ),
                    max_tp_pct=self._resolve_signal_max_tp_pct(
                        entry=entry,
                        take_profit=tp,
                    ),
                    leverage=leverage,
                    max_margin_loss_pct=self._resolve_signal_max_margin_loss_pct(
                        entry=entry,
                        stop_loss=sl,
                        leverage=leverage,
                        symbol=symbol,
                        side=side,
                        atr_pct=atr_pct,
                        btc_guard=btc_guard,
                    ),
                )

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
                        "entry_type": candles_entry_type,
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
                self._cache_open_trade_row(
                    open_trades_by_symbol=candles_open_trades_by_symbol,
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry_price=entry,
                    quantity=quantity,
                )
                self._enqueue_ml_candles_bg_open_notification(
                    trade_id=trade_id,
                    symbol=symbol,
                    side=side,
                    entry=entry,
                    take_profit=normalized_tp,
                    stop_loss=normalized_sl,
                    leverage=leverage,
                    margin_usdt=margin_usdt,
                    btc_following=btc_following,
                    raw_probability=raw_prob,
                    effective_probability=effective_prob,
                    pattern_sample=pattern_sample,
                )
                opened_candles_orders += 1

        # 1b) Separate liquidation+EMA99 model on top volatility symbols.
        if (not open_paused) and (not entry_hard_blocked) and self.liquid_enabled and self.liquid_predictor is not None:
            for symbol in top_vol_symbols:
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
                required_min_win = self._apply_basic_ml_post_dump_short_min_win(
                    required_min_win=required_min_win,
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                required_min_win = self._apply_basic_ml_post_pump_long_min_win(
                    required_min_win=required_min_win,
                    symbol=symbol,
                    side=side,
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
                if not self._close_profitable_opposite_trades_on_btc_follow_cluster(
                    target_side=side,
                    current_btc_following=self._resolve_btc_following_flag(symbol),
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                    market_prices=market_prices,
                    scan_signal_sides_by_symbol=scan_signal_sides_by_symbol,
                ):
                    continue
                if not self._handle_opposite_signal_on_touch(
                    symbol=symbol,
                    target_side=side,
                    market_price=float(market_price),
                    open_trades_by_symbol=open_trades_by_symbol,
                    closed_trade_ids=closed_trade_ids,
                ):
                    continue

                atr_value = await self._resolve_symbol_atr(symbol)
                atr_for_pct = float(atr_value) if atr_value is not None else 0.0
                atr_pct = (atr_for_pct / float(entry)) * 100 if entry > 0 else 0.0
                leverage = self._resolve_symbol_leverage(symbol, atr_pct)
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
        self._negative_recovery_exit_armed_trade_ids.intersection_update(
            {int(trade.get("id") or 0) for trade in open_trades if int(trade.get("id") or 0) > 0}
        )
        for trade in open_trades:
            try:
                trade_id = int(trade.get("id") or 0)
                if trade_id in closed_trade_ids:
                    continue
                symbol = str(trade["symbol"])
                side = str(trade["side"])
                entry_type = str(trade.get("entry_type") or "LIMIT")
                skip_btc_guards = self._skip_btc_guards_for_entry_type(entry_type)
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

                # Close losing counter-trend positions on BTC 1H reversal (day-toggle capable).
                if not skip_btc_guards:
                    if self._should_force_close_loss_on_btc_reversal(
                        side=side,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        self.repo.close_trade(
                            trade_id=int(trade["id"]),
                            close_price=price,
                            pnl=net_pnl,
                            result=0,
                            close_reason="BTC_1H_REVERSAL_LOSS_EXIT",
                            commission_usdt=commission,
                        )
                        continue

                # Close profitable counter-trend positions when BTC 1H reversal is detected.
                if not skip_btc_guards:
                    if self._should_force_close_profit_on_btc_reversal(
                        symbol=symbol,
                        side=side,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        btc_guard=btc_guard,
                    ):
                        commission = self._calc_fee(entry=entry, quantity=qty, entry_type=entry_type, fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                        net_pnl = pnl - commission
                        reversal_reason = (
                            "BTC_1H_REVERSAL_PROFIT_EXIT"
                            if self._is_countertrend_on_btc_1h_reversal(side=side, btc_guard=btc_guard)
                            else "BTC_REVERSAL_PROFIT_EXIT"
                        )
                        self.repo.close_trade(
                            trade_id=int(trade["id"]),
                            close_price=price,
                            pnl=net_pnl,
                            result=1,
                            close_reason=reversal_reason,
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
                        self.repo.close_trade(
                            trade_id=int(trade["id"]),
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
                        self.repo.close_trade(
                            trade_id=int(trade["id"]),
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
                    self.repo.close_trade(
                        trade_id=int(trade["id"]),
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
                    self.repo.close_trade(
                        trade_id=int(trade["id"]),
                        close_price=price,
                        pnl=net_pnl,
                        result=close_reason,
                        close_reason="TP",
                        commission_usdt=commission,
                    )
                    continue

                rebound_exit_armed = self._arm_negative_recovery_exit(
                    trade_id=trade_id,
                    opened_at=trade.get("opened_at"),
                    pnl_pct=pnl_pct,
                )
                if rebound_exit_armed and pnl_pct > self.negative_recovery_exit_recover_pnl_pct:
                    self._close_trade_now(
                        trade_id=trade_id,
                        close_price=price,
                        pnl=pnl,
                        entry=entry,
                        quantity=qty,
                        entry_type=entry_type,
                        close_reason="NEGATIVE_RECOVERY_EXIT",
                    )
                    continue

                guard_close_reason = self._force_close_guard_reason_for_trade(
                    trade=trade,
                    side=side,
                    entry=entry,
                    stop_loss=sl,
                    pnl_pct=pnl_pct,
                )
                if guard_close_reason:
                    self._close_trade_now(
                        trade_id=int(trade["id"]),
                        close_price=price,
                        pnl=pnl,
                        entry=entry,
                        quantity=qty,
                        entry_type=entry_type,
                        close_reason=guard_close_reason,
                    )
                    continue

                # Timeout policy.
                if not self._is_expired(trade.get("opened_at")):
                    continue

                if pnl > 0:
                    close_reason = 1
                    commission = self._calc_fee(entry=entry, quantity=qty, entry_type=str(trade.get("entry_type") or "LIMIT"), fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                    net_pnl = pnl - commission
                    self.repo.close_trade(
                        trade_id=int(trade["id"]),
                        close_price=price,
                        pnl=net_pnl,
                        result=close_reason,
                        close_reason="TIMEOUT_PROFIT",
                        commission_usdt=commission,
                    )
                    continue

                # If expired but still in loss: move TP to entry and wait for breakeven exit.
                if abs(tp - entry) > max(1e-9, abs(entry) * 1e-8):
                    self.repo.update_take_profit(trade_id=int(trade["id"]), take_profit=entry)

                recovered_to_entry = (side == "LONG" and price >= entry) or (side == "SHORT" and price <= entry)
                if not recovered_to_entry:
                    continue

                close_reason = 1 if pnl >= 0 else 0

                commission = self._calc_fee(entry=entry, quantity=qty, entry_type=str(trade.get("entry_type") or "LIMIT"), fee_taker=self.fee_taker_pct, fee_maker=self.fee_maker_pct)
                net_pnl = pnl - commission
                self.repo.close_trade(
                    trade_id=int(trade["id"]),
                    close_price=price,
                    pnl=net_pnl,
                    result=close_reason,
                    close_reason="TIMEOUT_BREAKEVEN",
                    commission_usdt=commission,
                )
            except Exception as exc:
                trade_id = trade.get("id")
                symbol = trade.get("symbol")
                print(
                    f"[paper-engine] manage trade failed id={trade_id} symbol={symbol}: "
                    f"{type(exc).__name__}: {exc}"
                )

    def _set_stream_open_trade_watch(self, rows: list[dict[str, Any]]) -> None:
        by_symbol: dict[str, list[dict[str, Any]]] = {}
        watched: set[str] = set()
        for row in rows:
            symbol = str(row.get("symbol") or "").strip()
            if not symbol:
                continue
            key = self._normalize_stream_symbol_key(symbol)
            if not key:
                continue
            by_symbol.setdefault(key, []).append(dict(row))
            watched.add(key)
        self._stream_open_trades_by_symbol = by_symbol
        self._watched_stream_symbol_keys = watched
        self._stream_open_trades_refreshed_ts = time.time()

    def _refresh_stream_open_trade_watch(self, force: bool = False) -> None:
        now = time.time()
        if (not force) and (now - self._stream_open_trades_refreshed_ts < self._stream_open_trade_refresh_sec):
            return
        self._set_stream_open_trade_watch(self.repo.list_open_trades())

    def _remove_stream_open_trade(self, trade_id: int, symbol: str) -> None:
        key = self._normalize_stream_symbol_key(symbol)
        rows = list(self._stream_open_trades_by_symbol.get(key, []))
        if not rows:
            return
        kept = [row for row in rows if int(row.get("id") or 0) != int(trade_id)]
        if kept:
            self._stream_open_trades_by_symbol[key] = kept
        else:
            self._stream_open_trades_by_symbol.pop(key, None)
            self._watched_stream_symbol_keys.discard(key)

    def _process_realtime_tp_sl_for_trade(self, trade: dict[str, Any], price: float) -> None:
        trade_id = int(trade.get("id") or 0)
        symbol = str(trade.get("symbol") or "")
        if trade_id <= 0 or not symbol or price <= 0:
            return

        side = str(trade.get("side") or "")
        entry_type = str(trade.get("entry_type") or "LIMIT")
        entry = float(trade.get("entry_price") or 0.0)
        tp = float(trade.get("take_profit") or 0.0)
        sl = float(trade.get("stop_loss") or 0.0)
        qty = float(trade.get("quantity") or 0.0)
        leverage = int(trade.get("leverage") or self.leverage)
        if entry <= 0 or tp <= 0 or sl <= 0 or qty <= 0:
            return

        pnl = self._calc_pnl(side=side, entry=entry, close_price=price, quantity=qty)
        pnl_pct = self._calc_pnl_pct(side=side, entry=entry, mark_price=price, leverage=leverage)
        prev_mae = float(trade.get("mae_pct") or 0.0)
        prev_mfe = float(trade.get("mfe_pct") or 0.0)
        next_mae = min(prev_mae, pnl_pct)
        next_mfe = max(prev_mfe, pnl_pct)
        if (abs(next_mae - prev_mae) > 1e-9) or (abs(next_mfe - prev_mfe) > 1e-9):
            self.repo.update_trade_excursions(trade_id=trade_id, mae_pct=next_mae, mfe_pct=next_mfe)
            trade["mae_pct"] = next_mae
            trade["mfe_pct"] = next_mfe

        move_sl_trigger_pct = self._resolve_move_sl_trigger_pnl_pct(leverage=leverage)
        if (not self.disable_sl) and pnl_pct >= move_sl_trigger_pct:
            lock_pnl_pct = min(self.move_sl_lock_pnl_pct, move_sl_trigger_pct)
            locked_sl = self._calc_locked_profit_sl(
                side=side,
                entry=entry,
                mark_price=price,
                leverage=leverage,
                lock_pnl_pct=lock_pnl_pct,
            )
            if locked_sl is not None:
                if (side == "LONG" and locked_sl > sl) or (side == "SHORT" and locked_sl < sl):
                    self.repo.update_stop_loss(trade_id=trade_id, stop_loss=locked_sl)
                    sl = locked_sl
                    trade["stop_loss"] = locked_sl

        sl_hit = False
        if not self.disable_sl:
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
            self._register_instant_sl_event(
                symbol=symbol,
                side=side,
                opened_at=trade.get("opened_at"),
                pnl_pct=pnl_pct,
                mae_pct=next_mae,
            )
            self.repo.close_trade(
                trade_id=trade_id,
                close_price=price,
                pnl=net_pnl,
                result=result,
                close_reason="SL",
                commission_usdt=commission,
            )
            self._remove_stream_open_trade(trade_id, symbol)
            return

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
            self.repo.close_trade(
                trade_id=trade_id,
                close_price=price,
                pnl=net_pnl,
                result=result,
                close_reason="TP",
                commission_usdt=commission,
            )
            self._remove_stream_open_trade(trade_id, symbol)

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


    def _reentry_cooldown_reason(
        self,
        *,
        symbol: str,
        side: str,
        entry_type: str,
        force_entry_type_scope: bool = False,
    ) -> str | None:
        if (
            self.reentry_cooldown_minutes <= 0
            and self.reentry_after_sl_cooldown_minutes <= 0
            and (not self.instant_sl_guard_enabled)
        ):
            return None

        latest = self.repo.latest_trade(
            symbol=symbol,
            side=side,
            entry_type=None if (self.single_position_per_symbol_side and not force_entry_type_scope) else entry_type,
        )
        if not latest:
            return None

        if str(latest.get("status") or "").upper() == "OPEN":
            return "Open trade exists"

        last_update = self._parse_dt(latest.get("updated_at"))
        if last_update is None:
            last_update = self._parse_dt(latest.get("closed_at"))
        if last_update is None:
            return None

        close_reason = str(latest.get("close_reason") or "").upper()
        cooldown_minutes = self.reentry_cooldown_minutes
        if self._is_sl_close_reason(close_reason):
            cooldown_minutes = max(cooldown_minutes, self.reentry_after_sl_cooldown_minutes)
            if self._is_severe_instant_sl_row(latest):
                cooldown_minutes = max(cooldown_minutes, self.instant_sl_guard_cooldown_minutes)
        if cooldown_minutes <= 0:
            return None

        now = datetime.now(self._vn_tz).replace(tzinfo=None)
        elapsed = max(0.0, (now - last_update).total_seconds())
        remaining_seconds = float(cooldown_minutes * 60) - elapsed
        if remaining_seconds <= 0:
            return None
        remain_minutes = max(1, int(math.ceil(remaining_seconds / 60.0)))
        return f"Reentry cooldown ({remain_minutes}m left)"

    def _is_reentry_cooldown_active(
        self,
        *,
        symbol: str,
        side: str,
        entry_type: str,
        force_entry_type_scope: bool = False,
    ) -> bool:
        return self._reentry_cooldown_reason(
            symbol=symbol,
            side=side,
            entry_type=entry_type,
            force_entry_type_scope=force_entry_type_scope,
        ) is not None

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

    def _instant_sl_guard_reason(self, *, symbol: str, side: str) -> str | None:
        if not self.instant_sl_guard_enabled:
            return None
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
        side_key = str(side or "").upper()
        if side_key == "SHORT" and self._is_short_top_test_rejection(symbol=symbol):
            return None
        remain_minutes = max(1, int(math.ceil((lock_until_ts - now_ts) / 60.0)))
        return f"Instant-SL guard {side_key} ({remain_minutes}m left)"

    def _pass_instant_sl_guard(self, *, symbol: str, side: str) -> bool:
        return self._instant_sl_guard_reason(symbol=symbol, side=side) is None

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
                        wick_range_ok = upper_wick_ratio >= self.instant_sl_guard_short_rejection_min_upper_wick_ratio
                        wick_body_ok = upper_wick >= (
                            body_size * self.instant_sl_guard_short_rejection_min_wick_body_ratio
                        )
                        matched = top_tested and close_back_below_top and bearish_close and wick_range_ok and wick_body_ok
        except Exception:
            matched = False

        self._short_top_test_rejection_cache[key] = (now_ts, bool(matched))
        return bool(matched)

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
        return reason in {"SL", "STOP_LOSS", "MANUAL_FORCE_LOSS"}

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

    @staticmethod
    def _skip_btc_guards_for_entry_type(entry_type: str | None) -> bool:
        normalized_entry_type = str(entry_type or "").strip().upper()
        if normalized_entry_type == "ML_CANDLES_BG":
            return False
        return normalized_entry_type.startswith("ML_CANDLES")

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

        chosen = self._find_hourly_profile_for_now(
            side=side,
            entry_type=entry_type,
            btc_guard=btc_guard,
            min_total_orders=self.hourly_profile_min_samples,
        )
        if not chosen:
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

    def _is_limit_long_blocked_now(self, *, side: str, entry_type: str) -> bool:
        if not self._limit_long_block_hours_set:
            return False
        if str(side or "").upper() != "LONG":
            return False
        if str(entry_type or "").strip().upper() != "LIMIT":
            return False
        hour_vn, _ = self._current_vn_hour_weekday()
        return int(hour_vn) in self._limit_long_block_hours_set

    def _limit_long_block_reason(self, *, side: str, entry_type: str) -> str | None:
        if not self._is_limit_long_blocked_now(side=side, entry_type=entry_type):
            return None
        hour_vn, _ = self._current_vn_hour_weekday()
        return f"LIMIT LONG block hour VN ({hour_vn:02d}h)"

    def _is_candles_bg_blocked_now(self, *, entry_type: str, side: str) -> bool:
        if self._normalize_entry_type_name(entry_type) != self.candles_bg_entry_type:
            return False
        hour_vn, _ = self._current_vn_hour_weekday()
        hour_key = int(hour_vn)
        side_key = str(side or "").upper()
        if hour_key in self._candles_bg_block_hours_set:
            return True
        if side_key == "LONG" and hour_key in self._candles_bg_long_block_hours_set:
            return True
        if side_key == "SHORT" and hour_key in self._candles_bg_short_block_hours_set:
            return True
        return False

    def _candles_bg_block_reason(self, *, entry_type: str, side: str) -> str | None:
        if self._normalize_entry_type_name(entry_type) != self.candles_bg_entry_type:
            return None
        hour_vn, _ = self._current_vn_hour_weekday()
        hour_key = int(hour_vn)
        side_key = str(side or "").upper()
        if hour_key in self._candles_bg_block_hours_set:
            return f"ML_CANDLES_BG hour block VN ({hour_vn:02d}h)"
        if side_key == "LONG" and hour_key in self._candles_bg_long_block_hours_set:
            return f"ML_CANDLES_BG LONG hour block VN ({hour_vn:02d}h)"
        if side_key == "SHORT" and hour_key in self._candles_bg_short_block_hours_set:
            return f"ML_CANDLES_BG SHORT hour block VN ({hour_vn:02d}h)"
        return None

    def _candles_bg_strict_min_win_bonus_for_now(self, *, entry_type: str, side: str) -> float:
        if self._normalize_entry_type_name(entry_type) != self.candles_bg_entry_type:
            return 0.0
        hour_vn, _ = self._current_vn_hour_weekday()
        hour_key = int(hour_vn)
        side_key = str(side or "").upper()
        if side_key == "LONG" and hour_key in self._candles_bg_long_strict_hours_set:
            return float(self.candles_bg_strict_min_win_bonus)
        if side_key == "SHORT" and hour_key in self._candles_bg_short_strict_hours_set:
            return float(self.candles_bg_strict_min_win_bonus)
        return 0.0

    @staticmethod
    def _normalize_entry_type_name(entry_type: str | None) -> str:
        return str(entry_type or "").strip().upper()

    def _is_basic_ml_entry_type(self, entry_type: str | None) -> bool:
        return self._normalize_entry_type_name(entry_type) == "LIMIT"

    @staticmethod
    def _coerce_pattern_sample(raw: object) -> dict[str, Any] | None:
        if isinstance(raw, dict):
            return dict(raw)
        return None

    @staticmethod
    def _pattern_sample_win_rate_pct(sample: dict[str, Any] | None) -> float:
        if not isinstance(sample, dict):
            return 0.0
        try:
            return float(sample.get("win_rate_pct") or 0.0)
        except Exception:
            return 0.0

    @staticmethod
    def _pattern_sample_quality_tier(sample: dict[str, Any] | None) -> str:
        if not isinstance(sample, dict):
            return ""
        return str(sample.get("quality_tier") or "").strip().upper()

    @classmethod
    def _pattern_sample_is_ab_100(cls, sample: dict[str, Any] | None) -> bool:
        tier = cls._pattern_sample_quality_tier(sample)
        win_rate_pct = cls._pattern_sample_win_rate_pct(sample)
        return tier in {"A", "B"} and win_rate_pct >= 100.0

    @classmethod
    def _pattern_sample_should_block_c(cls, sample: dict[str, Any] | None) -> bool:
        tier = cls._pattern_sample_quality_tier(sample)
        win_rate_pct = cls._pattern_sample_win_rate_pct(sample)
        return tier == "C" and win_rate_pct < 90.0

    def _resolve_ml_pattern_sample(
        self,
        *,
        symbol: str,
        side: str,
        candle_pattern_sample: dict[str, Any] | None = None,
        feature_snapshot: dict[str, float] | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, float] | None]:
        sample = self._coerce_pattern_sample(candle_pattern_sample)
        snapshot = feature_snapshot
        if snapshot is None:
            snapshot = self._capture_feature_snapshot(symbol, side)
        if snapshot is not None:
            rematched_sample = signal_candle_pattern_service.match_signal(
                signal_source="ML",
                side=side,
                feature_snapshot=snapshot,
            )
            rematched_sample = self._coerce_pattern_sample(rematched_sample)
            if rematched_sample is not None:
                sample = rematched_sample
        return sample, snapshot

    @classmethod
    def _resolve_ml_pattern_leverage_override(cls, sample: dict[str, Any] | None) -> int | None:
        if cls._pattern_sample_is_ab_100(sample):
            return 10
        return None

    def _evaluate_basic_ml_pattern_gate(
        self,
        *,
        symbol: str,
        side: str,
        candle_pattern_sample: dict[str, Any] | None = None,
        feature_snapshot: dict[str, float] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None, dict[str, float] | None]:
        sample, snapshot = self._resolve_ml_pattern_sample(
            symbol=symbol,
            side=side,
            candle_pattern_sample=candle_pattern_sample,
            feature_snapshot=feature_snapshot,
        )
        if not self.basic_ml_pattern_gate_enabled:
            return None, sample, snapshot

        if sample is None:
            return None, None, snapshot

        if not self._pattern_sample_should_block_c(sample):
            return None, sample, snapshot

        sample_name = str(sample.get("sample_code") or sample.get("sample_key") or "pattern").strip() or "pattern"
        return f"Basic blocked {sample_name} tier C", sample, snapshot

    @staticmethod
    def _format_notification_price(value: float | None) -> str:
        if value is None:
            return "-"
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "-"
        if not math.isfinite(numeric):
            return "-"
        text = f"{numeric:.10f}".rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _format_notification_pct(value: float | None) -> str:
        if value is None:
            return "-"
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return "-"
        if not math.isfinite(numeric):
            return "-"
        return f"{numeric:+.2f}%"

    @staticmethod
    def _resolve_margin_move_pct(*, side: str, entry: float, price: float, leverage: int) -> float | None:
        if entry <= 0 or price <= 0 or leverage <= 0:
            return None
        if str(side or "").upper() == "LONG":
            raw_pct = ((price - entry) / entry) * 100.0
        else:
            raw_pct = ((entry - price) / entry) * 100.0
        return raw_pct * float(leverage)

    def _build_pattern_summary(self, sample: dict[str, Any] | None) -> str:
        sample_obj = self._coerce_pattern_sample(sample)
        if not sample_obj:
            return "-"
        sample_name = str(sample_obj.get("sample_code") or sample_obj.get("sample_name") or "-").strip() or "-"
        tier = self._pattern_sample_quality_tier(sample_obj) or "-"
        win_rate = self._pattern_sample_win_rate_pct(sample_obj)
        notes = str(sample_obj.get("sample_notes") or "").strip()
        parts = [f"{sample_name} {tier}"]
        if math.isfinite(win_rate):
            parts.append(f"{win_rate:.1f}%")
        if notes:
            parts.append(notes)
        return " | ".join(parts)

    async def _send_ml_candles_bg_open_notification(
        self,
        *,
        trade_id: int,
        symbol: str,
        side: str,
        entry: float,
        take_profit: float,
        stop_loss: float,
        leverage: int,
        margin_usdt: float,
        btc_following: bool,
        raw_probability: float,
        effective_probability: float,
        pattern_sample: dict[str, Any] | None,
    ) -> None:
        notifier = self.candles_bg_discord_notifier
        if not notifier.is_enabled:
            return
        tp_margin_pct = self._resolve_margin_move_pct(
            side=side,
            entry=entry,
            price=take_profit,
            leverage=leverage,
        )
        sl_margin_pct = self._resolve_margin_move_pct(
            side=side,
            entry=entry,
            price=stop_loss,
            leverage=leverage,
        )
        pattern_summary = self._build_pattern_summary(pattern_sample)
        now_vn = datetime.now(self._vn_tz).strftime("%Y-%m-%d %H:%M:%S ICT")
        embed = {
            "title": f"ML_CANDLES_BG {str(side or '').upper()} filled: {symbol}",
            "color": 5763719 if str(side or '').upper() == "LONG" else 15548997,
            "fields": [
                {"name": "Trade ID", "value": str(trade_id), "inline": True},
                {"name": "Side", "value": str(side or "-").upper(), "inline": True},
                {"name": "BTC Follow", "value": "YES" if btc_following else "NO", "inline": True},
                {"name": "Entry", "value": self._format_notification_price(entry), "inline": True},
                {"name": "TP", "value": f"{self._format_notification_price(take_profit)} ({self._format_notification_pct(tp_margin_pct)})", "inline": True},
                {"name": "SL", "value": f"{self._format_notification_price(stop_loss)} ({self._format_notification_pct(sl_margin_pct)})", "inline": True},
                {"name": "Leverage", "value": f"{int(leverage)}x", "inline": True},
                {"name": "Margin", "value": f"{float(margin_usdt):.2f}", "inline": True},
                {"name": "Win Prob", "value": f"raw {float(raw_probability):.4f} | eff {float(effective_probability):.4f}", "inline": True},
                {"name": "Pattern", "value": pattern_summary[:1024] or "-", "inline": False},
                {"name": "Opened At", "value": now_vn, "inline": False},
            ],
        }
        await asyncio.to_thread(notifier.send, embeds=[embed])

    def _enqueue_ml_candles_bg_open_notification(
        self,
        *,
        trade_id: int,
        symbol: str,
        side: str,
        entry: float,
        take_profit: float,
        stop_loss: float,
        leverage: int,
        margin_usdt: float,
        btc_following: bool,
        raw_probability: float,
        effective_probability: float,
        pattern_sample: dict[str, Any] | None,
    ) -> None:
        notifier = self.candles_bg_discord_notifier
        if not notifier.is_enabled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(
            self._send_ml_candles_bg_open_notification(
                trade_id=trade_id,
                symbol=symbol,
                side=side,
                entry=entry,
                take_profit=take_profit,
                stop_loss=stop_loss,
                leverage=leverage,
                margin_usdt=margin_usdt,
                btc_following=btc_following,
                raw_probability=raw_probability,
                effective_probability=effective_probability,
                pattern_sample=pattern_sample,
            )
        )
        task.add_done_callback(lambda task: self._log_notification_task_error(task, symbol=symbol))

    @staticmethod
    def _log_notification_task_error(task: asyncio.Task[Any], *, symbol: str) -> None:
        try:
            task.result()
        except Exception as exc:
            logger.warning("ML_CANDLES_BG Discord notification failed for %s: %s", symbol, exc)

    def _evaluate_ml_candles_bg_pattern_gate(
        self,
        *,
        symbol: str,
        side: str,
        feature_snapshot: dict[str, float] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None, dict[str, float] | None]:
        sample, snapshot = self._resolve_ml_pattern_sample(
            symbol=symbol,
            side=side,
            feature_snapshot=feature_snapshot,
        )
        if sample is None:
            return None, None, snapshot

        if not self._pattern_sample_should_block_c(sample):
            return None, sample, snapshot

        sample_name = str(sample.get("sample_code") or sample.get("sample_key") or "pattern").strip() or "pattern"
        return f"ML_CANDLES_BG blocked {sample_name} tier C", sample, snapshot

    def _is_guard_target_entry_type(self, entry_type: str | None) -> bool:
        normalized_entry_type = self._normalize_entry_type_name(entry_type)
        return (
            normalized_entry_type == "LIMIT"
            or normalized_entry_type == "ML_TEST"
            or normalized_entry_type.startswith("ML_CANDLES")
        )

    def _coerce_vn_datetime(self, value: object) -> datetime | None:
        dt = self._parse_dt(value)
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=self._vn_tz)
        return dt.astimezone(self._vn_tz)

    def _trade_age_minutes(self, opened_at: object) -> float:
        dt = self._coerce_vn_datetime(opened_at)
        if dt is None:
            return 0.0
        return max(0.0, (datetime.now(self._vn_tz) - dt).total_seconds() / 60.0)

    def _arm_negative_recovery_exit(self, *, trade_id: int, opened_at: object, pnl_pct: float) -> bool:
        if not self.negative_recovery_exit_enabled or trade_id <= 0:
            return False
        if trade_id in self._negative_recovery_exit_armed_trade_ids:
            return True
        trade_age_minutes = self._trade_age_minutes(opened_at)
        if trade_age_minutes < float(self.negative_recovery_exit_arm_after_minutes):
            return False
        if pnl_pct <= float(self.negative_recovery_exit_negative_pnl_pct):
            self._negative_recovery_exit_armed_trade_ids.add(trade_id)
            return True
        return False

    @staticmethod
    def _is_minute_window_active(now_minute: int, start_minute: int, end_minute: int) -> bool:
        if start_minute <= end_minute:
            return start_minute <= now_minute <= end_minute
        return now_minute >= start_minute or now_minute <= end_minute

    def _is_hourly_transition_entry_block_window(self, now_vn: datetime) -> bool:
        if not self.hourly_transition_guard_enabled:
            return False
        before_minutes = int(self.hourly_transition_entry_block_before_minutes)
        after_minutes = int(self.hourly_transition_entry_block_after_minutes)
        start_minute = (60 - before_minutes) % 60
        end_minute = max(0, min(59, after_minutes))
        return self._is_minute_window_active(int(now_vn.minute), start_minute, end_minute)

    def _is_hourly_transition_force_close_window(self, now_vn: datetime) -> bool:
        if not self.hourly_transition_guard_enabled:
            return False
        return self._is_minute_window_active(
            int(now_vn.minute),
            int(self.hourly_transition_start_minute),
            int(self.hourly_transition_force_close_end_minute),
        )

    def _iter_funding_windows_vn(self, now_vn: datetime) -> list[datetime]:
        markers: list[datetime] = []
        for day_offset in (-1, 0, 1):
            base_day = (now_vn + timedelta(days=day_offset)).date()
            for hour_vn in (7, 15, 23):
                markers.append(
                    datetime(
                        base_day.year,
                        base_day.month,
                        base_day.day,
                        hour_vn,
                        0,
                        tzinfo=self._vn_tz,
                    )
                )
        return markers

    def _find_active_funding_guard_window(
        self,
        *,
        now_vn: datetime,
        before_minutes: int,
        after_minutes: int,
    ) -> dict[str, Any] | None:
        if not self.funding_guard_enabled:
            return None
        best_match: tuple[float, datetime] | None = None
        for event_dt in self._iter_funding_windows_vn(now_vn):
            delta_minutes = (now_vn - event_dt).total_seconds() / 60.0
            if delta_minutes < (-1.0 * float(before_minutes)) or delta_minutes > float(after_minutes):
                continue
            distance = abs(delta_minutes)
            if best_match is None or distance < best_match[0]:
                best_match = (distance, event_dt)
        if best_match is None:
            return None
        return {
            "label": f"Funding {best_match[1].strftime('%H:%M')} VN",
            "close_reason": "FUNDING_GUARD",
            "event_dt": best_match[1],
        }

    def _iter_session_open_windows_vn(self, now_vn: datetime) -> list[tuple[str, datetime]]:
        out: list[tuple[str, datetime]] = []
        for session_name in sorted(self.session_open_guard_sessions):
            zone = self._session_guard_timezones.get(session_name)
            spec = self._session_guard_specs.get(session_name)
            if zone is None or spec is None:
                continue
            local_now = now_vn.astimezone(zone)
            for day_offset in (-1, 0, 1):
                local_day = (local_now + timedelta(days=day_offset)).date()
                session_dt_local = datetime(
                    local_day.year,
                    local_day.month,
                    local_day.day,
                    int(spec["hour"]),
                    int(spec["minute"]),
                    tzinfo=zone,
                )
                out.append((session_name, session_dt_local.astimezone(self._vn_tz)))
        return out

    def _find_active_session_open_guard_window(
        self,
        *,
        now_vn: datetime,
        before_minutes: int,
        after_minutes: int,
    ) -> dict[str, Any] | None:
        if not self.session_open_guard_enabled or not self.session_open_guard_sessions:
            return None
        best_match: tuple[float, str, datetime] | None = None
        for session_name, event_dt in self._iter_session_open_windows_vn(now_vn):
            delta_minutes = (now_vn - event_dt).total_seconds() / 60.0
            if delta_minutes < (-1.0 * float(before_minutes)) or delta_minutes > float(after_minutes):
                continue
            distance = abs(delta_minutes)
            if best_match is None or distance < best_match[0]:
                best_match = (distance, session_name, event_dt)
        if best_match is None:
            return None
        return {
            "session": best_match[1],
            "event_dt": best_match[2],
            "close_reason": f"SESSION_OPEN_GUARD_{best_match[1]}",
        }

    def _macro_event_keyword_for_row(self, row: dict[str, Any]) -> str | None:
        if not self.macro_event_guard_keywords:
            return None
        haystack = " ".join(
            [
                str(row.get("title") or ""),
                str(row.get("category") or ""),
                str(row.get("note") or ""),
            ]
        ).upper()
        for keyword in self.macro_event_guard_keywords:
            if keyword and keyword in haystack:
                return keyword
        return None

    def _load_macro_event_guard_rows(
        self,
        *,
        now_vn: datetime,
        lookback_minutes: int,
        lookahead_minutes: int,
    ) -> list[dict[str, Any]]:
        if not self.macro_event_guard_enabled or not self.macro_event_guard_keywords:
            return []
        cache_key = f"{int(lookback_minutes)}:{int(lookahead_minutes)}"
        cached = self._macro_event_guard_cache.get(cache_key)
        now_ts = time.time()
        if cached is not None and (now_ts - float(cached[0])) <= 30.0:
            return list(cached[1])
        try:
            rows = self.repo.list_market_event_windows(
                starts_from=now_vn - timedelta(minutes=lookback_minutes),
                ends_to=now_vn + timedelta(minutes=lookahead_minutes),
                active_only=True,
                limit=128,
            )
        except Exception as exc:
            print(f"[paper-engine] macro event guard load failed: {type(exc).__name__}: {exc}")
            rows = []
        filtered = [row for row in rows if self._macro_event_keyword_for_row(row) is not None]
        self._macro_event_guard_cache[cache_key] = (now_ts, list(filtered))
        return filtered

    def _find_active_macro_event_guard(
        self,
        *,
        now_vn: datetime,
        before_minutes: int,
        after_minutes: int,
    ) -> dict[str, Any] | None:
        rows = self._load_macro_event_guard_rows(
            now_vn=now_vn,
            lookback_minutes=after_minutes,
            lookahead_minutes=before_minutes,
        )
        best_match: tuple[float, str, str] | None = None
        for row in rows:
            starts_at = self._coerce_vn_datetime(row.get("starts_at"))
            ends_at = self._coerce_vn_datetime(row.get("ends_at"))
            keyword = self._macro_event_keyword_for_row(row)
            if starts_at is None or ends_at is None or keyword is None:
                continue
            guard_start = starts_at - timedelta(minutes=before_minutes)
            guard_end = ends_at + timedelta(minutes=after_minutes)
            if now_vn < guard_start or now_vn > guard_end:
                continue
            distance = min(
                abs((now_vn - starts_at).total_seconds()),
                abs((now_vn - ends_at).total_seconds()),
            )
            title = str(row.get("title") or keyword).strip() or keyword
            if best_match is None or distance < best_match[0]:
                best_match = (distance, keyword, title)
        if best_match is None:
            return None
        return {
            "keyword": best_match[1],
            "title": best_match[2],
            "close_reason": f"MACRO_EVENT_GUARD_{best_match[1]}",
        }

    def _has_locked_profit_stop(self, *, side: str, entry: float, stop_loss: float) -> bool:
        if self.disable_sl or entry <= 0 or stop_loss <= 0:
            return False
        tolerance = max(1e-9, abs(entry) * 1e-8)
        side_key = str(side or "").upper()
        if side_key == "LONG":
            return stop_loss >= (entry - tolerance)
        if side_key == "SHORT":
            return stop_loss <= (entry + tolerance)
        return False

    def _guard_entry_block_reason(self, *, entry_type: str) -> str | None:
        if not self._is_guard_target_entry_type(entry_type):
            return None
        now_vn = datetime.now(self._vn_tz)
        if self._is_hourly_transition_entry_block_window(now_vn):
            return f"Hourly transition guard ({now_vn.strftime('%H:%M')} VN)"
        funding_match = self._find_active_funding_guard_window(
            now_vn=now_vn,
            before_minutes=self.funding_guard_entry_block_before_minutes,
            after_minutes=self.funding_guard_entry_block_after_minutes,
        )
        if funding_match is not None:
            return f"{funding_match['label']} entry block"
        session_match = self._find_active_session_open_guard_window(
            now_vn=now_vn,
            before_minutes=self.session_open_guard_entry_block_before_minutes,
            after_minutes=self.session_open_guard_entry_block_after_minutes,
        )
        if session_match is not None:
            session_name = str(session_match.get("session") or "SESSION")
            event_dt = session_match.get("event_dt")
            event_label = event_dt.strftime('%H:%M') if isinstance(event_dt, datetime) else 'session-open'
            return f"Session open guard {session_name} ({event_label} VN)"
        macro_match = self._find_active_macro_event_guard(
            now_vn=now_vn,
            before_minutes=self.macro_event_guard_entry_block_before_minutes,
            after_minutes=self.macro_event_guard_entry_block_after_minutes,
        )
        if macro_match is not None:
            return f"Macro event guard {macro_match['keyword']} ({macro_match['title']})"
        return None

    def _force_close_guard_reason_for_trade(
        self,
        *,
        trade: dict[str, Any],
        side: str,
        entry: float,
        stop_loss: float,
        pnl_pct: float,
    ) -> str | None:
        entry_type = self._normalize_entry_type_name(trade.get("entry_type"))
        if not self._is_guard_target_entry_type(entry_type):
            return None
        if self._has_locked_profit_stop(side=side, entry=entry, stop_loss=stop_loss):
            return None
        now_vn = datetime.now(self._vn_tz)
        trade_age_minutes = self._trade_age_minutes(trade.get("opened_at"))
        if self._is_hourly_transition_force_close_window(now_vn):
            if (
                trade_age_minutes >= float(self.hourly_transition_min_hold_minutes)
                and pnl_pct > 0.1
            ):
                return "HOURLY_TRANSITION_GUARD"
        funding_match = self._find_active_funding_guard_window(
            now_vn=now_vn,
            before_minutes=self.funding_guard_force_close_before_minutes,
            after_minutes=self.funding_guard_force_close_after_minutes,
        )
        if funding_match is not None:
            if (
                trade_age_minutes >= float(self.funding_guard_min_hold_minutes)
                and pnl_pct > 0.0
                and pnl_pct < float(self.funding_guard_safe_pnl_pct)
            ):
                return str(funding_match["close_reason"])
        session_match = self._find_active_session_open_guard_window(
            now_vn=now_vn,
            before_minutes=self.session_open_guard_force_close_before_minutes,
            after_minutes=self.session_open_guard_force_close_after_minutes,
        )
        if session_match is not None:
            if (
                trade_age_minutes >= float(self.session_open_guard_min_hold_minutes)
                and pnl_pct > 0.0
                and pnl_pct < float(self.session_open_guard_safe_pnl_pct)
            ):
                return str(session_match["close_reason"])
        macro_match = self._find_active_macro_event_guard(
            now_vn=now_vn,
            before_minutes=self.macro_event_guard_force_close_before_minutes,
            after_minutes=self.macro_event_guard_force_close_after_minutes,
        )
        if macro_match is not None:
            if (
                trade_age_minutes >= float(self.macro_event_guard_min_hold_minutes)
                and pnl_pct > 0.0
                and pnl_pct < float(self.macro_event_guard_safe_pnl_pct)
            ):
                return str(macro_match["close_reason"])
        return None

    def _close_trade_now(
        self,
        *,
        trade_id: int,
        close_price: float,
        pnl: float,
        entry: float,
        quantity: float,
        entry_type: str,
        close_reason: str,
    ) -> None:
        commission = self._calc_fee(
            entry=entry,
            quantity=quantity,
            entry_type=entry_type,
            fee_taker=self.fee_taker_pct,
            fee_maker=self.fee_maker_pct,
        )
        net_pnl = pnl - commission
        self.repo.close_trade(
            trade_id=trade_id,
            close_price=close_price,
            pnl=net_pnl,
            result=1 if pnl >= 0 else 0,
            close_reason=close_reason,
            commission_usdt=commission,
        )

    def _entry_block_reason(
        self,
        *,
        symbol: str,
        side: str,
        entry_type: str,
        btc_guard: dict[str, Any] | None = None,
    ) -> str | None:
        del symbol
        hard_block_reason = self._entry_hard_block_reason()
        if hard_block_reason:
            return hard_block_reason
        limit_long_block_reason = self._limit_long_block_reason(side=side, entry_type=entry_type)
        if limit_long_block_reason:
            return limit_long_block_reason
        btc_reversal_reason = self._btc_reversal_entry_block_reason(side=side, btc_guard=btc_guard)
        if btc_reversal_reason:
            return btc_reversal_reason
        return self._guard_entry_block_reason(entry_type=entry_type)

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
        min_total_orders: int | None = None,
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
        fallback_match: dict[str, Any] | None = None
        for scope in scope_candidates:
            for key in (side_key, "ALL"):
                chosen = self._hourly_profiles_cache.get(scope, {}).get(key, {}).get(hour_vn)
                if chosen is None:
                    continue
                if fallback_match is None:
                    fallback_match = chosen
                if min_total_orders is None:
                    return chosen
                total_orders = int(chosen.get("total_orders") or 0)
                if total_orders >= int(min_total_orders):
                    return chosen
        if min_total_orders is None:
            return fallback_match
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

        profile = self._find_hourly_profile_for_now(
            side=side,
            entry_type=entry_type,
            btc_guard=btc_guard,
            min_total_orders=self.hourly_bad_window_min_samples,
        )
        if not profile:
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

    @staticmethod
    def _coerce_bool_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        if isinstance(value, (int, float)):
            return float(value) != 0.0
        return str(value).strip().lower() in {"1", "true", "yes", "y", "t", "on"}

    def _register_active_signal_side(
        self,
        active_sides_by_symbol: dict[str, set[str]],
        *,
        symbol: str,
        side: str,
    ) -> None:
        key = self._normalize_symbol_key(symbol)
        side_key = str(side or "").upper()
        if not key or side_key not in {"LONG", "SHORT"}:
            return
        active_sides_by_symbol.setdefault(key, set()).add(side_key)

    def _trade_has_active_same_side_signal(
        self,
        *,
        trade: dict[str, Any],
        market_prices: dict[str, float],
        scan_signal_sides_by_symbol: dict[str, set[str]] | None = None,
    ) -> bool:
        symbol = str(trade.get("symbol") or "").strip()
        side_key = str(trade.get("side") or "").upper()
        if not symbol or side_key not in {"LONG", "SHORT"}:
            return False

        symbol_key = self._normalize_symbol_key(symbol)
        active_scan_sides = (scan_signal_sides_by_symbol or {}).get(symbol_key, set())
        if side_key in active_scan_sides:
            return True

        entry_type = self._normalize_entry_type_name(trade.get("entry_type"))
        market_price = market_prices.get(symbol)
        if market_price is None:
            market_price = self._resolve_market_price(symbol)
        if market_price is None or float(market_price) <= 0:
            return False

        try:
            if entry_type == "ML_TEST" and self.predictor_test is not None:
                signal = self.predictor_test.predict(symbol, float(market_price))
                return (
                    float(getattr(signal, "win_probability", 0.0)) >= self.test_ml_min_win_probability
                    and str(getattr(signal, "side", "")).upper() == side_key
                )
            if entry_type == self.candles_bg_entry_type and self.predictor_candles is not None:
                signal = self.predictor_candles.predict(symbol, float(market_price))
                return (
                    float(getattr(signal, "win_probability", 0.0)) >= self.candles_bg_min_win_probability
                    and str(getattr(signal, "side", "")).upper() == side_key
                )
            if entry_type == "LIQ_EMA99" and self.liquid_predictor is not None:
                signal = self.liquid_predictor.predict(symbol, float(market_price))
                return (
                    float(getattr(signal, "win_probability", 0.0)) >= self.liquid_min_win_probability
                    and str(getattr(signal, "side", "")).upper() == side_key
                )
        except Exception:
            return False
        return False

    def _close_profitable_opposite_trades_on_btc_follow_cluster(
        self,
        *,
        target_side: str,
        current_btc_following: bool | None,
        open_trades_by_symbol: dict[str, list[dict[str, Any]]],
        closed_trade_ids: set[int],
        market_prices: dict[str, float],
        scan_signal_sides_by_symbol: dict[str, set[str]] | None = None,
    ) -> bool:
        if current_btc_following is not True:
            return True

        target_side_key = str(target_side or "").upper()
        if target_side_key not in {"LONG", "SHORT"}:
            return True

        btc_follow_count = 1
        for bucket in open_trades_by_symbol.values():
            for row in bucket:
                trade_id = int(row.get("id") or 0)
                if trade_id in closed_trade_ids:
                    continue
                if str(row.get("status") or "OPEN").upper() != "OPEN":
                    continue
                if str(row.get("side") or "").upper() != target_side_key:
                    continue
                if not self._coerce_bool_flag(row.get("btc_following")):
                    continue
                btc_follow_count += 1

        if btc_follow_count < 5:
            return True

        resolved_prices = dict(market_prices)
        close_items: list[tuple[int, float, float, float, str, float]] = []
        for bucket in open_trades_by_symbol.values():
            for row in bucket:
                trade_id = int(row.get("id") or 0)
                if trade_id <= 0 or trade_id in closed_trade_ids:
                    continue
                if str(row.get("status") or "OPEN").upper() != "OPEN":
                    continue
                side = str(row.get("side") or "").upper()
                if side not in {"LONG", "SHORT"} or side == target_side_key:
                    continue
                if self._trade_has_active_same_side_signal(
                    trade=row,
                    market_prices=resolved_prices,
                    scan_signal_sides_by_symbol=scan_signal_sides_by_symbol,
                ):
                    continue

                symbol = str(row.get("symbol") or "")
                entry_type = str(row.get("entry_type") or "LIMIT")
                try:
                    entry = float(row.get("entry_price") or 0.0)
                    quantity = float(row.get("quantity") or 0.0)
                except Exception:
                    continue
                if not symbol or entry <= 0 or quantity <= 0:
                    continue

                close_price = resolved_prices.get(symbol)
                if close_price is None:
                    close_price = self._resolve_market_price(symbol)
                    if close_price is not None:
                        resolved_prices[symbol] = float(close_price)
                if close_price is None or float(close_price) <= 0:
                    continue

                pnl = self._calc_pnl(
                    side=side,
                    entry=entry,
                    close_price=float(close_price),
                    quantity=quantity,
                )
                if pnl <= 0.1:
                    continue
                close_items.append((trade_id, float(close_price), float(pnl), entry, entry_type, quantity))

        for trade_id, close_price, pnl, entry, entry_type, quantity in close_items:
            try:
                self._close_trade_now(
                    trade_id=trade_id,
                    close_price=close_price,
                    pnl=pnl,
                    entry=entry,
                    quantity=quantity,
                    entry_type=entry_type,
                    close_reason="BTC_FOLLOW_OPPOSITE_CLUSTER_EXIT",
                )
                closed_trade_ids.add(trade_id)
            except Exception:
                continue

        if not close_items:
            return True

        for key, bucket in list(open_trades_by_symbol.items()):
            open_trades_by_symbol[key] = [
                row for row in bucket
                if int(row.get("id") or 0) not in closed_trade_ids
            ]
        return True

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
            close_items.append((trade_id, float(pnl), commission))

        for trade_id, pnl, commission in close_items:
            try:
                net_pnl = pnl - commission
                self.repo.close_trade(
                    trade_id=trade_id,
                    close_price=float(market_price),
                    pnl=net_pnl,
                    result=1,
                    close_reason="OPPOSITE_SIGNAL_FLIP",
                    commission_usdt=commission,
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
            "trend_1h_side": "NEUTRAL",
            "trend_1h_confidence": 0.0,
            "trend_1h_score": 0.0,
            "trend_1h_prev_side": "NEUTRAL",
            "trend_1h_reversal": False,
            "kill_short_15m_active": False,
            "kill_short_15m_confirmation_ready": False,
            "kill_short_15m_entry_buffer_pct": 0.0,
            "basic_ml_15m_cluster_side": "NEUTRAL",
            "basic_ml_15m_cluster_score": 0.0,
            "basic_ml_15m_last_candle_side": "NEUTRAL",
            "basic_ml_15m_long_confirmation_ready": False,
            "basic_ml_15m_short_confirmation_ready": False,
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
                trend_side, confidence, score, ema_fast, ema_slow = self._resolve_trend_signal(closes)
                rsi_15m = self._rsi_last(closes, period=14)
                rsi_1h = rsi_15m
                trend_1h_side = "NEUTRAL"
                trend_1h_confidence = 0.0
                trend_1h_score = 0.0
                trend_1h_prev_side = "NEUTRAL"
                trend_1h_reversal = False
                try:
                    rows_1h = self.market_client.fetch_ohlcv(
                        symbol="BTC/USDT",
                        timeframe="1h",
                        limit=220,
                    )
                    closes_1h = [float(row[4]) for row in rows_1h if len(row) >= 5]
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
                except Exception:
                    pass

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

                kill_short_15m = self._analyze_btc_kill_short_15m(rows)
                basic_ml_candle_confirm = self._analyze_basic_ml_btc_confirmation_15m(rows)
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
                    "trend_1h_side": trend_1h_side,
                    "trend_1h_confidence": float(trend_1h_confidence),
                    "trend_1h_score": float(trend_1h_score),
                    "trend_1h_prev_side": trend_1h_prev_side,
                    "trend_1h_reversal": bool(trend_1h_reversal),
                    "kill_short_15m_active": bool(kill_short_15m.get("active")),
                    "kill_short_15m_confirmation_ready": bool(kill_short_15m.get("confirmation_ready")),
                    "kill_short_15m_entry_buffer_pct": float(kill_short_15m.get("entry_buffer_pct") or 0.0),
                    "basic_ml_15m_cluster_side": str(basic_ml_candle_confirm.get("cluster_side") or "NEUTRAL").upper(),
                    "basic_ml_15m_cluster_score": float(basic_ml_candle_confirm.get("cluster_score") or 0.0),
                    "basic_ml_15m_last_candle_side": str(basic_ml_candle_confirm.get("last_candle_side") or "NEUTRAL").upper(),
                    "basic_ml_15m_long_confirmation_ready": bool(basic_ml_candle_confirm.get("long_confirmation_ready")),
                    "basic_ml_15m_short_confirmation_ready": bool(basic_ml_candle_confirm.get("short_confirmation_ready")),
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

    def _pass_btc_filter(self, symbol: str, side: str, effective_prob: float, btc_guard: dict[str, Any]) -> bool:
        if not self._pass_btc_trend_hour_lock(symbol=symbol, side=side):
            return False
        if self._btc_kill_short_guard_reason(side=side, btc_guard=btc_guard):
            return False
        if not self._pass_btc_shock_directional_guard(symbol=symbol, side=side, btc_guard=btc_guard):
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
            return
        if self.btc_trend_hour_lock_countertrend_hours <= 0:
            return

        trend_side = str((btc_guard or {}).get("side") or "NEUTRAL").upper()
        if trend_side not in {"LONG", "SHORT"}:
            return
        try:
            confidence = float((btc_guard or {}).get("confidence") or 0.0)
        except Exception:
            confidence = 0.0
        if confidence < self.btc_trend_hour_lock_min_confidence:
            return

        now_ts = time.time()
        lock_until_ts = now_ts + (self.btc_trend_hour_lock_countertrend_hours * 3600.0)

        if trend_side == "LONG":
            # Keep one shared timer for the active BTC regime only.
            if (
                self._btc_trend_hour_lock_trend_side == "LONG"
                and self._btc_long_regime_short_lock_until_ts > now_ts
            ):
                return
            self._btc_long_regime_short_lock_until_ts = lock_until_ts
            self._btc_short_regime_long_lock_until_ts = 0.0
            self._btc_trend_hour_lock_trend_side = "LONG"
            return

        if (
            self._btc_trend_hour_lock_trend_side == "SHORT"
            and self._btc_short_regime_long_lock_until_ts > now_ts
        ):
            return
        self._btc_short_regime_long_lock_until_ts = lock_until_ts
        self._btc_long_regime_short_lock_until_ts = 0.0
        self._btc_trend_hour_lock_trend_side = "SHORT"

    def _resolve_btc_trend_hour_lock_until(self, side: str) -> float:
        side_key = str(side or "").upper()
        if side_key == "SHORT":
            return float(self._btc_long_regime_short_lock_until_ts)
        if side_key == "LONG":
            return float(self._btc_short_regime_long_lock_until_ts)
        return 0.0

    def _pass_btc_trend_hour_lock(self, *, symbol: str, side: str) -> bool:
        if not self.btc_trend_hour_lock_enabled:
            return True
        if self.btc_trend_hour_lock_countertrend_hours <= 0:
            return True
        if not self.btc_trend_hour_lock_apply_non_btc_follow and (not self._is_symbol_following_btc(symbol)):
            return True

        lock_until_ts = self._resolve_btc_trend_hour_lock_until(side=side)
        if lock_until_ts <= 0:
            return True
        now_ts = time.time()
        if now_ts >= lock_until_ts:
            side_key = str(side or "").upper()
            if side_key == "SHORT":
                self._btc_long_regime_short_lock_until_ts = 0.0
                if self._btc_trend_hour_lock_trend_side == "LONG":
                    self._btc_trend_hour_lock_trend_side = "NEUTRAL"
            elif side_key == "LONG":
                self._btc_short_regime_long_lock_until_ts = 0.0
                if self._btc_trend_hour_lock_trend_side == "SHORT":
                    self._btc_trend_hour_lock_trend_side = "NEUTRAL"
            return True
        return False

    def _btc_trend_hour_lock_reason(self, *, symbol: str, side: str, btc_guard: dict[str, Any] | None = None) -> str | None:
        if btc_guard is not None:
            self._apply_btc_trend_hour_lock(btc_guard)
        if self._pass_btc_trend_hour_lock(symbol=symbol, side=side):
            return None

        lock_until_ts = self._resolve_btc_trend_hour_lock_until(side=side)
        now_ts = time.time()
        remain_minutes = max(1, int(math.ceil((lock_until_ts - now_ts) / 60.0)))
        side_key = str(side or "").upper()
        if side_key == "SHORT":
            return f"BTC bullish lock SHORT ({remain_minutes}m left)"
        if side_key == "LONG":
            return f"BTC bearish lock LONG ({remain_minutes}m left)"
        return f"BTC trend hour lock ({remain_minutes}m left)"

    def _resolve_btc_1h_reversal_block_side(self, *, btc_guard: dict[str, Any] | None) -> str:
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
            return "NEUTRAL"
        if trend_1h_side not in {"LONG", "SHORT"}:
            return "NEUTRAL"
        if trend_1h_confidence < self.btc_reversal_min_confidence:
            return "NEUTRAL"
        return "SHORT" if trend_1h_side == "LONG" else "LONG"

    def _apply_btc_reversal_entry_cooldown(self, btc_guard: dict[str, Any] | None) -> None:
        now_ts = time.time()
        if self._btc_reversal_block_until_ts > 0 and now_ts >= self._btc_reversal_block_until_ts:
            self._btc_reversal_block_until_ts = 0.0
            self._btc_reversal_block_side = "NEUTRAL"
        if self.btc_reversal_entry_cooldown_minutes <= 0:
            return
        block_side = self._resolve_btc_1h_reversal_block_side(btc_guard=btc_guard)
        if block_side not in {"LONG", "SHORT"}:
            return
        cooldown_until_ts = now_ts + (self.btc_reversal_entry_cooldown_minutes * 60.0)
        if block_side != self._btc_reversal_block_side or cooldown_until_ts > self._btc_reversal_block_until_ts:
            self._btc_reversal_block_side = block_side
            self._btc_reversal_block_until_ts = cooldown_until_ts

    def _btc_reversal_entry_block_reason(self, *, side: str, btc_guard: dict[str, Any] | None = None) -> str | None:
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return None
        if btc_guard is not None:
            self._apply_btc_reversal_entry_cooldown(btc_guard)
        block_side_now = self._resolve_btc_1h_reversal_block_side(btc_guard=btc_guard)
        if self.btc_reversal_entry_block_enabled and side_key == block_side_now:
            return f"BTC 1H reversal block {side_key}"
        now_ts = time.time()
        if self._btc_reversal_block_until_ts > 0 and now_ts >= self._btc_reversal_block_until_ts:
            self._btc_reversal_block_until_ts = 0.0
            self._btc_reversal_block_side = "NEUTRAL"
        if side_key != self._btc_reversal_block_side:
            return None
        if self._btc_reversal_block_until_ts <= now_ts:
            return None
        remain_minutes = max(1, int(math.ceil((self._btc_reversal_block_until_ts - now_ts) / 60.0)))
        return f"BTC 1H reversal cooldown {side_key} ({remain_minutes}m left)"

    def _resolve_recent_btc_down_shock_age_minutes(self) -> float | None:
        if self._btc_last_down_shock_ts <= 0:
            return None
        return max(0.0, (time.time() - self._btc_last_down_shock_ts) / 60.0)

    def _is_basic_ml_strong_btc_1h_short(self, btc_guard: dict[str, Any] | None) -> bool:
        guard = btc_guard or {}
        trend_1h_side = str(guard.get("trend_1h_side") or guard.get("side_1h") or "NEUTRAL").upper()
        try:
            trend_1h_confidence = float(guard.get("trend_1h_confidence") or guard.get("confidence_1h") or 0.0)
        except Exception:
            trend_1h_confidence = 0.0
        return trend_1h_side == "SHORT" and trend_1h_confidence >= self.basic_ml_post_dump_short_min_1h_confidence

    def _basic_ml_post_dump_short_guard_reason(
        self,
        *,
        symbol: str,
        side: str,
        btc_guard: dict[str, Any] | None,
    ) -> str | None:
        del symbol
        if not self.basic_ml_post_dump_short_guard_enabled:
            return None
        if str(side or "").upper() != "SHORT":
            return None
        if self.basic_ml_reversal_short_guard_enabled and self._resolve_btc_1h_reversal_block_side(btc_guard=btc_guard) == "SHORT":
            return "Basic ML blocked by BTC 1H reversal short"
        shock_age_minutes = self._resolve_recent_btc_down_shock_age_minutes()
        if shock_age_minutes is None:
            return None
        if shock_age_minutes > self.basic_ml_post_dump_short_recovery_minutes:
            return None
        if shock_age_minutes < self.basic_ml_post_dump_short_block_minutes:
            remain = max(1, int(math.ceil(self.basic_ml_post_dump_short_block_minutes - shock_age_minutes)))
            return f"Basic ML blocked after BTC down shock ({remain}m left)"
        if self._is_basic_ml_strong_btc_1h_short(btc_guard):
            return None
        remain = max(1, int(math.ceil(self.basic_ml_post_dump_short_recovery_minutes - shock_age_minutes)))
        return f"Basic ML blocked in BTC bottom regime ({remain}m left)"

    def _apply_basic_ml_post_dump_short_min_win(
        self,
        *,
        required_min_win: float,
        symbol: str,
        side: str,
        btc_guard: dict[str, Any] | None,
    ) -> float:
        if not self.basic_ml_post_dump_short_guard_enabled:
            return required_min_win
        if str(side or "").upper() != "SHORT":
            return required_min_win
        shock_age_minutes = self._resolve_recent_btc_down_shock_age_minutes()
        if shock_age_minutes is None or shock_age_minutes > self.basic_ml_post_dump_short_recovery_minutes:
            return required_min_win
        follows_btc = self._is_symbol_following_btc(symbol)
        floor = self.basic_ml_post_dump_short_follow_min_win if follows_btc else self.basic_ml_post_dump_short_nonfollow_min_win
        if not self._is_basic_ml_strong_btc_1h_short(btc_guard):
            floor = max(floor, self.basic_ml_post_dump_short_nonfollow_min_win)
        return max(float(required_min_win), float(floor))

    def _resolve_recent_btc_up_shock_age_minutes(self) -> float | None:
        if self._btc_last_up_shock_ts <= 0:
            return None
        return max(0.0, (time.time() - self._btc_last_up_shock_ts) / 60.0)

    def _is_basic_ml_strong_btc_1h_long(self, btc_guard: dict[str, Any] | None) -> bool:
        guard = btc_guard or {}
        trend_1h_side = str(guard.get("trend_1h_side") or guard.get("side_1h") or "NEUTRAL").upper()
        try:
            trend_1h_confidence = float(guard.get("trend_1h_confidence") or guard.get("confidence_1h") or 0.0)
        except Exception:
            trend_1h_confidence = 0.0
        return trend_1h_side == "LONG" and trend_1h_confidence >= self.basic_ml_post_pump_long_min_1h_confidence

    def _basic_ml_post_pump_long_guard_reason(
        self,
        *,
        symbol: str,
        side: str,
        btc_guard: dict[str, Any] | None,
    ) -> str | None:
        del symbol
        if not self.basic_ml_post_pump_long_guard_enabled:
            return None
        if str(side or "").upper() != "LONG":
            return None
        if self.basic_ml_reversal_long_guard_enabled and self._resolve_btc_1h_reversal_block_side(btc_guard=btc_guard) == "LONG":
            return "Basic ML blocked by BTC 1H reversal long"
        shock_age_minutes = self._resolve_recent_btc_up_shock_age_minutes()
        if shock_age_minutes is None:
            return None
        if shock_age_minutes > self.basic_ml_post_pump_long_recovery_minutes:
            return None
        if shock_age_minutes < self.basic_ml_post_pump_long_block_minutes:
            remain = max(1, int(math.ceil(self.basic_ml_post_pump_long_block_minutes - shock_age_minutes)))
            return f"Basic ML blocked after BTC up shock ({remain}m left)"
        if self._is_basic_ml_strong_btc_1h_long(btc_guard):
            return None
        remain = max(1, int(math.ceil(self.basic_ml_post_pump_long_recovery_minutes - shock_age_minutes)))
        return f"Basic ML blocked in BTC top regime ({remain}m left)"

    def _apply_basic_ml_post_pump_long_min_win(
        self,
        *,
        required_min_win: float,
        symbol: str,
        side: str,
        btc_guard: dict[str, Any] | None,
    ) -> float:
        if not self.basic_ml_post_pump_long_guard_enabled:
            return required_min_win
        if str(side or "").upper() != "LONG":
            return required_min_win
        shock_age_minutes = self._resolve_recent_btc_up_shock_age_minutes()
        if shock_age_minutes is None or shock_age_minutes > self.basic_ml_post_pump_long_recovery_minutes:
            return required_min_win
        follows_btc = self._is_symbol_following_btc(symbol)
        floor = self.basic_ml_post_pump_long_follow_min_win if follows_btc else self.basic_ml_post_pump_long_nonfollow_min_win
        if not self._is_basic_ml_strong_btc_1h_long(btc_guard):
            floor = max(floor, self.basic_ml_post_pump_long_nonfollow_min_win)
        return max(float(required_min_win), float(floor))

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
        side: str,
        pnl: float,
        pnl_pct: float,
        btc_guard: dict[str, Any],
    ) -> bool:
        if not self.btc_reversal_loss_exit_enabled:
            return False
        if pnl >= 0:
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

    def _is_countertrend_on_btc_1h_reversal(self, *, side: str, btc_guard: dict[str, Any]) -> bool:
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return False

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
            self._btc_last_up_shock_ts = max(self._btc_last_up_shock_ts, now)
            self._btc_up_shock_long_block_until_ts = max(self._btc_up_shock_long_block_until_ts, long_block_until)
        if shock_direction == "DOWN" and self.btc_shock_down_short_block_minutes > 0:
            block_minutes = max(self.btc_shock_cooldown_minutes, self.btc_shock_down_short_block_minutes)
            short_block_until = now + (block_minutes * 60)
            self._btc_last_down_shock_ts = max(self._btc_last_down_shock_ts, now)
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

    def _analyze_btc_kill_short_15m(self, rows: list[list[Any]]) -> dict[str, bool]:
        payload = {
            "active": False,
            "confirmation_ready": False,
            "entry_buffer_pct": 0.0,
        }
        if not self.btc_kill_short_guard_enabled:
            return payload
        candles = [row for row in rows if len(row) >= 5]
        if len(candles) < 6:
            return payload

        current_row = candles[-1]
        closed_rows = candles[:-1]
        if len(closed_rows) < 4:
            return payload

        closes = [float(row[4]) for row in closed_rows]
        if len(closes) < 100:
            return payload

        last_closed = closed_rows[-1]
        prev_closed = closed_rows[-2]
        recent_closed_rows = closed_rows[-4:]
        ema99_last_closed = self._ema_last(closes[-160:], period=99)
        ema99_prev_closed = self._ema_last(closes[:-1][-160:], period=99) if len(closes) > 100 else ema99_last_closed
        current_closes = closes + [float(current_row[4])]
        ema99_current = self._ema_last(current_closes[-160:], period=99)

        prev_pump = self._is_btc_kill_short_pump_candle(
            candle=prev_closed,
            ema99=ema99_prev_closed,
            prior_rows=recent_closed_rows[:-2],
        )
        last_closed_pump = self._is_btc_kill_short_pump_candle(
            candle=last_closed,
            ema99=ema99_last_closed,
            prior_rows=recent_closed_rows[:-1],
        )
        closed_confirmation = prev_pump and self._is_btc_kill_short_confirmation_candle(
            candle=last_closed,
            pump_candle=prev_closed,
        )
        live_pump = self._is_btc_kill_short_live_pump_candle(
            candle=current_row,
            ema99=ema99_current,
            prior_rows=recent_closed_rows,
        )

        if live_pump:
            payload["entry_buffer_pct"] = self._resolve_btc_kill_short_entry_buffer_pct(
                candle=current_row,
                ema99=ema99_current,
            )
        elif last_closed_pump:
            payload["entry_buffer_pct"] = self._resolve_btc_kill_short_entry_buffer_pct(
                candle=last_closed,
                ema99=ema99_last_closed,
            )
        elif closed_confirmation:
            payload["entry_buffer_pct"] = self._resolve_btc_kill_short_entry_buffer_pct(
                candle=prev_closed,
                ema99=ema99_prev_closed,
            )

        payload["confirmation_ready"] = bool(closed_confirmation)
        payload["active"] = bool(live_pump or last_closed_pump or (prev_pump and not closed_confirmation))
        return payload

    def _is_btc_kill_short_pump_candle(self, *, candle: list[Any], ema99: float, prior_rows: list[list[Any]]) -> bool:
        if ema99 <= 0:
            return False
        try:
            open_px = float(candle[1])
            high_px = float(candle[2])
            low_px = float(candle[3])
            close_px = float(candle[4])
        except Exception:
            return False
        if open_px <= 0 or close_px <= open_px:
            return False
        body_pct = ((close_px - open_px) / open_px) * 100.0
        if body_pct < self.btc_kill_short_pump_min_body_pct:
            return False
        tol = self.btc_kill_short_ema_tolerance_pct
        touched_ema_zone = low_px <= (ema99 * (1.0 + tol))
        reclaimed_ema = close_px >= (ema99 * (1.0 + (tol * 0.2)))
        swept_above = high_px >= max(close_px, ema99 * (1.0 + tol))
        if not (touched_ema_zone and reclaimed_ema and swept_above):
            return False
        had_recent_red = False
        had_recent_ema_pressure = False
        for row in prior_rows:
            if len(row) < 5:
                continue
            try:
                prev_open = float(row[1])
                prev_low = float(row[3])
                prev_close = float(row[4])
            except Exception:
                continue
            if prev_close < prev_open:
                had_recent_red = True
            if prev_low <= (ema99 * (1.0 + tol)) or prev_close <= (ema99 * (1.0 + tol)):
                had_recent_ema_pressure = True
        return had_recent_red or had_recent_ema_pressure

    def _is_btc_kill_short_live_pump_candle(self, *, candle: list[Any], ema99: float, prior_rows: list[list[Any]]) -> bool:
        if ema99 <= 0:
            return False
        try:
            open_px = float(candle[1])
            high_px = float(candle[2])
            low_px = float(candle[3])
            close_px = float(candle[4])
        except Exception:
            return False
        if open_px <= 0:
            return False
        tol = self.btc_kill_short_ema_tolerance_pct
        range_pct = ((high_px - low_px) / open_px) * 100.0
        if range_pct < self.btc_kill_short_pump_min_body_pct:
            return False
        touched_ema_zone = low_px <= (ema99 * (1.0 + tol))
        swept_above = high_px >= (ema99 * (1.0 + tol))
        held_above_zone = max(open_px, close_px) >= (ema99 * (1.0 + (tol * 0.2)))
        if not (touched_ema_zone and swept_above and held_above_zone):
            return False
        had_recent_red = False
        had_recent_ema_pressure = False
        for row in prior_rows:
            if len(row) < 5:
                continue
            try:
                prev_open = float(row[1])
                prev_low = float(row[3])
                prev_close = float(row[4])
            except Exception:
                continue
            if prev_close < prev_open:
                had_recent_red = True
            if prev_low <= (ema99 * (1.0 + tol)) or prev_close <= (ema99 * (1.0 + tol)):
                had_recent_ema_pressure = True
        return had_recent_red or had_recent_ema_pressure

    @staticmethod
    def _is_btc_kill_short_confirmation_candle(*, candle: list[Any], pump_candle: list[Any]) -> bool:
        if len(candle) < 5 or len(pump_candle) < 5:
            return False
        try:
            open_px = float(candle[1])
            close_px = float(candle[4])
            pump_close = float(pump_candle[4])
        except Exception:
            return False
        return close_px < open_px and close_px < pump_close

    def _resolve_btc_kill_short_entry_buffer_pct(self, *, candle: list[Any], ema99: float) -> float:
        if len(candle) < 5 or ema99 <= 0:
            return 0.0
        try:
            open_px = float(candle[1])
            high_px = float(candle[2])
            low_px = float(candle[3])
        except Exception:
            return 0.0
        if open_px <= 0:
            return 0.0
        tol = self.btc_kill_short_ema_tolerance_pct
        sweep_pct = max(0.0, (high_px / max(ema99, 1e-12)) - 1.0)
        range_pct = max(0.0, (high_px - low_px) / open_px)
        return self._clamp(max(0.0012, sweep_pct + (range_pct * 0.25) + (tol * 0.5)), 0.0012, 0.0045)

    def _adjust_entry_for_btc_kill_short(
        self,
        *,
        side: str,
        entry: float,
        take_profit: float,
        stop_loss: float,
        market_price: float,
        btc_guard: dict[str, Any] | None,
    ) -> tuple[float, float, float]:
        side_key = str(side or "").upper()
        if side_key != "SHORT":
            return entry, take_profit, stop_loss
        if bool((btc_guard or {}).get("kill_short_15m_active")):
            return entry, take_profit, stop_loss
        if not bool((btc_guard or {}).get("kill_short_15m_confirmation_ready")):
            return entry, take_profit, stop_loss
        try:
            buffer_pct = float((btc_guard or {}).get("kill_short_15m_entry_buffer_pct") or 0.0)
        except Exception:
            buffer_pct = 0.0
        if buffer_pct <= 0.0 or entry <= 0 or take_profit <= 0 or stop_loss <= 0 or market_price <= 0:
            return entry, take_profit, stop_loss
        adjusted_entry = max(entry * (1.0 + buffer_pct), market_price * (1.0 + max(0.0008, buffer_pct * 0.5)))
        scale = adjusted_entry / max(entry, 1e-12)
        adjusted_tp = take_profit * scale
        adjusted_sl = stop_loss * scale
        return float(adjusted_entry), float(adjusted_tp), float(adjusted_sl)

    def _btc_kill_short_guard_reason(self, *, side: str, btc_guard: dict[str, Any]) -> str | None:
        if not self.btc_kill_short_guard_enabled:
            return None
        if str(side or "").upper() != "SHORT":
            return None
        if not bool((btc_guard or {}).get("kill_short_15m_active")):
            return None
        return "BTC 15m kill-short guard (wait red confirmation)"

    def _classify_basic_ml_btc_candle_side(
        self,
        *,
        candle: list[Any],
        min_body_pct: float,
    ) -> tuple[str, float]:
        if len(candle) < 5:
            return "NEUTRAL", 0.0
        try:
            open_px = float(candle[1])
            close_px = float(candle[4])
        except Exception:
            return "NEUTRAL", 0.0
        if open_px <= 0:
            return "NEUTRAL", 0.0
        body_pct = ((close_px - open_px) / open_px) * 100.0
        threshold = max(0.01, float(min_body_pct))
        if body_pct >= threshold:
            return "LONG", float(body_pct)
        if body_pct <= -threshold:
            return "SHORT", float(body_pct)
        return "NEUTRAL", float(body_pct)

    def _analyze_basic_ml_btc_confirmation_15m(self, rows: list[list[Any]]) -> dict[str, Any]:
        payload = {
            "cluster_side": "NEUTRAL",
            "cluster_score": 0.0,
            "last_candle_side": "NEUTRAL",
            "long_confirmation_ready": False,
            "short_confirmation_ready": False,
        }
        if not self.basic_ml_btc_candle_confirm_enabled:
            return payload
        candles = [row for row in rows if len(row) >= 5]
        if len(candles) < 6:
            return payload

        current_row = candles[-1]
        closed_rows = candles[:-1]
        if len(closed_rows) < 4:
            return payload
        closes = [float(row[4]) for row in closed_rows]
        if len(closes) < 60:
            return payload

        current_close = float(current_row[4])
        current_closes = closes + [current_close]
        ema_fast = self._ema_last(current_closes[-120:], period=21)
        ema_slow = self._ema_last(current_closes[-160:], period=55)
        recent_rows = (closed_rows[-2:] + [current_row]) if len(closed_rows) >= 2 else candles[-3:]
        weights = (0.75, 1.0, 1.35)
        body_denominator = max(self.basic_ml_btc_candle_confirm_min_body_pct * 2.5, 0.2)
        body_score = 0.0
        weight_sum = 0.0
        directions: list[str] = []
        for weight, candle in zip(weights, recent_rows):
            candle_side, body_pct = self._classify_basic_ml_btc_candle_side(
                candle=candle,
                min_body_pct=self.basic_ml_btc_candle_confirm_min_body_pct,
            )
            directions.append(candle_side)
            body_component = self._clamp(body_pct / body_denominator, -1.0, 1.0)
            body_score += body_component * weight
            weight_sum += weight
        weighted_body_score = body_score / max(weight_sum, 1e-12)

        anchor_close = float(closed_rows[-3][4]) if len(closed_rows) >= 3 else float(closed_rows[0][4])
        momentum_pct = 0.0
        if abs(anchor_close) > 1e-12:
            momentum_pct = ((current_close - anchor_close) / anchor_close) * 100.0
        momentum_denominator = max(self.basic_ml_btc_candle_confirm_min_body_pct * 4.0, 0.45)
        momentum_component = self._clamp(momentum_pct / momentum_denominator, -1.0, 1.0)

        ema_component = 0.0
        if ema_fast > 0 and ema_slow > 0:
            if current_close >= ema_fast and ema_fast >= (ema_slow * 0.9995):
                ema_component = 1.0
            elif current_close <= ema_fast and ema_fast <= (ema_slow * 1.0005):
                ema_component = -1.0
            elif current_close > ema_fast:
                ema_component = 0.35
            elif current_close < ema_fast:
                ema_component = -0.35

        cluster_score = (weighted_body_score * 0.58) + (momentum_component * 0.22) + (ema_component * 0.20)
        threshold = self.basic_ml_btc_candle_confirm_cluster_score
        if cluster_score >= threshold:
            cluster_side = "LONG"
        elif cluster_score <= -threshold:
            cluster_side = "SHORT"
        else:
            cluster_side = "NEUTRAL"

        green_count = sum(1 for item in directions if item == "LONG")
        red_count = sum(1 for item in directions if item == "SHORT")
        last_candle_side = directions[-1] if directions else "NEUTRAL"
        long_confirmation_ready = bool(
            cluster_side == "LONG"
            and last_candle_side == "LONG"
            and green_count >= 2
            and (ema_fast <= 0 or current_close >= (ema_fast * 0.9992))
        )
        short_confirmation_ready = bool(
            cluster_side == "SHORT"
            and last_candle_side == "SHORT"
            and red_count >= 2
            and (ema_fast <= 0 or current_close <= (ema_fast * 1.0008))
        )

        payload.update(
            {
                "cluster_side": cluster_side,
                "cluster_score": float(cluster_score),
                "last_candle_side": last_candle_side,
                "long_confirmation_ready": long_confirmation_ready,
                "short_confirmation_ready": short_confirmation_ready,
            }
        )
        return payload

    def _basic_ml_btc_candle_confirmation_reason(
        self,
        *,
        symbol: str,
        side: str,
        btc_guard: dict[str, Any] | None,
    ) -> str | None:
        del symbol
        if not self.basic_ml_btc_candle_confirm_enabled:
            return None
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return None
        if side_key == "SHORT":
            kill_short_reason = self._btc_kill_short_guard_reason(side=side_key, btc_guard=btc_guard or {})
            if kill_short_reason:
                return kill_short_reason

        guard = btc_guard or {}
        cluster_side = str(guard.get("basic_ml_15m_cluster_side") or "NEUTRAL").upper()
        last_candle_side = str(guard.get("basic_ml_15m_last_candle_side") or "NEUTRAL").upper()
        try:
            cluster_score = abs(float(guard.get("basic_ml_15m_cluster_score") or 0.0))
        except Exception:
            cluster_score = 0.0
        trend_side = str(guard.get("side") or "NEUTRAL").upper()
        trend_1h_side = str(guard.get("trend_1h_side") or "NEUTRAL").upper()
        try:
            trend_confidence = float(guard.get("confidence") or 0.0)
        except Exception:
            trend_confidence = 0.0
        try:
            trend_1h_confidence = float(guard.get("trend_1h_confidence") or 0.0)
        except Exception:
            trend_1h_confidence = 0.0

        ready_flag = bool(
            guard.get("basic_ml_15m_long_confirmation_ready")
            if side_key == "LONG"
            else guard.get("basic_ml_15m_short_confirmation_ready")
        )
        trend_confirms = (
            trend_side == side_key
            and trend_confidence >= self.basic_ml_btc_candle_confirm_min_trend_confidence
        )
        trend_1h_confirms = (
            trend_1h_side == side_key
            and trend_1h_confidence >= self.basic_ml_btc_candle_confirm_min_1h_confidence
        )
        if ready_flag or (trend_confirms and last_candle_side == side_key) or (trend_confirms and trend_1h_confirms):
            return None

        opposite_side = "SHORT" if side_key == "LONG" else "LONG"
        opposite_strong = bool(
            (cluster_side == opposite_side and cluster_score >= (self.basic_ml_btc_candle_confirm_cluster_score * 0.85))
            or (trend_side == opposite_side and trend_confidence >= self.basic_ml_btc_candle_confirm_min_trend_confidence)
            or (trend_1h_side == opposite_side and trend_1h_confidence >= self.basic_ml_btc_candle_confirm_min_1h_confidence)
        )
        wait_label = "green" if side_key == "LONG" else "red"
        if opposite_strong:
            return f"Basic ML wait BTC 15m {wait_label} confirmation"
        if last_candle_side != side_key:
            return f"Basic ML wait BTC 15m {wait_label} confirmation"
        if cluster_side == "NEUTRAL" and not trend_confirms:
            return f"Basic ML wait BTC 15m {wait_label} confirmation"
        return None

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

    def _should_bypass_instant_sl_global_pause_for_entry_type(self, entry_type: str) -> bool:
        entry_type_key = str(entry_type or "").strip().upper()
        if entry_type_key != "LIMIT":
            return False
        reason = str(self._open_pause_reason or "").strip().upper()
        return reason.startswith("INSTANT-SL GLOBAL PAUSE")

    def _should_bypass_instant_sl_symbol_guard_for_entry_type(self, entry_type: str) -> bool:
        entry_type_key = str(entry_type or "").strip().upper()
        return entry_type_key == "LIMIT"

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
        # Prefer markPrice for accurate TP/SL checks (matches exchange UI).
        mark_price = ticker.get("markPrice")
        if mark_price is None:
            info = ticker.get("info")
            if isinstance(info, dict):
                mark_price = info.get("markPrice")
        if mark_price is not None:
            return float(mark_price)
        # Fallback: mid price of best bid/ask.
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is not None and ask is not None:
            return float((bid + ask) / 2)
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
    def _entry_touched_strict(side: str, market_price: float, entry: float) -> bool:
        side_key = str(side or "").upper()
        if entry <= 0:
            return False
        if side_key == "LONG":
            return market_price <= entry
        if side_key == "SHORT":
            return market_price >= entry
        return False

    def _resolve_ml_candles_bg_bullish_short_buffer_pct(
        self,
        *,
        symbol: str,
        side: str,
        btc_guard: dict[str, Any] | None,
    ) -> float:
        side_key = str(side or "").upper()
        if side_key != "SHORT":
            return 0.0
        if not self._is_btc_bullish_regime(btc_guard):
            return 0.0
        buffer_pct = float(self.candles_bg_bullish_short_entry_buffer_pct)
        if not self._is_symbol_following_btc(symbol):
            buffer_pct += float(self.candles_bg_bullish_short_nonfollow_extra_buffer_pct)
        return max(0.0, buffer_pct)

    def _adjust_ml_candles_bg_entry_for_btc_regime(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        take_profit: float,
        stop_loss: float,
        market_price: float,
        btc_guard: dict[str, Any] | None,
    ) -> tuple[float, float, float]:
        buffer_pct = self._resolve_ml_candles_bg_bullish_short_buffer_pct(
            symbol=symbol,
            side=side,
            btc_guard=btc_guard,
        )
        if buffer_pct <= 0.0 or entry <= 0 or take_profit <= 0 or stop_loss <= 0 or market_price <= 0:
            return entry, take_profit, stop_loss

        adjusted_entry = max(entry * (1.0 + buffer_pct), market_price * (1.0 + max(0.001, buffer_pct * 0.5)))
        scale = adjusted_entry / max(entry, 1e-12)
        adjusted_tp = take_profit * scale
        adjusted_sl = stop_loss * scale
        return float(adjusted_entry), float(adjusted_tp), float(adjusted_sl)

    def _ml_candles_bg_entry_timing_reason(
        self,
        *,
        symbol: str,
        side: str,
        market_price: float,
        entry: float,
        btc_guard: dict[str, Any] | None,
    ) -> str | None:
        if not self._entry_touched(side=side, market_price=market_price, entry=entry):
            return "Entry not touched"

        side_key = str(side or "").upper()
        if side_key != "SHORT":
            return None
        if not self._is_btc_bullish_regime(btc_guard):
            return None
        if not self._entry_touched_strict(side=side_key, market_price=market_price, entry=entry):
            return "ML_CANDLES_BG short wait retest"
        if not self._is_short_top_test_rejection(symbol=symbol):
            return "ML_CANDLES_BG short wait rejection"
        return None

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
    def _calc_fee(
        entry: float,
        quantity: float,
        entry_type: str,
        fee_taker: float,
        fee_maker: float,
    ) -> float:
        """Return total commission for both legs (entry + exit) in USDT.
        MARKET order -> taker rate, LIMIT order -> maker rate.
        """
        notional = entry * quantity
        rate = fee_taker if str(entry_type).upper() == "MARKET" else fee_maker
        return notional * rate * 2  # entry leg + exit leg

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

    @staticmethod
    def _normalize_stream_symbol_key(symbol: str) -> str:
        base = str(symbol or "").upper().strip()
        return base.replace(":USDT", "").replace("/", "")

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

    def _resolve_symbol_leverage(self, symbol: str, atr_pct: float | None = None) -> int:
        base_lev = self.major_symbol_leverage if self._is_major_symbol(symbol) else self.leverage
        if atr_pct is not None and atr_pct >= self.high_volatility_threshold_pct:
            return min(base_lev, self.high_volatility_leverage)
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

    def _resolve_signal_strength(
        self,
        *,
        effective_prob: float,
        base_min_win: float,
    ) -> float:
        threshold = min(0.95, max(0.05, float(base_min_win)))
        return self._clamp(
            (float(effective_prob) - threshold)
            / max(0.05, 0.95 - threshold),
            0.0,
            1.0,
        )

    def _resolve_signal_min_rr(
        self,
        *,
        effective_prob: float,
        base_min_win: float,
    ) -> float:
        strength = self._resolve_signal_strength(
            effective_prob=effective_prob,
            base_min_win=base_min_win,
        )
        return 0.2 + (0.1 * strength)

    def _expand_signal_exit_targets(
        self,
        *,
        side: str,
        entry: float,
        take_profit: float,
        stop_loss: float,
        leverage: int,
        effective_prob: float,
        base_min_win: float,
    ) -> tuple[float, float]:
        if entry <= 0 or take_profit <= 0 or stop_loss <= 0:
            return float(take_profit), float(stop_loss)
        lev = max(1, int(leverage))
        strength = self._resolve_signal_strength(
            effective_prob=effective_prob,
            base_min_win=base_min_win,
        )
        min_sl_margin_pct = 12.0 + (4.0 * strength)
        min_tp_margin_pct = 2.4 + (1.2 * strength)
        target_rr = self._resolve_signal_min_rr(
            effective_prob=effective_prob,
            base_min_win=base_min_win,
        )
        min_sl_distance = float(entry) * (min_sl_margin_pct / 100.0) / float(lev)
        min_tp_distance = float(entry) * (min_tp_margin_pct / 100.0) / float(lev)
        sl_distance = max(abs(float(entry) - float(stop_loss)), min_sl_distance)
        tp_distance = max(
            abs(float(take_profit) - float(entry)),
            min_tp_distance,
            sl_distance * target_rr,
        )
        if str(side or "").upper() == "LONG":
            return float(entry + tp_distance), float(entry - sl_distance)
        return float(entry - tp_distance), float(entry + sl_distance)

    def _resolve_signal_max_tp_pct(
        self,
        *,
        entry: float,
        take_profit: float,
    ) -> float | None:
        base_pct = max(0.0, float(settings.paper_trade_max_tp_pct))
        if entry <= 0 or take_profit <= 0:
            return (base_pct / 100.0) if base_pct > 0 else None
        signal_pct = (abs(float(take_profit) - float(entry)) / float(entry)) * 100.0
        dynamic_pct = max(0.05, signal_pct * 1.05)
        return dynamic_pct / 100.0

    def _resolve_signal_max_margin_loss_pct(
        self,
        *,
        entry: float,
        stop_loss: float,
        leverage: int,
        symbol: str,
        side: str,
        atr_pct: float | None,
        btc_guard: dict[str, Any] | None,
    ) -> float:
        base_cap_pct = self._resolve_max_margin_loss_pct(
            symbol=symbol,
            side=side,
            atr_pct=atr_pct,
            btc_guard=btc_guard,
        )
        if entry <= 0 or stop_loss <= 0:
            return base_cap_pct
        signal_margin_loss_pct = (
            (abs(float(entry) - float(stop_loss)) / float(entry))
            * float(max(1, leverage))
            * 100.0
        )
        return max(0.5, signal_margin_loss_pct * 1.05)

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










