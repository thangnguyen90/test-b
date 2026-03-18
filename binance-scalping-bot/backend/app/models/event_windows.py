from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MarketEventWindow(BaseModel):
    id: int
    title: str
    category: str
    impact_level: str
    starts_at: datetime
    ends_at: datetime
    expected_volatility_pct: Optional[float] = None
    source_url: Optional[str] = None
    note: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime
    phase: str
    minutes_to_start: Optional[int] = None
    minutes_to_end: Optional[int] = None


class MarketEventWindowListResponse(BaseModel):
    server_time_vn: datetime
    phase: str
    count: int
    items: list[MarketEventWindow]


class MarketEventWindowCreateRequest(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    category: str = Field(default="macro", min_length=2, max_length=64)
    impact_level: str = Field(default="HIGH", pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    starts_at: datetime
    ends_at: datetime
    expected_volatility_pct: Optional[float] = Field(default=None, ge=0)
    source_url: Optional[str] = Field(default=None, max_length=512)
    note: Optional[str] = Field(default=None, max_length=4000)
    is_active: bool = True


class MarketEventWindowUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=255)
    category: Optional[str] = Field(default=None, min_length=2, max_length=64)
    impact_level: Optional[str] = Field(default=None, pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    expected_volatility_pct: Optional[float] = Field(default=None, ge=0)
    source_url: Optional[str] = Field(default=None, max_length=512)
    note: Optional[str] = Field(default=None, max_length=4000)
    is_active: Optional[bool] = None

class MarketEventImportResponse(BaseModel):
    source: str
    imported_at_vn: datetime
    total_in_feed: int
    inserted: int
    updated: int
    skipped: int
    count: int
    items: list[MarketEventWindow]

