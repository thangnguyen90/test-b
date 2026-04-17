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
    signals_scan_default_max_symbols: int = int(os.getenv("SIGNALS_SCAN_DEFAULT_MAX_SYMBOLS", "80"))
    signals_candles_scan_default_max_symbols: int = int(
        os.getenv("SIGNALS_CANDLES_SCAN_DEFAULT_MAX_SYMBOLS", "400")
    )
    signal_candle_pattern_cache_sec: int = int(os.getenv("SIGNAL_CANDLE_PATTERN_CACHE_SEC", "300"))
    signal_candle_pattern_refresh_limit: int = int(os.getenv("SIGNAL_CANDLE_PATTERN_REFRESH_LIMIT", "4000"))
    signal_candle_pattern_min_samples: int = int(os.getenv("SIGNAL_CANDLE_PATTERN_MIN_SAMPLES", "3"))
    signal_candle_pattern_example_limit: int = int(os.getenv("SIGNAL_CANDLE_PATTERN_EXAMPLE_LIMIT", "4"))
    signal_candle_pattern_auto_refresh_enabled: bool = os.getenv("SIGNAL_CANDLE_PATTERN_AUTO_REFRESH_ENABLED", "true").lower() == "true"
    signal_candle_pattern_auto_refresh_minutes: int = int(os.getenv("SIGNAL_CANDLE_PATTERN_AUTO_REFRESH_MINUTES", "15"))
    signal_candle_pattern_auto_refresh_startup_delay_sec: int = int(os.getenv("SIGNAL_CANDLE_PATTERN_AUTO_REFRESH_STARTUP_DELAY_SEC", "45"))
    paper_trade_ml_candles_bg_discord_webhook_enabled: bool = os.getenv("PAPER_TRADE_ML_CANDLES_BG_DISCORD_WEBHOOK_ENABLED", "false").lower() == "true"
    paper_trade_ml_candles_bg_discord_webhook_url: str = os.getenv("PAPER_TRADE_ML_CANDLES_BG_DISCORD_WEBHOOK_URL", "").strip()
    paper_trade_ml_candles_bg_discord_webhook_username: str = os.getenv("PAPER_TRADE_ML_CANDLES_BG_DISCORD_WEBHOOK_USERNAME", "ML Candles BG Bot").strip() or "ML Candles BG Bot"
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
    paper_trade_max_hold_minutes: int = int(os.getenv("PAPER_TRADE_MAX_HOLD_MINUTES", "60"))
    paper_trade_negative_recovery_exit_enabled: bool = os.getenv(
        "PAPER_TRADE_NEGATIVE_RECOVERY_EXIT_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_negative_recovery_exit_arm_after_minutes: int = int(
        os.getenv("PAPER_TRADE_NEGATIVE_RECOVERY_EXIT_ARM_AFTER_MINUTES", "30")
    )
    paper_trade_negative_recovery_exit_negative_pnl_pct: float = float(
        os.getenv("PAPER_TRADE_NEGATIVE_RECOVERY_EXIT_NEGATIVE_PNL_PCT", "0.0")
    )
    paper_trade_negative_recovery_exit_recover_pnl_pct: float = float(
        os.getenv("PAPER_TRADE_NEGATIVE_RECOVERY_EXIT_RECOVER_PNL_PCT", "0.1")
    )
    paper_trade_hourly_transition_guard_enabled: bool = os.getenv("PAPER_TRADE_HOURLY_TRANSITION_GUARD_ENABLED", "true").lower() == "true"
    paper_trade_hourly_transition_start_minute: int = int(os.getenv("PAPER_TRADE_HOURLY_TRANSITION_START_MINUTE", "55"))
    paper_trade_hourly_transition_force_close_end_minute: int = int(
        os.getenv("PAPER_TRADE_HOURLY_TRANSITION_FORCE_CLOSE_END_MINUTE", "5")
    )
    paper_trade_hourly_transition_entry_block_before_minutes: int = int(
        os.getenv("PAPER_TRADE_HOURLY_TRANSITION_ENTRY_BLOCK_BEFORE_MINUTES", "10")
    )
    paper_trade_hourly_transition_entry_block_after_minutes: int = int(
        os.getenv("PAPER_TRADE_HOURLY_TRANSITION_ENTRY_BLOCK_AFTER_MINUTES", "10")
    )
    paper_trade_hourly_transition_min_hold_minutes: int = int(os.getenv("PAPER_TRADE_HOURLY_TRANSITION_MIN_HOLD_MINUTES", "15"))
    paper_trade_hourly_transition_safe_pnl_pct: float = float(os.getenv("PAPER_TRADE_HOURLY_TRANSITION_SAFE_PNL_PCT", "0.3"))
    paper_trade_funding_guard_enabled: bool = os.getenv("PAPER_TRADE_FUNDING_GUARD_ENABLED", "true").lower() == "true"
    paper_trade_funding_guard_force_close_before_minutes: int = int(
        os.getenv("PAPER_TRADE_FUNDING_GUARD_FORCE_CLOSE_BEFORE_MINUTES", "5")
    )
    paper_trade_funding_guard_force_close_after_minutes: int = int(
        os.getenv("PAPER_TRADE_FUNDING_GUARD_FORCE_CLOSE_AFTER_MINUTES", "5")
    )
    paper_trade_funding_guard_entry_block_before_minutes: int = int(
        os.getenv("PAPER_TRADE_FUNDING_GUARD_ENTRY_BLOCK_BEFORE_MINUTES", "10")
    )
    paper_trade_funding_guard_entry_block_after_minutes: int = int(
        os.getenv("PAPER_TRADE_FUNDING_GUARD_ENTRY_BLOCK_AFTER_MINUTES", "10")
    )
    paper_trade_funding_guard_min_hold_minutes: int = int(os.getenv("PAPER_TRADE_FUNDING_GUARD_MIN_HOLD_MINUTES", "15"))
    paper_trade_funding_guard_safe_pnl_pct: float = float(os.getenv("PAPER_TRADE_FUNDING_GUARD_SAFE_PNL_PCT", "0.3"))
    paper_trade_session_open_guard_enabled: bool = os.getenv("PAPER_TRADE_SESSION_OPEN_GUARD_ENABLED", "true").lower() == "true"
    paper_trade_session_open_guard_sessions: list[str] = _csv_list(
        os.getenv("PAPER_TRADE_SESSION_OPEN_GUARD_SESSIONS", "TOKYO,LONDON,NEWYORK")
    )
    paper_trade_session_open_guard_force_close_before_minutes: int = int(
        os.getenv("PAPER_TRADE_SESSION_OPEN_GUARD_FORCE_CLOSE_BEFORE_MINUTES", "30")
    )
    paper_trade_session_open_guard_force_close_after_minutes: int = int(
        os.getenv("PAPER_TRADE_SESSION_OPEN_GUARD_FORCE_CLOSE_AFTER_MINUTES", "30")
    )
    paper_trade_session_open_guard_entry_block_before_minutes: int = int(
        os.getenv("PAPER_TRADE_SESSION_OPEN_GUARD_ENTRY_BLOCK_BEFORE_MINUTES", "30")
    )
    paper_trade_session_open_guard_entry_block_after_minutes: int = int(
        os.getenv("PAPER_TRADE_SESSION_OPEN_GUARD_ENTRY_BLOCK_AFTER_MINUTES", "30")
    )
    paper_trade_session_open_guard_min_hold_minutes: int = int(
        os.getenv("PAPER_TRADE_SESSION_OPEN_GUARD_MIN_HOLD_MINUTES", "15")
    )
    paper_trade_session_open_guard_safe_pnl_pct: float = float(
        os.getenv("PAPER_TRADE_SESSION_OPEN_GUARD_SAFE_PNL_PCT", "0.3")
    )
    paper_trade_macro_event_guard_enabled: bool = os.getenv("PAPER_TRADE_MACRO_EVENT_GUARD_ENABLED", "true").lower() == "true"
    paper_trade_macro_event_guard_keywords: list[str] = _csv_list(
        os.getenv("PAPER_TRADE_MACRO_EVENT_GUARD_KEYWORDS", "CPI,FOMC,NFP")
    )
    paper_trade_macro_event_guard_entry_block_before_minutes: int = int(
        os.getenv("PAPER_TRADE_MACRO_EVENT_GUARD_ENTRY_BLOCK_BEFORE_MINUTES", "30")
    )
    paper_trade_macro_event_guard_entry_block_after_minutes: int = int(
        os.getenv("PAPER_TRADE_MACRO_EVENT_GUARD_ENTRY_BLOCK_AFTER_MINUTES", "30")
    )
    paper_trade_macro_event_guard_force_close_before_minutes: int = int(
        os.getenv("PAPER_TRADE_MACRO_EVENT_GUARD_FORCE_CLOSE_BEFORE_MINUTES", "15")
    )
    paper_trade_macro_event_guard_force_close_after_minutes: int = int(
        os.getenv("PAPER_TRADE_MACRO_EVENT_GUARD_FORCE_CLOSE_AFTER_MINUTES", "15")
    )
    paper_trade_macro_event_guard_min_hold_minutes: int = int(
        os.getenv("PAPER_TRADE_MACRO_EVENT_GUARD_MIN_HOLD_MINUTES", "15")
    )
    paper_trade_macro_event_guard_safe_pnl_pct: float = float(
        os.getenv("PAPER_TRADE_MACRO_EVENT_GUARD_SAFE_PNL_PCT", "0.3")
    )
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
    paper_trade_btc_kill_short_guard_enabled: bool = os.getenv("PAPER_TRADE_BTC_KILL_SHORT_GUARD_ENABLED", "true").lower() == "true"
    paper_trade_btc_kill_short_pump_min_body_pct: float = float(os.getenv("PAPER_TRADE_BTC_KILL_SHORT_PUMP_MIN_BODY_PCT", "0.7"))
    paper_trade_btc_kill_short_ema_tolerance_pct: float = float(os.getenv("PAPER_TRADE_BTC_KILL_SHORT_EMA_TOLERANCE_PCT", "0.0025"))
    paper_trade_btc_bullish_short_reprice_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_BULLISH_SHORT_REPRICE_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_bullish_short_entry_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_BULLISH_SHORT_ENTRY_BUFFER_PCT", "0.003")
    )
    paper_trade_btc_bullish_short_nonfollow_extra_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_BULLISH_SHORT_NONFOLLOW_EXTRA_BUFFER_PCT", "0.001")
    )
    paper_trade_btc_bearish_long_reprice_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_BEARISH_LONG_REPRICE_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_bearish_long_entry_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_BEARISH_LONG_ENTRY_BUFFER_PCT", "0.003")
    )
    paper_trade_btc_bearish_long_follow_extra_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_BEARISH_LONG_FOLLOW_EXTRA_BUFFER_PCT", "0.0015")
    )
    paper_trade_btc_reversal_profit_exit_enabled: bool = os.getenv("PAPER_TRADE_BTC_REVERSAL_PROFIT_EXIT_ENABLED", "false").lower() == "true"
    paper_trade_btc_reversal_threshold_pct: float = float(os.getenv("PAPER_TRADE_BTC_REVERSAL_THRESHOLD_PCT", "0.8"))
    paper_trade_btc_reversal_min_confidence: float = float(os.getenv("PAPER_TRADE_BTC_REVERSAL_MIN_CONFIDENCE", "0.55"))
    paper_trade_btc_reversal_min_profit_pct: float = float(os.getenv("PAPER_TRADE_BTC_REVERSAL_MIN_PROFIT_PCT", "0.1"))
    paper_trade_basic_ml_btc_reversal_exit_enabled: bool = os.getenv(
        "PAPER_TRADE_BASIC_ML_BTC_REVERSAL_EXIT_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_basic_ml_btc_reversal_exit_min_pnl_pct: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_BTC_REVERSAL_EXIT_MIN_PNL_PCT", "-5")
    )
    paper_trade_btc_reversal_loss_exit_enabled: bool = os.getenv("PAPER_TRADE_BTC_REVERSAL_LOSS_EXIT_ENABLED", "false").lower() == "true"
    paper_trade_btc_reversal_loss_exit_days_vn: str = os.getenv(
        "PAPER_TRADE_BTC_REVERSAL_LOSS_EXIT_DAYS_VN",
        "MON,TUE,WED,THU,FRI,SAT,SUN",
    )
    paper_trade_btc_reversal_loss_exit_min_loss_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_REVERSAL_LOSS_EXIT_MIN_LOSS_PCT", "0.1")
    )
    paper_trade_btc_reversal_entry_block_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_REVERSAL_ENTRY_BLOCK_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_reversal_entry_cooldown_minutes: int = int(
        os.getenv("PAPER_TRADE_BTC_REVERSAL_ENTRY_COOLDOWN_MINUTES", "60")
    )
    paper_trade_btc_profit_lock_enabled: bool = os.getenv("PAPER_TRADE_BTC_PROFIT_LOCK_ENABLED", "true").lower() == "true"
    paper_trade_btc_profit_lock_min_confidence: float = float(os.getenv("PAPER_TRADE_BTC_PROFIT_LOCK_MIN_CONFIDENCE", "0.6"))
    paper_trade_btc_bad_trend_panic_exit_enabled: bool = os.getenv(
        "PAPER_TRADE_BTC_BAD_TREND_PANIC_EXIT_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_btc_bad_trend_panic_exit_min_confidence: float = float(
        os.getenv("PAPER_TRADE_BTC_BAD_TREND_PANIC_EXIT_MIN_CONFIDENCE", "0.62")
    )
    paper_trade_btc_bad_trend_panic_exit_min_shock_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_BAD_TREND_PANIC_EXIT_MIN_SHOCK_PCT", "0.8")
    )
    paper_trade_btc_bad_trend_panic_exit_min_tp_progress_ratio: float = float(
        os.getenv("PAPER_TRADE_BTC_BAD_TREND_PANIC_EXIT_MIN_TP_PROGRESS_RATIO", "0.35")
    )
    paper_trade_btc_bad_trend_panic_exit_min_1h_body_pct: float = float(
        os.getenv("PAPER_TRADE_BTC_BAD_TREND_PANIC_EXIT_MIN_1H_BODY_PCT", "0.0035")
    )
    paper_trade_btc_follow_min_corr: float = float(os.getenv("PAPER_TRADE_BTC_FOLLOW_MIN_CORR", "0.45"))
    paper_trade_btc_follow_min_beta: float = float(os.getenv("PAPER_TRADE_BTC_FOLLOW_MIN_BETA", "0.2"))
    paper_trade_btc_follow_lookback: int = int(os.getenv("PAPER_TRADE_BTC_FOLLOW_LOOKBACK", "120"))
    paper_trade_btc_follow_cache_sec: float = float(os.getenv("PAPER_TRADE_BTC_FOLLOW_CACHE_SEC", "300"))
    paper_trade_base_ml_max_symbols: int = int(os.getenv("PAPER_TRADE_BASE_ML_MAX_SYMBOLS", "200"))
    paper_trade_basic_ml_max_orders_per_cycle: int = int(
        os.getenv("PAPER_TRADE_BASIC_ML_MAX_ORDERS_PER_CYCLE", "6")
    )
    paper_trade_basic_ml_pattern_gate_enabled: bool = os.getenv(
        "PAPER_TRADE_BASIC_ML_PATTERN_GATE_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_basic_ml_pattern_min_win_rate_pct: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_MIN_WIN_RATE_PCT", "90")
    )
    paper_trade_basic_ml_pattern_expectancy_gate_enabled: bool = os.getenv(
        "PAPER_TRADE_BASIC_ML_PATTERN_EXPECTANCY_GATE_ENABLED",
        "true",
    ).lower() == "true"
    paper_trade_basic_ml_pattern_expectancy_min_samples: int = int(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_EXPECTANCY_MIN_SAMPLES", "120")
    )
    paper_trade_basic_ml_pattern_expectancy_min_avg_pnl_pct: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_EXPECTANCY_MIN_AVG_PNL_PCT", "0.45")
    )
    paper_trade_basic_ml_pattern_expectancy_max_avg_mae_pct: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_EXPECTANCY_MAX_AVG_MAE_PCT", "2.5")
    )
    paper_trade_basic_ml_pattern_expectancy_min_avg_mfe_pct: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_EXPECTANCY_MIN_AVG_MFE_PCT", "2.5")
    )
    paper_trade_basic_ml_pattern_expectancy_min_mfe_mae_ratio: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_EXPECTANCY_MIN_MFE_MAE_RATIO", "0.95")
    )
    paper_trade_basic_ml_pattern_c_expectancy_min_win_rate_pct: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_C_EXPECTANCY_MIN_WIN_RATE_PCT", "80")
    )
    paper_trade_basic_ml_pattern_c_expectancy_min_samples: int = int(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_C_EXPECTANCY_MIN_SAMPLES", "200")
    )
    paper_trade_basic_ml_pattern_c_expectancy_min_avg_pnl_pct: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_C_EXPECTANCY_MIN_AVG_PNL_PCT", "0.35")
    )
    paper_trade_basic_ml_pattern_c_expectancy_max_avg_mae_pct: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_C_EXPECTANCY_MAX_AVG_MAE_PCT", "2.2")
    )
    paper_trade_basic_ml_pattern_c_expectancy_min_avg_mfe_pct: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_C_EXPECTANCY_MIN_AVG_MFE_PCT", "2.7")
    )
    paper_trade_basic_ml_pattern_c_expectancy_min_mfe_mae_ratio: float = float(
        os.getenv("PAPER_TRADE_BASIC_ML_PATTERN_C_EXPECTANCY_MIN_MFE_MAE_RATIO", "1.05")
    )
    paper_trade_test_ml_enabled: bool = os.getenv("PAPER_TRADE_TEST_ML_ENABLED", "false").lower() == "true"
    paper_trade_test_ml_min_win: float = float(os.getenv("PAPER_TRADE_TEST_ML_MIN_WIN", "0.75"))
    paper_trade_test_ml_max_symbols: int = int(os.getenv("PAPER_TRADE_TEST_ML_MAX_SYMBOLS", "350"))
    paper_trade_test_ml_max_orders_per_cycle: int = int(os.getenv("PAPER_TRADE_TEST_ML_MAX_ORDERS_PER_CYCLE", "2"))
    paper_trade_candles_bg_enabled: bool = os.getenv("PAPER_TRADE_CANDLES_BG_ENABLED", "true").lower() == "true"
    paper_trade_candles_bg_min_win: float = float(os.getenv("PAPER_TRADE_CANDLES_BG_MIN_WIN", "0.75"))
    paper_trade_candles_bg_max_symbols: int = int(os.getenv("PAPER_TRADE_CANDLES_BG_MAX_SYMBOLS", "557"))
    paper_trade_candles_bg_max_orders_per_cycle: int = int(os.getenv("PAPER_TRADE_CANDLES_BG_MAX_ORDERS_PER_CYCLE", "2"))
    paper_trade_candles_bg_max_open_trades: int = int(os.getenv("PAPER_TRADE_CANDLES_BG_MAX_OPEN_TRADES", "50"))
    paper_trade_candles_bg_disable_sl: bool = os.getenv("PAPER_TRADE_CANDLES_BG_DISABLE_SL", "false").lower() == "true"
    paper_trade_candles_bg_require_entry_touch: bool = os.getenv(
        "PAPER_TRADE_CANDLES_BG_REQUIRE_ENTRY_TOUCH",
        "true",
    ).lower() == "true"
    paper_trade_candles_bg_debug_enabled: bool = os.getenv(
        "PAPER_TRADE_CANDLES_BG_DEBUG_ENABLED",
        "false",
    ).lower() == "true"
    paper_trade_candles_bg_block_hours_vn: str = os.getenv("PAPER_TRADE_CANDLES_BG_BLOCK_HOURS_VN", "").strip()
    paper_trade_candles_bg_long_block_hours_vn: str = os.getenv(
        "PAPER_TRADE_CANDLES_BG_LONG_BLOCK_HOURS_VN",
        "00,04,05,06,11,12,15,18,21,22",
    ).strip()
    paper_trade_candles_bg_short_block_hours_vn: str = os.getenv(
        "PAPER_TRADE_CANDLES_BG_SHORT_BLOCK_HOURS_VN",
        "03,12,17",
    ).strip()
    paper_trade_candles_bg_long_strict_hours_vn: str = os.getenv(
        "PAPER_TRADE_CANDLES_BG_LONG_STRICT_HOURS_VN",
        "01,07,13,14,16,23",
    ).strip()
    paper_trade_candles_bg_short_strict_hours_vn: str = os.getenv(
        "PAPER_TRADE_CANDLES_BG_SHORT_STRICT_HOURS_VN",
        "",
    ).strip()
    paper_trade_candles_bg_strict_min_win_bonus: float = float(
        os.getenv("PAPER_TRADE_CANDLES_BG_STRICT_MIN_WIN_BONUS", "0.04")
    )
    paper_trade_candles_bg_bullish_long_entry_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_CANDLES_BG_BULLISH_LONG_ENTRY_BUFFER_PCT", "0.002")
    )
    paper_trade_candles_bg_bullish_long_follow_extra_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_CANDLES_BG_BULLISH_LONG_FOLLOW_EXTRA_BUFFER_PCT", "0.001")
    )
    paper_trade_candles_bg_bullish_short_entry_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_CANDLES_BG_BULLISH_SHORT_ENTRY_BUFFER_PCT", "0.003")
    )
    paper_trade_candles_bg_bullish_short_nonfollow_extra_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_CANDLES_BG_BULLISH_SHORT_NONFOLLOW_EXTRA_BUFFER_PCT", "0.001")
    )
    paper_trade_candles_bg_bad_hour_aligned_entry_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_CANDLES_BG_BAD_HOUR_ALIGNED_ENTRY_BUFFER_PCT", "0.0025")
    )
    paper_trade_candles_bg_block_countertrend_strong_btc: bool = os.getenv(
        "PAPER_TRADE_CANDLES_BG_BLOCK_COUNTERTREND_STRONG_BTC",
        "true",
    ).lower() == "true"
    paper_trade_single_position_per_symbol_side: bool = os.getenv("PAPER_TRADE_SINGLE_POSITION_PER_SYMBOL_SIDE", "true").lower() == "true"
    paper_trade_max_open_positions_per_side: int = int(
        os.getenv("PAPER_TRADE_MAX_OPEN_POSITIONS_PER_SIDE", "40")
    )
    paper_trade_reentry_cooldown_minutes: int = int(os.getenv("PAPER_TRADE_REENTRY_COOLDOWN_MINUTES", "0"))
    paper_trade_reentry_after_sl_cooldown_minutes: int = int(os.getenv("PAPER_TRADE_REENTRY_AFTER_SL_COOLDOWN_MINUTES", "30"))
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
    paper_trade_entry_hard_block_hours_vn: str = os.getenv("PAPER_TRADE_ENTRY_HARD_BLOCK_HOURS_VN", "20")
    paper_trade_limit_long_block_hours_vn: str = os.getenv("PAPER_TRADE_LIMIT_LONG_BLOCK_HOURS_VN", "")
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
    paper_trade_bad_hour_aligned_entry_buffer_pct: float = float(
        os.getenv("PAPER_TRADE_BAD_HOUR_ALIGNED_ENTRY_BUFFER_PCT", "0.002")
    )
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

    binance_api_key: str = os.getenv("BINANCE_API_KEY", "").strip()
    binance_api_secret: str = os.getenv("BINANCE_API_SECRET", "").strip()
    binance_api_base_url: str = os.getenv("BINANCE_API_BASE_URL", "https://fapi.binance.com").strip() or "https://fapi.binance.com"
    binance_execution_mode: str = os.getenv("BINANCE_EXECUTION_MODE", "disabled").strip().lower() or "disabled"
    binance_execution_allow_live: bool = os.getenv("BINANCE_EXECUTION_ALLOW_LIVE", "false").lower() == "true"
    binance_execution_recv_window_ms: int = int(os.getenv("BINANCE_EXECUTION_RECV_WINDOW_MS", "5000"))
    binance_execution_set_leverage_before_order: bool = os.getenv("BINANCE_EXECUTION_SET_LEVERAGE_BEFORE_ORDER", "false").lower() == "true"

settings = Settings()
