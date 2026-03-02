from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.models.paper_trades import (
    PaperManualCloseRequest,
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
from app.services.risk_manager import (
    calc_atr_from_ohlcv,
    calc_estimated_margin_ratio_pct,
    calc_min_sl_pct_from_loss,
    calc_margin_usdt,
    calc_quantity_from_order_usdt,
    normalize_tp_sl,
)


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
        self.price_stream = None
        self.market_client = BinanceFuturesClient()

        self.router.add_api_route("/open", self.get_open, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/history", self.get_history, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/stats", self.get_stats, methods=["GET"], response_model=PaperTradeStatsResponse)
        self.router.add_api_route("/daily", self.get_daily_summary, methods=["GET"], response_model=PaperTradeDailySummaryResponse)
        self.router.add_api_route("/market-open", self.market_open, methods=["POST"], response_model=PaperTrade)
        self.router.add_api_route("/close/{trade_id}", self.manual_close, methods=["POST"], response_model=PaperTrade)

    def bind_repo(self, repo: MySQLTradeRepository | None) -> None:
        self.repo = repo

    def bind_price_stream(self, price_stream: object | None) -> None:
        self.price_stream = price_stream

    def _require_repo(self) -> MySQLTradeRepository:
        if self.repo is None:
            raise HTTPException(status_code=503, detail="Paper trading DB is not configured")
        return self.repo

    @staticmethod
    def _map_trade(row: dict) -> PaperTrade:
        entry_price = float(row["entry_price"])
        quantity = float(row["quantity"])
        leverage = int(row["leverage"])
        close_price = float(row["close_price"]) if row.get("close_price") is not None else None
        pnl = float(row["pnl"]) if row.get("pnl") is not None else None
        margin_usdt = (
            float(row["margin_usdt"])
            if row.get("margin_usdt") is not None
            else calc_margin_usdt(entry_price=entry_price, quantity=quantity, leverage=leverage)
        )
        pnl_pct = None
        if pnl is not None and margin_usdt > 0:
            pnl_pct = (pnl / margin_usdt) * 100

        return PaperTrade(
            id=int(row["id"]),
            symbol=str(row["symbol"]),
            side=str(row["side"]),
            entry_type=str(row.get("entry_type") or "LIMIT"),
            signal_win_probability=float(row["signal_win_probability"]),
            effective_win_probability=float(row.get("effective_win_probability") or row["signal_win_probability"]),
            entry_price=entry_price,
            take_profit=float(row["take_profit"]),
            stop_loss=float(row["stop_loss"]),
            quantity=quantity,
            leverage=leverage,
            status=str(row["status"]),
            opened_at=_parse_dt(row.get("opened_at")) or datetime.utcnow(),
            closed_at=_parse_dt(row.get("closed_at")),
            close_price=close_price,
            close_reason=str(row["close_reason"]) if row.get("close_reason") is not None else None,
            pnl=pnl,
            pnl_pct=pnl_pct,
            mae_pct=float(row["mae_pct"]) if row.get("mae_pct") is not None else None,
            mfe_pct=float(row["mfe_pct"]) if row.get("mfe_pct") is not None else None,
            margin_usdt=margin_usdt,
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
        payload["order_usdt"] = settings.paper_trade_order_usdt
        payload["margin_usdt"] = settings.paper_trade_margin_usdt if settings.paper_trade_margin_usdt > 0 else (
            settings.paper_trade_order_usdt / max(1, settings.paper_trade_leverage)
        )
        payload["leverage"] = settings.paper_trade_leverage
        payload["maint_margin_rate"] = settings.paper_trade_maint_margin_rate
        payload["max_risk_pct"] = settings.paper_trade_max_risk_pct
        stats = PaperTradeStats(**payload)
        return PaperTradeStatsResponse(stats=stats)

    def get_daily_summary(self, days: int = Query(default=30, ge=1, le=365)) -> PaperTradeDailySummaryResponse:
        repo = self._require_repo()
        rows = repo.daily_summary(days=days)
        return PaperTradeDailySummaryResponse(items=[PaperTradeDailySummary(**row) for row in rows])

    async def market_open(self, req: PaperMarketOpenRequest) -> PaperTrade:
        repo = self._require_repo()
        if repo.has_open_trade(symbol=req.symbol, side=req.side):
            raise HTTPException(status_code=409, detail=f"Open trade already exists for {req.symbol} {req.side}")

        market_price = req.entry_price
        if market_price is None:
            if self.price_stream is not None:
                try:
                    stream_price, _ = await self.price_stream.get_price(symbol=req.symbol)
                    if stream_price is not None:
                        market_price = float(stream_price)
                except Exception:
                    pass

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
            min_sl_pct=max(
                settings.paper_trade_min_sl_pct,
                calc_min_sl_pct_from_loss(min_sl_loss_pct=settings.paper_trade_min_sl_loss_pct),
            ),
            sl_extra_buffer_pct=settings.paper_trade_sl_extra_buffer_pct,
            atr_value=self._resolve_symbol_atr(req.symbol),
            sl_atr_multiplier=settings.paper_trade_sl_atr_multiplier,
            min_rr=settings.paper_trade_min_rr,
            max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
        )
        leverage = req.leverage or settings.paper_trade_leverage
        risk_pct = calc_estimated_margin_ratio_pct(
            leverage=leverage,
            maint_margin_rate=settings.paper_trade_maint_margin_rate,
        )
        if risk_pct > settings.paper_trade_max_risk_pct:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Risk too high for {req.symbol}: {risk_pct:.2f}% > "
                    f"max {settings.paper_trade_max_risk_pct:.2f}% (est. margin ratio)"
                ),
            )

        if req.quantity is not None:
            quantity = req.quantity
        else:
            order_usdt = req.order_usdt or settings.paper_trade_order_usdt
            quantity = calc_quantity_from_order_usdt(
                entry_price=float(market_price),
                order_usdt=order_usdt,
                fallback_quantity=settings.paper_trade_quantity,
            )
        margin_usdt = req.margin_usdt or settings.paper_trade_margin_usdt
        if margin_usdt <= 0:
            margin_usdt = calc_margin_usdt(
                entry_price=float(market_price),
                quantity=quantity,
                leverage=leverage,
            )

        trade_id = repo.create_open_trade(
            {
                "symbol": req.symbol,
                "side": req.side,
                "entry_type": "MARKET",
                "signal_win_probability": req.signal_win_probability,
                "effective_win_probability": req.effective_win_probability or req.signal_win_probability,
                "entry_price": float(market_price),
                "take_profit": normalized_tp,
                "stop_loss": normalized_sl,
                "quantity": quantity,
                "margin_usdt": margin_usdt,
                "leverage": leverage,
            }
        )
        rows = repo.list_recent_trades(limit=1)
        if not rows:
            raise HTTPException(status_code=500, detail=f"Cannot read created trade {trade_id}")
        return self._map_trade(rows[0])

    async def manual_close(self, trade_id: int, req: PaperManualCloseRequest) -> PaperTrade:
        repo = self._require_repo()
        open_rows = repo.list_open_trades()
        row = next((item for item in open_rows if int(item.get("id") or 0) == int(trade_id)), None)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Open trade {trade_id} not found")

        symbol = str(row["symbol"])
        side = str(row["side"])
        entry = float(row["entry_price"])
        qty = float(row["quantity"])

        close_price: float | None = None
        if self.price_stream is not None:
            try:
                stream_price, _ = await self.price_stream.get_price(symbol=symbol)
                if stream_price is not None:
                    close_price = float(stream_price)
            except Exception:
                close_price = None
        if close_price is None:
            try:
                ticker = self.market_client.fetch_ticker(symbol)
                px = ticker.get("last") or ticker.get("close")
                if px is not None:
                    close_price = float(px)
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Cannot fetch close price for {symbol}: {exc}") from exc
        if close_price is None:
            raise HTTPException(status_code=503, detail=f"Cannot fetch close price for {symbol}")

        pnl = (close_price - entry) * qty if side == "LONG" else (entry - close_price) * qty
        leverage = int(row.get("leverage") or settings.paper_trade_leverage)
        margin_usdt = (
            float(row["margin_usdt"])
            if row.get("margin_usdt") is not None
            else calc_margin_usdt(entry_price=entry, quantity=qty, leverage=leverage)
        )
        pnl_pct = (pnl / margin_usdt) * 100 if margin_usdt > 0 else 0.0
        prev_mae = float(row.get("mae_pct") or 0.0)
        prev_mfe = float(row.get("mfe_pct") or 0.0)
        repo.update_trade_excursions(
            trade_id=trade_id,
            mae_pct=min(prev_mae, pnl_pct),
            mfe_pct=max(prev_mfe, pnl_pct),
        )
        result = req.force_result if req.force_result is not None else (1 if pnl >= 0 else 0)
        manual_reason = "MANUAL_FORCE_LOSS" if req.force_result == 0 else "MANUAL_FORCE_WIN" if req.force_result == 1 else "MANUAL"
        repo.close_trade(
            trade_id=trade_id,
            close_price=close_price,
            pnl=pnl,
            result=int(result),
            close_reason=manual_reason,
        )

        recent = repo.list_recent_trades(limit=200)
        closed = next((item for item in recent if int(item.get("id") or 0) == int(trade_id)), None)
        if closed is None:
            raise HTTPException(status_code=500, detail=f"Cannot read closed trade {trade_id}")
        return self._map_trade(closed)

    def _resolve_symbol_atr(self, symbol: str) -> float | None:
        if settings.paper_trade_sl_atr_multiplier <= 0:
            return None
        try:
            rows = self.market_client.fetch_ohlcv(
                symbol=symbol,
                timeframe=settings.paper_trade_sl_atr_timeframe,
                limit=settings.paper_trade_sl_atr_limit,
            )
            return calc_atr_from_ohlcv(rows=rows, period=14)
        except Exception:
            return None


paper_trade_api = PaperTradeAPI()
router = paper_trade_api.router
