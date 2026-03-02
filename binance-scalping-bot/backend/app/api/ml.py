from fastapi import APIRouter

from app.core.config import settings
from app.deps import auto_trainer, liquid_ml_predictor, ml_predictor
from app.models.ml import (
    LiquidModelStatus,
    LiquidTrainRequest,
    LiquidTrainResponse,
    ModelStatus,
    TrainRequest,
    TrainResponse,
)
from app.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/api/v1/ml", tags=["ml"])
analytics_service = AnalyticsService()


@router.get("/status", response_model=ModelStatus)
def get_model_status() -> ModelStatus:
    data = ml_predictor.status()
    data.update(auto_trainer.status())
    return ModelStatus(**data)


@router.post("/train", response_model=TrainResponse)
def train_model(req: TrainRequest) -> TrainResponse:
    result = ml_predictor.train(
        limit=req.limit,
        horizon=req.horizon,
        rr_ratio=req.rr_ratio,
        symbols=settings.training_symbols,
    )
    return TrainResponse(**result)


@router.get("/liquid/status", response_model=LiquidModelStatus)
def get_liquid_model_status() -> LiquidModelStatus:
    return LiquidModelStatus(**liquid_ml_predictor.status())


@router.post("/liquid/train", response_model=LiquidTrainResponse)
def train_liquid_model(req: LiquidTrainRequest) -> LiquidTrainResponse:
    symbols = [
        str(item.get("symbol"))
        for item in analytics_service.top_volatility(days=req.top_vol_days, limit=req.max_symbols)
        if item.get("symbol")
    ]
    if not symbols:
        symbols = settings.training_symbols[: req.max_symbols]
    result = liquid_ml_predictor.train(
        symbols=symbols,
        limit=req.limit,
        horizon=req.horizon,
        rr_ratio=req.rr_ratio,
    )
    result["symbols_used"] = len(symbols)
    return LiquidTrainResponse(**result)
