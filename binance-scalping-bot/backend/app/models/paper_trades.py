from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class PaperTrade(BaseModel):
    id: int
    symbol: str
    side: str
    btc_following: Optional[bool] = None
    entry_type: str = "LIMIT"
    signal_win_probability: float
    effective_win_probability: float
    entry_price: float
    take_profit: float
    stop_loss: float
    liq_ema99_15m: Optional[float] = None
    liq_ema99_1h: Optional[float] = None
    liq_zone_price: Optional[float] = None
    liq_zone_score: Optional[float] = None
    entry_point_score: Optional[float] = None
    quantity: float
    leverage: int
    status: str
    opened_at: datetime
    closed_at: Optional[datetime] = None
    close_price: Optional[float] = None
    mark_price: Optional[float] = None
    mark_price_timestamp: Optional[str] = None
    close_reason: Optional[str] = None
    reference_win_symbol: Optional[str] = None
    reference_win_at: Optional[datetime] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    commission_usdt: Optional[float] = None
    mae_pct: Optional[float] = None
    mfe_pct: Optional[float] = None
    margin_usdt: Optional[float] = None
    result: Optional[int] = None
    current_candle_pattern: Optional[str] = None
    current_btc_trend: Optional[str] = None
    close_candle_pattern: Optional[str] = None
    btc_trend_at_close: Optional[str] = None
    entry_source: Optional[str] = None
    entry_stage: Optional[str] = None
    entry_signal_label: Optional[str] = None
    entry_signal_type: Optional[str] = None
    entry_execution_mode: Optional[str] = None


class PaperTradeStats(BaseModel):
    total_trades: int
    open_trades: int
    closed_trades: int
    win_trades: int
    loss_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    total_pnl_pct: float
    avg_pnl_pct: float
    order_usdt: float
    margin_usdt: float
    leverage: int
    maint_margin_rate: float
    max_risk_pct: float
    market_closed_trades: int = 0
    market_win_trades: int = 0
    market_win_rate: float = 0.0
    market_loss_trades: int = 0
    market_total_pnl: float = 0.0
    market_avg_pnl: float = 0.0
    market_total_pnl_pct: float = 0.0
    market_avg_pnl_pct: float = 0.0
    limit_closed_trades: int = 0
    limit_win_trades: int = 0
    limit_win_rate: float = 0.0
    limit_loss_trades: int = 0
    limit_total_pnl: float = 0.0
    limit_avg_pnl: float = 0.0
    limit_total_pnl_pct: float = 0.0
    limit_avg_pnl_pct: float = 0.0


class PaperTradeListResponse(BaseModel):
    items: list[PaperTrade]
    total: Optional[int] = None
    page: Optional[int] = None
    page_size: Optional[int] = None
    total_pages: Optional[int] = None


class PaperTradeStatsResponse(BaseModel):
    stats: PaperTradeStats


class PaperTradeEma99BounceSignal(BaseModel):
    symbol: str
    timeframe: str
    side: str = "LONG"
    signal_time: datetime
    mark_price: Optional[float] = None
    close_price: float
    ema25: float
    ema99: float
    volume_ratio: float
    touch_gap_pct: float
    ema99_gap_pct: float
    entry_ok: bool
    entry_status: str
    rsi14: float
    score: float


class PaperTradeEma99BounceSignalsResponse(BaseModel):
    count: int
    scanned: int
    generated_at: datetime
    items: list[PaperTradeEma99BounceSignal]


class PaperTradeDailySummary(BaseModel):
    trade_date: str
    total_trades: int
    win_trades: int
    loss_trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float


class PaperTradeDailySummaryResponse(BaseModel):
    items: list[PaperTradeDailySummary]


class PaperTradeHourlySideStats(BaseModel):
    total_orders: int = 0
    wins: int = 0
    losses: int = 0
    win_rate_pct: float = 0.0
    loss_rate_pct: float = 0.0
    net_pnl: float = 0.0
    avg_pnl: float = 0.0
    action: str = "LOW_DATA"
    note: Optional[str] = None


