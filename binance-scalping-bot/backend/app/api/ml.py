from fastapi import APIRouter

from app.core.config import settings
from app.deps import auto_trainer, ml_predictor
from app.models.ml import ModelStatus, TrainRequest, TrainResponse

router = APIRouter(prefix="/api/v1/ml", tags=["ml"])


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
