from app.core.config import settings
from app.services.binance_price_stream import BinancePriceStream
from app.services.ml_predictor import MLPredictor
from app.services.order_manager import OrderManager
from app.services.ws_manager import WSManager

order_manager = OrderManager(settings.sqlite_db_path)
ml_predictor = MLPredictor(model_path=settings.ml_model_path)
ws_manager = WSManager()
price_stream = BinancePriceStream()
