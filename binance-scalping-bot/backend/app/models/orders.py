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


class HunterLiveOrder(BaseModel):
    key: str
    symbol: str
    side: str
    score: float = 0.0
    signal_label: Optional[str] = None
    stage: Optional[str] = None
    entry_order_type: Optional[str] = None
    entry_price: Optional[float] = None
    tp_price: Optional[float] = None
    sl_price: Optional[float] = None
    quantity: Optional[str] = None
    filled_qty: Optional[str] = None
    leverage: Optional[int] = None
    margin_type: Optional[str] = None
    placed_at_text: Optional[str] = None
    age_minutes: float = 0.0
    entry_filled: bool = False
    tp_order_placed: bool = False
    tp_moved_to_entry: bool = False
    sl_order_placed: bool = False
    sl_moved_to_entry: bool = False


class ApiHealth(BaseModel):
    status: str
    app_name: str
    environment: str
    timestamp: datetime
