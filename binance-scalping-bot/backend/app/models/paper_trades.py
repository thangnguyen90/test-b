from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PaperTrade(BaseModel):
    id: int
    symbol: str
    side: str
    signal_win_probability: float
    effective_win_probability: float
    entry_price: float
    take_profit: float
    stop_loss: float
    quantity: float
    leverage: int
    status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    pnl: Optional[float] = None
    result: Optional[int] = None


class PaperTradeStats(BaseModel):
    total_trades: int
    open_trades: int
    closed_trades: int
    win_trades: int
    loss_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


class PaperTradeListResponse(BaseModel):
    items: list[PaperTrade]


class PaperTradeStatsResponse(BaseModel):
    stats: PaperTradeStats


class PaperMarketOpenRequest(BaseModel):
    symbol: str
    side: str = Field(pattern="^(LONG|SHORT)$")
    signal_win_probability: float = Field(ge=0, le=1)
    effective_win_probability: Optional[float] = Field(default=None, ge=0, le=1)
    take_profit: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    quantity: Optional[float] = Field(default=None, gt=0)
    leverage: Optional[int] = Field(default=None, ge=1, le=125)
