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
    training_in_progress: bool = False
    auto_train_enabled: bool = False
    auto_train_running: bool = False
    auto_train_interval_minutes: int | None = None
    auto_train_next_run_at: datetime | None = None
    auto_train_last_run_started_at: datetime | None = None
    auto_train_last_run_finished_at: datetime | None = None
    auto_train_last_result: str | None = None
    auto_train_last_error: str | None = None
    last_train_trigger: str | None = None
    last_train_started_at: datetime | None = None
    last_train_finished_at: datetime | None = None
    last_train_duration_sec: float | None = None
    last_train_result: str | None = None
    last_train_error: str | None = None
    train_log_path: str | None = None
