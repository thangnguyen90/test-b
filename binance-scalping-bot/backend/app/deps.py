from app.core.config import settings
from app.services.auto_trainer import AutoTrainer
from app.services.binance_price_stream import BinancePriceStream
from app.services.ml_predictor import MLPredictor
from app.services.order_manager import OrderManager
from app.services.ws_manager import WSManager

order_manager = OrderManager(settings.sqlite_db_path)
ml_predictor = MLPredictor(model_path=settings.ml_model_path)
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
