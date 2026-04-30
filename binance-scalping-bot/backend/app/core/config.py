from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel
import os


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH)


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseModel):
    app_name: str = os.getenv("APP_NAME", "Binance Scalping Bot API")
    app_env: str = os.getenv("APP_ENV", "development")
    host: str = os.getenv("HOST", "127.0.0.1")
    port: int = int(os.getenv("PORT", "8000"))
    allowed_origins: list[str] = _csv_list(os.getenv("ALLOWED_ORIGINS", "http://localhost:5173"))

    sqlite_db_path: str = os.getenv(
        "SQLITE_DB_PATH",
        str(BASE_DIR / "backend_data" / "trading_bot.db"),
    )
    ml_model_path: str = os.getenv(
        "ML_MODEL_PATH",
        str(BASE_DIR / "backend_data" / "rf_model.joblib"),
    )
    ml_candles_model_path: str = os.getenv(
        "ML_CANDLES_MODEL_PATH",
        str(BASE_DIR / "backend_data" / "ml_candles_model.joblib"),
    )
    ml_test_model_path: str = os.getenv(
        "ML_TEST_MODEL_PATH",
        str(BASE_DIR / "backend_data" / "rf_model_test.joblib"),
    )
    liquid_ml_model_path: str = os.getenv(
        "LIQUID_ML_MODEL_PATH",
        str(BASE_DIR / "backend_data" / "liquid_rf_model.joblib"),
    )
    ml_feedback_train_limit: int = int(os.getenv("ML_FEEDBACK_TRAIN_LIMIT", "1200"))
    ml_feedback_mae_penalty_pct: float = float(os.getenv("ML_FEEDBACK_MAE_PENALTY_PCT", "20"))
    ml_feedback_flip_win_on_deep_mae: bool = os.getenv("ML_FEEDBACK_FLIP_WIN_ON_DEEP_MAE", "true").lower() == "true"
    ml_feedback_use_pnl_weight: bool = os.getenv("ML_FEEDBACK_USE_PNL_WEIGHT", "true").lower() == "true"
    ml_feedback_pnl_weight_factor: float = float(os.getenv("ML_FEEDBACK_PNL_WEIGHT_FACTOR", "0.02"))
    ml_feedback_pnl_weight_max_boost: float = float(os.getenv("ML_FEEDBACK_PNL_WEIGHT_MAX_BOOST", "2.0"))
    ml_feedback_pnl_loss_boost_multiplier: float = float(os.getenv("ML_FEEDBACK_PNL_LOSS_BOOST_MULTIPLIER", "1.1"))
    ml_feedback_recovery_penalty_enabled: bool = os.getenv("ML_FEEDBACK_RECOVERY_PENALTY_ENABLED", "true").lower() == "true"
    ml_feedback_recovery_penalty_mae_pct: float = float(os.getenv("ML_FEEDBACK_RECOVERY_PENALTY_MAE_PCT", "10"))
    ml_feedback_recovery_penalty_max_pnl_pct: float = float(os.getenv("ML_FEEDBACK_RECOVERY_PENALTY_MAX_PNL_PCT", "2"))
    ml_feedback_recovery_penalty_weight_factor: float = float(os.getenv("ML_FEEDBACK_RECOVERY_PENALTY_WEIGHT_FACTOR", "0.35"))
    ml_feedback_good_signal_boost_enabled: bool = os.getenv("ML_FEEDBACK_GOOD_SIGNAL_BOOST_ENABLED", "true").lower() == "true"
    ml_feedback_good_signal_min_pnl_pct: float = float(os.getenv("ML_FEEDBACK_GOOD_SIGNAL_MIN_PNL_PCT", "8"))
    ml_feedback_good_signal_max_mae_pct: float = float(os.getenv("ML_FEEDBACK_GOOD_SIGNAL_MAX_MAE_PCT", "4"))
    ml_feedback_good_signal_weight_multiplier: float = float(os.getenv("ML_FEEDBACK_GOOD_SIGNAL_WEIGHT_MULTIPLIER", "1.4"))
    ml_feedback_early_loss_penalty_enabled: bool = os.getenv("ML_FEEDBACK_EARLY_LOSS_PENALTY_ENABLED", "true").lower() == "true"
    ml_feedback_early_loss_max_hold_minutes: int = int(os.getenv("ML_FEEDBACK_EARLY_LOSS_MAX_HOLD_MINUTES", "25"))
    ml_feedback_early_loss_weight_multiplier: float = float(os.getenv("ML_FEEDBACK_EARLY_LOSS_WEIGHT_MULTIPLIER", "5.0"))
    ml_feedback_long_hold_bad_penalty_enabled: bool = os.getenv("ML_FEEDBACK_LONG_HOLD_BAD_PENALTY_ENABLED", "true").lower() == "true"
    ml_feedback_long_hold_min_hold_minutes: int = int(os.getenv("ML_FEEDBACK_LONG_HOLD_MIN_HOLD_MINUTES", "90"))
    ml_feedback_long_hold_max_pnl_pct: float = float(os.getenv("ML_FEEDBACK_LONG_HOLD_MAX_PNL_PCT", "1.0"))
    ml_feedback_long_hold_bad_weight_multiplier: float = float(os.getenv("ML_FEEDBACK_LONG_HOLD_BAD_WEIGHT_MULTIPLIER", "4.0"))
    auto_train_enabled: bool = os.getenv("AUTO_TRAIN_ENABLED", "true").lower() == "true"
    auto_train_interval_minutes: int = int(os.getenv("AUTO_TRAIN_INTERVAL_MINUTES", "240"))
    auto_train_startup_delay_sec: int = int(os.getenv("AUTO_TRAIN_STARTUP_DELAY_SEC", "30"))
    auto_train_limit: int = int(os.getenv("AUTO_TRAIN_LIMIT", "800"))
    auto_train_horizon: int = int(os.getenv("AUTO_TRAIN_HORIZON", "4"))
    auto_train_rr_ratio: float = float(os.getenv("AUTO_TRAIN_RR_RATIO", "1.5"))
    ml_use_liquidation_features: bool = os.getenv("ML_USE_LIQUIDATION_FEATURES", "true").lower() == "true"
    ml_candles_use_liquidation_features: bool = os.getenv("ML_CANDLES_USE_LIQUIDATION_FEATURES", "true").lower() == "true"
    ml_candles_profile_min_samples: int = int(os.getenv("ML_CANDLES_PROFILE_MIN_SAMPLES", "18"))
    signals_active_symbols_cache_sec: int = int(os.getenv("SIGNALS_ACTIVE_SYMBOLS_CACHE_SEC", "180"))
    signals_scan_default_max_symbols: int = int(os.getenv("SIGNALS_SCAN_DEFAULT_MAX_SYMBOLS", "300"))
    signals_candles_scan_default_max_symbols: int = int(
        os.getenv("SIGNALS_CANDLES_SCAN_DEFAULT_MAX_SYMBOLS", "300")
    )
    ml_test_use_liquidation_features: bool = os.getenv("ML_TEST_USE_LIQUIDATION_FEATURES", "true").lower() == "true"
    liquid_ml_enabled: bool = os.getenv("LIQUID_ML_ENABLED", "true").lower() == "true"
    liquid_ml_min_win: float = float(os.getenv("LIQUID_ML_MIN_WIN", "0.68"))
    liquid_ml_top_vol_days: int = int(os.getenv("LIQUID_ML_TOP_VOL_DAYS", "1"))
    liquid_ml_max_symbols: int = int(os.getenv("LIQUID_ML_MAX_SYMBOLS", "30"))
    liquid_ml_touch_tolerance_pct: float = float(os.getenv("LIQUID_ML_TOUCH_TOLERANCE_PCT", "0.004"))
    liquid_ml_short_zone_min_score: float = float(os.getenv("LIQUID_ML_SHORT_ZONE_MIN_SCORE", "0.012"))
    liquid_ml_short_zone_touch_multiplier: float = float(os.getenv("LIQUID_ML_SHORT_ZONE_TOUCH_MULTIPLIER", "2.0"))
    liquid_ml_train_limit: int = int(os.getenv("LIQUID_ML_TRAIN_LIMIT", "900"))
    liquid_ml_train_horizon: int = int(os.getenv("LIQUID_ML_TRAIN_HORIZON", "16"))
    liquid_ml_train_rr_ratio: float = float(os.getenv("LIQUID_ML_TRAIN_RR_RATIO", "1.5"))

    training_symbols: list[str] = _csv_list(os.getenv("TRAINING_SYMBOLS", "SOL/USDT,XRP/USDT,ADA/USDT,DOGE/USDT"))
    websocket_ping_interval_sec: float = float(os.getenv("WS_PING_INTERVAL_SEC", "1.0"))

    mysql_enabled: bool = os.getenv("MYSQL_ENABLED", "false").lower() == "true"
    mysql_host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")
    mysql_candle_database: str = os.getenv(
        "MYSQL_CANDLE_DATABASE",
        os.getenv("MYSQL_DATABASE", "trading_bot_candle"),
    )
    mysql_database: str = os.getenv("MYSQL_DATABASE", mysql_candle_database)

    paper_trade_min_win_probability: float = float(os.getenv("PAPER_TRADE_MIN_WIN", "0.75"))
    paper_trade_quantity: float = float(os.getenv("PAPER_TRADE_QUANTITY", "0.01"))
    paper_trade_order_usdt: float = float(os.getenv("PAPER_TRADE_ORDER_USDT", "10"))
    paper_trade_margin_usdt: float = float(os.getenv("PAPER_TRADE_MARGIN_USDT", "0"))
    paper_trade_maint_margin_rate: float = float(os.getenv("PAPER_TRADE_MAINT_MARGIN_RATE", "0.02"))
    paper_trade_leverage: int = int(os.getenv("PAPER_TRADE_LEVERAGE", "5"))
    paper_trade_perfect_pattern_leverage_enabled: bool = os.getenv(
        "PAPER_TRADE_PERFECT_PATTERN_LEVERAGE_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_perfect_pattern_leverage: int = int(os.getenv("PAPER_TRADE_PERFECT_PATTERN_LEVERAGE", "10"))
    paper_trade_perfect_pattern_lookback: int = int(os.getenv("PAPER_TRADE_PERFECT_PATTERN_LOOKBACK", "180"))
    paper_trade_major_symbols: list[str] = _csv_list(os.getenv("PAPER_TRADE_MAJOR_SYMBOLS", "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT"))
    paper_trade_major_dynamic_enabled: bool = os.getenv("PAPER_TRADE_MAJOR_DYNAMIC_ENABLED", "true").lower() == "true"
    paper_trade_major_dynamic_refresh_sec: int = int(os.getenv("PAPER_TRADE_MAJOR_DYNAMIC_REFRESH_SEC", "180"))
    paper_trade_major_dynamic_limit: int = int(os.getenv("PAPER_TRADE_MAJOR_DYNAMIC_LIMIT", "8"))
    paper_trade_major_dynamic_candidates: int = int(os.getenv("PAPER_TRADE_MAJOR_DYNAMIC_CANDIDATES", "30"))
    paper_trade_major_dynamic_candle_lookback: int = int(os.getenv("PAPER_TRADE_MAJOR_DYNAMIC_CANDLE_LOOKBACK", "24"))
    paper_trade_major_leverage: int = int(os.getenv("PAPER_TRADE_MAJOR_LEVERAGE", "5"))
    paper_trade_major_max_risk_pct: float = float(os.getenv("PAPER_TRADE_MAJOR_MAX_RISK_PCT", "20"))
    paper_trade_poll_interval_sec: float = float(os.getenv("PAPER_TRADE_POLL_INTERVAL_SEC", "6"))
    paper_trade_stream_max_stale_sec: float = float(os.getenv("PAPER_TRADE_STREAM_MAX_STALE_SEC", "5"))
    paper_trade_entry_require_fresh_stream_price: bool = os.getenv(
        "PAPER_TRADE_ENTRY_REQUIRE_FRESH_STREAM_PRICE",
        "true",
    ).lower() == "true"
    paper_trade_min_sl_pct: float = float(os.getenv("PAPER_TRADE_MIN_SL_PCT", "0.008"))
    paper_trade_min_sl_loss_pct: float = float(os.getenv("PAPER_TRADE_MIN_SL_LOSS_PCT", "5"))
    paper_trade_sl_extra_buffer_pct: float = float(os.getenv("PAPER_TRADE_SL_EXTRA_BUFFER_PCT", "0.002"))
    paper_trade_sl_atr_multiplier: float = float(os.getenv("PAPER_TRADE_SL_ATR_MULTIPLIER", "1.2"))
    paper_trade_sl_atr_timeframe: str = os.getenv("PAPER_TRADE_SL_ATR_TIMEFRAME", "5m")
    paper_trade_sl_atr_limit: int = int(os.getenv("PAPER_TRADE_SL_ATR_LIMIT", "120"))
    paper_trade_max_tp_pct: float = float(os.getenv("PAPER_TRADE_MAX_TP_PCT", "15"))
    paper_trade_min_rr: float = float(os.getenv("PAPER_TRADE_MIN_RR", "1.5"))
    paper_trade_max_risk_pct: float = float(os.getenv("PAPER_TRADE_MAX_RISK_PCT", "12"))
    paper_trade_max_margin_loss_pct: float = float(os.getenv("PAPER_TRADE_MAX_MARGIN_LOSS_PCT", "10.5"))
    paper_trade_max_margin_loss_high_atr_pct: float = float(
        os.getenv("PAPER_TRADE_MAX_MARGIN_LOSS_HIGH_ATR_PCT", "12.5")
    )
    paper_trade_max_margin_loss_high_atr_threshold_pct: float = float(
        os.getenv("PAPER_TRADE_MAX_MARGIN_LOSS_HIGH_ATR_THRESHOLD_PCT", "1.5")
    )
    paper_trade_max_margin_loss_aligned_regime_bonus_pct: float = float(
        os.getenv("PAPER_TRADE_MAX_MARGIN_LOSS_ALIGNED_REGIME_BONUS_PCT", "1.0")
    )
    paper_trade_max_margin_loss_countertrend_penalty_pct: float = float(
        os.getenv("PAPER_TRADE_MAX_MARGIN_LOSS_COUNTERTREND_PENALTY_PCT", "1.0")
    )
    paper_trade_max_hold_minutes: int = int(os.getenv("PAPER_TRADE_MAX_HOLD_MINUTES", "120"))
    paper_trade_disable_sl: bool = os.getenv("PAPER_TRADE_DISABLE_SL", "false").lower() == "true"
    paper_trade_move_sl_to_entry_pnl_pct: float = float(os.getenv("PAPER_TRADE_MOVE_SL_TO_ENTRY_PNL_PCT", "15"))
    paper_trade_move_sl_lock_pnl_pct: float = float(os.getenv("PAPER_TRADE_MOVE_SL_LOCK_PNL_PCT", "10"))
    paper_trade_move_sl_scale_by_leverage: bool = os.getenv("PAPER_TRADE_MOVE_SL_SCALE_BY_LEVERAGE", "true").lower() == "true"
    paper_trade_move_sl_reference_leverage: float = float(os.getenv("PAPER_TRADE_MOVE_SL_REFERENCE_LEVERAGE", "5"))
    paper_trade_btc_filter_enabled: bool = os.getenv("PAPER_TRADE_BTC_FILTER_ENABLED", "true").lower() == "true"
    paper_trade_btc_filter_timeframe: str = os.getenv("PAPER_TRADE_BTC_FILTER_TIMEFRAME", "15m")
    paper_trade_btc_filter_cache_sec: float = float(os.getenv("PAPER_TRADE_BTC_FILTER_CACHE_SEC", "20"))
    paper_trade_btc_filter_min_confidence: float = float(os.getenv("PAPER_TRADE_BTC_FILTER_MIN_CONFIDENCE", "0.55"))
    paper_trade_btc_filter_block_countertrend: bool = os.getenv("PAPER_TRADE_BTC_FILTER_BLOCK_COUNTERTREND", "true").lower() == "true"
    paper_trade_btc_filter_countertrend_min_win: float = float(os.getenv("PAPER_TRADE_BTC_FILTER_COUNTERTREND_MIN_WIN", "0.9"))
    paper_trade_btc_filter_block_nonfollow_countertrend: bool = os.getenv(
        "PAPER_TRADE_BTC_FILTER_BLOCK_NONFOLLOW_COUNTERTREND",
        "true",
    ).lower() == "true"
    paper_trade_btc_filter_nonfollow_countertrend_min_confidence: float = float(
        os.getenv("PAPER_TRADE_BTC_FILTER_NONFOLLOW_COUNTERTREND_MIN_CONFIDENCE", "0.70")
    )
    paper_trade_btc_filter_nonfollow_countertrend_min_win: float = float(
        os.getenv("PAPER_TRADE_BTC_FILTER_NONFOLLOW_COUNTERTREND_MIN_WIN", "0.88")
    )
    paper_trade_btc_trend_hour_lock_enabled: bool = os.getenv("PAPER_TRADE_BTC_TREND_HOUR_LOCK_ENABLED", "true").lower() == "true"
    paper_trade_btc_trend_hour_lock_min_confidence: float = float(
        os.getenv("PAPER_TRADE_BTC_TREND_HOUR_LOCK_MIN_CONFIDENCE", "0.60")
    )
    paper_trade_btc_trend_hour_lock_countertrend_hours: float = float(
        os.getenv("PAPER_TRADE_BTC_TREND_HOUR_LOCK_COUNTERTREND_HOURS", "2")
    )
    paper_trade_btc_trend_hour_lock_apply_non_btc_follow: bool = os.getenv(
        "PAPER_TRADE_BTC_TREND_HOUR_LOCK_APPLY_NON_BTC_FOLLOW",
        "true",
    ).lower() == "true"
    paper_trade_btc_shock_pause_enabled: bool = os.getenv("PAPER_TRADE_BTC_SHOCK_PAUSE_ENABLED", "true").lower() == "true"
    paper_trade_btc_shock_threshold_pct: float = float(os.getenv("PAPER_TRADE_BTC_SHOCK_THRESHOLD_PCT", "1.2"))
    paper_trade_btc_shock_cooldown_minutes: int = int(os.getenv("PAPER_TRADE_BTC_SHOCK_COOLDOWN_MINUTES", "30"))
    paper_trade_btc_shock_up_long_block_minutes: int = int(os.getenv("PAPER_TRADE_BTC_SHOCK_UP_LONG_BLOCK_MINUTES", "60"))
    paper_trade_btc_shock_down_short_block_minutes: int = int(os.getenv("PAPER_TRADE_BTC_SHOCK_DOWN_SHORT_BLOCK_MINUTES", "60"))
    paper_trade_btc_shock_up_require_pullback: bool = os.getenv("PAPER_TRADE_BTC_SHOCK_UP_REQUIRE_PULLBACK", "true").lower() == "true"
    paper_trade_btc_shock_pullback_ema_period: int = int(os.getenv("PAPER_TRADE_BTC_SHOCK_PULLBACK_EMA_PERIOD", "21"))
    paper_trade_btc_shock_pullback_tolerance_pct: float = float(os.getenv("PAPER_TRADE_BTC_SHOCK_PULLBACK_TOLERANCE_PCT", "0.0015"))
    paper_trade_btc_reversal_profit_exit_enabled: bool = os.getenv("PAPER_TRADE_BTC_REVERSAL_PROFIT_EXIT_ENABLED", "false").lower() == "true"
    paper_trade_btc_reversal_threshold_pct: float = float(os.getenv("PAPER_TRADE_BTC_REVERSAL_THRESHOLD_PCT", "0.8"))
    paper_trade_btc_reversal_min_confidence: float = float(os.getenv("PAPER_TRADE_BTC_REVERSAL_MIN_CONFIDENCE", "0.55"))
    paper_trade_btc_reversal_min_profit_pct: float = float(os.getenv("PAPER_TRADE_BTC_REVERSAL_MIN_PROFIT_PCT", "0.1"))
    paper_trade_btc_reversal_loss_exit_enabled: bool = os.getenv("PAPER_TRADE_BTC_REVERSAL_LOSS_EXIT_ENABLED", "false").lower() == "true"
    paper_trade_btc_reversal_loss_exit_days_vn: str = os.getenv(
        "PAPER_TRADE_BTC_REVERSAL_LOSS_EXIT_DAYS_VN",
        "MON,TUE,WED,THU,FRI,SAT,SUN",
    )
    paper_trade_btc_reversal_loss_exit_min_loss_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_REVERSAL_LOSS_EXIT_MIN_LOSS_PCT", "0.1")
    )
    paper_trade_btc_short_rebound_profit_exit_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_SHORT_REBOUND_PROFIT_EXIT_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_short_stall_profit_exit_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_SHORT_STALL_PROFIT_EXIT_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_short_stall_profit_exit_lookback_candles: int = int(
        os.getenv("PAPER_TRADE_BTC_SHORT_STALL_PROFIT_EXIT_LOOKBACK_CANDLES", "3")
    )
    paper_trade_btc_short_stall_profit_exit_min_pullback_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_STALL_PROFIT_EXIT_MIN_PULLBACK_PCT", "0.9")
    )
    paper_trade_btc_short_stall_profit_exit_max_cluster_range_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_STALL_PROFIT_EXIT_MAX_CLUSTER_RANGE_PCT", "0.45")
    )
    paper_trade_btc_short_stall_profit_exit_max_avg_body_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_STALL_PROFIT_EXIT_MAX_AVG_BODY_PCT", "0.18")
    )
    paper_trade_btc_short_stall_profit_exit_near_low_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_STALL_PROFIT_EXIT_NEAR_LOW_PCT", "0.35")
    )
    paper_trade_btc_short_slow_grind_block_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_SHORT_SLOW_GRIND_BLOCK_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_short_slow_grind_min_rebound_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_SLOW_GRIND_MIN_REBOUND_PCT", "0.35")
    )
    paper_trade_btc_short_slow_grind_max_avg_body_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_SLOW_GRIND_MAX_AVG_BODY_PCT", "0.22")
    )
    paper_trade_btc_short_slow_grind_min_upper_wick_ratio: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_SLOW_GRIND_MIN_UPPER_WICK_RATIO", "0.28")
    )
    paper_trade_btc_short_slow_grind_max_box_position_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_SLOW_GRIND_MAX_BOX_POSITION_PCT", "0.72")
    )
    paper_trade_btc_long_weak_base_block_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_LONG_WEAK_BASE_BLOCK_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_long_weak_base_min_pullback_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_LONG_WEAK_BASE_MIN_PULLBACK_PCT", "0.45")
    )
    paper_trade_btc_long_weak_base_max_avg_body_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_LONG_WEAK_BASE_MAX_AVG_BODY_PCT", "0.22")
    )
    paper_trade_btc_long_weak_base_min_upper_wick_ratio: float = float(
        os.getenv("PAPER_TRADE_BTC_LONG_WEAK_BASE_MIN_UPPER_WICK_RATIO", "0.28")
    )
    paper_trade_btc_long_weak_base_max_box_position_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_LONG_WEAK_BASE_MAX_BOX_POSITION_PCT", "0.48")
    )
    paper_trade_btc_short_rebound_ema99_block_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_SHORT_REBOUND_EMA99_BLOCK_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_long_pullback_ema99_block_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_LONG_PULLBACK_EMA99_BLOCK_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_long_top_fade_block_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_LONG_TOP_FADE_BLOCK_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_short_rebound_ema99_tolerance_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_REBOUND_EMA99_TOLERANCE_PCT", "0.004")
    )
    paper_trade_btc_short_rebound_ema99_rebound_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_REBOUND_EMA99_REBOUND_PCT", "0.9")
    )
    paper_trade_btc_long_top_fade_pullback_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_LONG_TOP_FADE_PULLBACK_PCT", "0.45")
    )
    paper_trade_btc_short_rebound_ema99_green_candle_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_REBOUND_EMA99_GREEN_CANDLE_PCT", "0.35")
    )
    paper_trade_btc_short_rebound_ema99_lookback_candles: int = int(
        os.getenv("PAPER_TRADE_BTC_SHORT_REBOUND_EMA99_LOOKBACK_CANDLES", "12")
    )
    paper_trade_btc_short_rebound_1h_block_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_SHORT_REBOUND_1H_BLOCK_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_short_rebound_1h_lookback_candles: int = int(
        os.getenv("PAPER_TRADE_BTC_SHORT_REBOUND_1H_LOOKBACK_CANDLES", "12")
    )
    paper_trade_btc_short_rebound_1h_rebound_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_REBOUND_1H_REBOUND_PCT", "1.0")
    )
    paper_trade_btc_short_rebound_1h_green_candle_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_REBOUND_1H_GREEN_CANDLE_PCT", "0.25")
    )
    paper_trade_btc_short_rebound_1h_15m_confirm_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_REBOUND_1H_15M_CONFIRM_PCT", "0.6")
    )
    paper_trade_btc_short_rebound_1h_ema8_tolerance_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_SHORT_REBOUND_1H_EMA8_TOLERANCE_PCT", "0.003")
    )
    paper_trade_btc_profit_lock_enabled: bool = os.getenv("PAPER_TRADE_BTC_PROFIT_LOCK_ENABLED", "true").lower() == "true"
    paper_trade_btc_profit_lock_min_confidence: float = float(os.getenv("PAPER_TRADE_BTC_PROFIT_LOCK_MIN_CONFIDENCE", "0.6"))
    paper_trade_btc_follow_min_corr: float = float(os.getenv("PAPER_TRADE_BTC_FOLLOW_MIN_CORR", "0.45"))
    paper_trade_btc_follow_min_beta: float = float(os.getenv("PAPER_TRADE_BTC_FOLLOW_MIN_BETA", "0.2"))
    paper_trade_btc_follow_lookback: int = int(os.getenv("PAPER_TRADE_BTC_FOLLOW_LOOKBACK", "120"))
    paper_trade_btc_follow_cache_sec: float = float(os.getenv("PAPER_TRADE_BTC_FOLLOW_CACHE_SEC", "300"))
    paper_trade_discord_loss_alert_enabled: bool = os.getenv("PAPER_TRADE_DISCORD_LOSS_ALERT_ENABLED", "true").lower() == "true"
    paper_trade_discord_loss_alert_threshold_pct: float = float(
        os.getenv("PAPER_TRADE_DISCORD_LOSS_ALERT_THRESHOLD_PCT", os.getenv("PAPER_TRADE_DISCORD_LOSS_ALERT_THRESHOLD_USDT", "-8"))
    )
    paper_trade_discord_loss_alert_rearm_pct: float = float(
        os.getenv("PAPER_TRADE_DISCORD_LOSS_ALERT_REARM_PCT", os.getenv("PAPER_TRADE_DISCORD_LOSS_ALERT_REARM_USDT", "-6"))
    )
    paper_trade_discord_loss_webhook_url: str = os.getenv("PAPER_TRADE_DISCORD_LOSS_WEBHOOK_URL", "")
    paper_trade_discord_open_alert_enabled: bool = os.getenv("PAPER_TRADE_DISCORD_OPEN_ALERT_ENABLED", "true").lower() == "true"
    paper_trade_discord_open_webhook_url: str = os.getenv(
        "PAPER_TRADE_DISCORD_OPEN_WEBHOOK_URL",
        os.getenv("PAPER_TRADE_DISCORD_LOSS_WEBHOOK_URL", ""),
    )
    pump_hunter_discord_alert_enabled: bool = os.getenv("PUMP_HUNTER_DISCORD_ALERT_ENABLED", "true").lower() == "true"
    pump_hunter_discord_min_score: float = float(os.getenv("PUMP_HUNTER_DISCORD_MIN_SCORE", "75"))
    pump_hunter_discord_alert_cooldown_sec: int = int(os.getenv("PUMP_HUNTER_DISCORD_ALERT_COOLDOWN_SEC", "900"))
    pump_hunter_discord_webhook_url: str = os.getenv("PUMP_HUNTER_DISCORD_WEBHOOK_URL", os.getenv("PAPER_TRADE_DISCORD_LOSS_WEBHOOK_URL", ""))
    ema99_bounce_discord_alert_enabled: bool = os.getenv("EMA99_BOUNCE_DISCORD_ALERT_ENABLED", "true").lower() == "true"
    ema99_bounce_discord_min_score: float = float(os.getenv("EMA99_BOUNCE_DISCORD_MIN_SCORE", "7"))
    ema99_bounce_discord_alert_cooldown_sec: int = int(os.getenv("EMA99_BOUNCE_DISCORD_ALERT_COOLDOWN_SEC", "21600"))
    ema99_bounce_discord_webhook_url: str = os.getenv("EMA99_BOUNCE_DISCORD_WEBHOOK_URL", "")
    ema99_bounce_bg_enabled: bool = os.getenv("EMA99_BOUNCE_BG_ENABLED", "true").lower() == "true"
    ema99_bounce_bg_interval_sec: float = float(os.getenv("EMA99_BOUNCE_BG_INTERVAL_SEC", "20"))
    ema99_bounce_bg_max_symbols: int = int(os.getenv("EMA99_BOUNCE_BG_MAX_SYMBOLS", "200"))
    ema99_bounce_bg_max_items: int = int(os.getenv("EMA99_BOUNCE_BG_MAX_ITEMS", "12"))
    pump_hunter_bg_enabled: bool = os.getenv("PUMP_HUNTER_BG_ENABLED", "true").lower() == "true"
    pump_hunter_bg_interval_sec: float = float(os.getenv("PUMP_HUNTER_BG_INTERVAL_SEC", "45"))
    pump_hunter_bg_max_symbols: int = int(os.getenv("PUMP_HUNTER_BG_MAX_SYMBOLS", "0"))
    pump_hunter_bg_min_score: float = float(os.getenv("PUMP_HUNTER_BG_MIN_SCORE", "58"))
    pump_hunter_bg_limit: int = int(os.getenv("PUMP_HUNTER_BG_LIMIT", "18"))
    pump_hunter_tp_scale_down_threshold_pct: float = float(os.getenv("PUMP_HUNTER_TP_SCALE_DOWN_THRESHOLD_PCT", "50"))
    pump_hunter_tp_scale_down_factor: float = float(os.getenv("PUMP_HUNTER_TP_SCALE_DOWN_FACTOR", "0.5"))
    binance_api_key: str = os.getenv("BINANCE_API_KEY", "")
    binance_api_secret: str = os.getenv("BINANCE_API_SECRET", "")
    binance_api_base_url: str = os.getenv("BINANCE_API_BASE_URL", "https://fapi.binance.com")
    binance_recv_window_ms: int = int(os.getenv("BINANCE_RECV_WINDOW_MS", "5000"))
    pump_hunter_live_trade_enabled: bool = os.getenv("PUMP_HUNTER_LIVE_TRADE_ENABLED", "false").lower() == "true"
    pump_hunter_live_order_test_mode: bool = os.getenv("PUMP_HUNTER_LIVE_ORDER_TEST_MODE", "true").lower() == "true"
    pump_hunter_live_order_usdt: float = float(os.getenv("PUMP_HUNTER_LIVE_ORDER_USDT", "2"))
    pump_hunter_live_leverage: int = int(os.getenv("PUMP_HUNTER_LIVE_LEVERAGE", "5"))
    pump_hunter_live_margin_type: str = os.getenv("PUMP_HUNTER_LIVE_MARGIN_TYPE", "ISOLATED").upper()
    pump_hunter_live_min_score: float = float(os.getenv("PUMP_HUNTER_LIVE_MIN_SCORE", "60"))
    pump_hunter_live_min_tp_pct: float = float(os.getenv("PUMP_HUNTER_LIVE_MIN_TP_PCT", "20"))
    pump_hunter_live_low_expected_pnl_threshold_pct: float = float(
        os.getenv("PUMP_HUNTER_LIVE_LOW_EXPECTED_PNL_THRESHOLD_PCT", "30")
    )
    pump_hunter_live_low_expected_pnl_target_tp_pct: float = float(
        os.getenv("PUMP_HUNTER_LIVE_LOW_EXPECTED_PNL_TARGET_TP_PCT", "10")
    )
    pump_hunter_live_profit_timeout_hours: float = float(
        os.getenv("PUMP_HUNTER_LIVE_PROFIT_TIMEOUT_HOURS", "8")
    )
    pump_hunter_live_profit_timeout_min_pnl_pct: float = float(
        os.getenv("PUMP_HUNTER_LIVE_PROFIT_TIMEOUT_MIN_PNL_PCT", "0")
    )
    pump_hunter_live_market_entry_volume_ratio_15m_threshold: float = float(
        os.getenv("PUMP_HUNTER_LIVE_MARKET_ENTRY_VOLUME_RATIO_15M_THRESHOLD", "3")
    )
    pump_hunter_live_market_entry_require_above_ema_stack: bool = os.getenv(
        "PUMP_HUNTER_LIVE_MARKET_ENTRY_REQUIRE_ABOVE_EMA_STACK",
        "true",
    ).lower() == "true"
    pump_hunter_live_market_entry_max_distance_pct: float = float(
        os.getenv("PUMP_HUNTER_LIVE_MARKET_ENTRY_MAX_DISTANCE_PCT", "0.2")
    )
    pump_hunter_live_signal_cooldown_sec: int = int(os.getenv("PUMP_HUNTER_LIVE_SIGNAL_COOLDOWN_SEC", "900"))
    pump_hunter_live_place_tp_on_fill_enabled: bool = os.getenv(
        "PUMP_HUNTER_LIVE_PLACE_TP_ON_FILL_ENABLED",
        "true",
    ).lower() == "true"
    pump_hunter_live_move_tp_to_entry_enabled: bool = os.getenv(
        "PUMP_HUNTER_LIVE_MOVE_TP_TO_ENTRY_ENABLED",
        "true",
    ).lower() == "true"
    pump_hunter_live_move_tp_to_entry_pnl_pct: float = float(
        os.getenv("PUMP_HUNTER_LIVE_MOVE_TP_TO_ENTRY_PNL_PCT", "-20")
    )
    pump_hunter_live_place_sl_on_fill_enabled: bool = os.getenv(
        "PUMP_HUNTER_LIVE_PLACE_SL_ON_FILL_ENABLED",
        "true",
    ).lower() == "true"
    pump_hunter_live_move_sl_to_entry_enabled: bool = os.getenv(
        "PUMP_HUNTER_LIVE_MOVE_SL_TO_ENTRY_ENABLED",
        "true",
    ).lower() == "true"
    pump_hunter_live_move_sl_to_entry_pnl_pct: float = float(
        os.getenv("PUMP_HUNTER_LIVE_MOVE_SL_TO_ENTRY_PNL_PCT", "5")
    )
    pump_hunter_live_cancel_unfilled_enabled: bool = os.getenv(
        "PUMP_HUNTER_LIVE_CANCEL_UNFILLED_ENABLED",
        "true",
    ).lower() == "true"
    pump_hunter_live_cancel_after_minutes: int = int(os.getenv("PUMP_HUNTER_LIVE_CANCEL_AFTER_MINUTES", "120"))
    pump_hunter_live_cancel_check_interval_sec: int = int(
        os.getenv("PUMP_HUNTER_LIVE_CANCEL_CHECK_INTERVAL_SEC", "60")
    )
    pump_hunter_order_discord_webhook_url: str = os.getenv("PUMP_HUNTER_ORDER_DISCORD_WEBHOOK_URL", "")
    paper_trade_base_ml_max_symbols: int = int(os.getenv("PAPER_TRADE_BASE_ML_MAX_SYMBOLS", "300"))
    paper_trade_limit_max_orders_per_cycle: int = int(os.getenv("PAPER_TRADE_LIMIT_MAX_ORDERS_PER_CYCLE", "4"))
    paper_trade_test_ml_enabled: bool = os.getenv("PAPER_TRADE_TEST_ML_ENABLED", "false").lower() == "true"
    paper_trade_test_ml_min_win: float = float(os.getenv("PAPER_TRADE_TEST_ML_MIN_WIN", "0.75"))
    paper_trade_test_ml_max_symbols: int = int(os.getenv("PAPER_TRADE_TEST_ML_MAX_SYMBOLS", "300"))
    paper_trade_test_ml_max_orders_per_cycle: int = int(os.getenv("PAPER_TRADE_TEST_ML_MAX_ORDERS_PER_CYCLE", "2"))
    paper_trade_candles_bg_enabled: bool = os.getenv("PAPER_TRADE_CANDLES_BG_ENABLED", "true").lower() == "true"
    paper_trade_candles_bg_min_win: float = float(os.getenv("PAPER_TRADE_CANDLES_BG_MIN_WIN", "0.75"))
    paper_trade_candles_bg_max_symbols: int = int(os.getenv("PAPER_TRADE_CANDLES_BG_MAX_SYMBOLS", "300"))
    paper_trade_candles_bg_max_orders_per_cycle: int = int(os.getenv("PAPER_TRADE_CANDLES_BG_MAX_ORDERS_PER_CYCLE", "2"))
    paper_trade_liquid_max_orders_per_cycle: int = int(os.getenv("PAPER_TRADE_LIQUID_MAX_ORDERS_PER_CYCLE", "2"))
    paper_trade_max_open_trades: int = int(os.getenv("PAPER_TRADE_MAX_OPEN_TRADES", "24"))
    paper_trade_max_open_shorts: int = int(os.getenv("PAPER_TRADE_MAX_OPEN_SHORTS", "18"))
    paper_trade_single_position_per_symbol_side: bool = os.getenv("PAPER_TRADE_SINGLE_POSITION_PER_SYMBOL_SIDE", "true").lower() == "true"
    paper_trade_reentry_cooldown_minutes: int = int(os.getenv("PAPER_TRADE_REENTRY_COOLDOWN_MINUTES", "0"))
    paper_trade_reentry_after_sl_cooldown_minutes: int = int(os.getenv("PAPER_TRADE_REENTRY_AFTER_SL_COOLDOWN_MINUTES", "30"))
    paper_trade_symbol_sl_block_minutes: int = int(os.getenv("PAPER_TRADE_SYMBOL_SL_BLOCK_MINUTES", "180"))
    paper_trade_instant_sl_guard_enabled: bool = os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_ENABLED", "true").lower() == "true"
    paper_trade_instant_sl_guard_max_hold_minutes: int = int(
        os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_MAX_HOLD_MINUTES", "25")
    )
    paper_trade_instant_sl_guard_min_abs_pnl_pct: float = float(
        os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_MIN_ABS_PNL_PCT", "10")
    )
    paper_trade_instant_sl_guard_min_abs_mae_pct: float = float(
        os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_MIN_ABS_MAE_PCT", "8")
    )
    paper_trade_instant_sl_guard_cooldown_minutes: int = int(
        os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_COOLDOWN_MINUTES", "90")
    )
    paper_trade_instant_sl_guard_short_top_test_bypass_enabled: bool = os.getenv(
        "PAPER_TRADE_INSTANT_SL_GUARD_SHORT_TOP_TEST_BYPASS_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_instant_sl_guard_short_top_test_lookback_candles: int = int(
        os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_SHORT_TOP_TEST_LOOKBACK_CANDLES", "20")
    )
    paper_trade_instant_sl_guard_short_top_test_tolerance_pct: float = float(
        os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_SHORT_TOP_TEST_TOLERANCE_PCT", "0.001")
    )
    paper_trade_instant_sl_guard_short_rejection_min_upper_wick_ratio: float = float(
        os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_SHORT_REJECTION_MIN_UPPER_WICK_RATIO", "0.35")
    )
    paper_trade_instant_sl_guard_short_rejection_min_wick_body_ratio: float = float(
        os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_SHORT_REJECTION_MIN_WICK_BODY_RATIO", "1.2")
    )
    paper_trade_entry_long_pump_red_block_enabled: bool = os.getenv(
        "PAPER_TRADE_ENTRY_LONG_PUMP_RED_BLOCK_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_entry_long_pump_red_lookback_candles: int = int(
        os.getenv("PAPER_TRADE_ENTRY_LONG_PUMP_RED_LOOKBACK_CANDLES", "12")
    )
    paper_trade_entry_long_pump_red_top_tolerance_pct: float = float(
        os.getenv("PAPER_TRADE_ENTRY_LONG_PUMP_RED_TOP_TOLERANCE_PCT", "0.0015")
    )
    paper_trade_entry_long_pump_red_pump_min_body_pct: float = float(
        os.getenv("PAPER_TRADE_ENTRY_LONG_PUMP_RED_PUMP_MIN_BODY_PCT", "0.6")
    )
    paper_trade_entry_long_pump_red_confirm_min_body_pct: float = float(
        os.getenv("PAPER_TRADE_ENTRY_LONG_PUMP_RED_CONFIRM_MIN_BODY_PCT", "0.2")
    )
    paper_trade_entry_symbol_shock_pause_enabled: bool = os.getenv(
        "PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_entry_symbol_shock_pause_lookback_candles: int = int(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_LOOKBACK_CANDLES", "12")
    )
    paper_trade_entry_symbol_shock_pause_cooldown_candles: int = int(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_COOLDOWN_CANDLES", "3")
    )
    paper_trade_entry_symbol_shock_pause_min_range_pct: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_MIN_RANGE_PCT", "0.9")
    )
    paper_trade_entry_symbol_shock_pause_min_body_pct: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_MIN_BODY_PCT", "0.3")
    )
    paper_trade_entry_symbol_shock_pause_min_wick_ratio: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_MIN_WICK_RATIO", "0.45")
    )
    paper_trade_entry_symbol_shock_pause_min_volume_ratio: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_MIN_VOLUME_RATIO", "2.2")
    )
    paper_trade_entry_symbol_shock_pause_min_range_vs_avg: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_MIN_RANGE_VS_AVG", "1.8")
    )
    paper_trade_entry_symbol_shock_pause_use_btc_for_alts: bool = os.getenv(
        "PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_USE_BTC_FOR_ALTS",
        "true",
    ).lower() == "true"
    paper_trade_entry_symbol_shock_pause_btc_symbol: str = os.getenv(
        "PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_BTC_SYMBOL",
        "BTC/USDT",
    )
    paper_trade_entry_symbol_shock_pause_action: str = os.getenv(
        "PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_ACTION",
        "OFFSET",
    )
    paper_trade_entry_symbol_shock_pause_offset_factor: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_OFFSET_FACTOR", "0.35")
    )
    paper_trade_entry_symbol_shock_pause_offset_max_pct: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_PAUSE_OFFSET_MAX_PCT", "0.8")
    )
    paper_trade_entry_symbol_shock_directional_offset_only: bool = os.getenv(
        "PAPER_TRADE_ENTRY_SYMBOL_SHOCK_DIRECTIONAL_OFFSET_ONLY",
        "true",
    ).lower() == "true"
    paper_trade_entry_symbol_shock_long_offset_factor: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_LONG_OFFSET_FACTOR", "0.6")
    )
    paper_trade_entry_symbol_shock_short_offset_factor: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_SHORT_OFFSET_FACTOR", "0.6")
    )
    paper_trade_entry_symbol_shock_min_offset_pct: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_MIN_OFFSET_PCT", "0.2")
    )
    paper_trade_entry_symbol_shock_strong_block_enabled: bool = os.getenv(
        "PAPER_TRADE_ENTRY_SYMBOL_SHOCK_STRONG_BLOCK_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_entry_symbol_shock_strong_min_range_pct: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_STRONG_MIN_RANGE_PCT", "1.2")
    )
    paper_trade_entry_symbol_shock_strong_min_body_pct: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_STRONG_MIN_BODY_PCT", "0.5")
    )
    paper_trade_entry_symbol_shock_strong_min_volume_ratio: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_STRONG_MIN_VOLUME_RATIO", "3.0")
    )
    paper_trade_entry_symbol_shock_strong_min_range_vs_avg: float = float(
        os.getenv("PAPER_TRADE_ENTRY_SYMBOL_SHOCK_STRONG_MIN_RANGE_VS_AVG", "2.4")
    )
    paper_trade_entry_short_inside_bar_breakdown_confirm_enabled: bool = os.getenv(
        "PAPER_TRADE_ENTRY_SHORT_INSIDE_BAR_BREAKDOWN_CONFIRM_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_entry_long_inside_bar_breakout_confirm_enabled: bool = os.getenv(
        "PAPER_TRADE_ENTRY_LONG_INSIDE_BAR_BREAKOUT_CONFIRM_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_entry_strong_bull_long_confirm_enabled: bool = os.getenv(
        "PAPER_TRADE_ENTRY_STRONG_BULL_LONG_CONFIRM_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_entry_strong_bull_short_confirm_enabled: bool = os.getenv(
        "PAPER_TRADE_ENTRY_STRONG_BULL_SHORT_CONFIRM_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_instant_sl_guard_short_top_test_cache_sec: float = float(
        os.getenv("PAPER_TRADE_INSTANT_SL_GUARD_SHORT_TOP_TEST_CACHE_SEC", "8")
    )
    paper_trade_instant_sl_global_guard_enabled: bool = os.getenv(
        "PAPER_TRADE_INSTANT_SL_GLOBAL_GUARD_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_instant_sl_global_threshold: int = int(
        os.getenv("PAPER_TRADE_INSTANT_SL_GLOBAL_THRESHOLD", "3")
    )
    paper_trade_instant_sl_global_window_minutes: int = int(
        os.getenv("PAPER_TRADE_INSTANT_SL_GLOBAL_WINDOW_MINUTES", "20")
    )
    paper_trade_instant_sl_global_cooldown_minutes: int = int(
        os.getenv("PAPER_TRADE_INSTANT_SL_GLOBAL_COOLDOWN_MINUTES", "60")
    )
    paper_trade_entry_hard_block_hours_vn: str = os.getenv("PAPER_TRADE_ENTRY_HARD_BLOCK_HOURS_VN", "6,7")
    paper_trade_hourly_profile_enabled: bool = os.getenv("PAPER_TRADE_HOURLY_PROFILE_ENABLED", "true").lower() == "true"
    paper_trade_hourly_profile_min_samples: int = int(os.getenv("PAPER_TRADE_HOURLY_PROFILE_MIN_SAMPLES", "60"))
    paper_trade_hourly_profile_prob_alpha: float = float(os.getenv("PAPER_TRADE_HOURLY_PROFILE_PROB_ALPHA", "0.25"))
    paper_trade_hourly_profile_refresh_sec: int = int(os.getenv("PAPER_TRADE_HOURLY_PROFILE_REFRESH_SEC", "300"))
    paper_trade_hourly_profile_lookback_days: int = int(os.getenv("PAPER_TRADE_HOURLY_PROFILE_LOOKBACK_DAYS", "60"))
    paper_trade_hourly_profile_use_weekday: bool = os.getenv("PAPER_TRADE_HOURLY_PROFILE_USE_WEEKDAY", "true").lower() == "true"
    paper_trade_hourly_profile_use_btc_trend: bool = os.getenv("PAPER_TRADE_HOURLY_PROFILE_USE_BTC_TREND", "true").lower() == "true"
    paper_trade_hourly_profile_btc_trend_min_confidence: float = float(
        os.getenv("PAPER_TRADE_HOURLY_PROFILE_BTC_TREND_MIN_CONFIDENCE", "0.55")
    )
    paper_trade_hourly_bad_window_enabled: bool = os.getenv("PAPER_TRADE_HOURLY_BAD_WINDOW_ENABLED", "true").lower() == "true"
    paper_trade_hourly_bad_window_min_samples: int = int(os.getenv("PAPER_TRADE_HOURLY_BAD_WINDOW_MIN_SAMPLES", "60"))
    paper_trade_hourly_bad_window_block_win_rate_pct: float = float(os.getenv("PAPER_TRADE_HOURLY_BAD_WINDOW_BLOCK_WIN_RATE_PCT", "48"))
    paper_trade_hourly_bad_window_strict_win_rate_pct: float = float(os.getenv("PAPER_TRADE_HOURLY_BAD_WINDOW_STRICT_WIN_RATE_PCT", "53"))
    paper_trade_hourly_bad_window_strict_min_win_bonus: float = float(os.getenv("PAPER_TRADE_HOURLY_BAD_WINDOW_STRICT_MIN_WIN_BONUS", "0.04"))
    paper_trade_hourly_bad_window_countertrend_hard_block: bool = os.getenv("PAPER_TRADE_HOURLY_BAD_WINDOW_COUNTERTREND_HARD_BLOCK", "true").lower() == "true"
    paper_trade_bullish_short_nonfollow_max_open_ratio: float = float(
        os.getenv("PAPER_TRADE_BULLISH_SHORT_NONFOLLOW_MAX_OPEN_RATIO", "0.25")
    )
    paper_trade_bullish_short_nonfollow_min_win_bonus: float = float(
        os.getenv("PAPER_TRADE_BULLISH_SHORT_NONFOLLOW_MIN_WIN_BONUS", "0.05")
    )
    paper_trade_short_sl_streak_guard_enabled: bool = os.getenv("PAPER_TRADE_SHORT_SL_STREAK_GUARD_ENABLED", "true").lower() == "true"
    paper_trade_short_sl_streak_threshold: int = int(os.getenv("PAPER_TRADE_SHORT_SL_STREAK_THRESHOLD", "3"))
    paper_trade_short_sl_streak_cooldown_minutes: int = int(
        os.getenv("PAPER_TRADE_SHORT_SL_STREAK_COOLDOWN_MINUTES", "45")
    )
    paper_trade_short_sl_streak_refresh_sec: int = int(os.getenv("PAPER_TRADE_SHORT_SL_STREAK_REFRESH_SEC", "15"))
    paper_trade_open_pressure_close_enabled: bool = os.getenv(
        "PAPER_TRADE_OPEN_PRESSURE_CLOSE_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_open_pressure_close_window_minutes: int = int(
        os.getenv("PAPER_TRADE_OPEN_PRESSURE_CLOSE_WINDOW_MINUTES", "12")
    )
    paper_trade_open_pressure_close_min_opens: int = int(
        os.getenv("PAPER_TRADE_OPEN_PRESSURE_CLOSE_MIN_OPENS", "3")
    )
    paper_trade_open_pressure_close_min_lead: int = int(
        os.getenv("PAPER_TRADE_OPEN_PRESSURE_CLOSE_MIN_LEAD", "1")
    )
    paper_trade_open_pressure_close_min_net_pnl_usdt: float = float(
        os.getenv("PAPER_TRADE_OPEN_PRESSURE_CLOSE_MIN_NET_PNL_USDT", "0")
    )

    ml_feedback_hourly_weight_enabled: bool = os.getenv("ML_FEEDBACK_HOURLY_WEIGHT_ENABLED", "true").lower() == "true"
    ml_feedback_hourly_weight_min_samples: int = int(os.getenv("ML_FEEDBACK_HOURLY_WEIGHT_MIN_SAMPLES", "60"))
    ml_feedback_hourly_weight_factor: float = float(os.getenv("ML_FEEDBACK_HOURLY_WEIGHT_FACTOR", "1.0"))
    ml_feedback_hourly_weight_use_weekday: bool = os.getenv("ML_FEEDBACK_HOURLY_WEIGHT_USE_WEEKDAY", "true").lower() == "true"
    ml_feedback_hourly_weight_use_btc_trend: bool = os.getenv("ML_FEEDBACK_HOURLY_WEIGHT_USE_BTC_TREND", "true").lower() == "true"

    ml_feedback_mfe_missed_profit_enabled: bool = os.getenv("ML_FEEDBACK_MFE_MISSED_PROFIT_ENABLED", "true").lower() == "true"
    ml_feedback_mfe_missed_profit_min_pct: float = float(os.getenv("ML_FEEDBACK_MFE_MISSED_PROFIT_MIN_PCT", "5.0"))
    ml_feedback_mfe_missed_profit_weight_factor: float = float(os.getenv("ML_FEEDBACK_MFE_MISSED_PROFIT_WEIGHT_FACTOR", "0.6"))
    
    ml_feedback_mfe_instant_loss_enabled: bool = os.getenv("ML_FEEDBACK_MFE_INSTANT_LOSS_ENABLED", "true").lower() == "true"
    ml_feedback_mfe_instant_loss_max_pct: float = float(os.getenv("ML_FEEDBACK_MFE_INSTANT_LOSS_MAX_PCT", "0.5"))
    ml_feedback_mfe_instant_loss_weight_multiplier: float = float(os.getenv("ML_FEEDBACK_MFE_INSTANT_LOSS_WEIGHT_MULTIPLIER", "4.0"))
    ml_feedback_severe_loss_penalty_enabled: bool = os.getenv("ML_FEEDBACK_SEVERE_LOSS_PENALTY_ENABLED", "true").lower() == "true"
    ml_feedback_severe_loss_min_abs_pnl_pct: float = float(os.getenv("ML_FEEDBACK_SEVERE_LOSS_MIN_ABS_PNL_PCT", "18.0"))
    ml_feedback_severe_loss_min_abs_mae_pct: float = float(os.getenv("ML_FEEDBACK_SEVERE_LOSS_MIN_ABS_MAE_PCT", "12.0"))
    ml_feedback_severe_loss_max_mfe_pct: float = float(os.getenv("ML_FEEDBACK_SEVERE_LOSS_MAX_MFE_PCT", "1.0"))
    ml_feedback_severe_loss_weight_multiplier: float = float(os.getenv("ML_FEEDBACK_SEVERE_LOSS_WEIGHT_MULTIPLIER", "8.0"))

settings = Settings()
