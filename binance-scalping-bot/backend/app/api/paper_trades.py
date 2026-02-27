from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.models.paper_trades import (
    PaperTradeDailySummary,
    PaperTradeDailySummaryResponse,
    PaperMarketOpenRequest,
    PaperTrade,
    PaperTradeListResponse,
    PaperTradeStats,
    PaperTradeStatsResponse,
)
from app.services.binance_client import BinanceFuturesClient
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.risk_manager import calc_margin_risk_pct, normalize_tp_sl


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


class PaperTradeAPI:
    def __init__(self) -> None:
        self.router = APIRouter(prefix="/api/v1/paper-trades", tags=["paper-trades"])
        self.repo: MySQLTradeRepository | None = None
        self.market_client = BinanceFuturesClient()

        self.router.add_api_route("/open", self.get_open, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/history", self.get_history, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/stats", self.get_stats, methods=["GET"], response_model=PaperTradeStatsResponse)
        self.router.add_api_route("/daily", self.get_daily_summary, methods=["GET"], response_model=PaperTradeDailySummaryResponse)
        self.router.add_api_route("/market-open", self.market_open, methods=["POST"], response_model=PaperTrade)

    def bind_repo(self, repo: MySQLTradeRepository | None) -> None:
        self.repo = repo

    def _require_repo(self) -> MySQLTradeRepository:
        if self.repo is None:
            raise HTTPException(status_code=503, detail="Paper trading DB is not configured")
        return self.repo

    @staticmethod
    def _map_trade(row: dict) -> PaperTrade:
        return PaperTrade(
            id=int(row["id"]),
            symbol=str(row["symbol"]),
            side=str(row["side"]),
            signal_win_probability=float(row["signal_win_probability"]),
            effective_win_probability=float(row.get("effective_win_probability") or row["signal_win_probability"]),
            entry_price=float(row["entry_price"]),
            take_profit=float(row["take_profit"]),
            stop_loss=float(row["stop_loss"]),
            quantity=float(row["quantity"]),
            leverage=int(row["leverage"]),
            status=str(row["status"]),
            opened_at=_parse_dt(row.get("opened_at")) or datetime.utcnow(),
            closed_at=_parse_dt(row.get("closed_at")),
            close_price=float(row["close_price"]) if row.get("close_price") is not None else None,
            pnl=float(row["pnl"]) if row.get("pnl") is not None else None,
            result=int(row["result"]) if row.get("result") is not None else None,
        )

    def get_open(self) -> PaperTradeListResponse:
        repo = self._require_repo()
        rows = repo.list_open_trades()
        return PaperTradeListResponse(items=[self._map_trade(row) for row in rows])

    def get_history(self, limit: int = Query(default=200, ge=1, le=2000)) -> PaperTradeListResponse:
        repo = self._require_repo()
        rows = repo.list_recent_trades(limit=limit)
        return PaperTradeListResponse(items=[self._map_trade(row) for row in rows])

    def get_stats(self) -> PaperTradeStatsResponse:
        repo = self._require_repo()
        payload = repo.stats()
        stats = PaperTradeStats(**payload)
        return PaperTradeStatsResponse(stats=stats)

    def get_daily_summary(self, days: int = Query(default=30, ge=1, le=365)) -> PaperTradeDailySummaryResponse:
        repo = self._require_repo()
        rows = repo.daily_summary(days=days)
        return PaperTradeDailySummaryResponse(items=[PaperTradeDailySummary(**row) for row in rows])

    def market_open(self, req: PaperMarketOpenRequest) -> PaperTrade:
        repo = self._require_repo()
        if repo.has_open_trade(symbol=req.symbol, side=req.side):
            raise HTTPException(status_code=409, detail=f"Open trade already exists for {req.symbol} {req.side}")

        market_price = req.entry_price
        if market_price is None:
            try:
                ticker = self.market_client.fetch_ticker(req.symbol)
                market_price = ticker.get("last") or ticker.get("close")
                if market_price is None:
                    bid = ticker.get("bid")
                    ask = ticker.get("ask")
                    if bid is not None and ask is not None:
                        market_price = (bid + ask) / 2
                if market_price is None:
                    rows = self.market_client.fetch_ohlcv(req.symbol, timeframe="1m", limit=2)
                    market_price = rows[-1][4] if rows else None
                if market_price is None:
                    raise RuntimeError("No market price")
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Cannot open market trade for {req.symbol}: {exc}") from exc

        normalized_tp, normalized_sl = normalize_tp_sl(
            side=req.side,
            entry_price=float(market_price),
            take_profit=req.take_profit,
            stop_loss=req.stop_loss,
            min_sl_pct=settings.paper_trade_min_sl_pct,
            min_rr=settings.paper_trade_min_rr,
        )
        leverage = req.leverage or settings.paper_trade_leverage
        risk_pct = calc_margin_risk_pct(
            side=req.side,
            entry_price=float(market_price),
            stop_loss=normalized_sl,
            leverage=leverage,
        )
        if risk_pct > settings.paper_trade_max_risk_pct:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Risk too high for {req.symbol}: {risk_pct:.2f}% > "
                    f"max {settings.paper_trade_max_risk_pct:.2f}% (margin risk)"
                ),
            )

        trade_id = repo.create_open_trade(
            {
                "symbol": req.symbol,
                "side": req.side,
                "signal_win_probability": req.signal_win_probability,
                "effective_win_probability": req.effective_win_probability or req.signal_win_probability,
                "entry_price": float(market_price),
                "take_profit": normalized_tp,
                "stop_loss": normalized_sl,
                "quantity": req.quantity or settings.paper_trade_quantity,
                "leverage": leverage,
            }
        )
        rows = repo.list_recent_trades(limit=1)
        if not rows:
            raise HTTPException(status_code=500, detail=f"Cannot read created trade {trade_id}")
        return self._map_trade(rows[0])


paper_trade_api = PaperTradeAPI()
router = paper_trade_api.router
