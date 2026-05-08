from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PumpHunterBinanceOrderRequest(BaseModel):
    symbol: str = Field(min_length=3)
    test_mode: bool = True
    order_usdt: float | None = Field(default=None, gt=0)
    leverage: int | None = Field(default=None, ge=1, le=125)
    margin_type: Literal["ISOLATED", "CROSSED"] | None = None


class PumpHunterEntrySideControlRequest(BaseModel):
    allow_long: bool
    allow_short: bool