class PaperTradeHourlyWindow(BaseModel):
    hour_vn: int
    all: PaperTradeHourlySideStats
    long: PaperTradeHourlySideStats
    short: PaperTradeHourlySideStats
    is_bad_window: bool = False


class PaperTradeHourlyWindowResponse(BaseModel):
    lookback_days: int
    min_samples: int
    block_win_rate_pct: float
    strict_win_rate_pct: float
    current_hour_vn: int
    weekday_vn: Optional[int] = None
    trend_key: str = "ALL"
    items: list[PaperTradeHourlyWindow]


class PaperTradeEntryHourCell(BaseModel):
    hour_vn: int
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0


class PaperTradeEntryHourRow(BaseModel):
    trade_date: str
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    cells: list[PaperTradeEntryHourCell]


class PaperTradeEntryHourSummary(BaseModel):
    active_days: int = 0
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0


class PaperTradeEntryHourTypeOption(BaseModel):
    key: str
    label: str
    total_trades: int = 0


class PaperTradeEntryHourMatrixResponse(BaseModel):
    lookback_days: int
    entry_type_key: str
    entry_type_label: str
    summary: PaperTradeEntryHourSummary
    total_row: PaperTradeEntryHourRow
    entry_type_options: list[PaperTradeEntryHourTypeOption]
    items: list[PaperTradeEntryHourRow]


class PaperTradePatternStatsItem(BaseModel):
    candle_pattern: str
    btc_trend: str
    total_trades: int
    wins: int
    losses: int
    win_rate_pct: float
    loss_rate_pct: float
    net_pnl: float
    avg_pnl: float


class PaperTradePatternStatsResponse(BaseModel):
    repo_scope: str
    lookback: int
    closed_trades: int
    unknown_trades: int = 0
    items: list[PaperTradePatternStatsItem]


class PaperTradePatternBackfillResponse(BaseModel):
    repo_scope: str
    batch_size: int
    processed: int
    updated: int


class PaperMarketOpenRequest(BaseModel):
    symbol: str
    side: str = Field(pattern="^(LONG|SHORT)$")
    signal_win_probability: float = Field(ge=0, le=1)
    effective_win_probability: Optional[float] = Field(default=None, ge=0, le=1)
    repo_scope: Optional[str] = Field(default=None, pattern="^(main|candles|auto)$")
    entry_type: Optional[str] = Field(default=None, pattern="^[A-Z0-9_]+$")
    entry_price: Optional[float] = Field(default=None, gt=0)
    take_profit: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    reference_win_symbol: Optional[str] = Field(default=None, max_length=64)
    reference_win_at: Optional[datetime] = None
    entry_point_score: Optional[float] = Field(default=None)
    order_usdt: Optional[float] = Field(default=None, gt=0)
    margin_usdt: Optional[float] = Field(default=None, gt=0)
    quantity: Optional[float] = Field(default=None, gt=0)
    leverage: Optional[int] = Field(default=None, ge=1, le=125)
    entry_snapshot: Optional[dict[str, Any]] = None


class PaperManualCloseRequest(BaseModel):
    force_result: Optional[int] = Field(default=None, ge=0, le=1)


class PaperBinanceOrderRequest(BaseModel):
    symbol: str
    side: str = Field(pattern="^(LONG|SHORT)$")
    order_type: str = Field(pattern="^(MARKET|LIMIT)$")
    entry_price: Optional[float] = Field(default=None, gt=0)
    order_usdt: float = Field(gt=0)
    margin_usdt: float = Field(gt=0)
    tp_pct: Optional[float] = Field(default=None, gt=0)
    sl_pct: Optional[float] = Field(default=None, gt=0)
    margin_type: str = Field(default="ISOLATED", pattern="^(ISOLATED|CROSSED)$")
    test_mode: bool = False
