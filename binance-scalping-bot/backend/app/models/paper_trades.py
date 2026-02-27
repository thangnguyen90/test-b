from datetime import datetime
from typing import Optional

from pydantic import BaseModel


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
