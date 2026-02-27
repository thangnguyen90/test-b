from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.models.paper_trades import PaperTrade, PaperTradeListResponse, PaperTradeStats, PaperTradeStatsResponse
from app.services.mysql_trade_repo import MySQLTradeRepository


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

        self.router.add_api_route("/open", self.get_open, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/history", self.get_history, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/stats", self.get_stats, methods=["GET"], response_model=PaperTradeStatsResponse)

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


paper_trade_api = PaperTradeAPI()
router = paper_trade_api.router
