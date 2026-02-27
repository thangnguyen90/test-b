from datetime import datetime

from pydantic import BaseModel, Field


class TrainRequest(BaseModel):
    limit: int = Field(default=800, ge=200, le=2000)
    horizon: int = Field(default=4, ge=1, le=24)
    rr_ratio: float = Field(default=1.5, ge=1.0, le=5.0)


class TrainResponse(BaseModel):
    trained: bool
    samples: int
    features: int
    accuracy: float | None
    roc_auc: float | None
    trained_at: datetime | None
    feedback_samples: int = 0


class ModelStatus(BaseModel):
    is_loaded: bool
    model_path: str
    trained_at: datetime | None
    feature_count: int
    accuracy: float | None
    roc_auc: float | None
