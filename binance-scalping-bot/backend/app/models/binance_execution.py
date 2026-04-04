from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class BinanceExecutionRequest(BaseModel):
    symbol: str = Field(min_length=3, max_length=32)
    side: str = Field(pattern="^(LONG|SHORT)$")
    quantity: float = Field(gt=0)
    leverage: Optional[int] = Field(default=None, ge=1, le=125)
    mode: Optional[str] = Field(default=None, pattern="^(test|live)$")
    order_type: str = Field(default="MARKET", pattern="^(MARKET)$")
    position_side: Optional[str] = Field(default=None, pattern="^(BOTH|LONG|SHORT)$")
    reduce_only: bool = False
    set_leverage: bool = False
    confirm_live: bool = False


class BinanceExecutionResponse(BaseModel):
    ok: bool
    model: str = "ML_CANDLES_BG"
    mode: str
    symbol: str
    exchange_symbol: str
    exchange_side: str
    order_type: str
    quantity: float
    leverage: Optional[int] = None
    reduce_only: bool = False
    client_order_id: str
    test_order: bool = True
    live_sent: bool = False
    order_id: Optional[str] = None
    order_status: Optional[str] = None
    mark_price: Optional[float] = None
    price_source: Optional[str] = None
    base_url: Optional[str] = None
    note: Optional[str] = None
    response: Optional[dict[str, Any]] = None


class BinanceExecutionStatusResponse(BaseModel):
    configured: bool
    mode: str
    allow_live: bool
    can_place_test_orders: bool
    can_place_live_orders: bool
    base_url: str
