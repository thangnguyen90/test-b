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
    ml_feedback_hard_flip_mae_pct: float = float(os.getenv("ML_FEEDBACK_HARD_FLIP_MAE_PCT", "10"))
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
    auto_train_enabled: bool = os.getenv("AUTO_TRAIN_ENABLED", "true").lower() == "true"
    auto_train_interval_minutes: int = int(os.getenv("AUTO_TRAIN_INTERVAL_MINUTES", "240"))
    auto_train_startup_delay_sec: int = int(os.getenv("AUTO_TRAIN_STARTUP_DELAY_SEC", "30"))
    auto_train_limit: int = int(os.getenv("AUTO_TRAIN_LIMIT", "800"))
    auto_train_horizon: int = int(os.getenv("AUTO_TRAIN_HORIZON", "4"))
    auto_train_rr_ratio: float = float(os.getenv("AUTO_TRAIN_RR_RATIO", "1.5"))
    ml_use_liquidation_features: bool = os.getenv("ML_USE_LIQUIDATION_FEATURES", "true").lower() == "true"
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
    mysql_database: str = os.getenv("MYSQL_DATABASE", "trading_bot")

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
    paper_trade_major_leverage: int = int(os.getenv("PAPER_TRADE_MAJOR_LEVERAGE", "10"))
    paper_trade_major_max_risk_pct: float = float(os.getenv("PAPER_TRADE_MAJOR_MAX_RISK_PCT", "20"))
    paper_trade_poll_interval_sec: float = float(os.getenv("PAPER_TRADE_POLL_INTERVAL_SEC", "6"))
    paper_trade_stream_max_stale_sec: float = float(os.getenv("PAPER_TRADE_STREAM_MAX_STALE_SEC", "5"))
    paper_trade_min_sl_pct: float = float(os.getenv("PAPER_TRADE_MIN_SL_PCT", "0.008"))
    paper_trade_min_sl_loss_pct: float = float(os.getenv("PAPER_TRADE_MIN_SL_LOSS_PCT", "5"))
    paper_trade_sl_extra_buffer_pct: float = float(os.getenv("PAPER_TRADE_SL_EXTRA_BUFFER_PCT", "0.002"))
    paper_trade_sl_atr_multiplier: float = float(os.getenv("PAPER_TRADE_SL_ATR_MULTIPLIER", "1.2"))
    paper_trade_sl_atr_timeframe: str = os.getenv("PAPER_TRADE_SL_ATR_TIMEFRAME", "5m")
    paper_trade_sl_atr_limit: int = int(os.getenv("PAPER_TRADE_SL_ATR_LIMIT", "120"))
    paper_trade_max_tp_pct: float = float(os.getenv("PAPER_TRADE_MAX_TP_PCT", "15"))
    paper_trade_min_rr: float = float(os.getenv("PAPER_TRADE_MIN_RR", "1.5"))
    paper_trade_max_risk_pct: float = float(os.getenv("PAPER_TRADE_MAX_RISK_PCT", "12"))
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
    paper_trade_btc_shock_pause_enabled: bool = os.getenv("PAPER_TRADE_BTC_SHOCK_PAUSE_ENABLED", "true").lower() == "true"
    paper_trade_btc_shock_threshold_pct: float = float(os.getenv("PAPER_TRADE_BTC_SHOCK_THRESHOLD_PCT", "1.2"))
    paper_trade_btc_shock_cooldown_minutes: int = int(os.getenv("PAPER_TRADE_BTC_SHOCK_COOLDOWN_MINUTES", "30"))
    paper_trade_btc_shock_up_long_block_minutes: int = int(os.getenv("PAPER_TRADE_BTC_SHOCK_UP_LONG_BLOCK_MINUTES", "60"))
    paper_trade_btc_shock_down_short_block_minutes: int = int(os.getenv("PAPER_TRADE_BTC_SHOCK_DOWN_SHORT_BLOCK_MINUTES", "60"))
    paper_trade_btc_shock_up_require_pullback: bool = os.getenv("PAPER_TRADE_BTC_SHOCK_UP_REQUIRE_PULLBACK", "true").lower() == "true"
    paper_trade_btc_shock_pullback_ema_period: int = int(os.getenv("PAPER_TRADE_BTC_SHOCK_PULLBACK_EMA_PERIOD", "21"))
    paper_trade_btc_shock_pullback_tolerance_pct: float = float(os.getenv("PAPER_TRADE_BTC_SHOCK_PULLBACK_TOLERANCE_PCT", "0.0015"))
    paper_trade_btc_reversal_profit_exit_enabled: bool = os.getenv("PAPER_TRADE_BTC_REVERSAL_PROFIT_EXIT_ENABLED", "true").lower() == "true"
    paper_trade_btc_reversal_threshold_pct: float = float(os.getenv("PAPER_TRADE_BTC_REVERSAL_THRESHOLD_PCT", "0.8"))
    paper_trade_btc_reversal_min_confidence: float = float(os.getenv("PAPER_TRADE_BTC_REVERSAL_MIN_CONFIDENCE", "0.55"))
    paper_trade_btc_profit_lock_enabled: bool = os.getenv("PAPER_TRADE_BTC_PROFIT_LOCK_ENABLED", "true").lower() == "true"
    paper_trade_btc_profit_lock_min_confidence: float = float(os.getenv("PAPER_TRADE_BTC_PROFIT_LOCK_MIN_CONFIDENCE", "0.6"))
    paper_trade_btc_follow_min_corr: float = float(os.getenv("PAPER_TRADE_BTC_FOLLOW_MIN_CORR", "0.45"))
    paper_trade_btc_follow_min_beta: float = float(os.getenv("PAPER_TRADE_BTC_FOLLOW_MIN_BETA", "0.2"))
    paper_trade_btc_follow_lookback: int = int(os.getenv("PAPER_TRADE_BTC_FOLLOW_LOOKBACK", "120"))
    paper_trade_btc_follow_cache_sec: float = float(os.getenv("PAPER_TRADE_BTC_FOLLOW_CACHE_SEC", "300"))
    paper_trade_volatility_guard_enabled: bool = os.getenv("PAPER_TRADE_VOLATILITY_GUARD_ENABLED", "true").lower() == "true"
    paper_trade_volatility_guard_timeframe: str = os.getenv("PAPER_TRADE_VOLATILITY_GUARD_TIMEFRAME", "5m")
    paper_trade_volatility_guard_limit: int = int(os.getenv("PAPER_TRADE_VOLATILITY_GUARD_LIMIT", "60"))
    paper_trade_volatility_guard_cache_sec: float = float(os.getenv("PAPER_TRADE_VOLATILITY_GUARD_CACHE_SEC", "12"))
    paper_trade_volatility_guard_max_atr_pct: float = float(os.getenv("PAPER_TRADE_VOLATILITY_GUARD_MAX_ATR_PCT", "1.4"))
    paper_trade_volatility_guard_max_range_pct: float = float(os.getenv("PAPER_TRADE_VOLATILITY_GUARD_MAX_RANGE_PCT", "2.6"))
    paper_trade_volatility_guard_max_body_pct: float = float(os.getenv("PAPER_TRADE_VOLATILITY_GUARD_MAX_BODY_PCT", "1.8"))
    paper_trade_volatility_guard_max_range_atr_ratio: float = float(
        os.getenv("PAPER_TRADE_VOLATILITY_GUARD_MAX_RANGE_ATR_RATIO", "2.8")
    )
    paper_trade_test_ml_enabled: bool = os.getenv("PAPER_TRADE_TEST_ML_ENABLED", "false").lower() == "true"
    paper_trade_test_ml_min_win: float = float(os.getenv("PAPER_TRADE_TEST_ML_MIN_WIN", "0.75"))
    paper_trade_test_ml_max_symbols: int = int(os.getenv("PAPER_TRADE_TEST_ML_MAX_SYMBOLS", "80"))
    paper_trade_test_ml_max_orders_per_cycle: int = int(os.getenv("PAPER_TRADE_TEST_ML_MAX_ORDERS_PER_CYCLE", "2"))


settings = Settings()
