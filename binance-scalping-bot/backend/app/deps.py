from typing import Any

from app.core.config import settings
from app.services.auto_trainer import AutoTrainer
from app.services.binance_price_stream import BinancePriceStream
from app.services.liquidation_ml_predictor import LiquidationMLPredictor
from app.services.ml_predictor import MLPredictor
from app.services.order_manager import OrderManager
from app.services.ws_manager import WSManager

order_manager = OrderManager(settings.sqlite_db_path)
ml_predictor = MLPredictor(model_path=settings.ml_model_path)
ml_test_predictor = MLPredictor(
    model_path=settings.ml_test_model_path,
    use_liquidation_features=settings.ml_test_use_liquidation_features,
)
liquid_ml_predictor = LiquidationMLPredictor(
    model_path=settings.liquid_ml_model_path,
    touch_tolerance_pct=settings.liquid_ml_touch_tolerance_pct,
    short_zone_min_score=settings.liquid_ml_short_zone_min_score,
    short_zone_touch_multiplier=settings.liquid_ml_short_zone_touch_multiplier,
    rr_ratio=settings.liquid_ml_train_rr_ratio,
)
ws_manager = WSManager()
price_stream = BinancePriceStream()
auto_trainer = AutoTrainer(
    predictor=ml_predictor,
    enabled=settings.auto_train_enabled,
    interval_minutes=settings.auto_train_interval_minutes,
    startup_delay_sec=settings.auto_train_startup_delay_sec,
    limit=settings.auto_train_limit,
    horizon=settings.auto_train_horizon,
    rr_ratio=settings.auto_train_rr_ratio,
    symbols=settings.training_symbols,
)

# Runtime references so APIs can expose true paper-trade enterability state.
paper_trade_repo: Any | None = None
paper_trade_engine: Any | None = None


def bind_paper_trade_runtime(repo: Any | None, engine: Any | None) -> None:
    global paper_trade_repo, paper_trade_engine
    paper_trade_repo = repo
    paper_trade_engine = engine


def get_paper_trade_runtime() -> tuple[Any | None, Any | None]:
    return paper_trade_repo, paper_trade_engine
