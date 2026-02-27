from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELED = "CANCELED"


class OrderBase(BaseModel):
    symbol: str
    side: str = Field(pattern="^(LONG|SHORT)$")
    quantity: float
    leverage: int = Field(ge=1, le=125)
    predicted_entry_price: float
    stop_loss: float
    take_profit: float
    win_probability: float = Field(ge=0, le=1)


class OrderCreate(OrderBase):
    expiration_time: Optional[datetime] = None


class Order(OrderBase):
    id: int
    status: OrderStatus
    created_at: datetime
    updated_at: datetime
    opened_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    pnl: Optional[float] = None
    expiration_time: Optional[datetime] = None


class ApiHealth(BaseModel):
    status: str
    app_name: str
    environment: str
    timestamp: datetime
