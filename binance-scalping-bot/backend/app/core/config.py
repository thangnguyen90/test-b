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
    ml_feedback_train_limit: int = int(os.getenv("ML_FEEDBACK_TRAIN_LIMIT", "1200"))
    auto_train_enabled: bool = os.getenv("AUTO_TRAIN_ENABLED", "true").lower() == "true"
    auto_train_interval_minutes: int = int(os.getenv("AUTO_TRAIN_INTERVAL_MINUTES", "240"))
    auto_train_startup_delay_sec: int = int(os.getenv("AUTO_TRAIN_STARTUP_DELAY_SEC", "30"))
    auto_train_limit: int = int(os.getenv("AUTO_TRAIN_LIMIT", "800"))
    auto_train_horizon: int = int(os.getenv("AUTO_TRAIN_HORIZON", "4"))
    auto_train_rr_ratio: float = float(os.getenv("AUTO_TRAIN_RR_RATIO", "1.5"))
    ml_use_liquidation_features: bool = os.getenv("ML_USE_LIQUIDATION_FEATURES", "true").lower() == "true"

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
    paper_trade_poll_interval_sec: float = float(os.getenv("PAPER_TRADE_POLL_INTERVAL_SEC", "6"))
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


settings = Settings()
